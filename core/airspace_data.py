#!/usr/bin/env python3
"""
airspace_data.py - 空域数据解析、规范化、提交计划项处理
"""

import hashlib

from core.constants import AIRSPACE_QUERY_ALGO_VERSION
from core.config import load_airspace_cache, load_submit_plan
from core.time_utils import (
    format_local_datetime,
    get_now_local,
    get_timezone_from_config,
    get_tomorrow_same_time,
)


def normalize_space_payload(space, default_top=None):
    if not isinstance(space, dict):
        raise ValueError(f'space 必须是对象: {space!r}')
    polygon = space.get('polygonWgs84') or space.get('locationWgs84') or space.get('points')
    if not polygon:
        raise ValueError(f'space 缺少 polygonWgs84/locationWgs84/points: {space!r}')
    spc_bottom = space.get('spcBottom', 0)
    spc_top = space.get('spcTop', default_top if default_top is not None else 120)
    return {
        **space,
        'locationWgs84': polygon,
        'polygonWgs84': polygon,
        'spcBottom': spc_bottom,
        'spcTop': spc_top,
        'spcName': space.get('spcName') or space.get('name') or '',
        'groupName': space.get('groupName') or '空域1',
        'spaceShape': space.get('spaceShape') or '面',
        'spcShape': space.get('spcShape') or '1',
    }


def find_cached_airspace_by_name(airspace_cache, name):
    items = airspace_cache.get('items', []) if isinstance(airspace_cache, dict) else []
    for item in items:
        if item.get('name') == name:
            return item
    raise ValueError(f'airspace.json 中找不到常用空域: {name}')


def get_first_cached_airspace(airspace_cache):
    items = airspace_cache.get('items', []) if isinstance(airspace_cache, dict) else []
    if not items:
        raise ValueError('airspace.json 的 items 为空，无法作为保底空域来源')
    return items[0]


def utc_timestamps_to_local_pair(start_ts, end_ts, cfg):
    from datetime import datetime, timezone
    tzinfo, tz_name = get_timezone_from_config(cfg)
    start_dt = datetime.fromtimestamp(int(start_ts), timezone.utc).astimezone(tzinfo)
    end_dt = datetime.fromtimestamp(int(end_ts), timezone.utc).astimezone(tzinfo)
    if end_dt <= start_dt:
        raise ValueError(f'结束时间必须晚于开始时间: start={start_ts}, end={end_ts}, timezone={tz_name}')
    return format_local_datetime(start_dt), format_local_datetime(end_dt)


def normalize_time_pair(item, cfg):
    if not isinstance(item, dict):
        raise ValueError(f'time item 必须是对象: {item!r}')
    start_ts = item.get('start_utc_ts', item.get('startUtcTs'))
    end_ts = item.get('end_utc_ts', item.get('endUtcTs'))
    if start_ts is None or end_ts is None:
        raise ValueError(f'time item 缺少 start_utc_ts/end_utc_ts: {item!r}')
    plan_beg, plan_end = utc_timestamps_to_local_pair(start_ts, end_ts, cfg)
    return {
        'planBeg': plan_beg,
        'planEnd': plan_end,
        'source': 'config_list',
        'startUtcTs': int(start_ts),
        'endUtcTs': int(end_ts),
    }


def normalize_submission_plan_item(item, cfg, airspace_cache):
    if not isinstance(item, dict):
        raise ValueError(f'submit plan item 必须是对象: {item!r}')
    plan_beg = item.get('planBeg')
    plan_end = item.get('planEnd')
    if not plan_beg or not plan_end:
        raise ValueError(f'submit plan item 缺少 planBeg/planEnd: {item!r}')
    airspace = item.get('airspace')
    if not isinstance(airspace, dict):
        raise ValueError(f'submit plan item 缺少 airspace 对象: {item!r}')
    airspace_type = airspace.get('type')
    default_top = cfg.get('plan_defaults', {}).get('spcTop')
    if airspace_type == 'common_ref':
        ref_name = airspace.get('name')
        if not ref_name:
            raise ValueError(f'common_ref 缺少 name: {item!r}')
        cached = find_cached_airspace_by_name(airspace_cache, ref_name)
        space = normalize_space_payload(cached, default_top=default_top)
        return {
            'planBeg': plan_beg,
            'planEnd': plan_end,
            'source': 'submit_plan_file',
            'airspaceSource': 'airspace_cache',
            'airspaceRefName': ref_name,
            'space': space,
        }
    if airspace_type == 'polygon':
        inline_space = airspace.get('space')
        space = normalize_space_payload(inline_space, default_top=default_top)
        return {
            'planBeg': plan_beg,
            'planEnd': plan_end,
            'source': 'submit_plan_file',
            'airspaceSource': 'submit_plan_inline',
            'airspaceRefName': None,
            'space': space,
        }
    raise ValueError(f'不支持的 airspace.type: {airspace_type!r}')


def resolve_submission_items(cfg, latest_detail, cli_args):
    airspace_cache = load_airspace_cache()
    default_top = cfg.get('plan_defaults', {}).get('spcTop')
    if cli_args.start_utc_ts is not None or cli_args.end_utc_ts is not None:
        if cli_args.start_utc_ts is None or cli_args.end_utc_ts is None:
            raise ValueError('必须同时传 --start-utc-ts 和 --end-utc-ts')
        plan_beg, plan_end = utc_timestamps_to_local_pair(cli_args.start_utc_ts, cli_args.end_utc_ts, cfg)
        first_airspace = get_first_cached_airspace(airspace_cache)
        return [{
            'planBeg': plan_beg,
            'planEnd': plan_end,
            'source': 'cli_utc_pair',
            'startUtcTs': int(cli_args.start_utc_ts),
            'endUtcTs': int(cli_args.end_utc_ts),
            'airspaceSource': 'fallback_first_cached_airspace',
            'airspaceRefName': first_airspace.get('name'),
            'space': normalize_space_payload(first_airspace, default_top=default_top),
        }]

    if getattr(cli_args, 'use_submit_plan', False) or getattr(cli_args, 'use_time_list', False):
        data = load_submit_plan()
        items = data.get('plans', [])
        if not items:
            raise ValueError('submit_plan.json 中 plans 为空，无法按列表提交')
        if len(items) > 5:
            raise ValueError(f'submit_plan.json 的 plans 最多允许 5 条，本次配置了 {len(items)} 条')
        return [normalize_submission_plan_item(item, cfg, airspace_cache) for item in items]

    plan_beg, plan_end = get_tomorrow_same_time(latest_detail['planBeg'], latest_detail['planEnd'])
    first_airspace = get_first_cached_airspace(airspace_cache)
    return [{
        'planBeg': plan_beg,
        'planEnd': plan_end,
        'source': 'latest_plus_one_day',
        'airspaceSource': 'fallback_first_cached_airspace',
        'airspaceRefName': first_airspace.get('name'),
        'space': normalize_space_payload(first_airspace, default_top=default_top),
    }]


def describe_submission_item(item, index=None):
    prefix = f'[{index}] ' if index is not None else ''
    extra = ''
    if item.get('source') == 'cli_utc_pair':
        extra = f" | UTC: {item.get('startUtcTs')} -> {item.get('endUtcTs')}"
    return (
        f"{prefix}{item['planBeg']} ~ {item['planEnd']} ({item.get('source')})"
        f" | airspace={item.get('airspaceSource')} | ref={item.get('airspaceRefName') or '-'}{extra}"
    )


def resolve_time_pairs(cfg, latest_detail, cli_args):
    return resolve_submission_items(cfg, latest_detail, cli_args)


def describe_time_pair(pair, index=None):
    return describe_submission_item(pair, index=index)


def parse_polygon_wgs84(polygon_wgs84):
    if not polygon_wgs84 or not str(polygon_wgs84).strip():
        raise ValueError('polygonWgs84 不能为空')
    points = []
    for idx, token in enumerate(str(polygon_wgs84).split('|'), start=1):
        piece = token.strip()
        if not piece:
            continue
        try:
            lng_text, lat_text = [x.strip() for x in piece.split(',', 1)]
            lng = float(lng_text)
            lat = float(lat_text)
        except Exception as e:
            raise ValueError(f'polygonWgs84 第 {idx} 个点格式错误: {piece!r}') from e
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            raise ValueError(f'polygonWgs84 第 {idx} 个点超出经纬度范围: {piece!r}')
        points.append((lng, lat))
    if len(points) < 3:
        raise ValueError(f'polygonWgs84 至少需要 3 个点，当前只有 {len(points)} 个')

    deduped = []
    for point in points:
        if not deduped or point != deduped[-1]:
            deduped.append(point)
    if len(deduped) >= 2 and deduped[0] == deduped[-1]:
        deduped.pop()
    unique_points = set(deduped)
    if len(deduped) < 3 or len(unique_points) < 3:
        raise ValueError('polygonWgs84 需要至少 3 个互不相同的点')
    return deduped


def _format_polygon_point(point):
    lng, lat = point
    return f'{lng:.8f},{lat:.8f}'


def _rotate_strings_to_smallest(items):
    rotations = []
    total = len(items)
    for idx in range(total):
        rotations.append(items[idx:] + items[:idx])
    return min(rotations)


def normalize_polygon_wgs84(polygon_wgs84):
    points = parse_polygon_wgs84(polygon_wgs84)
    encoded = [_format_polygon_point(point) for point in points]
    forward = _rotate_strings_to_smallest(encoded)
    backward = _rotate_strings_to_smallest(list(reversed(encoded)))
    normalized = min(forward, backward)
    return '|'.join(normalized)


def build_airspace_query_cache_key(normalized_polygon_wgs84):
    raw = f'{AIRSPACE_QUERY_ALGO_VERSION}|{normalized_polygon_wgs84}'
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]
    return f'{AIRSPACE_QUERY_ALGO_VERSION}:{digest}:{normalized_polygon_wgs84}'
