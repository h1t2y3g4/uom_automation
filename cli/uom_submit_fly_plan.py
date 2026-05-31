#!/usr/bin/env python3
"""
uom_submit_fly_plan.py - UOM 自动提交飞行计划流程

职责：
- 复用持久化浏览器登录态
- 自动进入一般飞行活动并打开新增页
- 自动填入最近计划内容和目标时间
- 记录 precheck/postcheck 调试信息
- 支持 dry-run 只做时间解析与预演，不触发提交
- 自动触发提交并检查结果

用法：
  python3 uom_submit_fly_plan.py
  python3 uom_submit_fly_plan.py --start-utc-ts 1747908000 --end-utc-ts 1747911600
  python3 uom_submit_fly_plan.py --use-submit-plan
  python3 uom_submit_fly_plan.py --use-time-list
  python3 uom_submit_fly_plan.py --dry-run
  python3 uom_submit_fly_plan.py --use-submit-plan --dry-run
  python3 uom_submit_fly_plan.py --headless --use-submit-plan
"""

import argparse
import json
import sys
import traceback
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import core.uom_core as core


def build_parser():
    parser = argparse.ArgumentParser(description='UOM 自动提交飞行计划脚本')
    parser.add_argument('--start-utc-ts', type=int, help='开始 UTC 秒级时间戳')
    parser.add_argument('--end-utc-ts', type=int, help='结束 UTC 秒级时间戳')
    parser.add_argument('--use-submit-plan', action='store_true', help='按 submit_plan.json 中的 plans 循环提交')
    parser.add_argument('--use-time-list', action='store_true', help='兼容旧参数；实际改为读取 submit_plan.json')
    parser.add_argument('--dry-run', action='store_true', help='只解析时间并打印计划，不进入新增页、不提交')
    parser.add_argument('--headless', action='store_true', help='无头模式运行浏览器')
    return parser


def write_submit_log(payload):
    core.MANUAL_SELECTION_LOG.write_text(
        json.dumps(core.sanitize_for_json(payload), ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def build_run_log(submission_items):
    return {
        'runStartedAt': core.format_local_datetime(core.datetime.now()),
        'runFinishedAt': None,
        'status': 'running',
        'total': len(submission_items),
        'successCount': 0,
        'failureCount': 0,
        'loginFlow': {
            'attemptedAutoLogin': False,
            'statusBefore': None,
            'loginResult': None,
            'statusAfter': None,
        },
        'items': [
            {
                'pairIndex': idx,
                'pairTotal': len(submission_items),
                'submission_item': item,
                'status': 'pending',
                'error': None,
                'fill': None,
                'precheck': None,
                'postcheck': None,
                'submit': None,
                'latest_after_submit': None,
            }
            for idx, item in enumerate(submission_items, start=1)
        ],
    }


def update_run_log(run_log, pair_index, **fields):
    item = run_log['items'][pair_index - 1]
    item.update(fields)
    statuses = [x.get('status') for x in run_log.get('items', [])]
    run_log['successCount'] = sum(1 for s in statuses if s == 'post_submit_checked')
    run_log['failureCount'] = sum(1 for s in statuses if s in ('submit_exception', 'failed'))
    if all(s == 'post_submit_checked' for s in statuses):
        run_log['status'] = 'completed'
    elif any(s in ('submit_exception', 'failed') for s in statuses):
        run_log['status'] = 'partial_failure'
    else:
        run_log['status'] = 'running'
    return run_log


def finalize_run_log(run_log):
    run_log['runFinishedAt'] = core.format_local_datetime(core.datetime.now())
    statuses = [x.get('status') for x in run_log.get('items', [])]
    if statuses and all(s == 'post_submit_checked' for s in statuses):
        run_log['status'] = 'completed'
    elif any(s in ('submit_exception', 'failed') for s in statuses):
        run_log['status'] = 'partial_failure'
    else:
        run_log['status'] = 'incomplete'
    run_log['successCount'] = sum(1 for s in statuses if s == 'post_submit_checked')
    run_log['failureCount'] = sum(1 for s in statuses if s in ('submit_exception', 'failed'))
    return run_log


def run_single_submission(page, detail, submission_item, drone_name, drone_uas_code, driver_name, pair_index, pair_total, run_log):
    next_beg = submission_item['planBeg']
    next_end = submission_item['planEnd']
    resolved_space = submission_item['space']
    print(f'\n===== 第 {pair_index}/{pair_total} 条计划 =====')
    print(f'目标计划: {core.describe_submission_item(submission_item)}')
    add_res = core.open_new_fly_form(page)
    print('打开新增页:')
    print(json.dumps(add_res, ensure_ascii=False, indent=2))
    fly_add = core.wait_for_fly_add(page)
    print('等待新增页:')
    print(json.dumps(fly_add, ensure_ascii=False, indent=2)[:4000])
    if not fly_add:
        raise SystemExit(1)
    fill = core.fill_new_form_from_detail(page, detail, next_beg, next_end, resolved_space)
    print('填充结果:')
    print(json.dumps(core.sanitize_for_json(fill), ensure_ascii=False, indent=2)[:4000])
    precheck = core.get_form_debug_snapshot(page, f'precheck_auto_fill_{pair_index}')
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
    print(f'1. 当前目标时间是否正确：{next_beg} ~ {next_end}')
    print('2. 页面是否真的打开过航空器/操控员的添加动作（看终端 clickedUavAdd / clickedDriverAdd）')
    print(f'3. 航空器信息里是否有 {drone_name} / {drone_uas_code}')
    print(f'4. 操控员信息里是否有 {driver_name}')
    print('5. 不要自己点“提交申请”，脚本会在 5 秒观察后自动继续提交')
    print('   如需中止，请直接关闭浏览器或中断脚本。')
    print('   5 秒观察中...')
    core.time.sleep(5)
    print('[auto] 观察结束，继续提交流程...')
    postcheck = core.get_form_debug_snapshot(page, f'postcheck_manual_before_submit_{pair_index}')
    print('手动确认后的前端校验/快照:')
    print(json.dumps(postcheck, ensure_ascii=False, indent=2)[:5000])
    update_run_log(
        run_log,
        pair_index,
        target_time={'planBeg': submission_item['planBeg'], 'planEnd': submission_item['planEnd']},
        fill=fill,
        precheck=precheck,
        postcheck=postcheck,
        submit=None,
        latest_after_submit=None,
        status='pre_submit_logged',
        error=None,
    )
    write_submit_log(run_log)
    print(f'日志已保存: {core.MANUAL_SELECTION_LOG}')
    try:
        submit_res = core.trigger_submit_copied_form(page)
        update_run_log(run_log, pair_index, submit=submit_res, status='submit_triggered')
        write_submit_log(run_log)
        print('触发提交:')
        print(json.dumps(submit_res, ensure_ascii=False, indent=2))
        print('提交后等待 20 秒，便于观察页面反馈...')
        core.time.sleep(20)
        latest2 = core.get_latest_plan(page)
        update_run_log(run_log, pair_index, latest_after_submit=latest2, status='post_submit_checked')
        write_submit_log(run_log)
        print('提交后最近计划:')
        print(json.dumps(latest2, ensure_ascii=False, indent=2)[:4000])
    except Exception as e:
        error_payload = {
            'type': type(e).__name__,
            'message': str(e),
            'traceback': traceback.format_exc(),
        }
        update_run_log(run_log, pair_index, status='submit_exception', error=error_payload)
        write_submit_log(run_log)
        print('提交后流程出现异常，已写入日志:')
        print(json.dumps(error_payload, ensure_ascii=False, indent=2))
        raise
    return {
        'submission_item': submission_item,
        'fill': fill,
        'precheck': precheck,
        'postcheck': postcheck,
        'submit': run_log['items'][pair_index - 1]['submit'],
        'latest_after_submit': run_log['items'][pair_index - 1]['latest_after_submit'],
        'status': run_log['items'][pair_index - 1]['status'],
        'error': run_log['items'][pair_index - 1]['error'],
    }


def main():
    args = build_parser().parse_args()
    print('UOM 自动提交飞行计划脚本')
    print('流程：先关闭温馨提示，再按更接近人工的顺序逐项填入 -> 观察 5 秒 -> 自动提交并检查结果')
    cfg = core.load_config()
    drone_name = cfg.get("drone", {}).get("proName", "config.json 中的 drone.proName")
    drone_uas_code = cfg.get("drone", {}).get("uasCode", "config.json 中的 drone.uasCode")
    driver_name = cfg.get("driver", {}).get("name", "config.json 中的 driver.name")
    playwright_handle, context, page = core.launch_context(headless=args.headless)
    try:
        login_flow = core.ensure_main_login_with_auto_sms(page)
        print('主站状态/自动登录结果:')
        print(json.dumps(login_flow, ensure_ascii=False, indent=2))
        status = login_flow.get('statusAfter') or {}
        if not status.get('hasMainLogin') or status.get('onLoginPage') or '#/login' in (status.get('url') or ''):
            run_log = build_run_log([])
            run_log['status'] = 'login_failed'
            run_log['loginFlow'] = core.sanitize_for_json(login_flow)
            write_submit_log(run_log)
        core.require_reliable_main_login(status, context, '当前不在可靠的主站已登录状态，自动登录后仍失败，停止自动提交流程，避免在错误页面上继续执行。')
        print('进入 一般飞行活动 ...')
        core.open_fly_activity(page)
        core.time.sleep(6)
        latest, latest_err, detail = core.fetch_latest_detail(page)
        if latest_err:
            print('读取最近计划失败:')
            print(json.dumps(latest_err, ensure_ascii=False, indent=2))
            context.close()
            raise SystemExit(1)
        if args.use_time_list and not args.use_submit_plan:
            print('[compat] --use-time-list 已废弃，当前按 submit_plan.json 处理。')
        submission_items = core.resolve_submission_items(cfg, detail, args)
        run_log = build_run_log(submission_items)
        run_log['loginFlow'] = core.sanitize_for_json(login_flow)
        write_submit_log(run_log)
        print('本轮计划列表:')
        for idx, item in enumerate(submission_items, start=1):
            print(core.describe_submission_item(item, idx))
        if args.dry_run:
            preview = []
            for item in submission_items:
                space = item.get('space') or {}
                preview.append({
                    'planBeg': item.get('planBeg'),
                    'planEnd': item.get('planEnd'),
                    'source': item.get('source'),
                    'airspaceSource': item.get('airspaceSource'),
                    'airspaceRefName': item.get('airspaceRefName'),
                    'spacePreview': {
                        'polygonWgs84': space.get('polygonWgs84'),
                        'spcBottom': space.get('spcBottom'),
                        'spcTop': space.get('spcTop'),
                        'groupName': space.get('groupName'),
                        'spcName': space.get('spcName'),
                    },
                })
            print('dry-run 解析结果:')
            print(json.dumps(core.sanitize_for_json(preview), ensure_ascii=False, indent=2)[:8000])
            run_log['preview'] = preview
            run_log['status'] = 'dry_run'
            run_log['runFinishedAt'] = core.format_local_datetime(core.datetime.now())
            write_submit_log(run_log)
            print('dry-run 模式：已完成登录态/最近计划检查，以及时间与空域解析；不进入新增页、不触发提交。')
            return
        results = []
        for idx, item in enumerate(submission_items, start=1):
            results.append(run_single_submission(page, detail, item, drone_name, drone_uas_code, driver_name, idx, len(submission_items), run_log))
        finalize_run_log(run_log)
        write_submit_log(run_log)
        print('全部提交流程结束，结果摘要:')
        print(json.dumps(core.sanitize_for_json(results), ensure_ascii=False, indent=2)[:8000])
    finally:
        core.close_context(playwright_handle, context)


if __name__ == '__main__':
    main()
