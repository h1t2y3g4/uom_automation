#!/usr/bin/env python3
"""
uom_no_submit_probe.py - UOM 无副作用探测脚本

作用：
- 复用持久化浏览器登录态
- 自动进入一般飞行活动并打开新增页
- 不执行最终提交
- 重点探测航空器/操控员选择弹层与组件结构

适用场景：
- 想研究新增页内部组件结构
- 想观察选择弹层、表格、选择器状态
- 想尽量避免产生提交副作用时做调试
"""
import json
import time
from playwright.sync_api import sync_playwright
import uom_persistent as up

print('UOM 无副作用调试：探测新增页航空器/操控员选择弹层结构')

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(up.PERSIST_DIR),
        headless=False,
        viewport={'width': 1280, 'height': 900},
        args=['--no-sandbox', '--disable-dev-shm-usage'],
    )
    page = context.pages[0] if context.pages else context.new_page()

    page.goto(f'{up.BASE_URL}/#/main', wait_until='domcontentloaded', timeout=120000)
    time.sleep(8)
    up.dismiss_popup(page)
    status = up.check_main_login(page)
    print('主站状态:')
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if '#/login' in (status.get('url') or ''):
        print('当前落在登录页，停止无副作用调试，避免消耗短信验证码。')
        context.close()
        raise SystemExit(2)

    print('进入 一般飞行活动 ...')
    up.open_fly_activity(page)
    time.sleep(12)

    latest = up.get_latest_plan(page)
    if not latest.get('ok'):
        print('读取最近计划失败:')
        print(json.dumps(latest, ensure_ascii=False, indent=2))
        context.close()
        raise SystemExit(1)

    detail = up.get_plan_detail(page, latest['latest']['planId'])
    full_profile = up.load_full_submit_profile()
    if full_profile:
        detail['uavs'] = full_profile['uavs']
        detail['drivers'] = full_profile['drivers']

    next_beg, next_end = up.get_next_tuesday_same_time(detail['planBeg'], detail['planEnd'])
    print(f'目标时间: {next_beg} ~ {next_end}')

    add_res = up.open_new_fly_form(page)
    print('打开新增页:')
    print(json.dumps(add_res, ensure_ascii=False, indent=2))

    fly_add = up.wait_for_fly_add(page)
    print('等待新增页:')
    print(json.dumps(fly_add, ensure_ascii=False, indent=2)[:4000])
    if not fly_add:
        context.close()
        raise SystemExit(1)

    inspect = up.inspect_add_dialogs(page, detail, next_beg, next_end)
    print('弹层探测结果:')
    print(json.dumps(inspect, ensure_ascii=False, indent=2)[:12000])

    input('探测完成。按回车关闭浏览器...')
    context.close()
