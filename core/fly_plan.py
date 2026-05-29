#!/usr/bin/env python3
"""
fly_plan.py - 飞行计划操作（获取计划、填写表单、提交）
"""

import time


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
