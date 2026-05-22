#!/usr/bin/env python3
"""
uom_airspace_probe.py - UOM 空域页探测与多边形适飞查询

职责：
- 复用持久化浏览器登录态，必要时自动短信登录
- `probe` 模式：进入运行管理下的“空域信息查询”，输出页面与地图调试信息
- `query` 模式：输入多边形经纬度，在线判断与适飞空域图层的覆盖关系，并做本地缓存

用法：
  python3 cli/uom_airspace_probe.py
  python3 cli/uom_airspace_probe.py probe --pause
  python3 cli/uom_airspace_probe.py query --polygon-wgs84 "104.0,30.5|104.1,30.5|104.1,30.6"
  python3 cli/uom_airspace_probe.py query --polygon-wgs84 "104.0,30.5|104.1,30.5|104.1,30.6" --force-refresh
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import core.uom_core as core

AIRSPACE_PROBE_LOG = core.PROJECT_ROOT / 'log' / 'airspace_probe_log.json'


def build_parser():
    parser = argparse.ArgumentParser(description='UOM 空域信息查询探测 / 多边形适飞查询脚本')
    parser.add_argument('command', nargs='?', default='probe', choices=['probe', 'query'], help='默认 probe；query 用于多边形适飞查询')
    parser.add_argument('--headless', action='store_true', help='无头模式运行浏览器')
    parser.add_argument('--pause', action='store_true', help='完成后停留浏览器，按回车再关闭')
    parser.add_argument('--menu-text', default='空域信息查询', help='默认打开的业务菜单文本')
    parser.add_argument('--output', default=None, help='输出路径；probe 默认写探测日志，query 默认只打印 JSON')
    parser.add_argument('--polygon-wgs84', help='query 模式使用的多边形经纬度，格式 lng,lat|lng,lat|...')
    parser.add_argument('--force-refresh', action='store_true', help='query 模式忽略本地缓存，强制重新在线查询')
    return parser


def write_json_output(output_path, payload):
    if not output_path:
        return None
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(core.sanitize_for_json(payload), ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return path


def probe_airspace_page(page):
    return page.evaluate(
        r"""
        () => {
            const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
            const isVisible = (el) => !!(el && el.offsetParent !== null);
            const iframe = document.querySelector('iframe');
            if (!iframe) {
                return {
                    ok: false,
                    error: 'no iframe',
                    topLevel: {
                        href: location.href || '',
                        title: document.title || '',
                        bodyHead: norm(document.body ? document.body.innerText : '').slice(0, 1000),
                    }
                };
            }

            const win = iframe.contentWindow;
            const doc = iframe.contentDocument;
            if (!win || !doc) {
                return {
                    ok: false,
                    error: 'no iframe content',
                    iframeSrc: iframe.src || '',
                };
            }

            const collectResourceNames = (perfWin) => {
                try {
                    return perfWin.performance.getEntriesByType('resource')
                        .map(entry => entry && entry.name)
                        .filter(Boolean);
                } catch (e) {
                    return [];
                }
            };

            const collectStorage = (storage) => {
                try {
                    const keys = [];
                    for (let i = 0; i < storage.length; i += 1) {
                        keys.push(storage.key(i));
                    }
                    return keys.filter(Boolean).slice(0, 80);
                } catch (e) {
                    return [];
                }
            };

            const resourceNames = collectResourceNames(win);
            const mapKeywords = /air|space|map|gis|geo|tile|wmts|wms|vector|layer|query|arcgis|amap|bmap|tianditu|leaflet|mapbox|openlayers|cesium/i;
            const visibleTexts = Array.from(doc.querySelectorAll('button, a, span, div, label, li, p, input, textarea'))
                .filter(isVisible)
                .map(el => {
                    const text = norm(
                        el.tagName === 'INPUT' || el.tagName === 'TEXTAREA'
                            ? (el.value || el.placeholder || '')
                            : (el.textContent || '')
                    );
                    return {
                        tag: el.tagName,
                        text,
                        className: (el.className || '').toString().slice(0, 200),
                    };
                })
                .filter(item => item.text);

            const queryTextHits = visibleTexts
                .filter(item => /空域|适飞|禁飞|查询|机场|起降点|坐标|图层|缓冲|半径|多边形/.test(item.text))
                .slice(0, 80);

            const visibleInputs = Array.from(doc.querySelectorAll('input, textarea'))
                .filter(isVisible)
                .map(el => ({
                    tag: el.tagName,
                    type: el.getAttribute('type') || '',
                    placeholder: el.getAttribute('placeholder') || '',
                    value: (el.value || '').slice(0, 200),
                    className: (el.className || '').toString().slice(0, 200),
                }));

            const visibleSelectLike = Array.from(doc.querySelectorAll('select, .el-select, .ivu-select, .ant-select'))
                .filter(isVisible)
                .map(el => ({
                    tag: el.tagName,
                    text: norm(el.textContent || '').slice(0, 300),
                    className: (el.className || '').toString().slice(0, 200),
                }))
                .slice(0, 40);

            const globals = ['AMap', 'BMap', 'TMap', 'L', 'mapboxgl', 'ol', 'Cesium']
                .filter(name => {
                    try {
                        return !!win[name];
                    } catch (e) {
                        return false;
                    }
                });

            const scripts = Array.from(doc.querySelectorAll('script'))
                .map(el => el.src || '')
                .filter(Boolean)
                .filter(src => mapKeywords.test(src))
                .slice(0, 50);

            const componentSummary = (() => {
                const app = doc.querySelector('#app');
                if (!app || !app.__vue__) return {hasVue: false};
                const names = [];
                const interesting = [];
                const seen = new Set();
                function walk(vm, depth) {
                    if (!vm || depth > 10 || seen.has(vm)) return;
                    seen.add(vm);
                    const name = (vm.$options && (vm.$options.name || vm.$options._componentTag)) || '';
                    if (name) names.push(name);
                    const data = vm.$data || {};
                    const keys = Object.keys(data).filter(key => /map|space|air|query|layer|polygon|coord|lon|lat/i.test(key));
                    if (keys.length) {
                        interesting.push({
                            name: name || '(anonymous)',
                            keys: keys.slice(0, 30),
                        });
                    }
                    for (const child of (vm.$children || [])) walk(child, depth + 1);
                }
                walk(app.__vue__, 0);
                return {
                    hasVue: true,
                    names: Array.from(new Set(names)).slice(0, 80),
                    interesting: interesting.slice(0, 30),
                };
            })();

            return {
                ok: true,
                topLevel: {
                    href: location.href || '',
                    title: document.title || '',
                },
                iframe: {
                    src: iframe.src || '',
                    href: win.location ? (win.location.href || '') : '',
                    title: doc.title || '',
                    readyState: doc.readyState || '',
                },
                dom: {
                    bodyTextHead: norm(doc.body ? doc.body.innerText : '').slice(0, 2000),
                    queryTextHits,
                    visibleInputs,
                    visibleSelectLike,
                    canvasCount: doc.querySelectorAll('canvas').length,
                    svgCount: doc.querySelectorAll('svg').length,
                    imgCount: doc.querySelectorAll('img').length,
                },
                resources: {
                    totalCount: resourceNames.length,
                    matched: resourceNames.filter(name => mapKeywords.test(name)).slice(0, 120),
                    scriptMatches: scripts,
                },
                storage: {
                    localStorageKeys: collectStorage(win.localStorage),
                    sessionStorageKeys: collectStorage(win.sessionStorage),
                },
                globals,
                componentSummary,
            };
        }
        """
    )


def run_probe_mode(args):
    playwright_handle, context, page = core.launch_context(headless=args.headless)
    output_path = args.output or str(AIRSPACE_PROBE_LOG)
    run_log = {
        'runStartedAt': core.format_local_datetime(core.get_now_local()),
        'runFinishedAt': None,
        'status': 'running',
        'menuText': args.menu_text,
        'loginFlow': None,
        'menuOpen': None,
        'menuList': None,
        'probe': None,
        'error': None,
    }
    try:
        login_flow = core.ensure_main_login_with_auto_sms(page)
        run_log['loginFlow'] = core.sanitize_for_json(login_flow)
        status = login_flow.get('statusAfter') or {}
        core.require_reliable_main_login(status, context, '当前不在可靠的主站已登录状态，停止空域页面探测，避免在错误页面上继续执行。')

        menu_list = core.list_layout_menu_items(page)
        run_log['menuList'] = {
            'ok': menu_list.get('ok'),
            'count': menu_list.get('count'),
            'queryMatches': [
                item for item in (menu_list.get('items') or [])
                if args.menu_text in ' | '.join([
                    item.get('title') or '',
                    item.get('label') or '',
                    item.get('appName') or '',
                    item.get('value') or '',
                    item.get('routeLabel') or '',
                    item.get('routeValue') or '',
                ])
            ][:20],
        }
        print('菜单匹配结果:')
        print(json.dumps(run_log['menuList'], ensure_ascii=False, indent=2))

        print('进入业务菜单：运行管理 -> 空域信息查询')
        menu_open = core.open_airspace_query_via_real_nav(page)
        run_log['menuOpen'] = core.sanitize_for_json(menu_open)
        core.time.sleep(8)
        highlight = core.highlight_business_iframe(page, label=args.menu_text)
        run_log['iframeHighlight'] = core.sanitize_for_json(highlight)
        print('iframe 高亮结果:')
        print(json.dumps(highlight, ensure_ascii=False, indent=2))

        probe = probe_airspace_page(page)
        run_log['probe'] = core.sanitize_for_json(probe)
        run_log['status'] = 'completed' if probe.get('ok') else 'probe_failed'
        print('空域页面探测结果摘要:')
        print(json.dumps({
            'ok': probe.get('ok'),
            'iframe': (probe.get('iframe') or {}),
            'globals': probe.get('globals'),
            'resourceMatchedCount': len(((probe.get('resources') or {}).get('matched') or [])),
            'visibleInputCount': len(((probe.get('dom') or {}).get('visibleInputs') or [])),
            'queryTextHits': ((probe.get('dom') or {}).get('queryTextHits') or [])[:20],
        }, ensure_ascii=False, indent=2))
    except Exception as e:
        run_log['status'] = 'exception'
        run_log['error'] = {
            'type': type(e).__name__,
            'message': str(e),
        }
        raise
    finally:
        run_log['runFinishedAt'] = core.format_local_datetime(core.get_now_local())
        write_json_output(output_path, run_log)
        print(f'探测日志已写入: {output_path}')
        if args.pause:
            try:
                input('浏览器保持打开中，按回车关闭...')
            except EOFError:
                pass
        core.close_context(playwright_handle, context)


def run_query_mode(args):
    if not args.polygon_wgs84:
        raise SystemExit('query 模式必须传 --polygon-wgs84')

    normalized_polygon_wgs84 = core.normalize_polygon_wgs84(args.polygon_wgs84)
    if not args.force_refresh:
        cached = core.get_cached_airspace_query_result(normalized_polygon_wgs84)
        if cached:
            print(json.dumps(core.sanitize_for_json(cached), ensure_ascii=False, indent=2))
            if args.output:
                write_json_output(args.output, cached)
            return

    playwright_handle = None
    context = None
    query_result = None
    try:
        playwright_handle, context, page = core.launch_context(headless=args.headless)
        query_result = core.query_airspace_polygon_online(page, context, normalized_polygon_wgs84)
        if query_result.get('status') == 'online_ok':
            core.store_airspace_query_result(normalized_polygon_wgs84, query_result)
    except SystemExit:
        raise
    except Exception as e:
        query_result = {
            'status': 'error',
            'judgement': 'unknown',
            'polygonWgs84': normalized_polygon_wgs84,
            'cacheHit': False,
            'queriedAt': core.format_local_datetime(core.get_now_local()),
            'evidence': {
                'errorType': type(e).__name__,
                'errorMessage': str(e),
            },
        }
    finally:
        if query_result is not None:
            print(json.dumps(core.sanitize_for_json(query_result), ensure_ascii=False, indent=2))
            if args.output:
                write_json_output(args.output, query_result)
        if args.pause and context is not None:
            try:
                input('浏览器保持打开中，按回车关闭...')
            except EOFError:
                pass
        if playwright_handle is not None and context is not None:
            core.close_context(playwright_handle, context)


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == 'query':
        run_query_mode(args)
        return
    run_probe_mode(args)


if __name__ == '__main__':
    main()
