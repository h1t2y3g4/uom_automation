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

### 4.3 登录应优先使用浏览器真实流程

已确认：
- `/api/home/anon/login` 发的是明文 JSON
- `telephone` 字段使用双重 base64（`d_base64`）
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

### 5.4 配置文件

#### `config.json`
保存内容包括：
- 无人机信息
- 操控员信息
- 联系方式
- 部分提交所需字段

### 5.5 持久化浏览器目录

`/home/skye/hermes_interface/uom-automation/.playwright-uom-profile`

非常重要。
这是当前复用登录态的核心资产，不要随意删除。

### 5.6 项目说明文件

- `README.md`
- `PROJECT_STATUS.md`

当前应以：
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

### 6.2 复用现有 profile 打开浏览器

```bash
python3 open_uom_persistent_browser.py
```

### 6.3 进入业务页 / 拉起新增流程

```bash
python3 uom_persistent.py --ensure-fly
```

### 6.4 运行半自动调试

```bash
python3 uom_semiauto_submit.py
```

### 6.5 运行无副作用探测

```bash
python3 uom_no_submit_probe.py
```

## 7. 后续开发优先级

最优先目标不是继续暴力点“提交申请”，而是先把“航空器/操控员的内部选中状态”真正补齐。

建议优先顺序：
1. 先稳定复现 `validate()` 报 `uavs/drivers`
2. 单独研究两个“添加”弹层里的表格 selection 机制
3. 找到真正驱动 `form` 校验通过的组件状态或回调
4. 让 `this.$refs.form.validate()` 先通过
5. 再做最终提交流程验证

## 8. 对接手 AI / 开发者最重要的注意事项

1. 不要用主站 token 去请求 `/oapi/*`
2. 不要把“页面出现行数据”当成“前端已选中”
3. 不要把“点击了提交按钮”当成“已提交成功”
4. 不要随意删除 `.playwright-uom-profile/`
5. 真正主线在 `uom_persistent.py`，不是别处

## 9. 建议接手顺序

1. 先阅读：
   - `uom_persistent.py`
   - `uom_semiauto_submit.py`
   - `PROJECT_STATUS.md`
2. 再理解两套认证：
   - 主站 `/api/*`
   - 业务 `/oapi/*`
3. 再开始调试新增页里航空器/操控员选择器的内部状态
