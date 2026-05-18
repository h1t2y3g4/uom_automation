#!/usr/bin/env python3
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
