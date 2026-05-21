#!/usr/bin/env python3
"""
uom_takeoff_confirm.py - 检测临近起飞计划并自动提交起飞确认

职责：
- 复用持久化浏览器登录态
- 自动进入一般飞行活动页面并读取最近计划详情
- 过滤掉已过期计划，只保留未来计划
- 检测 1 小时内即将起飞且尚未提交起飞确认的计划
- 通过页面交互方式逐条提交起飞确认，准备情况固定填写“准备完毕”
- 支持同一轮循环处理多个候选计划
- 将运行结果写入日志文件

用法：
  python3 uom_takeoff_confirm.py
  python3 uom_takeoff_confirm.py --headless
  python3 uom_takeoff_confirm.py --dry-run
  python3 uom_takeoff_confirm.py --window-minutes 60
"""

import argparse
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.uom_core as core

TAKEOFF_CONFIRM_LOG = core.PROJECT_ROOT / 'log' / 'takeoff_confirm_log.json'
PREPARE_STATUS_TEXT = '准备完毕'


def build_parser():
    parser = argparse.ArgumentParser(description='UOM 起飞确认脚本')
    parser.add_argument('--headless', action='store_true', help='无头模式运行浏览器')
    parser.add_argument('--dry-run', action='store_true', help='只检测，不实际提交起飞确认')
    parser.add_argument('--window-minutes', type=int, default=60, help='起飞前检测窗口，默认 60 分钟')
    parser.add_argument('--output', default=str(core.DEFAULT_RECENT_PLAN_DETAILS_FILE), help='计划缓存输出文件路径')
    return parser


def write_run_log(payload):
    TAKEOFF_CONFIRM_LOG.parent.mkdir(parents=True, exist_ok=True)
    TAKEOFF_CONFIRM_LOG.write_text(
        json.dumps(core.sanitize_for_json(payload), ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def has_takeoff_confirmation(detail):
    if not isinstance(detail, dict):
        return False
    takeoffs = detail.get('takeoffs') or []
    return bool(takeoffs)


def build_candidate(detail, now, window_minutes):
    if not isinstance(detail, dict):
        return None
    plan_beg = detail.get('planBeg') or detail.get('planBegStr')
    if not plan_beg:
        return None
    try:
        plan_beg_dt = core.parse_local_datetime(plan_beg)
    except Exception:
        return None
    delta_minutes = (plan_beg_dt - now).total_seconds() / 60.0
    if delta_minutes < 0 or delta_minutes > window_minutes:
        return None
    return {
        'planId': detail.get('planId'),
        'planBeg': plan_beg,
        'planEnd': detail.get('planEnd') or detail.get('planEndStr'),
        'applySts': detail.get('applySts'),
        'spcNames': [space.get('spcName') for space in (detail.get('spaces') or []) if isinstance(space, dict) and space.get('spcName')],
        'deltaMinutes': round(delta_minutes, 2),
        'takeoffsCount': len(detail.get('takeoffs') or []),
    }


def detect_candidates(payload, window_minutes):
    now = core.get_now_local()
    details = payload.get('details') or []
    candidates = []
    skipped = []
    for item in details:
        detail = item.get('detail') if isinstance(item, dict) else None
        summary = item.get('summary') if isinstance(item, dict) else None
        if not isinstance(detail, dict):
            skipped.append({
                'summaryPlanId': (summary or {}).get('planId') if isinstance(summary, dict) else None,
                'reason': 'missing_detail',
            })
            continue
        if has_takeoff_confirmation(detail):
            skipped.append({
                'planId': detail.get('planId'),
                'reason': 'already_has_takeoff_confirmation',
                'takeoffsCount': len(detail.get('takeoffs') or []),
            })
            continue
        candidate = build_candidate(detail, now, window_minutes)
        if candidate is None:
            skipped.append({
                'planId': detail.get('planId'),
                'reason': 'outside_window_or_expired',
                'planBeg': detail.get('planBeg') or detail.get('planBegStr'),
            })
            continue
        candidates.append({'detail': detail, 'candidate': candidate})
    return {
        'now': core.format_local_datetime(now),
        'windowMinutes': window_minutes,
        'candidates': candidates,
        'skipped': skipped,
    }


def run_single_takeoff_confirmation(page, candidate):
    open_res = core.open_takeoff_confirmation(page, candidate['planId'], candidate['planBeg'])
    wait_res = core.wait_for_takeoff_page(page)
    precheck = core.get_takeoff_form_snapshot(page, f"takeoff_precheck_{candidate['planId']}")
    fill_res = core.fill_takeoff_confirmation_form(page, PREPARE_STATUS_TEXT)
    core.time.sleep(1)
    postfill = core.get_takeoff_form_snapshot(page, f"takeoff_postfill_{candidate['planId']}")
    submit_res = None
    final_snapshot = None
    if fill_res.get('ok'):
        submit_res = core.submit_takeoff_confirmation_ui(page)
        core.time.sleep(3)
        final_snapshot = core.get_takeoff_form_snapshot(page, f"takeoff_postsubmit_{candidate['planId']}")
    return {
        'candidate': candidate,
        'open': core.sanitize_for_json(open_res),
        'wait': core.sanitize_for_json(wait_res),
        'precheck': core.sanitize_for_json(precheck),
        'fill': core.sanitize_for_json(fill_res),
        'postfill': core.sanitize_for_json(postfill),
        'submit': core.sanitize_for_json(submit_res),
        'finalSnapshot': core.sanitize_for_json(final_snapshot),
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    playwright_handle, context, page = core.launch_context(headless=args.headless)
    run_log = {
        'runStartedAt': core.format_local_datetime(core.get_now_local()),
        'runFinishedAt': None,
        'status': 'running',
        'prepareStatusText': PREPARE_STATUS_TEXT,
        'windowMinutes': args.window_minutes,
        'dryRun': args.dry_run,
        'loginFlow': None,
        'planCacheOutput': args.output,
        'detected': None,
        'submitted': [],
        'errors': [],
    }
    write_run_log(run_log)
    try:
        login_flow = core.ensure_main_login_with_auto_sms(page)
        run_log['loginFlow'] = core.sanitize_for_json(login_flow)
        write_run_log(run_log)
        status = login_flow.get('statusAfter') or {}
        core.require_reliable_main_login(status, context, '当前不在可靠的主站已登录状态，停止起飞确认流程，避免在错误页面上继续执行。')
        print('进入 一般飞行活动 ...')
        core.open_fly_activity(page)
        core.time.sleep(6)
        result, err = core.fetch_recent_plan_details(page, limit=10)
        if err:
            print('读取最近计划详情失败:')
            print(json.dumps(err, ensure_ascii=False, indent=2))
            run_log['status'] = 'read_failed'
            run_log['errors'].append(core.sanitize_for_json(err))
            write_run_log(run_log)
            raise SystemExit(1)

        filtered_result = core.filter_future_plan_details(result)
        saved_path = core.save_recent_plan_details(filtered_result, args.output)
        detection = detect_candidates(filtered_result, args.window_minutes)
        run_log['detected'] = {
            'now': detection['now'],
            'windowMinutes': detection['windowMinutes'],
            'candidateCount': len(detection['candidates']),
            'candidates': [item['candidate'] for item in detection['candidates']],
            'skipped': detection['skipped'],
            'savedPath': str(saved_path),
        }
        write_run_log(run_log)

        print('检测结果摘要:')
        print(json.dumps(run_log['detected'], ensure_ascii=False, indent=2))

        if args.dry_run:
            run_log['status'] = 'dry_run'
            return

        if not detection['candidates']:
            run_log['status'] = 'no_candidate'
            return

        for idx, item in enumerate(detection['candidates'], start=1):
            candidate = item['candidate']
            print(f"\n===== 起飞确认 {idx}/{len(detection['candidates'])} =====")
            print(f"planId={candidate['planId']} planBeg={candidate['planBeg']}")
            try:
                result_entry = run_single_takeoff_confirmation(page, candidate)
                run_log['submitted'].append(result_entry)
                submit_info = result_entry.get('submit') or {}
                if submit_info.get('hasFailure'):
                    run_log['status'] = 'partial_failure'
                write_run_log(run_log)
                print('本条结果:')
                print(json.dumps({
                    'candidate': candidate,
                    'openOk': (result_entry.get('open') or {}).get('ok'),
                    'waitReady': (result_entry.get('wait') or {}).get('ready'),
                    'fillOk': (result_entry.get('fill') or {}).get('ok'),
                    'submitOk': (submit_info.get('ok') if submit_info else None),
                    'submitHasSuccess': submit_info.get('hasSuccess') if submit_info else None,
                    'submitHasFailure': submit_info.get('hasFailure') if submit_info else None,
                }, ensure_ascii=False, indent=2))
            except Exception as e:
                error_payload = {
                    'candidate': candidate,
                    'type': type(e).__name__,
                    'message': str(e),
                    'traceback': traceback.format_exc(),
                }
                run_log['errors'].append(error_payload)
                run_log['submitted'].append({
                    'candidate': candidate,
                    'error': error_payload,
                })
                run_log['status'] = 'partial_failure'
                write_run_log(run_log)

        if run_log['errors']:
            run_log['status'] = 'partial_failure'
        elif run_log['submitted']:
            failures = [x for x in run_log['submitted'] if ((x.get('submit') or {}).get('hasFailure'))]
            run_log['status'] = 'partial_failure' if failures else 'completed'
        else:
            run_log['status'] = 'no_candidate'
    except SystemExit:
        raise
    except Exception as e:
        run_log['status'] = 'exception'
        run_log['errors'].append({
            'type': type(e).__name__,
            'message': str(e),
            'traceback': traceback.format_exc(),
        })
        write_run_log(run_log)
        raise
    finally:
        run_log['runFinishedAt'] = core.format_local_datetime(core.get_now_local())
        write_run_log(run_log)
        core.close_context(playwright_handle, context)


if __name__ == '__main__':
    main()
