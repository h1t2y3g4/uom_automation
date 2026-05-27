#!/usr/bin/env python3
"""
uom_core.py - UOM 持久化浏览器核心能力与统一 CLI 入口

职责：
- 持久化 Playwright profile 复用登录态
- 主站登录态 / 飞行活动 iframe oapi 认证检查
- 进入 一般飞行活动
- 读取最近计划 / 详情
- 打开新增页 / 自动填充 / 提交
- 提供公共底层能力，供上层脚本复用

说明：
- 这个文件主要承载底层函数，不是推荐的人类主入口
- 常用入口优先使用：uom_login.py / uom_submit_fly_plan.py
"""

import base64
import fcntl
import hashlib
import io
import json
import os
import select
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

try:
    from PIL import Image, ImageChops, ImageDraw
except Exception:
    Image = None
    ImageChops = None
    ImageDraw = None

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
CONFIG_FILE = PROJECT_ROOT / "config" / "config.json"
AIRSPACE_FILE = PROJECT_ROOT / "config" / "airspace.json"
SUBMIT_PLAN_FILE = PROJECT_ROOT / "config" / "submit_plan.json"
PERSIST_DIR = PROJECT_ROOT / ".playwright-uom-profile"
SMS_CODE_FILE = PROJECT_ROOT / "config" / "sms_code.json"
CAPTCHA_FILE = Path("/tmp/uom_persistent_captcha.png")
BASE_URL = "https://uom.caac.gov.cn"
MANUAL_SELECTION_LOG = PROJECT_ROOT / "log" / "manual_selection_log.json"
DEFAULT_RECENT_PLAN_DETAILS_FILE = PROJECT_ROOT / "log" / "uom_recent_plan_details.json"
AIRSPACE_QUERY_CACHE_FILE = PROJECT_ROOT / "cache" / "airspace_query_cache.json"
AIRSPACE_QUERY_ALGO_VERSION = "v1"
AIRSPACE_QUERY_WMS_KEYWORD = "/map/airspace/wms"
AIRSPACE_QUERY_MIN_ZOOM = 9
RUNTIME_DIR = PROJECT_ROOT / "runtime"
PROFILE_LOCK_FILE = RUNTIME_DIR / "locks" / "playwright-uom-profile.lock"
_PROFILE_LOCK_HANDLE = None


def parse_local_datetime(value: str):
    return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')


def get_now_local():
    return datetime.now()


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


def format_local_datetime(value: datetime):
    return value.strftime('%Y-%m-%d %H:%M:%S')


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


def sanitize_for_json(value):
    if isinstance(value, dict):
        return {str(k): sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_for_json(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat(sep=' ')
    if isinstance(value, Path):
        return str(value)
    return value


def load_json_file(path: Path, missing_hint: str):
    if not path.exists():
        raise FileNotFoundError(missing_hint)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_config():
    return load_json_file(CONFIG_FILE, f"config.json 不存在，请先创建 {CONFIG_FILE}")


def load_airspace_cache():
    return load_json_file(AIRSPACE_FILE, f"airspace.json 不存在，请先创建 {AIRSPACE_FILE}")


def load_submit_plan():
    return load_json_file(SUBMIT_PLAN_FILE, f"submit_plan.json 不存在，请先创建 {SUBMIT_PLAN_FILE}")


def require_pillow():
    if Image is None or ImageChops is None or ImageDraw is None:
        raise RuntimeError('当前环境缺少 Pillow，无法执行空域瓦片像素分析')


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


def load_airspace_query_cache():
    if not AIRSPACE_QUERY_CACHE_FILE.exists():
        return {
            'algoVersion': AIRSPACE_QUERY_ALGO_VERSION,
            'items': {},
            'updatedAt': None,
        }
    data = load_json_file(AIRSPACE_QUERY_CACHE_FILE, f"空域查询缓存文件不存在: {AIRSPACE_QUERY_CACHE_FILE}")
    if not isinstance(data, dict) or data.get('algoVersion') != AIRSPACE_QUERY_ALGO_VERSION:
        return {
            'algoVersion': AIRSPACE_QUERY_ALGO_VERSION,
            'items': {},
            'updatedAt': None,
        }
    items = data.get('items')
    if not isinstance(items, dict):
        items = {}
    return {
        'algoVersion': AIRSPACE_QUERY_ALGO_VERSION,
        'updatedAt': data.get('updatedAt'),
        'items': items,
    }


def save_airspace_query_cache(data):
    AIRSPACE_QUERY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'algoVersion': AIRSPACE_QUERY_ALGO_VERSION,
        'updatedAt': format_local_datetime(get_now_local()),
        'items': (data or {}).get('items', {}),
    }
    AIRSPACE_QUERY_CACHE_FILE.write_text(
        json.dumps(sanitize_for_json(payload), ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return AIRSPACE_QUERY_CACHE_FILE


def get_cached_airspace_query_result(normalized_polygon_wgs84):
    cache = load_airspace_query_cache()
    key = build_airspace_query_cache_key(normalized_polygon_wgs84)
    entry = cache.get('items', {}).get(key)
    if not isinstance(entry, dict):
        return None
    result = entry.get('result')
    if not isinstance(result, dict):
        return None
    payload = json.loads(json.dumps(sanitize_for_json(result), ensure_ascii=False))
    evidence = payload.get('evidence')
    if not isinstance(evidence, dict):
        evidence = {}
        payload['evidence'] = evidence
    evidence['cachedAt'] = entry.get('cachedAt')
    evidence['onlineQueriedAt'] = result.get('queriedAt')
    payload['status'] = 'cache_hit'
    payload['cacheHit'] = True
    payload['queriedAt'] = format_local_datetime(get_now_local())
    return payload


def store_airspace_query_result(normalized_polygon_wgs84, result):
    cache = load_airspace_query_cache()
    key = build_airspace_query_cache_key(normalized_polygon_wgs84)
    cache.setdefault('items', {})
    cache['items'][key] = {
        'normalizedPolygonWgs84': normalized_polygon_wgs84,
        'cachedAt': format_local_datetime(get_now_local()),
        'result': sanitize_for_json(result),
    }
    save_airspace_query_cache(cache)
    return key


def acquire_profile_lock():
    global _PROFILE_LOCK_HANDLE
    if _PROFILE_LOCK_HANDLE is not None:
        return
    PROFILE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = open(PROFILE_LOCK_FILE, 'a+', encoding='utf-8')
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as e:
        handle.seek(0)
        owner = handle.read().strip()
        handle.close()
        owner_text = owner or 'unknown'
        raise RuntimeError(
            f'另一个 UOM 脚本正在占用持久化浏览器 profile，请先结束它再重试。lock={PROFILE_LOCK_FILE} owner={owner_text}'
        ) from e
    handle.seek(0)
    handle.truncate()
    handle.write(
        f'pid={os.getpid()} startedAt={format_local_datetime(get_now_local())} cwd={os.getcwd()}'
    )
    handle.flush()
    _PROFILE_LOCK_HANDLE = handle


def release_profile_lock():
    global _PROFILE_LOCK_HANDLE
    handle = _PROFILE_LOCK_HANDLE
    if handle is None:
        return
    try:
        try:
            handle.seek(0)
            handle.truncate()
            handle.flush()
        except Exception:
            pass
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
    finally:
        try:
            handle.close()
        finally:
            _PROFILE_LOCK_HANDLE = None


def _build_airspace_query_tile_signature(snapshot):
    tiles = snapshot.get('wmsTiles') or []
    signature = []
    for tile in tiles[:40]:
        signature.append((
            tile.get('src'),
            round(float(tile.get('left') or 0), 2),
            round(float(tile.get('top') or 0), 2),
            round(float(tile.get('width') or 0), 2),
            round(float(tile.get('height') or 0), 2),
        ))
    return (
        snapshot.get('zoom'),
        len(tiles),
        tuple(signature),
    )


def _rects_intersect(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2):
    return ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1


def fetch_airspace_query_snapshot(page, polygon_points, fit_view=False):
    payload = [{'lng': lng, 'lat': lat} for lng, lat in polygon_points]
    return page.evaluate(
        r"""
        ([polygonPoints, fitView, minZoom, wmsKeyword]) => {
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const win = iframe.contentWindow;
            const doc = iframe.contentDocument;
            if (!win || !doc) return {ok:false, error:'no iframe content'};
            const app = doc.querySelector('#app');
            if (!app || !app.__vue__) return {ok:false, error:'no vue root'};
            let target = null;
            const seen = new Set();
            function walk(vm, depth) {
                if (!vm || depth > 12 || seen.has(vm)) return;
                seen.add(vm);
                const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                if (name === 'leaflet_map') {
                    target = vm;
                    return;
                }
                for (const child of (vm.$children || [])) walk(child, depth + 1);
            }
            walk(app.__vue__, 0);
            if (!target) return {ok:false, error:'leaflet_map not found'};
            const map = target.$data && target.$data.map;
            if (!map) return {ok:false, error:'leaflet_map map not ready'};
            if (!win.L) return {ok:false, error:'leaflet global missing'};

            const latLngs = (polygonPoints || []).map(item => win.L.latLng(item.lat, item.lng));
            const bounds = win.L.latLngBounds(latLngs);
            map.invalidateSize(false);
            if (fitView) {
                map.fitBounds(bounds.pad(0.6), {animate: false});
                if (map.getZoom() < minZoom) {
                    map.setView(bounds.getCenter(), minZoom, {animate: false});
                }
            }

            const container = map.getContainer();
            const mapRect = container.getBoundingClientRect();
            const polygonPixelPoints = latLngs.map(item => {
                const pt = map.latLngToContainerPoint(item);
                return {x: pt.x, y: pt.y};
            });

            const legendTexts = Array.from(doc.querySelectorAll('.legendInfo, .head-info'))
                .map(el => ((el.textContent || '').replace(/\s+/g, ' ').trim()))
                .filter(Boolean)
                .slice(0, 20);

            const wmsTiles = Array.from(doc.querySelectorAll('img.leaflet-tile'))
                .map((img, idx) => {
                    const src = img.src || '';
                    if (!src.includes(wmsKeyword)) return null;
                    const rect = img.getBoundingClientRect();
                    return {
                        idx,
                        src,
                        className: (img.className || '').toString().slice(0, 200),
                        left: rect.left - mapRect.left,
                        top: rect.top - mapRect.top,
                        width: rect.width,
                        height: rect.height,
                        naturalWidth: img.naturalWidth || 0,
                        naturalHeight: img.naturalHeight || 0,
                        opacity: Number.parseFloat(getComputedStyle(img).opacity || '1'),
                        display: getComputedStyle(img).display || '',
                    };
                })
                .filter(Boolean)
                .filter(item => item.width > 0 && item.height > 0 && item.display !== 'none');

            return {
                ok: true,
                zoom: map.getZoom(),
                center: map.getCenter ? {
                    lat: map.getCenter().lat,
                    lng: map.getCenter().lng,
                } : null,
                legendTexts,
                queryTextHead: ((doc.body && doc.body.innerText) || '').replace(/\s+/g, ' ').trim().slice(0, 500),
                mapSize: {
                    width: mapRect.width,
                    height: mapRect.height,
                },
                polygonPixelPoints,
                polygonBounds: {
                    west: bounds.getWest(),
                    south: bounds.getSouth(),
                    east: bounds.getEast(),
                    north: bounds.getNorth(),
                },
                wmsTiles,
            };
        }
        """,
        [payload, fit_view, AIRSPACE_QUERY_MIN_ZOOM, AIRSPACE_QUERY_WMS_KEYWORD],
    )


def wait_for_airspace_query_snapshot(page, polygon_points, timeout_s=18):
    deadline = time.time() + timeout_s
    last_snapshot = None
    last_signature = None
    stable_hits = 0
    while time.time() < deadline:
        snapshot = fetch_airspace_query_snapshot(page, polygon_points, fit_view=False)
        last_snapshot = snapshot
        if snapshot.get('ok') and snapshot.get('zoom', 0) >= AIRSPACE_QUERY_MIN_ZOOM:
            signature = _build_airspace_query_tile_signature(snapshot)
            if signature == last_signature:
                stable_hits += 1
            else:
                stable_hits = 1
                last_signature = signature
            if len(snapshot.get('wmsTiles') or []) > 0 and stable_hits >= 2:
                return snapshot
        time.sleep(1)
    return last_snapshot or {'ok': False, 'error': 'wait_for_airspace_query_snapshot timeout'}


def collect_airspace_query_online_state(page, polygon_wgs84):
    polygon_points = parse_polygon_wgs84(polygon_wgs84)
    setup = fetch_airspace_query_snapshot(page, polygon_points, fit_view=True)
    if not setup.get('ok'):
        return setup
    time.sleep(2)
    snapshot = wait_for_airspace_query_snapshot(page, polygon_points, timeout_s=18)
    if not snapshot.get('ok'):
        return snapshot
    snapshot['normalizedPolygonWgs84'] = normalize_polygon_wgs84(polygon_wgs84)
    return snapshot


def _count_non_zero_bytes(blob):
    return sum(1 for value in blob if value)


def analyze_airspace_query_snapshot(context, snapshot):
    require_pillow()
    if not snapshot.get('ok'):
        return {
            'ok': False,
            'judgement': 'unknown',
            'reason': snapshot.get('error') or 'snapshot_not_ready',
            'evidence': {
                'mapZoom': snapshot.get('zoom'),
                'wmsTileCount': len(snapshot.get('wmsTiles') or []),
                'legendTexts': snapshot.get('legendTexts') or [],
                'queryTextHead': snapshot.get('queryTextHead') or '',
            },
        }

    map_size = snapshot.get('mapSize') or {}
    canvas_width = max(1, int(round(map_size.get('width') or 0)))
    canvas_height = max(1, int(round(map_size.get('height') or 0)))
    polygon_pixels = snapshot.get('polygonPixelPoints') or []
    if len(polygon_pixels) < 3:
        return {
            'ok': False,
            'judgement': 'unknown',
            'reason': 'polygon_pixel_points_not_ready',
            'evidence': {
                'mapZoom': snapshot.get('zoom'),
                'wmsTileCount': len(snapshot.get('wmsTiles') or []),
            },
        }

    polygon_bbox = {
        'left': min(point['x'] for point in polygon_pixels),
        'top': min(point['y'] for point in polygon_pixels),
        'right': max(point['x'] for point in polygon_pixels),
        'bottom': max(point['y'] for point in polygon_pixels),
    }

    relevant_tiles = []
    for tile in snapshot.get('wmsTiles') or []:
        left = float(tile.get('left') or 0)
        top = float(tile.get('top') or 0)
        right = left + float(tile.get('width') or 0)
        bottom = top + float(tile.get('height') or 0)
        if not _rects_intersect(
            polygon_bbox['left'], polygon_bbox['top'], polygon_bbox['right'], polygon_bbox['bottom'],
            left, top, right, bottom,
        ):
            continue
        relevant_tiles.append(tile)

    overlay_mask = Image.new('L', (canvas_width, canvas_height), 0)
    fetch_failures = []
    fetched_tile_count = 0
    for tile in relevant_tiles:
        try:
            response = context.request.get(tile['src'], fail_on_status_code=False)
            if response.status != 200:
                fetch_failures.append({'url': tile['src'], 'status': response.status})
                continue
            rgba = Image.open(io.BytesIO(response.body())).convert('RGBA')
            alpha = rgba.getchannel('A')
            tile_left = int(round(tile.get('left') or 0))
            tile_top = int(round(tile.get('top') or 0))
            tile_width, tile_height = alpha.size
            src_left = 0
            src_top = 0
            dst_left = tile_left
            dst_top = tile_top
            dst_right = tile_left + tile_width
            dst_bottom = tile_top + tile_height
            if dst_right <= 0 or dst_bottom <= 0 or dst_left >= canvas_width or dst_top >= canvas_height:
                continue
            if dst_left < 0:
                src_left = -dst_left
                dst_left = 0
            if dst_top < 0:
                src_top = -dst_top
                dst_top = 0
            if dst_right > canvas_width:
                dst_right = canvas_width
            if dst_bottom > canvas_height:
                dst_bottom = canvas_height
            crop_box = (src_left, src_top, src_left + (dst_right - dst_left), src_top + (dst_bottom - dst_top))
            alpha_cropped = alpha.crop(crop_box)
            temp_mask = Image.new('L', (canvas_width, canvas_height), 0)
            temp_mask.paste(alpha_cropped, (dst_left, dst_top))
            overlay_mask = ImageChops.lighter(overlay_mask, temp_mask)
            fetched_tile_count += 1
        except Exception as e:
            fetch_failures.append({'url': tile.get('src'), 'error': str(e)})

    polygon_mask = Image.new('L', (canvas_width, canvas_height), 0)
    draw = ImageDraw.Draw(polygon_mask)
    draw.polygon([(point['x'], point['y']) for point in polygon_pixels], fill=255)

    polygon_mask_bytes = polygon_mask.tobytes()
    overlay_mask_bytes = overlay_mask.tobytes()
    polygon_pixel_count = _count_non_zero_bytes(polygon_mask_bytes)
    overlap_pixel_count = sum(
        1 for polygon_value, overlay_value in zip(polygon_mask_bytes, overlay_mask_bytes)
        if polygon_value and overlay_value
    )
    coverage_ratio = 0.0 if polygon_pixel_count == 0 else overlap_pixel_count / polygon_pixel_count

    if polygon_pixel_count == 0:
        judgement = 'unknown'
        ok = False
        reason = 'polygon_mask_empty'
    elif overlap_pixel_count == 0:
        judgement = 'outside_suitable'
        ok = True
        reason = None
    elif coverage_ratio >= 0.95:
        judgement = 'inside_suitable'
        ok = True
        reason = None
    else:
        judgement = 'partial_overlap'
        ok = True
        reason = None

    return {
        'ok': ok,
        'judgement': judgement,
        'reason': reason,
        'evidence': {
            'mapZoom': snapshot.get('zoom'),
            'legendTexts': snapshot.get('legendTexts') or [],
            'queryTextHead': snapshot.get('queryTextHead') or '',
            'allWmsTileCount': len(snapshot.get('wmsTiles') or []),
            'relevantWmsTileCount': len(relevant_tiles),
            'fetchedWmsTileCount': fetched_tile_count,
            'tileFetchFailures': fetch_failures,
            'polygonPixelCount': polygon_pixel_count,
            'overlapPixelCount': overlap_pixel_count,
            'coverageRatio': round(coverage_ratio, 6),
            'mapSize': snapshot.get('mapSize'),
            'polygonPixelBounds': polygon_bbox,
        },
    }


def query_airspace_polygon_online(page, context, polygon_wgs84):
    normalized_polygon_wgs84 = normalize_polygon_wgs84(polygon_wgs84)
    login_flow = ensure_main_login_with_auto_sms(page, settle_seconds=8)
    login_state = login_flow.get('statusAfter') or {}
    if login_state.get('onLoginPage') or not login_state.get('hasMainLogin'):
        return {
            'status': 'login_required',
            'judgement': 'unknown',
            'polygonWgs84': normalized_polygon_wgs84,
            'cacheHit': False,
            'queriedAt': format_local_datetime(get_now_local()),
            'evidence': {
                'loginFlow': sanitize_for_json(login_flow),
            },
        }

    menu_open = open_airspace_query_via_real_nav(page)
    time.sleep(6)
    snapshot = collect_airspace_query_online_state(page, normalized_polygon_wgs84)
    analysis = analyze_airspace_query_snapshot(context, snapshot)

    result = {
        'status': 'online_ok' if analysis.get('ok') else 'unknown',
        'judgement': analysis.get('judgement') or 'unknown',
        'polygonWgs84': normalized_polygon_wgs84,
        'cacheHit': False,
        'queriedAt': format_local_datetime(get_now_local()),
        'evidence': {
            **(analysis.get('evidence') or {}),
            'loginFlow': sanitize_for_json(login_flow),
            'menuOpenOk': menu_open.get('ok'),
            'snapshotOk': snapshot.get('ok'),
        },
    }
    if not analysis.get('ok') and analysis.get('reason'):
        result['evidence']['reason'] = analysis.get('reason')
    return result


def load_full_submit_profile():
    try:
        cfg = load_config()
        return {
            "uavs": [{
                "remark": None,
                "pubSeq": cfg["drone"].get("pubSeq"),
                "userId": None,
                "sn": cfg["drone"].get("sn"),
                "uasCode": cfg["drone"].get("uasCode"),
                "ownType": cfg["drone"].get("ownType"),
                "ownName": cfg["drone"].get("ownName"),
                "psnCardtype": cfg["drone"].get("psnCardtype"),
                "psnCardno": cfg["drone"].get("psnCardno"),
                "deptCode": None,
                "mnfName": cfg["drone"].get("mnfName"),
                "mnfCode": cfg["drone"].get("mnfCode"),
                "proMode": cfg["drone"].get("proMode"),
                "proName": cfg["drone"].get("proName"),
                "proClass": cfg["drone"].get("proClass"),
                "proType": cfg["drone"].get("proType"),
                "weightEmpty": cfg["drone"].get("weightEmpty"),
                "weightMax": cfg["drone"].get("weightMax"),
                "phone": cfg["drone"].get("phone_encrypted"),
                "delSts": cfg["drone"].get("delSts"),
                "plantProtector": cfg["drone"].get("plantProtector"),
                "uavPurpose": None,
                "uavState": None,
                "validSts": cfg["drone"].get("validSts"),
                "checkTime": None,
                "comm": None,
            }],
            "drivers": [{
                "remark": None,
                "pdiSeq": cfg["driver"].get("pdiSeq"),
                "userId": cfg["driver"].get("userId"),
                "name": cfg["driver"].get("name"),
                "cardtype": cfg["driver"].get("cardtype"),
                "cardno": cfg["driver"].get("cardno"),
                "licno": None,
                "licType": None,
                "lvlType": None,
                "lvlLevel": None,
                "lvlBvlos": None,
                "lvlTeacher": None,
                "dateIssue": cfg["driver"].get("dateIssue"),
                "dateLose": cfg["driver"].get("dateLose"),
                "phone": cfg["driver"].get("phone"),
                "pp": cfg["driver"].get("pp"),
                "uasCodes": [cfg["drone"].get("uasCode")],
            }]
        }
    except Exception:
        return None


def load_phone():
    phone = load_config().get("contact", {}).get("phone")
    if not phone:
        raise RuntimeError(f"config.json 缺少 contact.phone，请先在 {CONFIG_FILE} 中填写手机号")
    return phone


def read_sms_code_file():
    """读取 config/sms_code.json，文件不存在或异常时返回空结构。"""
    try:
        return json.loads(SMS_CODE_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {'code': '', 'sent_at': '', 'filled_at': ''}


def write_sms_code_file(data):
    """写入 config/sms_code.json。"""
    SMS_CODE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def is_sms_sent_within_window(sent_at_str, window_seconds=600):
    """判断 sent_at 是否在 window_seconds 秒内（默认 10 分钟）。"""
    if not sent_at_str:
        return False
    try:
        sent_dt = datetime.fromisoformat(sent_at_str)
        return (datetime.now() - sent_dt).total_seconds() < window_seconds
    except (ValueError, TypeError):
        return False


def wait_for_sms_code_from_file(timeout_s=600):
    """
    轮询 sms_code.json，每秒读一次。
    当 filled_at 非空、在 sent_at 之后、且距 sent_at 不超过 10 分钟时，返回 code。
    若 stdin 是终端（人工运行），同时监听 stdin，用户可直接输入验证码。
    超时返回 None。
    """
    interactive = sys.stdin.isatty()
    if interactive:
        print('提示：你也可以直接在此处输入短信验证码并回车，或在 sms_code.json 中写入后自动读取。')
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        # 1. 检查文件
        data = read_sms_code_file()
        code = (data.get('code') or '').strip()
        filled_at = (data.get('filled_at') or '').strip()
        sent_at = (data.get('sent_at') or '').strip()
        if code and filled_at and sent_at:
            try:
                sent_dt = datetime.fromisoformat(sent_at)
                filled_dt = datetime.fromisoformat(filled_at)
                if filled_dt >= sent_dt and (filled_dt - sent_dt).total_seconds() < 600:
                    return code
            except (ValueError, TypeError):
                pass

        # 2. 交互模式下非阻塞读 stdin
        if interactive:
            try:
                rlist, _, _ = select.select([sys.stdin], [], [], 0)
                if rlist:
                    line = sys.stdin.readline().strip()
                    if line:
                        return line
            except Exception:
                pass

        time.sleep(1)
    return None


def solve_captcha(image_path: str):
    try:
        import ddddocr
        ocr = ddddocr.DdddOcr(show_ad=False)
        with open(image_path, "rb") as f:
            return ocr.classification(f.read())
    except Exception as e:
        print(f"⚠️ ddddocr 识别失败: {e}")
        return None


def dismiss_popup(page):
    try:
        page.evaluate(
            """
            () => {
                const btns = document.querySelectorAll('.ivu-modal-confirm-footer .ivu-btn-primary, .ivu-modal-footer .ivu-btn-primary');
                for (const btn of btns) {
                    if (btn.offsetParent !== null) { btn.click(); return true; }
                }
                return false;
            }
            """
        )
    except Exception:
        pass


def handle_system_error(page, max_refresh=2):
    for _ in range(max_refresh):
        try:
            res = page.evaluate(
                r"""
                () => {
                    function norm(s) {
                        return (s || '').replace(/\s+/g, ' ').trim();
                    }
                    function visible(el) {
                        return !!(el && el.offsetParent !== null);
                    }
                    function clickish(el) {
                        if (!el) return false;
                        el.scrollIntoView({block: 'center', inline: 'center'});
                        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                            el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
                        }
                        return true;
                    }
                    const nodes = Array.from(document.querySelectorAll('div,span,button,a'));
                    const visibleTexts = nodes
                        .map(el => ({
                            el,
                            text: norm(el.textContent || ''),
                            visible: visible(el)
                        }))
                        .filter(x => x.visible && x.text);
                    const hasSystemError = visibleTexts.some(x => /系统错误/.test(x.text));
                    if (!hasSystemError) return {handled:false, found:false};
                    const refresh = visibleTexts.find(x => x.text === '刷新');
                    const ignore = visibleTexts.find(x => x.text === '忽略');
                    if (refresh) {
                        clickish(refresh.el);
                        return {handled:true, found:true, action:'refresh'};
                    }
                    if (ignore) {
                        clickish(ignore.el);
                        return {handled:true, found:true, action:'ignore'};
                    }
                    return {
                        handled:false,
                        found:true,
                        texts: visibleTexts.filter(x => /系统错误|刷新|忽略/.test(x.text)).map(x => x.text).slice(0, 20)
                    };
                }
                """
            )
        except Exception:
            res = {"handled": False, "found": False}
        if not res.get("found"):
            return res
        time.sleep(3)
    return {"handled": False, "found": True, "error": "system error persisted"}


def wait_for_login_component(page, timeout_s=20):
    deadline = time.time() + timeout_s
    js = r"""
    () => {
        const app = document.querySelector('#app');
        if (!app || !app.__vue__) return null;
        function find(vm, d) {
            if (d > 12) return null;
            if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
            for (const c of (vm.$children || [])) { const r = find(c, d + 1); if (r) return r; }
            return null;
        }
        const lm = find(app.__vue__, 0);
        if (!lm) return null;
        return {
            uuid: lm.txYzmUuid,
            hasCaptcha: !!lm.yzmImageCode,
            userForm: JSON.parse(JSON.stringify(lm.userForm))
        };
    }
    """
    while time.time() < deadline:
        try:
            res = page.evaluate(js)
            if res:
                return res
        except Exception:
            pass
        time.sleep(0.5)
    return None


def get_login_captcha(page):
    res = page.evaluate(
        """
        () => {
            const app = document.querySelector('#app').__vue__;
            function find(vm, d) {
                if (d > 12) return null;
                if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
                for (const c of (vm.$children || [])) { const r = find(c, d + 1); if (r) return r; }
                return null;
            }
            const lm = find(app, 0);
            return { imgBase64: lm.yzmImageCode, uuid: lm.txYzmUuid };
        }
        """
    )
    img_b64 = res["imgBase64"]
    if img_b64.startswith("data:"):
        img_b64 = img_b64.split(",", 1)[1]
    with open(CAPTCHA_FILE, "wb") as f:
        f.write(base64.b64decode(img_b64))
    return str(CAPTCHA_FILE), res["uuid"]


def is_success_code(code):
    return str(code) in ["0", "1"]


def fetch_login_captcha_with_ocr(page):
    captcha_path, uuid = get_login_captcha(page)
    ocr = solve_captcha(captcha_path)
    print(f"验证码图片: {captcha_path}")
    print(f"OCR 识别: {ocr or '失败'}")
    return {
        'captchaPath': captcha_path,
        'uuid': uuid,
        'ocr': ocr,
    }


def fill_login_phone(page, phone):
    """在登录页填入手机号（不发送短信）。"""
    return page.evaluate(
        """
        ([phone]) => {
            const app = document.querySelector('#app').__vue__;
            function find(vm, d) {
                if (d > 12) return null;
                if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
                for (const c of (vm.$children || [])) { const r = find(c, d + 1); if (r) return r; }
                return null;
            }
            const lm = find(app, 0);
            if (!lm) return {ok: false, error: '找不到登录组件'};
            lm.$set(lm.userForm, 'telephone', phone);
            return {ok: true};
        }
        """,
        [phone],
    )


def request_login_sms(page, phone, captcha):
    return page.evaluate(
        """
        async ([phone, captcha]) => {
            const app = document.querySelector('#app').__vue__;
            function find(vm, d) {
                if (d > 12) return null;
                if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
                for (const c of (vm.$children || [])) { const r = find(c, d + 1); if (r) return r; }
                return null;
            }
            const lm = find(app, 0);
            lm.$set(lm.userForm, 'telephone', phone);
            lm.$set(lm.userForm, 'tcode', captcha);
            const resp = await fetch('/api/home/anon/sendSmsCode', {
                method: 'POST',
                headers: {'Content-Type':'application/json','devicetype':'PC'},
                body: JSON.stringify({mobileNum: phone, icode: captcha, uuid: lm.txYzmUuid, scene: '1'})
            });
            return await resp.json();
        }
        """,
        [phone, captcha],
    )


def submit_login_sms_code(page, sms_code):
    return page.evaluate(
        """
        ([smsCode]) => {
            const app = document.querySelector('#app').__vue__;
            function find(vm, d) {
                if (d > 12) return null;
                if (vm.$data && vm.$data.personnalUsername !== undefined && vm.$data.userForm) return vm;
                for (const c of (vm.$children || [])) { const r = find(c, d + 1); if (r) return r; }
                return null;
            }
            const lm = find(app, 0);
            lm.$set(lm.userForm, 'dcode', smsCode);
            try {
                lm.handleSubmit();
                return {ok:true};
            } catch (e) {
                return {ok:false, error:e.message};
            }
        }
        """,
        [sms_code],
    )


def poll_login_result(page, timeout_s=20):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = check_main_login(page)
        if status.get("hasMainLogin"):
            return {"ok": True, "status": status}
        time.sleep(1)
    return {"ok": False, "error": "登录后仍未进入主站", "status": check_main_login(page)}


def is_captcha_error_response(payload):
    text = json.dumps(payload, ensure_ascii=False) if isinstance(payload, (dict, list)) else str(payload)
    return '图形验证码错误' in text


def is_sms_still_valid_response(payload):
    text = json.dumps(payload, ensure_ascii=False) if isinstance(payload, (dict, list)) else str(payload)
    return '短信验证码还在有效期内' in text



def login_via_sms(page):
    phone = load_phone()
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)
    dismiss_popup(page)
    comp = wait_for_login_component(page)
    if not comp:
        raise RuntimeError("找不到登录组件")

    login_trace = {
        'usedOcrAsDefaultCaptcha': True,
        'captchaAttempts': [],
        'smsCodeSource': 'file',
        'waitedForSmsInput': False,
        'reusedExistingSmsCode': False,
    }

    # ---- 始终填入手机号 ----
    fill_result = fill_login_phone(page, phone)
    print(f'填入手机号: {fill_result}')

    # ---- 判断是否需要重新发送短信 ----
    sms_file = read_sms_code_file()
    skip_send = is_sms_sent_within_window(sms_file.get('sent_at', ''))

    if skip_send:
        print('检测到 sms_code.json 中 sent_at 仍在 10 分钟有效期内，跳过重复发送')
        login_trace['reusedExistingSmsCode'] = True
    else:
        captcha_meta = fetch_login_captcha_with_ocr(page)
        captcha = (captcha_meta.get('ocr') or '').strip()
        if not captcha:
            raise RuntimeError("OCR 未识别出图形验证码")

        sms_result = request_login_sms(page, phone, captcha)
        login_trace['captchaAttempts'].append({
            'captcha': captcha,
            'uuid': captcha_meta.get('uuid'),
            'ocr': captcha_meta.get('ocr'),
            'smsResult': sms_result,
        })
        print("发短信返回:", sms_result)

        if not is_success_code(sms_result.get("code")):
            if is_sms_still_valid_response(sms_result):
                print('服务器返回：短信验证码仍在有效期内，不更新 sent_at，等待原有验证码写入。')
                login_trace['reusedExistingSmsCode'] = True
                # 不更新 sent_at，因为没有新短信发出，保留原发送时间
            elif is_captcha_error_response(sms_result):
                raise RuntimeError(
                    '本次发短信返回图形验证码错误。为避免重复发送短信导致限制，当前流程不会自动再次发短信。'
                    '请更新 sms_code.json 中的验证码后重新发起登录。'
                )
            else:
                raise RuntimeError(f"短信发送失败: {sms_result}")
        else:
            # 发送成功，记录 sent_at
            write_sms_code_file({
                'code': '',
                'sent_at': datetime.now().isoformat(),
                'filled_at': '',
            })
            print(f'短信已发送，请在 {SMS_CODE_FILE} 中写入验证码')

    # ---- 等待文件中的验证码 ----
    print('等待短信验证码写入 sms_code.json（每秒轮询，10 分钟超时）...')
    sms = wait_for_sms_code_from_file(timeout_s=600)
    if not sms:
        raise RuntimeError("等待短信验证码超时（10 分钟），登录中止")

    submit_result = submit_login_sms_code(page, sms)
    if not submit_result.get("ok"):
        submit_result['loginTrace'] = login_trace
        return submit_result

    poll_result = poll_login_result(page)
    poll_result['loginTrace'] = login_trace
    return poll_result


def check_main_login(page):
    try:
        return page.evaluate(
            r"""
            () => {
                const sessionToken = localStorage.getItem('session_token');
                let nrosToken = null;
                try {
                    nrosToken = window.nros && window.nros.getToken ? window.nros.getToken() : null;
                } catch(e) {}
                const text = document.body ? document.body.innerText : '';
                const hasMainUi = /系统主页|运行管理|首页/.test(text || '');
                const href = location.href || '';
                const onLoginPage = /#\/login(?:$|[?#])/.test(href);
                return {
                    url: href,
                    title: document.title,
                    nrosToken,
                    sessionToken,
                    hasMainUi,
                    onLoginPage,
                    hasMainLogin: !!(!onLoginPage && (nrosToken || hasMainUi))
                };
            }
            """
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}


def debug_menu_snapshot(page):
    try:
        return page.evaluate(
            r"""
            () => {
                const href = location.href || '';
                const title = document.title || '';
                const text = ((document.body && document.body.innerText) || '').replace(/\s+/g, ' ').trim();
                const snippets = [];
                const candidates = Array.from(document.querySelectorAll('div,span,li,a,button')).slice(0, 800);
                for (const el of candidates) {
                    const t = ((el.textContent || '').replace(/\s+/g, ' ').trim());
                    if (!t) continue;
                    if (/运行管理|飞行活动申请|一般飞行活动/.test(t)) {
                        snippets.push({
                            text: t,
                            tag: el.tagName,
                            cls: (el.className || '').toString().slice(0, 200)
                        });
                    }
                }
                return {
                    url: href,
                    title,
                    bodyTextHead: text.slice(0, 1200),
                    menuHits: snippets.slice(0, 40),
                    iframeCount: document.querySelectorAll('iframe').length,
                };
            }
            """
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_layout_menu_items(page):
    return page.evaluate(
        r"""
        () => {
            const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
            const app = document.querySelector('#app');
            if (!app || !app.__vue__) return {ok:false, error:'no vue root'};
            let layout = null;
            const seen = new Set();
            function walkVm(vm, depth) {
                if (!vm || depth > 8 || seen.has(vm)) return;
                seen.add(vm);
                const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                if (name === 'Layout') {
                    layout = vm;
                    return;
                }
                for (const c of (vm.$children || [])) walkVm(c, depth + 1);
            }
            walkVm(app.__vue__, 0);
            if (!layout) return {ok:false, error:'layout not found'};
            const tree = (layout.$data && layout.$data.menuTree) || [];
            const items = [];
            function walkMenu(nodes, depth) {
                for (const node of (nodes || [])) {
                    if (!node) continue;
                    items.push({
                        id: node.id || null,
                        title: norm(node.title || ''),
                        label: norm(node.label || ''),
                        appName: norm(node.appName || ''),
                        value: norm(node.value || ''),
                        routeLabel: norm(node.__label || ''),
                        routeValue: norm(node.__value || ''),
                        depth,
                        hasChildren: !!(node.children && node.children.length),
                    });
                    if (Array.isArray(node.children) && node.children.length) {
                        walkMenu(node.children, depth + 1);
                    }
                }
            }
            walkMenu(tree, 0);
            return {
                ok: true,
                count: items.length,
                items,
            };
        }
        """
    )


def wait_for_business_iframe(page, timeout_s=45):
    deadline = time.time() + timeout_s
    last_state = None
    while time.time() < deadline:
        try:
            state = page.evaluate(
                r"""
                () => ({
                    iframeCount: document.querySelectorAll('iframe').length,
                    hash: location.hash || '',
                    href: location.href || '',
                    bodyHead: ((document.body && document.body.innerText) || '').replace(/\s+/g, ' ').slice(0, 500)
                })
                """
            )
            last_state = state
            if state.get("iframeCount"):
                return state
        except Exception:
            pass
        try:
            err_loop = handle_system_error(page, max_refresh=1)
            if err_loop.get("found"):
                time.sleep(2)
        except Exception:
            pass
        time.sleep(1)
    return last_state or {"iframeCount": 0}


def activate_visible_business_tab(page, target_text, timeout_s=10):
    deadline = time.time() + timeout_s
    last_res = None
    while time.time() < deadline:
        try:
            res = page.evaluate(
                r"""
                ([targetText]) => {
                    const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
                    const isVisible = (el) => !!(el && el.offsetParent !== null);
                    const clickish = (el) => {
                        if (!el) return false;
                        el.scrollIntoView({block: 'center', inline: 'center'});
                        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                            el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
                        }
                        return true;
                    };
                    const keyword = norm(targetText || '');
                    const candidates = Array.from(document.querySelectorAll('a,button,span,div,li'))
                        .filter(isVisible)
                        .map(el => ({
                            el,
                            text: norm(el.textContent || ''),
                            className: (el.className || '').toString(),
                        }))
                        .filter(item => item.text);
                    const exact = candidates.find(item => item.text === keyword);
                    const partial = candidates.find(item => item.text.includes(keyword));
                    const chosen = exact || partial || null;
                    if (!chosen) {
                        return {
                            ok: false,
                            error: 'tab not found',
                            samples: candidates
                                .filter(item => /首页|空域|一般飞行活动|运行管理/.test(item.text))
                                .slice(0, 30)
                                .map(item => ({text: item.text, className: item.className.slice(0, 200)})),
                        };
                    }
                    clickish(chosen.el);
                    return {
                        ok: true,
                        text: chosen.text,
                        className: chosen.className.slice(0, 200),
                    };
                }
                """,
                [target_text],
            )
            last_res = res
            if res.get('ok'):
                return res
        except Exception as e:
            last_res = {"ok": False, "error": str(e)}
        time.sleep(1)
    return last_res or {"ok": False, "error": "timeout"}


def highlight_business_iframe(page, label=None):
    try:
        return page.evaluate(
            r"""
            ([label]) => {
                const iframe = document.querySelector('iframe');
                if (!iframe) return {ok:false, error:'no iframe'};
                iframe.scrollIntoView({block: 'center', inline: 'center'});
                iframe.style.outline = '4px solid #ff4d4f';
                iframe.style.outlineOffset = '2px';
                iframe.style.boxShadow = '0 0 0 4px rgba(255,77,79,0.2)';
                iframe.setAttribute('data-codex-highlight', label || 'iframe');
                return {
                    ok: true,
                    src: iframe.src || '',
                    width: iframe.clientWidth,
                    height: iframe.clientHeight,
                };
            }
            """,
            [label or 'business-iframe'],
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}


def click_visible_text(page, target_text, timeout_s=10, exact=True):
    deadline = time.time() + timeout_s
    last_res = None
    while time.time() < deadline:
        try:
            res = page.evaluate(
                r"""
                ([targetText, exact]) => {
                    const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
                    const isVisible = (el) => {
                        if (!el || el.offsetParent === null) return false;
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };
                    const clickish = (el) => {
                        el.scrollIntoView({block: 'center', inline: 'center'});
                        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                            el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
                        }
                    };
                    const target = norm(targetText || '');
                    const all = Array.from(document.querySelectorAll('a,button,span,div,li'))
                        .filter(isVisible)
                        .map(el => {
                            const text = norm(el.innerText || el.textContent || '');
                            const rect = el.getBoundingClientRect();
                            return {
                                el,
                                text,
                                tag: el.tagName,
                                className: (el.className || '').toString().slice(0, 200),
                                area: Math.round(rect.width * rect.height),
                            };
                        })
                        .filter(item => item.text);
                    let candidates = all.filter(item => exact ? item.text === target : item.text.includes(target));
                    if (!candidates.length && exact) {
                        candidates = all.filter(item => item.text.includes(target));
                    }
                    if (!candidates.length) {
                        return {
                            ok: false,
                            error: 'target not found',
                            target,
                            samples: all
                                .filter(item => /运行管理|空域信息查询|首页|一般飞行活动/.test(item.text))
                                .slice(0, 30)
                                .map(item => ({
                                    text: item.text,
                                    tag: item.tag,
                                    className: item.className,
                                    area: item.area,
                                })),
                        };
                    }
                    candidates.sort((a, b) => {
                        if (a.text.length !== b.text.length) return a.text.length - b.text.length;
                        return a.area - b.area;
                    });
                    const chosen = candidates[0];
                    clickish(chosen.el);
                    return {
                        ok: true,
                        target,
                        clicked: {
                            text: chosen.text,
                            tag: chosen.tag,
                            className: chosen.className,
                            area: chosen.area,
                        },
                        candidateCount: candidates.length,
                    };
                }
                """,
                [target_text, exact],
            )
            last_res = res
            if res.get('ok'):
                return res
        except Exception as e:
            last_res = {"ok": False, "error": str(e)}
        time.sleep(1)
    return last_res or {"ok": False, "error": "timeout"}


def click_cascader_path(page, path_texts, timeout_s=12):
    deadline = time.time() + timeout_s
    last_res = None
    while time.time() < deadline:
        try:
            res = page.evaluate(
                r"""
                ([pathTexts]) => {
                    const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
                    const isVisible = (el) => !!(el && el.offsetParent !== null && el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0);
                    const fireMouse = (el, type) => el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
                    const activate = (el) => {
                        el.scrollIntoView({block: 'center', inline: 'center'});
                        for (const type of ['mouseenter', 'mouseover', 'pointerdown', 'mousedown', 'mouseup', 'click']) {
                            fireMouse(el, type);
                        }
                    };
                    const dropdowns = Array.from(document.querySelectorAll('.ivu-cascader-dropdown')).filter(isVisible);
                    if (!dropdowns.length) {
                        return {ok:false, error:'no visible cascader dropdown'};
                    }
                    const dropdown = dropdowns[dropdowns.length - 1];
                    const trace = [];
                    for (const targetText of (pathTexts || [])) {
                        const menus = Array.from(dropdown.querySelectorAll('.ivu-cascader-menu')).filter(isVisible);
                        const items = menus.flatMap((menu, menuIdx) =>
                            Array.from(menu.querySelectorAll('.ivu-cascader-menu-item'))
                                .filter(isVisible)
                                .map((el, itemIdx) => ({
                                    el,
                                    menuIdx,
                                    itemIdx,
                                    text: norm(el.innerText || el.textContent || ''),
                                    className: (el.className || '').toString().slice(0, 200),
                                }))
                        );
                        let candidates = items.filter(item => item.text === targetText);
                        if (!candidates.length) {
                            candidates = items.filter(item => item.text.includes(targetText));
                        }
                        if (!candidates.length) {
                            return {
                                ok: false,
                                error: 'path item not found',
                                targetText,
                                trace,
                                visibleItems: items.slice(0, 120).map(item => ({
                                    menuIdx: item.menuIdx,
                                    itemIdx: item.itemIdx,
                                    text: item.text,
                                    className: item.className,
                                })),
                            };
                        }
                        candidates.sort((a, b) => a.text.length - b.text.length || a.menuIdx - b.menuIdx || a.itemIdx - b.itemIdx);
                        const chosen = candidates[0];
                        activate(chosen.el);
                        trace.push({
                            targetText,
                            clickedText: chosen.text,
                            menuIdx: chosen.menuIdx,
                            itemIdx: chosen.itemIdx,
                            className: chosen.className,
                        });
                    }
                    return {
                        ok: true,
                        trace,
                    };
                }
                """,
                [path_texts],
            )
            last_res = res
            if res.get('ok'):
                return res
        except Exception as e:
            last_res = {"ok": False, "error": str(e)}
        time.sleep(1)
    return last_res or {"ok": False, "error": "timeout"}


def click_top_menu_button(page, label_text, timeout_s=10):
    deadline = time.time() + timeout_s
    last_res = None
    while time.time() < deadline:
        try:
            res = page.evaluate(
                r"""
                ([labelText]) => {
                    const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
                    const isVisible = (el) => !!(el && el.offsetParent !== null && el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0);
                    const fireMouse = (el, type) => el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
                    const clickish = (el) => {
                        el.scrollIntoView({block: 'center', inline: 'center'});
                        for (const type of ['mouseenter', 'mouseover', 'pointerdown', 'mousedown', 'mouseup', 'click']) {
                            fireMouse(el, type);
                        }
                    };
                    const target = norm(labelText || '');
                    const labels = Array.from(document.querySelectorAll('.main-portal-header .topMenuBtnBox strong.topMenuBtn label'))
                        .filter(isVisible)
                        .map((label, idx) => {
                            const text = norm(label.innerText || label.textContent || '');
                            const button = label.closest('strong.topMenuBtn');
                            const rect = button ? button.getBoundingClientRect() : label.getBoundingClientRect();
                            return {
                                idx,
                                text,
                                label,
                                button,
                                className: button ? (button.className || '').toString().slice(0, 200) : '',
                                area: Math.round(rect.width * rect.height),
                            };
                        })
                        .filter(item => item.text);
                    let candidates = labels.filter(item => item.text === target);
                    if (!candidates.length) {
                        candidates = labels.filter(item => item.text.includes(target));
                    }
                    if (!candidates.length) {
                        return {
                            ok: false,
                            error: 'top menu label not found',
                            target,
                            labels: labels.map(item => ({
                                idx: item.idx,
                                text: item.text,
                                className: item.className,
                                area: item.area,
                            })),
                        };
                    }
                    candidates.sort((a, b) => a.text.length - b.text.length || a.idx - b.idx);
                    const chosen = candidates[0];
                    const clickable = chosen.button || chosen.label;
                    clickish(clickable);
                    return {
                        ok: true,
                        target,
                        clicked: {
                            text: chosen.text,
                            className: chosen.className,
                            area: chosen.area,
                        },
                        candidateCount: candidates.length,
                    };
                }
                """,
                [label_text],
            )
            last_res = res
            if res.get('ok'):
                return res
        except Exception as e:
            last_res = {"ok": False, "error": str(e)}
        time.sleep(1)
    return last_res or {"ok": False, "error": "timeout"}


def wait_for_business_iframe_state(page, timeout_s=20):
    deadline = time.time() + timeout_s
    last_state = None
    while time.time() < deadline:
        try:
            state = page.evaluate(
                r"""
                () => {
                    const iframe = document.querySelector('iframe');
                    if (!iframe) return {ok:false, iframeCount:0};
                    const rect = iframe.getBoundingClientRect();
                    const win = iframe.contentWindow;
                    const doc = iframe.contentDocument;
                    return {
                        ok: true,
                        iframeCount: document.querySelectorAll('iframe').length,
                        src: iframe.src || '',
                        width: Math.round(rect.width),
                        height: Math.round(rect.height),
                        display: getComputedStyle(iframe).display,
                        visibility: getComputedStyle(iframe).visibility,
                        href: win && win.location ? (win.location.href || '') : '',
                        readyState: doc ? (doc.readyState || '') : '',
                    };
                }
                """
            )
            last_state = state
            if state.get('iframeCount'):
                return state
        except Exception:
            pass
        time.sleep(1)
    return last_state or {"ok": False, "iframeCount": 0}


def open_business_page_via_real_nav(page, top_menu_text, cascader_path, wait_after_click_s=4):
    page.goto(f"{BASE_URL}/#/main", wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)
    dismiss_popup(page)
    top_menu_click = click_top_menu_button(page, top_menu_text, timeout_s=10)
    if not top_menu_click.get('ok'):
        top_menu_click = click_visible_text(page, top_menu_text, timeout_s=6, exact=True)
    if not top_menu_click.get('ok'):
        raise RuntimeError({
            'error': '点击顶栏菜单失败',
            'top_menu_text': top_menu_text,
            'click': top_menu_click,
            'snapshot': debug_menu_snapshot(page),
        })
    time.sleep(2)
    dismiss_popup(page)
    cascader_click = click_cascader_path(page, cascader_path, timeout_s=12)
    if not cascader_click.get('ok'):
        full_path_text = ' / '.join(cascader_path)
        cascader_click = click_visible_text(page, full_path_text, timeout_s=8, exact=False)
    if not cascader_click.get('ok') and cascader_path:
        cascader_click = click_visible_text(page, cascader_path[-1], timeout_s=8, exact=False)
    if not cascader_click.get('ok'):
        raise RuntimeError({
            'error': '点击级联菜单失败',
            'top_menu_text': top_menu_text,
            'cascader_path': cascader_path,
            'top_menu_click': top_menu_click,
            'click': cascader_click,
            'snapshot': debug_menu_snapshot(page),
        })
    time.sleep(wait_after_click_s)
    iframe_state = wait_for_business_iframe_state(page, timeout_s=20)
    return {
        'ok': bool(iframe_state.get('iframeCount')),
        'topMenuText': top_menu_text,
        'cascaderPath': cascader_path,
        'topMenuClick': top_menu_click,
        'cascaderClick': cascader_click,
        'iframeState': iframe_state,
    }


def open_airspace_query_via_real_nav(page):
    return open_business_page_via_real_nav(
        page,
        top_menu_text='运行管理',
        cascader_path=['运行管理', '空域信息查询'],
        wait_after_click_s=4,
    )


def open_layout_menu_item(page, target_text=None, target_id=None, expect_iframe=True):
    if not target_text and not target_id:
        raise ValueError('必须至少提供 target_text 或 target_id')
    page.goto(f"{BASE_URL}/#/main", wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)
    dismiss_popup(page)

    direct_res = page.evaluate(
        r"""
        ([targetText, targetId]) => {
            const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
            const app = document.querySelector('#app');
            if (!app || !app.__vue__) return {ok:false, error:'no vue root'};
            let layout = null;
            const seen = new Set();
            function walkVm(vm, depth) {
                if (!vm || depth > 8 || seen.has(vm)) return;
                seen.add(vm);
                const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                if (name === 'Layout') {
                    layout = vm;
                    return;
                }
                for (const c of (vm.$children || [])) walkVm(c, depth + 1);
            }
            walkVm(app.__vue__, 0);
            if (!layout) return {ok:false, error:'layout not found'};
            const tree = (layout.$data && layout.$data.menuTree) || [];
            const items = [];
            function walkMenu(nodes, depth) {
                for (const node of (nodes || [])) {
                    if (!node) continue;
                    items.push({
                        node,
                        depth,
                        meta: {
                            id: node.id || null,
                            title: norm(node.title || ''),
                            label: norm(node.label || ''),
                            appName: norm(node.appName || ''),
                            value: norm(node.value || ''),
                            routeLabel: norm(node.__label || ''),
                            routeValue: norm(node.__value || ''),
                            hasChildren: !!(node.children && node.children.length),
                        }
                    });
                    if (Array.isArray(node.children) && node.children.length) {
                        walkMenu(node.children, depth + 1);
                    }
                }
            }
            walkMenu(tree, 0);
            const keyword = norm(targetText || '');
            const keywordHit = keyword
                ? items.find(item => {
                    const haystack = [
                        item.meta.title,
                        item.meta.label,
                        item.meta.appName,
                        item.meta.value,
                        item.meta.routeLabel,
                        item.meta.routeValue,
                    ].join(' | ');
                    return haystack.includes(keyword);
                })
                : null;
            const idHit = targetId ? items.find(item => item.meta.id === targetId) : null;
            const chosen = idHit || keywordHit || null;
            if (!chosen) {
                return {
                    ok: false,
                    error: 'menu item not found',
                    targetText: keyword || null,
                    targetId: targetId || null,
                    candidates: items.slice(0, 80).map(item => item.meta),
                };
            }
            if (typeof layout.openPage !== 'function') {
                return {ok:false, error:'layout.openPage not available'};
            }
            try {
                layout.openPage(chosen.node);
                return {
                    ok: true,
                    path: 'layout.openPage',
                    matchedBy: idHit ? 'id' : 'text',
                    item: chosen.meta,
                    candidateCount: items.length,
                };
            } catch (e) {
                return {ok:false, error:'layout.openPage failed', detail:String(e), item:chosen.meta};
            }
        }
        """,
        [target_text, target_id],
    )
    if not direct_res.get("ok"):
        raise RuntimeError({
            "error": "直达业务菜单失败",
            "target_text": target_text,
            "target_id": target_id,
            "direct": direct_res,
            "snapshot": debug_menu_snapshot(page),
        })

    time.sleep(2)
    err_res = handle_system_error(page, max_refresh=3)
    tab_res = activate_visible_business_tab(page, target_text or '', timeout_s=8) if target_text else None
    if tab_res and tab_res.get('ok'):
        time.sleep(2)
    if not expect_iframe:
        return {
            "ok": True,
            "direct": direct_res,
            "system_error": err_res,
            "tab_activation": tab_res,
        }

    frame_debug = page.evaluate(
        r"""
        () => ({
            iframeCount: document.querySelectorAll('iframe').length,
            anchors: Array.from(document.querySelectorAll('a,button,span,div'))
                .map(el => ((el.textContent || '').replace(/\s+/g, ' ').trim()))
                .filter(Boolean)
                .slice(0, 50),
            hash: location.hash || '',
            href: location.href || ''
        })
        """
    )
    if frame_debug.get("iframeCount"):
        return {
            "ok": True,
            "direct": direct_res,
            "system_error": err_res,
            "tab_activation": tab_res,
            "frame_debug": frame_debug,
        }

    waited_state = wait_for_business_iframe(page, timeout_s=45)
    if waited_state and waited_state.get("iframeCount"):
        return {
            "ok": True,
            "direct": direct_res,
            "system_error": err_res,
            "tab_activation": tab_res,
            "frame_debug": frame_debug,
            "waited_state": waited_state,
        }

    raise RuntimeError({
        "error": "进入业务页面失败：未出现 iframe",
        "target_text": target_text,
        "target_id": target_id,
        "direct": direct_res,
        "system_error": err_res,
        "frame_debug": frame_debug,
        "waited_state": waited_state,
        "snapshot": debug_menu_snapshot(page),
    })


def open_fly_activity(page):
    try:
        nav_res = open_business_page_via_real_nav(
            page,
            top_menu_text='运行管理',
            cascader_path=['运行管理', '飞行活动申请', '一般飞行活动'],
            wait_after_click_s=5,
        )
        iframe_state = nav_res.get('iframeState') or {}
        if iframe_state.get('iframeCount'):
            return True
    except Exception:
        pass

    open_layout_menu_item(
        page,
        target_text='一般飞行活动',
        target_id='a2e16537-2fa4-4ca6-a935-1baf0efb111e',
        expect_iframe=True,
    )
    return True


def get_iframe_auth(page):
    return page.evaluate(
        """
        async () => {
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            const src = iframe.src;
            const u = new URL(src, location.origin);
            const ticket = u.searchParams.get('ticket');
            const userName = u.searchParams.get('userName');
            const cookie = doc.cookie || '';
            const m = cookie.match(/(?:^|; )PUB-Token=([^;]+)/);
            const pubToken = m ? decodeURIComponent(m[1]) : null;
            return {ok: !!(ticket && userName && pubToken), ticket, userName, pubToken, src, cookie};
        }
        """
    )


def check_oapi_auth(page):
    return page.evaluate(
        """
        async () => {
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            const src = iframe.src;
            const u = new URL(src, location.origin);
            const ticket = u.searchParams.get('ticket');
            const userName = u.searchParams.get('userName');
            const cookie = doc.cookie || '';
            const m = cookie.match(/(?:^|; )PUB-Token=([^;]+)/);
            const pubToken = m ? decodeURIComponent(m[1]) : null;
            if (!(ticket && userName && pubToken)) return {ok:false, error:'missing auth', ticket, userName, pubToken};
            const resp = await iframe.contentWindow.fetch('/oapi/pub/planInfo/list?pageNum=1&pageSize=1&planTypes=11,12,13', {
                headers: {
                    'Authorization': 'Bearer ' + pubToken,
                    'pubUserName': userName,
                    'ticket': ticket,
                    'deviceType': 'PC'
                },
                credentials: 'include'
            });
            const text = await resp.text();
            let data = null;
            try { data = JSON.parse(text); } catch(e) {}
            return {ok: resp.ok, status: resp.status, data, raw: text.slice(0, 1200)};
        }
        """
    )


def get_latest_plan(page):
    return get_recent_plans(page, page_num=1, page_size=5)


def get_recent_plans(page, page_num=1, page_size=5):
    return page.evaluate(
        """
        async ([pageNum, pageSize]) => {
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            const src = iframe.src;
            const u = new URL(src, location.origin);
            const ticket = u.searchParams.get('ticket');
            const userName = u.searchParams.get('userName');
            const cookie = doc.cookie || '';
            const m = cookie.match(/(?:^|; )PUB-Token=([^;]+)/);
            const pubToken = m ? decodeURIComponent(m[1]) : null;
            if (!(ticket && userName && pubToken)) return {ok:false, error:'missing auth'};
            const resp = await iframe.contentWindow.fetch(`/oapi/pub/planInfo/list?pageNum=${pageNum}&pageSize=${pageSize}&planTypes=11,12,13`, {
                headers: {
                    'Authorization': 'Bearer ' + pubToken,
                    'pubUserName': userName,
                    'ticket': ticket,
                    'deviceType': 'PC'
                },
                credentials: 'include'
            });
            const data = await resp.json();
            const rows = (data && data.data && data.data.rows) || data.rows || (data && data.data && data.data.list) || data.list || [];
            return {ok: true, total: rows.length, latest: rows[0] || null, rows, data, pageNum, pageSize};
        }
        """,
        [page_num, page_size]
    )


def get_plan_detail(page, plan_id):
    return page.evaluate(
        """
        async ([planId]) => {
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            const src = iframe.src;
            const u = new URL(src, location.origin);
            const ticket = u.searchParams.get('ticket');
            const userName = u.searchParams.get('userName');
            const cookie = doc.cookie || '';
            const m = cookie.match(/(?:^|; )PUB-Token=([^;]+)/);
            const pubToken = m ? decodeURIComponent(m[1]) : null;
            if (!(ticket && userName && pubToken)) return {ok:false, error:'missing auth'};
            const resp = await iframe.contentWindow.fetch('/oapi/pub/planInfo/' + planId, {
                headers: {
                    'Authorization': 'Bearer ' + pubToken,
                    'pubUserName': userName,
                    'ticket': ticket,
                    'deviceType': 'PC'
                },
                credentials: 'include'
            });
            const data = await resp.json();
            return data && data.data ? data.data : data;
        }
        """,
        [plan_id]
    )


def wait_for_fly_add(page, timeout_s=30):
    deadline = time.time() + timeout_s
    stable_ready_count = 0
    last_ready = None
    last_snapshot = None
    while time.time() < deadline:
        try:
            res = page.evaluate(
                r"""
                () => {
                    const iframe = document.querySelector('iframe');
                    if (!iframe) return {ok:false, error:'no iframe'};
                    const href = iframe.contentWindow ? iframe.contentWindow.location.href : '';
                    const doc = iframe.contentDocument;
                    if (!doc) return {ok:false, error:'no iframe doc', href};
                    const bodyText = ((doc.body && doc.body.innerText) || '').replace(/\s+/g, ' ').trim();
                    const app = doc.querySelector('#app');
                    const vueNames = [];
                    let mainComp = null;

                    function walk(vm, depth) {
                        if (!vm || depth > 15) return;
                        const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                        if (name) vueNames.push(name);
                        if (!mainComp && name === 'FLY_INDEX_ADD' && vm.$data && vm.$data.form) {
                            mainComp = vm;
                        }
                        for (const c of (vm.$children || [])) walk(c, depth + 1);
                    }

                    if (app && app.__vue__) walk(app.__vue__, 0);
                    const uniqNames = Array.from(new Set(vueNames));

                    const visibleAddButtons = Array.from(doc.querySelectorAll('button.addButton, button'))
                        .filter(el => el && el.offsetParent !== null)
                        .filter(el => ((el.textContent || '').replace(/\s+/g, ' ').trim()) === '添加');

                    const datetimeInputs = Array.from(doc.querySelectorAll('input')).filter(el => {
                        if (!el || el.offsetParent === null) return false;
                        const ph = (el.getAttribute('placeholder') || '').trim();
                        const cls = (el.className || '').toString();
                        return /日期|时间|选择日期|选择时间/.test(ph) || /date|time/i.test(cls);
                    });

                    const loadingVisible = Array.from(doc.querySelectorAll('div,span,section,aside')).some(el => {
                        if (!el || el.offsetParent === null) return false;
                        const cls = (el.className || '').toString();
                        const text = ((el.textContent || '').replace(/\s+/g, ' ').trim());
                        return /loading|spinner|is-loading|el-loading|ivu-spin|ant-spin/i.test(cls) || /加载中|提交中|处理中/.test(text);
                    });

                    const noticeVisible = Array.from(doc.querySelectorAll('div,section,aside,span'))
                        .filter(el => el && el.offsetParent !== null)
                        .some(el => /温馨提示/.test((el.textContent || '').replace(/\s+/g, ' ').trim()));

                    const form = mainComp && mainComp.$data ? mainComp.$data.form || {} : {};
                    const refKeys = mainComp && mainComp.$refs ? Object.keys(mainComp.$refs) : [];
                    const dataKeys = mainComp && mainComp.$data ? Object.keys(mainComp.$data) : [];
                    const hasKeyData = dataKeys.includes('showNoticeDialog') && dataKeys.includes('form');
                    const hasKeyRefs = refKeys.includes('form') && refKeys.includes('spaceSelection') && refKeys.includes('leafletMap');
                    const formShapeReady = (
                        form && typeof form === 'object' &&
                        Object.prototype.hasOwnProperty.call(form, 'planBeg') &&
                        Object.prototype.hasOwnProperty.call(form, 'planEnd') &&
                        Array.isArray(form.uavs) &&
                        Array.isArray(form.drivers) &&
                        Array.isArray(form.spaces)
                    );

                    const stabilityFingerprint = JSON.stringify({
                        href,
                        visibleAddButtonCount: visibleAddButtons.length,
                        datetimeInputCount: datetimeInputs.length,
                        loadingVisible,
                        noticeVisible,
                        hasKeyRefs,
                        hasKeyData,
                        formShapeReady,
                        vueNames: uniqNames.slice().sort(),
                        refKeys: refKeys.slice().sort(),
                        dataKeys: dataKeys.filter(k => /form|dialog|notice|uav|driver|space|loading/i.test(k)).slice().sort(),
                        bodyTextHead: bodyText.slice(0, 300),
                    });

                    const ready = (
                        href.includes('flyIndexAdd') &&
                        !!mainComp &&
                        visibleAddButtons.length >= 2 &&
                        datetimeInputs.length >= 2 &&
                        hasKeyRefs &&
                        hasKeyData &&
                        formShapeReady &&
                        !loadingVisible &&
                        /无人驾驶航空器飞行活动申请/.test(bodyText)
                    );

                    return {
                        ok: true,
                        ready,
                        loadingVisible,
                        noticeVisible,
                        stableFingerprint: stabilityFingerprint,
                        href,
                        hasVue: uniqNames.length > 0,
                        vueNames: uniqNames.slice(0, 20),
                        hasMainComp: !!mainComp,
                        refKeys: refKeys.slice(0, 20),
                        interestingDataKeys: dataKeys.filter(k => /form|dialog|notice|uav|driver|space|loading/i.test(k)).slice(0, 30),
                        visibleAddButtonCount: visibleAddButtons.length,
                        datetimeInputCount: datetimeInputs.length,
                        bodyText: bodyText.slice(0, 1000),
                        formSummary: {
                            planBegType: form && form.planBeg != null ? typeof form.planBeg : null,
                            planEndType: form && form.planEnd != null ? typeof form.planEnd : null,
                            hasUavsField: Array.isArray(form && form.uavs),
                            hasDriversField: Array.isArray(form && form.drivers),
                            hasSpacesField: Array.isArray(form && form.spaces),
                        },
                    };
                }
                """
            )
            last_snapshot = res
            if res and res.get('ready'):
                stable_key = res.get('stableFingerprint')
                if stable_key == last_ready:
                    stable_ready_count += 1
                else:
                    last_ready = stable_key
                    stable_ready_count = 1
                if stable_ready_count >= 3:
                    res['stableReadyCount'] = stable_ready_count
                    return res
            else:
                stable_ready_count = 0
                last_ready = None
        except Exception as e:
            last_snapshot = {'ok': False, 'error': str(e)}
        time.sleep(1)
    return last_snapshot


def open_new_fly_form(page):
    return page.evaluate(
        """
        () => {
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const app = iframe.contentDocument.querySelector('#app');
            if (!app || !app.__vue__) return {ok:false, error:'no vue root'};
            function findFlyIndex(vm, depth) {
                if (!vm || depth > 12) return null;
                const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                if (name === 'FLY_INDEX') return vm;
                for (const c of (vm.$children || [])) {
                    const r = findFlyIndex(c, depth + 1);
                    if (r) return r;
                }
                return null;
            }
            const comp = findFlyIndex(app.__vue__, 0);
            if (!comp) return {ok:false, error:'FLY_INDEX not found'};
            try {
                comp.addFly();
                return {ok:true};
            } catch (e) {
                return {ok:false, error:e.message};
            }
        }
        """
    )


def fill_new_form_from_detail(page, detail, plan_beg_new, plan_end_new, space_payload):
    return page.evaluate(
        r"""
        async ([detail, planBegNew, planEndNew, spacePayload]) => {
            const STEP_SLEEP_MS = 2000;
            const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            const app = doc.querySelector('#app');
            if (!app || !app.__vue__) return {ok:false, error:'no vue root'};

            function findMainForm(vm, depth) {
                if (!vm || depth > 15) return null;
                const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                if (name === 'FLY_INDEX_ADD' && vm.$data && vm.$data.form) return vm;
                for (const c of (vm.$children || [])) {
                    const r = findMainForm(c, depth + 1);
                    if (r) return r;
                }
                return null;
            }

            function findComponentsWithFlag(vm, flagName, depth = 0, out = []) {
                if (!vm || depth > 8 || out.length > 80) return out;
                try {
                    if (vm.$data && Object.prototype.hasOwnProperty.call(vm.$data, flagName)) out.push(vm);
                } catch (e) {}
                for (const c of (vm.$children || [])) findComponentsWithFlag(c, flagName, depth + 1, out);
                return out;
            }

            function norm(text) {
                return ((text || '').replace(/\s+/g, ' ').trim());
            }

            function isVisible(el) {
                return !!(el && el.offsetParent !== null);
            }

            function dispatchClick(el) {
                if (!el) return false;
                el.scrollIntoView({block: 'center', inline: 'center'});
                for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                    el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
                }
                return true;
            }

            function visibleAddButtons(root) {
                return Array.from(root.querySelectorAll('button.addButton, button, span, div, a')).filter(el => {
                    return norm(el.textContent) === '添加' && isVisible(el);
                });
            }

            function clone(obj) {
                return obj == null ? obj : JSON.parse(JSON.stringify(obj));
            }

            function collectVisibleDialogs() {
                const directMatches = Array.from(doc.querySelectorAll('.ivu-modal-wrap,.ivu-drawer-wrap,[role="dialog"],.el-dialog,.el-dialog__wrapper,.v-modal,.el-overlay,.el-message-box__wrapper'));
                const broadMatches = Array.from(doc.querySelectorAll('div,section,aside'))
                    .filter(el => isVisible(el))
                    .filter(el => {
                        const text = norm(el.innerText || el.textContent || '');
                        return /选择我的航空器|选择我的操控员|确定取消|产品序列号\/出厂序号实名登记标志|执照编号姓名|执照编号执照种类/.test(text);
                    });
                const merged = [];
                for (const el of [...directMatches, ...broadMatches]) {
                    if (!el || !isVisible(el) || merged.includes(el)) continue;
                    merged.push(el);
                }
                return merged.map(el => ({
                        text: norm((el.innerText || '')).slice(0, 800),
                        cls: (el.className || '').toString(),
                        tag: el.tagName,
                    }));
            }

            async function closeNoticeDialogIfPresent() {
                const noticeOwners = findComponentsWithFlag(comp, 'showNoticeDialog');
                for (let i = 0; i < 10; i++) {
                    const visibleDialogs = collectVisibleDialogs();
                    const notice = visibleDialogs.find(x => /温馨提示/.test(x.text || ''));
                    const ownerStates = noticeOwners.map(vm => ({
                        name: (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '',
                        value: vm.$data ? vm.$data.showNoticeDialog : undefined,
                        methods: Object.keys(vm || {}).filter(k => typeof vm[k] === 'function' && /notice|dialog|close|know|confirm/i.test(k)).slice(0, 12),
                    }));
                    const allClosed = ownerStates.length ? ownerStates.every(x => x.value === false) : true;
                    if (!notice && allClosed) return {ok:true, closed:true, attempts:i, remainingDialogs:visibleDialogs, ownerStates};

                    let clicked = false;
                    const buttons = Array.from(doc.querySelectorAll('button,span,div,a')).filter(isVisible);
                    for (const el of buttons) {
                        const t = norm(el.textContent);
                        const cls = (el.className || '').toString();
                        if (t === '我知道了' || (/dialog__footer/.test(cls) && /我知道了/.test(norm(el.innerText || '')))) {
                            dispatchClick(el);
                            clicked = true;
                            break;
                        }
                    }

                    for (const vm of noticeOwners) {
                        try {
                            if (vm.$data && Object.prototype.hasOwnProperty.call(vm.$data, 'showNoticeDialog')) {
                                vm.$data.showNoticeDialog = false;
                            }
                            for (const key of Object.keys(vm)) {
                                if (typeof vm[key] === 'function' && /notice|dialog|close|know|confirm/i.test(key)) {
                                    try { vm[key](); clicked = true; } catch (e) {}
                                }
                            }
                            if (typeof vm.$forceUpdate === 'function') vm.$forceUpdate();
                        } catch (e) {}
                    }
                    if (typeof comp.$forceUpdate === 'function') comp.$forceUpdate();

                    await sleep(STEP_SLEEP_MS);
                    const stillVisible = collectVisibleDialogs().some(x => /温馨提示/.test(x.text || ''));
                    const ownerStatesAfter = noticeOwners.map(vm => ({
                        name: (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '',
                        value: vm.$data ? vm.$data.showNoticeDialog : undefined,
                    }));
                    const allClosedAfter = ownerStatesAfter.length ? ownerStatesAfter.every(x => x.value === false) : true;
                    if (!stillVisible && allClosedAfter) {
                        return {ok:true, closed:true, attempts:i + 1, remainingDialogs:collectVisibleDialogs(), ownerStates:ownerStatesAfter};
                    }
                    if (!clicked && !noticeOwners.length) {
                        return {ok:false, error:'notice dialog visible but no clickable path or owner found', attempts:i, remainingDialogs:collectVisibleDialogs(), ownerStates:ownerStatesAfter};
                    }
                }
                return {ok:false, error:'notice dialog still visible after retries', remainingDialogs:collectVisibleDialogs(), ownerStates:noticeOwners.map(vm => ({name:(vm.$options && (vm.$options.name || vm.$options._componentTag)) || '', value: vm.$data ? vm.$data.showNoticeDialog : undefined}))};
            }

            function findDialogByTitle(titleRegex) {
                const direct = Array.from(doc.querySelectorAll('.ivu-modal-wrap,.ivu-drawer-wrap,[role="dialog"],.el-dialog,.el-dialog__wrapper,.el-message-box__wrapper,.el-overlay,.v-modal'))
                    .filter(el => isVisible(el) && titleRegex.test(norm(el.innerText || el.textContent || '')));
                if (direct.length) return direct[direct.length - 1];
                const broad = Array.from(doc.querySelectorAll('div,section,aside'))
                    .filter(el => isVisible(el) && titleRegex.test(norm(el.innerText || el.textContent || '')))
                    .sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
                if (broad.length) return broad[0];
                if (/选择我的航空器/.test(String(titleRegex))) {
                    const uavInfo = Array.from(doc.querySelectorAll('div,section,aside'))
                        .filter(el => isVisible(el) && /(app-container\s+uavInfo|\buavInfo\b)/.test((el.className || '').toString()))
                        .sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
                    if (uavInfo.length) return uavInfo[0];
                }
                if (/选择我的操控员|选择操控员/.test(String(titleRegex))) {
                    const driverInfo = Array.from(doc.querySelectorAll('div,section,aside'))
                        .filter(el => isVisible(el) && /(app-container\s+driverInfo|\bdriverInfo\b|app-container\s+operator|\boperator\b)/.test((el.className || '').toString()))
                        .sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
                    if (driverInfo.length) return driverInfo[0];
                }
                return null;
            }

            function selectRowInDialog(dialogEl, keyword) {
                if (!dialogEl || !keyword) return {ok:false, error:'missing dialog or keyword'};
                const rows = Array.from(dialogEl.querySelectorAll('tr')).filter(isVisible);
                const altKeywords = Array.from(new Set(String(keyword).split(/\s+/).map(s => s.trim()).filter(Boolean)));
                for (const row of rows) {
                    const text = norm(row.innerText || row.textContent || '');
                    if (!text) continue;
                    if (text.includes(keyword) || altKeywords.some(k => k && text.includes(k))) {
                        const checkbox = row.querySelector('.el-checkbox__original, input[type="checkbox"]');
                        const checkboxWrap = row.querySelector('.el-checkbox, .el-checkbox__input, label');
                        if (checkbox) {
                            checkbox.scrollIntoView({block: 'center', inline: 'center'});
                            if (!checkbox.checked) checkbox.click();
                            if (!checkbox.checked && checkboxWrap) dispatchClick(checkboxWrap);
                            if (!checkbox.checked) dispatchClick(row);
                            return {ok:!!checkbox.checked, method:'checkbox', text:text.slice(0, 200), checked:!!checkbox.checked, keyword, altKeywords};
                        }
                        dispatchClick(row);
                        return {ok:true, method:'row', text:text.slice(0, 200), keyword, altKeywords};
                    }
                }
                return {ok:false, error:'row not found', keyword, altKeywords, availableRows: rows.map(row => norm(row.innerText || row.textContent || '').slice(0, 200)).filter(Boolean).slice(0, 10)};
            }

            function clickDialogConfirm(dialogEl) {
                if (!dialogEl) return {ok:false, error:'no dialog'};
                const candidates = Array.from(dialogEl.querySelectorAll('button,span,div,a')).filter(isVisible);
                for (const el of candidates) {
                    const t = norm(el.textContent);
                    if (t === '确定') {
                        dispatchClick(el);
                        return {ok:true, text:t, tag:el.tagName, cls:(el.className || '').toString()};
                    }
                }
                return {ok:false, error:'confirm button not found'};
            }

            function sectionTitleFor(el) {
                let node = el;
                for (let i = 0; node && i < 8; i++, node = node.parentElement) {
                    const text = norm(node.innerText || node.textContent || '');
                    if (/航空器信息/.test(text)) return 'uav';
                    if (/操控员信息/.test(text)) return 'driver';
                    if (/飞行空域信息/.test(text)) return 'space';
                }
                return '';
            }

            function describeAddButton(el, index) {
                if (!el) return null;
                const parent = el.parentElement;
                const row = parent ? parent.parentElement : null;
                return {
                    index,
                    text: norm(el.textContent),
                    tag: el.tagName,
                    cls: (el.className || '').toString(),
                    section: sectionTitleFor(el),
                    parentText: norm(parent && (parent.innerText || parent.textContent || '')).slice(0, 160),
                    rowText: norm(row && (row.innerText || row.textContent || '')).slice(0, 220),
                };
            }

            function listAddButtons(root) {
                return visibleAddButtons(root).map((el, index) => ({el, meta: describeAddButton(el, index)}));
            }

            function findAddButtonBySection(root, section) {
                const buttons = listAddButtons(root);
                const buttonTagPreferred = buttons.find(x => x.meta && x.meta.section === section && x.el && x.el.tagName === 'BUTTON' && /\baddButton\b/.test((x.el.className || '').toString()));
                if (buttonTagPreferred) return buttonTagPreferred;
                const buttonPreferred = buttons.find(x => x.meta && x.meta.section === section && x.el && x.el.tagName === 'BUTTON');
                if (buttonPreferred) return buttonPreferred;
                const spanPreferred = buttons.find(x => x.meta && x.meta.section === section && x.el && x.el.tagName === 'SPAN');
                if (spanPreferred) return spanPreferred;
                const exact = buttons.find(x => x.meta && x.meta.section === section);
                if (exact) return exact;
                const fallbackButton = buttons.find(x => x.el && x.el.tagName === 'BUTTON');
                if (fallbackButton) return fallbackButton;
                return section === 'uav' ? buttons[0] || null : buttons[1] || null;
            }

            function dumpVueComponentNames(vm, depth = 0, out = []) {
                if (!vm || depth > 6 || out.length > 160) return out;
                const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                out.push({
                    depth,
                    name,
                    refKeys: vm.$refs ? Object.keys(vm.$refs).slice(0, 12) : [],
                    dataKeys: Object.keys(vm.$data || {}).filter(k => /uav|driver|select|check|space|dialog|table|choose|row|current/i.test(k)).slice(0, 20),
                });
                for (const c of (vm.$children || [])) dumpVueComponentNames(c, depth + 1, out);
                return out;
            }

            function tryInvoke(obj, key, ...args) {
                try {
                    if (obj && typeof obj[key] === 'function') {
                        return {ok:true, value: obj[key](...args)};
                    }
                } catch (e) {
                    return {ok:false, error:e.message};
                }
                return {ok:false, missing:true};
            }

            function setValidationLikeState(comp, sourceUav, sourceDriver) {
                const notes = [];
                if ('uavInfoCheck' in comp.$data) {
                    comp.$data.uavInfoCheck = !!(sourceUav);
                    notes.push('set uavInfoCheck');
                }
                if ('selectMap' in comp.$data && sourceUav) {
                    comp.$data.selectMap = sourceUav.uasCode || sourceUav.sn || 'selected';
                    notes.push('set selectMap');
                }
                if (Array.isArray(comp.$data.checkList)) {
                    const values = [];
                    if (sourceUav && (sourceUav.uasCode || sourceUav.sn)) values.push(sourceUav.uasCode || sourceUav.sn);
                    if (sourceDriver && (sourceDriver.name || sourceDriver.cardno)) values.push(sourceDriver.name || sourceDriver.cardno);
                    comp.$data.checkList = Array.from(new Set([...(comp.$data.checkList || []), ...values]));
                    notes.push('extended checkList');
                }
                if ('noCheckFlag' in comp.$data) {
                    comp.$data.noCheckFlag = false;
                    notes.push('set noCheckFlag=false');
                }
                return notes;
            }

            const comp = findMainForm(app.__vue__, 0);
            if (!comp) return {ok:false, error:'FLY_INDEX_ADD not found'};
            const f = comp.$data.form || {};
            const noticeOwners = findComponentsWithFlag(comp, 'showNoticeDialog').map(vm => ({
                name: (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '',
                value: vm.$data ? vm.$data.showNoticeDialog : undefined,
                dataKeys: Object.keys(vm.$data || {}).slice(0, 20),
            }));
            const beforeKeys = Object.keys(comp.$data || {}).filter(k => /uav|driver|select|check|space|dialog|table|choose/i.test(k));
            const sourceSpace = spacePayload || {};
            const sourceUav = Array.isArray(detail.uavs) && detail.uavs.length ? clone(detail.uavs[0]) : null;
            const sourceDriver = Array.isArray(detail.drivers) && detail.drivers.length ? clone(detail.drivers[0]) : null;

            const noticeHandling = await closeNoticeDialogIfPresent();
            await sleep(STEP_SLEEP_MS);
            const addButtonsBefore = listAddButtons(doc).map(x => x.meta);

            const uavAddButton = findAddButtonBySection(doc, 'uav');
            const clickedUavAdd = uavAddButton ? (() => { dispatchClick(uavAddButton.el); return {...uavAddButton.meta, ok:true, outerHTML: (uavAddButton.el.outerHTML || '').slice(0, 400)}; })() : {ok:false};
            await sleep(STEP_SLEEP_MS);
            const uavPickDialog = findDialogByTitle(/选择我的航空器/);
            const uavDialogCandidates = collectVisibleDialogs();
            const uavSelectionKeyword = sourceUav ? [sourceUav.uasCode, sourceUav.sn, sourceUav.proName, sourceUav.proMode].filter(Boolean).join(' ') : '';
            const uavSelection = uavPickDialog && sourceUav ? selectRowInDialog(uavPickDialog, uavSelectionKeyword) : {ok:false, error:'uav dialog not found', dialogs:uavDialogCandidates};
            await sleep(STEP_SLEEP_MS);
            const uavConfirm = uavPickDialog ? clickDialogConfirm(uavPickDialog) : {ok:false, error:'uav dialog not found', dialogs:uavDialogCandidates};
            await sleep(STEP_SLEEP_MS);
            const uavDialogsAfterClick = collectVisibleDialogs();
            const uavVueAfterClick = dumpVueComponentNames(comp);

            const shouldOpenDriverDialog = !!(uavSelection && uavSelection.ok && uavConfirm && uavConfirm.ok);
            const driverAddButton = findAddButtonBySection(doc, 'driver');
            const clickedDriverAdd = shouldOpenDriverDialog && driverAddButton ? (() => { dispatchClick(driverAddButton.el); return {...driverAddButton.meta, ok:true, outerHTML: (driverAddButton.el.outerHTML || '').slice(0, 400)}; })() : {ok:false, skipped:true, reason:'uav selection/confirm not completed'};
            await sleep(STEP_SLEEP_MS);
            const driverPickDialog = shouldOpenDriverDialog ? findDialogByTitle(/选择我的操控员|选择操控员/) : null;
            const driverDialogCandidates = collectVisibleDialogs();
            const driverSelectionKeyword = sourceDriver ? [sourceDriver.name, sourceDriver.cardno, sourceDriver.phone].filter(Boolean).join(' ') : '';
            const driverSelection = driverPickDialog && sourceDriver ? selectRowInDialog(driverPickDialog, driverSelectionKeyword) : {ok:false, error:'driver dialog not found', dialogs:driverDialogCandidates};
            await sleep(STEP_SLEEP_MS);
            const driverConfirm = driverPickDialog ? clickDialogConfirm(driverPickDialog) : {ok:false, error:'driver dialog not found', dialogs:driverDialogCandidates};
            await sleep(STEP_SLEEP_MS);
            const driverDialogsAfterClick = collectVisibleDialogs();
            const driverVueAfterClick = dumpVueComponentNames(comp);

            f.planBeg = planBegNew;
            f.planEnd = planEndNew;
            if (f.planBegStr !== undefined) f.planBegStr = planBegNew;
            if (f.planEndStr !== undefined) f.planEndStr = planEndNew;
            if (detail.planType) f.planType = detail.planType;
            if (detail.taskType) f.taskType = detail.taskType;
            if (detail.txll) f.txll = detail.txll;
            if (detail.spcTop) f.spcTop = detail.spcTop;

            if (sourceSpace && Object.keys(sourceSpace).length) {
                const space = {
                    ...sourceSpace,
                    locationWgs84: sourceSpace.locationWgs84 || sourceSpace.polygonWgs84 || '',
                    polygonWgs84: sourceSpace.polygonWgs84 || sourceSpace.locationWgs84 || '',
                    spcShape: sourceSpace.spcShape || '1',
                    spcBottom: sourceSpace.spcBottom ?? 0,
                    spcTop: sourceSpace.spcTop ?? detail.spcTop ?? 120,
                    spcName: sourceSpace.spcName || '',
                    groupName: sourceSpace.groupName || '空域1',
                    spaceShape: sourceSpace.spaceShape || '面',
                    index: 0,
                    polyLinePoints: sourceSpace.polyLinePoints || [],
                    polygonPoints: sourceSpace.polygonPoints || [],
                    radius: sourceSpace.radius ?? null,
                    lineWidth: sourceSpace.lineWidth ?? null,
                };
                f.spaces = [space];
                comp.$data.spaceList = [clone(space)];
                comp.$data.oldSpaceList = [clone(space)];
                tryInvoke(comp, 'callbackAddSpace', [space]);
            }

            const hookResults = {};
            const candidatePickerComponents = [];
            const allowProgrammaticBackfill = false;

            if (allowProgrammaticBackfill && sourceUav) {
                f.uavs = [sourceUav];
                comp.$data.uavInfoList = [clone(sourceUav)];
                hookResults.callbackAddUavs = tryInvoke(comp, 'callbackAddUavs', [sourceUav]);
                hookResults.handleSelectionChangeUav = tryInvoke(comp, 'handleSelectionChangeUav', [sourceUav]);
                hookResults.handleCurrentChangeUav = tryInvoke(comp, 'handleCurrentChangeUav', sourceUav);
                hookResults.selectUav = tryInvoke(comp, 'selectUav', sourceUav);
            }

            if (allowProgrammaticBackfill && sourceDriver) {
                if (!sourceDriver.uasCodes && sourceUav && sourceUav.uasCode) sourceDriver.uasCodes = [sourceUav.uasCode];
                f.drivers = [sourceDriver];
                comp.$data.driverInfoList = [clone(sourceDriver)];
                comp.$data.currentRowDriver = clone(sourceDriver);
                if (Array.isArray(comp.$data.currentRowDriver)) comp.$data.currentRowDriver = clone(sourceDriver);
                if (Array.isArray(comp.$data.currentRowDriver)) comp.$data.currentRowDriver = clone(sourceDriver[0] || sourceDriver);
                comp.$data.selectDriverIndex = 0;
                hookResults.callbackAddDrivers = tryInvoke(comp, 'callbackAddDrivers', [sourceDriver]);
                hookResults.handleSelectionDriverUavs = tryInvoke(comp, 'handleSelectionDriverUavs', sourceUav ? [sourceUav] : [], sourceDriver);
                hookResults.handleSelectionChangeDriver = tryInvoke(comp, 'handleSelectionChangeDriver', [sourceDriver]);
                hookResults.handleCurrentChangeDriver = tryInvoke(comp, 'handleCurrentChangeDriver', sourceDriver);
                hookResults.selectDriver = tryInvoke(comp, 'selectDriver', sourceDriver);
                hookResults.currentRowDriverTypeAfterSet = Array.isArray(comp.$data.currentRowDriver) ? 'array' : typeof comp.$data.currentRowDriver;
            }

            if (allowProgrammaticBackfill) {
                hookResults.validationStateTweaks = setValidationLikeState(comp, sourceUav, sourceDriver);
            }

            try {
                const uavTable = comp.$refs && (comp.$refs.uavTable || comp.$refs.uavInfoTable || comp.$refs.tableUav || comp.$refs.tableUavs);
                if (allowProgrammaticBackfill && uavTable && typeof uavTable.toggleRowSelection === 'function' && sourceUav) {
                    uavTable.toggleRowSelection(sourceUav, true);
                    hookResults.uavTableToggle = {ok:true};
                }
            } catch (e) {
                hookResults.uavTableToggle = {ok:false, error:e.message};
            }

            try {
                const driverTable = comp.$refs && (comp.$refs.driverTable || comp.$refs.driverInfoTable || comp.$refs.tableDriver || comp.$refs.tableDrivers);
                if (allowProgrammaticBackfill && driverTable && typeof driverTable.toggleRowSelection === 'function' && sourceDriver) {
                    driverTable.toggleRowSelection(sourceDriver, true);
                    hookResults.driverTableToggle = {ok:true};
                }
            } catch (e) {
                hookResults.driverTableToggle = {ok:false, error:e.message};
            }

            if (typeof comp.$forceUpdate === 'function') comp.$forceUpdate();
            await sleep(STEP_SLEEP_MS);
            if (comp.$refs && comp.$refs.form && typeof comp.$refs.form.clearValidate === 'function') comp.$refs.form.clearValidate();

            return {
                ok: true,
                mode: 'stepwise-ui-plus-hooks',
                compName: (comp.$options && (comp.$options.name || comp.$options._componentTag)) || '',
                noticeHandling,
                noticeOwners,
                clickedUavAdd,
                clickedDriverAdd,
                uavSelection,
                uavConfirm,
                driverSelection,
                driverConfirm,
                addButtonsBefore,
                uavDialogsAfterClick,
                driverDialogsAfterClick,
                uavVueAfterClick,
                driverVueAfterClick,
                hookResults,
                candidatePickerComponents,
                planBeg: f.planBeg,
                planEnd: f.planEnd,
                spcTop: f.spcTop,
                txll: f.txll,
                uavCount: Array.isArray(comp.$data.uavInfoList) ? comp.$data.uavInfoList.length : 0,
                driverCount: Array.isArray(comp.$data.driverInfoList) ? comp.$data.driverInfoList.length : 0,
                spaceCount: Array.isArray(comp.$data.spaceList) ? comp.$data.spaceList.length : 0,
                stateKeys: beforeKeys,
                refKeys: comp.$refs ? Object.keys(comp.$refs) : [],
                firstSpace: comp.$data.spaceList && comp.$data.spaceList[0] ? {
                    locationWgs84: comp.$data.spaceList[0].locationWgs84,
                    polygonWgs84: comp.$data.spaceList[0].polygonWgs84,
                    spcTop: comp.$data.spaceList[0].spcTop,
                    spcBottom: comp.$data.spaceList[0].spcBottom,
                } : null,
            };
        }
        """,
        [detail, plan_beg_new, plan_end_new, space_payload],
    )


def update_copied_form_times(page, plan_beg_new, plan_end_new):
    return page.evaluate(
        """
        ([planBegNew, planEndNew]) => {
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            const rootEl = doc.querySelector('#app') || doc.body;
            if (!rootEl) return {ok:false, error:'no root el'};
            function collectVueRoots() {
                const roots = [];
                const all = [rootEl, ...Array.from(rootEl.querySelectorAll('*')).slice(0, 300)];
                for (const el of all) {
                    if (el && el.__vue__) roots.push(el.__vue__);
                }
                return roots;
            }
            function findMainForm(vm, depth) {
                if (!vm || depth > 15) return null;
                const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                if (name === 'FLY_INDEX_ADD' && vm.$data && vm.$data.form) return vm;
                for (const c of (vm.$children || [])) {
                    const r = findMainForm(c, depth + 1);
                    if (r) return r;
                }
                return null;
            }

            function findComponentsWithFlag(vm, flagName, depth = 0, out = []) {
                if (!vm || depth > 8 || out.length > 80) return out;
                try {
                    if (vm.$data && Object.prototype.hasOwnProperty.call(vm.$data, flagName)) out.push(vm);
                } catch (e) {}
                for (const c of (vm.$children || [])) findComponentsWithFlag(c, flagName, depth + 1, out);
                return out;
            }
            let comp = null;
            for (const root of collectVueRoots()) {
                comp = findMainForm(root, 0);
                if (comp) break;
            }
            if (!comp) return {ok:false, error:'FLY_INDEX_ADD not found'};
            const f = comp.$data.form || {};
            f.planBeg = planBegNew;
            f.planEnd = planEndNew;
            if (f.planBegStr !== undefined) f.planBegStr = planBegNew;
            if (f.planEndStr !== undefined) f.planEndStr = planEndNew;
            if (typeof comp.$forceUpdate === 'function') comp.$forceUpdate();
            return {ok:true, planBeg:f.planBeg, planEnd:f.planEnd};
        }
        """,
        [plan_beg_new, plan_end_new],
    )


def trigger_submit_copied_form(page):
    return page.evaluate(
        r"""
        async () => {
            const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            const app = doc.querySelector('#app');
            if (!app || !app.__vue__) return {ok:false, error:'no vue root'};
            function findMainForm(vm, depth) {
                if (!vm || depth > 15) return null;
                const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                if (name === 'FLY_INDEX_ADD' && vm.$data && vm.$data.form) return vm;
                for (const c of (vm.$children || [])) {
                    const r = findMainForm(c, depth + 1);
                    if (r) return r;
                }
                return null;
            }

            function norm(text) {
                return ((text || '').replace(/\s+/g, ' ').trim());
            }

            function isVisible(el) {
                return !!(el && el.offsetParent !== null);
            }

            function dispatchClick(el) {
                if (!el) return false;
                el.scrollIntoView({block: 'center', inline: 'center'});
                for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                    el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
                }
                return true;
            }

            function collectVisibleDialogs() {
                return Array.from(doc.querySelectorAll('.ivu-modal-wrap,.ivu-drawer-wrap,[role="dialog"],.el-dialog,.el-dialog__wrapper,.v-modal,.el-overlay,.el-message-box__wrapper,div,section,aside'))
                    .filter(el => isVisible(el))
                    .map(el => ({
                        text: norm((el.innerText || el.textContent || '')).slice(0, 800),
                        cls: (el.className || '').toString(),
                        tag: el.tagName,
                    }))
                    .filter(x => x.text && /提交申请|确认提交|确定取消|确定提交|是否提交|失败|错误|异常|成功/.test(x.text))
                    .slice(0, 20);
            }

            function clickConfirmSubmitDialog() {
                const visible = Array.from(doc.querySelectorAll('button,span,div,a')).filter(isVisible);
                const confirmBtn = visible.find(el => {
                    const text = norm(el.textContent);
                    return text === '提交' || text === '确认提交' || text === '确定';
                });
                if (confirmBtn) {
                    dispatchClick(confirmBtn);
                    return {ok:true, text:norm(confirmBtn.textContent), cls:(confirmBtn.className || '').toString(), tag:confirmBtn.tagName};
                }
                return {ok:false, error:'confirm submit button not found', dialogs:collectVisibleDialogs()};
            }

            function collectPageSignals() {
                const text = norm(doc.body && (doc.body.innerText || doc.body.textContent || ''));
                const dialogs = collectVisibleDialogs();
                return {
                    text: text.slice(0, 3000),
                    dialogs,
                    hasFailure: /保存飞行活动失败|提交失败|失败|错误|异常/.test(text) || dialogs.some(x => /保存飞行活动失败|提交失败|失败|错误|异常/.test(x.text || '')),
                    hasSuccess: /提交成功|申请成功|成功/.test(text) || dialogs.some(x => /提交成功|申请成功|成功/.test(x.text || '')),
                };
            }

            async function runSingleAttempt(attempt) {
                const result = {attempt};
                const submitBtn = Array.from(doc.querySelectorAll('button,span,div,a')).find(el => norm(el.textContent) === '提交申请' && isVisible(el));
                if (submitBtn) {
                    dispatchClick(submitBtn);
                    result.clickedSubmitButton = true;
                } else {
                    result.clickedSubmitButton = false;
                }
                try {
                    if (typeof comp.submitPlan === 'function') {
                        comp.submitPlan();
                        result.calledSubmitPlan = true;
                    } else {
                        result.calledSubmitPlan = false;
                    }
                } catch (e) {
                    result.submitPlanError = e.message;
                }
                await sleep(1000);
                result.submitDialogsAfterPrimarySubmit = collectVisibleDialogs();
                result.confirmSubmitClick = clickConfirmSubmitDialog();
                await sleep(1500);
                result.submitDialogsAfterConfirm = collectVisibleDialogs();
                result.signals = collectPageSignals();
                result.failed = !!result.signals.hasFailure;
                result.succeeded = !!result.signals.hasSuccess && !result.signals.hasFailure;
                return result;
            }

            const comp = findMainForm(app.__vue__, 0);
            if (!comp) return {ok:false, error:'FLY_INDEX_ADD not found'};
            const payload = {
                ok:true,
                before: {
                    uavs: Array.isArray(comp.$data.form && comp.$data.form.uavs) ? comp.$data.form.uavs.length : null,
                    drivers: Array.isArray(comp.$data.form && comp.$data.form.drivers) ? comp.$data.form.drivers.length : null,
                    spaces: Array.isArray(comp.$data.form && comp.$data.form.spaces) ? comp.$data.form.spaces.length : null,
                },
                attempts: []
            };
            payload.attempts.push(await runSingleAttempt(1));
            const first = payload.attempts[0] || {};
            payload.success = !!first.succeeded;
            payload.failure = !!first.failed;
            payload.signals = first.signals || collectPageSignals();
            return payload;
        }
        """
    )


def open_takeoff_confirmation(page, plan_id, plan_beg):
    return page.evaluate(
        r"""
        ([planId, planBeg]) => {
            const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            if (!doc) return {ok:false, error:'no iframe doc'};
            const targetPlanId = String(planId || '');
            const targetPlanBeg = String(planBeg || '');
            const visibleRows = Array.from(doc.querySelectorAll('tr, .el-table__row, .ivu-table-row, li, .card, .list-item'))
                .filter(el => el && el.offsetParent !== null);
            const rows = visibleRows.map((el, idx) => ({ index: idx, el, text: norm(el.textContent) }));
            let row = rows.find(x => targetPlanId && x.text.includes(targetPlanId));
            if (!row) row = rows.find(x => targetPlanBeg && x.text.includes(targetPlanBeg));
            if (!row) {
                return {
                    ok:false,
                    error:'target_row_not_found',
                    targetPlanId,
                    targetPlanBeg,
                    rowPreview: rows.map(x => x.text.slice(0, 400)).slice(0, 20),
                };
            }
            const clickable = Array.from(row.el.querySelectorAll('a,button,span,div'))
                .filter(el => el && el.offsetParent !== null)
                .map(el => ({
                    el,
                    text: norm(el.textContent),
                    tag: el.tagName,
                    className: (el.className || '').toString(),
                    outerHTML: (el.outerHTML || '').slice(0, 1200),
                    href: el.getAttribute ? (el.getAttribute('href') || null) : null,
                }));
            const target = clickable.find(x => x.tag === 'A' && /flyIndexTakeoff/.test(x.href || '') && /起飞确认/.test(x.text || ''))
                || clickable.find(x => /起飞确认/.test(x.text || '') && /flyIndexTakeoff/.test(x.outerHTML || ''))
                || clickable.find(x => /起飞确认/.test(x.text || ''));
            if (!target) {
                return {
                    ok:false,
                    error:'takeoff_entry_not_found',
                    matchedRow:{index:row.index, text:row.text.slice(0, 1000)},
                    clickable: clickable.map(x => ({text:x.text, tag:x.tag, className:x.className, href:x.href, outerHTML:x.outerHTML})).slice(0, 50),
                };
            }
            if (typeof target.el.click === 'function') target.el.click();
            return {
                ok:true,
                matchedRow:{index:row.index, text:row.text.slice(0, 1000)},
                clicked:{text:target.text, tag:target.tag, className:target.className, href:target.href, outerHTML:target.outerHTML},
            };
        }
        """,
        [plan_id, plan_beg],
    )


def wait_for_takeoff_page(page, timeout_s=30):
    deadline = time.time() + timeout_s
    last_snapshot = None
    last_ready = None
    stable_ready_count = 0
    while time.time() < deadline:
        snapshot = page.evaluate(
            r"""
            () => {
                const iframe = document.querySelector('iframe');
                if (!iframe) return {ok:false, error:'no iframe'};
                const doc = iframe.contentDocument;
                if (!doc) return {ok:false, error:'no iframe doc'};
                const href = iframe.contentWindow ? iframe.contentWindow.location.href : '';
                const bodyText = ((doc.body && doc.body.innerText) || '').replace(/\s+/g, ' ').trim();
                const visibleInputs = Array.from(doc.querySelectorAll('input, textarea')).filter(el => el && el.offsetParent !== null);
                const visibleButtons = Array.from(doc.querySelectorAll('button, a, span, div')).filter(el => el && el.offsetParent !== null);
                const ready = href.includes('flyIndexTakeoff') && visibleButtons.some(el => /提交|确认|确定/.test((el.textContent || '').replace(/\s+/g, ' ').trim()));
                return {
                    ok:true,
                    ready,
                    href,
                    bodyText: bodyText.slice(0, 3000),
                    visibleInputCount: visibleInputs.length,
                    visibleButtonTexts: visibleButtons.map(el => ((el.textContent || '').replace(/\s+/g, ' ').trim())).filter(Boolean).filter(t => /提交|确认|确定|准备|起飞/.test(t)).slice(0, 80),
                    fingerprint: JSON.stringify({
                        href,
                        inputCount: visibleInputs.length,
                        bodyHead: bodyText.slice(0, 500),
                        actionTexts: visibleButtons.map(el => ((el.textContent || '').replace(/\s+/g, ' ').trim())).filter(Boolean).filter(t => /提交|确认|确定|准备|起飞/.test(t)).slice(0, 20),
                    }),
                };
            }
            """
        )
        last_snapshot = snapshot
        if snapshot.get('ready'):
            key = snapshot.get('fingerprint')
            if key == last_ready:
                stable_ready_count += 1
            else:
                last_ready = key
                stable_ready_count = 1
            if stable_ready_count >= 2:
                return snapshot
        time.sleep(1)
    return last_snapshot or {ok:false, error:'timeout'}


def get_takeoff_form_snapshot(page, stage):
    return js_eval(page, r"""
    (stage) => {
      const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
      const iframe = document.querySelector('iframe');
      if (!iframe) return {ok:false, stage, error:'no iframe'};
      const doc = iframe.contentDocument;
      if (!doc) return {ok:false, stage, error:'no iframe doc'};
      const bodyText = norm(doc.body && doc.body.innerText).slice(0, 4000);
      const inputs = Array.from(doc.querySelectorAll('input, textarea')).filter(el => el && el.offsetParent !== null).map((el, idx) => ({
        index: idx,
        tag: el.tagName,
        type: el.getAttribute('type') || '',
        value: (el.value || '').slice(0, 1000),
        placeholder: (el.getAttribute('placeholder') || '').slice(0, 300),
        className: (el.className || '').toString().slice(0, 500),
      })).slice(0, 80);
      const buttons = Array.from(doc.querySelectorAll('button, a, span, div')).filter(el => el && el.offsetParent !== null).map((el, idx) => ({
        index: idx,
        tag: el.tagName,
        text: norm(el.textContent).slice(0, 300),
        className: (el.className || '').toString().slice(0, 500),
      })).filter(x => x.text && /提交|确认|确定|取消|准备|起飞/.test(x.text)).slice(0, 120);
      return {
        ok:true,
        stage,
        href: iframe.contentWindow ? iframe.contentWindow.location.href : null,
        bodyText,
        inputs,
        buttons,
      };
    }
    """, stage, stage)


def fill_takeoff_confirmation_form(page, prepare_text):
    return page.evaluate(
        r"""
        (prepareText) => {
            const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            if (!doc) return {ok:false, error:'no iframe doc'};
            const visibleInputs = Array.from(doc.querySelectorAll('input, textarea')).filter(el => el && el.offsetParent !== null);
            const actions = [];

            function setNativeValue(el, value) {
                const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                if (desc && desc.set) desc.set.call(el, value);
                else el.value = value;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
            }

            let target = visibleInputs.find(el => /准备/.test(norm(el.placeholder || '')));
            if (!target) target = visibleInputs.find(el => /备注|说明/.test(norm(el.placeholder || '')));
            if (!target) target = visibleInputs.find(el => /准备/.test(norm(el.parentElement && el.parentElement.textContent)));
            if (!target && visibleInputs.length === 1) target = visibleInputs[0];
            if (!target) {
                return {
                    ok:false,
                    error:'prepare_input_not_found',
                    inputs: visibleInputs.map((el, idx) => ({
                        index: idx,
                        tag: el.tagName,
                        type: el.getAttribute('type') || '',
                        placeholder: el.getAttribute('placeholder') || '',
                        className: (el.className || '').toString(),
                        parentText: norm(el.parentElement && el.parentElement.textContent).slice(0, 500),
                    })).slice(0, 40),
                };
            }

            target.focus();
            setNativeValue(target, prepareText);
            actions.push({
                kind:'set_input',
                tag: target.tagName,
                placeholder: target.getAttribute('placeholder') || '',
                className: (target.className || '').toString(),
                value: target.value || '',
            });

            return {ok:true, actions};
        }
        """,
        prepare_text,
    )


def submit_takeoff_confirmation_ui(page):
    return page.evaluate(
        r"""
        async () => {
            const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
            const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            if (!doc) return {ok:false, error:'no iframe doc'};

            function isVisible(el) {
                return !!(el && el.offsetParent !== null);
            }

            function dispatchClick(el) {
                if (!el) return false;
                el.scrollIntoView({block: 'center', inline: 'center'});
                for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                    el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
                }
                return true;
            }

            const before = Array.from(doc.querySelectorAll('button, a, span, div')).filter(isVisible).map(el => ({
                text: norm(el.textContent),
                tag: el.tagName,
                className: (el.className || '').toString(),
            })).filter(x => x.text && /提交|确认|确定|取消|准备|起飞|成功|失败|错误/.test(x.text)).slice(0, 80);

            const primary = Array.from(doc.querySelectorAll('button, a, span, div')).filter(isVisible).find(el => {
                const text = norm(el.textContent);
                return text === '提交' || text === '确认提交' || text === '确认';
            });
            if (!primary) {
                return {ok:false, error:'submit_button_not_found', before};
            }
            const clicked = {text: norm(primary.textContent), tag: primary.tagName, className: (primary.className || '').toString()};
            dispatchClick(primary);
            await sleep(1200);

            const confirm = Array.from(doc.querySelectorAll('button, a, span, div')).filter(isVisible).find(el => {
                const text = norm(el.textContent);
                return text === '确定' || text === '确认提交' || text === '提交';
            });
            let confirmClicked = null;
            if (confirm) {
                confirmClicked = {text: norm(confirm.textContent), tag: confirm.tagName, className: (confirm.className || '').toString()};
                dispatchClick(confirm);
            }
            await sleep(2000);

            const text = norm(doc.body && doc.body.innerText).slice(0, 4000);
            const dialogs = Array.from(doc.querySelectorAll('button, a, span, div')).filter(isVisible).map(el => ({
                text: norm(el.textContent),
                tag: el.tagName,
                className: (el.className || '').toString(),
            })).filter(x => x.text && /提交|确认|确定|取消|成功|失败|错误|异常/.test(x.text)).slice(0, 100);

            return {
                ok:true,
                clicked,
                confirmClicked,
                text,
                dialogs,
                hasFailure: /失败|错误|异常/.test(text),
                hasSuccess: /成功|确认中/.test(text),
            };
        }
        """
    )


def inspect_add_dialogs(page, detail, plan_beg_new, plan_end_new, space_payload=None):
    return page.evaluate(
        r"""
        async ([detail, planBegNew, planEndNew]) => {
            const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
            const iframe = document.querySelector('iframe');
            if (!iframe) return {ok:false, error:'no iframe'};
            const doc = iframe.contentDocument;
            const app = doc.querySelector('#app');
            if (!app || !app.__vue__) return {ok:false, error:'no vue root'};

            function findMainForm(vm, depth) {
                if (!vm || depth > 15) return null;
                const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                if (name === 'FLY_INDEX_ADD' && vm.$data && vm.$data.form) return vm;
                for (const c of (vm.$children || [])) {
                    const r = findMainForm(c, depth + 1);
                    if (r) return r;
                }
                return null;
            }

            function findComponentsWithFlag(vm, flagName, depth = 0, out = []) {
                if (!vm || depth > 8 || out.length > 80) return out;
                try {
                    if (vm.$data && Object.prototype.hasOwnProperty.call(vm.$data, flagName)) out.push(vm);
                } catch (e) {}
                for (const c of (vm.$children || [])) findComponentsWithFlag(c, flagName, depth + 1, out);
                return out;
            }

            function norm(s) {
                return ((s || '').replace(/\s+/g, ' ').trim());
            }

            function isVisible(el) {
                return !!(el && el.offsetParent !== null);
            }

            function dispatchClick(el) {
                if (!el) return false;
                el.scrollIntoView({block: 'center', inline: 'center'});
                for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                    el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
                }
                return true;
            }

            function dumpVisibleDialogs(root) {
                return Array.from(root.querySelectorAll('.ivu-modal-wrap,.ivu-drawer-wrap,[role="dialog"],.el-dialog,.el-dialog__wrapper')).filter(isVisible).map(el => ({
                    text: norm((el.innerText || '')).slice(0, 1000),
                    cls: (el.className || '').toString(),
                    tag: el.tagName,
                }));
            }

            function sectionTitleFor(el) {
                let node = el;
                for (let i = 0; node && i < 8; i++, node = node.parentElement) {
                    const text = norm(node.innerText || node.textContent || '');
                    if (/航空器信息/.test(text)) return 'uav';
                    if (/操控员信息/.test(text)) return 'driver';
                    if (/飞行空域信息/.test(text)) return 'space';
                }
                return '';
            }

            function listAddButtons(root) {
                return Array.from(root.querySelectorAll('button,span,div,a'))
                    .filter(el => norm(el.textContent) === '添加' && isVisible(el))
                    .map((el, index) => ({
                        index,
                        text: norm(el.textContent),
                        tag: el.tagName,
                        cls: (el.className || '').toString(),
                        section: sectionTitleFor(el),
                        parentText: norm(el.parentElement && (el.parentElement.innerText || el.parentElement.textContent || '')).slice(0, 160),
                    }));
            }

            function findAddButtonBySection(root, section) {
                const buttonEntries = Array.from(root.querySelectorAll('button,span,div,a'))
                    .filter(el => norm(el.textContent) === '添加' && isVisible(el))
                    .map(el => ({el, section: sectionTitleFor(el)}));
                const buttonTagPreferred = buttonEntries.find(x => x.section === section && x.el.tagName === 'BUTTON' && /\baddButton\b/.test((x.el.className || '').toString()));
                if (buttonTagPreferred) return buttonTagPreferred.el;
                const buttonPreferred = buttonEntries.find(x => x.section === section && x.el.tagName === 'BUTTON');
                if (buttonPreferred) return buttonPreferred.el;
                const spanPreferred = buttonEntries.find(x => x.section === section && x.el.tagName === 'SPAN');
                if (spanPreferred) return spanPreferred.el;
                const exact = buttonEntries.find(x => x.section === section);
                if (exact) return exact.el;
                const fallbackButton = buttonEntries.find(x => x.el.tagName === 'BUTTON');
                if (fallbackButton) return fallbackButton.el;
                return section === 'uav' ? (buttonEntries[0] && buttonEntries[0].el) || null : (buttonEntries[1] && buttonEntries[1].el) || null;
            }

            function dumpVueTree(vm, depth = 0, out = []) {
                if (!vm || depth > 5 || out.length > 120) return out;
                out.push({
                    depth,
                    name: (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '',
                    refKeys: vm.$refs ? Object.keys(vm.$refs).slice(0, 12) : [],
                    dataKeys: Object.keys(vm.$data || {}).filter(k => /uav|driver|select|check|space|dialog|table|choose/i.test(k)).slice(0, 20),
                });
                for (const c of (vm.$children || [])) dumpVueTree(c, depth + 1, out);
                return out;
            }

            const comp = findMainForm(app.__vue__, 0);
            if (!comp) return {ok:false, error:'FLY_INDEX_ADD not found'};
            const addButtons = listAddButtons(doc);
            const uavAdd = findAddButtonBySection(doc, 'uav');
            if (uavAdd) dispatchClick(uavAdd);
            await sleep(500);
            const dialogsAfterUav = dumpVisibleDialogs(doc);
            const vueAfterUav = dumpVueTree(comp);
            const driverAdd = findAddButtonBySection(doc, 'driver');
            if (driverAdd) dispatchClick(driverAdd);
            await sleep(500);
            const dialogsAfterDriver = dumpVisibleDialogs(doc);
            const vueAfterDriver = dumpVueTree(comp);
            return {
                ok:true,
                fill: {
                    planBeg: planBegNew,
                    planEnd: planEndNew,
                    uavSourceCount: Array.isArray(detail.uavs) ? detail.uavs.length : 0,
                    driverSourceCount: Array.isArray(detail.drivers) ? detail.drivers.length : 0,
                },
                addButtons,
                dialogsAfterUav,
                dialogsAfterDriver,
                vueAfterUav,
                vueAfterDriver,
                refKeys: comp.$refs ? Object.keys(comp.$refs) : [],
                dataKeys: Object.keys(comp.$data || {}).filter(k => /uav|driver|select|check|space|dialog|table|choose/i.test(k)),
            };
        }
        """,
        [detail, plan_beg_new, plan_end_new, space_payload],
    )


def js_eval(page, expression, fallback_label, arg=None):
    try:
        if arg is None:
            return page.evaluate(expression)
        return page.evaluate(expression, arg)
    except Exception as e:
        return {"ok": False, "error": str(e), "stage": fallback_label}


def get_form_debug_snapshot(page, stage):
    return js_eval(page, """
    (stage) => {
      const iframe = document.querySelector('iframe');
      if (!iframe) return {ok:false, stage, error:'no iframe'};
      const doc = iframe.contentDocument;
      if (!doc) return {ok:false, stage, error:'no iframe doc'};
      const app = doc.querySelector('#app');
      function find(vm,d){ if(!vm||d>15) return null; const n=(vm.$options&&(vm.$options.name||vm.$options._componentTag))||''; if(n==='FLY_INDEX_ADD'&&vm.$data&&vm.$data.form) return vm; for(const c of (vm.$children||[])){ const r=find(c,d+1); if(r) return r; } return null; }
      const comp = app && app.__vue__ ? find(app.__vue__,0) : null;
      const bodyText = (doc.body && doc.body.innerText || '').slice(0, 4000);
      if (!comp) {
        return {
          ok:false,
          stage,
          error:'FLY_INDEX_ADD not found',
          hasApp: !!app,
          hasVue: !!(app && app.__vue__),
          href: iframe.contentWindow ? iframe.contentWindow.location.href : null,
          text: bodyText
        };
      }
      const dataKeys = Object.keys(comp.$data || {});
      const interestingKeys = dataKeys.filter(k => /uav|driver|select|check|space|dialog|table|choose/i.test(k));
      const form = comp.$data.form || {};
      const payload = {
        ok:true,
        stage,
        compName: (comp.$options && (comp.$options.name || comp.$options._componentTag)) || '',
        href: iframe.contentWindow ? iframe.contentWindow.location.href : null,
        refKeys: comp.$refs ? Object.keys(comp.$refs) : [],
        interestingKeys,
        flags: Object.fromEntries(interestingKeys.slice(0, 80).map(k => {
          const v = comp.$data[k];
          if (Array.isArray(v)) return [k, {type:'array', length:v.length}];
          if (v && typeof v === 'object') return [k, {type:'object', keys:Object.keys(v).slice(0,10)}];
          return [k, v];
        })),
        form: {
          planBeg: form.planBeg ? String(form.planBeg) : null,
          planEnd: form.planEnd ? String(form.planEnd) : null,
          uavs: Array.isArray(form.uavs) ? form.uavs.length : null,
          drivers: Array.isArray(form.drivers) ? form.drivers.length : null,
          spaces: Array.isArray(form.spaces) ? form.spaces.length : null,
        },
        text: bodyText
      };
      if (comp.$refs && comp.$refs.form && typeof comp.$refs.form.validate === 'function') {
        return new Promise(resolve => {
          comp.$refs.form.validate((valid, fields) => {
            payload.valid = valid;
            payload.fields = Object.keys(fields || {});
            resolve(payload);
          });
        });
      }
      payload.valid = null;
      payload.fields = [];
      return payload;
    }
    """, stage, stage)


def launch_context(headless=False):
    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    acquire_profile_lock()
    p = None
    context = None
    try:
        p = sync_playwright().start()
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PERSIST_DIR),
            headless=headless,
            viewport={"width": 1280, "height": 900},
            ignore_https_errors=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.on("console", lambda msg: print(f"[browser:{msg.type}] {msg.text}"))
            page.on("pageerror", lambda exc: print(f"[pageerror] {exc}"))
            page.on("response", lambda resp: print(f"[http {resp.status}] {resp.url}") if ("flyApply" in resp.url or "/oapi/" in resp.url or "/api/" in resp.url and resp.status >= 400) else None)
        except Exception:
            pass
    except Exception:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if p is not None:
            try:
                p.stop()
            except Exception:
                pass
        release_profile_lock()
        raise
    return p, context, page


def close_context(playwright_handle, context):
    try:
        context.close()
    finally:
        try:
            playwright_handle.stop()
        finally:
            release_profile_lock()


def ensure_main_page(page, timeout=120000, settle_seconds=8):
    page.goto(f'{BASE_URL}/#/main', wait_until='domcontentloaded', timeout=timeout)
    time.sleep(settle_seconds)
    dismiss_popup(page)
    return check_main_login(page)


def require_reliable_main_login(status, context, message):
    if status.get('onLoginPage') or '#/login' in (status.get('url') or '') or not status.get('hasMainLogin'):
        print(message)
        context.close()
        raise SystemExit(2)


def ensure_main_login_with_auto_sms(page, settle_seconds=8):
    status_before = ensure_main_page(page, settle_seconds=settle_seconds)
    result = {
        'statusBefore': status_before,
        'attemptedAutoLogin': False,
        'loginResult': None,
        'statusAfter': status_before,
    }
    if status_before.get('hasMainLogin') and not status_before.get('onLoginPage'):
        return result

    result['attemptedAutoLogin'] = True
    login_result = login_via_sms(page)
    result['loginResult'] = login_result
    status_after = ensure_main_page(page, settle_seconds=3)
    result['statusAfter'] = status_after
    return result


def fetch_latest_detail(page):
    latest = get_latest_plan(page)
    if not latest.get('ok'):
        return None, latest, None
    if not latest.get('latest'):
        return latest, {'ok': False, 'error': '最近计划列表为空', 'latest': latest}, None
    detail = get_plan_detail(page, latest['latest']['planId'])
    full_profile = load_full_submit_profile()
    if full_profile:
        detail['uavs'] = full_profile['uavs']
        detail['drivers'] = full_profile['drivers']
    return latest, None, detail


def fetch_recent_plan_details(page, limit=5):
    plans = get_recent_plans(page, page_num=1, page_size=limit)
    if not plans.get('ok'):
        return None, plans
    rows = plans.get('rows') or []
    details = []
    for row in rows[:limit]:
        plan_id = row.get('planId')
        if not plan_id:
            details.append({
                'summary': row,
                'detail_error': {'ok': False, 'error': 'missing planId'},
            })
            continue
        try:
            detail = get_plan_detail(page, plan_id)
            details.append({
                'summary': row,
                'detail': detail,
            })
        except Exception as e:
            details.append({
                'summary': row,
                'detail_error': {
                    'ok': False,
                    'error': str(e),
                    'type': type(e).__name__,
                },
            })
    return {
        'ok': True,
        'count': len(details),
        'list': plans,
        'details': details,
    }, None


def save_recent_plan_details(payload, output_path=DEFAULT_RECENT_PLAN_DETAILS_FILE):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(sanitize_for_json(payload), ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return output_path
