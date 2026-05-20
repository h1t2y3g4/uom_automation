#!/usr/bin/env python3
"""
uom_takeoff_confirm.py - 检测临近起飞计划并自动提交起飞确认

职责：
- 复用持久化浏览器登录态
- 自动进入一般飞行活动页面并读取最近计划详情
- 过滤掉已过期计划，只保留未来计划
- 检测 1 小时内即将起飞且尚未提交起飞确认的计划
- 自动提交起飞确认，准备情况固定填写“准备完毕”
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


def submit_takeoff_confirmation(page, plan_id, prepare_text):
    return page.evaluate(
        """
        async ([planId, prepareText]) => {
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            if (!doc) return {ok:false, error:'no iframe doc'};
            const src = iframe.src;
            const u = new URL(src, location.origin);
            const ticket = u.searchParams.get('ticket');
            const userName = u.searchParams.get('userName');
            const cookie = doc.cookie || '';
            const m = cookie.match(/(?:^|; )PUB-Token=([^;]+)/);
            const pubToken = m ? decodeURIComponent(m[1]) : null;
            if (!(ticket && userName && pubToken)) {
                return {ok:false, error:'missing auth', ticket, userName, hasToken: !!pubToken};
            }

            const payload = {
                planId,
                prepareSts: prepareText,
                prepareDesc: prepareText,
                remark: prepareText,
            };

            const candidatePaths = [
                '/oapi/pub/takeoff/confirm',
                '/oapi/pub/takeoff/save',
                '/oapi/pub/planTakeoff/confirm',
                '/oapi/pub/planTakeoff/save',
            ];

            const attempts = [];
            for (const path of candidatePaths) {
                try {
                    const resp = await iframe.contentWindow.fetch(path, {
                        method: 'POST',
                        headers: {
                            'Authorization': 'Bearer ' + pubToken,
                            'pubUserName': userName,
                            'ticket': ticket,
                            'deviceType': 'PC',
                            'Content-Type': 'application/json;charset=UTF-8'
                        },
                        credentials: 'include',
                        body: JSON.stringify(payload),
                    });
                    const text = await resp.text();
                    let data = null;
                    try { data = JSON.parse(text); } catch (e) {}
                    const ok = resp.ok && (!data || data.code === 200 || data.code === 0 || data.success === true);
                    const attempt = {
                        path,
                        httpStatus: resp.status,
                        ok,
                        data,
                        raw: text.slice(0, 1000),
                    };
                    attempts.push(attempt);
                    if (ok) {
                        return {ok:true, path, attempts, payload, response: attempt};
                    }
                } catch (error) {
                    attempts.push({
                        path,
                        ok: false,
                        error: String(error),
                    });
                }
            }
            return {ok:false, error:'all_candidate_paths_failed', payload, attempts};
        }
        """,
        [plan_id, prepare_text],
    )


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

        for item in detection['candidates']:
            detail = item['detail']
            candidate = item['candidate']
            print(f"提交起飞确认: planId={candidate['planId']} planBeg={candidate['planBeg']}")
            submit_result = submit_takeoff_confirmation(page, candidate['planId'], PREPARE_STATUS_TEXT)
            entry = {
                'candidate': candidate,
                'submitResult': core.sanitize_for_json(submit_result),
            }
            if submit_result.get('ok'):
                refreshed = core.get_plan_detail(page, candidate['planId'])
                entry['detailAfterSubmit'] = core.sanitize_for_json(refreshed)
            else:
                entry['detailAfterSubmit'] = None
            run_log['submitted'].append(entry)
            write_run_log(run_log)

        if run_log['submitted'] and all((x.get('submitResult') or {}).get('ok') for x in run_log['submitted']):
            run_log['status'] = 'completed'
        elif run_log['submitted']:
            run_log['status'] = 'partial_failure'
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
