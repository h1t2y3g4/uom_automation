# UOM 自动调度器

24小时后台运行的调度脚本，自动执行飞行计划提交和起飞确认。

## 功能

1. **每周日上午11点**：自动提交下周周一到周六的三江公园空域申请
   - 时间：17:50-19:00
   - 高度：120m
   - 自动重试机制（最多3次）

2. **每天凌晨4点**：检查计划并在起飞前1小时自动提交起飞确认
   - 持续监控，循环执行

3. **每小时检查**：扫描是否有即将起飞的计划需要确认

## 使用方法

### 手动运行

```bash
cd /home/skye/csy/uom_automation
python3 cli_auto/uom_scheduler.py
```

### 开机启动（systemd）

1. 复制 service 文件到 systemd 目录：
```bash
sudo cp cli_auto/uom-scheduler.service /etc/systemd/system/
```

2. 启用并启动服务：
```bash
sudo systemctl daemon-reload
sudo systemctl enable uom-scheduler
sudo systemctl start uom-scheduler
```

3. 查看状态和日志：
```bash
sudo systemctl status uom-scheduler
tail -f log/uom_scheduler.log
```

## 日志

- 调度器日志：`log/uom_scheduler.log`
- 提交计划日志：`log/manual_selection_log.json`
- 起飞确认日志：`log/takeoff_confirm_log.json`
- 计划详情缓存：`log/uom_recent_plan_details.json`

## 配置文件

- 计划模板：`config/submit_plan_demo1_sanjianggongyuan.json`
- 提交计划：`config/submit_plan.json`（由调度器自动生成）

## 停止服务

```bash
sudo systemctl stop uom-scheduler
```
