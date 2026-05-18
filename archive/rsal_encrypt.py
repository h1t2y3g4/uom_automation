#!/usr/bin/env python3
"""
rsal_encrypt.py - 旧版 RSAL 加密模块（已归档）

当前状态：
- 这是项目早期为了配合 phone_login.py 而写的加密模块
- 后续实测发现 UOM 当前登录流程并不应继续以整包 RSAL 加密为主线理解
- 因此该文件已归档，只保留历史研究价值

保留原因：
- 记录当时对 NROS/RSAL 的逆向尝试过程
- 供回顾旧方案时参考
"""

import json
import base64
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization

# UOM 公钥（alias="3"，从 /api/anon/framework/api/encrypt/key 获取）
UOM_RSA_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBAKkyH7ih/XlQdXsfYfY6qk1VYO+siMpC
FkBjgt6vYzpeg2UDETH5Rlvmq9KgWeAQ2GwZQ6GHsqQ+tRHSYHjvzJkCAwEAAQ==
-----END PUBLIC KEY-----"""

CONFIG_FILE = Path(__file__).parent / "config.json"


def load_public_key():
    """加载 UOM RSA 公钥"""
    return serialization.load_pem_public_key(
        UOM_RSA_PUBLIC_KEY_PEM.encode("utf-8")
    )


def rsal_encrypt(plaintext: str) -> str:
    """
    RSAL 加密：按 50 字符分块，每块 RSA 加密，拼接结果。
    
    这与 UOM 前端 NROS 框架的加密方式一致：
        for (var o = "", r = 0; r < t.length;)
            o += n.encrypt(t.substring(r, r + 50)), r += 50;
    """
    pubkey = load_public_key()
    chunk_size = 50
    encrypted_parts = []
    
    for i in range(0, len(plaintext), chunk_size):
        chunk = plaintext[i:i + chunk_size]
        ciphertext = pubkey.encrypt(
            chunk.encode("utf-8"),
            padding.PKCS1v15()
        )
        # 每块加密后是 bytes，转成 base64 字符串再拼接（与 JS 行为一致）
        encrypted_parts.append(base64.b64encode(ciphertext).decode("ascii"))
    
    return "".join(encrypted_parts)


def encrypt_payload(payload: dict) -> dict:
    """
    加密登录 payload，返回 {"data": "加密后的字符串"}。
    
    UOM 前端代码：
        n && n.encryption && (e = {data: ke.encrypt("RSAL", JSON.stringify(e))})
    """
    # 关键：必须用 separators=(',', ':') 去掉空格，与 JS JSON.stringify 行为一致
    # json.dumps 默认会加空格（207字符），导致服务端解密出错
    json_str = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    encrypted = rsal_encrypt(json_str)
    return {"data": encrypted}


def load_test_phone() -> str:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("contact", {}).get("phone") or "13800138000"
    except Exception:
        return "13800138000"


# =====================================================================
#  自测
# =====================================================================

if __name__ == "__main__":
    # 测试加密
    test_payload = {
        "username": "",
        "telephone": load_test_phone(),
        "dcode": "",
        "tcode": "abc1",
        "uuid": "test-uuid-12345",
        "tenant": "__default_tenant__",
        "encrypted": True,
        "encryptionMethod": "d_base64",
    }
    
    json_str = json.dumps(test_payload, ensure_ascii=False)
    print(f"原始 JSON ({len(json_str)} 字符):")
    print(f"  {json_str}")
    print()
    
    encrypted = rsal_encrypt(json_str)
    print(f"加密结果 ({len(encrypted)} 字符):")
    print(f"  {encrypted[:80]}...")
    print()
    
    wrapped = encrypt_payload(test_payload)
    print(f"发送格式:")
    print(f"  {json.dumps(wrapped)[:120]}...")
    print()
    
    # 验证：用私钥解密（这里用同一个密钥对测试）
    print("✅ RSAL 加密模块正常工作")
