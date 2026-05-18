# UOM 自动化项目

项目目录：`/home/skye/hermes_interface/uom-automation`

## 当前状态

这个项目当前主线是：
- 持久化 Playwright 浏览器
- 尽量复用登录态
- 进入 UOM 的“一般飞行活动”业务页
- 读取最近计划
- 打开新增页并自动填入上次计划内容
- 继续攻克航空器/操控员在前端内部的真正选中状态

当前已经打通：
- 复用持久化浏览器 profile
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

1. `uom_persistent.py`
   - 当前主脚本
   - 负责持久化 profile、状态检查、进入业务页、读取最近计划、打开新增页、自动填表
2. `HANDOFF_UOM_PROJECT.md`
   - 最完整的项目交接说明，适合给其他 AI 或其他人快速接手
3. `PROJECT_STATUS.md`
   - 当前进度摘要，适合先快速看一遍
4. `uom_semiauto_submit.py`
   - 半自动调试脚本，用于定位 `uavs/drivers` 校验问题
5. `uom_no_submit_probe.py`
   - 无副作用探测脚本，用来观察新增页选择弹层结构
6. `open_uom_persistent_browser.py`
   - 用同一个持久化 profile 打开浏览器，方便人工检查页面状态

## 当前现役文件

- `uom_persistent.py`
- `uom_semiauto_submit.py`
- `uom_no_submit_probe.py`
- `open_uom_persistent_browser.py`
- `HANDOFF_UOM_PROJECT.md`
- `PROJECT_STATUS.md`
- `config.json`
- `config_temp.json`
- `.playwright-uom-profile/`（持久化浏览器目录，不要随意删除）

## 备注

- `SKILL.md` 是 Hermes skill 相关文件，不属于项目业务主说明文档，但里面保存了大量调试结论。
- 如果后续继续开发，优先围绕 `uom_persistent.py` 和 `uom_semiauto_submit.py` 推进。
