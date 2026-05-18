#!/usr/bin/env python3
"""
uom_persistent.py - UOM 持久化浏览器方案

目标：
- 用固定 Playwright user-data-dir 复用登录态
- 避免每次都重新手机号登录
- 同时检测两层认证：
  1. 主站 NROS 登录态
  2. 飞行活动 iframe 的 oapi 认证态（PUB-Token + ticket）
- 基于持久化上下文读取最近计划，并尝试“复制旧计划 -> 改时间 -> 提交”

用法：
  python3 uom_persistent.py --status
  python3 uom_persistent.py --login
  python3 uom_persistent.py --ensure-fly
  python3 uom_persistent.py --latest-plan
  python3 uom_persistent.py --submit-copy-next-monday
"""

import argparse
import base64
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
PERSIST_DIR = SCRIPT_DIR / ".playwright-uom-profile"
CAPTCHA_FILE = Path("/tmp/uom_persistent_captcha.png")
BASE_URL = "https://uom.caac.gov.cn"


def get_next_weekday_same_time(plan_beg: str, plan_end: str, target_weekday: int):
    b = datetime.strptime(plan_beg, '%Y-%m-%d %H:%M:%S')
    e = datetime.strptime(plan_end, '%Y-%m-%d %H:%M:%S')
    days_ahead = (target_weekday - b.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    nb = b + timedelta(days=days_ahead)
    ne = e + timedelta(days=days_ahead)
    return nb.strftime('%Y-%m-%d %H:%M:%S'), ne.strftime('%Y-%m-%d %H:%M:%S')


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_full_submit_profile():
    try:
        cfg = load_config()
        return {
            "uavs": [{
                "remark": None,
                "pubSeq": cfg["drone"].get("pubSeq"),
                "userId": None,
                "sn": cfg["drone"].get("sn"),
                "uasCode": cfg["drone"].get("uasCode"),
                "ownType": cfg["drone"].get("ownType"),
                "ownName": cfg["drone"].get("ownName"),
                "psnCardtype": cfg["drone"].get("psnCardtype"),
                "psnCardno": cfg["drone"].get("psnCardno"),
                "deptCode": None,
                "mnfName": cfg["drone"].get("mnfName"),
                "mnfCode": cfg["drone"].get("mnfCode"),
                "proMode": cfg["drone"].get("proMode"),
                "proName": cfg["drone"].get("proName"),
                "proClass": cfg["drone"].get("proClass"),
                "proType": cfg["drone"].get("proType"),
                "weightEmpty": cfg["drone"].get("weightEmpty"),
                "weightMax": cfg["drone"].get("weightMax"),
                "phone": cfg["drone"].get("phone_encrypted"),
                "delSts": cfg["drone"].get("delSts"),
                "plantProtector": cfg["drone"].get("plantProtector"),
                "uavPurpose": None,
                "uavState": None,
                "validSts": cfg["drone"].get("validSts"),
                "checkTime": None,
                "comm": None,
            }],
            "drivers": [{
                "remark": None,
                "pdiSeq": cfg["driver"].get("pdiSeq"),
                "userId": cfg["driver"].get("userId"),
                "name": cfg["driver"].get("name"),
                "cardtype": cfg["driver"].get("cardtype"),
                "cardno": cfg["driver"].get("cardno"),
                "licno": None,
                "licType": None,
                "lvlType": None,
                "lvlLevel": None,
                "lvlBvlos": None,
                "lvlTeacher": None,
                "dateIssue": cfg["driver"].get("dateIssue"),
                "dateLose": cfg["driver"].get("dateLose"),
                "phone": cfg["driver"].get("phone"),
                "pp": cfg["driver"].get("pp"),
                "uasCodes": [cfg["drone"].get("uasCode")],
            }]
        }
    except Exception:
        return None


def load_phone():
    phone = load_config().get("contact", {}).get("phone")
    if not phone:
        raise RuntimeError(f"config.json 缺少 contact.phone，请先在 {CONFIG_FILE} 中填写手机号")
    return phone


def solve_captcha(image_path: str):
    try:
        import ddddocr
        ocr = ddddocr.DdddOcr(show_ad=False)
        with open(image_path, "rb") as f:
            return ocr.classification(f.read())
    except Exception as e:
        print(f"⚠️ ddddocr 识别失败: {e}")
        return None


def dismiss_popup(page):
    try:
        page.evaluate(
            """
            () => {
                const btns = document.querySelectorAll('.ivu-modal-confirm-footer .ivu-btn-primary, .ivu-modal-footer .ivu-btn-primary');
                for (const btn of btns) {
                    if (btn.offsetParent !== null) { btn.click(); return true; }
                }
                return false;
            }
            """
        )
    except Exception:
        pass


def wait_for_login_component(page, timeout_s=20):
    deadline = time.time() + timeout_s
    js = """
    () => {
        const app = document.querySelector('#app');
        if (!app || !app.__vue__) return null;
        function find(vm, d) {
            if (d > 12) return null;
            if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
            for (const c of (vm.$children || [])) { const r = find(c, d + 1); if (r) return r; }
            return null;
        }
        const lm = find(app.__vue__, 0);
        if (!lm) return null;
        return {
            uuid: lm.txYzmUuid,
            hasCaptcha: !!lm.yzmImageCode,
            userForm: JSON.parse(JSON.stringify(lm.userForm))
        };
    }
    """
    while time.time() < deadline:
        try:
            res = page.evaluate(js)
            if res:
                return res
        except Exception:
            pass
        time.sleep(0.5)
    return None


def get_login_captcha(page):
    res = page.evaluate(
        """
        () => {
            const app = document.querySelector('#app').__vue__;
            function find(vm, d) {
                if (d > 12) return null;
                if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
                for (const c of (vm.$children || [])) { const r = find(c, d + 1); if (r) return r; }
                return null;
            }
            const lm = find(app, 0);
            return { imgBase64: lm.yzmImageCode, uuid: lm.txYzmUuid };
        }
        """
    )
    img_b64 = res["imgBase64"]
    if img_b64.startswith("data:"):
        img_b64 = img_b64.split(",", 1)[1]
    with open(CAPTCHA_FILE, "wb") as f:
        f.write(base64.b64decode(img_b64))
    return str(CAPTCHA_FILE), res["uuid"]


def login_via_sms(page):
    phone = load_phone()
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)
    dismiss_popup(page)
    comp = wait_for_login_component(page)
    if not comp:
        raise RuntimeError("找不到登录组件")

    captcha_path, _uuid = get_login_captcha(page)
    ocr = solve_captcha(captcha_path)
    print(f"验证码图片: {captcha_path}")
    print(f"OCR 识别: {ocr or '失败'}")
    captcha = input(f"请输入图形验证码（回车默认使用 OCR={ocr}）: ").strip() or (ocr or "")
    if not captcha:
        raise RuntimeError("图形验证码为空")

    sms_result = page.evaluate(
        """
        async ([phone, captcha]) => {
            const app = document.querySelector('#app').__vue__;
            function find(vm, d) {
                if (d > 12) return null;
                if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
                for (const c of (vm.$children || [])) { const r = find(c, d + 1); if (r) return r; }
                return null;
            }
            const lm = find(app, 0);
            lm.$set(lm.userForm, 'telephone', phone);
            lm.$set(lm.userForm, 'tcode', captcha);
            const resp = await fetch('/api/home/anon/sendSmsCode', {
                method: 'POST',
                headers: {'Content-Type':'application/json','devicetype':'PC'},
                body: JSON.stringify({mobileNum: phone, icode: captcha, uuid: lm.txYzmUuid, scene: '1'})
            });
            return await resp.json();
        }
        """,
        [phone, captcha],
    )
    print("发短信返回:", sms_result)
    if str(sms_result.get("code")) not in ["0", "1", 0, 1]:
        raise RuntimeError(f"短信发送失败: {sms_result}")

    sms = input("请输入短信验证码: ").strip()
    if not sms:
        raise RuntimeError("短信验证码为空")

    submit_result = page.evaluate(
        """
        ([smsCode]) => {
            const app = document.querySelector('#app').__vue__;
            function find(vm, d) {
                if (d > 12) return null;
                if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
                for (const c of (vm.$children || [])) { const r = find(c, d + 1); if (r) return r; }
                return null;
            }
            const lm = find(app, 0);
            lm.$set(lm.userForm, 'dcode', smsCode);
            try {
                lm.handleSubmit();
                return {ok:true};
            } catch(e) {
                return {ok:false, error:e.message};
            }
        }
        """,
        [sms],
    )
    print("提交登录返回:", submit_result)
    if not submit_result.get("ok"):
        time.sleep(1)
        page.evaluate(
            """
            () => {
                const app = document.querySelector('#app').__vue__;
                function find(vm, d) {
                    if (d > 12) return null;
                    if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
                    for (const c of (vm.$children || [])) { const r = find(c, d + 1); if (r) return r; }
                    return null;
                }
                const lm = find(app, 0);
                lm.handleSubmit();
            }
            """
        )

    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if '#/main' in page.url:
                break
        except Exception:
            pass
        time.sleep(0.5)
    time.sleep(2)
    return check_main_login(page)


def check_main_login(page):
    try:
        return page.evaluate(
            """
            () => {
                const sessionToken = localStorage.getItem('session_token');
                let nrosToken = null;
                try {
                    nrosToken = window.nros && window.nros.getToken ? window.nros.getToken() : null;
                } catch(e) {}
                const text = document.body ? document.body.innerText : '';
                const hasMainUi = /系统主页|运行管理|首页/.test(text || '');
                return {
                    url: location.href,
                    title: document.title,
                    nrosToken,
                    sessionToken,
                    hasMainLogin: !!(nrosToken || sessionToken || hasMainUi)
                };
            }
            """
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}


def open_fly_activity(page):
    page.goto(f"{BASE_URL}/#/main", wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)
    dismiss_popup(page)
    js = """
    () => {
        function findByText(root, regex, allowedClasses=[]) {
            const els = Array.from(root.querySelectorAll('div,span,li,a,button'));
            for (const el of els) {
                const text = (el.textContent || '').trim();
                if (regex.test(text)) {
                    const cls = (el.className || '').toString();
                    if (!allowedClasses.length || allowedClasses.some(c => cls.includes(c))) return el;
                }
            }
            return null;
        }
        const top = findByText(document, /^运行管理$/);
        if (top) top.click();
        const leftExpand = findByText(document, /^飞行活动申请$/);
        if (leftExpand) leftExpand.click();
        const general = findByText(document, /^一般飞行活动$/, ['ivu-menu-item', 'menuitem']);
        if (general) {
            general.click();
            return {ok:true};
        }
        return {ok:false, error:'一般飞行活动菜单未找到'};
    }
    """
    res = page.evaluate(js)
    if not res.get("ok"):
        raise RuntimeError(res)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            iframe_count = page.evaluate("() => document.querySelectorAll('iframe').length")
            if iframe_count:
                return True
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("进入飞行活动页面失败：未出现 iframe")


def get_iframe_auth(page):
    return page.evaluate(
        """
        async () => {
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            const src = iframe.src;
            const u = new URL(src, location.origin);
            const ticket = u.searchParams.get('ticket');
            const userName = u.searchParams.get('userName');
            const cookie = doc.cookie || '';
            const m = cookie.match(/(?:^|; )PUB-Token=([^;]+)/);
            const pubToken = m ? decodeURIComponent(m[1]) : null;
            return {ok: !!(ticket && userName && pubToken), ticket, userName, pubToken, src, cookie};
        }
        """
    )


def check_oapi_auth(page):
    return page.evaluate(
        """
        async () => {
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            const src = iframe.src;
            const u = new URL(src, location.origin);
            const ticket = u.searchParams.get('ticket');
            const userName = u.searchParams.get('userName');
            const cookie = doc.cookie || '';
            const m = cookie.match(/(?:^|; )PUB-Token=([^;]+)/);
            const pubToken = m ? decodeURIComponent(m[1]) : null;
            if (!(ticket && userName && pubToken)) return {ok:false, error:'missing auth', ticket, userName, pubToken};
            const resp = await iframe.contentWindow.fetch('/oapi/pub/planInfo/list?pageNum=1&pageSize=1&planTypes=11,12,13', {
                headers: {
                    'Authorization': 'Bearer ' + pubToken,
                    'pubUserName': userName,
                    'ticket': ticket,
                    'deviceType': 'PC'
                },
                credentials: 'include'
            });
            const text = await resp.text();
            let data = null;
            try { data = JSON.parse(text); } catch(e) {}
            return {ok: resp.ok, status: resp.status, data, raw: text.slice(0, 1200)};
        }
        """
    )


def get_latest_plan(page):
    return page.evaluate(
        """
        async () => {
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            const src = iframe.src;
            const u = new URL(src, location.origin);
            const ticket = u.searchParams.get('ticket');
            const userName = u.searchParams.get('userName');
            const cookie = doc.cookie || '';
            const m = cookie.match(/(?:^|; )PUB-Token=([^;]+)/);
            const pubToken = m ? decodeURIComponent(m[1]) : null;
            if (!(ticket && userName && pubToken)) return {ok:false, error:'missing auth'};
            const resp = await iframe.contentWindow.fetch('/oapi/pub/planInfo/list?pageNum=1&pageSize=5&planTypes=11,12,13', {
                headers: {
                    'Authorization': 'Bearer ' + pubToken,
                    'pubUserName': userName,
                    'ticket': ticket,
                    'deviceType': 'PC'
                },
                credentials: 'include'
            });
            const data = await resp.json();
            const rows = data && data.data && (data.data.rows || data.data.list || data.rows || data.list) || [];
            return {ok: true, total: rows.length, latest: rows[0] || null, data};
        }
        """
    )


def get_plan_detail(page, plan_id):
    return page.evaluate(
        """
        async ([planId]) => {
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            const src = iframe.src;
            const u = new URL(src, location.origin);
            const ticket = u.searchParams.get('ticket');
            const userName = u.searchParams.get('userName');
            const cookie = doc.cookie || '';
            const m = cookie.match(/(?:^|; )PUB-Token=([^;]+)/);
            const pubToken = m ? decodeURIComponent(m[1]) : null;
            if (!(ticket && userName && pubToken)) return {ok:false, error:'missing auth'};
            const resp = await iframe.contentWindow.fetch('/oapi/pub/planInfo/' + planId, {
                headers: {
                    'Authorization': 'Bearer ' + pubToken,
                    'pubUserName': userName,
                    'ticket': ticket,
                    'deviceType': 'PC'
                },
                credentials: 'include'
            });
            const data = await resp.json();
            return data && data.data ? data.data : data;
        }
        """,
        [plan_id]
    )


def wait_for_fly_add(page, timeout_s=30):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            res = page.evaluate(
                """
                () => {
                    const iframe = document.querySelector('iframe');
                    if (!iframe) return null;
                    const href = iframe.contentWindow.location.href;
                    const doc = iframe.contentDocument;
                    const bodyText = (doc.body && doc.body.innerText || '').slice(0, 1000);
                    const app = doc.querySelector('#app');
                    const vueNames = [];
                    if (app && app.__vue__) {
                        function walk(vm, depth) {
                            if (!vm || depth > 8) return;
                            const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                            if (name) vueNames.push(name);
                            for (const c of (vm.$children || [])) walk(c, depth + 1);
                        }
                        walk(app.__vue__, 0);
                    }
                    const uniqNames = Array.from(new Set(vueNames));
                    const ready = uniqNames.includes('FLY_INDEX_ADD') || bodyText.includes('无人驾驶航空器飞行活动申请');
                    return {href, hasVue: uniqNames.length > 0, vueNames: uniqNames.slice(0, 20), pageText: bodyText, ready};
                }
                """
            )
            if res and 'flyIndexAdd' in res['href'] and res.get('ready'):
                return res
        except Exception:
            pass
        time.sleep(1)
    return None


def open_new_fly_form(page):
    return page.evaluate(
        """
        () => {
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const app = iframe.contentDocument.querySelector('#app');
            if (!app || !app.__vue__) return {ok:false, error:'no vue root'};
            function findFlyIndex(vm, depth) {
                if (!vm || depth > 12) return null;
                const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                if (name === 'FLY_INDEX') return vm;
                for (const c of (vm.$children || [])) {
                    const r = findFlyIndex(c, depth + 1);
                    if (r) return r;
                }
                return null;
            }
            const comp = findFlyIndex(app.__vue__, 0);
            if (!comp) return {ok:false, error:'FLY_INDEX not found'};
            try {
                comp.addFly();
                return {ok:true};
            } catch (e) {
                return {ok:false, error:e.message};
            }
        }
        """
    )


def fill_new_form_from_detail(page, detail, plan_beg_new, plan_end_new):
    return page.evaluate(
        """
        ([detail, planBegNew, planEndNew]) => {
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            const app = doc.querySelector('#app');
            if (!app || !app.__vue__) return {ok:false, error:'no vue root'};

            function findMainForm(vm, depth) {
                if (!vm || depth > 15) return null;
                const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                if (name === 'FLY_INDEX_ADD' && vm.$data && vm.$data.form) return vm;
                for (const c of (vm.$children || [])) {
                    const r = findMainForm(c, depth + 1);
                    if (r) return r;
                }
                return null;
            }

            function visibleAddButtons(root) {
                return Array.from(root.querySelectorAll('button,span,div,a')).filter(el => {
                    const t = ((el.textContent || '').replace(/\s+/g, ' ').trim());
                    return t === '添加' && el.offsetParent !== null;
                });
            }

            function clickText(root, expected) {
                const all = Array.from(root.querySelectorAll('button,span,div,a'));
                for (const el of all) {
                    const t = ((el.textContent || '').replace(/\s+/g, ' ').trim());
                    if (t === expected && el.offsetParent !== null) {
                        el.click();
                        return {ok:true, text:t, tag:el.tagName, cls:(el.className || '').toString()};
                    }
                }
                return {ok:false, text:expected};
            }

            const comp = findMainForm(app.__vue__, 0);
            if (!comp) return {ok:false, error:'FLY_INDEX_ADD not found'};
            const f = comp.$data.form || {};
            const beforeKeys = Object.keys(comp.$data || {}).filter(k => /uav|driver|select|check|space|dialog|table|choose/i.test(k));
            const sourceSpace = (detail.spaces && detail.spaces[0]) || {};
            const sourceUav = Array.isArray(detail.uavs) && detail.uavs.length ? JSON.parse(JSON.stringify(detail.uavs[0])) : null;
            const sourceDriver = Array.isArray(detail.drivers) && detail.drivers.length ? JSON.parse(JSON.stringify(detail.drivers[0])) : null;

            const clickedNotice = clickText(doc, '我知道了');
            const addButtons = visibleAddButtons(doc);
            const clickedUavAdd = addButtons[0] ? (() => { addButtons[0].click(); return {ok:true, index:0}; })() : {ok:false};
            const clickedDriverAdd = addButtons[1] ? (() => { addButtons[1].click(); return {ok:true, index:1}; })() : {ok:false};

            f.planBeg = planBegNew;
            f.planEnd = planEndNew;
            if (f.planBegStr !== undefined) f.planBegStr = planBegNew;
            if (f.planEndStr !== undefined) f.planEndStr = planEndNew;
            if (detail.planType) f.planType = detail.planType;
            if (detail.taskType) f.taskType = detail.taskType;
            if (detail.txll) f.txll = detail.txll;
            if (detail.spcTop) f.spcTop = detail.spcTop;

            if (sourceSpace && Object.keys(sourceSpace).length) {
                const space = {
                    ...sourceSpace,
                    locationWgs84: sourceSpace.locationWgs84 || sourceSpace.polygonWgs84 || '',
                    polygonWgs84: sourceSpace.polygonWgs84 || sourceSpace.locationWgs84 || '',
                    spcShape: sourceSpace.spcShape || '1',
                    spcBottom: sourceSpace.spcBottom ?? 0,
                    spcTop: sourceSpace.spcTop ?? detail.spcTop ?? 120,
                    spcName: sourceSpace.spcName || '',
                    groupName: sourceSpace.groupName || '空域1',
                    spaceShape: sourceSpace.spaceShape || '面',
                    index: 0,
                    polyLinePoints: sourceSpace.polyLinePoints || [],
                    polygonPoints: sourceSpace.polygonPoints || [],
                    radius: sourceSpace.radius ?? null,
                    lineWidth: sourceSpace.lineWidth ?? null,
                };
                f.spaces = [space];
                comp.$data.spaceList = [JSON.parse(JSON.stringify(space))];
                comp.$data.oldSpaceList = [JSON.parse(JSON.stringify(space))];
                try {
                    if (typeof comp.callbackAddSpace === 'function') comp.callbackAddSpace([space]);
                } catch (e) {}
            }

            if (sourceUav) {
                f.uavs = [sourceUav];
                comp.$data.uavInfoList = [sourceUav];
                try {
                    if (typeof comp.callbackAddUavs === 'function') comp.callbackAddUavs([sourceUav]);
                } catch (e) {}
            }

            if (sourceDriver) {
                if (!sourceDriver.uasCodes && sourceUav && sourceUav.uasCode) sourceDriver.uasCodes = [sourceUav.uasCode];
                f.drivers = [sourceDriver];
                comp.$data.driverInfoList = [sourceDriver];
                comp.$data.currentRowDriver = sourceDriver;
                comp.$data.selectDriverIndex = 0;
                try {
                    if (typeof comp.callbackAddDrivers === 'function') comp.callbackAddDrivers([sourceDriver]);
                    if (typeof comp.handleSelectionDriverUavs === 'function' && sourceUav) comp.handleSelectionDriverUavs([sourceUav], sourceDriver);
                } catch (e) {}
            }

            if (typeof comp.$forceUpdate === 'function') comp.$forceUpdate();
            if (comp.$refs && comp.$refs.form && typeof comp.$refs.form.clearValidate === 'function') comp.$refs.form.clearValidate();

            return {
                ok: true,
                mode: 'stepwise-lite',
                compName: (comp.$options && (comp.$options.name || comp.$options._componentTag)) || '',
                clickedNotice,
                clickedUavAdd,
                clickedDriverAdd,
                planBeg: f.planBeg,
                planEnd: f.planEnd,
                spcTop: f.spcTop,
                txll: f.txll,
                uavCount: Array.isArray(comp.$data.uavInfoList) ? comp.$data.uavInfoList.length : 0,
                driverCount: Array.isArray(comp.$data.driverInfoList) ? comp.$data.driverInfoList.length : 0,
                spaceCount: Array.isArray(comp.$data.spaceList) ? comp.$data.spaceList.length : 0,
                stateKeys: beforeKeys,
                refKeys: comp.$refs ? Object.keys(comp.$refs) : [],
                firstSpace: comp.$data.spaceList && comp.$data.spaceList[0] ? {
                    locationWgs84: comp.$data.spaceList[0].locationWgs84,
                    polygonWgs84: comp.$data.spaceList[0].polygonWgs84,
                    spcTop: comp.$data.spaceList[0].spcTop,
                    spcBottom: comp.$data.spaceList[0].spcBottom,
                } : null,
            };
        }
        """,
        [detail, plan_beg_new, plan_end_new],
    )


def get_next_monday_same_time(plan_beg: str, plan_end: str):
    return get_next_weekday_same_time(plan_beg, plan_end, 0)


def get_next_tuesday_same_time(plan_beg: str, plan_end: str):
    return get_next_weekday_same_time(plan_beg, plan_end, 1)


def update_copied_form_times(page, plan_beg_new, plan_end_new):
    return page.evaluate(
        """
        ([planBegNew, planEndNew]) => {
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            const rootEl = doc.querySelector('#app') || doc.body;
            if (!rootEl) return {ok:false, error:'no root el'};
            function collectVueRoots() {
                const roots = [];
                const all = [rootEl, ...Array.from(rootEl.querySelectorAll('*')).slice(0, 300)];
                for (const el of all) {
                    if (el && el.__vue__) roots.push(el.__vue__);
                }
                return roots;
            }
            function findMainForm(vm, depth) {
                if (!vm || depth > 15) return null;
                const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                if (name === 'FLY_INDEX_ADD' && vm.$data && vm.$data.form) return vm;
                if (vm.$data && vm.$data.form && vm.$data.uavInfoList !== undefined && vm.$data.driverInfoList !== undefined) return vm;
                for (const c of (vm.$children || [])) {
                    const r = findMainForm(c, depth + 1);
                    if (r) return r;
                }
                return null;
            }
            let comp = null;
            for (const rootVm of collectVueRoots()) {
                comp = findMainForm(rootVm, 0);
                if (comp) break;
            }
            if (!comp) return {ok:false, error:'main form not found'};
            const f = comp.$data.form;
            f.planBeg = planBegNew;
            f.planEnd = planEndNew;
            if (f.planBegStr !== undefined) f.planBegStr = planBegNew;
            if (f.planEndStr !== undefined) f.planEndStr = planEndNew;
            if (typeof comp.$forceUpdate === 'function') comp.$forceUpdate();
            return {
                ok:true,
                compName: (comp.$options && (comp.$options.name || comp.$options._componentTag)) || '',
                planBeg: f.planBeg,
                planEnd: f.planEnd,
                planBegStr: f.planBegStr,
                planEndStr: f.planEndStr,
                uavCount: Array.isArray(comp.$data.uavInfoList) ? comp.$data.uavInfoList.length : null,
                driverCount: Array.isArray(comp.$data.driverInfoList) ? comp.$data.driverInfoList.length : null,
            };
        }
        """,
        [plan_beg_new, plan_end_new],
    )


def trigger_submit_copied_form(page):
    return page.evaluate(
        """
        () => {
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;

            const knowBtn = Array.from(doc.querySelectorAll('button,span,div')).find(el => {
                const t = (el.textContent || '').replace(/\s+/g, ' ').trim();
                return t === '我知道了';
            });
            if (knowBtn) knowBtn.click();

            const submitBtn = Array.from(doc.querySelectorAll('button,span,div,a')).find(el => {
                const t = (el.textContent || '').replace(/\s+/g, ' ').trim();
                const cls = (el.className || '').toString();
                return t === '提交申请' && cls.includes('el-button');
            });
            if (submitBtn) {
                submitBtn.click();
                return {ok:true, method:'button-click'};
            }

            const rootEl = doc.querySelector('#app') || doc.body;
            function collectVueRoots() {
                const roots = [];
                const all = [rootEl, ...Array.from(rootEl.querySelectorAll('*')).slice(0, 300)];
                for (const el of all) {
                    if (el && el.__vue__) roots.push(el.__vue__);
                }
                return roots;
            }
            function findMainForm(vm, depth) {
                if (!vm || depth > 15) return null;
                const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                if (name === 'FLY_INDEX_ADD' && vm.$data && vm.$data.form) return vm;
                if (vm.$data && vm.$data.form && vm.$data.uavInfoList !== undefined && vm.$data.driverInfoList !== undefined) return vm;
                for (const c of (vm.$children || [])) {
                    const r = findMainForm(c, depth + 1);
                    if (r) return r;
                }
                return null;
            }
            let comp = null;
            for (const rootVm of collectVueRoots()) {
                comp = findMainForm(rootVm, 0);
                if (comp) break;
            }
            if (comp && typeof comp.submitPlan === 'function') {
                comp.submitPlan();
                return {ok:true, method:'submitPlan()'};
            }
            return {ok:false, error:'submit trigger not found'};
        }
        """
    )


def inspect_add_dialogs(page, detail, plan_beg_new, plan_end_new):
    return page.evaluate(
        """
        ([detail, planBegNew, planEndNew]) => {
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            const app = doc.querySelector('#app');
            if (!app || !app.__vue__) return {ok:false, error:'no vue root'};

            function findMainForm(vm, depth) {
                if (!vm || depth > 15) return null;
                const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                if (name === 'FLY_INDEX_ADD' && vm.$data && vm.$data.form) return vm;
                for (const c of (vm.$children || [])) {
                    const r = findMainForm(c, depth + 1);
                    if (r) return r;
                }
                return null;
            }

            function textOf(el) {
                return ((el && el.textContent) || '').replace(/\s+/g, ' ').trim();
            }

            function visibleAddButtons(root) {
                return Array.from(root.querySelectorAll('button,span,div,a')).filter(el => textOf(el) === '添加' && el.offsetParent !== null);
            }

            function snapshotDialogs(label) {
                const dialogs = Array.from(doc.querySelectorAll('.el-dialog, .ivu-modal, [role="dialog"]')).filter(el => el.offsetParent !== null);
                return {
                    label,
                    dialogCount: dialogs.length,
                    dialogs: dialogs.slice(0, 4).map((dlg, idx) => ({
                        index: idx,
                        cls: (dlg.className || '').toString(),
                        title: textOf(dlg.querySelector('.el-dialog__title, .ivu-modal-header-inner, .ivu-modal-header, .el-dialog__header')),
                        buttons: Array.from(dlg.querySelectorAll('button,span,a,div')).map(el => textOf(el)).filter(Boolean).slice(0, 20),
                        tables: Array.from(dlg.querySelectorAll('table')).map((tbl, i) => ({
                            index: i,
                            headers: Array.from(tbl.querySelectorAll('th')).map(th => textOf(th)).filter(Boolean).slice(0, 12),
                            firstRows: Array.from(tbl.querySelectorAll('tr')).slice(0, 4).map(tr => Array.from(tr.querySelectorAll('td')).map(td => textOf(td)).filter(Boolean).slice(0, 12))
                        })),
                        text: textOf(dlg).slice(0, 1200)
                    }))
                };
            }

            const comp = findMainForm(app.__vue__, 0);
            if (!comp) return {ok:false, error:'FLY_INDEX_ADD not found'};
            const f = comp.$data.form || {};
            f.planBeg = planBegNew;
            f.planEnd = planEndNew;
            if (f.planBegStr !== undefined) f.planBegStr = planBegNew;
            if (f.planEndStr !== undefined) f.planEndStr = planEndNew;

            const before = snapshotDialogs('before');
            const addButtons = visibleAddButtons(doc);
            const result = {ok:true, before, addButtonCount:addButtons.length};

            if (addButtons[0]) {
                addButtons[0].click();
                result.afterUavClick = snapshotDialogs('after_uav_click');
            }
            if (addButtons[1]) {
                addButtons[1].click();
                result.afterDriverClick = snapshotDialogs('after_driver_click');
            }

            result.compRefKeys = comp.$refs ? Object.keys(comp.$refs) : [];
            result.dataKeys = Object.keys(comp.$data || {}).filter(k => /uav|driver|select|check|space|dialog|table|choose/i.test(k));
            return result;
        }
        """,
        [detail, plan_beg_new, plan_end_new],
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--login", action="store_true")
    parser.add_argument("--ensure-fly", action="store_true")
    parser.add_argument("--latest-plan", action="store_true")
    parser.add_argument("--submit-copy-next-monday", action="store_true")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PERSIST_DIR),
            headless=args.headless,
            viewport={"width": 1280, "height": 900},
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        if args.status:
            page.goto(f"{BASE_URL}/#/main", wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)
            dismiss_popup(page)
            status = check_main_login(page)
            print("主站状态:")
            print(json.dumps(status, ensure_ascii=False, indent=2))
            if status.get("hasMainLogin"):
                try:
                    open_fly_activity(page)
                    oapi = check_oapi_auth(page)
                    print("飞行活动 oapi 状态:")
                    print(json.dumps(oapi, ensure_ascii=False, indent=2)[:3000])
                except Exception as e:
                    print("飞行活动检查失败:", e)
            context.close()
            return

        if args.login:
            page.goto(f"{BASE_URL}/#/main", wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)
            dismiss_popup(page)
            status = check_main_login(page)
            if status.get("hasMainLogin"):
                print("已复用登录态，无需重新登录")
                print(json.dumps(status, ensure_ascii=False, indent=2))
            else:
                result = login_via_sms(page)
                print("登录结果:")
                print(json.dumps(result, ensure_ascii=False, indent=2))
            context.close()
            return

        if args.ensure_fly:
            page.goto(f"{BASE_URL}/#/main", wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)
            dismiss_popup(page)
            status = check_main_login(page)
            if not status.get("hasMainLogin"):
                result = login_via_sms(page)
                print("登录结果:")
                print(json.dumps(result, ensure_ascii=False, indent=2))
            open_fly_activity(page)
            auth = get_iframe_auth(page)
            print("iframe 认证:")
            print(json.dumps(auth, ensure_ascii=False, indent=2))
            oapi = check_oapi_auth(page)
            print("oapi 检查:")
            print(json.dumps(oapi, ensure_ascii=False, indent=2)[:3000])
            context.close()
            return

        if args.latest_plan:
            page.goto(f"{BASE_URL}/#/main", wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)
            dismiss_popup(page)
            status = check_main_login(page)
            if not status.get("hasMainLogin"):
                result = login_via_sms(page)
                print("登录结果:")
                print(json.dumps(result, ensure_ascii=False, indent=2))
            open_fly_activity(page)
            latest = get_latest_plan(page)
            print("最近计划:")
            print(json.dumps(latest, ensure_ascii=False, indent=2))
            if latest.get('ok'):
                detail = get_plan_detail(page, latest['latest']['planId'])
                print("最近计划详情:")
                print(json.dumps(detail, ensure_ascii=False, indent=2)[:4000])
            context.close()
            return

        if args.submit_copy_next_monday:
            page.goto(f"{BASE_URL}/#/main", wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)
            dismiss_popup(page)
            status = check_main_login(page)
            if not status.get("hasMainLogin"):
                result = login_via_sms(page)
                print("登录结果:")
                print(json.dumps(result, ensure_ascii=False, indent=2))
            open_fly_activity(page)
            latest = get_latest_plan(page)
            if not latest.get('ok'):
                print(json.dumps(latest, ensure_ascii=False, indent=2))
                context.close()
                return
            detail = get_plan_detail(page, latest['latest']['planId'])
            full_profile = load_full_submit_profile()
            if full_profile:
                detail['uavs'] = full_profile['uavs']
                detail['drivers'] = full_profile['drivers']
            print("使用最近计划:")
            print(json.dumps(detail, ensure_ascii=False, indent=2)[:3000])
            next_beg, next_end = get_next_tuesday_same_time(detail['planBeg'], detail['planEnd'])
            print(f"目标时间: {next_beg} ~ {next_end}")
            add_res = open_new_fly_form(page)
            print("打开新增页:", json.dumps(add_res, ensure_ascii=False))
            fly_add = wait_for_fly_add(page)
            print("等待 flyIndexAdd:", json.dumps(fly_add, ensure_ascii=False))
            if not fly_add:
                context.close()
                return
            upd = fill_new_form_from_detail(page, detail, next_beg, next_end)
            print("填充表单:")
            print(json.dumps(upd, ensure_ascii=False, indent=2)[:4000])
            sub = trigger_submit_copied_form(page)
            print("触发提交:", json.dumps(sub, ensure_ascii=False))
            time.sleep(6)
            latest2 = get_latest_plan(page)
            print("提交后最近计划:")
            print(json.dumps(latest2, ensure_ascii=False, indent=2))
            context.close()
            return

        parser.print_help()
        context.close()


if __name__ == '__main__':
    main()
