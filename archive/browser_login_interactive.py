#!/usr/bin/env python3
"""
browser_login_interactive.py - 旧版浏览器交互登录脚本（已归档）

当前状态：
- 这是项目早期为 stdin/stdout 交互设计的浏览器登录脚本
- 使用 JSON 协议与外部调用方通信
- 现在不是当前开发主线

保留原因：
- 可参考早期交互协议设计
- 可参考验证码确认与短信码输入流程
"""

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


def send(obj):
    """输出 JSON 到 stdout"""
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def recv():
    """从 stdin 读取 JSON"""
    line = sys.stdin.readline().strip()
    if not line:
        return None
    return json.loads(line)


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_phone():
    phone = load_config().get("contact", {}).get("phone")
    if not phone:
        raise RuntimeError(f"config.json 缺少 contact.phone，请先在 {CONFIG_FILE} 中填写手机号")
    return phone


def find_login_main(page):
    """查找 loginMain Vue 组件"""
    return page.evaluate("""() => {
        const r = document.querySelector('#app').__vue__;
        function find(vm, d) {
            if (d > 12) return null;
            if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
            for (const c of (vm.$children || [])) { const res = find(c, d + 1); if (res) return res; }
            return null;
        }
        return find(r, 0) ? true : false;
    }""")


def get_captcha(page):
    """获取验证码图片"""
    return page.evaluate("""() => {
        const r = document.querySelector('#app').__vue__;
        function find(vm, d) {
            if (d > 12) return null;
            if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
            for (const c of (vm.$children || [])) { const res = find(c, d + 1); if (res) return res; }
            return null;
        }
        const lm = find(r, 0);
        if (!lm) return null;
        return { imgBase64: lm.yzmImageCode, uuid: lm.txYzmUuid };
    }""")


def fill_and_send_sms(page, phone, captcha):
    """填写手机号+验证码并发送短信"""
    # 填写
    page.evaluate("""(args) => {
        const r = document.querySelector('#app').__vue__;
        function find(vm, d) {
            if (d > 12) return null;
            if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
            for (const c of (vm.$children || [])) { const res = find(c, d + 1); if (res) return res; }
            return null;
        }
        const lm = find(r, 0);
        lm.$set(lm.userForm, 'telephone', args[0]);
        lm.$set(lm.userForm, 'tcode', args[1]);
    }""", [phone, captcha])

    time.sleep(0.3)

    # 发送短信
    return page.evaluate("""() => {
        const r = document.querySelector('#app').__vue__;
        function find(vm, d) {
            if (d > 12) return null;
            if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
            for (const c of (vm.$children || [])) { const res = find(c, d + 1); if (res) return res; }
            return null;
        }
        const lm = find(r, 0);
        if (!lm.userForm.telephone) return {error: '手机号未填写'};
        if (!lm.userForm.tcode) return {error: '图形验证码未填写'};
        try { lm.sendSMS(); return {ok: true}; }
        catch(e) { return {error: e.message}; }
    }""")


def submit_login(page, sms_code):
    """填写短信码并提交登录"""
    # 填写短信码
    page.evaluate("""(code) => {
        const r = document.querySelector('#app').__vue__;
        function find(vm, d) {
            if (d > 12) return null;
            if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
            for (const c of (vm.$children || [])) { const res = find(c, d + 1); if (res) return res; }
            return null;
        }
        const lm = find(r, 0);
        lm.$set(lm.userForm, 'dcode', code);
    }""", sms_code)

    time.sleep(0.5)

    # 提交
    return page.evaluate("""() => {
        const r = document.querySelector('#app').__vue__;
        function find(vm, d) {
            if (d > 12) return null;
            if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
            for (const c of (vm.$children || [])) { const res = find(c, d + 1); if (res) return res; }
            return null;
        }
        const lm = find(r, 0);
        if (!lm.userForm.dcode) return {error: '短信验证码未填写'};
        try {
            if (typeof lm.telephoneSubmit === 'function') { lm.telephoneSubmit(); return {ok: true, method: 'telephoneSubmit'}; }
            lm.handleSubmit();
            return {ok: true, method: 'handleSubmit'};
        } catch(e) { return {error: e.message}; }
    }""")


def check_login_result(page):
    """检查登录结果"""
    time.sleep(5)
    return page.evaluate("""() => {
        const url = window.location.href;
        const sessionToken = localStorage.getItem('session_token');
        let pubToken = null;
        const m = document.cookie.match(/PUB-Token=([^;]+)/);
        if (m) pubToken = m[1];
        return {
            url: url,
            hasSessionToken: !!sessionToken,
            hasPubToken: !!pubToken,
            pubToken: pubToken,
        };
    }""")


def main():
    from playwright.sync_api import sync_playwright

    phone = load_phone()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        # Step 1: 打开 UOM
        send({"step": "opening", "msg": "正在打开 UOM..."})
        page.goto(BASE_URL, wait_until="networkidle", timeout=60000)
        time.sleep(4)

        # 关弹窗
        page.evaluate("""() => {
            const btns = document.querySelectorAll('.ivu-modal-confirm-footer .ivu-btn-primary, .ivu-modal-footer .ivu-btn-primary');
            for (const btn of btns) { if (btn.offsetParent !== null) { btn.click(); return true; } }
            return false;
        }""")
        time.sleep(1)

        # Step 2: 检查组件
        found = find_login_main(page)
        if not found:
            send({"step": "error", "msg": "找不到登录组件"})
            browser.close()
            return

        # Step 3: 获取验证码
        captcha = get_captcha(page)
        if not captcha or not captcha.get("imgBase64"):
            send({"step": "error", "msg": "无法获取验证码"})
            browser.close()
            return

        # 保存验证码图片
        img_b64 = captcha["imgBase64"]
        if img_b64.startswith("data:"):
            img_b64 = img_b64.split(",", 1)[1]
        with open(CAPTCHA_FILE, "wb") as f:
            f.write(base64.b64decode(img_b64))

        # ddddocr 识别
        ocr_result = None
        try:
            import ddddocr
            ocr = ddddocr.DdddOcr(show_ad=False)
            with open(CAPTCHA_FILE, "rb") as f:
                ocr_result = ocr.classification(f.read())
        except Exception:
            pass

        send({
            "step": "captcha",
            "ocr_result": ocr_result,
            "uuid": captcha["uuid"],
            "captcha_path": str(CAPTCHA_FILE),
            "phone": phone,
        })

        # 等待用户确认验证码
        cmd = recv()
        if not cmd or cmd.get("action") != "confirm_captcha":
            send({"step": "error", "msg": "expected confirm_captcha"})
            browser.close()
            return

        captcha_code = cmd["captcha"]
        if not captcha_code:
            send({"step": "error", "msg": "captcha empty"})
            browser.close()
            return

        # Step 4: 填写并发送短信
        sms_result = fill_and_send_sms(page, phone, captcha_code)

        if sms_result.get("error"):
            err = sms_result["error"]
            send({"step": "sms_failed", "error": err})

            if "有效期内" in err:
                # 验证码还在有效期内，继续等待 SMS
                pass
            else:
                browser.close()
                return

        send({"step": "sms_sent", "ok": True, "msg": "短信验证码已发送"})

        # 等待用户输入短信码
        cmd = recv()
        if not cmd or cmd.get("action") != "input_sms":
            send({"step": "error", "msg": "expected input_sms"})
            browser.close()
            return

        sms_code = cmd["sms_code"]

        # Step 5: 提交登录
        submit_result = submit_login(page, sms_code)
        send({"step": "submitting", "result": submit_result})

        # 检查结果
        login_result = check_login_result(page)
        page.screenshot(path="/tmp/uom_login_result.png")

        is_ok = (
            login_result.get("hasSessionToken") or
            login_result.get("hasPubToken") or
            "login" not in login_result.get("url", "").lower()
        )

        # 再等一下看跳转
        if not is_ok:
            time.sleep(3)
            login_result = page.evaluate("""() => {
                const url = window.location.href;
                const sessionToken = localStorage.getItem('session_token');
                let pubToken = null;
                const m = document.cookie.match(/PUB-Token=([^;]+)/);
                if (m) pubToken = m[1];
                return { url, hasSessionToken: !!sessionToken, hasPubToken: !!pubToken, pubToken };
            }""")
            is_ok = (
                login_result.get("hasSessionToken") or
                login_result.get("hasPubToken") or
                "login" not in login_result.get("url", "").lower()
            )

        if is_ok:
            # 保存浏览器状态
            storage = context.storage_state()
            with open(STATE_FILE, "w") as f:
                json.dump(storage, f, ensure_ascii=False, indent=2)

            # 更新 config.json
            if login_result.get("pubToken"):
                try:
                    cfg = load_config()
                    cfg["auth"]["pub_token"] = login_result["pubToken"]
                    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                        json.dump(cfg, f, ensure_ascii=False, indent=4)
                except Exception:
                    pass

        send({
            "step": "login_result",
            "ok": is_ok,
            "url": login_result.get("url", ""),
            "hasSessionToken": login_result.get("hasSessionToken"),
            "hasPubToken": login_result.get("hasPubToken"),
            "screenshot": "/tmp/uom_login_result.png",
        })

        browser.close()


if __name__ == "__main__":
    main()
