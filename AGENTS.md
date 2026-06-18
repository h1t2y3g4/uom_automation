# AGENTS.md

## 项目概述

Python + Playwright 自动化项目，用于 UOM（中国民航局无人驾驶航空器综合管理平台，`uom.caac.gov.cn`）。通过持久化浏览器会话实现飞行计划提交、空域查询和起飞确认。

## 关键约束

- **不要在沙盒/容器中运行** — Playwright 持久化 profile 和浏览器需要真实宿主机访问，沙盒执行会失败。
- **AI/自动化场景必须使用 `--headless`** — 仅手动调试时使用有头模式。
- **单实例浏览器 profile** — `.playwright-uom-profile/` 有文件锁（`runtime/locks/playwright-uom-profile.lock`），同一时刻只能有一个 UOM 脚本占用。锁冲突时先终止另一个 UOM 进程。
- **无 CI、无 lint、无测试** — 本仓库没有任何自动化验证，没有 pytest、ruff、mypy 等。
- **使用 Python 3** — 所有脚本显式使用 `python3`，不要用 `python`。

## 快速参考

```bash
# 登录/状态
python3 cli/uom_login.py status
python3 cli/uom_login.py login
python3 cli/uom_login.py ensure-fly
python3 cli/uom_login.py latest-plan
python3 cli/uom_login.py open-browser

# 提交飞行计划（推荐路径）
python3 cli/uom_submit_fly_plan.py --use-submit-plan
python3 cli/uom_submit_fly_plan.py --use-submit-plan --dry-run

# 指定时间戳提交
python3 cli/uom_submit_fly_plan.py --start-utc-ts 1747908000 --end-utc-ts 1747911600

# 读取最近计划
python3 cli/uom_read_plan.py
python3 cli/uom_read_plan.py --output /path/to.json

# 空域多边形查询
python3 cli/uom_airspace_probe.py query --polygon-wgs84 "lng,lat|lng,lat|lng,lat|lng,lat"
python3 cli/uom_airspace_probe.py query --polygon-wgs84 "..." --force-refresh

# 更新文档中硬编码的项目路径（clone/移动后）
bash tools/fix_path.sh
```

## 架构

- `cli/` — 入口脚本：`uom_login.py`、`uom_submit_fly_plan.py`、`uom_airspace_probe.py`、`uom_read_plan.py`、`uom_takeoff_confirm.py`
- `core/` — 共享模块。`uom_core.py` 是 re-export 外观模块（向后兼容）；实际逻辑拆分为 `auth.py`、`config.py`、`constants.py`、`context.py`、`fly_plan.py`、`takeoff.py`、`ui_helpers.py`、`airspace_data.py`、`airspace_query.py`、`time_utils.py`、`qq_code_receiver.py`
- `cli_auto/` — 24 小时调度器（`uom_scheduler.py`），配合 systemd 服务实现每周计划提交 + 每日起飞确认
- `config/` — 运行时配置（`config.json` 含凭证已 gitignore；`airspace.json`、`submit_plan.json`、`sms_code.json` 已跟踪）
- `cache/` — 空域查询缓存（gitignored）
- `log/` — 执行日志（gitignored）
- `doc/HANDOFF_UOM_PROJECT.md` — 完整项目交接文档，含历史和调试笔记

## 两套独立认证体系

- **主站**（`/api/*`）：Token 来自 `window.nros.getToken()` / `localStorage.session_token`
- **oapi**（`/oapi/*`）：`PUB-Token` + `ticket` + `userName`，从业务 iframe 中提取
- 绝对不要用主站 token 调 oapi 接口，反之亦然。不要把父页面 cookie 中的 `PUB-Token=undefined` 当作有效凭证。

## 短信验证码中继

通过 `config/sms_code.json` 文件握手：
1. 脚本发送短信后写入 `sent_at`
2. AI 或人工写入 `code` + `filled_at`
3. 脚本每秒轮询，10 分钟超时
4. 若短信接口返回"验证码仍在有效期内"，跳过重复发送，等待已有验证码
5. 所有 CLI 脚本在未登录时自动尝试短信登录

## 空域查询约定

- 输入：`--polygon-wgs84 "lng,lat|lng,lat|..."`（WGS84，竖线分隔）
- 结果缓存在 `cache/airspace_query_cache.json`；重复查询默认命中缓存
- 仅在明确需要重新查询网页时才加 `--force-refresh`
- 判定值：`inside_suitable`、`partial_overlap`、`outside_suitable`、`unknown`
- 这只是图层覆盖关系，不是审批结论

## 高度策略

- `inside_suitable` → 默认 500m
- `partial_overlap` / `outside_suitable` → 默认 120m
- `unknown` → 不要猜测，先完成查询

## 提交流程

- 推荐路径：编辑 `config/submit_plan.json`，然后 `python3 cli/uom_submit_fly_plan.py --use-submit-plan`
- 将所有空域编辑批量放入一个 `submit_plan.json`，避免重复登录
- 始终通过 `log/manual_selection_log.json` 和提交后计划回读验证成功 — 点击提交 ≠ 提交成功

## 依赖

```
playwright>=1.60.0
ddddocr>=1.6.1
Pillow>=10.2.0
qq-botpy>=1.0.0
```

安装：`pip install -r requirements.txt && playwright install`

## 约定

- AI/自动化场景所有脚本默认 `--headless`
- 调试脚本是临时的 — 需要时创建，用完后删除
- 共享逻辑放入 `core/` 子模块，不要在 CLI 脚本中重复
- `SKILL.md` 是 AI 操作索引；`doc/HANDOFF_UOM_PROJECT.md` 是完整上下文文档
- `config/config.json` 包含凭证（手机号、认证信息）— 已 gitignore
