# UOM 自动化项目

路径变量约定：
- `PROJECT_ROOT=/home/skye/hermes_interface/uom-automation`
- 下文再次提到文件时，默认使用相对路径，或写作 ``${PROJECT_ROOT}/相对路径``

## 目录结构

```
.
├── cli/                    # CLI 脚本入口
│   ├── uom_login.py        # 登录/状态/浏览器入口
│   ├── uom_airspace_probe.py  # 空域页探测 / 多边形适飞查询
│   ├── uom_read_plan.py    # 读取飞行计划详情
│   └── uom_submit_fly_plan.py  # 自动提交飞行计划
├── cache/                  # 运行时缓存
│   └── airspace_query_cache.json  # 多边形适飞查询结果缓存
├── config/                 # 配置文件
│   ├── config.json         # 主配置文件
│   ├── airspace.json       # 本地常用空域缓存
│   └── submit_plan.json    # 待提交计划列表
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
python3 cli/uom_submit_fly_plan.py --use-submit-plan
python3 cli/uom_submit_fly_plan.py --use-time-list
python3 cli/uom_submit_fly_plan.py --dry-run
python3 cli/uom_submit_fly_plan.py --use-submit-plan --dry-run
```

空域探测 / 多边形适飞查询：

```bash
python3 cli/uom_airspace_probe.py
python3 cli/uom_airspace_probe.py probe --headless
python3 cli/uom_airspace_probe.py query --polygon-wgs84 "104.01902676,30.52132641|104.01574373,30.51483813|104.02269602,30.51310046|104.02413368,30.51722276"
python3 cli/uom_airspace_probe.py query --polygon-wgs84 "..." --force-refresh
```

说明：
- `probe` 主要用于调试页面与地图加载
- `query` 是正式给 AI / 脚本调用的空域查询入口
- `query` 会优先复用登录态；真掉线时会沿用现有自动短信登录
- 同一块多边形再次查询时，默认优先命中 `cache/airspace_query_cache.json`
- 当前返回的是“与适飞空域图层的覆盖关系”，不是最终审批结论

## 当前主路径约定

- AI 触发批量提交时，默认应优先修改 `config/submit_plan.json`，再执行 `python3 cli/uom_submit_fly_plan.py --use-submit-plan`
- `--use-time-list` 仅作兼容别名，实际仍读取 `submit_plan.json`，不建议新流程继续依赖它
- CLI 直接传 `--start-utc-ts / --end-utc-ts` 时，空域默认使用 `airspace.json` 第一项作为保底
- 空域统一走经纬度注入；`common_ref` 仅表示从 `airspace.json` 按名称换算经纬度，不再走“常用空域 UI 点选”

## 各目录职责

### `cli/` - CLI 脚本入口
- `uom_login.py`: 登录/状态检查、打开浏览器
- `uom_airspace_probe.py`: 空域页探测，以及基于官方适飞图层的多边形查询
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
- `config.json`: 主配置文件（认证、联系人、无人机、操控员、默认参数）
- `airspace.json`: 本地常用空域缓存，按名称提供经纬度
- `submit_plan.json`: 待提交计划列表，AI 批量提交默认优先改这个文件

### `cache/` - 运行时缓存
- `airspace_query_cache.json`: 空域多边形查询缓存；命中缓存时不会再次打开网页

### 持久化浏览器约定
- `.playwright-uom-profile` 是当前复用登录态的核心目录
- 同一时刻只能有一个 UOM 脚本占用它；若锁冲突，应先结束另一条在跑的 UOM 命令
- 锁文件属于运行时产物，现统一放在 `runtime/` 下，不再放根目录

### `doc/` - 文档
- `HANDOFF_UOM_PROJECT.md`: 项目完整交接文档

### `log/` - 日志和输出文件
- `manual_selection_log.json`: 最近一轮运行的整轮聚合日志（覆盖上一轮，但保留本轮全部计划项）
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
