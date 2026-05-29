#!/usr/bin/env python3
"""
config.py - 配置加载、JSON 工具
"""

import json
from datetime import datetime
from pathlib import Path

from core.constants import (
    AIRSPACE_FILE,
    CONFIG_FILE,
    SMS_CODE_FILE,
    SUBMIT_PLAN_FILE,
)


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
