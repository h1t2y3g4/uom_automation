#!/usr/bin/env python3
"""
auth.py - 登录认证（SMS、验证码、登录态检查）
"""

import base64
import json
import select
import sys
import time
from datetime import datetime

from core.constants import BASE_URL, CAPTCHA_FILE, SMS_CODE_FILE
from core.config import load_phone, read_sms_code_file, write_sms_code_file


def require_pillow():
    try:
        from PIL import Image, ImageChops, ImageDraw
        if Image is None or ImageChops is None or ImageDraw is None:
            raise RuntimeError('当前环境缺少 Pillow，无法执行空域瓦片像素分析')
    except ImportError:
        raise RuntimeError('当前环境缺少 Pillow，无法执行空域瓦片像素分析')


def is_sms_sent_within_window(sent_at_str, window_seconds=600):
    """判断 sent_at 是否在 window_seconds 秒内（默认 10 分钟）。"""
    if not sent_at_str:
        return False
    try:
        sent_dt = datetime.fromisoformat(sent_at_str)
        return (datetime.now() - sent_dt).total_seconds() < window_seconds
    except (ValueError, TypeError):
        return False


def wait_for_sms_code_from_file(timeout_s=600):
    """
    轮询 sms_code.json，每秒读一次。
    当 filled_at 非空、在 sent_at 之后、且距 sent_at 不超过 10 分钟时，返回 code。
    若 stdin 是终端（人工运行），同时监听 stdin，用户可直接输入验证码。
    超时返回 None。
    """
    interactive = sys.stdin.isatty()
    if interactive:
        print('提示：你也可以直接在此处输入短信验证码并回车，或在 sms_code.json 中写入后自动读取。')
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        # 1. 检查文件
        data = read_sms_code_file()
        code = (data.get('code') or '').strip()
        filled_at = (data.get('filled_at') or '').strip()
        sent_at = (data.get('sent_at') or '').strip()
        if code and filled_at and sent_at:
            try:
                sent_dt = datetime.fromisoformat(sent_at)
                filled_dt = datetime.fromisoformat(filled_at)
                if filled_dt >= sent_dt and (filled_dt - sent_dt).total_seconds() < 600:
                    return code
            except (ValueError, TypeError):
                pass

        # 2. 交互模式下非阻塞读 stdin
        if interactive:
            try:
                rlist, _, _ = select.select([sys.stdin], [], [], 0)
                if rlist:
                    line = sys.stdin.readline().strip()
                    if line:
                        return line
            except Exception:
                pass

        time.sleep(1)
    return None


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
    """只有 code=0 才算真正的发送成功。code=1 表示有其他信息（如验证码还在有效期），不算成功。"""
    return str(code) == "0"


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


def fill_login_phone(page, phone):
    """在登录页填入手机号（不发送短信）。"""
    return page.evaluate(
        """
        ([phone]) => {
            const app = document.querySelector('#app').__vue__;
            function find(vm, d) {
                if (d > 12) return null;
                if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
                for (const c of (vm.$children || [])) { const r = find(c, d + 1); if (r) return r; }
                return null;
            }
            const lm = find(app, 0);
            if (!lm) return {ok: false, error: '找不到登录组件'};
            lm.$set(lm.userForm, 'telephone', phone);
            return {ok: true};
        }
        """,
        [phone],
    )


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
        'smsCodeSource': 'file',
        'waitedForSmsInput': False,
        'reusedExistingSmsCode': False,
    }

    # ---- 始终填入手机号 ----
    fill_result = fill_login_phone(page, phone)
    print(f'填入手机号: {fill_result}')

    # ---- 判断是否需要重新发送短信 ----
    sms_file = read_sms_code_file()
    skip_send = is_sms_sent_within_window(sms_file.get('sent_at', ''))

    if skip_send:
        print('检测到 sms_code.json 中 sent_at 仍在 10 分钟有效期内，跳过重复发送')
        login_trace['reusedExistingSmsCode'] = True
    else:
        captcha_meta = fetch_login_captcha_with_ocr(page)
        captcha = (captcha_meta.get('ocr') or '').strip()
        if not captcha:
            raise RuntimeError("OCR 未识别出图形验证码")

        sms_result = request_login_sms(page, phone, captcha)
        login_trace['captchaAttempts'].append({
            'captcha': captcha,
            'uuid': captcha_meta.get('uuid'),
            'ocr': captcha_meta.get('ocr'),
            'smsResult': sms_result,
        })
        print("发短信返回:", sms_result)

        resp_code = str(sms_result.get("code", ""))
        if resp_code == "0":
            # code=0：发送成功，记录 sent_at
            write_sms_code_file({
                'code': '',
                'sent_at': datetime.now().isoformat(),
                'filled_at': '',
            })
            print(f'短信已发送，请在 {SMS_CODE_FILE} 中写入验证码')
        elif resp_code == "1":
            # code=1：有其他信息（如验证码还在有效期等），不算发送成功
            msg = sms_result.get("msg") or sms_result.get("message") or sms_result.get("data") or ""
            print(f'短信发送返回 code=1，未成功。响应信息: {msg or sms_result}')
            if is_sms_still_valid_response(sms_result):
                print('短信验证码仍在有效期内，不更新 sent_at，等待原有验证码写入。')
                login_trace['reusedExistingSmsCode'] = True
                # 不更新 sent_at，因为没有新短信发出，保留原发送时间
            else:
                # code=1 但不是"验证码在有效期"的情况，记录详细信息
                print(f'⚠️ 短信发送返回 code=1 且非验证码有效期问题，请检查响应: {sms_result}')
                login_trace['smsCode1Info'] = str(msg or sms_result)
        elif is_captcha_error_response(sms_result):
            raise RuntimeError(
                '本次发短信返回图形验证码错误。为避免重复发送短信导致限制，当前流程不会自动再次发短信。'
                '请更新 sms_code.json 中的验证码后重新发起登录。'
            )
        else:
            raise RuntimeError(f"短信发送失败: {sms_result}")

    # ---- 等待文件中的验证码 ----
    print('等待短信验证码写入 sms_code.json（每秒轮询，10 分钟超时）...')
    sms = wait_for_sms_code_from_file(timeout_s=600)
    if not sms:
        raise RuntimeError("等待短信验证码超时（10 分钟），登录中止")

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
