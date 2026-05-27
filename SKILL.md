---
name: uom-automation
description: UOM 自动化项目操作索引：按接口调用登录/状态脚本、空域多边形查询脚本、读取计划脚本、自动提交脚本，并在需要时修改主配置/空域缓存/提交列表。
tags: [uom, drone, automation, caac, 无人机, 飞行申请]
---

# UOM 自动化项目操作索引

路径变量约定：
- `PROJECT_ROOT=/home/skye/csy/uom_automation`
- `HANDOFF_DOC=${PROJECT_ROOT}/doc/HANDOFF_UOM_PROJECT.md`
- 下文再次提到文件时，默认使用相对路径，或写作 ``${PROJECT_ROOT}/相对路径``

这份 `SKILL.md` 只负责告诉 AI：
- 先看哪些文件
- 该调用哪个脚本入口
- 常用命令怎么跑
- 配置改哪里
- 提交列表和空域缓存放哪里
- 哪些细节不要写回 skill，而应去看交接文档

详细背景、历史坑点、技术结论、调试经验，统一看：
- `doc/HANDOFF_UOM_PROJECT.md`

不要在沙盒里运行，直接在系统中运行。不然会找不到环境。
不要使用 `--headless` 模式，默认用有头浏览器。

## 优先阅读顺序

1. `SKILL.md`
2. `README.md`
3. `doc/HANDOFF_UOM_PROJECT.md`
4. 需要实现或修改逻辑时，再读：
   - `core/uom_core.py`
   - `cli/uom_login.py`
   - `cli/uom_airspace_probe.py`
   - `cli/uom_submit_fly_plan.py`
   - `cli/uom_read_plan.py`

## 当前入口脚本

### 1. 登录 / 状态入口
文件：`cli/uom_login.py`

用途：
- 检查主站登录态
- 必要时走短信登录
- 确保进入 `一般飞行活动`
- 读取最近计划
- 打开持久化浏览器供人工检查

常用命令：
```bash
python3 cli/uom_login.py status
python3 cli/uom_login.py login
python3 cli/uom_login.py ensure-fly
python3 cli/uom_login.py latest-plan
python3 cli/uom_login.py open-browser
```

### 2. 自动提交飞行计划入口
文件：`cli/uom_submit_fly_plan.py`

用途：
- 未登录时自动发送短信验证码登录主站
- 自动进入一般飞行活动并打开新增页
- 自动填入最近计划内容和目标时间
- 自动触发提交并在提交后检查结果
- 支持 `--dry-run` 只做预演，不真正提交

常用命令：
```bash
python3 cli/uom_submit_fly_plan.py
python3 cli/uom_submit_fly_plan.py --start-utc-ts 1747908000 --end-utc-ts 1747911600
python3 cli/uom_submit_fly_plan.py --use-submit-plan
python3 cli/uom_submit_fly_plan.py --use-time-list
python3 cli/uom_submit_fly_plan.py --dry-run
python3 cli/uom_submit_fly_plan.py --use-submit-plan --dry-run
```

说明：
- 这个脚本已不是"半自动"定位，当前文档统一按"自动提交"理解
- AI 触发提交时，默认应优先走 `submit_plan.json` + `--use-submit-plan` 这条主路径
- `submit_plan.json`文件相对路径在config/submit_plan.json。默认已经创建了的，先找一下，找不到才能创建。
- `--use-time-list` 仅保留兼容，不应作为 AI 首选入口
- 运行日志默认看：`log/manual_selection_log.json`，该文件始终保存最近一轮运行的整轮聚合结果（会覆盖上一轮，但保留本轮全部计划项）

### 3. 读取飞行计划详情入口
文件：`cli/uom_read_plan.py`

用途：
- 未登录时自动发送短信验证码登录主站
- 读取最近飞行计划详情（默认 20 条）
- 保存为 JSON 文件（默认 `log/uom_recent_plan_details.json`）
- 包含计划列表和详细信息（空域经纬度、无人机、操控员等）

常用命令：
```bash
python3 cli/uom_read_plan.py
python3 cli/uom_read_plan.py --output /path/to/output.json
python3 cli/uom_read_plan.py --headless
```

### 4. 空域多边形查询入口
文件：`cli/uom_airspace_probe.py`

用途：
- `probe`：进入“空域信息查询”页，输出页面、iframe、地图、资源加载等调试信息
- `query`：输入一块 WGS84 多边形，判断它和官方“适飞空域”图层的覆盖关系
- 查询结果会写入本地缓存；同一块多边形再次查询时默认优先命中缓存

常用命令：
```bash
python3 cli/uom_airspace_probe.py
python3 cli/uom_airspace_probe.py probe --headless
python3 cli/uom_airspace_probe.py query --polygon-wgs84 "104.01902676,30.52132641|104.01574373,30.51483813|104.02269602,30.51310046|104.02413368,30.51722276"
python3 cli/uom_airspace_probe.py query --polygon-wgs84 "..." --force-refresh
```

返回结果约定：
- `status=online_ok`：本轮真实打开网页并完成判断
- `status=cache_hit`：命中 `cache/airspace_query_cache.json`
- `status=login_required`：脚本已尝试复用/自动登录，但当前仍未进入可靠主站登录态
- `judgement` 当前只回答：
  - `inside_suitable`
  - `partial_overlap`
  - `outside_suitable`
  - `unknown`

AI 使用约定：
- 需要判断“某块区域是不是在适飞区”时，优先调这个脚本，不要自己重新点网页
- 输入直接传 `--polygon-wgs84 "lng,lat|lng,lat|..."`，不要发明新坐标格式
- 若只是重复问同一块区域，默认不要加 `--force-refresh`
- 只有在怀疑缓存过旧、或明确要重查网页时，才加 `--force-refresh`
- 该判断口径目前仅是“与官方适飞空域图层的覆盖关系”，不是审批结果、也不是最终合规结论
- 与空域申请联动时，默认决策顺序是：高德定位 POI/地址 → 生成 polygonWgs84 → 调 `query` 判断适飞区覆盖关系 → 再决定提交高度与是否直接申请
- 高度默认规则：
  - `judgement=inside_suitable`：默认按 500m 申请
  - `judgement=partial_overlap` / `outside_suitable`：默认按 120m 申请
  - `judgement=unknown`：不得假装已经判定，需先补完查询或明确告诉用户当前仍待核验

AI 调用模板：
- 若用户已经直接给出一块区域的经纬度：
  - 先整理成 `polygonWgs84="lng,lat|lng,lat|lng,lat|..."`，点顺序沿边界依次排列即可
  - 再执行：
    ```bash
    python3 cli/uom_airspace_probe.py query --polygon-wgs84 "..."
    ```
- 若用户给的是“某个地点”而不是多边形：
  - 先向用户要边界点，或先把需求收敛成“以该点为中心的一小块矩形/多边形”
  - 不要擅自编造坐标
- 若用户只是重复问刚查过的同一块区域：
  - 默认直接执行同一条 `query` 命令，不加 `--force-refresh`
- 若用户明确说“重新查一次网页最新结果”：
  - 才执行：
    ```bash
    python3 cli/uom_airspace_probe.py query --polygon-wgs84 "..." --force-refresh
    ```

AI 对用户的推荐输出格式：
```text
查询区域 polygonWgs84:
104.01902676,30.52132641|104.01574373,30.51483813|104.02269602,30.51310046|104.02413368,30.51722276

查询结果:
- status: online_ok
- judgement: outside_suitable
- cacheHit: false

说明:
- 当前结论表示这块区域与“适飞空域”图层没有重叠
- 这不是最终审批结论，只是图层覆盖关系判断
```

### 5. 底层公共能力
文件：`core/uom_core.py`

用途：
- 放公共函数，不作为主要人类入口文档
- 涉及登录态判断、业务页进入、读取最近计划、打开新增页、填表、提交等底层能力时，优先在这里改

## 配置文件

主要配置文件：`config/config.json`

配置拆分后职责如下：
- `config/config.json`：主配置（认证、联系人、无人机、操控员、默认参数）
- `config/airspace.json`：本地常用空域缓存；提交前按名称换算成经纬度
- `config/submit_plan.json`：待提交批次列表；每项同时包含时间和空域描述
- `config/sms_code.json`：短信验证码传递文件；脚本写 `sent_at`，AI 写 `code` + `filled_at`
- `cache/airspace_query_cache.json`：空域多边形查询缓存；命中时不再打开网页

AI 在需要改配置时，优先按职责修改：
- 身份与默认参数改 `config/config.json`
- 常用空域缓存改 `config/airspace.json`
- 待提交计划改 `config/submit_plan.json`

`submit_plan.json` 示例：
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

时间入口约定：
- AI / 批量提交默认入口：`--use-submit-plan`
- 直接传 CLI UTC 时间戳：`--start-utc-ts` / `--end-utc-ts`（保底使用 `airspace.json` 第一项空域）
- `--use-time-list` 仅作兼容别名，实际仍读取 `submit_plan.json`，不建议新流程继续依赖它
- 只做预演不提交：`--dry-run`

## 文档分工约定

### `SKILL.md` 应该保留的内容
- 脚本入口索引
- 常用命令
- 配置文件位置与修改点
- 文件职责的最小说明
- 告诉 AI 详细背景去哪里看

### 不应继续堆在 `SKILL.md` 的内容
以下内容应写到 `doc/HANDOFF_UOM_PROJECT.md`：
- 详细历史背景
- 认证模型细节
- 页面结构与前端坑点
- 调试结论
- 时间规则演化
- 旧方案为什么废弃
- 长篇阻塞点分析

## 关于调试脚本

约定：
- 调试脚本只作为临时调试用途
- 用完即可删除
- 不应作为长期现役文件写进项目结构
- 若后续再次需要专项调试，AI 可根据当时问题自行临时创建新的调试脚本
- 调试结束后应优先删除，避免项目结构继续膨胀

## 当前执行约束

- 不要回退到旧的纯 API / RSAL 主线
- 优先复用持久化浏览器 profile，减少短信登录
- 当前 `.playwright-uom-profile` 已加单实例锁；同一时刻只能有一个 UOM 脚本占用它，若锁冲突应先结束另一条在跑的 UOM 命令
- 锁文件属于运行时产物，统一放在 `runtime/` 下；不要把它当项目源码文件处理
- 需要改流程时，公共逻辑优先收敛到 `core/uom_core.py`
- 需要背景和历史结论时，不要在 `SKILL.md` 里找长篇说明，直接看 `doc/HANDOFF_UOM_PROJECT.md`
- 当前所有入口脚本（`uom_login.py`、`uom_submit_fly_plan.py`、`uom_read_plan.py`、`uom_airspace_probe.py`）在未登录时都会自动尝试短信验证码登录
- 短信验证码通过 `config/sms_code.json` 文件传递：脚本发短信后写入 `sent_at`，AI 或人工写入 `code` 和 `filled_at`，脚本每秒轮询读取，10 分钟超时
- 人工在终端运行时，也可直接在命令行输入验证码（stdin 非阻塞读取与文件轮询并行）
- 图形验证码默认直接使用 OCR 结果尝试发短信；若接口返回"短信验证码还在有效期内"，脚本会跳过重复发送，等待文件中的验证码
- 遇到新的空域申请任务，默认工作流是：先用高德地图定位地点并拿到经纬度，再计算待申请 polygon，然后用 `cli/uom_airspace_probe.py query` 查询是否适飞，最后再根据查询结果生成并提交申请
- 默认高度策略：如果整块 polygon 都在适飞区（`inside_suitable`），默认按 500m 申请；如果哪怕只有一部分不在适飞区（如 `partial_overlap`、`outside_suitable`），默认按 120m 申请
- 若适飞区查询结果仍是 `unknown` / `error` / `login_required`，不得跳过核验直接声称方案已定；应先补完查询或明确告知用户当前仍待核验

## 结论

把这份 skill 当作"项目操作索引"，不要当作完整交接文档。

详细说明以：
`doc/HANDOFF_UOM_PROJECT.md`
为准。
