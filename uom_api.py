#!/usr/bin/env python3
"""
uom_api.py - UOM 平台 API 封装
所有与 UOM 服务器交互的逻辑都在这里。
"""

import json
import os
import requests

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

BASE_URL = "https://uom.caac.gov.cn"


def load_config():
    """加载配置文件"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _headers(cfg):
    """构建请求头"""
    auth = cfg["auth"]
    return {
        "authorization": f"Bearer {auth['pub_token']}",
        "pubusername": auth["username"],
        "ticket": auth["ticket"],
        "devicetype": "PC",
        "host": "uom.caac.gov.cn",
        "origin": BASE_URL,
        "referer": f"{BASE_URL}/web_pub/flyActivity/flyIndexAdd",
        "accept": "application/json, text/plain, */*",
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    }


def _cookies(cfg):
    """构建 Cookie"""
    auth = cfg["auth"]
    return {
        "userName": auth["username"],
        "PUB-Token": auth["pub_token"],
    }


def check_token(cfg=None):
    """检查 token 是否有效（通过查询计划列表）"""
    if cfg is None:
        cfg = load_config()
    try:
        resp = requests.get(
            f"{BASE_URL}/oapi/pub/planInfo/list",
            params={"pageNum": "1", "pageSize": "1", "planTypes": "11,12,13"},
            headers=_headers(cfg),
            cookies=_cookies(cfg),
            timeout=15,
        )
        data = resp.json()
        # 返回 code=0 说明 token 有效
        return data.get("code") == "0" or data.get("code") == 0
    except Exception:
        return False


def list_plans(cfg=None):
    """查询飞行计划列表"""
    if cfg is None:
        cfg = load_config()
    resp = requests.get(
        f"{BASE_URL}/oapi/pub/planInfo/list",
        params={"pageNum": "1", "pageSize": "20", "planTypes": "11,12,13"},
        headers=_headers(cfg),
        cookies=_cookies(cfg),
        timeout=15,
    )
    return resp.json()


def submit_plan(airspace_points, plan_beg, plan_end, cfg=None, dry_run=False):
    """
    提交飞行计划。

    参数:
      airspace_points: 空域坐标字符串，格式 "lng1,lat1|lng2,lat2|..."
      plan_beg: 起飞时间 "YYYY-MM-DD HH:MM:SS"
      plan_end: 结束时间 "YYYY-MM-DD HH:MM:SS"
      cfg: 配置字典，None 则自动加载
      dry_run: True 则不实际提交，只返回请求数据
    """
    if cfg is None:
        cfg = load_config()

    contact = cfg["contact"]
    drone = cfg["drone"]
    driver = cfg["driver"]
    defaults = cfg["plan_defaults"]

    payload = {
        "planType": defaults["planType"],
        "spaceType": defaults["spaceType"],
        "optType": "",
        "flyType": "",
        "txll": f"{contact['name']}\t{contact['phone']}",
        "taskType": defaults["taskType"],
        "takeoffs": [],
        "spaces": [
            {
                "locationWgs84": airspace_points,
                "spcBottom": 0,
                "spcTop": defaults["spcTop"],
                "spcShape": 1,
                "spcName": "",
                "lineWidth": None,
                "radius": None,
                "groupName": "空域1",
                "spaceShape": "面",
                "index": 0,
                "polyLinePoints": [],
                "polygonPoints": [],
            }
        ],
        "fileList": [],
        "files": [],
        "spcTop": defaults["spcTop"],
        "checkFlag": None,
        "planBeg": plan_beg,
        "planEnd": plan_end,
        "uavs": [
            {
                "remark": None,
                "pubSeq": drone["pubSeq"],
                "userId": None,
                "sn": drone["sn"],
                "uasCode": drone["uasCode"],
                "ownType": drone["ownType"],
                "ownName": drone["ownName"],
                "psnCardtype": drone["psnCardtype"],
                "psnCardno": drone["psnCardno"],
                "deptCode": None,
                "mnfName": drone["mnfName"],
                "mnfCode": drone["mnfCode"],
                "proMode": drone["proMode"],
                "proName": drone["proName"],
                "proClass": drone["proClass"],
                "proType": drone["proType"],
                "weightEmpty": drone["weightEmpty"],
                "weightMax": drone["weightMax"],
                "phone": drone["phone_encrypted"],
                "delSts": drone["delSts"],
                "plantProtector": drone["plantProtector"],
                "uavPurpose": None,
                "uavState": None,
                "validSts": drone["validSts"],
                "checkTime": None,
                "comm": None,
            }
        ],
        "remark": defaults["remark"],
        "drivers": [
            {
                "remark": None,
                "pdiSeq": driver["pdiSeq"],
                "userId": driver["userId"],
                "name": driver["name"],
                "cardtype": driver["cardtype"],
                "cardno": driver["cardno"],
                "licno": None,
                "licType": None,
                "lvlType": None,
                "lvlLevel": None,
                "lvlBvlos": None,
                "lvlTeacher": None,
                "dateIssue": driver["dateIssue"],
                "dateLose": driver["dateLose"],
                "phone": driver["phone"],
                "pp": driver["pp"],
            }
        ],
        "six": defaults["six"],
        "applySts": "3",
    }

    if dry_run:
        return {"dry_run": True, "payload": payload, "url": f"{BASE_URL}/oapi/pub/planInfo"}

    resp = requests.post(
        f"{BASE_URL}/oapi/pub/planInfo",
        headers=_headers(cfg),
        cookies=_cookies(cfg),
        data=payload,
        timeout=30,
    )
    return resp.json()
