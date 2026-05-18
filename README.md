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
- `uom_probe.py`
  - 只放无副作用探测流程
- `uom_semiauto.py`
  - 只放半自动提交流程

这样做的目的：
- 不再让 `uom_core.py` 同时充当 CLI 大杂烩
- 登录/状态、无副作用探测、半自动提交流程各自独立
- 公共底层能力只维护一份，避免重复逻辑漂移

## 推荐用法

登录/状态相关：

```bash
python3 uom_login.py status
python3 uom_login.py login
python3 uom_login.py ensure-fly
python3 uom_login.py latest-plan
python3 uom_login.py open-browser
```

无副作用探测：

```bash
python3 uom_probe.py
```

半自动流程：

```bash
python3 uom_semiauto.py
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

### 3. `uom_probe.py`
专门负责：
- 在不提交的前提下打开新增页
- 探测航空器/操控员弹层、组件结构、选择器状态

### 4. `uom_semiauto.py`
专门负责：
- 自动进入新增页
- 填入最近计划内容
- 输出 precheck / postcheck
- 让你人工确认
- 再尝试触发提交

## 当前状态

主线已经打通：
- 复用持久化 Playwright profile
- 检查主站登录态
- 进入 `运行管理 -> 飞行活动申请 -> 一般飞行活动`
- 从 iframe 提取 `PUB-Token / ticket / userName`
- 查询最近历史计划与详情
- 打开 `flyIndexAdd` 新增页
- 自动填入上次计划的大部分内容

当前主要阻塞点：
- 新增页前端校验仍可能卡在 `uavs` / `drivers`
- 页面虽然显示航空器和操控员行，但前端内部未必认定为“已选中”
- 因此当前还不能把“点到提交按钮”视为真正提交成功

## 当前建议阅读顺序

1. `uom_core.py`
   - 当前底层能力实现
2. `uom_login.py`
   - 登录/状态入口
3. `uom_probe.py`
   - 无副作用探测流程
4. `uom_semiauto.py`
   - 半自动提交流程
5. `HANDOFF_UOM_PROJECT.md`
6. `PROJECT_STATUS.md`
7. `SKILL.md`

## 当前现役文件

- `uom_core.py`
- `uom_login.py`
- `uom_probe.py`
- `uom_semiauto.py`
- `HANDOFF_UOM_PROJECT.md`
- `PROJECT_STATUS.md`
- `SKILL.md`
- `config.json`
- `config_temp.json`
- `.playwright-uom-profile/`（持久化浏览器目录，不要随意删除）

## 备注

- `SKILL.md` 是项目内最新说明文档，涉及脚本职责、默认命令、时间规则、阻塞点时优先看它
- 如果后续继续开发，优先围绕 `uom_core.py` 补底层能力；围绕 `uom_probe.py` / `uom_semiauto.py` 调整具体流程
