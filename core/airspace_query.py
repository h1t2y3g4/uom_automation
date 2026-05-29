#!/usr/bin/env python3
"""
airspace_query.py - 空域在线查询、缓存、瓦片分析
"""

import io
import json
import time

from core.constants import (
    AIRSPACE_QUERY_CACHE_FILE,
    AIRSPACE_QUERY_ALGO_VERSION,
    AIRSPACE_QUERY_MIN_ZOOM,
    AIRSPACE_QUERY_WMS_KEYWORD,
)
from core.config import sanitize_for_json, load_json_file
from core.time_utils import format_local_datetime, get_now_local
from core.airspace_data import (
    normalize_polygon_wgs84,
    parse_polygon_wgs84,
    build_airspace_query_cache_key,
)
from core.auth import require_pillow
from core.context import ensure_main_login_with_auto_sms
from core.ui_helpers import open_airspace_query_via_real_nav


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
    from PIL import Image, ImageChops, ImageDraw

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
