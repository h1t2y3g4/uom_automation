# UOM 自动提交飞行计划项目交接说明

项目目录：`/home/skye/hermes_interface/uom-automation`

## 1. 项目目标

这个项目的目标，是为 UOM（中国民航局无人驾驶航空器综合管理平台，`uom.caac.gov.cn`）实现一套“尽量少重复登录、尽量贴近人工操作”的飞行计划自动提交流程。

核心诉求：
1. 优先复用登录态，尽量避免反复短信登录
2. 能读取最近一次飞行计划
3. 以最近一次计划为模板，生成下一次计划
4. 最终实现自动提交
5. 优先追求真实可用，而不是理论上优雅但不稳定的纯 API 方案

这个项目的难点不在于“填几个表单”，而在于：
1. UOM 登录系统和飞行计划业务系统是两套认证体系
2. 飞行计划业务页运行在 iframe 中
3. 新增页是 Vue + 复杂选择器状态，不是简单改 DOM 或改 form 数据就能提交
4. 页面上“看起来已经有无人机/操控员”并不等于前端内部真的认为“已选中”

## 2. 当前状态总览

一句话总结：

登录、进入业务页、读取历史计划、打开新增页、自动填入大部分内容，这些都已经打通；
真正还没完全攻克的最后阻塞点，是新增页里“航空器/操控员的内部选中状态”，导致前端 `validate()` 仍报 `uavs/drivers`，最终不能确认真正提交成功。

已经打通：
1. 持久化浏览器登录态复用
2. UOM 主站登录状态检查
3. 进入 `运行管理 -> 飞行活动申请 -> 一般飞行活动`
4. 从 iframe 提取 oapi 所需认证
5. 查询最近计划列表和详情
6. 打开 `flyIndexAdd` 新增页
7. 自动把上一次计划的核心内容灌入新增页
8. 页面上已经能显示航空器和操控员

还没彻底打通：
1. 新增页前端校验通过
2. 让前端真正承认“航空器已选、操控员已选”
3. 确认提交后确实生成新 `planId` / 新记录

## 3. 当前推荐技术路线

当前主线不是“纯 API 提交”，而是“持久化浏览器 + 前端真实流程”。

推荐主线：
1. 使用 Playwright 持久化 profile
2. 复用已登录状态
3. 打开“一般飞行活动”业务页
4. 从 iframe 读取最近计划
5. 打开新增页
6. 自动填入上次计划内容 + 目标新时间
7. 继续攻克航空器/操控员真正选中状态
8. 只有当前端 `validate()` 通过后，才做最终提交

为什么不走纯 API 主线：
1. 直接 `POST /oapi/pub/planInfo` 目前仍返回 500
2. 服务端报错为：`Cannot invoke PlanInfo.getPlanType() because planInfo is null`
3. 即使把前端 form 序列化出来直接发，也没有跑通
4. 所以“直接构造请求体提交”目前不是可靠方案

## 4. 最重要的技术结论

### 4.1 UOM 有两套认证，不要混用

A. 主站认证：
用于 `/api/*` 接口
来源：
- `window.nros.getToken()`
- `localStorage.session_token`

B. 飞行计划业务认证：
用于 `/oapi/*` 接口
来源不是主站 token，而是“一般飞行活动” iframe 内部的：
- `PUB-Token`
- iframe URL 里的 `ticket`
- `userName`

关键结论：
主站 token 和 oapi token 不是一回事。
不能拿主站 token 直接调 `/oapi/*`。

### 4.2 oapi 认证必须从业务 iframe 里取

已验证：
- 父页面 cookie 里经常会出现 `PUB-Token=undefined`
- 真正可用的 `PUB-Token` 要从 `iframe.contentDocument.cookie` 里提取
- `ticket` 要从 `iframe.src` 里拿
- `userName` 也要从 `iframe.src` 参数里拿

正确请求头：

```text
Authorization: Bearer {PUB-Token}
pubUserName: {userName}
ticket: {ticket}
deviceType: PC
```

请求头大小写敏感，尤其：
- `pubUserName`
- `deviceType`

### 4.3 登录应优先使用浏览器真实流程，不再以 RSAL 为主线

老的思路曾经认为登录需要 RSAL 加密，但后来实际验证后，已确认：
- `/api/home/anon/login` 发的是明文 JSON
- 不是整个 payload 做 RSAL
- 只有 `telephone` 字段使用双重 base64（`d_base64`）

所以当前结论：
- `rsal_encrypt.py`、`phone_login.py` 等旧脚本只保留参考价值
- 登录主线应以浏览器脚本和持久化 profile 为准

### 4.4 sendSMS() 不可靠，必须直接 fetch

登录页里 Vue 组件自带的 `sendSMS()` 经常走 NROS 客户端，返回：
`401 TokenUndefined`

正确方式是：
直接在页面上下文中 `fetch`：
`POST /api/home/anon/sendSmsCode`

请求体：

```json
{
  "mobileNum": "手机号",
  "icode": "图形验证码",
  "uuid": "验证码uuid",
  "scene": "1"
}
```

### 4.5 新增页最大阻塞点不是“没填值”，而是“没选中”

这是整个项目当前最关键的问题。

已确认现象：
1. 新增页中 `form.uavs / form.drivers` 可以有值
2. 页面表格里也会显示 1 架航空器、1 个操控员
3. 但 `this.$refs.form.validate()` 仍然返回失败
4. `fields` 仍然报：
   - `uavs`
   - `drivers`

因此不能再误判：
- “页面显示出行数据” != “前端内部已选中”
- “点到了提交申请按钮” != “提交成功”

高概率真实原因：
UOM 前端把以下两件事分开维护：
1. 主表显示的数据行
2. 选择器/Element Table 内部 selection 状态

当前脚本已经能把显示层补出来，但还没把“真正选中状态”补齐。

## 5. 当前项目结构

### 5.1 主线文件

#### `uom_persistent.py`
当前核心脚本，主线在这里。

作用：
- 使用固定 Playwright profile
- 检测主站登录状态
- 检测业务 iframe 认证状态
- 进入一般飞行活动
- 查询最近计划
- 打开新增页
- 自动填充计划内容
- 尝试走提交流程

### 5.2 半自动调试脚本

#### `uom_semiauto_submit.py`

作用：
- 自动进入新增页
- 自动填入数据
- 停下来让人肉确认
- 记录 precheck/postcheck 调试信息
- 用于定位为什么 `validate()` 仍报 `uavs/drivers`

这个脚本的价值很大，因为它证明了：
即使页面已经显示无人机和操控员，前端校验仍可能失败。

### 5.3 辅助打开浏览器

#### `open_uom_persistent_browser.py`

作用：
- 用同一个持久化 profile 打开浏览器
- 方便人工检查当前页面和登录态
- 适合配合调试使用

### 5.4 历史方案 / 参考脚本

这些文件现在已经移入 `archive/`，不再作为项目根目录下的主线文件，但仍保留参考价值：
- `archive/browser_login.py`
- `archive/browser_login_interactive.py`
- `archive/browser_submit.py`
- `archive/phone_login.py`
- `archive/rsal_encrypt.py`
- `archive/submit.py`
- `archive/uom_api.py`

说明：
- 这些文件主要代表早期的浏览器登录实验、RSAL 登录思路、直接 API 提交思路。
- 当前开发时不要优先改它们；如需查历史字段、旧思路、旧坑点，再去翻 archive。

### 5.5 配置文件

#### `config.json`
保存内容包括：
- 无人机信息
- 操控员信息
- 联系方式
- 部分提交所需字段

### 5.6 持久化浏览器目录

`/home/skye/hermes_interface/uom-automation/.playwright-uom-profile`

非常重要。
这是当前复用登录态的核心资产，不要随意删除。

### 5.7 项目说明文件

- `README.md`
- `PROJECT_STATUS.md`
- `archive/PROGRESS.md`

注意：
- `README.md` 已更新为当前主线说明。
- `archive/PROGRESS.md` 是旧的 RSAL 阶段记录，保留仅供历史参考。
- 当前应以：
  - `uom_persistent.py`
  - `PROJECT_STATUS.md`
  - `HANDOFF_UOM_PROJECT.md`
  - 最新调试结论
  为准。

## 6. 当前可用使用方法

以下命令均在项目目录下执行：

```bash
cd /home/skye/hermes_interface/uom-automation
```

### 6.1 查看当前状态

```bash
python3 uom_persistent.py --status
```

目标：
- 检查主站是否已登录
- 检查本地持久化 profile 是否还能复用

判断登录态不能只看 `window.nros.getToken()`。
更稳的判断顺序应该是：
1. `window.nros.getToken()`
2. `localStorage.session_token`
3. 页面 UI 是否已进入主站（如“首页”“运行管理”等）

### 6.2 如有需要，重新登录

```bash
python3 uom_persistent.py --login
```

登录流程特点：
- 浏览器真实打开 UOM
- 读取 Vue 组件中的图形验证码 base64
- 保存验证码图片到 `/tmp/uom_persistent_captcha.png`
- 可用 `ddddocr` 识别，也允许人工输入
- 发短信后，由用户手动输入短信验证码
- 再由脚本触发登录

注意：
短信码 10 分钟有效。
如果 8 分钟左右还没登录成功，应提醒用户手动把验证码用掉，避免频繁浪费导致 1~4 小时限制。

### 6.3 进入一般飞行活动页

```bash
python3 uom_persistent.py --ensure-fly
```

用途：
- 自动打开主站
- 进入：`运行管理 -> 飞行活动申请 -> 一般飞行活动`
- 确保 iframe 业务页真正打开

注意：
不能只看到左侧菜单文字“ 一般飞行活动 ”就当成功。
必须额外确认至少一项：
- iframe 已出现
- Vue 树已出现 `FLY_INDEX` 或 `FLY_INDEX_ADD`
- 页面已不是单纯宿主壳层

### 6.4 读取最近计划

```bash
python3 uom_persistent.py --latest-plan
```

用途：
- 读取最近一条计划
- 用于确认最近计划详情、后续复制或新建时的源数据

### 6.5 半自动调试提交流程

```bash
python3 uom_semiauto_submit.py
```

这个脚本当前很适合给其他 AI 工具接手时使用，因为它能：
1. 自动打开新增页
2. 自动填入数据
3. 输出前端校验结果
4. 保存调试日志到：
   `/home/skye/hermes_interface/uom-automation/manual_selection_log.json`

这个日志对定位“为什么前端仍不承认 `uavs/drivers` 已选中”非常关键。

## 7. 当前已验证的业务流程

### 7.1 登录流程：已验证可行

已确认：
- 可以在浏览器里真实完成手机号登录
- 图形验证码可以从 Vue data 直接提取
- 发送短信接口可直接 `fetch`
- 用户手动输入短信验证码后可以登录成功

登录页关键组件特征：
递归查找满足：

```javascript
vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm
```

关键字段：
- `lm.userForm.telephone`
- `lm.userForm.tcode`
- `lm.userForm.dcode`
- `lm.txYzmUuid`
- `lm.yzmImageCode`

图形验证码推荐方式：
- 从 `lm.yzmImageCode` 读 base64
- 存成本地图片
- OCR 或人工识别

### 7.2 读取历史计划：已验证可行

已验证可用接口：
- `GET /oapi/pub/planInfo/list?pageNum=1&pageSize=5&planTypes=11,12,13`
- `GET /oapi/pub/planInfo/{planId}`

可拿到的数据包括：
- `planBeg / planEnd`
- `spaces`
- `locationWgs84`
- `uavs`
- `drivers`
- `taskType`
- `planType`
- `txll`
- `spcTop`

### 7.3 打开新增页：已验证可行

推荐方式不是点表面 DOM“新增”按钮，而是：
在 Vue 组件树中找到列表页主组件 `FLY_INDEX`，然后调用：

```javascript
addFly()
```

理由：
这比按文本找“新增”按钮更稳。

### 7.4 自动填表：大部分已验证可行

当前已能自动灌入：
- `form.planBeg`
- `form.planEnd`
- `form.planBegStr`
- `form.planEndStr`
- `form.spaces`
- `form.uavs`
- `form.drivers`
- `spcTop`
- `txll`
- `taskType`
- `planType`

时间修改必须同步改：
- `planBeg`
- `planEnd`
- `planBegStr`
- `planEndStr`

不能只改前两个。

## 8. 当前真实阻塞点

当前项目不是“完全不能用”，而是卡在最后一步的前端选择器状态。

已知阻塞点：
1. 新增页显示层已有航空器/操控员
2. 但 `validate()` 仍报：
   - `uavs`
   - `drivers`
3. 说明脚本还没真正触发内部选择状态

因此接手的 AI 工具，下一步不应再把精力主要放在：
- 直接手搓 `POST /oapi/pub/planInfo`
- 盲目重复点提交按钮
- 只改 form 数据

而应重点攻克：
“Element 表格 / 选择器 / 组件回调链中的真正选中动作”

推荐突破方向：
1. 先关闭“我知道了”弹窗
2. 分别点击航空器区块的“添加”
3. 进入“选择我的航空器”表格
4. 尝试：
   - 点整行
   - 点 checkbox label
   - 调用组件 selection API
   - 触发 `selection-change` / `toggleRowSelection` 等内部机制
5. 操控员区块同理
6. 每一步后立即验证：
   `this.$refs.form.validate()`
   看 `uavs/drivers` 是否从失败字段中消失

核心原则：
目标不是“按钮点到了”，而是“`validate()` 通过了”。

## 9. 时间生成规则

项目中有一个很重要的业务约定：

默认是基于最近计划，生成“下一个工作目标时间”。

之前默认常用逻辑是：
下周一同时间

但后续已明确补充：
如果用户刚提交了周一计划，那么下一次目标时间应改为：
星期二同时间

当前在半自动脚本里，已经体现出“下一个目标时间可以改为周二同时间”的思路。

所以接手时要注意：
不要死写“永远下周一”，要结合当前最近一次计划和业务上下文判断。

## 10. 对接手 AI 最重要的注意事项

1. 不要再把 RSAL 当主线
   RSAL 阶段是旧路线，现在只保留参考。

2. 不要用主站 token 去请求 `/oapi/*`
   必须从 iframe 取 `PUB-Token + ticket + userName`。

3. 不要看到页面显示数据就以为前端已承认
   显示有行 != 选择器已选中。

4. 不要把“点击提交申请按钮成功”当作提交成功
   真正成功标准至少满足其一：
   - `validate()` 返回 `valid=true`
   - 列表重新查询出现新的 `planId` 或目标时间记录

5. 不要只依赖临时浏览器会话
   优先使用持久化 Playwright profile。

6. 不要浪费短信验证码
   验证码 10 分钟有效，反复发可能导致封禁。

7. 不要只看 iframe URL 已变成 `flyIndexAdd` 就继续
   还要确认：
   - `FLY_INDEX_ADD` 组件已经渲染
   - 页面正文已经出现申请表单
   - 提交申请按钮和时间字段已出现

8. 不要只改 DOM
   UOM 是 Vue 页面，很多地方必须改 Vue data 或触发 Vue/Element 内部逻辑。

## 11. 建议给接手 AI 的任务拆解

任务目标：
在现有项目 `/home/skye/hermes_interface/uom-automation` 基础上，继续攻克 UOM 新增页中“航空器/操控员真正选中状态”的问题，使 `this.$refs.form.validate()` 不再报 `uavs/drivers`，并在此基础上验证是否能成功提交新计划。

建议执行顺序：
1. 先阅读：
   - `uom_persistent.py`
   - `uom_semiauto_submit.py`
   - `PROJECT_STATUS.md`
2. 理解两套认证：
   - `/api` 主站认证
   - `/oapi` iframe 认证
3. 使用持久化 profile，不要重建临时无状态流程
4. 先运行半自动脚本，观察 `manual_selection_log.json`
5. 重点研究新增页中：
   - `FLY_INDEX_ADD` 组件
   - 航空器添加弹层
   - 操控员添加弹层
   - Element Table 的 selection 状态
6. 每次实验都以 `validate()` 是否通过作为判断标准
7. 通过后再验证提交是否真的生成新记录

## 12. 当前成熟度评估

可以这样理解当前成熟度：

A. 登录复用能力：较成熟
B. 读取计划能力：成熟
C. 打开新增页和自动灌表：基本成熟
D. 最终提交：未完成
E. 最大风险点：前端内部选择状态无法仅靠填 form 伪造

所以这个项目现在最像：
“已经打到最后一道门锁，钥匙孔也找到了，但还没完全转开。”

## 13. 极简交接摘要

这是一个 UOM 飞行计划自动提交流程项目，目录在 `/home/skye/hermes_interface/uom-automation`。当前主线不是纯 API，而是 Playwright 持久化浏览器方案，核心脚本是 `uom_persistent.py`。已打通：复用登录态、进入“运行管理 -> 飞行活动申请 -> 一般飞行活动”、从 iframe 获取 `PUB-Token/ticket/userName`、查询最近计划、打开 `flyIndexAdd` 新增页、自动灌入上次计划内容和新时间。当前唯一核心阻塞点是：新增页里即使 `form.uavs/form.drivers` 和页面显示层都有数据，`this.$refs.form.validate()` 仍报 `uavs/drivers`，说明 UOM 前端把“显示数据”和“选择器内部已选中状态”分开维护。不要再主攻直接 `POST /oapi/pub/planInfo`，它仍报 `planInfo is null`。接手时应重点研究 `FLY_INDEX_ADD` 中航空器/操控员添加弹层、Element Table selection API、selection-change 回调链，目标是先让 `validate()` 通过，再验证是否真正提交成功。
