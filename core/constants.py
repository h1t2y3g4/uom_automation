#!/usr/bin/env python3
"""
constants.py - 路径常量、URL、全局配置常量
"""

from pathlib import Path

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
