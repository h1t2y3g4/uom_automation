---
name: uom-automation
description: UOM 飞行申请自动提交详细用法、API 文档、逆向分析记录。
tags: [uom, drone, caac, 无人机, 飞行申请]
---

# UOM 飞行申请自动化 — 详细文档

## 文件结构

    uom-automation/
    ├── config.json       配置（认证 token、无人机、驾驶员、预设空域）
    ├── uom_api.py        API 封装层（所有网络交互）
    ├── submit.py         飞行申请提交脚本（主入口）
    ├── login.py          半自动登录模块
    ├── quick_login.py    快速登录脚本（调试用）
    └── README.md

## 命令用法

### 登录（半自动）

```bash
python3 login.py
```

流程：自动获取验证码 → ddddocr 自动识别 → 用户确认 → 自动发短信 → 用户输入短信验证码 + 密码 → 完成

### 检查 token

```bash
python3 login.py --check
```

### 手动保存 token

```bash
python3 login.py --save "TOKEN值" "TICKET值"
```

### 提交飞行申请

```bash
# 试运行
python3 submit.py --space default --beg "2026-05-16 09:00" --end "2026-05-16 10:00" --dry-run

# 实际提交
python3 submit.py --space default --beg "2026-05-16 09:00" --end "2026-05-16 10:00"

# 查询当前计划
python3 submit.py --check
```

### 添加新空域

编辑 `config.json`，在 `airspaces` 下添加：

```json
"my_space": {
    "points": "经度1,纬度1|经度2,纬度2|经度3,纬度3|经度4,纬度4",
    "spcBottom": 0,
    "spcTop": 120,
    "groupName": "自定义空域"
}
```

然后 `--space my_space`。

## API 架构（重要）

UOM 有两套 API 前缀，**必须区分**：

| 前缀 | 用途 | 示例 |
|------|------|------|
| `/api/` | 认证相关（登录、验证码、短信） | `/api/captcha/anon/captchaImage` |
| `/oapi/pub/` | 业务数据（飞行计划） | `/oapi/pub/planInfo` |

**坑**：不带前缀直接访问会返回 HTML 页面而非 JSON。

## 认证机制

1. **Cookie**: `userName=xxx; PUB-Token=xxx`
2. **Header**: `authorization: Bearer xxx`, `pubusername: xxx`, `ticket: xxx`, `devicetype: PC`
3. Token 有效期约 1 小时
4. 密码用 **base64 编码**（单次），不是 RSA

## 已抓到的 API 接口

### 数据接口

| 接口 | Method | 用途 |
|------|--------|------|
| `/oapi/pub/planInfo` | POST | 提交飞行计划 |
| `/oapi/pub/planInfo/list?pageNum=1&pageSize=20&planTypes=11,12,13` | GET | 查询飞行计划列表 |
| `/oapi/pub/pubEmergentApply/getMyApply` | GET | 查询紧急申请 |

### 认证接口

| 接口 | Method | 用途 |
|------|--------|------|
| `/api/captcha/anon/captchaImage` | GET | 获取图形验证码 |
| `/api/home/anon/usernameLogin` | POST | 账号密码登录 |
| `/api/home/anon/sendSmsCodeByUserName` | POST | 发送短信验证码 |
| `/nvwa/getLoginContext` | GET | 心跳续期 |

## 登录 Payload 格式

```json
{
  "username": "用户",
  "password": "base64编码密码",
  "smscode": "短信验证码",
  "dcode": "短信验证码",
  "tcode": "图形验证码",
  "uuid": "验证码uuid",
  "tenant": "__default_tenant__"
}
```

**关键坑**：`smscode`、`dcode`、`tcode` 三个字段必须都传，只传一个会报"参数为空"。

## 飞行计划 Payload 关键字段

| 字段 | 说明 | 示例 |
|------|------|------|
| planType | 11=报备, 12=审批, 13=其他 | "11" |
| planBeg/planEnd | 起止时间 | "2026-05-15 17:50:00" |
| spaces[].locationWgs84 | WGS84 坐标 | "lng1,lat1\|lng2,lat2\|..." |
| spaces[].spcTop | 最高高度(米) | 120 |
| uavs[].sn | 无人机序列号 | 从 UOM 账号获取 |
| drivers[].pdiSeq | 驾驶员记录 ID | 从 UOM 账号获取 |

## Pitfalls

1. **API 前缀**：认证用 `/api/`，数据用 `/oapi/pub/`
2. **密码编码**：base64，不是 RSA。明文密码返回"参数为空"
3. **三个验证码字段**：smscode + dcode + tcode 都要传
4. **短信频率限制**：多次发送未使用会被锁 1 小时
5. **multipart/form-data**：提交飞行计划时用 `requests.post(data=payload)` 而非 `json=payload`
6. **图形验证码**：ddddocr 识别准确率高，脚本会展示让用户确认

## 待完成

- [ ] 自动登录模块实际测试（等短信限制解除）
- [ ] 登录成功后测试飞行申请提交
- [ ] 起飞确认接口抓包和实现
- [ ] 多空域批量提交
- [ ] heartbeat 续期实现
