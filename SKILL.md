---
name: uom-automation
description: UOM 自动化项目当前主线：uom_core.py 底层能力 + uom_login.py / uom_semiauto.py / uom_selection_debug.py 分离入口。
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
- 当前主线已打通稳定半自动提交：
- 自动关闭温馨提示
- 自动选择航空器
- 自动选择操控员
- 自动处理提交申请后的二次确认弹窗
- 支持提交失败后按 5 秒间隔自动重试，最多 3 次
- 提交后等待一段时间再读取最近计划列表验证结果

重要结论：
- 当前主线不是旧的 API/RSAL 方案
- 当前主线不是“直接 POST /oapi/pub/planInfo”
- 当前主线已经重构为：
  - `uom_core.py`：只放底层能力
  - `uom_login.py`：专门处理登录/状态
  - `uom_semiauto.py`：只放半自动提交流程（当前已可稳定走通主链路）
  - `uom_selection_debug.py`：只放内部选中状态专项调试
- 当前阻塞点是：页面上已经显示航空器和操控员行，不等于前端校验已经认定它们被正式选中

## 用户期望（重要）

该用户非常在意减少重复登录。
处理这个项目时应默认：
- 优先复用本地 Playwright 持久化 profile
- 只有主站 NROS 登录态和飞行活动 iframe/oapi 认证都失效时，才重新走短信登录
- 不要把 Hermes 临时 browser 会话当长期主通道
- 不要轻易消耗短信验证码

如果 8 分钟内登录未成功，应提醒用户手动在当前页面把验证码用掉，避免因未使用验证码过多触发 1~4 小时限制。

## 当前项目文件（按主线理解）

### 底层能力
- `uom_core.py`
  - 当前底层能力实现
  - 使用固定持久化目录：`~/hermes_interface/uom-automation/.playwright-uom-profile/`
  - 复用 cookie / localStorage / 站点会话
  - 检测两层认证：
    1. 主站 NROS 登录态
    2. 飞行活动 iframe 的 oapi 认证态（`PUB-Token + ticket + userName`）
  - 支持读取最近计划、打开新增页、填充表单、触发提交、专项调试等公共函数

### 登录/状态入口
- `uom_login.py`
  - 专门负责：
    - 状态检查
    - 必要时短信登录
    - 确保进入一般飞行活动
    - 查看最近计划
    - 只打开浏览器供人工检查

### 半自动业务验证入口
- `uom_semiauto.py`
  - 专门负责：
    - 自动进入新增页并填表
    - 支持 CLI UTC 对 / config 时间列表 / 最近计划 +1 天保底 三种时间来源
    - 支持 `--dry-run` 只做时间解析、登录态检查与最近计划检查，不进入新增页、不提交
    - 记录 precheck / postcheck
    - 让用户肉眼确认
    - 再尝试提交并检查最近计划是否变化
  - 它当前已能稳定：自动填表、自动选择航空器/操控员、自动二次确认提交、失败自动重试、提交后读取列表验证

### 内部选中状态专项调试入口
- `uom_selection_debug.py`
  - 专门负责：
    - 自动进入新增页
    - 自动填表
    - 不走最终提交流程
    - 输出 data/ref/dialog/validate 相关线索
    - 聚焦 `uavs / drivers` 为什么仍不被前端认定为已选中

### 已不应再当主线的旧方案
不要再把下面这些当成当前正确路线：
- 旧版 `login.py` / `quick_login.py` / `submit.py`
- 旧版纯 API 提交思路
- RSAL 加密整包登录思路
- 直接手工 POST `/oapi/pub/planInfo` 作为主方案
- 与半自动流程高度重叠的旧 `probe` 脚本思路

## 当前脚本用法

### 登录/状态相关
```bash
python3 uom_login.py status
python3 uom_login.py login
python3 uom_login.py ensure-fly
python3 uom_login.py latest-plan
python3 uom_login.py open-browser
```

### 半自动流程
```bash
python3 uom_semiauto.py
python3 uom_semiauto.py --start-utc-ts 1747908000 --end-utc-ts 1747911600
python3 uom_semiauto.py --use-time-list
python3 uom_semiauto.py --dry-run
python3 uom_semiauto.py --use-time-list --dry-run
```

### 内部选中状态专项调试
```bash
python3 uom_selection_debug.py
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
- 不要只看残留 `session_token`
- 若当前 URL 仍在 `#/login`，应优先视为“未真正进入主站”
- 更稳的判断是：`URL + nros token + session_token + 主站 UI` 四者综合判断

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

`uom_core.py` 的 `open_fly_activity()` 当前已加强：
- 先点 `运行管理`
- 再点 `飞行活动申请`
- 再找最合适的 `一般飞行活动` 项
- 失败时输出页面快照，便于判断是假登录态、菜单结构变更还是异步渲染未完成

## 当前确认无误的读取最近计划方式

进入 iframe 后，可稳定请求：
```javascript
GET /oapi/pub/planInfo/list?pageNum=1&pageSize=5&planTypes=11,12,13
```

读取详情：
```javascript
GET /oapi/pub/planInfo/{planId}
```

这些调用在 `uom_core.py` 的：
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

对应函数：
- `open_new_fly_form(page)`
- `wait_for_fly_add(page)`
- `fill_new_form_from_detail(page, detail, plan_beg_new, plan_end_new)`
- `inspect_add_dialogs(page, detail, plan_beg_new, plan_end_new)`
- `trigger_submit_copied_form(page)`

## 时间生成规则

`uom_semiauto.py` 当前支持 3+1 种时间入口：

1. CLI 直接传一对 UTC 时间戳
   - `--start-utc-ts <秒级UTC时间戳>`
   - `--end-utc-ts <秒级UTC时间戳>`
   - 读取 `config.json` 中 `time.timezone`，转成本地时间后提交单条计划

2. CLI 传 `--use-time-list`
   - 读取 `config.json` 中 `time.pairs`
   - 顺序循环提交
   - 最多 5 条，超过直接报错

3. 无参数保底
   - 读取最近一条计划详情
   - 使用 `planBeg/planEnd + 1 day`

4. `--dry-run`
   - 只做时间解析与打印
   - 会完成登录态、进入业务页、读取最近计划检查
   - 不进入新增页，不触发提交
   - 可与 `--use-time-list` 组合使用

`config.json` 中应配置：

```json
"time": {
  "timezone": "UTC+8",
  "pairs": [
    {
      "start_utc_ts": 1747908000,
      "end_utc_ts": 1747911600
    }
  ]
}
```

注意：
- 这里应填写“时区”，不是编码；不要写成 `UTF+8`
- 当前默认推荐：`UTC+8` 或 `Asia/Shanghai`

在 `uom_core.py` 里相关函数包括：
- `get_timezone_from_config(...)`
- `utc_timestamps_to_local_pair(...)`
- `resolve_time_pairs(...)`
- `describe_time_pair(...)`

## 主要入口的区别

### `uom_login.py`
- 偏基础能力与状态检查
- 用来确认登录态、业务页进入能力、最近计划读取是否正常

### `uom_semiauto.py`
- 半自动业务验证
- 自动打开新增页并填表
- 记录 precheck / postcheck
- 让用户肉眼确认
- 再尝试提交并检查最近计划是否变化

### `uom_selection_debug.py`
- 专项调试
- 不提交
- 用来观察新增页弹层、选择器、data/ref/validate 线索
- 比旧 probe 更贴近当前真正阻塞点

## 当前能力边界

当前最重要的状态变化：
- 不再把 `uavs / drivers` 前端内部选中状态作为主阻塞点；该链路已打通
- `uom_semiauto.py` 当前可稳定完成从新增页打开到最终提交的主链路
- 仍需警惕 UOM 服务器偶发失败，因此提交阶段保留失败检测与最多 3 次自动重试
- 页面反馈不一定直观，是否成功应优先看提交后最近计划列表与返回日志

## 建议动作

当任务涉及这个项目时，先做下面这件事：
1. 读取 `~/hermes_interface/uom-automation/SKILL.md`
2. 再看 `~/hermes_interface/uom-automation/README.md`
3. 详细交接说明统一看 `~/hermes_interface/uom-automation/HANDOFF_UOM_PROJECT.md`
4. 根据任务性质选择：
   - 登录/状态：`uom_login.py`
   - 半自动流程：`uom_semiauto.py`
   - 内部选中状态专项调试：`uom_selection_debug.py`

必要时再看：
- `uom_core.py`
- `HANDOFF_UOM_PROJECT.md`
- `references/login-state-false-positive-and-menu-not-found-2026-05-18.md`

## 常见误区

1. 不要继续依赖过时的全局说明做细节决策。
2. 不要把历史会话里已经被推翻的旧登录/API 方案当主线。
3. 不要在 `uom_semiauto.py` / `uom_selection_debug.py` 里复制底层能力实现；公共逻辑应回到 `uom_core.py`。
4. 不要跳过项目内 `SKILL.md`，尤其是在提交、登录复用、时间生成规则这些地方。

## 结论

当前 UOM 自动化项目的详细、最新、可执行说明，以：
`~/hermes_interface/uom-automation/SKILL.md`
为第一优先来源。

完整交接说明以：
`~/hermes_interface/uom-automation/HANDOFF_UOM_PROJECT.md`
为准。

公共底层实现以：
`~/hermes_interface/uom-automation/uom_core.py`
为准。
