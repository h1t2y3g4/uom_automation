#!/usr/bin/env python3
"""
browser_submit.py - UOM 浏览器飞行计划提交

使用 browser_login.py 保存的浏览器状态，在浏览器中完成飞行计划提交。
全程浏览器操作，不需要 API 逆向。

用法:
  python3 browser_submit.py --beg "2026-05-16 09:00" --end "2026-05-16 10:00"
  python3 browser_submit.py --beg "2026-05-16 09:00" --end "2026-05-16 10:00" --dry-run
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).parent
STATE_FILE = SCRIPT_DIR / "browser_state.json"
CONFIG_FILE = SCRIPT_DIR / "config.json"
BASE_URL = "https://uom.caac.gov.cn"


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def fmt_time(s):
    """补全时间格式"""
    s = s.strip()
    if len(s) == 10:
        return s + " 00:00:00"
    if len(s) == 16:
        return s + ":00"
    return s


# =====================================================================
#  浏览器内操作的 JS 代码
# =====================================================================

# 导航到飞行申请页面并等待加载
JS_NAVIGATE_FLY = """
() => {
    // 直接跳转到飞行申请页面
    window.location.hash = '/web_pub/flyActivity/flyIndexAdd';
    return {ok: true};
}
"""

# 截取页面上的表单字段信息（了解页面结构）
JS_INSPECT_FORM = """
() => {
    // 获取页面上所有 input/select/textarea
    const inputs = document.querySelectorAll('input, select, textarea');
    const fields = [];
    for (const el of inputs) {
        fields.push({
            tag: el.tagName,
            type: el.type || '',
            name: el.name || '',
            placeholder: el.placeholder || '',
            value: el.value || '',
            id: el.id || '',
            className: el.className.substring(0, 80),
        });
    }
    return fields;
}
"""

# 查找飞行申请的 Vue 组件
JS_FIND_FLY_COMPONENT = """
() => {
    const root = document.querySelector('#app');
    if (!root || !root.__vue__) return {error: 'Vue root not found'};

    function findComponents(vm, depth, results) {
        if (depth > 15) return;
        const name = vm.$options.name || vm.$options._componentTag || '';
        const data = vm.$data ? Object.keys(vm.$data) : [];
        if (data.length > 0) {
            results.push({
                name: name,
                depth: depth,
                dataKeys: data.slice(0, 20),
                hasPlanData: data.some(k => k.toLowerCase().includes('plan') || k.toLowerCase().includes('fly')),
            });
        }
        for (const c of (vm.$children || [])) {
            findComponents(c, depth + 1, results);
        }
    }

    const results = [];
    findComponents(root.__vue__, 0, results);
    // 只返回可能有用的组件
    return results.filter(r => r.dataKeys.length > 2).slice(-10);
}
"""

# 直接提交飞行计划 API（通过浏览器内的 fetch，自动携带 cookie 和 token）
JS_SUBMIT_PLAN = """
(payload) => {
    return fetch('/oapi/pub/planInfo', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        body: JSON.stringify(payload),
    })
    .then(r => r.json())
    .then(data => ({ok: true, response: data}))
    .catch(err => ({ok: false, error: err.message}));
}
"""

# 获取用户信息（无人机列表、驾驶员列表等）
JS_GET_USER_INFO = """
() => {
    const results = {};

    // 尝试从 Vue store 获取
    const root = document.querySelector('#app');
    if (root && root.__vue__) {
        const store = root.__vue__.$store;
        if (store) {
            results.storeState = Object.keys(store.state).slice(0, 20);
            // 尝试获取无人机和驾驶员
            if (store.state.uavList) results.uavList = store.state.uavList;
            if (store.state.driverList) results.driverList = store.state.driverList;
            if (store.state.planInfo) results.planInfo = store.state.planInfo;
        }
    }

    return results;
}
"""


def do_browser_submit(beg, end, dry_run=False, headless=True):
    """在浏览器中提交飞行计划"""
    from playwright.sync_api import sync_playwright

    cfg = load_config()
    contact = cfg["contact"]
    drone = cfg["drone"]
    driver = cfg["driver"]
    defaults = cfg["plan_defaults"]
    airspace = cfg["airspaces"]["default"]

    beg = fmt_time(beg)
    end = fmt_time(end)

    print("═══════════════════════════════════════════")
    print("  UOM 浏览器飞行计划提交")
    print("═══════════════════════════════════════════")
    print(f"  无人机:   {drone['proName']}")
    print(f"  飞行时段: {beg} ~ {end}")
    print(f"  高度范围: 0m ~ {airspace.get('spcTop', defaults['spcTop'])}m")
    if dry_run:
        print("  ⚠️  试运行模式")
    print("═══════════════════════════════════════════")

    if not STATE_FILE.exists():
        print("❌ 没有已保存的浏览器状态，请先运行 browser_login.py 登录")
        return False

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )

        with open(STATE_FILE) as f:
            state = json.load(f)

        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            storage_state=state,
        )
        page = context.new_page()

        # ---- Step 1: 打开飞行申请页面 ----
        print("\n  [1/3] 打开飞行申请页面...")
        page.goto(BASE_URL, wait_until="networkidle", timeout=60000)
        time.sleep(3)

        # 检查是否需要重新登录
        current_url = page.url
        if "login" in current_url.lower():
            print("  ❌ Session 已过期，请重新运行 browser_login.py")
            browser.close()
            return False

        # 导航到飞行申请页面
        page.evaluate(JS_NAVIGATE_FLY)
        time.sleep(3)
        page.screenshot(path="/tmp/uom_fly_page.png")
        print(f"  飞行申请页面截图: /tmp/uom_fly_page.png")

        # 检查页面结构
        form_fields = page.evaluate(JS_INSPECT_FORM)
        print(f"  找到 {len(form_fields)} 个表单字段")

        # ---- Step 2: 构造提交数据 ----
        print("\n  [2/3] 构造飞行计划数据...")

        # 通过浏览器内的 API 获取最新的无人机和驾驶员信息
        # （避免 config.json 中的数据过时）
        user_info = page.evaluate(JS_GET_USER_INFO)
        print(f"  用户信息: {json.dumps(user_info, ensure_ascii=False)[:200]}...")

        # 构造提交 payload
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
                    "locationWgs84": airspace["points"],
                    "spcBottom": 0,
                    "spcTop": airspace.get("spcTop", defaults["spcTop"]),
                    "spcShape": 1,
                    "spcName": "",
                    "lineWidth": None,
                    "radius": None,
                    "groupName": airspace.get("groupName", "空域1"),
                    "spaceShape": "面",
                    "index": 0,
                    "polyLinePoints": [],
                    "polygonPoints": [],
                }
            ],
            "fileList": [],
            "files": [],
            "spcTop": airspace.get("spcTop", defaults["spcTop"]),
            "checkFlag": None,
            "planBeg": beg,
            "planEnd": end,
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

        print(f"  Payload 构造完成")

        # ---- Step 3: 提交 ----
        if dry_run:
            print("\n  [3/3] 试运行 - 不实际提交")
            print(f"  请求数据预览:")
            preview = json.dumps(payload, ensure_ascii=False)
            print(f"  {preview[:500]}...")
            browser.close()
            return True

        print("\n  [3/3] 提交飞行计划...")
        result = page.evaluate(JS_SUBMIT_PLAN, payload)
        print(f"  服务器返回: {json.dumps(result, ensure_ascii=False)[:500]}")

        if result.get("ok") and result.get("response", {}).get("code") in ["0", 0]:
            print("\n  ✅ 飞行计划提交成功！")
        else:
            print(f"\n  ⚠️  提交结果请检查上方返回信息")

        page.screenshot(path="/tmp/uom_submit_result.png")
        print(f"  结果截图: /tmp/uom_submit_result.png")

        # 保存更新后的状态
        new_state = context.storage_state()
        with open(STATE_FILE, "w") as f:
            json.dump(new_state, f, ensure_ascii=False, indent=2)

        browser.close()
        return True


# =====================================================================
#  主入口
# =====================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UOM 浏览器飞行计划提交")
    parser.add_argument("--beg", required=True, help="起飞时间 (YYYY-MM-DD HH:MM)")
    parser.add_argument("--end", required=True, help="降落时间 (YYYY-MM-DD HH:MM)")
    parser.add_argument("--dry-run", action="store_true", help="试运行，不实际提交")
    parser.add_argument("--headed", action="store_true", help="有头模式（可看到浏览器）")
    args = parser.parse_args()

    ok = do_browser_submit(
        beg=args.beg,
        end=args.end,
        dry_run=args.dry_run,
        headless=not args.headed,
    )
    sys.exit(0 if ok else 1)
