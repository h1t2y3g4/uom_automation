# UOM 自动化项目说明

目录：`/home/skye/hermes_interface/uom-automation`

## 当前项目进度（截至本次会话）

已完成：
- 已确认并保留一份可复用的 Playwright 持久化浏览器 profile：
  - `/home/skye/hermes_interface/uom-automation/.playwright-uom-profile`
- 已验证浏览器登录态可以通过以下信息判断：
  - `window.nros.getToken()`
  - `localStorage.session_token`
  - 页面主站 UI 文本（首页/运行管理等）
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
  - 下周一同时间
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
- 点击“提交申请”按钮可以触发点击事件，但不会生成新计划
- 当前判断：UOM 前端把“页面里显示了一行数据”和“选择器内部已正式选中”分成了两套状态；脚本还没有把这套内部选中状态补齐

本次确认的负结论：
- 不能把“按钮点到了”误判成“提交成功”
- 不能把“页面里显示了 1 架航空器 / 1 个操控员”误判成“前端校验已通过”
- 直接 `POST /oapi/pub/planInfo` 仍然会报：
  - `planInfo is null`
  - 所以不能回退成直接 API 提交方案

## 推荐主线

当前最可靠主线：
1. 复用持久化浏览器 profile
2. 进入一般飞行活动列表
3. 读取最近计划详情
4. 打开新增页
5. 自动填充下周一同时间和上次计划内容
6. 继续研究并补齐“航空器/操控员选择器内部选中状态”
7. 待 `validate()` 真正通过后，再提交

## 主要脚本

保留使用：
- `uom_persistent.py`
  - 当前主脚本
  - 用于持久化 profile、状态检测、进入一般飞行活动、读取历史计划、打开新增页、自动填表
- `uom_semiauto_submit.py`
  - 半自动辅助脚本
  - 当前主要作用是验证“自动填表后前端校验仍卡在 uavs/drivers”
- `open_uom_persistent_browser.py`
  - 打开持久化浏览器，供人工确认页面状态用

保留参考：
- `browser_login.py`
- `browser_login_interactive.py`
- `browser_submit.py`
- `uom_api.py`
- `submit.py`
- `phone_login.py`
- `rsal_encrypt.py`

这些旧脚本可继续作为：
- 登录细节参考
- 历史尝试记录
- 字段结构参考

但当前主线以 `uom_persistent.py` 为准。

## 关键文件

- `config.json`
  - 无人机、操控员、联系方式配置
- `manual_selection_log.json`
  - 半自动脚本最近一次运行日志
- `.playwright-uom-profile/`
  - 持久化浏览器 profile，勿随意删除

## 后续建议

下一步优先做：
- 单独写“航空器/操控员内部选中状态调试脚本”
- 目标不是先点提交，而是先让：
  - `this.$refs.form.validate()` 不再报 `uavs/drivers`
- 只有这一步通过，才值得继续做最终提交流程
