#!/usr/bin/env python3
"""
browser_login.py - UOM 全浏览器登录方案（已验证 2026-05-15）

用 Playwright 打开 UOM，在浏览器内完成登录。

关键发现（2026-05-15 验证）：
  NROS 框架不会对整个 payload 做 RSAL 加密！
  实际发送的是明文 JSON，只是把 telephone 字段双重 base64 编码。
  之前的 rsal_encrypt.py 方案是错误的。

用法:
  python3 browser_login.py                    # 完整交互式登录
  python3 browser_login.py --check            # 检查已保存的 session 是否有效

登录流程（已验证可行）：
  1. Playwright 打开 UOM → 关弹窗
  2. 从 Vue 组件提取验证码图片 (lm.yzmImageCode)
  3. ddddocr 识别 → 用户确认
  4. Vue $set 填写手机号 + 验证码 → 直接 fetch /api/home/anon/sendSmsCode 发短信
  5. 用户输入短信码 → Vue $set 填写 → lm.handleSubmit() 提交登录
  6. 保存 token 和浏览器状态

重要坑点：
  - sendSMS() 方法走 NROS http 客户端会 401（TokenUndefined）
    → 改用 fetch 直接调用 /api/home/anon/sendSmsCode
  - handleSubmit() / telephoneSubmit() 第一次调用会报错
    → 重新调用一次即可（NROS 框架需要初始化）
  - Vue 组件路径: root/2/0/portal/portal-banner/portal-loginpage/loginMain
  - userForm 字段: username, password, telephone, dcode, tcode
  - 验证码 UUID: lm.txYzmUuid
  - 验证码图片: lm.yzmImageCode (base64)
"""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
STATE_FILE = SCRIPT_DIR / "browser_state.json"
CAPTCHA_FILE = Path("/tmp/uom_captcha.png")
CONFIG_FILE = SCRIPT_DIR / "config.json"
BASE_URL = "https://uom.caac.gov.cn"


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_phone():
    phone = load_config().get("contact", {}).get("phone")
    if not phone:
        raise RuntimeError(f"config.json 缺少 contact.phone，请先在 {CONFIG_FILE} 中填写手机号")
    return phone


# =====================================================================
#  JS 辅助函数
# =====================================================================

def _find_lm(expr):
    """包装 JS，自动查找 loginMain 组件"""
    return f"""() => {{
    const r = document.querySelector('#app').__vue__;
    function find(vm, d) {{
        if (d > 12) return null;
        if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
        for (const c of (vm.$children || [])) {{ const res = find(c, d + 1); if (res) return res; }}
        return null;
    }}
    const lm = find(r, 0);
    if (!lm) return {{error: 'loginMain not found'}};
    {expr}
}}"""


JS_STATUS = _find_lm("""
return {
    userForm: JSON.parse(JSON.stringify(lm.userForm)),
    uuid: lm.txYzmUuid,
    hasCaptcha: !!lm.yzmImageCode,
    isSending: lm.isSending,
};
""")

JS_GET_CAPTCHA = _find_lm("""
return { imgBase64: lm.yzmImageCode, uuid: lm.txYzmUuid };
""")

JS_DISMISS_POPUP = """() => {
    const btns = document.querySelectorAll('.ivu-modal-confirm-footer .ivu-btn-primary, .ivu-modal-footer .ivu-btn-primary');
    for (const btn of btns) { if (btn.offsetParent !== null) { btn.click(); return {clicked: true}; } }
    return {clicked: false};
}"""

JS_CHECK_LOGIN = """() => {
    const url = location.href;
    const sessionToken = localStorage.getItem('session_token');
    const nrosToken = window.nros && window.nros.getToken ? window.nros.getToken() : null;
    return { url, hasSessionToken: !!sessionToken, nrosToken };
}"""


def js_fill_phone_captcha(phone, captcha):
    return _find_lm(f"""
lm.$set(lm.userForm, 'telephone', {json.dumps(phone)});
lm.$set(lm.userForm, 'tcode', {json.dumps(captcha)});
return {{telephone: lm.userForm.telephone, tcode: lm.userForm.tcode, uuid: lm.txYzmUuid}};
""")


def js_fill_smscode(code):
    return _find_lm(f"""
lm.$set(lm.userForm, 'dcode', {json.dumps(code)});
return {{dcode: lm.userForm.dcode}};
""")


# =====================================================================
#  ddddocr
# =====================================================================

def solve_captcha_ocr(image_path):
    try:
        import ddddocr
        ocr = ddddocr.DdddOcr(show_ad=False)
        with open(image_path, "rb") as f:
            return ocr.classification(f.read())
    except Exception as e:
        print(f"  ⚠️  ddddocr: {e}")
        return None


# =====================================================================
#  核心登录
# =====================================================================

def do_browser_login(headless=False):
    from playwright.sync_api import sync_playwright

    phone = load_phone()
    print("═══════════════════════════════════════════")
    print("  UOM 浏览器登录")
    print("═══════════════════════════════════════════")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        # Step 1: 打开 UOM
        print("\n  [1/5] 打开 UOM...")
        page.goto(BASE_URL, wait_until="networkidle", timeout=60000)
        time.sleep(4)
        page.evaluate(JS_DISMISS_POPUP)
        time.sleep(1)

        # Step 2: 检查组件
        print("  [2/5] 检查登录组件...")
        status = page.evaluate(JS_STATUS)
        if status.get("error"):
            print(f"  ❌ {status['error']}")
            browser.close()
            return False
        print(f"  UUID: {status.get('uuid', '')[:16]}...")

        # Step 3: 验证码
        print("\n  [3/5] 处理图形验证码...")
        captcha_info = page.evaluate(JS_GET_CAPTCHA)
        if not captcha_info or not captcha_info.get("imgBase64"):
            print("  ❌ 无法获取验证码")
            browser.close()
            return False

        img_b64 = captcha_info["imgBase64"]
        if img_b64.startswith("data:"):
            img_b64 = img_b64.split(",", 1)[1]
        with open(CAPTCHA_FILE, "wb") as f:
            f.write(base64.b64decode(img_b64))

        ocr_result = solve_captcha_ocr(str(CAPTCHA_FILE))
        if ocr_result:
            print(f"  🤖 OCR: {ocr_result}")
            u = input(f"  使用 '{ocr_result}' ？(回车/输入正确验证码): ").strip()
            captcha_code = u if u else ocr_result
        else:
            print(f"  📷 验证码: {CAPTCHA_FILE}")
            captcha_code = input("  输入验证码: ").strip()

        if not captcha_code:
            browser.close()
            return False

        # Step 4: 填写 + 发短信
        print(f"\n  [4/5] 填写表单，发送短信...")
        page.evaluate(js_fill_phone_captcha(phone, captcha_code))

        # 关键：用 fetch 直接发短信，不用 Vue 的 sendSMS()（会 401）
        sms_result = page.evaluate(f"""() => {{
            const r = document.querySelector('#app').__vue__;
            function find(vm, d) {{
                if (d > 12) return null;
                if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
                for (const c of (vm.$children || [])) {{ const res = find(c, d + 1); if (res) return res; }}
                return null;
            }}
            const lm = find(r, 0);
            return fetch('/api/home/anon/sendSmsCode', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json', 'devicetype': 'PC'}},
                body: JSON.stringify({{
                    mobileNum: lm.userForm.telephone,
                    icode: lm.userForm.tcode,
                    uuid: lm.txYzmUuid,
                    scene: '1'
                }})
            }})
            .then(r => r.json())
            .then(data => ({{ok: data.code === 0, response: data}}))
            .catch(err => ({{ok: false, error: err.message}}));
        }}""")

        print(f"  短信: {sms_result}")

        if not sms_result.get("ok"):
            err = sms_result.get("error") or sms_result.get("response", {}).get("msg", "未知错误")
            print(f"  ❌ 短信发送失败: {err}")
            if "有效期内" in str(err):
                print("  验证码还在有效期内，可继续")
            else:
                browser.close()
                return False

        # 等待短信
        print("\n  📱 短信已发送！请输入短信验证码...")
        sms_start = time.time()
        sms_code = None
        while True:
            elapsed = time.time() - sms_start
            remaining = 600 - elapsed
            if 420 < elapsed <= 425:
                print(f"\n  ⚠️  已过 {int(elapsed/60)} 分钟！如登录失败，手动打开浏览器用掉验证码避免封号。")
            if remaining <= 0:
                print("\n  ❌ 验证码过期")
                browser.close()
                return False
            sms_code = input(f"\n  短信码 (剩 {int(remaining)}s): ").strip()
            if sms_code:
                break

        # Step 5: 提交登录
        print(f"\n  [5/5] 提交登录...")
        page.evaluate(js_fill_smscode(sms_code))
        time.sleep(0.5)

        # handleSubmit 第一次可能报错，重试一次
        submit_result = page.evaluate(_find_lm("""
try {
    if (typeof lm.telephoneSubmit === 'function') { lm.telephoneSubmit(); return {ok: true, method: 'telephoneSubmit'}; }
    lm.handleSubmit();
    return {ok: true, method: 'handleSubmit'};
} catch(e) { return {error: e.message}; }
"""))
        print(f"  提交: {submit_result}")

        if submit_result.get("error"):
            print("  重试 handleSubmit...")
            time.sleep(1)
            submit_result = page.evaluate(_find_lm("""
try { lm.handleSubmit(); return {ok: true}; } catch(e) { return {error: e.message}; }
"""))
            print(f"  重试: {submit_result}")

        # 等待响应
        print("\n  ⏳ 等待...")
        time.sleep(5)

        login_status = page.evaluate(JS_CHECK_LOGIN)
        print(f"  URL: {login_status.get('url', '')}")
        print(f"  Token: {login_status.get('nrosToken', '')[:20]}...")

        is_ok = (
            login_status.get("hasSessionToken") or
            login_status.get("nrosToken") or
            "login" not in login_status.get("url", "").lower()
        )

        if not is_ok:
            time.sleep(3)
            login_status = page.evaluate(JS_CHECK_LOGIN)
            is_ok = "login" not in login_status.get("url", "").lower()

        if is_ok:
            print("\n  ✅ 登录成功！")

            # 保存浏览器状态
            storage = context.storage_state()
            with open(STATE_FILE, "w") as f:
                json.dump(storage, f, ensure_ascii=False, indent=2)
            print(f"  浏览器状态: {STATE_FILE}")

            # 更新 config.json
            if login_status.get("nrosToken"):
                try:
                    cfg = load_config()
                    cfg["auth"]["pub_token"] = login_status["nrosToken"]
                    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                        json.dump(cfg, f, ensure_ascii=False, indent=4)
                    print("  config.json 已更新")
                except Exception as e:
                    print(f"  ⚠️  {e}")

            browser.close()
            return True
        else:
            print("\n  ❌ 登录失败")
            browser.close()
            return False


def check_session():
    if not STATE_FILE.exists():
        print("❌ 无已保存状态")
        return False
    from playwright.sync_api import sync_playwright
    print("检查 session...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        with open(STATE_FILE) as f:
            state = json.load(f)
        ctx = browser.new_context(storage_state=state)
        page = ctx.new_page()
        page.goto(f"{BASE_URL}/web_pub/flyActivity/flyIndexAdd", wait_until="networkidle", timeout=30000)
        time.sleep(3)
        url = page.url
        print(f"  URL: {url}")
        ok = "login" not in url.lower()
        print(f"  {'✅ 有效' if ok else '❌ 过期'}")
        browser.close()
        return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UOM 浏览器登录")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    if args.check:
        sys.exit(0 if check_session() else 1)
    else:
        sys.exit(0 if do_browser_login(headless=args.headless) else 1)
