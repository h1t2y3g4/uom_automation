# UOM 自动化项目说明

目录：`/home/skye/hermes_interface/uom-automation`

## 当前项目进度（截至本次会话）

已完成：
- 已确认并保留一份可复用的 Playwright 持久化浏览器 profile：
  - `/home/skye/hermes_interface/uom-automation/.playwright-uom-profile`
- 已将项目重构为 4 个明确职责文件：
  - `uom_core.py`：底层能力
  - `uom_login.py`：登录/状态入口
  - `uom_probe.py`：无副作用探测流程
  - `uom_semiauto.py`：半自动提交流程
- 已验证浏览器登录态判断已加强：
  - 结合 `window.nros.getToken()`
  - `localStorage.session_token`
  - 页面主站 UI 文本（首页/运行管理等）
  - 当前 URL 是否仍为 `#/login`
- 已验证可以稳定进入：
  - `运行管理 -> 飞行活动申请 -> 一般飞行活动`
- 已验证可以从业务 iframe 中读取：
  - `PUB-Token`
  - `ticket`
  - `userName`
- 已验证可以通过 iframe `/oapi/pub/planInfo/list` 查询历史计划
- 已验证可以通过 iframe `/oapi/pub/planInfo/{planId}` 查询单条详情
- 已验证可以自动打开新增页 `flyIndexAdd`
- 已验证可以把“上一次计划”的关键内容灌入新增页：
  - 下周二同时间
  - 空域经纬度
  - 高度
  - 通信方式
  - 航空器/操控员数据
- 已验证新增页前端显示层会出现航空器和操控员行

未完成 / 当前阻塞：
- 新增页前端校验仍然卡在：
  - `uavs`
  - `drivers`
- 即使页面已经显示航空器和操控员行，`this.$refs.form.validate()` 仍然返回失败
- 点击“提交申请”按钮可以触发点击事件，但不会稳定生成新计划
- 当前判断：UOM 前端把“页面里显示了一行数据”和“选择器内部已正式选中”分成了两套状态；脚本还没有把这套内部选中状态补齐

本次确认的负结论：
- 不能把“按钮点到了”误判成“提交成功”
- 不能把“页面里显示了 1 架航空器 / 1 个操控员”误判成“前端校验已通过”
- 不能只看残留 token / session_token 就当成已登录；若 URL 仍在 `#/login` 或主站框架没起来，应优先视为假阳性登录态
- 直接 `POST /oapi/pub/planInfo` 仍然会报：
  - `planInfo is null`
  - 所以不能回退成直接 API 提交方案

## 推荐主线

当前最可靠主线：
1. 复用持久化浏览器 profile
2. 用 `uom_login.py` / `uom_probe.py` / `uom_semiauto.py` 进入相应模式
3. 进入一般飞行活动列表
4. 读取最近计划详情
5. 打开新增页
6. 自动填充目标时间和上次计划内容
7. 继续研究并补齐“航空器/操控员选择器内部选中状态”
8. 待 `validate()` 真正通过后，再提交

## 主要脚本

现役主线：
- `uom_core.py`
  - 只放底层能力
  - 包括持久化 profile、状态检测、进入一般飞行活动、读取历史计划、打开新增页、自动填表、探测、提交流程所需的公共函数
- `uom_login.py`
  - 登录/状态入口
- `uom_probe.py`
  - 无副作用探测流程
- `uom_semiauto.py`
  - 半自动提交流程

## 使用方式

登录/状态相关：

```bash
python3 uom_login.py status
python3 uom_login.py login
python3 uom_login.py ensure-fly
python3 uom_login.py latest-plan
python3 uom_login.py open-browser
```

无副作用探测：

```bash
python3 uom_probe.py
```

半自动流程：

```bash
python3 uom_semiauto.py
```

## 关键文件

- `config.json`
  - 无人机、操控员、联系方式配置
- `manual_selection_log.json`
  - 半自动脚本最近一次运行日志
- `.playwright-uom-profile/`
  - 持久化浏览器 profile，勿随意删除
- `HANDOFF_UOM_PROJECT.md`
  - 面向接手者/其他 AI 的完整移交文档
- `README.md`
  - 项目入口说明
- `SKILL.md`
  - 项目内最新可执行说明

## 后续建议

下一步优先做：
- 单独加强“航空器/操控员内部选中状态调试”逻辑
- 目标不是先点提交，而是先让：
  - `this.$refs.form.validate()` 不再报 `uavs/drivers`
- 只有这一步通过，才值得继续做最终提交流程
