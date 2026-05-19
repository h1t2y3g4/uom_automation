---
name: uom-automation
description: UOM 自动化项目操作索引：按接口调用登录/状态脚本、读取计划脚本、自动提交脚本，并在需要时修改 config.json。
tags: [uom, drone, automation, caac, 无人机, 飞行申请]
---

# UOM 自动化项目操作索引

位置：`~/hermes_interface/uom-automation/`

这份 `SKILL.md` 只负责告诉 AI：
- 先看哪些文件
- 该调用哪个脚本入口
- 常用命令怎么跑
- 配置改哪里
- 哪些细节不要写回 skill，而应去看交接文档

详细背景、历史坑点、技术结论、调试经验，统一看：
- `~/hermes_interface/uom-automation/HANDOFF_UOM_PROJECT.md`

## 优先阅读顺序

1. `~/hermes_interface/uom-automation/SKILL.md`
2. `~/hermes_interface/uom-automation/README.md`
3. `~/hermes_interface/uom-automation/HANDOFF_UOM_PROJECT.md`
4. 需要实现或修改逻辑时，再读：
   - `~/hermes_interface/uom-automation/uom_core.py`
   - `~/hermes_interface/uom-automation/uom_login.py`
   - `~/hermes_interface/uom-automation/uom_submit_fly_plan.py`
   - `~/hermes_interface/uom-automation/uom_read_plan.py`

## 当前入口脚本

### 1. 登录 / 状态入口
文件：`uom_login.py`

用途：
- 检查主站登录态
- 必要时走短信登录
- 确保进入 `一般飞行活动`
- 读取最近计划
- 打开持久化浏览器供人工检查

常用命令：
```bash
python3 uom_login.py status
python3 uom_login.py login
python3 uom_login.py ensure-fly
python3 uom_login.py latest-plan
python3 uom_login.py open-browser
```

### 2. 自动提交飞行计划入口
文件：`uom_submit_fly_plan.py`

用途：
- 复用持久化浏览器登录态
- 自动进入一般飞行活动并打开新增页
- 自动填入最近计划内容和目标时间
- 自动触发提交并在提交后检查结果
- 支持 `--dry-run` 只做预演，不真正提交

常用命令：
```bash
python3 uom_submit_fly_plan.py
python3 uom_submit_fly_plan.py --start-utc-ts 1747908000 --end-utc-ts 1747911600
python3 uom_submit_fly_plan.py --use-time-list
python3 uom_submit_fly_plan.py --dry-run
python3 uom_submit_fly_plan.py --use-time-list --dry-run
```

说明：
- 这个脚本已不是"半自动"定位，当前文档统一按"自动提交"理解
- 运行日志默认看：`manual_selection_log.json`

### 3. 读取飞行计划详情入口
文件：`uom_read_plan.py`

用途：
- 读取最近飞行计划详情（默认 10 条）
- 保存为 JSON 文件（默认 `uom_recent_plan_details.json`）
- 包含计划列表和详细信息（空域经纬度、无人机、操控员等）

常用命令：
```bash
python3 uom_read_plan.py
python3 uom_read_plan.py --output /path/to/output.json
python3 uom_read_plan.py --headless
```

### 4. 底层公共能力
文件：`uom_core.py`

用途：
- 放公共函数，不作为主要人类入口文档
- 涉及登录态判断、业务页进入、读取最近计划、打开新增页、填表、提交等底层能力时，优先在这里改

## 配置文件

主要配置文件：`config.json`

AI 在需要改配置时，优先修改这里：
- 无人机信息
- 操控员信息
- 联系方式
- 时间配置

时间相关配置示例：
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

时间入口约定：
- 直接传 CLI UTC 时间戳：`--start-utc-ts` / `--end-utc-ts`
- 批量使用配置时间列表：`--use-time-list`
- 只做预演不提交：`--dry-run`

## 文档分工约定

### `SKILL.md` 应该保留的内容
- 脚本入口索引
- 常用命令
- 配置文件位置与修改点
- 文件职责的最小说明
- 告诉 AI 详细背景去哪里看

### 不应继续堆在 `SKILL.md` 的内容
以下内容应写到 `HANDOFF_UOM_PROJECT.md`：
- 详细历史背景
- 认证模型细节
- 页面结构与前端坑点
- 调试结论
- 时间规则演化
- 旧方案为什么废弃
- 长篇阻塞点分析

## 关于调试脚本

`uom_selection_debug.py` 不属于稳定项目架构的一部分。

约定：
- 它只是临时调试脚本
- 用完即可删除
- 不应作为长期现役文件写进项目结构
- 若后续再次需要专项调试，AI 可根据当时问题自行临时创建新的调试脚本
- 调试结束后应优先删除，避免项目结构继续膨胀

## 当前执行约束

- 不要回退到旧的纯 API / RSAL 主线
- 优先复用持久化浏览器 profile，减少短信登录
- 需要改流程时，公共逻辑优先收敛到 `uom_core.py`
- 需要背景和历史结论时，不要在 `SKILL.md` 里找长篇说明，直接看 `HANDOFF_UOM_PROJECT.md`

## 结论

把这份 skill 当作“项目操作索引”，不要当作完整交接文档。

详细说明以：
`~/hermes_interface/uom-automation/HANDOFF_UOM_PROJECT.md`
为准。
