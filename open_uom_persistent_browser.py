#!/usr/bin/env python3
"""
open_uom_persistent_browser.py - 打开 UOM 持久化浏览器供人工检查

作用：
- 使用与主线脚本相同的 Playwright 持久化 profile
- 直接打开 UOM 主站页面
- 方便人工确认当前登录态、菜单状态、业务页状态

适用场景：
- 想确认持久化 profile 是否还登录着
- 想人工进入“一般飞行活动”页面再配合其他脚本调试
- 不想在这个脚本里执行提交，只想人工观察页面
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = 'https://uom.caac.gov.cn/#/main'
PERSIST = Path('/home/skye/hermes_interface/uom-automation/.playwright-uom-profile')

print('将打开 UOM 持久化浏览器。')
print('请手动进入：运行管理 -> 飞行活动申请 -> 一般飞行活动')
print(f'使用的持久化目录: {PERSIST}')

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PERSIST),
        headless=False,
        viewport={'width': 1280, 'height': 900},
        args=['--no-sandbox', '--disable-dev-shm-usage'],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(BASE, wait_until='domcontentloaded', timeout=120000)
    print('浏览器已打开。')
    print('操作完成后，回到终端按回车关闭浏览器。')
    input('按回车关闭...')
    ctx.close()
