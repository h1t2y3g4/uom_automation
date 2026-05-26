# UOM 自动提交飞行计划项目交接说明

路径变量约定：
- `PROJECT_ROOT=/home/skye/hermes_interface/uom-automation`
- `PROFILE_DIR=${PROJECT_ROOT}/.playwright-uom-profile`
- 下文再次提到文件时，默认使用相对路径，或写作 ``${PROJECT_ROOT}/相对路径``

这份文档是当前项目的完整交接说明。
如果只看一份文档，就优先看这份。

它的目标是让接手者能快速理解：
- 这个项目到底要解决什么问题
- 当前已经打通到哪一步
- 为什么不能简单走纯 API
- 现在的代码结构是什么
- 每个脚本该怎么用
- 当前最关键的历史坑点和结论是什么
- 后续继续调试时应如何处理临时调试脚本

--------------------------------------------------
## 1. 项目目标
--------------------------------------------------

这个项目的目标，是为 UOM（中国民航局无人驾驶航空器综合管理平台，`uom.caac.gov.cn`）实现一套“尽量少重复登录、尽量贴近人工操作、最终可稳定自动提交”的飞行计划提交流程。

核心诉求：
1. 优先复用登录态，尽量避免反复短信登录
2. 能读取最近一次飞行计划
3. 以最近一次计划为模板，生成下一次计划
4. 最终实现自动提交
5. 优先追求真实可用，而不是理论上优雅但不稳定的纯 API 方案

这个项目的难点不在于“填几个表单”，而在于：
1. UOM 登录系统和飞行计划业务系统是两套认证体系
2. 飞行计划业务页运行在 iframe 中
3. 新增页是 Vue + 复杂选择器状态，不是简单改 DOM 或改 form 数据就能稳定提交
4. 页面上“看起来已经有无人机/操控员”并不总等于前端内部真的认为“已选中”
5. 自动化需要尽量复用人工浏览器式持久会话，避免频繁重新短信登录

--------------------------------------------------
## 2. 当前状态总览
--------------------------------------------------

一句话总结：

登录、进入业务页、读取历史计划、打开新增页、自动填表、自动点击提交、自动处理二次确认、提交后回读最近计划，这条主线已经打通；
但 UOM 站点本身仍可能偶发异常，因此仍需保留日志、结果校验与必要时的临时调试能力。

已经打通：
1. 持久化浏览器登录态复用
2. UOM 主站登录状态检查
3. 进入 `运行管理 -> 飞行活动申请 -> 一般飞行活动`
4. 从 iframe 提取 oapi 所需认证
5. 查询最近计划列表和详情
6. 打开 `flyIndexAdd` 新增页
7. 自动把上一次计划的航空器/操控员等核心内容灌入新增页
8. 空域统一按本地经纬度配置注入（不再依赖常用空域点选）
9. 自动关闭温馨提示
10. 自动选择航空器
11. 自动选择操控员
12. 自动触发提交和二次确认
13. 提交后等待并重新读取最近计划做验证
14. 进入 `运行管理 -> 空域信息查询`
15. 对输入的 WGS84 多边形执行“适飞空域”图层覆盖判断
16. 将空域查询结果缓存到本地，重复查询优先命中缓存

仍需持续关注：
1. UOM 服务端偶发 500 / 异常响应
2. 页面异步渲染导致的时序不稳定
3. 批量提交列表模式下的稳定性
4. 如果站点改版，新增页控件结构可能再次变化
5. 空域查询当前仅判断“与适飞空域图层的覆盖关系”，不是最终审批结论

--------------------------------------------------
## 3. 当前推荐技术路线
--------------------------------------------------

当前主线不是“纯 API 提交”，而是“持久化浏览器 + 前端真实流程”。

推荐主线：
1. 使用 Playwright 持久化 profile
2. 复用已登录状态
3. 打开“一般飞行活动”业务页
4. 从 iframe 读取最近计划
5. 打开新增页
6. 自动填入上次计划内容 + 目标新时间
7. 走真实前端提交流程
8. 提交后再回读最近计划验证结果

--------------------------------------------------
## 3.1 配置文件拆分（2026-05-20）
--------------------------------------------------

当前提交流程已拆分为三类配置文件：

1. `config/config.json`
   - 主配置，仅存稳定身份与默认参数
   - 包含认证、联系人、无人机、操控员、`plan_defaults.timezone` 等

2. `config/airspace.json`
   - 本地常用空域缓存
   - 只负责按名称提供经纬度空域数据
   - `items[0]` 也是 CLI 直接传时间时的保底空域来源

3. `config/submit_plan.json`
   - 待提交计划列表
   - 每项都包含 `planBeg` / `planEnd` 和 `airspace`
   - `airspace.type=common_ref` 表示从 `airspace.json` 按名称取经纬度
   - `airspace.type=polygon` 表示直接使用该条内联经纬度

当前统一策略：
- 不实现常用空域 UI 点选
- 所有空域最终都解析成经纬度对象，再注入新增页
- AI 触发批量提交时，默认应优先改写 `submit_plan.json`，并使用 `--use-submit-plan` 执行
- 默认无参和 CLI 仅传时间时，都使用 `airspace.json` 第一个空域作为保底

为什么不走纯 API 主线：
1. 直接 `POST /oapi/pub/planInfo` 曾返回 500
2. 服务端报错曾出现：`Cannot invoke PlanInfo.getPlanType() because planInfo is null`
3. 即使把前端 form 序列化出来直接发，也不稳定
4. 所以“直接构造请求体提交”目前不是可靠主方案

--------------------------------------------------
## 4. 最重要的技术结论
--------------------------------------------------

### 4.1 UOM 有两套认证，不要混用

A. 主站认证：
用于 `/api/*` 接口
来源：
- `window.nros.getToken()`
- `localStorage.session_token`
- 页面主站 UI 文本（首页/运行管理等）
- 当前 URL 是否仍为 `#/login`

B. 飞行计划业务认证：
用于 `/oapi/*` 接口
来源不是主站 token，而是“一般飞行活动” iframe 内部的：
- `PUB-Token`
- iframe URL 里的 `ticket`
- `userName`

关键结论：
- 主站 token 和 oapi token 不是一回事
- 不能拿主站 token 直接调 `/oapi/*`
- 不能只看残留 `session_token` 就判断“已登录”
- 如果 URL 仍在 `#/login`，或者主站框架没真正起来，应优先视为假阳性登录态

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

补充约定（2026-05）：
- 图形验证码默认直接使用 OCR 结果发起这一次 `sendSmsCode`
- 若接口返回“短信验证码还在有效期内”，不要再次调用 `sendSmsCode`
- 此时应优先复用用户手上上一条仍在 10 分钟有效期内的短信验证码继续登录
- 若 AI 代跑脚本并阻塞在 `input("请输入短信验证码")`，当前约定仍是保留 `stdin` 回填模式：应以后台 `pty` 进程运行，轮询输出，在拿到用户回复后通过 stdin 回写验证码，不要另起文件轮询的新方案

### 4.5 新增页提交链路虽已打通，但不要因此删除调试意识

已确认：
1. 页面显示出无人机/操控员行，不代表任意后续版本都仍然稳定
2. 点击到提交申请按钮，不代表一定已真正成功落库
3. 自动化结论应优先看提交日志和提交后最近计划回读结果
4. 如果后续站点改版或校验逻辑变动，仍可能需要重新创建临时调试脚本

### 4.6 空域查询已形成可复用 CLI 能力

已确认：
1. `cli/uom_airspace_probe.py query` 已可作为正式入口，供 AI 直接查询“某块区域是否落在适飞空域图层上”
2. 输入统一使用 `polygonWgs84` 字符串：`lng,lat|lng,lat|...`
3. 查询会优先复用持久化登录态；若登录真的失效，脚本会沿用现有自动短信登录链路
4. 查询结果会写入 `cache/airspace_query_cache.json`
5. 同一块多边形再次查询时，默认优先命中缓存，不再重新打开网页

当前判断口径：
- `inside_suitable`
- `partial_overlap`
- `outside_suitable`
- `unknown`

注意：
- 这只是“与官方适飞空域图层的覆盖关系”
- 不是飞行申请审批结论
- 不是机场净空、临时管制、天气风险等综合判断

--------------------------------------------------
## 5. 当前项目结构
--------------------------------------------------

### 5.1 底层能力

#### `uom_core.py`
当前公共底层能力文件。

职责：
- 配置读取
- 持久化浏览器启动/关闭
- 主站登录态判断
- 短信登录
- 菜单定位与进入 `一般飞行活动`
- iframe 认证提取
- oapi 检查
- 最近计划 / 详情读取
- 打开新增页
- 自动填表
- 表单快照 / 探测 / 提交等辅助函数

重要原则：
- 公共逻辑应尽量回到这里
- 不要在上层流程脚本里重复复制底层实现

### 5.2 登录/状态入口

#### `uom_login.py`
职责：
- 检查主站状态
- 必要时短信登录
- 确保能进入一般飞行活动
- 查看最近计划
- 打开持久化浏览器给人工检查

常用命令：

```bash
python3 uom_login.py status
python3 uom_login.py login
python3 uom_login.py ensure-fly
python3 uom_login.py latest-plan
python3 uom_login.py open-browser
```

### 5.3 自动提交飞行计划入口

#### `uom_submit_fly_plan.py`
职责：
- 自动进入新增页
- 自动填入数据
- 输出 precheck / postcheck
- 自动继续提交流程
- 提交后等待并回读最近计划验证结果
- 支持 `--dry-run` 只预演、不真正提交

常用命令：

```bash
python3 uom_submit_fly_plan.py
python3 uom_submit_fly_plan.py --start-utc-ts 1747908000 --end-utc-ts 1747911600
python3 uom_submit_fly_plan.py --use-submit-plan
python3 uom_submit_fly_plan.py --use-time-list
python3 uom_submit_fly_plan.py --dry-run
python3 uom_submit_fly_plan.py --use-submit-plan --dry-run
```

说明：
- 该文件原名 `uom_semiauto.py`
- 现在已按实际职责改名为 `uom_submit_fly_plan.py`
- 文档层面不再按“半自动”定位描述它，而统一按“自动提交”理解

### 5.4 空域探测 / 多边形查询入口

#### `uom_airspace_probe.py`
职责：
- `probe`：进入 `运行管理 -> 空域信息查询`，输出 iframe / 地图 / 资源加载调试信息
- `query`：输入多边形，判断它和“适飞空域”图层的覆盖关系
- 自动复用持久化登录态；确实掉线时沿用现有自动短信登录逻辑
- 自动把查询结果缓存到本地

常用命令：

```bash
python3 cli/uom_airspace_probe.py
python3 cli/uom_airspace_probe.py probe --headless
python3 cli/uom_airspace_probe.py query --polygon-wgs84 "104.01902676,30.52132641|104.01574373,30.51483813|104.02269602,30.51310046|104.02413368,30.51722276"
python3 cli/uom_airspace_probe.py query --polygon-wgs84 "..." --force-refresh
```

输出重点：
- `status=online_ok`：本轮真实打开网页完成判断
- `status=cache_hit`：本轮直接命中本地缓存
- `status=login_required`：当前仍未进入可靠主站登录态
- `evidence`：保留 zoom、tile 数量、像素覆盖率、登录链路等证据

### 5.4 配置文件

#### `config/config.json`
保存内容包括：
- 无人机信息
- 操控员信息
- 联系方式
- 默认参数（例如 `plan_defaults.timezone`）

#### `config/airspace.json`
保存本地常用空域缓存，按名称提供经纬度。

#### `config/submit_plan.json`
保存待提交计划列表；AI 若要批量提交，默认优先修改这个文件。

#### `cache/airspace_query_cache.json`
保存空域多边形查询缓存。

约定：
- 命中缓存时不再打开网页
- 若明确要重查网页，使用 `--force-refresh`
- 这是运行时缓存，不是人工维护的配置文件

推荐结构示例：

```json
{
  "timezone": "UTC+8",
  "plans": [
    {
      "planBeg": "2026-05-22 10:00:00",
      "planEnd": "2026-05-22 11:00:00",
      "airspace": {
        "type": "common_ref",
        "name": "三江公园"
      }
    }
  ]
}
```

### 5.5 持久化浏览器目录

`PROFILE_DIR`

非常重要。
这是当前复用登录态的核心资产，不要随意删除。

补充约定（2026-05-22）：
- 当前 profile 已加单实例锁
- 同一时刻只能有一个 UOM 脚本占用它
- 若脚本报“另一个 UOM 脚本正在占用持久化浏览器 profile”，应先结束另一条在跑的 UOM 命令，再继续
- 锁文件属于运行时产物，统一放在 `runtime/` 下，不放项目根目录

### 5.6 其他重要文件

- `manual_selection_log.json`
  - 自动提交脚本最近一轮运行的整轮聚合日志
  - 会覆盖上一轮，但保留本轮全部计划项的执行结果
- `README.md`
  - 项目入口说明，偏快速导航
- `SKILL.md`
  - 给 AI 用的调用索引，只保留入口、命令、配置修改方式

### 5.7 临时调试脚本约定

`uom_selection_debug.py` 已删除。

这里要明确约定：
- 它只是某一阶段的临时调试文件
- 调试完成后删除是正确做法
- 不应再把它作为长期现役文件写进项目结构
- 如果后续遇到新的页面问题、选择器问题、提交流程问题，可以按当时问题临时创建新的调试脚本
- 临时调试脚本应尽量非阻塞，不要使用 `input()` 等待回车
- 调试完成后优先删除，避免项目结构持续膨胀

--------------------------------------------------
## 6. 当前脚本使用方式
--------------------------------------------------

### 6.1 查看登录状态

```bash
python3 uom_login.py status
```

### 6.2 执行短信登录

```bash
python3 uom_login.py login
```

注意：
- 要尽量节省短信验证码消耗
- 如果 8 分钟内登录未成功，应提醒人工把验证码用掉，避免未使用验证码堆积导致限制

### 6.3 确保进入一般飞行活动

```bash
python3 uom_login.py ensure-fly
```

### 6.4 查看最近计划

```bash
python3 uom_login.py latest-plan
```

### 6.5 打开浏览器给人工检查

```bash
python3 uom_login.py open-browser
```

### 6.6 运行自动提交流程

```bash
python3 uom_submit_fly_plan.py
```

### 6.7 仅预演，不真正提交

```bash
python3 uom_submit_fly_plan.py --dry-run
```

### 6.8 使用指定 UTC 时间戳

```bash
python3 uom_submit_fly_plan.py --start-utc-ts 1747908000 --end-utc-ts 1747911600
```

### 6.9 使用 `submit_plan.json` 批量执行（推荐主路径）

```bash
python3 uom_submit_fly_plan.py --use-submit-plan
```

兼容旧参数：

```bash
python3 uom_submit_fly_plan.py --use-time-list
```

### 6.10 探测空域信息查询页

```bash
python3 cli/uom_airspace_probe.py
python3 cli/uom_airspace_probe.py probe --headless
```

### 6.11 查询一块区域是否与适飞空域重叠

```bash
python3 cli/uom_airspace_probe.py query --polygon-wgs84 "104.01902676,30.52132641|104.01574373,30.51483813|104.02269602,30.51310046|104.02413368,30.51722276"
```

### 6.12 强制忽略缓存，重新打开网页查询

```bash
python3 cli/uom_airspace_probe.py query --polygon-wgs84 "..." --force-refresh
```

--------------------------------------------------
## 7. 时间规则与输入模式
--------------------------------------------------

当前支持 3+1 种时间入口：

1. CLI 直接传一对 UTC 时间戳
   - `--start-utc-ts <秒级UTC时间戳>`
   - `--end-utc-ts <秒级UTC时间戳>`
   - 读取 `plan_defaults.timezone`（兼容旧配置时也可兜底读旧字段），转成本地时间后提交单条计划
   - 空域默认使用 `airspace.json` 第一项作为保底

2. CLI 传 `--use-submit-plan`（推荐主路径）
   - 读取 `config/submit_plan.json` 中 `plans`
   - 顺序循环提交
   - 最多 5 条，超过直接报错

3. CLI 传 `--use-time-list`
   - 仅作兼容别名
   - 实际仍读取 `config/submit_plan.json`
   - 不建议 AI 新流程继续依赖它

4. 无参数保底
   - 读取最近一条计划详情
   - 使用 `planBeg/planEnd + 1 day`
   - 空域使用 `airspace.json` 第一项保底

5. `--dry-run`
   - 会完成登录态、进入业务页、读取最近计划检查
   - 不进入新增页，不触发提交
   - 可与 `--use-submit-plan` 组合使用

注意：
- `timezone` 填写的是时区，不是编码；不要误写成 `UTF+8`
- 当前默认推荐：`UTC+8` 或 `Asia/Shanghai`

--------------------------------------------------
## 8. 当前工作流建议
--------------------------------------------------

如果是接手维护这个项目，建议顺序：

1. 先看 `README.md`
2. 再看 `SKILL.md`
3. 需要完整背景时看 `HANDOFF_UOM_PROJECT.md`
4. 需要改底层逻辑时读 `uom_core.py`
5. 需要改登录入口时读 `uom_login.py`
6. 需要改自动提交流程时读 `uom_submit_fly_plan.py`
7. 如果真实站点行为异常，再按当时问题临时创建新的调试脚本

--------------------------------------------------
## 9. 常见误区
--------------------------------------------------

1. 不要继续依赖旧的纯 API / RSAL 路线做主线
2. 不要只看 token 残留就判断“登录成功”
3. 不要把父页面 `PUB-Token=undefined` 当成可用业务认证
4. 不要在多个入口文件里重复复制公共逻辑
5. 不要把临时调试脚本固化成长期架构的一部分
6. 不要把 `SKILL.md` 当成长篇交接文档继续堆细节
7. 不要只凭“点到了提交按钮”就断言提交成功；要看日志和提交后最近计划回读

--------------------------------------------------
## 10. 结论
--------------------------------------------------

当前项目的文档分工是：

- `SKILL.md`
  - 给 AI 的项目调用索引
  - 只保留入口脚本、常用命令、配置修改方式
- `README.md`
  - 给人快速了解项目结构和入口
- `HANDOFF_UOM_PROJECT.md`
  - 保留完整背景、历史结论、技术细节、架构演化与调试约定

当前主线脚本是：
- `uom_core.py`
- `uom_login.py`
- `uom_submit_fly_plan.py`

而临时调试脚本：
- 不应再视为长期项目结构的一部分
- 需要时临时创建，用完删除
