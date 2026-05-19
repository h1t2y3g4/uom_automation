# UOM 自动化项目

项目目录：`/home/skye/hermes_interface/uom-automation`

## 当前结构

这个项目现在按职责拆成 4 个明确入口：

- `uom_core.py`
  - 只放底层能力
  - 包括持久化 Playwright profile、登录态判断、进入一般飞行活动、oapi 认证、读取最近计划、打开新增页、填表、探测、提交等公共函数
- `uom_login.py`
  - 专门处理登录/状态类操作
  - 包括：`status / login / ensure-fly / latest-plan / open-browser`
- `uom_semiauto.py`
  - 只放半自动提交流程
- `uom_selection_debug.py`
  - 只放“航空器/操控员内部选中状态”专项调试流程

这样做的目的：
- 不再让 `uom_core.py` 同时充当 CLI 大杂烩
- 登录/状态、半自动提交流程、专项调试各自独立
- 公共底层能力只维护一份，避免重复逻辑漂移
- 不再保留一个和半自动流程高度重叠的旧 probe 脚本

## 推荐用法

登录/状态相关：

```bash
python3 uom_login.py status
python3 uom_login.py login
python3 uom_login.py ensure-fly
python3 uom_login.py latest-plan
python3 uom_login.py open-browser
```

半自动流程：

```bash
python3 uom_semiauto.py
python3 uom_semiauto.py --start-utc-ts 1747908000 --end-utc-ts 1747911600
python3 uom_semiauto.py --use-time-list
python3 uom_semiauto.py --dry-run
python3 uom_semiauto.py --use-time-list --dry-run
```

专项调试：

```bash
python3 uom_selection_debug.py
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

### 3. `uom_semiauto.py`
专门负责：
- 自动进入新增页
- 填入最近计划内容
- 支持三种时间入口（CLI UTC 对 / config 列表 / 最近计划 +1 天保底）
- 支持 `--dry-run` 只解析时间与预演
- 输出 precheck / postcheck
- 让你人工确认
- 再尝试触发提交

### 4. `uom_selection_debug.py`
专门负责：
- 自动进入新增页
- 自动填表
- 不走最终提交流程
- 聚焦航空器/操控员内部选中状态
- 输出 data/ref/dialog/validate 相关线索

## 当前状态

主线已经打通：
- 复用持久化 Playwright profile
- 检查主站登录态
- 进入 `运行管理 -> 飞行活动申请 -> 一般飞行活动`
- 从 iframe 提取 `PUB-Token / ticket / userName`
- 查询最近历史计划与详情
- 打开 `flyIndexAdd` 新增页
- 自动填入上次计划的大部分内容
- `uom_semiauto.py` 已支持 AI 友好的时间传递：CLI UTC 对、config 批量列表、无参数保底、以及 `--dry-run` 预演

当前主要阻塞点：
- UOM 站点本身仍可能偶发返回异常或前端状态污染，批量模式仍需继续观察稳定性

## 当前建议阅读顺序

1. `HANDOFF_UOM_PROJECT.md`
   - 当前完整交接文档
2. `uom_core.py`
   - 当前底层能力实现
3. `uom_login.py`
   - 登录/状态入口
4. `uom_semiauto.py`
   - 半自动提交流程
5. `uom_selection_debug.py`
   - 内部选中状态专项调试流程
6. `SKILL.md`

## 当前现役文件

- `uom_core.py`
- `uom_login.py`
- `uom_semiauto.py`
- `uom_selection_debug.py`
- `HANDOFF_UOM_PROJECT.md`
- `SKILL.md`
- `config.json`
- `config_temp.json`
- `.playwright-uom-profile/`（持久化浏览器目录，不要随意删除）

## 备注

- 详细说明统一看 `HANDOFF_UOM_PROJECT.md`
- `SKILL.md` 是项目内最新操作说明文档，涉及脚本职责、默认命令、时间规则、阻塞点时优先看它
- 如果后续继续开发，优先围绕 `uom_core.py` 补底层能力；围绕 `uom_semiauto.py` / `uom_selection_debug.py` 调整具体流程
