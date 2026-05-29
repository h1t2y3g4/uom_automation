#!/usr/bin/env python3
"""
UOM 自动调度器 - 24小时后台运行
功能：
1. 每周日上午11点：自动提交下周周一到周六的三江公园空域申请
2. 每天凌晨4点：检查计划并在起飞前1小时自动提交起飞确认
"""

import os
import sys
import json
import time
import signal
import logging
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path
from queue import Queue, Empty

# 添加项目根目录到 Python 路径
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
CLI_DIR = PROJECT_ROOT / "cli"
CONFIG_DIR = PROJECT_ROOT / "config"
LOG_DIR = PROJECT_ROOT / "log"

# 日志文件
SCHEDULER_LOG = LOG_DIR / "uom_scheduler.log"
PLAN_DETAILS_FILE = LOG_DIR / "uom_recent_plan_details.json"
SUBMIT_PLAN_FILE = CONFIG_DIR / "submit_plan.json"
TEMPLATE_FILE = CONFIG_DIR / "submit_plan_demo1_sanjianggongyuan.json"

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(SCHEDULER_LOG, mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class TaskQueue:
    """任务队列 - 确保任务顺序执行"""

    def __init__(self):
        self._queue = Queue()
        self._running = False
        self._thread = None

    def start(self):
        """启动任务处理线程"""
        self._running = True
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()
        logger.info("任务队列已启动")

    def stop(self):
        """停止任务处理"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def add_task(self, task_func, task_name="unnamed"):
        """添加任务到队列"""
        self._queue.put((task_func, task_name))
        logger.info(f"任务已加入队列: {task_name}")

    def _process_loop(self):
        """任务处理循环"""
        while self._running:
            try:
                task_func, task_name = self._queue.get(timeout=1)
                logger.info(f"开始执行任务: {task_name}")
                try:
                    task_func()
                    logger.info(f"任务完成: {task_name}")
                except Exception as e:
                    logger.error(f"任务失败: {task_name}, 错误: {e}", exc_info=True)
                finally:
                    self._queue.task_done()
            except Empty:
                continue


class UOMScheduler:
    """UOM 调度器主类"""

    def __init__(self):
        self.task_queue = TaskQueue()
        self._stop_event = threading.Event()
        self._scheduler_thread = None
        self._qq_receiver = None

    def start(self):
        """启动调度器"""
        logger.info("=" * 60)
        logger.info("UOM 调度器启动")
        logger.info(f"项目根目录: {PROJECT_ROOT}")
        logger.info("=" * 60)

        # 注册信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # 启动任务队列
        self.task_queue.start()

        # 启动 QQ 验证码接收器
        self._start_qq_receiver()

        # 启动调度线程
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._scheduler_thread.start()

        # 主线程等待
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self):
        """停止调度器"""
        logger.info("正在停止调度器...")
        self._stop_event.set()

        # 停止 QQ Bot
        if self._qq_receiver:
            self._qq_receiver.stop()

        self.task_queue.stop()
        logger.info("调度器已停止")

    def _start_qq_receiver(self):
        """启动 QQ 验证码接收器"""
        try:
            # 动态导入，避免未配置时也要求安装 botpy
            from core.config import load_config
            from core.qq_code_receiver import QQCodeReceiver

            config = load_config()
            qq_cfg = config.get('qq_bot', {})

            if not qq_cfg.get('enabled'):
                logger.info("QQ Bot 未启用（config.json 中 qq_bot.enabled=false），跳过")
                return

            appid = qq_cfg.get('appid', '')
            secret = qq_cfg.get('secret', '')

            if not appid or not secret:
                logger.warning("QQ Bot 配置不完整（缺少 appid 或 secret），跳过")
                return

            self._qq_receiver = QQCodeReceiver(appid=appid, secret=secret)
            self._qq_receiver.start()
            logger.info("QQ 验证码接收器已启动")

        except ImportError as e:
            logger.warning(f"缺少 botpy 依赖，QQ Bot 未启动: {e}")
        except Exception as e:
            logger.error(f"启动 QQ Bot 失败: {e}", exc_info=True)

    def _signal_handler(self, signum, frame):
        """信号处理"""
        logger.info(f"收到信号 {signum}，准备退出...")
        self.stop()

    def _scheduler_loop(self):
        """调度循环 - 每分钟检查一次"""
        logger.info("调度循环已启动，每分钟检查一次")

        while not self._stop_event.is_set():
            now = datetime.now()
            self._check_and_schedule(now)
            # 等待1分钟或直到停止信号
            self._stop_event.wait(60)

    def _check_and_schedule(self, now):
        """检查并调度任务"""
        weekday = now.weekday()  # 0=周一, 6=周日
        hour = now.hour
        minute = now.minute

        # 每周日上午11点：提交下周计划
        if weekday == 6 and hour == 11 and minute == 0:
            self.task_queue.add_task(
                self._task_submit_weekly_plan,
                "提交下周三江公园空域申请"
            )

        # 每天凌晨4点：检查并调度起飞确认
        if hour == 4 and minute == 0:
            self.task_queue.add_task(
                self._task_check_and_schedule_takeoff,
                "检查计划并调度起飞确认"
            )

        # 每小时检查一次是否有即将起飞的计划需要确认
        if minute == 0:
            self.task_queue.add_task(
                self._task_check_takeoff_now,
                f"检查起飞确认 (当前时间 {now.strftime('%H:%M')})"
            )

    def _run_cli_script(self, script_name, args=None, timeout=600):
        """运行 CLI 脚本（后台，等待完成）"""
        script_path = CLI_DIR / script_name
        cmd = [sys.executable, str(script_path), "--headless"]
        if args:
            cmd.extend(args)

        logger.info(f"运行脚本: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=timeout
            )

            if result.returncode == 0:
                logger.info(f"脚本执行成功: {script_name}")
                if result.stdout:
                    logger.debug(f"标准输出:\n{result.stdout}")
                return True
            else:
                logger.error(f"脚本执行失败: {script_name}, 返回码: {result.returncode}")
                if result.stderr:
                    logger.error(f"错误输出:\n{result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"脚本执行超时: {script_name}, 超时时间: {timeout}秒")
            return False
        except Exception as e:
            logger.error(f"脚本执行异常: {script_name}, 错误: {e}")
            return False

    def _generate_weekly_plans(self):
        """生成下周周一到周六的计划"""
        now = datetime.now()
        # 计算下周周一
        days_until_next_monday = (7 - now.weekday()) % 7
        if days_until_next_monday == 0:
            days_until_next_monday = 7
        next_monday = now + timedelta(days=days_until_next_monday)

        plans = []
        for i in range(6):  # 周一到周六
            date = next_monday + timedelta(days=i)
            plans.append({
                "planBeg": date.strftime('%Y-%m-%d') + " 17:50:00",
                "planEnd": date.strftime('%Y-%m-%d') + " 19:00:00",
                "airspace": {
                    "type": "common_ref",
                    "name": "三江公园"
                }
            })

        return plans

    def _write_submit_plan(self, plans):
        """写入 submit_plan.json"""
        data = {
            "_说明": "待提交飞行计划列表 - 由 uom_scheduler 自动生成",
            "timezone": "UTC+8",
            "plans": plans
        }

        with open(SUBMIT_PLAN_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"已写入 {SUBMIT_PLAN_FILE}, 共 {len(plans)} 条计划")

    def _check_submit_result(self):
        """检查提交结果，返回失败的计划列表（submit_plan.json 格式）"""
        log_file = LOG_DIR / "manual_selection_log.json"

        if not log_file.exists():
            logger.warning("提交日志文件不存在")
            return None

        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                log_data = json.load(f)

            status = log_data.get('status')
            if status == 'completed':
                success_count = log_data.get('successCount', 0)
                failure_count = log_data.get('failureCount', 0)
                total = log_data.get('total', 0)

                logger.info(f"提交结果: 总数={total}, 成功={success_count}, 失败={failure_count}")

                if failure_count == 0:
                    return []  # 全部成功

                # 收集失败的计划，转换为 submit_plan.json 格式
                failed_plans = []
                for item in log_data.get('items', []):
                    if item.get('status') in ['failed', 'submit_exception']:
                        plan = self._convert_submission_item_to_plan(item.get('submission_item'))
                        if plan:
                            failed_plans.append(plan)

                return failed_plans

            elif status in ['partial_failure', 'incomplete']:
                logger.warning(f"提交状态异常: {status}")
                # 收集未成功的计划
                failed_plans = []
                for item in log_data.get('items', []):
                    if item.get('status') not in ['post_submit_checked']:
                        plan = self._convert_submission_item_to_plan(item.get('submission_item'))
                        if plan:
                            failed_plans.append(plan)
                return failed_plans

            else:
                logger.warning(f"未知提交状态: {status}")
                return None

        except Exception as e:
            logger.error(f"读取提交日志失败: {e}")
            return None

    def _convert_submission_item_to_plan(self, submission_item):
        """将 submission_item 转换为 submit_plan.json 格式"""
        if not submission_item:
            return None

        return {
            "planBeg": submission_item.get("planBeg"),
            "planEnd": submission_item.get("planEnd"),
            "airspace": {
                "type": "common_ref",
                "name": submission_item.get("airspaceRefName") or submission_item.get("space", {}).get("name", "三江公园")
            }
        }


    def _task_submit_weekly_plan(self):
        """任务：提交下周三江公园空域申请"""
        logger.info("=" * 40)
        logger.info("开始执行：提交下周三江公园空域申请")
        logger.info("=" * 40)

        max_retries = 3
        retry_count = 0
        current_plans = None

        while retry_count < max_retries:
            if retry_count == 0:
                # 首次运行，生成并写入全部计划
                current_plans = self._generate_weekly_plans()
                self._write_submit_plan(current_plans)
            else:
                # 重试时，只写入失败的计划
                logger.info(f"第 {retry_count + 1} 次重试，只提交失败的计划")
                if current_plans:
                    self._write_submit_plan(current_plans)

            # 运行提交脚本
            success = self._run_cli_script(
                "uom_submit_fly_plan.py",
                ["--use-submit-plan"],
                timeout=900  # 15分钟超时
            )

            if not success:
                logger.error("提交脚本执行失败")
                retry_count += 1
                time.sleep(30)  # 等待30秒后重试
                continue

            # 检查提交结果
            failed_plans = self._check_submit_result()

            if failed_plans is None:
                logger.warning("无法确定提交结果，将重试")
                retry_count += 1
                time.sleep(30)
                continue

            if len(failed_plans) == 0:
                logger.info("所有计划提交成功！")
                return

            # 更新当前计划为失败的计划
            current_plans = failed_plans
            logger.warning(f"还有 {len(failed_plans)} 条计划未成功，准备重试")
            retry_count += 1
            time.sleep(30)

        logger.error(f"重试 {max_retries} 次后仍有计划未成功")

    def _task_check_and_schedule_takeoff(self):
        """任务：检查计划并调度起飞确认（每天凌晨4点执行）"""
        logger.info("=" * 40)
        logger.info("开始执行：检查计划并调度起飞确认")
        logger.info("=" * 40)

        # 直接读取本地计划文件（不登录，不调脚本）
        if not PLAN_DETAILS_FILE.exists():
            logger.warning("计划详情文件不存在，跳过")
            return

        try:
            with open(PLAN_DETAILS_FILE, 'r', encoding='utf-8') as f:
                plan_data = json.load(f)

            plans = plan_data.get('details', [])
            now = datetime.now()

            for plan in plans:
                summary = plan.get('summary', {})
                plan_beg_str = summary.get('planBeg') or summary.get('planBegStr')

                if not plan_beg_str:
                    continue

                # 解析计划开始时间
                try:
                    plan_beg = datetime.strptime(plan_beg_str, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    try:
                        plan_beg = datetime.strptime(plan_beg_str, '%Y-%m-%dT%H:%M:%S')
                    except ValueError:
                        logger.warning(f"无法解析计划时间: {plan_beg_str}")
                        continue

                # 计算起飞前1小时的时间点
                confirm_time = plan_beg - timedelta(hours=1)

                # 如果确认时间已过但计划时间未到，立即安排
                if confirm_time <= now < plan_beg:
                    logger.info(f"计划 {summary.get('planId')} 已到确认时间，立即安排起飞确认")
                    self.task_queue.add_task(
                        self._task_submit_takeoff_confirm,
                        f"提交起飞确认 (计划ID: {summary.get('planId')})"
                    )
                elif confirm_time > now:
                    # 计算需要等待的秒数
                    wait_seconds = (confirm_time - now).total_seconds()
                    logger.info(f"计划 {summary.get('planId')} 将在 {confirm_time} 需要确认，等待 {wait_seconds:.0f} 秒")

                    # 使用定时器在指定时间执行
                    timer = threading.Timer(
                        wait_seconds,
                        lambda pid=summary.get('planId'): self.task_queue.add_task(
                            self._task_submit_takeoff_confirm,
                            f"提交起飞确认 (计划ID: {pid})"
                        )
                    )
                    timer.daemon = True
                    timer.start()

        except Exception as e:
            logger.error(f"处理计划数据失败: {e}", exc_info=True)

    def _task_check_takeoff_now(self):
        """任务：检查当前是否有需要立即确认的计划"""
        if not PLAN_DETAILS_FILE.exists():
            return

        try:
            with open(PLAN_DETAILS_FILE, 'r', encoding='utf-8') as f:
                plan_data = json.load(f)

            plans = plan_data.get('details', [])
            now = datetime.now()

            for plan in plans:
                summary = plan.get('summary', {})
                plan_beg_str = summary.get('planBeg') or summary.get('planBegStr')

                if not plan_beg_str:
                    continue

                # 解析计划开始时间
                try:
                    plan_beg = datetime.strptime(plan_beg_str, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    continue

                # 检查是否在起飞前1小时内
                time_until_takeoff = (plan_beg - now).total_seconds() / 60

                if 0 <= time_until_takeoff <= 60:
                    # 检查是否已确认
                    takeoffs = summary.get('takeoffs', [])
                    if not takeoffs:
                        logger.info(f"发现需要确认的计划: {summary.get('planId')}, 起飞时间: {plan_beg_str}")
                        self.task_queue.add_task(
                            self._task_submit_takeoff_confirm,
                            f"提交起飞确认 (计划ID: {summary.get('planId')})"
                        )

        except Exception as e:
            logger.error(f"检查起飞确认失败: {e}")

    def _task_submit_takeoff_confirm(self):
        """任务：提交起飞确认"""
        logger.info("=" * 40)
        logger.info("开始执行：提交起飞确认")
        logger.info("=" * 40)

        success = self._run_cli_script(
            "uom_takeoff_confirm.py",
            ["--window-minutes", "60"],
            timeout=300
        )

        if success:
            logger.info("起飞确认提交成功")
        else:
            logger.error("起飞确认提交失败")


def main():
    """主入口"""
    scheduler = UOMScheduler()
    scheduler.start()


if __name__ == "__main__":
    main()
