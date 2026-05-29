#!/usr/bin/env python3
"""
time_utils.py - 时间/日期工具函数
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def parse_local_datetime(value: str):
    return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')


def get_now_local():
    return datetime.now()


def format_local_datetime(value: datetime):
    return value.strftime('%Y-%m-%d %H:%M:%S')


def is_future_plan(plan_beg: str, now=None):
    if not plan_beg:
        return False
    now = now or get_now_local()
    try:
        return parse_local_datetime(plan_beg) >= now
    except Exception:
        return False


def filter_future_plan_details(payload, now=None):
    if not isinstance(payload, dict):
        return payload
    now = now or get_now_local()
    details = payload.get('details') or []
    kept_details = []
    kept_plan_ids = set()
    for item in details:
        if not isinstance(item, dict):
            continue
        summary = item.get('summary') if isinstance(item.get('summary'), dict) else {}
        detail = item.get('detail') if isinstance(item.get('detail'), dict) else {}
        plan_beg = detail.get('planBeg') or detail.get('planBegStr') or summary.get('planBeg') or summary.get('planBegStr')
        if not is_future_plan(plan_beg, now=now):
            continue
        kept_details.append(item)
        plan_id = detail.get('planId') or summary.get('planId')
        if plan_id is not None:
            kept_plan_ids.add(plan_id)

    list_payload = payload.get('list') if isinstance(payload.get('list'), dict) else {}
    rows = list_payload.get('rows') or []
    kept_rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_plan_beg = row.get('planBeg') or row.get('planBegStr')
        row_plan_id = row.get('planId')
        if row_plan_id in kept_plan_ids or is_future_plan(row_plan_beg, now=now):
            kept_rows.append(row)

    latest = kept_rows[0] if kept_rows else None
    filtered = {
        **payload,
        'count': len(kept_details),
        'details': kept_details,
        'filteredAt': format_local_datetime(now),
        'filter': {
            'type': 'future_plan_only',
            'keptCount': len(kept_details),
            'droppedCount': max(0, len(details) - len(kept_details)),
        },
        'list': {
            **list_payload,
            'total': len(kept_rows),
            'latest': latest,
            'rows': kept_rows,
        },
    }
    return filtered


def get_debug_target_plan_time(plan_beg: str, plan_end: str):
    b = parse_local_datetime(plan_beg)
    e = parse_local_datetime(plan_end)
    target_date = datetime.now().date() + timedelta(days=1)
    nb = datetime.combine(target_date, b.time())
    ne = datetime.combine(target_date, e.time())
    if ne <= nb:
        ne = ne + timedelta(days=1)
    return format_local_datetime(nb), format_local_datetime(ne)


def get_next_monday_same_time(plan_beg: str, plan_end: str):
    b = parse_local_datetime(plan_beg)
    e = parse_local_datetime(plan_end)
    days_ahead = (0 - b.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    nb = b + timedelta(days=days_ahead)
    ne = e + timedelta(days=days_ahead)
    return format_local_datetime(nb), format_local_datetime(ne)


def get_tomorrow_same_time(plan_beg: str, plan_end: str):
    b = parse_local_datetime(plan_beg)
    e = parse_local_datetime(plan_end)
    nb = b + timedelta(days=1)
    ne = e + timedelta(days=1)
    return format_local_datetime(nb), format_local_datetime(ne)


def get_next_tuesday_same_time(plan_beg: str, plan_end: str):
    return get_debug_target_plan_time(plan_beg, plan_end)


def get_timezone_from_config(cfg):
    tz_name = (
        cfg.get('plan_defaults', {}).get('timezone')
        or cfg.get('time', {}).get('timezone')
        or 'UTC+8'
    )
    if tz_name in ('UTC+8', 'UTC+08:00', 'Asia/Shanghai'):
        return timezone(timedelta(hours=8)), tz_name
    if tz_name.startswith('UTC'):
        sign = 1
        offset = tz_name[3:]
        if offset.startswith('+'):
            sign = 1
            offset = offset[1:]
        elif offset.startswith('-'):
            sign = -1
            offset = offset[1:]
        parts = offset.split(':') if offset else ['0']
        hours = int(parts[0] or '0')
        minutes = int(parts[1] or '0') if len(parts) > 1 else 0
        return timezone(sign * timedelta(hours=hours, minutes=minutes)), tz_name
    return ZoneInfo(tz_name), tz_name
