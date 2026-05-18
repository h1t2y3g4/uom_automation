# UOM 自动化工具

UOM（无人驾驶航空器综合管理平台）飞行申请自动提交脚本。

基于浏览器抓包逆向，直接调用 UOM 后端 API，无需浏览器。

## 文件结构

    uom-automation/
    ├── config.json       # 配置文件（认证信息、无人机、驾驶员、预设空域）
    ├── uom_api.py        # API 封装层（所有网络请求）
    ├── submit.py         # 飞行申请提交脚本（主入口）
    └── README.md         # 本文件

## 使用方法

### 1. 更新 token

每次使用前确认 token 有效：

    python3 submit.py --check-token

如果过期了：
- 打开 https://uom.caac.gov.cn 并登录
- F12 → Console → 输入 document.cookie
- 从返回的字符串中找到 PUB-Token=xxx 的值
- 更新 config.json 中 auth.pub_token
- 同时更新 auth.ticket

### 2. 查询当前飞行计划

    python3 submit.py --check

### 3. 试运行（不实际提交）

    python3 submit.py --space default --beg "2026-05-16 09:00" --end "2026-05-16 10:00" --dry-run

### 4. 实际提交

    python3 submit.py --space default --beg "2026-05-16 09:00" --end "2026-05-16 10:00"

会要求确认后才提交。

### 5. 添加新空域

编辑 config.json，在 airspaces 下添加：

    "my_space": {
        "points": "经度1,纬度1|经度2,纬度2|经度3,纬度3|经度4,纬度4",
        "spcBottom": 0,
        "spcTop": 120,
        "groupName": "自定义空域"
    }

然后用 --space my_space 指定。

## 注意事项

- PUB-Token 有效期未知，过期后需重新登录获取
- 本脚本仅供个人学习研究使用，请遵守相关法规
- 飞行申请涉及空域安全，请确保信息准确
