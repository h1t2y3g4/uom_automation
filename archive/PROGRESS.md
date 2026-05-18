# UOM 自动化 - 当前进度（2026-05-13）

## 已完成

1. **RSAL 加密模块** `rsal_encrypt.py` — 自测通过，加密长度与 JS 一致（352 字符）
2. **手机号登录脚本** `phone_login.py` — 支持 --step1/--step2 分步调用
3. **删除旧脚本** — login.py、quick_login.py 已删除
4. **Skill 文档** — 已更新，包含所有坑点和解决方案

## 已发现并修复的 Bug

**json.dumps 空格问题**：
- `json.dumps` 默认用 `", "` 和 `": "`（带空格），导致明文 207 字符
- JS `JSON.stringify` 不带空格，明文 192 字符
- 差异导致加密后密文不同，服务端解密出错
- 修复：`json.dumps(..., separators=(',', ':'))`
- 加密长度已验证匹配（352 字符）✅

## 待验证

- 完整登录流程（获取验证码 → 发短信 → RSAL 加密 → 登录）尚未跑通
- 被短信频率限制阻塞，等限制解除后测试

## 下次执行步骤

```bash
cd ~/hermes_interface/uom-automation

# 1. 发短信
python3 phone_login.py --step1
# 输出 JSON: {"captcha": "xxx", "uuid": "xxx", "sms_result": {...}}

# 2. 输入短信验证码后登录
python3 phone_login.py --step2 <SMS_CODE> <CAPTCHA> <UUID>
```

## 备选方案：浏览器登录

如果 Python 加密仍有问题，可用浏览器方案：
1. 浏览器打开 UOM 登录页
2. 用 Vue `$set` 设置表单数据（browser_type 不改 Vue data）
3. 提交 → 提取 token → 保存到 config.json
