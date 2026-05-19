# UOM 自动化项目

项目目录：`/home/skye/hermes_interface/uom-automation`

## 目录结构

```
.
├── cli/                    # CLI 脚本入口
│   ├── uom_login.py        # 登录/状态/浏览器入口
│   ├── uom_read_plan.py    # 读取飞行计划详情
│   └── uom_submit_fly_plan.py  # 自动提交飞行计划
├── config/                 # 配置文件
│   ├── config.json         # 主配置文件
│   └── config_temp.json    # 临时配置
├── core/                   # 核心模块
│   └── uom_core.py         # 底层公共能力
├── doc/                    # 文档
│   └── HANDOFF_UOM_PROJECT.md  # 项目交接文档
├── log/                    # 日志和输出文件
│   ├── manual_selection_log.json
│   └── uom_recent_plan_details.json
├── .gitignore
├── README.md
└── SKILL.md                # AI 操作索引
```

## 推荐用法

登录/状态相关：

```bash
python3 cli/uom_login.py status
python3 cli/uom_login.py login
python3 cli/uom_login.py ensure-fly
python3 cli/uom_login.py latest-plan
python3 cli/uom_login.py open-browser
```

读取飞行计划详情：

```bash
python3 cli/uom_read_plan.py
python3 cli/uom_read_plan.py --output /path/to/output.json
python3 cli/uom_read_plan.py --headless
```

自动提交流程：

```bash
python3 cli/uom_submit_fly_plan.py
python3 cli/uom_submit_fly_plan.py --start-utc-ts 1747908000 --end-utc-ts 1747911600
python3 cli/uom_submit_fly_plan.py --use-time-list
python3 cli/uom_submit_fly_plan.py --dry-run
python3 cli/uom_submit_fly_plan.py --use-time-list --dry-run
```

## 各目录职责

### `cli/` - CLI 脚本入口
- `uom_login.py`: 登录/状态检查、打开浏览器
- `uom_read_plan.py`: 读取最近飞行计划详情（默认 10 条）
- `uom_submit_fly_plan.py`: 自动提交飞行计划

### `core/` - 核心模块
- `uom_core.py`: 底层公共能力，包括：
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

### `config/` - 配置文件
- `config.json`: 主配置文件（无人机、操控员、联系方式、时间配置）
- `config_temp.json`: 临时配置

### `doc/` - 文档
- `HANDOFF_UOM_PROJECT.md`: 项目完整交接文档

### `log/` - 日志和输出文件
- `manual_selection_log.json`: 提交日志
- `uom_recent_plan_details.json`: 读取的计划详情

## 当前建议阅读顺序

1. `doc/HANDOFF_UOM_PROJECT.md`
   - 当前完整交接文档
2. `core/uom_core.py`
   - 当前底层能力实现
3. `cli/uom_login.py`
   - 登录/状态入口
4. `cli/uom_submit_fly_plan.py`
   - 自动提交飞行计划入口
5. `SKILL.md`
   - 给 AI 的项目操作索引

## 临时调试脚本约定

- 这类文件只作为临时调试用途
- 若后续需要专项调试，可按当时问题临时创建
- 调试结束后应删除，不作为长期项目结构记录

## 备注

- 详细说明统一看 `doc/HANDOFF_UOM_PROJECT.md`
- `SKILL.md` 只保留脚本入口、命令和配置修改方式，不再承载大段背景细节
- 如果后续继续开发，优先围绕 `core/uom_core.py` 补底层能力；围绕 `cli/uom_submit_fly_plan.py` 调整自动提交流程
