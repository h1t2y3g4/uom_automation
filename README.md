# UOM 自动化项目

项目目录：`/home/skye/hermes_interface/uom-automation`

## 当前结构

这个项目现在按职责保留 3 个主要入口：

- `uom_core.py`
  - 只放底层公共能力
  - 包括持久化 Playwright profile、登录态判断、进入一般飞行活动、oapi 认证、读取最近计划、打开新增页、填表、提交等公共函数
- `uom_login.py`
  - 专门处理登录/状态类操作
  - 包括：`status / login / ensure-fly / latest-plan / open-browser`
- `uom_submit_fly_plan.py`
  - 自动提交飞行计划入口

这样做的目的：
- 不再让 `uom_core.py` 同时充当 CLI 大杂烩
- 登录/状态与自动提交流程分离
- 公共底层能力只维护一份，避免重复逻辑漂移
- 临时调试脚本不作为长期架构的一部分记录

## 推荐用法

登录/状态相关：

```bash
python3 uom_login.py status
python3 uom_login.py login
python3 uom_login.py ensure-fly
python3 uom_login.py latest-plan
python3 uom_login.py open-browser
```

自动提交流程：

```bash
python3 uom_submit_fly_plan.py
python3 uom_submit_fly_plan.py --start-utc-ts 1747908000 --end-utc-ts 1747911600
python3 uom_submit_fly_plan.py --use-time-list
python3 uom_submit_fly_plan.py --dry-run
python3 uom_submit_fly_plan.py --use-time-list --dry-run
```

## 各文件职责

### 1. `uom_core.py`
只放公共能力，不承担具体业务模式的入口说明。

主要内容：
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

### 2. `uom_login.py`
专门负责：
- 检查主站状态
- 必要时短信登录
- 确保能进入一般飞行活动
- 查看最近计划
- 打开持久化浏览器给人工检查

### 3. `uom_submit_fly_plan.py`
专门负责：
- 自动进入新增页
- 填入最近计划内容
- 支持三种时间入口（CLI UTC 对 / config 列表 / 最近计划 +1 天保底）
- 支持 `--dry-run` 只解析时间与预演
- 自动提交流程
- 提交后等待 20 秒再检查最近计划与写日志

## 当前建议阅读顺序

1. `HANDOFF_UOM_PROJECT.md`
   - 当前完整交接文档
2. `uom_core.py`
   - 当前底层能力实现
3. `uom_login.py`
   - 登录/状态入口
4. `uom_submit_fly_plan.py`
   - 自动提交飞行计划入口
5. `SKILL.md`
   - 给 AI 的项目操作索引

## 当前现役文件

- `uom_core.py`
- `uom_login.py`
- `uom_submit_fly_plan.py`
- `HANDOFF_UOM_PROJECT.md`
- `SKILL.md`
- `config.json`
- `config_temp.json`
- `.playwright-uom-profile/`（持久化浏览器目录，不要随意删除）

## 临时调试脚本约定

- `uom_selection_debug.py` 已删除
- 这类文件只作为临时调试用途
- 若后续需要专项调试，可按当时问题临时创建
- 调试结束后应删除，不作为长期项目结构记录

## 备注

- 详细说明统一看 `HANDOFF_UOM_PROJECT.md`
- `SKILL.md` 只保留脚本入口、命令和配置修改方式，不再承载大段背景细节
- 如果后续继续开发，优先围绕 `uom_core.py` 补底层能力；围绕 `uom_submit_fly_plan.py` 调整自动提交流程
