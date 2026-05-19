#!/usr/bin/env python3
"""
uom_semiauto.py - UOM 半自动提交流程

职责：
- 复用持久化浏览器登录态
- 自动进入一般飞行活动并打开新增页
- 自动填入最近计划内容和目标时间
- 记录 precheck/postcheck 调试信息
- 你确认后再尝试触发提交

用法：
  python3 uom_semiauto.py
"""

import json

import uom_core as core


def main():
    print('UOM 半自动提交脚本')
    print('流程：先关闭温馨提示，再按更接近人工的顺序逐项填入 -> 观察 10 秒 -> 自动提交并检查结果')
    cfg = core.load_config()
    drone_name = cfg.get("drone", {}).get("proName", "config.json 中的 drone.proName")
    drone_uas_code = cfg.get("drone", {}).get("uasCode", "config.json 中的 drone.uasCode")
    driver_name = cfg.get("driver", {}).get("name", "config.json 中的 driver.name")
    playwright_handle, context, page = core.launch_context(headless=False)
    try:
        status = core.ensure_main_page(page)
        print('主站状态:')
        print(json.dumps(status, ensure_ascii=False, indent=2))
        core.require_reliable_main_login(status, context, '当前不在可靠的主站已登录状态，停止半自动流程，避免在错误页面上继续执行。')
        print('进入 一般飞行活动 ...')
        core.open_fly_activity(page)
        core.time.sleep(6)
        latest, latest_err, detail = core.fetch_latest_detail(page)
        if latest_err:
            print('读取最近计划失败:')
            print(json.dumps(latest_err, ensure_ascii=False, indent=2))
            context.close()
            raise SystemExit(1)
        next_beg, next_end = core.get_tomorrow_same_time(detail['planBeg'], detail['planEnd'])
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
        print(json.dumps(core.sanitize_for_json(fill), ensure_ascii=False, indent=2)[:4000])
        precheck = core.get_form_debug_snapshot(page, 'precheck_auto_fill')
        print('自动填充后的前端校验/快照:')
        print(json.dumps(precheck, ensure_ascii=False, indent=2)[:5000])
        page.evaluate(r"""
        () => {
          const iframe = document.querySelector('iframe');
          if (!iframe) return false;
          const doc = iframe.contentDocument;
          if (!doc) return false;
          const btn = Array.from(doc.querySelectorAll('button,span,div,a')).find(b => ((b.textContent||'').replace(/\s+/g,' ').trim()) === '我知道了' && b.offsetParent !== null);
          if (btn) btn.click();
          return true;
        }
        """)
        core.time.sleep(1)
        print('\n请你肉眼确认下面几点：')
        print('1. 时间当前按上一条计划顺延一天（本轮应为 5 月 22 日同时间）')
        print('2. 页面是否真的打开过航空器/操控员的添加动作（看终端 clickedUavAdd / clickedDriverAdd）')
        print(f'3. 航空器信息里是否有 {drone_name} / {drone_uas_code}')
        print(f'4. 操控员信息里是否有 {driver_name}')
        print('5. 不要自己点“提交申请”，脚本会在 10 秒观察后自动继续提交')
        print('   如需中止，请直接关闭浏览器或中断脚本。')
        print('   10 秒观察中...')
        core.time.sleep(10)
        print('[auto] 观察结束，继续提交流程...')
        postcheck = core.get_form_debug_snapshot(page, 'postcheck_manual_before_submit')
        print('手动确认后的前端校验/快照:')
        print(json.dumps(postcheck, ensure_ascii=False, indent=2)[:5000])
        core.MANUAL_SELECTION_LOG.write_text(json.dumps(core.sanitize_for_json({
            'target_time': {'planBeg': next_beg, 'planEnd': next_end},
            'fill': fill,
            'precheck': precheck,
            'postcheck': postcheck,
        }), ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'日志已保存: {core.MANUAL_SELECTION_LOG}')
        submit_res = core.trigger_submit_copied_form(page)
        print('触发提交:')
        print(json.dumps(submit_res, ensure_ascii=False, indent=2))
        core.time.sleep(10)
        latest2 = core.get_latest_plan(page)
        print('提交后最近计划:')
        print(json.dumps(latest2, ensure_ascii=False, indent=2)[:4000])
        input('检查结果后，按回车关闭浏览器...')
    finally:
        core.close_context(playwright_handle, context)


if __name__ == '__main__':
    main()
