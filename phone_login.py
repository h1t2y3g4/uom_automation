#!/usr/bin/env python3
"""
phone_login.py - UOM 纯手机号登录（不需要密码）

用法:
  python3 phone_login.py              # 交互式登录（自动识别图形验证码，手动输入短信验证码）
  python3 phone_login.py --check      # 检查当前 token 是否有效
  python3 phone_login.py --step1      # 仅获取验证码+发短信（非交互，供 Hermes 调用）
  python3 phone_login.py --step2 SMS_CODE  CAPTCHA  UUID  # 用短信码登录

登录流程:
  1. 自动获取图形验证码并用 ddddocr 识别
  2. 自动发送短信验证码到手机号
  3. 用户输入短信验证码
  4. 用 RSAL 加密 payload 并登录
  5. 保存 token 到 config.json
"""

import argparse
import base64
import json
import os
import sys
import time

import requests

# 本项目的 RSAL 加密模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rsal_encrypt import encrypt_payload

# =====================================================================
#  配置
# =====================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
BASE_URL = "https://uom.caac.gov.cn"
API_BASE = f"{BASE_URL}/api"

HEADERS = {
    "host": "uom.caac.gov.cn",
    "origin": BASE_URL,
    "referer": f"{BASE_URL}/#/",
    "accept": "application/json, text/plain, */*",
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "devicetype": "PC",
    "content-type": "application/json",
}


# =====================================================================
#  工具函数
# =====================================================================

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)
    print(f"  配置已保存到 {CONFIG_PATH}")


def solve_captcha(image_base64):
    """用 ddddocr 识别图形验证码"""
    try:
        import ddddocr
        ocr = ddddocr.DdddOcr(show_ad=False)
        img_bytes = base64.b64decode(image_base64)
        result = ocr.classification(img_bytes)
        return result
    except Exception as e:
        print(f"  ⚠️  验证码识别失败: {e}")
        return None


# =====================================================================
#  API 调用
# =====================================================================

def get_captcha(session):
    """获取图形验证码，返回 (image_base64, uuid)"""
    resp = session.get(
        f"{API_BASE}/captcha/anon/captchaImage",
        params={"rand": str(time.time())},
        headers=HEADERS,
        timeout=60,
    )
    data = resp.json()
    return data.get("img"), data.get("uuid")


def send_sms(session, phone, captcha_code, uuid):
    """发送短信验证码（手机号登录专用接口）"""
    resp = session.post(
        f"{API_BASE}/home/anon/sendSmsCode",
        json={
            "mobileNum": phone,
            "icode": captcha_code,
            "uuid": uuid,
            "scene": "1",  # 1=登录页面
        },
        headers=HEADERS,
        timeout=60,
    )
    return resp.json()


def do_phone_login(session, phone, sms_code, captcha_code, uuid):
    """
    执行手机号登录（不需要密码）。
    
    关键：payload 需要用 RSAL (RSA 512-bit PKCS1v15) 加密，
    然后以 {"data": "密文"} 格式发送。
    """
    # 构造 payload
    # 注意：用 mobileNum 而不是 telephone（前端 JS 虽然传 telephone，
    # 但服务端内部映射到 mobileNum；不传 telephone 可避免空值覆盖）
    payload = {
        "username": "",
        "mobileNum": phone,
        "dcode": sms_code,
        "tcode": captcha_code,
        "uuid": uuid,
        "tenant": "__default_tenant__",
        "encrypted": True,
        "encryptionMethod": "d_base64",
    }

    # RSAL 加密
    encrypted_body = encrypt_payload(payload)

    resp = session.post(
        f"{API_BASE}/home/anon/login",
        json=encrypted_body,
        headers=HEADERS,
        timeout=60,
    )
    return resp.json()


def check_token(cfg=None):
    """检查当前 token 是否有效"""
    if cfg is None:
        cfg = load_config()
    auth = cfg.get("auth", {})
    if not auth.get("pub_token"):
        return False

    headers = {
        **HEADERS,
        "authorization": f"Bearer {auth['pub_token']}",
        "pubusername": auth.get("username", ""),
        "ticket": auth.get("ticket", ""),
    }
    cookies = {
        "userName": auth.get("username", ""),
        "PUB-Token": auth["pub_token"],
    }
    try:
        resp = requests.get(
            f"{BASE_URL}/oapi/pub/planInfo/list",
            params={"pageNum": "1", "pageSize": "1", "planTypes": "11,12,13"},
            headers=headers,
            cookies=cookies,
            timeout=15,
        )
        data = resp.json()
        return data.get("code") in ["0", 0]
    except Exception:
        return False


# =====================================================================
#  登录结果处理
# =====================================================================

def process_login_result(login_result, session, cfg):
    """处理登录返回，提取 token 并保存"""
    code = login_result.get("code")
    print(f"  返回码: {code}")
    print(f"  完整返回: {json.dumps(login_result, ensure_ascii=False)[:800]}")

    if str(code) in ["0", "200", 200]:
        token = login_result.get("token")
        ticket = login_result.get("ticket", "")

        # 手机号登录可能返回 data 数组（个人/单位账户）
        if not token and isinstance(login_result.get("data"), list):
            data = login_result["data"]
            if len(data) > 0:
                # 可能需要用户选择账户，这里先取第一个
                item = data[0]
                token = (item.get("token") or item.get("pubToken")
                         or item.get("PUB-Token", ""))
                ticket = item.get("ticket", "")
                username = item.get("username", item.get("name", ""))

        # 从 cookie 获取 PUB-Token
        for cookie in session.cookies:
            if cookie.name == "PUB-Token":
                token = cookie.value
                break

        if token:
            cfg["auth"]["pub_token"] = token
            if ticket:
                cfg["auth"]["ticket"] = ticket
            if username:
                cfg["auth"]["username"] = username
            save_config(cfg)
            print(f"\n  ✅ 登录成功！")
            print(f"  Token: {token[:30]}...")
            if username:
                print(f"  用户名: {username}")
            return True
        else:
            print(f"\n  ⚠️  登录返回成功但未提取到 token")
            return False
    else:
        msg = login_result.get("msg", "未知错误")
        print(f"\n  ❌ 登录失败: {msg}")
        return False


# =====================================================================
#  交互式登录
# =====================================================================

def interactive_login():
    """交互式手机号登录流程"""
    cfg = load_config()
    phone = cfg["contact"]["phone"]

    print("═══════════════════════════════════════════")
    print("  UOM 手机号登录（无需密码，RSAL 加密）")
    print("═══════════════════════════════════════════")
    print(f"  手机号: {phone}")

    session = requests.Session()

    if check_token(cfg):
        print("\n  ✅ 当前 token 仍然有效，无需重新登录")
        return True

    print("\n  ⚠️  Token 已过期，开始重新登录...")

    # Step 1: 获取图形验证码
    print("\n  [1/3] 获取图形验证码...")
    img_b64, uuid = get_captcha(session)
    if not img_b64:
        print("  ❌ 获取验证码失败")
        return False

    captcha_path = "/tmp/uom_captcha.png"
    with open(captcha_path, "wb") as f:
        f.write(base64.b64decode(img_b64))
    print(f"  验证码图片已保存: {captcha_path}")

    captcha_text = solve_captcha(img_b64)
    if captcha_text:
        print(f"  自动识别结果: {captcha_text}")
        use_auto = input("  使用自动识别结果？(Y/n) ").strip().lower()
        if use_auto == "n":
            captcha_text = input("  请输入你看到的验证码: ").strip()
    else:
        captcha_text = input("  自动识别失败，请手动输入验证码: ").strip()

    if not captcha_text:
        print("  ❌ 验证码为空")
        return False

    # Step 2: 发送短信验证码
    print("\n  [2/3] 发送短信验证码...")
    sms_result = send_sms(session, phone, captcha_text, uuid)
    print(f"  服务器返回: {json.dumps(sms_result, ensure_ascii=False)}")

    sms_already_valid = False
    code = sms_result.get("code")
    if str(code) not in ["0", 0]:
        msg = sms_result.get("msg", "")
        if "有效期内" in msg:
            print("\n  ℹ️  上一条短信验证码还在有效期内")
            sms_already_valid = True
        elif "未正常使用次数过多" in msg:
            print("\n  ❌ 短信发送频率受限，请等待后重试")
            return False
        else:
            print(f"\n  ⚠️  短信发送异常: {msg}")
            return False

    if not sms_already_valid:
        print("\n  📱 短信验证码已发送到你的手机！")

    # Step 3: 输入短信验证码并登录
    sms_code = input("\n  [3/3] 请输入短信验证码: ").strip()
    if not sms_code:
        print("  ❌ 验证码为空")
        return False

    print("\n  执行 RSAL 加密登录...")
    login_result = do_phone_login(session, phone, sms_code, captcha_text, uuid)
    return process_login_result(login_result, session, cfg)


# =====================================================================
#  非交互步骤（供 Hermes 自动化调用）
# =====================================================================

def step1_send_sms():
    """步骤1：获取验证码 + 发短信，输出 JSON 结果"""
    cfg = load_config()
    phone = cfg["contact"]["phone"]
    session = requests.Session()

    img_b64, uuid = get_captcha(session)
    if not img_b64:
        print(json.dumps({"error": "获取验证码失败"}))
        return

    captcha_text = solve_captcha(img_b64)
    if not captcha_text:
        print(json.dumps({"error": "验证码识别失败"}))
        return

    sms_result = send_sms(session, phone, captcha_text, uuid)

    # 保存 session cookies 供 step2 使用
    cookies_dict = dict(session.cookies)
    result = {
        "captcha": captcha_text,
        "uuid": uuid,
        "sms_result": sms_result,
        "cookies": cookies_dict,
    }
    print(json.dumps(result, ensure_ascii=False))


def step2_login(sms_code, captcha_text, uuid):
    """步骤2：用短信验证码登录"""
    cfg = load_config()
    phone = cfg["contact"]["phone"]
    session = requests.Session()

    print("执行 RSAL 加密登录...")
    login_result = do_phone_login(session, phone, sms_code, captcha_text, uuid)
    return process_login_result(login_result, session, cfg)


# =====================================================================
#  主入口
# =====================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UOM 手机号登录（无需密码，RSAL 加密）")
    parser.add_argument("--check", action="store_true", help="检查当前 token 是否有效")
    parser.add_argument("--step1", action="store_true", help="获取验证码+发短信（输出 JSON）")
    parser.add_argument("--step2", nargs=3, metavar=("SMS_CODE", "CAPTCHA", "UUID"),
                        help="用短信验证码登录")
    args = parser.parse_args()

    if args.check:
        cfg = load_config()
        if check_token(cfg):
            print("✅ Token 有效")
        else:
            print("❌ Token 已过期")
    elif args.step1:
        step1_send_sms()
    elif args.step2:
        sms_code, captcha_text, uuid = args.step2
        step2_login(sms_code, captcha_text, uuid)
    else:
        interactive_login()
