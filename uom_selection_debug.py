#!/usr/bin/env python3
"""
uom_selection_debug.py - UOM 航空器/操控员内部选中状态专项调试

职责：
- 复用持久化浏览器登录态
- 自动进入一般飞行活动并打开新增页
- 自动填入最近计划内容和目标时间
- 不走最终提交流程
- 专门输出航空器/操控员相关 data/ref/dialog/validate 线索

用法：
  python3 uom_selection_debug.py
"""

import json

import uom_core as core


def main():
    print('UOM 选择器专项调试：定位 uavs/drivers 为什么仍未被前端认定为已选中')
    playwright_handle, context, page = core.launch_context(headless=False)
    try:
        status = core.ensure_main_page(page)
        print('主站状态:')
        print(json.dumps(status, ensure_ascii=False, indent=2))
        core.require_reliable_main_login(status, context, '当前不在可靠的主站已登录状态，停止专项调试，避免在错误页面上继续执行。')
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
        fill = core.fill_new_form_from_detail(page, detail, next_beg, next_end)
        print('填充结果:')
        print(json.dumps(fill, ensure_ascii=False, indent=2)[:4000])
        precheck = core.get_form_debug_snapshot(page, 'selection_debug_precheck')
        print('自动填充后的前端校验/快照:')
        print(json.dumps(precheck, ensure_ascii=False, indent=2)[:5000])
        inspect = core.inspect_add_dialogs(page, detail, next_beg, next_end)
        print('航空器/操控员弹层与结构探测结果:')
        print(json.dumps(inspect, ensure_ascii=False, indent=2)[:12000])
        print('\n这个脚本不会自动提交。')
        print('请重点观察：')
        print('1. precheck 里 fields 是否仍然包含 uavs / drivers')
        print('2. inspect 里 visibleDialogs / refKeys / dataKeys 有没有选择器线索')
        print('3. 页面上是否真的弹出过航空器/操控员相关弹层')
        input('观察完成后，按回车关闭浏览器...')
    finally:
        core.close_context(playwright_handle, context)


if __name__ == '__main__':
    main()
