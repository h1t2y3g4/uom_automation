#!/usr/bin/env python3
"""
uom_core.py - UOM 持久化浏览器核心能力与统一 CLI 入口

职责：
- 持久化 Playwright profile 复用登录态
- 主站登录态 / 飞行活动 iframe oapi 认证检查
- 进入 一般飞行活动
- 读取最近计划 / 详情
- 打开新增页 / 半自动填充 / 探测 / 提交
- 提供统一命令入口，避免多个脚本各自复制前半段流程

用法：
  python3 uom_core.py status
  python3 uom_core.py login
  python3 uom_core.py ensure-fly
  python3 uom_core.py latest-plan
  python3 uom_core.py probe
  python3 uom_core.py semiauto
  python3 uom_core.py open-browser
  python3 uom_core.py submit-copy-next-tuesday
"""

import argparse
import base64
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
PERSIST_DIR = SCRIPT_DIR / ".playwright-uom-profile"
CAPTCHA_FILE = Path("/tmp/uom_persistent_captcha.png")
BASE_URL = "https://uom.caac.gov.cn"
MANUAL_SELECTION_LOG = SCRIPT_DIR / "manual_selection_log.json"


def get_next_weekday_same_time(plan_beg: str, plan_end: str, target_weekday: int):
    b = datetime.strptime(plan_beg, '%Y-%m-%d %H:%M:%S')
    e = datetime.strptime(plan_end, '%Y-%m-%d %H:%M:%S')
    days_ahead = (target_weekday - b.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    nb = b + timedelta(days=days_ahead)
    ne = e + timedelta(days=days_ahead)
    return nb.strftime('%Y-%m-%d %H:%M:%S'), ne.strftime('%Y-%m-%d %H:%M:%S')


def get_next_monday_same_time(plan_beg: str, plan_end: str):
    return get_next_weekday_same_time(plan_beg, plan_end, 0)


def get_next_tuesday_same_time(plan_beg: str, plan_end: str):
    return get_next_weekday_same_time(plan_beg, plan_end, 1)


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
    js = r"""
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
            } catch (e) {
                return {ok:false, error:e.message};
            }
        }
        """,
        [sms],
    )
    if not submit_result.get("ok"):
        return submit_result

    deadline = time.time() + 20
    while time.time() < deadline:
        status = check_main_login(page)
        if status.get("hasMainLogin"):
            return {"ok": True, "status": status}
        time.sleep(1)
    return {"ok": False, "error": "登录后仍未进入主站", "status": check_main_login(page)}


def check_main_login(page):
    try:
        return page.evaluate(
            r"""
            () => {
                const sessionToken = localStorage.getItem('session_token');
                let nrosToken = null;
                try {
                    nrosToken = window.nros && window.nros.getToken ? window.nros.getToken() : null;
                } catch(e) {}
                const text = document.body ? document.body.innerText : '';
                const hasMainUi = /系统主页|运行管理|首页/.test(text || '');
                const href = location.href || '';
                const onLoginPage = /#\/login(?:$|[?#])/.test(href);
                return {
                    url: href,
                    title: document.title,
                    nrosToken,
                    sessionToken,
                    hasMainUi,
                    onLoginPage,
                    hasMainLogin: !!(!onLoginPage && (nrosToken || hasMainUi))
                };
            }
            """
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}


def debug_menu_snapshot(page):
    try:
        return page.evaluate(
            r"""
            () => {
                const href = location.href || '';
                const title = document.title || '';
                const text = ((document.body && document.body.innerText) || '').replace(/\s+/g, ' ').trim();
                const snippets = [];
                const candidates = Array.from(document.querySelectorAll('div,span,li,a,button')).slice(0, 800);
                for (const el of candidates) {
                    const t = ((el.textContent || '').replace(/\s+/g, ' ').trim());
                    if (!t) continue;
                    if (/运行管理|飞行活动申请|一般飞行活动/.test(t)) {
                        snippets.push({
                            text: t,
                            tag: el.tagName,
                            cls: (el.className || '').toString().slice(0, 200)
                        });
                    }
                }
                return {
                    url: href,
                    title,
                    bodyTextHead: text.slice(0, 1200),
                    menuHits: snippets.slice(0, 40),
                    iframeCount: document.querySelectorAll('iframe').length,
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
    js = r"""
    () => {
        const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
        function norm(s) {
            return (s || '').replace(/\s+/g, ' ').trim();
        }
        function visible(el) {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            if (!style || style.visibility === 'hidden' || style.display === 'none') return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        }
        function clickish(el) {
            if (!el) return false;
            el.scrollIntoView({block: 'center', inline: 'center'});
            for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
            }
            return true;
        }
        function findBest(regex, classHints=[]) {
            const els = Array.from(document.querySelectorAll('div,span,li,a,button,i,p'));
            const scored = [];
            for (const el of els) {
                const text = norm(el.textContent || '');
                if (!text || !regex.test(text)) continue;
                const cls = (el.className || '').toString();
                const score = (visible(el) ? 100 : 0)
                    + (classHints.some(c => cls.includes(c)) ? 30 : 0)
                    + (/menu|nav|item|sub|title/i.test(cls) ? 10 : 0)
                    - Math.abs(text.length - String(regex).length);
                scored.push({el, text, cls, score});
            }
            scored.sort((a, b) => b.score - a.score);
            return scored[0] || null;
        }

        return (async () => {
            const top = findBest(/^运行管理$/);
            if (top) {
                clickish(top.el);
                await sleep(800);
            }
            const leftExpand = findBest(/^飞行活动申请$/);
            if (leftExpand) {
                clickish(leftExpand.el);
                await sleep(1200);
            }
            const general = findBest(/^一般飞行活动$/, ['ivu-menu-item', 'menuitem', 'el-menu-item']);
            if (general) {
                clickish(general.el);
                return {ok:true, clicked:{top: top && top.text, leftExpand: leftExpand && leftExpand.text, general: general.text}};
            }
            return {
                ok:false,
                error:'一般飞行活动菜单未找到',
                debug:{
                    top: top ? {text: top.text, cls: top.cls} : null,
                    leftExpand: leftExpand ? {text: leftExpand.text, cls: leftExpand.cls} : null,
                    menuTexts: Array.from(document.querySelectorAll('div,span,li,a,button')).map(el => norm(el.textContent || '')).filter(Boolean).filter(t => /运行管理|飞行活动申请|一般飞行活动/.test(t)).slice(0, 30)
                }
            };
        })();
    }
    """
    res = page.evaluate(js)
    if not res.get("ok"):
        raise RuntimeError({**res, "snapshot": debug_menu_snapshot(page)})
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            iframe_count = page.evaluate("() => document.querySelectorAll('iframe').length")
            if iframe_count:
                return True
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError({"error": "进入飞行活动页面失败：未出现 iframe", "snapshot": debug_menu_snapshot(page)})


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
        r"""
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
                for (const c of (vm.$children || [])) {
                    const r = findMainForm(c, depth + 1);
                    if (r) return r;
                }
                return null;
            }
            let comp = null;
            for (const root of collectVueRoots()) {
                comp = findMainForm(root, 0);
                if (comp) break;
            }
            if (!comp) return {ok:false, error:'FLY_INDEX_ADD not found'};
            const f = comp.$data.form || {};
            f.planBeg = planBegNew;
            f.planEnd = planEndNew;
            if (f.planBegStr !== undefined) f.planBegStr = planBegNew;
            if (f.planEndStr !== undefined) f.planEndStr = planEndNew;
            if (typeof comp.$forceUpdate === 'function') comp.$forceUpdate();
            return {ok:true, planBeg:f.planBeg, planEnd:f.planEnd};
        }
        """,
        [plan_beg_new, plan_end_new],
    )


def trigger_submit_copied_form(page):
    return page.evaluate(
        r"""
        async () => {
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
            const comp = findMainForm(app.__vue__, 0);
            if (!comp) return {ok:false, error:'FLY_INDEX_ADD not found'};
            const payload = {
                ok:true,
                before: {
                    uavs: Array.isArray(comp.$data.form && comp.$data.form.uavs) ? comp.$data.form.uavs.length : null,
                    drivers: Array.isArray(comp.$data.form && comp.$data.form.drivers) ? comp.$data.form.drivers.length : null,
                    spaces: Array.isArray(comp.$data.form && comp.$data.form.spaces) ? comp.$data.form.spaces.length : null,
                }
            };
            if (comp.$refs && comp.$refs.form && typeof comp.$refs.form.validate === 'function') {
                payload.validate = await new Promise(resolve => {
                    comp.$refs.form.validate((valid, fields) => {
                        resolve({valid, fields: fields ? Object.keys(fields) : []});
                    });
                });
            }
            const submitBtn = Array.from(doc.querySelectorAll('button,span,div,a')).find(el => ((el.textContent || '').replace(/\s+/g, ' ').trim()) === '提交申请' && el.offsetParent !== null);
            if (submitBtn) {
                submitBtn.click();
                payload.clickedSubmitButton = true;
            } else {
                payload.clickedSubmitButton = false;
            }
            try {
                if (typeof comp.submitPlan === 'function') {
                    comp.submitPlan();
                    payload.calledSubmitPlan = true;
                } else {
                    payload.calledSubmitPlan = false;
                }
            } catch (e) {
                payload.submitPlanError = e.message;
            }
            return payload;
        }
        """
    )


def inspect_add_dialogs(page, detail, plan_beg_new, plan_end_new):
    return page.evaluate(
        r"""
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

            function dumpVisibleDialogs(root) {
                return Array.from(root.querySelectorAll('.ivu-modal-wrap,.ivu-drawer-wrap,[role="dialog"]')).filter(el => el.offsetParent !== null).map(el => ({
                    text: ((el.innerText || '').replace(/\s+/g, ' ').trim()).slice(0, 1000),
                    cls: (el.className || '').toString(),
                }));
            }

            const comp = findMainForm(app.__vue__, 0);
            if (!comp) return {ok:false, error:'FLY_INDEX_ADD not found'};
            const fill = {
                planBeg: planBegNew,
                planEnd: planEndNew,
                uavSourceCount: Array.isArray(detail.uavs) ? detail.uavs.length : 0,
                driverSourceCount: Array.isArray(detail.drivers) ? detail.drivers.length : 0,
            };
            const addButtons = Array.from(doc.querySelectorAll('button,span,div,a')).filter(el => ((el.textContent || '').replace(/\s+/g, ' ').trim()) === '添加' && el.offsetParent !== null);
            if (addButtons[0]) addButtons[0].click();
            if (addButtons[1]) addButtons[1].click();
            return {
                ok:true,
                fill,
                visibleDialogs: dumpVisibleDialogs(doc),
                refKeys: comp.$refs ? Object.keys(comp.$refs) : [],
                dataKeys: Object.keys(comp.$data || {}).filter(k => /uav|driver|select|check|space|dialog|table|choose/i.test(k)),
            };
        }
        """,
        [detail, plan_beg_new, plan_end_new],
    )


def js_eval(page, expression, fallback_label, arg=None):
    try:
        if arg is None:
            return page.evaluate(expression)
        return page.evaluate(expression, arg)
    except Exception as e:
        return {"ok": False, "error": str(e), "stage": fallback_label}


def get_form_debug_snapshot(page, stage):
    return js_eval(page, """
    ([stage]) => {
      const iframe = document.querySelector('iframe');
      if (!iframe) return {ok:false, stage, error:'no iframe'};
      const doc = iframe.contentDocument;
      if (!doc) return {ok:false, stage, error:'no iframe doc'};
      const app = doc.querySelector('#app');
      function find(vm,d){ if(!vm||d>15) return null; const n=(vm.$options&&(vm.$options.name||vm.$options._componentTag))||''; if(n==='FLY_INDEX_ADD'&&vm.$data&&vm.$data.form) return vm; for(const c of (vm.$children||[])){ const r=find(c,d+1); if(r) return r; } return null; }
      const comp = app && app.__vue__ ? find(app.__vue__,0) : null;
      const bodyText = (doc.body && doc.body.innerText || '').slice(0, 4000);
      if (!comp) {
        return {
          ok:false,
          stage,
          error:'FLY_INDEX_ADD not found',
          hasApp: !!app,
          hasVue: !!(app && app.__vue__),
          href: iframe.contentWindow ? iframe.contentWindow.location.href : null,
          text: bodyText
        };
      }
      const dataKeys = Object.keys(comp.$data || {});
      const interestingKeys = dataKeys.filter(k => /uav|driver|select|check|space|dialog|table|choose/i.test(k));
      const form = comp.$data.form || {};
      const payload = {
        ok:true,
        stage,
        compName: (comp.$options && (comp.$options.name || comp.$options._componentTag)) || '',
        href: iframe.contentWindow ? iframe.contentWindow.location.href : null,
        refKeys: comp.$refs ? Object.keys(comp.$refs) : [],
        interestingKeys,
        flags: Object.fromEntries(interestingKeys.slice(0, 80).map(k => {
          const v = comp.$data[k];
          if (Array.isArray(v)) return [k, {type:'array', length:v.length}];
          if (v && typeof v === 'object') return [k, {type:'object', keys:Object.keys(v).slice(0,10)}];
          return [k, v];
        })),
        form: {
          planBeg: form.planBeg ? String(form.planBeg) : null,
          planEnd: form.planEnd ? String(form.planEnd) : null,
          uavs: Array.isArray(form.uavs) ? form.uavs.length : null,
          drivers: Array.isArray(form.drivers) ? form.drivers.length : null,
          spaces: Array.isArray(form.spaces) ? form.spaces.length : null,
        },
        text: bodyText
      };
      if (comp.$refs && comp.$refs.form && typeof comp.$refs.form.validate === 'function') {
        return new Promise(resolve => {
          comp.$refs.form.validate((valid, fields) => {
            payload.valid = valid;
            payload.fields = Object.keys(fields || {});
            resolve(payload);
          });
        });
      }
      payload.valid = null;
      payload.fields = [];
      return payload;
    }
    """, stage, [stage])


def launch_context(headless=False):
    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    p = sync_playwright().start()
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PERSIST_DIR),
        headless=headless,
        viewport={"width": 1280, "height": 900},
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    page = context.pages[0] if context.pages else context.new_page()
    return p, context, page


def close_context(playwright_handle, context):
    try:
        context.close()
    finally:
        playwright_handle.stop()


def ensure_main_page(page, timeout=120000, settle_seconds=8):
    page.goto(f'{BASE_URL}/#/main', wait_until='domcontentloaded', timeout=timeout)
    time.sleep(settle_seconds)
    dismiss_popup(page)
    return check_main_login(page)


def require_reliable_main_login(status, context, message):
    if status.get('onLoginPage') or '#/login' in (status.get('url') or '') or not status.get('hasMainLogin'):
        print(message)
        context.close()
        raise SystemExit(2)


def fetch_latest_detail(page):
    latest = get_latest_plan(page)
    if not latest.get('ok'):
        return None, latest, None
    detail = get_plan_detail(page, latest['latest']['planId'])
    full_profile = load_full_submit_profile()
    if full_profile:
        detail['uavs'] = full_profile['uavs']
        detail['drivers'] = full_profile['drivers']
    return latest, None, detail
