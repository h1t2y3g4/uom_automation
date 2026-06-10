#!/usr/bin/env python3
"""
context.py - 浏览器上下文管理、主入口编排
"""

import json
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from core.constants import (
    BASE_URL,
    DEFAULT_RECENT_PLAN_DETAILS_FILE,
    PERSIST_DIR,
    PROFILE_LOCK_FILE,
    PROJECT_ROOT,
    _PROFILE_LOCK_HANDLE,
)
from core.config import load_config, load_full_submit_profile, sanitize_for_json
from core.time_utils import format_local_datetime, get_now_local
from core.auth import check_main_login, dismiss_popup, login_via_sms
from core.ui_helpers import open_fly_activity
from core.fly_plan import get_latest_plan, get_plan_detail, get_recent_plans


def acquire_profile_lock():
    import core.constants as _constants
    if _constants._PROFILE_LOCK_HANDLE is not None:
        return
    PROFILE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = open(PROFILE_LOCK_FILE, 'a+', encoding='utf-8')
    try:
        import fcntl
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
    _constants._PROFILE_LOCK_HANDLE = handle


def release_profile_lock():
    import core.constants as _constants
    handle = _constants._PROFILE_LOCK_HANDLE
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
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
    finally:
        try:
            handle.close()
        finally:
            _constants._PROFILE_LOCK_HANDLE = None


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


def fetch_recent_plan_details(page, limit=20):
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
