#!/usr/bin/env python3
"""
uom_core.py - UOM 持久化浏览器核心能力与统一 CLI 入口

职责：
- 持久化 Playwright profile 复用登录态
- 主站登录态 / 飞行活动 iframe oapi 认证检查
- 进入 一般飞行活动
- 读取最近计划 / 详情
- 打开新增页 / 自动填充 / 提交
- 提供公共底层能力，供上层脚本复用

说明：
- 这个文件主要承载底层函数，不是推荐的人类主入口
- 常用入口优先使用：uom_login.py / uom_submit_fly_plan.py
"""

import base64
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
CONFIG_FILE = PROJECT_ROOT / "config" / "config.json"
AIRSPACE_FILE = PROJECT_ROOT / "config" / "airspace.json"
SUBMIT_PLAN_FILE = PROJECT_ROOT / "config" / "submit_plan.json"
PERSIST_DIR = PROJECT_ROOT / ".playwright-uom-profile"
CAPTCHA_FILE = Path("/tmp/uom_persistent_captcha.png")
BASE_URL = "https://uom.caac.gov.cn"
MANUAL_SELECTION_LOG = PROJECT_ROOT / "log" / "manual_selection_log.json"
DEFAULT_RECENT_PLAN_DETAILS_FILE = PROJECT_ROOT / "log" / "uom_recent_plan_details.json"


def parse_local_datetime(value: str):
    return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')


def format_local_datetime(value: datetime):
    return value.strftime('%Y-%m-%d %H:%M:%S')


def get_debug_target_plan_time(plan_beg: str, plan_end: str):
    b = parse_local_datetime(plan_beg)
    e = parse_local_datetime(plan_end)
    target_date = datetime.now().date() + timedelta(days=1)
    nb = datetime.combine(target_date, b.time())
    ne = datetime.combine(target_date, e.time())
    if ne <= nb:
        ne = ne + timedelta(days=1)
    return format_local_datetime(nb), format_local_datetime(ne)


def get_next_monday_same_time(plan_beg: str, plan_end: str):
    b = parse_local_datetime(plan_beg)
    e = parse_local_datetime(plan_end)
    days_ahead = (0 - b.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    nb = b + timedelta(days=days_ahead)
    ne = e + timedelta(days=days_ahead)
    return format_local_datetime(nb), format_local_datetime(ne)


def get_tomorrow_same_time(plan_beg: str, plan_end: str):
    b = parse_local_datetime(plan_beg)
    e = parse_local_datetime(plan_end)
    nb = b + timedelta(days=1)
    ne = e + timedelta(days=1)
    return format_local_datetime(nb), format_local_datetime(ne)


def get_next_tuesday_same_time(plan_beg: str, plan_end: str):
    return get_debug_target_plan_time(plan_beg, plan_end)


def get_timezone_from_config(cfg):
    tz_name = (
        cfg.get('plan_defaults', {}).get('timezone')
        or cfg.get('time', {}).get('timezone')
        or 'UTC+8'
    )
    if tz_name in ('UTC+8', 'UTC+08:00', 'Asia/Shanghai'):
        return timezone(timedelta(hours=8)), tz_name
    if tz_name.startswith('UTC'):
        sign = 1
        offset = tz_name[3:]
        if offset.startswith('+'):
            sign = 1
            offset = offset[1:]
        elif offset.startswith('-'):
            sign = -1
            offset = offset[1:]
        parts = offset.split(':') if offset else ['0']
        hours = int(parts[0] or '0')
        minutes = int(parts[1] or '0') if len(parts) > 1 else 0
        return timezone(sign * timedelta(hours=hours, minutes=minutes)), tz_name
    return ZoneInfo(tz_name), tz_name


def normalize_space_payload(space, default_top=None):
    if not isinstance(space, dict):
        raise ValueError(f'space 必须是对象: {space!r}')
    polygon = space.get('polygonWgs84') or space.get('locationWgs84') or space.get('points')
    if not polygon:
        raise ValueError(f'space 缺少 polygonWgs84/locationWgs84/points: {space!r}')
    spc_bottom = space.get('spcBottom', 0)
    spc_top = space.get('spcTop', default_top if default_top is not None else 120)
    return {
        **space,
        'locationWgs84': polygon,
        'polygonWgs84': polygon,
        'spcBottom': spc_bottom,
        'spcTop': spc_top,
        'spcName': space.get('spcName') or space.get('name') or '',
        'groupName': space.get('groupName') or '空域1',
        'spaceShape': space.get('spaceShape') or '面',
        'spcShape': space.get('spcShape') or '1',
    }


def find_cached_airspace_by_name(airspace_cache, name):
    items = airspace_cache.get('items', []) if isinstance(airspace_cache, dict) else []
    for item in items:
        if item.get('name') == name:
            return item
    raise ValueError(f'airspace.json 中找不到常用空域: {name}')


def get_first_cached_airspace(airspace_cache):
    items = airspace_cache.get('items', []) if isinstance(airspace_cache, dict) else []
    if not items:
        raise ValueError('airspace.json 的 items 为空，无法作为保底空域来源')
    return items[0]


def utc_timestamps_to_local_pair(start_ts, end_ts, cfg):
    tzinfo, tz_name = get_timezone_from_config(cfg)
    start_dt = datetime.fromtimestamp(int(start_ts), timezone.utc).astimezone(tzinfo)
    end_dt = datetime.fromtimestamp(int(end_ts), timezone.utc).astimezone(tzinfo)
    if end_dt <= start_dt:
        raise ValueError(f'结束时间必须晚于开始时间: start={start_ts}, end={end_ts}, timezone={tz_name}')
    return format_local_datetime(start_dt), format_local_datetime(end_dt)


def normalize_time_pair(item, cfg):
    if not isinstance(item, dict):
        raise ValueError(f'time item 必须是对象: {item!r}')
    start_ts = item.get('start_utc_ts', item.get('startUtcTs'))
    end_ts = item.get('end_utc_ts', item.get('endUtcTs'))
    if start_ts is None or end_ts is None:
        raise ValueError(f'time item 缺少 start_utc_ts/end_utc_ts: {item!r}')
    plan_beg, plan_end = utc_timestamps_to_local_pair(start_ts, end_ts, cfg)
    return {
        'planBeg': plan_beg,
        'planEnd': plan_end,
        'source': 'config_list',
        'startUtcTs': int(start_ts),
        'endUtcTs': int(end_ts),
    }


def normalize_submission_plan_item(item, cfg, airspace_cache):
    if not isinstance(item, dict):
        raise ValueError(f'submit plan item 必须是对象: {item!r}')
    plan_beg = item.get('planBeg')
    plan_end = item.get('planEnd')
    if not plan_beg or not plan_end:
        raise ValueError(f'submit plan item 缺少 planBeg/planEnd: {item!r}')
    airspace = item.get('airspace')
    if not isinstance(airspace, dict):
        raise ValueError(f'submit plan item 缺少 airspace 对象: {item!r}')
    airspace_type = airspace.get('type')
    default_top = cfg.get('plan_defaults', {}).get('spcTop')
    if airspace_type == 'common_ref':
        ref_name = airspace.get('name')
        if not ref_name:
            raise ValueError(f'common_ref 缺少 name: {item!r}')
        cached = find_cached_airspace_by_name(airspace_cache, ref_name)
        space = normalize_space_payload(cached, default_top=default_top)
        return {
            'planBeg': plan_beg,
            'planEnd': plan_end,
            'source': 'submit_plan_file',
            'airspaceSource': 'airspace_cache',
            'airspaceRefName': ref_name,
            'space': space,
        }
    if airspace_type == 'polygon':
        inline_space = airspace.get('space')
        space = normalize_space_payload(inline_space, default_top=default_top)
        return {
            'planBeg': plan_beg,
            'planEnd': plan_end,
            'source': 'submit_plan_file',
            'airspaceSource': 'submit_plan_inline',
            'airspaceRefName': None,
            'space': space,
        }
    raise ValueError(f'不支持的 airspace.type: {airspace_type!r}')


def resolve_submission_items(cfg, latest_detail, cli_args):
    airspace_cache = load_airspace_cache()
    default_top = cfg.get('plan_defaults', {}).get('spcTop')
    if cli_args.start_utc_ts is not None or cli_args.end_utc_ts is not None:
        if cli_args.start_utc_ts is None or cli_args.end_utc_ts is None:
            raise ValueError('必须同时传 --start-utc-ts 和 --end-utc-ts')
        plan_beg, plan_end = utc_timestamps_to_local_pair(cli_args.start_utc_ts, cli_args.end_utc_ts, cfg)
        first_airspace = get_first_cached_airspace(airspace_cache)
        return [{
            'planBeg': plan_beg,
            'planEnd': plan_end,
            'source': 'cli_utc_pair',
            'startUtcTs': int(cli_args.start_utc_ts),
            'endUtcTs': int(cli_args.end_utc_ts),
            'airspaceSource': 'fallback_first_cached_airspace',
            'airspaceRefName': first_airspace.get('name'),
            'space': normalize_space_payload(first_airspace, default_top=default_top),
        }]

    if getattr(cli_args, 'use_submit_plan', False) or getattr(cli_args, 'use_time_list', False):
        data = load_submit_plan()
        items = data.get('plans', [])
        if not items:
            raise ValueError('submit_plan.json 中 plans 为空，无法按列表提交')
        if len(items) > 5:
            raise ValueError(f'submit_plan.json 的 plans 最多允许 5 条，本次配置了 {len(items)} 条')
        return [normalize_submission_plan_item(item, cfg, airspace_cache) for item in items]

    plan_beg, plan_end = get_tomorrow_same_time(latest_detail['planBeg'], latest_detail['planEnd'])
    first_airspace = get_first_cached_airspace(airspace_cache)
    return [{
        'planBeg': plan_beg,
        'planEnd': plan_end,
        'source': 'latest_plus_one_day',
        'airspaceSource': 'fallback_first_cached_airspace',
        'airspaceRefName': first_airspace.get('name'),
        'space': normalize_space_payload(first_airspace, default_top=default_top),
    }]


def describe_submission_item(item, index=None):
    prefix = f'[{index}] ' if index is not None else ''
    extra = ''
    if item.get('source') == 'cli_utc_pair':
        extra = f" | UTC: {item.get('startUtcTs')} -> {item.get('endUtcTs')}"
    return (
        f"{prefix}{item['planBeg']} ~ {item['planEnd']} ({item.get('source')})"
        f" | airspace={item.get('airspaceSource')} | ref={item.get('airspaceRefName') or '-'}{extra}"
    )


def resolve_time_pairs(cfg, latest_detail, cli_args):
    return resolve_submission_items(cfg, latest_detail, cli_args)


def describe_time_pair(pair, index=None):
    return describe_submission_item(pair, index=index)


def sanitize_for_json(value):
    if isinstance(value, dict):
        return {str(k): sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_for_json(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat(sep=' ')
    if isinstance(value, Path):
        return str(value)
    return value


def load_json_file(path: Path, missing_hint: str):
    if not path.exists():
        raise FileNotFoundError(missing_hint)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_config():
    return load_json_file(CONFIG_FILE, f"config.json 不存在，请先创建 {CONFIG_FILE}")


def load_airspace_cache():
    return load_json_file(AIRSPACE_FILE, f"airspace.json 不存在，请先创建 {AIRSPACE_FILE}")


def load_submit_plan():
    return load_json_file(SUBMIT_PLAN_FILE, f"submit_plan.json 不存在，请先创建 {SUBMIT_PLAN_FILE}")


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


def handle_system_error(page, max_refresh=2):
    for _ in range(max_refresh):
        try:
            res = page.evaluate(
                r"""
                () => {
                    function norm(s) {
                        return (s || '').replace(/\s+/g, ' ').trim();
                    }
                    function visible(el) {
                        return !!(el && el.offsetParent !== null);
                    }
                    function clickish(el) {
                        if (!el) return false;
                        el.scrollIntoView({block: 'center', inline: 'center'});
                        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                            el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
                        }
                        return true;
                    }
                    const nodes = Array.from(document.querySelectorAll('div,span,button,a'));
                    const visibleTexts = nodes
                        .map(el => ({
                            el,
                            text: norm(el.textContent || ''),
                            visible: visible(el)
                        }))
                        .filter(x => x.visible && x.text);
                    const hasSystemError = visibleTexts.some(x => /系统错误/.test(x.text));
                    if (!hasSystemError) return {handled:false, found:false};
                    const refresh = visibleTexts.find(x => x.text === '刷新');
                    const ignore = visibleTexts.find(x => x.text === '忽略');
                    if (refresh) {
                        clickish(refresh.el);
                        return {handled:true, found:true, action:'refresh'};
                    }
                    if (ignore) {
                        clickish(ignore.el);
                        return {handled:true, found:true, action:'ignore'};
                    }
                    return {
                        handled:false,
                        found:true,
                        texts: visibleTexts.filter(x => /系统错误|刷新|忽略/.test(x.text)).map(x => x.text).slice(0, 20)
                    };
                }
                """
            )
        except Exception:
            res = {"handled": False, "found": False}
        if not res.get("found"):
            return res
        time.sleep(3)
    return {"handled": False, "found": True, "error": "system error persisted"}


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


def is_success_code(code):
    return str(code) in ["0", "1"]


def fetch_login_captcha_with_ocr(page):
    captcha_path, uuid = get_login_captcha(page)
    ocr = solve_captcha(captcha_path)
    print(f"验证码图片: {captcha_path}")
    print(f"OCR 识别: {ocr or '失败'}")
    return {
        'captchaPath': captcha_path,
        'uuid': uuid,
        'ocr': ocr,
    }


def request_login_sms(page, phone, captcha):
    return page.evaluate(
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


def submit_login_sms_code(page, sms_code):
    return page.evaluate(
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
        [sms_code],
    )


def poll_login_result(page, timeout_s=20):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = check_main_login(page)
        if status.get("hasMainLogin"):
            return {"ok": True, "status": status}
        time.sleep(1)
    return {"ok": False, "error": "登录后仍未进入主站", "status": check_main_login(page)}


def is_captcha_error_response(payload):
    text = json.dumps(payload, ensure_ascii=False) if isinstance(payload, (dict, list)) else str(payload)
    return '图形验证码错误' in text


def is_sms_still_valid_response(payload):
    text = json.dumps(payload, ensure_ascii=False) if isinstance(payload, (dict, list)) else str(payload)
    return '短信验证码还在有效期内' in text


def should_prompt_for_sms_in_interactive_mode():
    return os.environ.get('UOM_SMS_CODE') is None


def login_via_sms(page):
    phone = load_phone()
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)
    dismiss_popup(page)
    comp = wait_for_login_component(page)
    if not comp:
        raise RuntimeError("找不到登录组件")

    login_trace = {
        'usedOcrAsDefaultCaptcha': True,
        'captchaAttempts': [],
        'smsCodeSource': 'env' if os.environ.get('UOM_SMS_CODE') else 'interactive',
        'waitedForSmsInput': False,
        'reusedExistingSmsCode': False,
    }

    captcha_meta = fetch_login_captcha_with_ocr(page)
    captcha = (captcha_meta.get('ocr') or '').strip()
    if not captcha:
        raise RuntimeError("OCR 未识别出图形验证码，当前流程已改为默认直接使用 OCR，不再阻塞等待手输图形验证码")

    sms_result = request_login_sms(page, phone, captcha)
    login_trace['captchaAttempts'].append({
        'captcha': captcha,
        'uuid': captcha_meta.get('uuid'),
        'ocr': captcha_meta.get('ocr'),
        'smsResult': sms_result,
    })
    print("发短信返回:", sms_result)

    if not is_success_code(sms_result.get("code")):
        if is_sms_still_valid_response(sms_result):
            print('检测到短信验证码仍在有效期内：不会再次发送短信，后续将优先使用你手上的上一条短信验证码继续登录。')
            login_trace['reusedExistingSmsCode'] = True
        elif is_captcha_error_response(sms_result):
            raise RuntimeError(
                '本次发短信返回图形验证码错误。为避免重复发送短信导致限制，当前流程不会自动再次发短信。'
                '请优先使用你手上上一次收到且仍在 10 分钟有效期内的短信验证码继续登录；'
                '若你确认当前没有可复用短信码，再重新发起一次登录流程。'
            )
        else:
            raise RuntimeError(f"短信发送失败: {sms_result}")

    sms = os.environ.get('UOM_SMS_CODE', '').strip()
    if should_prompt_for_sms_in_interactive_mode():
        login_trace['waitedForSmsInput'] = True
        sms = input("请输入短信验证码: ").strip()
    if not sms:
        raise RuntimeError("短信验证码为空")

    submit_result = submit_login_sms_code(page, sms)
    if not submit_result.get("ok"):
        submit_result['loginTrace'] = login_trace
        return submit_result

    poll_result = poll_login_result(page)
    poll_result['loginTrace'] = login_trace
    return poll_result


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

    direct_res = page.evaluate(
        r"""
        () => {
            const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
            const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
            const app = document.querySelector('#app');
            if (!app || !app.__vue__) return {ok:false, error:'no vue root'};
            let layout = null;
            const seen = new Set();
            function walk(vm, depth) {
                if (!vm || depth > 8 || seen.has(vm)) return;
                seen.add(vm);
                const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                if (name === 'Layout') {
                    layout = vm;
                    return;
                }
                for (const c of (vm.$children || [])) walk(c, depth + 1);
            }
            walk(app.__vue__, 0);
            if (!layout) return {ok:false, error:'layout not found'};
            const tree = (layout.$data && layout.$data.menuTree) || [];
            let item = null;
            for (const x of tree) {
                if (x && x.id === 'a2e16537-2fa4-4ca6-a935-1baf0efb111e') item = x;
                if (!item && x && Array.isArray(x.children)) {
                    for (const c of x.children) {
                        if (c && c.id === 'a2e16537-2fa4-4ca6-a935-1baf0efb111e') item = c;
                    }
                }
            }
            if (!item) {
                return {
                    ok:false,
                    error:'一般飞行活动 menuTree 节点未找到',
                    menuLabels: tree.map(x => x && (x.__label || x.label || x.title || '')).filter(Boolean).slice(0, 50)
                };
            }
            if (typeof layout.openPage !== 'function') return {ok:false, error:'layout.openPage not available'};
            try {
                layout.openPage(item);
                return {
                    ok:true,
                    path:'layout.openPage',
                    item:{
                        id:item.id,
                        title:item.title,
                        appName:item.appName,
                        value:item.value,
                        label:item.label,
                        routeLabel:item.__label || null,
                        routeValue:item.__value || null
                    }
                };
            } catch (e) {
                return {ok:false, error:'layout.openPage failed', detail:String(e)};
            }
        }
        """
    )
    if not direct_res.get("ok"):
        raise RuntimeError({"error": "直达一般飞行活动失败", "direct": direct_res, "snapshot": debug_menu_snapshot(page)})
    time.sleep(2)
    err_res = handle_system_error(page, max_refresh=3)
    frame_debug = page.evaluate(
        r"""
        () => ({
            iframeCount: document.querySelectorAll('iframe').length,
            anchors: Array.from(document.querySelectorAll('a,button,span,div'))
                .map(el => ((el.textContent || '').replace(/\s+/g, ' ').trim()))
                .filter(Boolean)
                .filter(t => /一般飞行活动|飞行活动申请|刷新|新增|系统错误|忽略/.test(t))
                .slice(0, 30),
            hash: location.hash || '',
            href: location.href || ''
        })
        """
    )
    if frame_debug.get("iframeCount"):
        return True
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            err_loop = handle_system_error(page, max_refresh=1)
            state = page.evaluate(
                r"""
                () => ({
                    iframeCount: document.querySelectorAll('iframe').length,
                    hash: location.hash || '',
                    href: location.href || '',
                    bodyHead: ((document.body && document.body.innerText) || '').replace(/\s+/g, ' ').slice(0, 500)
                })
                """
            )
            if state.get("iframeCount"):
                return True
            if err_loop.get("found"):
                time.sleep(2)
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError({"error": "进入飞行活动页面失败：未出现 iframe", "direct": direct_res, "system_error": err_res, "frame_debug": frame_debug, "snapshot": debug_menu_snapshot(page)})


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
    return get_recent_plans(page, page_num=1, page_size=5)


def get_recent_plans(page, page_num=1, page_size=5):
    return page.evaluate(
        """
        async ([pageNum, pageSize]) => {
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
            const resp = await iframe.contentWindow.fetch(`/oapi/pub/planInfo/list?pageNum=${pageNum}&pageSize=${pageSize}&planTypes=11,12,13`, {
                headers: {
                    'Authorization': 'Bearer ' + pubToken,
                    'pubUserName': userName,
                    'ticket': ticket,
                    'deviceType': 'PC'
                },
                credentials: 'include'
            });
            const data = await resp.json();
            const rows = (data && data.data && data.data.rows) || data.rows || (data && data.data && data.data.list) || data.list || [];
            return {ok: true, total: rows.length, latest: rows[0] || null, rows, data, pageNum, pageSize};
        }
        """,
        [page_num, page_size]
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
    stable_ready_count = 0
    last_ready = None
    last_snapshot = None
    while time.time() < deadline:
        try:
            res = page.evaluate(
                r"""
                () => {
                    const iframe = document.querySelector('iframe');
                    if (!iframe) return {ok:false, error:'no iframe'};
                    const href = iframe.contentWindow ? iframe.contentWindow.location.href : '';
                    const doc = iframe.contentDocument;
                    if (!doc) return {ok:false, error:'no iframe doc', href};
                    const bodyText = ((doc.body && doc.body.innerText) || '').replace(/\s+/g, ' ').trim();
                    const app = doc.querySelector('#app');
                    const vueNames = [];
                    let mainComp = null;

                    function walk(vm, depth) {
                        if (!vm || depth > 15) return;
                        const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                        if (name) vueNames.push(name);
                        if (!mainComp && name === 'FLY_INDEX_ADD' && vm.$data && vm.$data.form) {
                            mainComp = vm;
                        }
                        for (const c of (vm.$children || [])) walk(c, depth + 1);
                    }

                    if (app && app.__vue__) walk(app.__vue__, 0);
                    const uniqNames = Array.from(new Set(vueNames));

                    const visibleAddButtons = Array.from(doc.querySelectorAll('button.addButton, button'))
                        .filter(el => el && el.offsetParent !== null)
                        .filter(el => ((el.textContent || '').replace(/\s+/g, ' ').trim()) === '添加');

                    const datetimeInputs = Array.from(doc.querySelectorAll('input')).filter(el => {
                        if (!el || el.offsetParent === null) return false;
                        const ph = (el.getAttribute('placeholder') || '').trim();
                        const cls = (el.className || '').toString();
                        return /日期|时间|选择日期|选择时间/.test(ph) || /date|time/i.test(cls);
                    });

                    const loadingVisible = Array.from(doc.querySelectorAll('div,span,section,aside')).some(el => {
                        if (!el || el.offsetParent === null) return false;
                        const cls = (el.className || '').toString();
                        const text = ((el.textContent || '').replace(/\s+/g, ' ').trim());
                        return /loading|spinner|is-loading|el-loading|ivu-spin|ant-spin/i.test(cls) || /加载中|提交中|处理中/.test(text);
                    });

                    const noticeVisible = Array.from(doc.querySelectorAll('div,section,aside,span'))
                        .filter(el => el && el.offsetParent !== null)
                        .some(el => /温馨提示/.test((el.textContent || '').replace(/\s+/g, ' ').trim()));

                    const form = mainComp && mainComp.$data ? mainComp.$data.form || {} : {};
                    const refKeys = mainComp && mainComp.$refs ? Object.keys(mainComp.$refs) : [];
                    const dataKeys = mainComp && mainComp.$data ? Object.keys(mainComp.$data) : [];
                    const hasKeyData = dataKeys.includes('showNoticeDialog') && dataKeys.includes('form');
                    const hasKeyRefs = refKeys.includes('form') && refKeys.includes('spaceSelection') && refKeys.includes('leafletMap');
                    const formShapeReady = (
                        form && typeof form === 'object' &&
                        Object.prototype.hasOwnProperty.call(form, 'planBeg') &&
                        Object.prototype.hasOwnProperty.call(form, 'planEnd') &&
                        Array.isArray(form.uavs) &&
                        Array.isArray(form.drivers) &&
                        Array.isArray(form.spaces)
                    );

                    const stabilityFingerprint = JSON.stringify({
                        href,
                        visibleAddButtonCount: visibleAddButtons.length,
                        datetimeInputCount: datetimeInputs.length,
                        loadingVisible,
                        noticeVisible,
                        hasKeyRefs,
                        hasKeyData,
                        formShapeReady,
                        vueNames: uniqNames.slice().sort(),
                        refKeys: refKeys.slice().sort(),
                        dataKeys: dataKeys.filter(k => /form|dialog|notice|uav|driver|space|loading/i.test(k)).slice().sort(),
                        bodyTextHead: bodyText.slice(0, 300),
                    });

                    const ready = (
                        href.includes('flyIndexAdd') &&
                        !!mainComp &&
                        visibleAddButtons.length >= 2 &&
                        datetimeInputs.length >= 2 &&
                        hasKeyRefs &&
                        hasKeyData &&
                        formShapeReady &&
                        !loadingVisible &&
                        /无人驾驶航空器飞行活动申请/.test(bodyText)
                    );

                    return {
                        ok: true,
                        ready,
                        loadingVisible,
                        noticeVisible,
                        stableFingerprint: stabilityFingerprint,
                        href,
                        hasVue: uniqNames.length > 0,
                        vueNames: uniqNames.slice(0, 20),
                        hasMainComp: !!mainComp,
                        refKeys: refKeys.slice(0, 20),
                        interestingDataKeys: dataKeys.filter(k => /form|dialog|notice|uav|driver|space|loading/i.test(k)).slice(0, 30),
                        visibleAddButtonCount: visibleAddButtons.length,
                        datetimeInputCount: datetimeInputs.length,
                        bodyText: bodyText.slice(0, 1000),
                        formSummary: {
                            planBegType: form && form.planBeg != null ? typeof form.planBeg : null,
                            planEndType: form && form.planEnd != null ? typeof form.planEnd : null,
                            hasUavsField: Array.isArray(form && form.uavs),
                            hasDriversField: Array.isArray(form && form.drivers),
                            hasSpacesField: Array.isArray(form && form.spaces),
                        },
                    };
                }
                """
            )
            last_snapshot = res
            if res and res.get('ready'):
                stable_key = res.get('stableFingerprint')
                if stable_key == last_ready:
                    stable_ready_count += 1
                else:
                    last_ready = stable_key
                    stable_ready_count = 1
                if stable_ready_count >= 3:
                    res['stableReadyCount'] = stable_ready_count
                    return res
            else:
                stable_ready_count = 0
                last_ready = None
        except Exception as e:
            last_snapshot = {'ok': False, 'error': str(e)}
        time.sleep(1)
    return last_snapshot


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


def fill_new_form_from_detail(page, detail, plan_beg_new, plan_end_new, space_payload):
    return page.evaluate(
        r"""
        async ([detail, planBegNew, planEndNew, spacePayload]) => {
            const STEP_SLEEP_MS = 2000;
            const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
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

            function findComponentsWithFlag(vm, flagName, depth = 0, out = []) {
                if (!vm || depth > 8 || out.length > 80) return out;
                try {
                    if (vm.$data && Object.prototype.hasOwnProperty.call(vm.$data, flagName)) out.push(vm);
                } catch (e) {}
                for (const c of (vm.$children || [])) findComponentsWithFlag(c, flagName, depth + 1, out);
                return out;
            }

            function norm(text) {
                return ((text || '').replace(/\s+/g, ' ').trim());
            }

            function isVisible(el) {
                return !!(el && el.offsetParent !== null);
            }

            function dispatchClick(el) {
                if (!el) return false;
                el.scrollIntoView({block: 'center', inline: 'center'});
                for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                    el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
                }
                return true;
            }

            function visibleAddButtons(root) {
                return Array.from(root.querySelectorAll('button.addButton, button, span, div, a')).filter(el => {
                    return norm(el.textContent) === '添加' && isVisible(el);
                });
            }

            function clone(obj) {
                return obj == null ? obj : JSON.parse(JSON.stringify(obj));
            }

            function collectVisibleDialogs() {
                const directMatches = Array.from(doc.querySelectorAll('.ivu-modal-wrap,.ivu-drawer-wrap,[role="dialog"],.el-dialog,.el-dialog__wrapper,.v-modal,.el-overlay,.el-message-box__wrapper'));
                const broadMatches = Array.from(doc.querySelectorAll('div,section,aside'))
                    .filter(el => isVisible(el))
                    .filter(el => {
                        const text = norm(el.innerText || el.textContent || '');
                        return /选择我的航空器|选择我的操控员|确定取消|产品序列号\/出厂序号实名登记标志|执照编号姓名|执照编号执照种类/.test(text);
                    });
                const merged = [];
                for (const el of [...directMatches, ...broadMatches]) {
                    if (!el || !isVisible(el) || merged.includes(el)) continue;
                    merged.push(el);
                }
                return merged.map(el => ({
                        text: norm((el.innerText || '')).slice(0, 800),
                        cls: (el.className || '').toString(),
                        tag: el.tagName,
                    }));
            }

            async function closeNoticeDialogIfPresent() {
                const noticeOwners = findComponentsWithFlag(comp, 'showNoticeDialog');
                for (let i = 0; i < 10; i++) {
                    const visibleDialogs = collectVisibleDialogs();
                    const notice = visibleDialogs.find(x => /温馨提示/.test(x.text || ''));
                    const ownerStates = noticeOwners.map(vm => ({
                        name: (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '',
                        value: vm.$data ? vm.$data.showNoticeDialog : undefined,
                        methods: Object.keys(vm || {}).filter(k => typeof vm[k] === 'function' && /notice|dialog|close|know|confirm/i.test(k)).slice(0, 12),
                    }));
                    const allClosed = ownerStates.length ? ownerStates.every(x => x.value === false) : true;
                    if (!notice && allClosed) return {ok:true, closed:true, attempts:i, remainingDialogs:visibleDialogs, ownerStates};

                    let clicked = false;
                    const buttons = Array.from(doc.querySelectorAll('button,span,div,a')).filter(isVisible);
                    for (const el of buttons) {
                        const t = norm(el.textContent);
                        const cls = (el.className || '').toString();
                        if (t === '我知道了' || (/dialog__footer/.test(cls) && /我知道了/.test(norm(el.innerText || '')))) {
                            dispatchClick(el);
                            clicked = true;
                            break;
                        }
                    }

                    for (const vm of noticeOwners) {
                        try {
                            if (vm.$data && Object.prototype.hasOwnProperty.call(vm.$data, 'showNoticeDialog')) {
                                vm.$data.showNoticeDialog = false;
                            }
                            for (const key of Object.keys(vm)) {
                                if (typeof vm[key] === 'function' && /notice|dialog|close|know|confirm/i.test(key)) {
                                    try { vm[key](); clicked = true; } catch (e) {}
                                }
                            }
                            if (typeof vm.$forceUpdate === 'function') vm.$forceUpdate();
                        } catch (e) {}
                    }
                    if (typeof comp.$forceUpdate === 'function') comp.$forceUpdate();

                    await sleep(STEP_SLEEP_MS);
                    const stillVisible = collectVisibleDialogs().some(x => /温馨提示/.test(x.text || ''));
                    const ownerStatesAfter = noticeOwners.map(vm => ({
                        name: (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '',
                        value: vm.$data ? vm.$data.showNoticeDialog : undefined,
                    }));
                    const allClosedAfter = ownerStatesAfter.length ? ownerStatesAfter.every(x => x.value === false) : true;
                    if (!stillVisible && allClosedAfter) {
                        return {ok:true, closed:true, attempts:i + 1, remainingDialogs:collectVisibleDialogs(), ownerStates:ownerStatesAfter};
                    }
                    if (!clicked && !noticeOwners.length) {
                        return {ok:false, error:'notice dialog visible but no clickable path or owner found', attempts:i, remainingDialogs:collectVisibleDialogs(), ownerStates:ownerStatesAfter};
                    }
                }
                return {ok:false, error:'notice dialog still visible after retries', remainingDialogs:collectVisibleDialogs(), ownerStates:noticeOwners.map(vm => ({name:(vm.$options && (vm.$options.name || vm.$options._componentTag)) || '', value: vm.$data ? vm.$data.showNoticeDialog : undefined}))};
            }

            function findDialogByTitle(titleRegex) {
                const direct = Array.from(doc.querySelectorAll('.ivu-modal-wrap,.ivu-drawer-wrap,[role="dialog"],.el-dialog,.el-dialog__wrapper,.el-message-box__wrapper,.el-overlay,.v-modal'))
                    .filter(el => isVisible(el) && titleRegex.test(norm(el.innerText || el.textContent || '')));
                if (direct.length) return direct[direct.length - 1];
                const broad = Array.from(doc.querySelectorAll('div,section,aside'))
                    .filter(el => isVisible(el) && titleRegex.test(norm(el.innerText || el.textContent || '')))
                    .sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
                if (broad.length) return broad[0];
                if (/选择我的航空器/.test(String(titleRegex))) {
                    const uavInfo = Array.from(doc.querySelectorAll('div,section,aside'))
                        .filter(el => isVisible(el) && /(app-container\s+uavInfo|\buavInfo\b)/.test((el.className || '').toString()))
                        .sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
                    if (uavInfo.length) return uavInfo[0];
                }
                if (/选择我的操控员|选择操控员/.test(String(titleRegex))) {
                    const driverInfo = Array.from(doc.querySelectorAll('div,section,aside'))
                        .filter(el => isVisible(el) && /(app-container\s+driverInfo|\bdriverInfo\b|app-container\s+operator|\boperator\b)/.test((el.className || '').toString()))
                        .sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
                    if (driverInfo.length) return driverInfo[0];
                }
                return null;
            }

            function selectRowInDialog(dialogEl, keyword) {
                if (!dialogEl || !keyword) return {ok:false, error:'missing dialog or keyword'};
                const rows = Array.from(dialogEl.querySelectorAll('tr')).filter(isVisible);
                const altKeywords = Array.from(new Set(String(keyword).split(/\s+/).map(s => s.trim()).filter(Boolean)));
                for (const row of rows) {
                    const text = norm(row.innerText || row.textContent || '');
                    if (!text) continue;
                    if (text.includes(keyword) || altKeywords.some(k => k && text.includes(k))) {
                        const checkbox = row.querySelector('.el-checkbox__original, input[type="checkbox"]');
                        const checkboxWrap = row.querySelector('.el-checkbox, .el-checkbox__input, label');
                        if (checkbox) {
                            checkbox.scrollIntoView({block: 'center', inline: 'center'});
                            if (!checkbox.checked) checkbox.click();
                            if (!checkbox.checked && checkboxWrap) dispatchClick(checkboxWrap);
                            if (!checkbox.checked) dispatchClick(row);
                            return {ok:!!checkbox.checked, method:'checkbox', text:text.slice(0, 200), checked:!!checkbox.checked, keyword, altKeywords};
                        }
                        dispatchClick(row);
                        return {ok:true, method:'row', text:text.slice(0, 200), keyword, altKeywords};
                    }
                }
                return {ok:false, error:'row not found', keyword, altKeywords, availableRows: rows.map(row => norm(row.innerText || row.textContent || '').slice(0, 200)).filter(Boolean).slice(0, 10)};
            }

            function clickDialogConfirm(dialogEl) {
                if (!dialogEl) return {ok:false, error:'no dialog'};
                const candidates = Array.from(dialogEl.querySelectorAll('button,span,div,a')).filter(isVisible);
                for (const el of candidates) {
                    const t = norm(el.textContent);
                    if (t === '确定') {
                        dispatchClick(el);
                        return {ok:true, text:t, tag:el.tagName, cls:(el.className || '').toString()};
                    }
                }
                return {ok:false, error:'confirm button not found'};
            }

            function sectionTitleFor(el) {
                let node = el;
                for (let i = 0; node && i < 8; i++, node = node.parentElement) {
                    const text = norm(node.innerText || node.textContent || '');
                    if (/航空器信息/.test(text)) return 'uav';
                    if (/操控员信息/.test(text)) return 'driver';
                    if (/飞行空域信息/.test(text)) return 'space';
                }
                return '';
            }

            function describeAddButton(el, index) {
                if (!el) return null;
                const parent = el.parentElement;
                const row = parent ? parent.parentElement : null;
                return {
                    index,
                    text: norm(el.textContent),
                    tag: el.tagName,
                    cls: (el.className || '').toString(),
                    section: sectionTitleFor(el),
                    parentText: norm(parent && (parent.innerText || parent.textContent || '')).slice(0, 160),
                    rowText: norm(row && (row.innerText || row.textContent || '')).slice(0, 220),
                };
            }

            function listAddButtons(root) {
                return visibleAddButtons(root).map((el, index) => ({el, meta: describeAddButton(el, index)}));
            }

            function findAddButtonBySection(root, section) {
                const buttons = listAddButtons(root);
                const buttonTagPreferred = buttons.find(x => x.meta && x.meta.section === section && x.el && x.el.tagName === 'BUTTON' && /\baddButton\b/.test((x.el.className || '').toString()));
                if (buttonTagPreferred) return buttonTagPreferred;
                const buttonPreferred = buttons.find(x => x.meta && x.meta.section === section && x.el && x.el.tagName === 'BUTTON');
                if (buttonPreferred) return buttonPreferred;
                const spanPreferred = buttons.find(x => x.meta && x.meta.section === section && x.el && x.el.tagName === 'SPAN');
                if (spanPreferred) return spanPreferred;
                const exact = buttons.find(x => x.meta && x.meta.section === section);
                if (exact) return exact;
                const fallbackButton = buttons.find(x => x.el && x.el.tagName === 'BUTTON');
                if (fallbackButton) return fallbackButton;
                return section === 'uav' ? buttons[0] || null : buttons[1] || null;
            }

            function dumpVueComponentNames(vm, depth = 0, out = []) {
                if (!vm || depth > 6 || out.length > 160) return out;
                const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                out.push({
                    depth,
                    name,
                    refKeys: vm.$refs ? Object.keys(vm.$refs).slice(0, 12) : [],
                    dataKeys: Object.keys(vm.$data || {}).filter(k => /uav|driver|select|check|space|dialog|table|choose|row|current/i.test(k)).slice(0, 20),
                });
                for (const c of (vm.$children || [])) dumpVueComponentNames(c, depth + 1, out);
                return out;
            }

            function tryInvoke(obj, key, ...args) {
                try {
                    if (obj && typeof obj[key] === 'function') {
                        return {ok:true, value: obj[key](...args)};
                    }
                } catch (e) {
                    return {ok:false, error:e.message};
                }
                return {ok:false, missing:true};
            }

            function setValidationLikeState(comp, sourceUav, sourceDriver) {
                const notes = [];
                if ('uavInfoCheck' in comp.$data) {
                    comp.$data.uavInfoCheck = !!(sourceUav);
                    notes.push('set uavInfoCheck');
                }
                if ('selectMap' in comp.$data && sourceUav) {
                    comp.$data.selectMap = sourceUav.uasCode || sourceUav.sn || 'selected';
                    notes.push('set selectMap');
                }
                if (Array.isArray(comp.$data.checkList)) {
                    const values = [];
                    if (sourceUav && (sourceUav.uasCode || sourceUav.sn)) values.push(sourceUav.uasCode || sourceUav.sn);
                    if (sourceDriver && (sourceDriver.name || sourceDriver.cardno)) values.push(sourceDriver.name || sourceDriver.cardno);
                    comp.$data.checkList = Array.from(new Set([...(comp.$data.checkList || []), ...values]));
                    notes.push('extended checkList');
                }
                if ('noCheckFlag' in comp.$data) {
                    comp.$data.noCheckFlag = false;
                    notes.push('set noCheckFlag=false');
                }
                return notes;
            }

            const comp = findMainForm(app.__vue__, 0);
            if (!comp) return {ok:false, error:'FLY_INDEX_ADD not found'};
            const f = comp.$data.form || {};
            const noticeOwners = findComponentsWithFlag(comp, 'showNoticeDialog').map(vm => ({
                name: (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '',
                value: vm.$data ? vm.$data.showNoticeDialog : undefined,
                dataKeys: Object.keys(vm.$data || {}).slice(0, 20),
            }));
            const beforeKeys = Object.keys(comp.$data || {}).filter(k => /uav|driver|select|check|space|dialog|table|choose/i.test(k));
            const sourceSpace = spacePayload || {};
            const sourceUav = Array.isArray(detail.uavs) && detail.uavs.length ? clone(detail.uavs[0]) : null;
            const sourceDriver = Array.isArray(detail.drivers) && detail.drivers.length ? clone(detail.drivers[0]) : null;

            const noticeHandling = await closeNoticeDialogIfPresent();
            await sleep(STEP_SLEEP_MS);
            const addButtonsBefore = listAddButtons(doc).map(x => x.meta);

            const uavAddButton = findAddButtonBySection(doc, 'uav');
            const clickedUavAdd = uavAddButton ? (() => { dispatchClick(uavAddButton.el); return {...uavAddButton.meta, ok:true, outerHTML: (uavAddButton.el.outerHTML || '').slice(0, 400)}; })() : {ok:false};
            await sleep(STEP_SLEEP_MS);
            const uavPickDialog = findDialogByTitle(/选择我的航空器/);
            const uavDialogCandidates = collectVisibleDialogs();
            const uavSelectionKeyword = sourceUav ? [sourceUav.uasCode, sourceUav.sn, sourceUav.proName, sourceUav.proMode].filter(Boolean).join(' ') : '';
            const uavSelection = uavPickDialog && sourceUav ? selectRowInDialog(uavPickDialog, uavSelectionKeyword) : {ok:false, error:'uav dialog not found', dialogs:uavDialogCandidates};
            await sleep(STEP_SLEEP_MS);
            const uavConfirm = uavPickDialog ? clickDialogConfirm(uavPickDialog) : {ok:false, error:'uav dialog not found', dialogs:uavDialogCandidates};
            await sleep(STEP_SLEEP_MS);
            const uavDialogsAfterClick = collectVisibleDialogs();
            const uavVueAfterClick = dumpVueComponentNames(comp);

            const shouldOpenDriverDialog = !!(uavSelection && uavSelection.ok && uavConfirm && uavConfirm.ok);
            const driverAddButton = findAddButtonBySection(doc, 'driver');
            const clickedDriverAdd = shouldOpenDriverDialog && driverAddButton ? (() => { dispatchClick(driverAddButton.el); return {...driverAddButton.meta, ok:true, outerHTML: (driverAddButton.el.outerHTML || '').slice(0, 400)}; })() : {ok:false, skipped:true, reason:'uav selection/confirm not completed'};
            await sleep(STEP_SLEEP_MS);
            const driverPickDialog = shouldOpenDriverDialog ? findDialogByTitle(/选择我的操控员|选择操控员/) : null;
            const driverDialogCandidates = collectVisibleDialogs();
            const driverSelectionKeyword = sourceDriver ? [sourceDriver.name, sourceDriver.cardno, sourceDriver.phone].filter(Boolean).join(' ') : '';
            const driverSelection = driverPickDialog && sourceDriver ? selectRowInDialog(driverPickDialog, driverSelectionKeyword) : {ok:false, error:'driver dialog not found', dialogs:driverDialogCandidates};
            await sleep(STEP_SLEEP_MS);
            const driverConfirm = driverPickDialog ? clickDialogConfirm(driverPickDialog) : {ok:false, error:'driver dialog not found', dialogs:driverDialogCandidates};
            await sleep(STEP_SLEEP_MS);
            const driverDialogsAfterClick = collectVisibleDialogs();
            const driverVueAfterClick = dumpVueComponentNames(comp);

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
                comp.$data.spaceList = [clone(space)];
                comp.$data.oldSpaceList = [clone(space)];
                tryInvoke(comp, 'callbackAddSpace', [space]);
            }

            const hookResults = {};
            const candidatePickerComponents = [];
            const allowProgrammaticBackfill = false;

            if (allowProgrammaticBackfill && sourceUav) {
                f.uavs = [sourceUav];
                comp.$data.uavInfoList = [clone(sourceUav)];
                hookResults.callbackAddUavs = tryInvoke(comp, 'callbackAddUavs', [sourceUav]);
                hookResults.handleSelectionChangeUav = tryInvoke(comp, 'handleSelectionChangeUav', [sourceUav]);
                hookResults.handleCurrentChangeUav = tryInvoke(comp, 'handleCurrentChangeUav', sourceUav);
                hookResults.selectUav = tryInvoke(comp, 'selectUav', sourceUav);
            }

            if (allowProgrammaticBackfill && sourceDriver) {
                if (!sourceDriver.uasCodes && sourceUav && sourceUav.uasCode) sourceDriver.uasCodes = [sourceUav.uasCode];
                f.drivers = [sourceDriver];
                comp.$data.driverInfoList = [clone(sourceDriver)];
                comp.$data.currentRowDriver = clone(sourceDriver);
                if (Array.isArray(comp.$data.currentRowDriver)) comp.$data.currentRowDriver = clone(sourceDriver);
                if (Array.isArray(comp.$data.currentRowDriver)) comp.$data.currentRowDriver = clone(sourceDriver[0] || sourceDriver);
                comp.$data.selectDriverIndex = 0;
                hookResults.callbackAddDrivers = tryInvoke(comp, 'callbackAddDrivers', [sourceDriver]);
                hookResults.handleSelectionDriverUavs = tryInvoke(comp, 'handleSelectionDriverUavs', sourceUav ? [sourceUav] : [], sourceDriver);
                hookResults.handleSelectionChangeDriver = tryInvoke(comp, 'handleSelectionChangeDriver', [sourceDriver]);
                hookResults.handleCurrentChangeDriver = tryInvoke(comp, 'handleCurrentChangeDriver', sourceDriver);
                hookResults.selectDriver = tryInvoke(comp, 'selectDriver', sourceDriver);
                hookResults.currentRowDriverTypeAfterSet = Array.isArray(comp.$data.currentRowDriver) ? 'array' : typeof comp.$data.currentRowDriver;
            }

            if (allowProgrammaticBackfill) {
                hookResults.validationStateTweaks = setValidationLikeState(comp, sourceUav, sourceDriver);
            }

            try {
                const uavTable = comp.$refs && (comp.$refs.uavTable || comp.$refs.uavInfoTable || comp.$refs.tableUav || comp.$refs.tableUavs);
                if (allowProgrammaticBackfill && uavTable && typeof uavTable.toggleRowSelection === 'function' && sourceUav) {
                    uavTable.toggleRowSelection(sourceUav, true);
                    hookResults.uavTableToggle = {ok:true};
                }
            } catch (e) {
                hookResults.uavTableToggle = {ok:false, error:e.message};
            }

            try {
                const driverTable = comp.$refs && (comp.$refs.driverTable || comp.$refs.driverInfoTable || comp.$refs.tableDriver || comp.$refs.tableDrivers);
                if (allowProgrammaticBackfill && driverTable && typeof driverTable.toggleRowSelection === 'function' && sourceDriver) {
                    driverTable.toggleRowSelection(sourceDriver, true);
                    hookResults.driverTableToggle = {ok:true};
                }
            } catch (e) {
                hookResults.driverTableToggle = {ok:false, error:e.message};
            }

            if (typeof comp.$forceUpdate === 'function') comp.$forceUpdate();
            await sleep(STEP_SLEEP_MS);
            if (comp.$refs && comp.$refs.form && typeof comp.$refs.form.clearValidate === 'function') comp.$refs.form.clearValidate();

            return {
                ok: true,
                mode: 'stepwise-ui-plus-hooks',
                compName: (comp.$options && (comp.$options.name || comp.$options._componentTag)) || '',
                noticeHandling,
                noticeOwners,
                clickedUavAdd,
                clickedDriverAdd,
                uavSelection,
                uavConfirm,
                driverSelection,
                driverConfirm,
                addButtonsBefore,
                uavDialogsAfterClick,
                driverDialogsAfterClick,
                uavVueAfterClick,
                driverVueAfterClick,
                hookResults,
                candidatePickerComponents,
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
        [detail, plan_beg_new, plan_end_new, space_payload],
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

            function findComponentsWithFlag(vm, flagName, depth = 0, out = []) {
                if (!vm || depth > 8 || out.length > 80) return out;
                try {
                    if (vm.$data && Object.prototype.hasOwnProperty.call(vm.$data, flagName)) out.push(vm);
                } catch (e) {}
                for (const c of (vm.$children || [])) findComponentsWithFlag(c, flagName, depth + 1, out);
                return out;
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
            const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
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

            function norm(text) {
                return ((text || '').replace(/\s+/g, ' ').trim());
            }

            function isVisible(el) {
                return !!(el && el.offsetParent !== null);
            }

            function dispatchClick(el) {
                if (!el) return false;
                el.scrollIntoView({block: 'center', inline: 'center'});
                for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                    el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
                }
                return true;
            }

            function collectVisibleDialogs() {
                return Array.from(doc.querySelectorAll('.ivu-modal-wrap,.ivu-drawer-wrap,[role="dialog"],.el-dialog,.el-dialog__wrapper,.v-modal,.el-overlay,.el-message-box__wrapper,div,section,aside'))
                    .filter(el => isVisible(el))
                    .map(el => ({
                        text: norm((el.innerText || el.textContent || '')).slice(0, 800),
                        cls: (el.className || '').toString(),
                        tag: el.tagName,
                    }))
                    .filter(x => x.text && /提交申请|确认提交|确定取消|确定提交|是否提交|失败|错误|异常|成功/.test(x.text))
                    .slice(0, 20);
            }

            function clickConfirmSubmitDialog() {
                const visible = Array.from(doc.querySelectorAll('button,span,div,a')).filter(isVisible);
                const confirmBtn = visible.find(el => {
                    const text = norm(el.textContent);
                    return text === '提交' || text === '确认提交' || text === '确定';
                });
                if (confirmBtn) {
                    dispatchClick(confirmBtn);
                    return {ok:true, text:norm(confirmBtn.textContent), cls:(confirmBtn.className || '').toString(), tag:confirmBtn.tagName};
                }
                return {ok:false, error:'confirm submit button not found', dialogs:collectVisibleDialogs()};
            }

            function collectPageSignals() {
                const text = norm(doc.body && (doc.body.innerText || doc.body.textContent || ''));
                const dialogs = collectVisibleDialogs();
                return {
                    text: text.slice(0, 3000),
                    dialogs,
                    hasFailure: /保存飞行活动失败|提交失败|失败|错误|异常/.test(text) || dialogs.some(x => /保存飞行活动失败|提交失败|失败|错误|异常/.test(x.text || '')),
                    hasSuccess: /提交成功|申请成功|成功/.test(text) || dialogs.some(x => /提交成功|申请成功|成功/.test(x.text || '')),
                };
            }

            async function runSingleAttempt(attempt) {
                const result = {attempt};
                const submitBtn = Array.from(doc.querySelectorAll('button,span,div,a')).find(el => norm(el.textContent) === '提交申请' && isVisible(el));
                if (submitBtn) {
                    dispatchClick(submitBtn);
                    result.clickedSubmitButton = true;
                } else {
                    result.clickedSubmitButton = false;
                }
                try {
                    if (typeof comp.submitPlan === 'function') {
                        comp.submitPlan();
                        result.calledSubmitPlan = true;
                    } else {
                        result.calledSubmitPlan = false;
                    }
                } catch (e) {
                    result.submitPlanError = e.message;
                }
                await sleep(1000);
                result.submitDialogsAfterPrimarySubmit = collectVisibleDialogs();
                result.confirmSubmitClick = clickConfirmSubmitDialog();
                await sleep(1500);
                result.submitDialogsAfterConfirm = collectVisibleDialogs();
                result.signals = collectPageSignals();
                result.failed = !!result.signals.hasFailure;
                result.succeeded = !!result.signals.hasSuccess && !result.signals.hasFailure;
                return result;
            }

            const comp = findMainForm(app.__vue__, 0);
            if (!comp) return {ok:false, error:'FLY_INDEX_ADD not found'};
            const payload = {
                ok:true,
                before: {
                    uavs: Array.isArray(comp.$data.form && comp.$data.form.uavs) ? comp.$data.form.uavs.length : null,
                    drivers: Array.isArray(comp.$data.form && comp.$data.form.drivers) ? comp.$data.form.drivers.length : null,
                    spaces: Array.isArray(comp.$data.form && comp.$data.form.spaces) ? comp.$data.form.spaces.length : null,
                },
                attempts: []
            };
            if (comp.$refs && comp.$refs.form && typeof comp.$refs.form.validate === 'function') {
                payload.validate = await new Promise(resolve => {
                    comp.$refs.form.validate((valid, fields) => {
                        resolve({valid, fields: fields ? Object.keys(fields) : []});
                    });
                });
            }

            let lastAttempt = null;
            for (let i = 1; i <= 3; i++) {
                const attempt = await runSingleAttempt(i);
                payload.attempts.push(attempt);
                lastAttempt = attempt;
                if (!attempt.failed) break;
                if (i < 3) await sleep(5000);
            }

            payload.finalAttempt = lastAttempt;
            payload.retryCount = payload.attempts.length;
            payload.failed = !!(lastAttempt && lastAttempt.failed);
            payload.succeeded = !!(lastAttempt && !lastAttempt.failed);
            payload.after = {
                uavs: Array.isArray(comp.$data.form && comp.$data.form.uavs) ? comp.$data.form.uavs.length : null,
                drivers: Array.isArray(comp.$data.form && comp.$data.form.drivers) ? comp.$data.form.drivers.length : null,
                spaces: Array.isArray(comp.$data.form && comp.$data.form.spaces) ? comp.$data.form.spaces.length : null,
            };
            if (payload.failed) {
                payload.ok = false;
                payload.error = 'submit failed after 3 attempts, manual intervention required';
            }
            return payload;
        }
        """
    )


def inspect_add_dialogs(page, detail, plan_beg_new, plan_end_new):
    return page.evaluate(
        r"""
        async ([detail, planBegNew, planEndNew]) => {
            const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
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

            function findComponentsWithFlag(vm, flagName, depth = 0, out = []) {
                if (!vm || depth > 8 || out.length > 80) return out;
                try {
                    if (vm.$data && Object.prototype.hasOwnProperty.call(vm.$data, flagName)) out.push(vm);
                } catch (e) {}
                for (const c of (vm.$children || [])) findComponentsWithFlag(c, flagName, depth + 1, out);
                return out;
            }

            function norm(s) {
                return ((s || '').replace(/\s+/g, ' ').trim());
            }

            function isVisible(el) {
                return !!(el && el.offsetParent !== null);
            }

            function dispatchClick(el) {
                if (!el) return false;
                el.scrollIntoView({block: 'center', inline: 'center'});
                for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                    el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
                }
                return true;
            }

            function dumpVisibleDialogs(root) {
                return Array.from(root.querySelectorAll('.ivu-modal-wrap,.ivu-drawer-wrap,[role="dialog"],.el-dialog,.el-dialog__wrapper')).filter(isVisible).map(el => ({
                    text: norm((el.innerText || '')).slice(0, 1000),
                    cls: (el.className || '').toString(),
                    tag: el.tagName,
                }));
            }

            function sectionTitleFor(el) {
                let node = el;
                for (let i = 0; node && i < 8; i++, node = node.parentElement) {
                    const text = norm(node.innerText || node.textContent || '');
                    if (/航空器信息/.test(text)) return 'uav';
                    if (/操控员信息/.test(text)) return 'driver';
                    if (/飞行空域信息/.test(text)) return 'space';
                }
                return '';
            }

            function listAddButtons(root) {
                return Array.from(root.querySelectorAll('button,span,div,a'))
                    .filter(el => norm(el.textContent) === '添加' && isVisible(el))
                    .map((el, index) => ({
                        index,
                        text: norm(el.textContent),
                        tag: el.tagName,
                        cls: (el.className || '').toString(),
                        section: sectionTitleFor(el),
                        parentText: norm(el.parentElement && (el.parentElement.innerText || el.parentElement.textContent || '')).slice(0, 160),
                    }));
            }

            function findAddButtonBySection(root, section) {
                const buttonEntries = Array.from(root.querySelectorAll('button,span,div,a'))
                    .filter(el => norm(el.textContent) === '添加' && isVisible(el))
                    .map(el => ({el, section: sectionTitleFor(el)}));
                const buttonTagPreferred = buttonEntries.find(x => x.section === section && x.el.tagName === 'BUTTON' && /\baddButton\b/.test((x.el.className || '').toString()));
                if (buttonTagPreferred) return buttonTagPreferred.el;
                const buttonPreferred = buttonEntries.find(x => x.section === section && x.el.tagName === 'BUTTON');
                if (buttonPreferred) return buttonPreferred.el;
                const spanPreferred = buttonEntries.find(x => x.section === section && x.el.tagName === 'SPAN');
                if (spanPreferred) return spanPreferred.el;
                const exact = buttonEntries.find(x => x.section === section);
                if (exact) return exact.el;
                const fallbackButton = buttonEntries.find(x => x.el.tagName === 'BUTTON');
                if (fallbackButton) return fallbackButton.el;
                return section === 'uav' ? (buttonEntries[0] && buttonEntries[0].el) || null : (buttonEntries[1] && buttonEntries[1].el) || null;
            }

            function dumpVueTree(vm, depth = 0, out = []) {
                if (!vm || depth > 5 || out.length > 120) return out;
                out.push({
                    depth,
                    name: (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '',
                    refKeys: vm.$refs ? Object.keys(vm.$refs).slice(0, 12) : [],
                    dataKeys: Object.keys(vm.$data || {}).filter(k => /uav|driver|select|check|space|dialog|table|choose/i.test(k)).slice(0, 20),
                });
                for (const c of (vm.$children || [])) dumpVueTree(c, depth + 1, out);
                return out;
            }

            const comp = findMainForm(app.__vue__, 0);
            if (!comp) return {ok:false, error:'FLY_INDEX_ADD not found'};
            const addButtons = listAddButtons(doc);
            const uavAdd = findAddButtonBySection(doc, 'uav');
            if (uavAdd) dispatchClick(uavAdd);
            await sleep(500);
            const dialogsAfterUav = dumpVisibleDialogs(doc);
            const vueAfterUav = dumpVueTree(comp);
            const driverAdd = findAddButtonBySection(doc, 'driver');
            if (driverAdd) dispatchClick(driverAdd);
            await sleep(500);
            const dialogsAfterDriver = dumpVisibleDialogs(doc);
            const vueAfterDriver = dumpVueTree(comp);
            return {
                ok:true,
                fill: {
                    planBeg: planBegNew,
                    planEnd: planEndNew,
                    uavSourceCount: Array.isArray(detail.uavs) ? detail.uavs.length : 0,
                    driverSourceCount: Array.isArray(detail.drivers) ? detail.drivers.length : 0,
                },
                addButtons,
                dialogsAfterUav,
                dialogsAfterDriver,
                vueAfterUav,
                vueAfterDriver,
                refKeys: comp.$refs ? Object.keys(comp.$refs) : [],
                dataKeys: Object.keys(comp.$data || {}).filter(k => /uav|driver|select|check|space|dialog|table|choose/i.test(k)),
            };
        }
        """,
        [detail, plan_beg_new, plan_end_new, space_payload],
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
    (stage) => {
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
    """, stage, stage)


def launch_context(headless=False):
    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    p = sync_playwright().start()
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PERSIST_DIR),
        headless=headless,
        viewport={"width": 1280, "height": 900},
        ignore_https_errors=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    page = context.pages[0] if context.pages else context.new_page()
    try:
        page.on("console", lambda msg: print(f"[browser:{msg.type}] {msg.text}"))
        page.on("pageerror", lambda exc: print(f"[pageerror] {exc}"))
        page.on("response", lambda resp: print(f"[http {resp.status}] {resp.url}") if ("flyApply" in resp.url or "/oapi/" in resp.url or "/api/" in resp.url and resp.status >= 400) else None)
    except Exception:
        pass
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


def ensure_main_login_with_auto_sms(page, settle_seconds=8):
    status_before = ensure_main_page(page, settle_seconds=settle_seconds)
    result = {
        'statusBefore': status_before,
        'attemptedAutoLogin': False,
        'loginResult': None,
        'statusAfter': status_before,
    }
    if status_before.get('hasMainLogin') and not status_before.get('onLoginPage'):
        return result

    result['attemptedAutoLogin'] = True
    login_result = login_via_sms(page)
    result['loginResult'] = login_result
    status_after = ensure_main_page(page, settle_seconds=3)
    result['statusAfter'] = status_after
    return result


def fetch_latest_detail(page):
    latest = get_latest_plan(page)
    if not latest.get('ok'):
        return None, latest, None
    if not latest.get('latest'):
        return latest, {'ok': False, 'error': '最近计划列表为空', 'latest': latest}, None
    detail = get_plan_detail(page, latest['latest']['planId'])
    full_profile = load_full_submit_profile()
    if full_profile:
        detail['uavs'] = full_profile['uavs']
        detail['drivers'] = full_profile['drivers']
    return latest, None, detail


def fetch_recent_plan_details(page, limit=5):
    plans = get_recent_plans(page, page_num=1, page_size=limit)
    if not plans.get('ok'):
        return None, plans
    rows = plans.get('rows') or []
    details = []
    for row in rows[:limit]:
        plan_id = row.get('planId')
        if not plan_id:
            details.append({
                'summary': row,
                'detail_error': {'ok': False, 'error': 'missing planId'},
            })
            continue
        try:
            detail = get_plan_detail(page, plan_id)
            details.append({
                'summary': row,
                'detail': detail,
            })
        except Exception as e:
            details.append({
                'summary': row,
                'detail_error': {
                    'ok': False,
                    'error': str(e),
                    'type': type(e).__name__,
                },
            })
    return {
        'ok': True,
        'count': len(details),
        'list': plans,
        'details': details,
    }, None


def save_recent_plan_details(payload, output_path=DEFAULT_RECENT_PLAN_DETAILS_FILE):
    output_path = Path(output_path)
    output_path.write_text(
        json.dumps(sanitize_for_json(payload), ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return output_path
