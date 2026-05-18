---
name: uom-automation
description: UOM 自动化项目当前主线：持久化 Playwright 浏览器 + 一般飞行活动新增页逐项填充/调试。
tags: [uom, drone, automation, caac, 无人机, 飞行申请]
---

# UOM 自动化项目当前主线

位置：`~/hermes_interface/uom-automation/`

目的：
- 优先复用持久化浏览器登录态，尽量避免重复短信登录
- 自动进入 `运行管理 -> 飞行活动申请 -> 一般飞行活动`
- 读取最近计划
- 打开真正的新增页 `flyIndexAdd`
- 把上一次计划内容按更接近人工的方式灌入新增页
- 当前重点仍是攻克 `uavs / drivers` 前端内部“已选中状态”问题，而不是宣称已稳定一键提交

重要结论：
- 当前主线不是旧的 API/RSAL 方案
- 当前主线不是“直接 POST /oapi/pub/planInfo”
- 当前主线是 `uom_persistent.py` + `.playwright-uom-profile` 持久化浏览器
- 当前阻塞点是：页面上已经显示航空器和操控员行，不等于前端校验已经认定它们被正式选中

## 用户期望（重要）

该用户非常在意减少重复登录。
处理这个项目时应默认：
- 优先复用本地 Playwright 持久化 profile
- 只有主站登录态和 iframe/oapi 认证都失效时，才重新走短信登录
- 不要把 Hermes 临时 browser 会话当长期主通道
- 不要轻易消耗短信验证码

如果 8 分钟内登录未成功，应提醒用户手动在当前页面把验证码用掉，避免因未使用验证码过多触发 1~4 小时限制。

## 当前项目文件（按主线理解）

### 主线脚本
- `uom_persistent.py`
  - 当前总控主线脚本
  - 使用固定持久化目录：`~/hermes_interface/uom-automation/.playwright-uom-profile/`
  - 复用 cookie / localStorage / 站点会话
  - 检测两层认证：
    1. 主站 NROS 登录态
    2. 飞行活动 iframe 的 oapi 认证态（`PUB-Token + ticket + userName`）
  - 支持读取最近计划、打开新增页、填充表单、触发提交、无副作用探测等底层函数

- `uom_semiauto_submit.py`
  - 当前主线调试脚本之一
  - 自动进入一般飞行活动 -> 打开新增页 -> 填入最近计划内容和目标时间 -> 记录 precheck/postcheck
  - 重点用于定位为什么前端 `validate()` 仍然报 `uavs/drivers`
  - 不是已经稳定可一键成功提交的正式脚本

- `uom_no_submit_probe.py`
  - 无副作用探测脚本
  - 自动进入新增页，但不执行最终提交
  - 用于观察航空器/操控员添加弹层、表格、组件结构和选择器状态

- `open_uom_persistent_browser.py`
  - 用相同持久化 profile 打开浏览器，供人工检查当前登录态、菜单状态、业务页状态

### 已不应再当主线的旧方案
不要再把下面这些当成当前正确路线：
- 旧版 `login.py` / `quick_login.py` / `submit.py`
- 旧版纯 API 提交思路
- RSAL 加密整包登录思路
- 直接手工 POST `/oapi/pub/planInfo` 作为主方案

## 当前主线脚本用法

### 1. 检查持久化登录态和飞行活动认证
```bash
python3 uom_persistent.py --status
```

它会：
- 打开 `/#/main`
- 检查主站登录态
- 若主站看起来已登录，再尝试进入一般飞行活动并检查 iframe/oapi 认证

### 2. 必要时重新登录
```bash
python3 uom_persistent.py --login
```

用途：
- 在当前持久化 profile 中完成短信登录
- 尽量把登录态留下来供后续复用

### 3. 只确保能进入“一般飞行活动”
```bash
python3 uom_persistent.py --ensure-fly
```

### 4. 查看最近计划
```bash
python3 uom_persistent.py --latest-plan
```

### 5. 半自动提交调试
```bash
python3 uom_semiauto_submit.py
```

流程：
- 打开持久化浏览器
- 进入一般飞行活动
- 读取最近计划
- 打开真正新增页 `flyIndexAdd`
- 把最近计划内容灌进去
- 记录前后校验快照
- 让用户肉眼确认
- 再尝试触发提交并检查最近计划是否变化

### 6. 无副作用结构探测
```bash
python3 uom_no_submit_probe.py
```

### 7. 仅打开持久化浏览器供人工确认
```bash
python3 open_uom_persistent_browser.py
```

## 当前确认无误的认证模型

UOM 有两套认证，必须严格区分。

### A. 主站 NROS 认证：`/api/*`
来源：
- `window.nros.getToken()`
- `localStorage.session_token`
- 再辅以页面 UI 是否已经进入主站作为兜底判断

注意：
- 不要只看 `window.nros.getToken()`
- 肉眼已经登录时，`nros.getToken()` 也可能暂时是 `null`
- 更稳的判断是：`nros token` / `session_token` / 主站 UI 三者综合判断

### B. 飞行计划 oapi 认证：`/oapi/*`
这套与主站 token 完全不同。

必须从“飞行活动申请”业务 iframe 中提取：
- `PUB-Token`（来自 iframe cookie）
- `ticket`（来自 iframe URL）
- `userName`（来自 iframe URL）

不能使用：
- 父页面 cookie 里的 `PUB-Token=undefined`
- 主站 `window.nros.getToken()` 去请求 `/oapi/*`

### 正确请求头
```javascript
{
  'Authorization': 'Bearer ' + pubToken,
  'pubUserName': userName,
  'ticket': ticket,
  'deviceType': 'PC'
}
```

注意：
- 请求头大小写敏感
- 必须用 `Authorization`、`pubUserName`、`ticket`、`deviceType`

## 当前确认无误的页面进入路径

标准路径：
1. 顶部点击：`运行管理`
2. 左侧展开：`飞行活动申请`
3. 子项点击：`一般飞行活动`
4. 等业务 iframe 真正出现

注意不要误判：
- 只看到左侧菜单文字，不代表业务页已真正打开
- 必须额外确认至少一项：
  - iframe 已出现
  - 或 Vue 树里出现 `FLY_INDEX / FLY_INDEX_ADD`
  - 或页面已有飞行申请业务正文

`uom_persistent.py` 的 `open_fly_activity()` 当前就是按这个稳定顺序点击：
- 先点 `运行管理`
- 再点 `飞行活动申请`
- 再只点击 class 含 `ivu-menu-item` / `menuitem` 的 `一般飞行活动`

## 当前确认无误的读取最近计划方式

进入 iframe 后，可稳定请求：
```javascript
GET /oapi/pub/planInfo/list?pageNum=1&pageSize=5&planTypes=11,12,13
```

读取详情：
```javascript
GET /oapi/pub/planInfo/{planId}
```

这些调用在 `uom_persistent.py` 的：
- `get_latest_plan(page)`
- `get_plan_detail(page, plan_id)`

已验证可用。

## 当前主线不是“复制按钮”，而是“新增页手动新建 + 复用上次计划内容”

当前脚本主线更接近：
1. 先读最近计划详情
2. 在列表页找到 `FLY_INDEX` 组件并直接调用 `addFly()`
3. 打开真正的新增页 `flyIndexAdd`
4. 把上次计划内容手动灌入新增页表单
5. 当前重点是让航空器/操控员选择器的内部选中状态被前端真正承认

这条链路对应：
- `open_new_fly_form(page)` -> 直接调 `FLY_INDEX.addFly()`
- `wait_for_fly_add(page)` -> 不只看 URL，还要等 `FLY_INDEX_ADD` 或业务正文真正渲染
- `fill_new_form_from_detail(page, detail, plan_beg_new, plan_end_new)` -> 往新增页主表单灌值

## 新增页当前已确认的重要结构

新增页主组件：
- 优先找组件名 `FLY_INDEX_ADD`
- 兜底特征：`vm.$data.form` 且存在 `uavInfoList / driverInfoList`

关键数据在：
- `comp.$data.form`

关键字段：
- `form.planBeg`
- `form.planEnd`
- `form.planBegStr`
- `form.planEndStr`
- `form.taskType`
- `form.planType`
- `form.txll`
- `form.spcTop`
- `form.spaces`
- `form.uavs`
- `form.drivers`

关键组件数据：
- `comp.$data.uavInfoList`
- `comp.$data.driverInfoList`
- `comp.$data.spaceList`
- `comp.$data.oldSpaceList`

涉及的组件方法：
- `addFly()`
- `callbackAddSpace()`
- `callbackAddUavs()`
- `callbackAddDrivers()`
- `handleSelectionDriverUavs()`
- `submitPlan()`

## 时间生成规则

项目里当前明确有两个规则：
- 通用函数：按目标星期几复用上次同时间
- 当前主线默认：星期二同时间

在 `uom_persistent.py` 里：
- `get_next_weekday_same_time(...)` 是通用函数
- `get_next_tuesday_same_time(...)` 是当前半自动主线实际在用的规则

原因：
- 用户已明确纠正：当刚提交了周一计划时，下一次目标时间应改为星期二同时间，不要继续生成周一

因此当前项目文档里应以“星期二同时间”为默认，而不是旧文档里的“下周一同时间”。

## 当前最大阻塞点：uavs / drivers 只是“显示出来”还不够

这是当前主线最关键的事实。

已验证：
- 即使 `form.uavs / form.drivers` 里有数据
- 即使页面表格已经显示出 1 架航空器和 1 个操控员
- `this.$refs.form.validate()` 仍可能返回：
  - `valid: false`
  - `fields: ['uavs', 'drivers']`

这说明：
- UOM 新增页把“表格里显示出的行数据”和“选择器内部正式选中状态”分开维护
- 仅仅向 `form.uavs / form.drivers`、`uavInfoList / driverInfoList` 灌值，不足以通过前端校验

因此不要再误判：
- “页面有行数据” = 可提交
- “点击到了提交申请按钮” = 提交成功

真正的成功判据至少满足其一：
1. `this.$refs.form.validate()` 返回 `valid: true`
2. 返回列表后，重新查询最近计划，出现新的目标时间记录或新的 `planId`

## 当前推荐的调试/执行思路

不要再优先走“一次性 bulk 注入整张表”。
用户已明确纠正，应尽量改成更像人工的逐项流程：
1. 先关掉 `我知道了`
2. 再分别处理时间、空域、航空器、操控员
3. 能点各区块自己的 `添加` 就优先点 `添加`
4. 每一步后都验证组件状态变化
5. 重点研究 Element/表格 selection 的真实勾选状态，而不是只改隐藏 input 或只改 form 数据

当前脚本里也已体现这个方向：
- `uom_semiauto_submit.py` 会记录 `precheck` / `postcheck`
- `uom_no_submit_probe.py` 会打开添加弹层做无副作用观察
- `fill_new_form_from_detail()` 会先尝试更接近人工的步骤，再灌基础数据

## 直接 POST `/oapi/pub/planInfo` 仍然不要回退为主方案

此前实测仍会报：
```json
{
  "code": 500,
  "msg": "保存飞行活动申请失败!",
  "data": "Cannot invoke PlanInfo.getPlanType() because planInfo is null"
}
```

结论：
- 即使拿到了 iframe 的正确 `PUB-Token`
- 即使请求头大小写也写对了
- 直接手工 POST `/oapi/pub/planInfo` 仍不可靠

所以当前不要再把它写成推荐主路线。

## 登录相关补充

### 图形验证码
推荐直接从登录 Vue 组件读取：
- `lm.yzmImageCode`
- `lm.txYzmUuid`

流程：
1. 从 Vue data 取 base64 验证码
2. 保存到 `/tmp/uom_persistent_captcha.png`
3. 用 `ddddocr` 识别
4. 用户人工确认

### 发短信验证码
不要调用：
- `lm.sendSMS()`

正确做法：
- 在浏览器上下文中 `fetch('/api/home/anon/sendSmsCode', ...)`

### 登录提交
- `lm.handleSubmit()` 第一次可能报错
- 可重试一次

### 关于旧 RSAL 方案
不要再写成当前正确结论。当前项目主线结论是：
- 旧的 RSAL 整包加密思路不是当前主线
- 当前以浏览器真实前端流程为准
- 与其维护旧 API 登录文档，不如优先维护持久化浏览器工作流

## 当前建议执行顺序

### A. 先查状态
1. `python3 uom_persistent.py --status`
2. 若主站和 iframe/oapi 都正常，继续
3. 若主站失效，再考虑 `--login`

### B. 进入业务页
1. 打开 `运行管理 -> 飞行活动申请 -> 一般飞行活动`
2. 确认真正出现 iframe / `FLY_INDEX`

### C. 读取最近计划
1. 请求列表
2. 取最近 `planId`
3. 请求详情
4. 读取空域、时间、无人机、操控员

### D. 打开新增页
1. 不优先点文本“新增”按钮
2. 优先直接调 `FLY_INDEX.addFly()`
3. 等 `FLY_INDEX_ADD` 真正渲染完成

### E. 填表与校验
1. 时间改为星期二同时间
2. 写入空域、无人机、操控员基础数据
3. 重点验证前端内部已选中状态
4. 看 `validate()` 是否仍报 `uavs / drivers`

### F. 判断是否真的成功
不要只看“按钮已点击”。
至少再做一个验证：
- `validate()` 已通过，或
- 列表里出现新记录

## 当前必须避免的坑

- 不要把用户人工浏览器已登录，当成自动化浏览器也已登录
- 不要只看 `window.nros.getToken()` 就认定主站未登录
- 不要只看到菜单里有“一般飞行活动”就认定业务页已加载
- 不要只看到 iframe URL 里变成 `flyIndexAdd` 就认定新增页已真正渲染
- 不要只改 DOM，不改 Vue data
- 不要只改 `planBeg / planEnd`，遗漏 `planBegStr / planEndStr`
- 不要把页面上显示出航空器/操控员行，误判成前端已通过校验
- 不要把“点击到提交申请按钮”误判成“提交成功”
- 不要回退成直接手搓 `/oapi/pub/planInfo` 当主方案
- 不要不加控制地反复消耗短信验证码

## 适合以后继续补充的方向

当前最值得继续沉淀的是：
- 如何让航空器选择弹层里的 Element/表格 selection 真正勾上
- 如何让操控员选择弹层里的选择状态被前端正式承认
- 哪个组件字段 / 回调 / 事件链才是 `validate()` 放行的关键

在这些点没有打通前，不要把项目描述成“已经稳定自动提交成功”。
