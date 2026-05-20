# UOM 提交配置拆分与经纬度统一方案改造清单

> **For Hermes:** 这是实现计划文档，不是交接背景文档。执行时优先按本文分步改造；涉及历史原因、页面坑点、认证上下文，再回看 `doc/HANDOFF_UOM_PROJECT.md`。

**Goal:** 将 UOM 自动提交脚本从“`config.json` 同时承载全局配置 + 时间列表 + 空域预设”的结构，改为“全局配置 / 常用空域缓存 / 待提交计划列表”三文件拆分，并统一空域填充路径为“最终都写入经纬度空域数据”。

**Architecture:** 保留现有“持久化浏览器 + 读取最近计划 + 打开新增页 + 真实前端提交”的主线，不再实现“常用空域 UI 点选”。无论待提交项是否引用常用空域，提交前都先解析成统一的 `space` 对象（含 `polygonWgs84 / spcBottom / spcTop`），再沿用现有表单空域注入逻辑写入页面。CLI 直接传时间时，保底从本地 `airspace.json` 的第一个常用空域取经纬度。

**Tech Stack:** Python 3, Playwright, JSON 配置文件, `core/uom_core.py`, `cli/uom_submit_fly_plan.py`

---

## 一、范围与目标确认

### 本次要实现的行为

1. `config/config.json` 只保留稳定全局配置：认证、联系人、无人机、操控员、默认参数。
2. 新建 `config/airspace.json`：本地缓存常用空域，供提交前按名称换算成经纬度空域对象。
3. 新建 `config/submit_plan.json`：保存待提交列表；每项同时包含时间和空域描述。
4. `cli/uom_submit_fly_plan.py` 不再读取 `config.json.time.pairs` 和 `config.json.airspaces`。
5. 所有提交流程统一走“经纬度空域注入”：
   - 如果待提交项写的是常用空域名，则从 `airspace.json` 找对应经纬度
   - 如果待提交项直接写了经纬度，则直接使用
6. CLI 仅传 `--start-utc-ts --end-utc-ts` 时：
   - 不点选常用空域
   - 不复制最近计划空域
   - 直接读取 `airspace.json` 的第一个空域项作为保底经纬度

### 本次明确不做的内容

1. 不实现“常用空域能否复用”的 AI 判断逻辑。
2. 不实现“打开 UOM 常用空域弹窗并自动点选”的 UI 流程。
3. 不在本轮实现“从 UOM 在线同步常用空域列表到 `airspace.json`”的抓取器。
4. 不扩展圆形、折线等新空域类型；先只覆盖当前已有的多边形/面空域数据结构。

---

## 二、改造后的文件职责

### 1. `config/config.json`

**职责：** 只保留稳定、不随单次提交批次变化的主配置。

**保留字段：**
- `auth`
- `contact`
- `drone`
- `driver`
- `plan_defaults`

**从这里移除：**
- `time`
- `airspaces`

**建议结构：**

```json
{
  "_说明": "UOM 自动化主配置，存稳定身份与默认参数",
  "auth": {},
  "contact": {},
  "drone": {},
  "driver": {},
  "plan_defaults": {
    "planType": "11",
    "spaceType": "3",
    "taskType": "8",
    "spcTop": 120,
    "six": "00000",
    "remark": "",
    "timezone": "UTC+8"
  }
}
```

> 注意：`timezone` 从旧的 `time.timezone` 挪到 `plan_defaults.timezone`。

---

### 2. `config/airspace.json`

**职责：** 本地常用空域缓存；只做“名称 -> 经纬度空域对象”的查询来源。

**建议结构：**

```json
{
  "_说明": "本地常用空域缓存，供提交脚本按名称换算经纬度",
  "updatedAt": "2026-05-20 00:00:00",
  "items": [
    {
      "name": "北辰鹿鸣院空域A",
      "spcName": "北辰鹿鸣院空域A",
      "groupName": "空域1",
      "polygonWgs84": "104.01902676,30.52132641|104.01574373,30.51483813|104.02269602,30.51310046|104.02413368,30.51722276",
      "spcBottom": 0,
      "spcTop": 120,
      "spaceShape": "面",
      "spcShape": "1"
    }
  ]
}
```

**最低必填字段：**
- `name`
- `polygonWgs84`
- `spcBottom`
- `spcTop`

**建议保留字段：**
- `spcName`
- `groupName`
- `spaceShape`
- `spcShape`

**约定：**
- `items[0]` 是 CLI 仅传时间时的保底空域来源。
- 若 `items` 为空，应明确报错，不要静默退回最近计划空域。

---

### 3. `config/submit_plan.json`

**职责：** 待提交批次列表；替代旧的 `config.json.time.pairs`。

**建议结构：**

```json
{
  "_说明": "待提交飞行计划列表",
  "timezone": "UTC+8",
  "plans": [
    {
      "planBeg": "2026-05-21 10:00:00",
      "planEnd": "2026-05-21 11:00:00",
      "airspace": {
        "type": "common_ref",
        "name": "北辰鹿鸣院空域A"
      }
    },
    {
      "planBeg": "2026-05-22 10:00:00",
      "planEnd": "2026-05-22 11:00:00",
      "airspace": {
        "type": "polygon",
        "space": {
          "polygonWgs84": "104.01902676,30.52132641|104.01574373,30.51483813|104.02269602,30.51310046|104.02413368,30.51722276",
          "spcBottom": 0,
          "spcTop": 120,
          "groupName": "空域1",
          "spcName": "临时空域1",
          "spaceShape": "面",
          "spcShape": "1"
        }
      }
    }
  ]
}
```

**约定：**
- `airspace.type = common_ref` 表示按名称去 `airspace.json` 查经纬度。
- `airspace.type = polygon` 表示本条直接自带经纬度空域对象。
- 本次不再支持 `config.json.airspaces.default` 这种旧入口。

---

## 三、内部数据模型改造

### 目标

把现在脚本内部的“只解析时间对”的模型，升级成“完整提交项（时间 + 空域）”模型。

### 新的内部统一结构（建议）

无论来源是 CLI 直接传时间，还是 `submit_plan.json`，最终都归一成：

```python
{
    "planBeg": "2026-05-21 10:00:00",
    "planEnd": "2026-05-21 11:00:00",
    "source": "cli_utc_pair | submit_plan_file | latest_plus_one_day",
    "airspaceSource": "airspace_cache | submit_plan_inline | fallback_first_cached_airspace",
    "airspaceRefName": "北辰鹿鸣院空域A",
    "space": {
        "polygonWgs84": "...",
        "locationWgs84": "...",
        "spcBottom": 0,
        "spcTop": 120,
        "spcName": "...",
        "groupName": "...",
        "spaceShape": "面",
        "spcShape": "1"
    }
}
```

### 关键原则

1. **进入提交流程前就必须把空域解析成最终 `space` 对象。**
2. UI 层不再关心“这是常用空域引用还是直接写的坐标”。
3. `fill_new_form_from_detail()` 或其后继函数只接收已经标准化好的 `space`。

---

## 四、需要修改的代码点

## Task 1：增加新的配置文件路径常量

**Objective:** 在核心模块中为新配置文件建立统一路径常量，避免路径散落在 CLI 脚本里。

**Files:**
- Modify: `uom-automation/core/uom_core.py`

**Step 1: 在常量区新增路径**

在现有：
- `CONFIG_FILE = PROJECT_ROOT / "config" / "config.json"`

附近新增：

```python
AIRSPACE_FILE = PROJECT_ROOT / "config" / "airspace.json"
SUBMIT_PLAN_FILE = PROJECT_ROOT / "config" / "submit_plan.json"
```

**Step 2: 保留旧常量不删除**

`CONFIG_FILE` 继续保留，因为主配置仍要从 `config.json` 读取。

**Step 3: 验证**

运行：
```bash
python3 -m py_compile core/uom_core.py
```

Expected: 无输出，编译成功。

---

## Task 2：调整时区读取逻辑

**Objective:** 将时区读取入口从 `config.time.timezone` 迁移到新结构，并兼容旧配置一小段过渡期。

**Files:**
- Modify: `uom-automation/core/uom_core.py`

**Step 1: 修改 `get_timezone_from_config(cfg)`**

当前逻辑读的是：

```python
tz_name = cfg.get('time', {}).get('timezone', 'UTC+8')
```

改成：

```python
tz_name = (
    cfg.get('plan_defaults', {}).get('timezone')
    or cfg.get('time', {}).get('timezone')
    or 'UTC+8'
)
```

**Step 2: 说明兼容策略**

- 新配置优先：`plan_defaults.timezone`
- 旧配置兜底：`time.timezone`
- 最终默认：`UTC+8`

**Step 3: 验证**

运行：
```bash
python3 -m py_compile core/uom_core.py
```

---

## Task 3：新增配置读取函数

**Objective:** 为 `airspace.json` 和 `submit_plan.json` 提供统一读入口。

**Files:**
- Modify: `uom-automation/core/uom_core.py`

**Step 1: 新增读取函数**

建议新增：

```python
def load_airspace_cache():
    with open(AIRSPACE_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_submit_plan():
    with open(SUBMIT_PLAN_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)
```

**Step 2: 可以额外增加“文件不存在”报错包装**

建议抛出更清晰的错误，例如：
- `airspace.json 不存在，请先创建 config/airspace.json`
- `submit_plan.json 不存在，请先创建 config/submit_plan.json`

**Step 3: 验证**

运行：
```bash
python3 -m py_compile core/uom_core.py
```

---

## Task 4：新增空域标准化与校验函数

**Objective:** 把所有来源的空域数据统一成前端注入所需格式。

**Files:**
- Modify: `uom-automation/core/uom_core.py`

**Step 1: 新增标准化函数**

建议新增：

```python
def normalize_space_payload(space, default_top=None):
    if not isinstance(space, dict):
        raise ValueError(f'space 必须是对象: {space!r}')

    polygon = space.get('polygonWgs84') or space.get('locationWgs84')
    if not polygon:
        raise ValueError(f'space 缺少 polygonWgs84/locationWgs84: {space!r}')

    spc_bottom = space.get('spcBottom', 0)
    spc_top = space.get('spcTop', default_top if default_top is not None else 120)

    return {
        **space,
        'locationWgs84': polygon,
        'polygonWgs84': polygon,
        'spcBottom': spc_bottom,
        'spcTop': spc_top,
        'spcName': space.get('spcName', ''),
        'groupName': space.get('groupName', '空域1'),
        'spaceShape': space.get('spaceShape', '面'),
        'spcShape': space.get('spcShape', '1'),
    }
```

**Step 2: 保持最小范围**

本轮不处理复杂几何，只做当前已有字段标准化。

**Step 3: 验证**

运行：
```bash
python3 -m py_compile core/uom_core.py
```

---

## Task 5：新增从 `airspace.json` 查空域的函数

**Objective:** 支持 `submit_plan.json` 通过名称引用本地常用空域缓存。

**Files:**
- Modify: `uom-automation/core/uom_core.py`

**Step 1: 新增按名称查找函数**

建议新增：

```python
def find_cached_airspace_by_name(airspace_cache, name):
    items = airspace_cache.get('items', []) if isinstance(airspace_cache, dict) else []
    for item in items:
        if item.get('name') == name:
            return item
    raise ValueError(f'airspace.json 中找不到常用空域: {name}')
```

**Step 2: 新增取第一个空域的保底函数**

```python
def get_first_cached_airspace(airspace_cache):
    items = airspace_cache.get('items', []) if isinstance(airspace_cache, dict) else []
    if not items:
        raise ValueError('airspace.json 的 items 为空，无法作为保底空域来源')
    return items[0]
```

**Step 3: 验证**

运行：
```bash
python3 -m py_compile core/uom_core.py
```

---

## Task 6：把 `resolve_time_pairs()` 重构为“解析完整提交项”

**Objective:** 用新的统一入口替代旧的纯时间列表解析。

**Files:**
- Modify: `uom-automation/core/uom_core.py`
- Modify: `uom-automation/cli/uom_submit_fly_plan.py`

**Step 1: 保留旧函数名还是换新名**

建议直接新增新函数，避免边改边炸：

```python
def resolve_submission_items(cfg, latest_detail, cli_args):
    ...
```

等主流程改完后，再决定是否删除 `resolve_time_pairs()`。

**Step 2: 新函数支持三类来源**

### A. CLI 直接传时间
如果传了：
- `--start-utc-ts`
- `--end-utc-ts`

则：
1. 按时区换算出 `planBeg / planEnd`
2. 读取 `airspace.json`
3. 取 `items[0]`
4. 标准化成 `space`
5. 返回单条 submission item

建议返回结构：

```python
{
    'planBeg': plan_beg,
    'planEnd': plan_end,
    'source': 'cli_utc_pair',
    'startUtcTs': int(cli_args.start_utc_ts),
    'endUtcTs': int(cli_args.end_utc_ts),
    'airspaceSource': 'fallback_first_cached_airspace',
    'airspaceRefName': first_airspace.get('name'),
    'space': normalized_space,
}
```

### B. 批量读取 `submit_plan.json`
如果传批量模式参数，则：
1. 读取 `submit_plan.json`
2. 遍历 `plans`
3. 对每项分别解析时间与空域
4. 返回列表

### C. 默认无参数
保持当前时间默认逻辑：
- 时间 = 最近计划 + 1 天

但空域不再复制最近计划；改为：
1. 读取 `airspace.json`
2. 取第一个空域
3. 生成单条 submission item

**Step 3: 暂时不要混入“最近计划空域复制”**

本轮既然已决定统一走经纬度，就不要在默认分支里再引用 `latest_detail.spaces[0]` 作为空域来源，避免语义混乱。

**Step 4: 验证**

执行 dry-run 前，先只做语法检查：

```bash
python3 -m py_compile core/uom_core.py cli/uom_submit_fly_plan.py
```

---

## Task 7：实现 `submit_plan.json` 单项解析逻辑

**Objective:** 将 `submit_plan.json` 每项转成最终可提交结构。

**Files:**
- Modify: `uom-automation/core/uom_core.py`

**Step 1: 新增单项解析函数**

建议新增：

```python
def normalize_submission_plan_item(item, cfg, airspace_cache):
    if not isinstance(item, dict):
        raise ValueError(f'submit plan item 必须是对象: {item!r}')

    plan_beg = item.get('planBeg')
    plan_end = item.get('planEnd')
    if not plan_beg or not plan_end:
        raise ValueError(f'submit plan item 缺少 planBeg/planEnd: {item!r}')

    airspace = item.get('airspace')
    if not isinstance(airspace, dict):
        raise ValueError(f'submit plan item 缺少 airspace 对象: {item!r}')

    airspace_type = airspace.get('type')
    if airspace_type == 'common_ref':
        ref_name = airspace.get('name')
        if not ref_name:
            raise ValueError(f'common_ref 缺少 name: {item!r}')
        cached = find_cached_airspace_by_name(airspace_cache, ref_name)
        space = normalize_space_payload(cached, default_top=cfg.get('plan_defaults', {}).get('spcTop'))
        return {
            'planBeg': plan_beg,
            'planEnd': plan_end,
            'source': 'submit_plan_file',
            'airspaceSource': 'airspace_cache',
            'airspaceRefName': ref_name,
            'space': space,
        }

    if airspace_type == 'polygon':
        inline_space = airspace.get('space')
        space = normalize_space_payload(inline_space, default_top=cfg.get('plan_defaults', {}).get('spcTop'))
        return {
            'planBeg': plan_beg,
            'planEnd': plan_end,
            'source': 'submit_plan_file',
            'airspaceSource': 'submit_plan_inline',
            'airspaceRefName': None,
            'space': space,
        }

    raise ValueError(f'不支持的 airspace.type: {airspace_type!r}')
```

**Step 2: 不做 AI 判定**

严格按显式配置执行：
- `common_ref` 就去缓存里找
- `polygon` 就直接用内联经纬度

**Step 3: 验证**

语法检查：
```bash
python3 -m py_compile core/uom_core.py
```

---

## Task 8：调整 CLI 参数语义

**Objective:** 废弃旧的 `--use-time-list` 语义，迁移为读取 `submit_plan.json`。

**Files:**
- Modify: `uom-automation/cli/uom_submit_fly_plan.py`

**Step 1: 参数层建议**

建议新增：

```python
parser.add_argument('--use-submit-plan', action='store_true', help='按 submit_plan.json 列表循环提交')
```

**Step 2: 保留旧参数兼容别名**

旧参数：

```python
parser.add_argument('--use-time-list', action='store_true', help='按 config.json 中的 time.pairs 循环提交')
```

建议改 help 文案，但先保留参数本身：

```python
parser.add_argument('--use-time-list', action='store_true', help='兼容旧参数；实际改为读取 submit_plan.json')
```

**Step 3: 主流程里统一判定**

满足以下任意条件时进入批量文件模式：
- `args.use_submit_plan`
- `args.use_time_list`

**Step 4: dry-run 输出里打印兼容提示（可选）**

例如：
- `--use-time-list 已废弃，当前按 submit_plan.json 处理`

---

## Task 9：把主循环从 `time_pairs` 改为 `submission_items`

**Objective:** 让提交脚本主流程围绕“完整提交项”运行。

**Files:**
- Modify: `uom-automation/cli/uom_submit_fly_plan.py`

**Step 1: 主流程改变量名**

当前：

```python
time_pairs = core.resolve_time_pairs(cfg, detail, args)
```

改为：

```python
submission_items = core.resolve_submission_items(cfg, detail, args)
```

**Step 2: 打印摘要时输出空域来源**

建议新增辅助描述函数，例如：

```python
def describe_submission_item(item, index=None):
    prefix = f'[{index}] ' if index is not None else ''
    return (
        f"{prefix}{item['planBeg']} ~ {item['planEnd']} "
        f"({item.get('source')}) | airspace={item.get('airspaceSource')}"
        f" | ref={item.get('airspaceRefName') or '-'}"
    )
```

dry-run 与正常运行都用它打印。

**Step 3: `run_single_submission()` 参数改造**

当前签名：

```python
def run_single_submission(page, detail, time_pair, drone_name, drone_uas_code, driver_name, pair_index, pair_total):
```

建议改为：

```python
def run_single_submission(page, detail, submission_item, drone_name, drone_uas_code, driver_name, pair_index, pair_total):
```

函数内部：
- `next_beg = submission_item['planBeg']`
- `next_end = submission_item['planEnd']`
- `resolved_space = submission_item['space']`

---

## Task 10：改造表单填充函数，接收显式 `space`

**Objective:** 让空域注入不再依赖 `detail.spaces[0]`。

**Files:**
- Modify: `uom-automation/core/uom_core.py`
- Modify: `uom-automation/cli/uom_submit_fly_plan.py`

**Step 1: 调整函数签名**

当前：

```python
def fill_new_form_from_detail(page, detail, plan_beg_new, plan_end_new):
```

建议改为：

```python
def fill_new_form_from_detail(page, detail, plan_beg_new, plan_end_new, space_payload):
```

**Step 2: 页面内 JS 逻辑改来源**

当前代码里有：

```javascript
const sourceSpace = (detail.spaces && detail.spaces[0]) || {};
```

建议改成：

```javascript
const sourceSpace = spacePayload || {};
```

然后 `page.evaluate` 的参数从：

```python
[detail, plan_beg_new, plan_end_new]
```

改为：

```python
[detail, plan_beg_new, plan_end_new, space_payload]
```

对应 JS 签名改成：

```javascript
async ([detail, planBegNew, planEndNew, spacePayload]) => {
```

**Step 3: 保留现有注入逻辑主体**

已有这段空域注入逻辑可以继续用，只改数据源：
- `locationWgs84`
- `polygonWgs84`
- `spcBottom`
- `spcTop`
- `spcName`
- `groupName`
- `spaceShape`
- `spcShape`

**Step 4: 调用方改传 `submission_item['space']`**

在 `run_single_submission()` 中调用：

```python
fill = core.fill_new_form_from_detail(page, detail, next_beg, next_end, resolved_space)
```

---

## Task 11：升级 dry-run 输出

**Objective:** 让 dry-run 能同时验证时间与空域解析结果。

**Files:**
- Modify: `uom-automation/cli/uom_submit_fly_plan.py`

**Step 1: dry-run 不只打印时间**

当前 dry-run 文案偏向“只完成时间解析”。

建议改成打印每条 submission item 的摘要，例如：

```json
{
  "planBeg": "2026-05-21 10:00:00",
  "planEnd": "2026-05-21 11:00:00",
  "source": "submit_plan_file",
  "airspaceSource": "airspace_cache",
  "airspaceRefName": "北辰鹿鸣院空域A",
  "spacePreview": {
    "polygonWgs84": "104.0190...,30.5213...|...",
    "spcBottom": 0,
    "spcTop": 120,
    "groupName": "空域1",
    "spcName": "北辰鹿鸣院空域A"
  }
}
```

**Step 2: 明确 dry-run 成功语义**

建议文案：
- 已完成登录态检查
- 已读取最近计划
- 已解析待提交时间与空域
- 未进入新增页、未触发提交

---

## Task 12：升级提交日志结构

**Objective:** 让日志明确记录本次提交用的是哪条空域来源。

**Files:**
- Modify: `uom-automation/cli/uom_submit_fly_plan.py`

**Step 1: 日志里新增完整提交项**

当前日志存：
- `target_time`

建议改为：
- `submission_item`

例如：

```python
'submission_item': submission_item,
```

**Step 2: 保留兼容字段（可选）**

如果担心老脚本看日志字段名，可以临时保留：

```python
'target_time': {
    'planBeg': submission_item['planBeg'],
    'planEnd': submission_item['planEnd'],
},
```

但新主字段应是 `submission_item`。

**Step 3: 在日志中附加空域摘要（可选）**

可额外记录：
- `airspaceSource`
- `airspaceRefName`
- `spacePreview`

---

## Task 13：更新模板与文档

**Objective:** 让项目内示例配置与说明同步新结构。

**Files:**
- Modify: `uom-automation/config/config.json`（本地实际文件，谨慎改）
- Modify: `uom-automation/config/config.example.json`（如存在）
- Modify: `uom-automation/SKILL.md`
- Modify: `uom-automation/doc/HANDOFF_UOM_PROJECT.md`
- Create: `uom-automation/config/airspace.json`
- Create: `uom-automation/config/submit_plan.json`

**Step 1: 更新示例配置**

如果项目里有模板文件，优先让模板反映新结构。

**Step 2: 更新 `SKILL.md` 的命令说明**

需要把旧描述：
- `--use-time-list` 读取 `config.json.time.pairs`

改成新描述：
- `--use-submit-plan` / `--use-time-list(兼容别名)` 读取 `config/submit_plan.json`
- `config/airspace.json` 提供本地常用空域缓存

**Step 3: 更新交接文档中的配置说明**

`HANDOFF_UOM_PROJECT.md` 里凡是写到：
- `config.json` 同时存时间和空域
- `time.pairs`
- `airspaces.default`

的地方，都要同步改成三文件职责。

---

## 五、推荐实施顺序

### Phase 1：数据结构改造（不碰页面逻辑）

1. 新增 `AIRSPACE_FILE / SUBMIT_PLAN_FILE`
2. 新增 `load_airspace_cache / load_submit_plan`
3. 新增 `normalize_space_payload`
4. 新增 `find_cached_airspace_by_name / get_first_cached_airspace`
5. 新增 `normalize_submission_plan_item / resolve_submission_items`
6. 调整 `get_timezone_from_config`

**验收标准：**
- 能在 dry-run 模式下正确打印 submission items
- 不进入页面填写也能验证 JSON 结构无误

---

### Phase 2：提交流程切换到新模型

1. `uom_submit_fly_plan.py` 改用 `submission_items`
2. `run_single_submission()` 接收完整提交项
3. `fill_new_form_from_detail()` 增加 `space_payload`
4. 页面内空域数据源从 `detail.spaces[0]` 改为 `space_payload`

**验收标准：**
- dry-run 正常
- 真运行时，新增页空域来源已不再依赖最近计划详情空域

---

### Phase 3：配置与文档收尾

1. 清理 `config.json` 中的 `time` / `airspaces`
2. 新建 `airspace.json`
3. 新建 `submit_plan.json`
4. 更新 `SKILL.md`
5. 更新 `HANDOFF_UOM_PROJECT.md`

**验收标准：**
- 新人只看配置文件和 skill 就能理解如何填写批量提交计划

---

## 六、验证清单

### 语法验证

```bash
python3 -m py_compile core/uom_core.py cli/uom_submit_fly_plan.py
```

### 配置解析验证

#### 1. 仅传时间（保底第一个常用空域）

```bash
python3 cli/uom_submit_fly_plan.py --start-utc-ts 1779616200 --end-utc-ts 1779618600 --dry-run
```

**预期：**
- 解析出 1 条 submission item
- `source = cli_utc_pair`
- `airspaceSource = fallback_first_cached_airspace`
- `airspaceRefName = airspace.json 第一项的 name`

#### 2. 读取 submit_plan.json（引用常用空域）

```bash
python3 cli/uom_submit_fly_plan.py --use-submit-plan --dry-run
```

**预期：**
- 能看到 `common_ref` 项被解析为 `airspace_cache`
- `spacePreview` 含经纬度

#### 3. 读取 submit_plan.json（直接写经纬度）

```bash
python3 cli/uom_submit_fly_plan.py --use-submit-plan --dry-run
```

**预期：**
- `polygon` 项被解析为 `submit_plan_inline`
- 不依赖 `airspace.json` 名称匹配

### 页面执行验证

#### 4. 单条真实提交前验证

```bash
python3 cli/uom_submit_fly_plan.py --start-utc-ts 1779616200 --end-utc-ts 1779618600
```

**观察点：**
- 新增页能正常打开
- 航空器/操控员仍能被正确选择
- 空域信息已注入
- precheck/postcheck 快照里 `spaceCount >= 1`

#### 5. 批量模式真实验证

```bash
python3 cli/uom_submit_fly_plan.py --use-submit-plan
```

**观察点：**
- 每条计划都使用各自解析后的 `space`
- 日志里能看到不同 `airspaceSource`

---

## 七、风险与注意事项

### 1. 不要再混用“最近计划空域复制”和“新提交项空域解析”

既然本轮目标已经改成统一走经纬度，主流程就应该只有一个空域入口：`submission_item['space']`。

### 2. `airspace.json` 为空时必须报错

不能静默 fallback 到最近计划空域，否则会让保底逻辑不可预测。

### 3. `submit_plan.json` 的时间建议先只支持本地时间字符串

本轮不要再把 UTC 时间戳批量格式塞进 `submit_plan.json`，避免时间来源混乱。
CLI 传时间戳的能力保留在命令行参数层即可。

### 4. 页面注入逻辑只改数据源，不轻易重写整个表单填充流程

现有航空器/操控员选择链路已经跑通，本轮应尽量少碰它，只改空域来源入口。

---

## 八、最终交付结果

改造完成后，应具备以下用户视角能力：

1. 可以把稳定配置继续放在 `config/config.json`
2. 可以在 `config/airspace.json` 维护本地常用空域经纬度缓存
3. 可以在 `config/submit_plan.json` 里写一个待提交列表
4. 列表里的空域既可以：
   - 通过常用空域名引用本地缓存
   - 也可以直接写经纬度
5. 脚本最终总是把空域转成经纬度对象注入页面
6. 直接传 CLI 时间时，自动使用 `airspace.json` 第一个空域作为保底空域

---

## 九、建议的提交粒度

如果实际开始改代码，建议按下面粒度提交：

1. `refactor: split UOM config into main config, airspace cache, and submit plan`
2. `feat: resolve submission items with normalized space payloads`
3. `refactor: submit fly plan flow to consume explicit space payloads`
4. `docs: update UOM config and submit plan documentation`
