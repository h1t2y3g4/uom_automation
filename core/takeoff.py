#!/usr/bin/env python3
"""
takeoff.py - 起飞确认相关操作
"""

import time

from core.fly_plan import js_eval


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
