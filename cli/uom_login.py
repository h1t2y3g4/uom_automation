#!/usr/bin/env python3
"""
uom_login.py - UOM 登录/状态/浏览器入口

职责：
- 检查主站登录态
- 必要时执行短信登录
- 确保能进入一般飞行活动
- 读取最近计划
- 只打开持久化浏览器供人工检查

用法：
  python3 uom_login.py status
  python3 uom_login.py login
  python3 uom_login.py ensure-fly
  python3 uom_login.py latest-plan
  python3 uom_login.py open-browser
"""

import argparse
import json

import uom_core as core


def command_status(page):
    status = core.ensure_main_page(page, settle_seconds=3)
    print("主站状态:")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if status.get("hasMainLogin"):
        try:
            core.open_fly_activity(page)
            oapi = core.check_oapi_auth(page)
            print("飞行活动 oapi 状态:")
            print(json.dumps(oapi, ensure_ascii=False, indent=2)[:3000])
        except Exception as e:
            print("飞行活动检查失败:", e)


def command_login(page):
    status = core.ensure_main_page(page, settle_seconds=3)
    if status.get("hasMainLogin"):
        print("已复用登录态，无需重新登录")
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return
    result = core.login_via_sms(page)
    print("登录结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_ensure_fly(page):
    status = core.ensure_main_page(page, settle_seconds=3)
    if not status.get("hasMainLogin"):
        result = core.login_via_sms(page)
        print("登录结果:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    core.open_fly_activity(page)
    auth = core.get_iframe_auth(page)
    print("iframe 认证:")
    print(json.dumps(auth, ensure_ascii=False, indent=2))
    oapi = core.check_oapi_auth(page)
    print("oapi 检查:")
    print(json.dumps(oapi, ensure_ascii=False, indent=2)[:3000])


def command_latest_plan(page):
    status = core.ensure_main_page(page, settle_seconds=3)
    if not status.get("hasMainLogin"):
        result = core.login_via_sms(page)
        print("登录结果:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    core.open_fly_activity(page)
    latest = core.get_latest_plan(page)
    print("最近计划:")
    print(json.dumps(latest, ensure_ascii=False, indent=2))
    if latest.get('ok'):
        detail = core.get_plan_detail(page, latest['latest']['planId'])
        print("最近计划详情:")
        print(json.dumps(detail, ensure_ascii=False, indent=2)[:4000])


def command_open_browser(page):
    page.goto(f'{core.BASE_URL}/#/main', wait_until='domcontentloaded', timeout=120000)
    print('浏览器已打开。')
    print('请手动进入：运行管理 -> 飞行活动申请 -> 一般飞行活动')
    print(f'使用的持久化目录: {core.PERSIST_DIR}')
    print('操作完成后，回到终端按回车关闭浏览器。')
    input('按回车关闭...')


def build_parser():
    parser = argparse.ArgumentParser(description='UOM 登录/状态入口')
    parser.add_argument('command', nargs='?', default='help', choices=['help', 'status', 'login', 'ensure-fly', 'latest-plan', 'open-browser'])
    parser.add_argument('--headless', action='store_true')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == 'help':
        parser.print_help()
        return
    playwright_handle, context, page = core.launch_context(headless=args.headless)
    try:
        if args.command == 'status':
            command_status(page)
        elif args.command == 'login':
            command_login(page)
        elif args.command == 'ensure-fly':
            command_ensure_fly(page)
        elif args.command == 'latest-plan':
            command_latest_plan(page)
        elif args.command == 'open-browser':
            command_open_browser(page)
    finally:
        core.close_context(playwright_handle, context)


if __name__ == '__main__':
    main()
