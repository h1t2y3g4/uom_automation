#!/usr/bin/env python3
"""
ui_helpers.py - 浏览器 UI 交互工具（点击、菜单、弹窗、导航）
"""

import time

from core.constants import BASE_URL
from core.auth import check_main_login, dismiss_popup, handle_system_error


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
