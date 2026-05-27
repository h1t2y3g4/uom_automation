#!/usr/bin/env python3
"""
uom_read_plan.py - 读取最近 5 条飞行计划详情并保存到文件

职责：
- 未登录时自动发送短信验证码登录主站
- 自动进入一般飞行活动页面
- 读取最近 5 条计划列表及详情
- 保存为本地 JSON 文件

用法：
  python3 uom_read_plan.py
  python3 uom_read_plan.py --output /path/to/output.json
  python3 uom_read_plan.py --headless
"""

import argparse
import json
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import core.uom_core as core


def build_parser():
    parser = argparse.ArgumentParser(description='读取最近 5 条飞行计划详情并保存到文件')
    parser.add_argument('--output', default=str(core.DEFAULT_RECENT_PLAN_DETAILS_FILE), help='输出 JSON 文件路径')
    parser.add_argument('--headless', action='store_true', help='无头模式运行浏览器')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    playwright_handle, context, page = core.launch_context(headless=args.headless)
    try:
        login_flow = core.ensure_main_login_with_auto_sms(page)
        status = login_flow.get('statusAfter') or {}
        print('主站状态:')
        print(json.dumps(status, ensure_ascii=False, indent=2))
        core.require_reliable_main_login(status, context, '当前不在可靠的主站已登录状态，自动登录后仍失败，停止读取计划流程。')
        print('进入 一般飞行活动 ...')
        core.open_fly_activity(page)
        core.time.sleep(6)
        result, err = core.fetch_recent_plan_details(page, limit=10)
        if err:
            print('读取最近计划详情失败:')
            print(json.dumps(err, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        filtered_result = core.filter_future_plan_details(result)
        saved_path = core.save_recent_plan_details(filtered_result, args.output)
        print('读取完成，结果摘要:')
        print(json.dumps({
            'ok': True,
            'count': filtered_result.get('count'),
            'output': str(saved_path),
            'planIds': [item.get('summary', {}).get('planId') for item in filtered_result.get('details', [])],
            'filter': filtered_result.get('filter'),
        }, ensure_ascii=False, indent=2))
    finally:
        core.close_context(playwright_handle, context)


if __name__ == '__main__':
    main()
