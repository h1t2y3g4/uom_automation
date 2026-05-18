#!/usr/bin/env python3
"""
uom_probe.py - UOM 无副作用探测流程

职责：
- 复用持久化浏览器登录态
- 自动进入一般飞行活动并打开新增页
- 不执行最终提交
- 重点探测航空器/操控员选择弹层与组件结构

用法：
  python3 uom_probe.py
"""

import json

import uom_core as core


def main():
    print('UOM 无副作用调试：探测新增页航空器/操控员选择弹层结构')
    playwright_handle, context, page = core.launch_context(headless=False)
    try:
        status = core.ensure_main_page(page)
        print('主站状态:')
        print(json.dumps(status, ensure_ascii=False, indent=2))
        core.require_reliable_main_login(status, context, '当前落在登录页或主站框架未真正加载，停止无副作用调试，避免消耗短信验证码。')
        print('进入 一般飞行活动 ...')
        core.open_fly_activity(page)
        core.time.sleep(12)
        latest, latest_err, detail = core.fetch_latest_detail(page)
        if latest_err:
            print('读取最近计划失败:')
            print(json.dumps(latest_err, ensure_ascii=False, indent=2))
            context.close()
            raise SystemExit(1)
        next_beg, next_end = core.get_next_tuesday_same_time(detail['planBeg'], detail['planEnd'])
        print(f'目标时间: {next_beg} ~ {next_end}')
        add_res = core.open_new_fly_form(page)
        print('打开新增页:')
        print(json.dumps(add_res, ensure_ascii=False, indent=2))
        fly_add = core.wait_for_fly_add(page)
        print('等待新增页:')
        print(json.dumps(fly_add, ensure_ascii=False, indent=2)[:4000])
        if not fly_add:
            context.close()
            raise SystemExit(1)
        inspect = core.inspect_add_dialogs(page, detail, next_beg, next_end)
        print('弹层探测结果:')
        print(json.dumps(inspect, ensure_ascii=False, indent=2)[:12000])
        input('探测完成。按回车关闭浏览器...')
    finally:
        core.close_context(playwright_handle, context)


if __name__ == '__main__':
    main()
