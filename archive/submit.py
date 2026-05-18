#!/usr/bin/env python3
"""
submit.py - 旧版直接提交脚本（已归档）

当前状态：
- 这是项目早期尝试通过配置和 API 直接提交飞行申请的脚本
- 当前已确认直接 POST /oapi/pub/planInfo 仍不可靠
- 因此该脚本不再是主线方案

保留原因：
- 可参考旧的命令行参数设计和字段拼装方式
- 可作为历史尝试记录
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from uom_api import load_config, check_token, list_plans, submit_plan
from login import interactive_login


def fmt_time(s):
    """把用户输入的时间补全为完整格式"""
    s = s.strip()
    if len(s) == 10:          # YYYY-MM-DD
        return s + " 00:00:00"
    if len(s) == 16:          # YYYY-MM-DD HH:MM
        return s + ":00"
    return s                   # YYYY-MM-DD HH:MM:SS


def main():
    parser = argparse.ArgumentParser(description="UOM 飞行申请提交")
    parser.add_argument("--check", action="store_true", help="查询当前飞行计划列表")
    parser.add_argument("--check-token", action="store_true", help="检查 token 是否有效")
    parser.add_argument("--dry-run", action="store_true", help="试运行，不实际提交")
    parser.add_argument("--space", default="default", help="空域名称（对应 config.json 中 airspaces 的 key）")
    parser.add_argument("--beg", help="起飞时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM)")
    parser.add_argument("--end", help="降落时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM)")
    args = parser.parse_args()

    cfg = load_config()

    # --- 检查 token ---
    if args.check_token:
        valid = check_token(cfg)
        if valid:
            print("✅ Token 有效")
        else:
            print("❌ Token 无效或已过期，请重新登录 UOM 获取新 token")
            print(f"   当前 username: {cfg['auth']['username']}")
            print(f"   当前 token:    {cfg['auth']['pub_token'][:20]}...")
        return

    # --- 查询计划 ---
    if args.check:
        if not check_token(cfg):
            print("  Token 已过期，尝试自动登录...")
            if not interactive_login():
                print("❌ 登录失败")
                return
            cfg = load_config()  # 重新加载配置
        result = list_plans(cfg)
        print(json.dumps(result, ensure_ascii=False, indent=2)[:3000])
        return

    # --- 提交申请 ---
    if not args.beg or not args.end:
        parser.error("提交申请需要 --beg 和 --end 参数")

    # dry-run 不需要检查 token
    if not args.dry_run and not check_token(cfg):
        print("  Token 已过期，尝试自动登录...")
        if not interactive_login():
            print("❌ 登录失败")
            return
        cfg = load_config()  # 重新加载配置

    # 获取空域坐标
    airspaces = cfg.get("airspaces", {})
    if args.space not in airspaces:
        print(f"❌ 未找到空域 '{args.space}'")
        print(f"   可用空域: {', '.join(k for k in airspaces if not k.startswith('_'))}")
        return

    airspace = airspaces[args.space]
    points = airspace["points"]
    beg = fmt_time(args.beg)
    end = fmt_time(args.end)

    # 显示摘要
    print(f"═══════════════════════════════════════════")
    print(f"  UOM 飞行申请")
    print(f"═══════════════════════════════════════════")
    print(f"  用户:     {cfg['auth']['username']}")
    print(f"  无人机:   {cfg['drone']['proName']}")
    print(f"  飞行时段: {beg} ~ {end}")
    print(f"  高度范围: 0m ~ {airspace.get('spcTop', cfg['plan_defaults']['spcTop'])}m")
    print(f"  空域名称: {airspace.get('groupName', args.space)}")
    print(f"═══════════════════════════════════════════")

    if args.dry_run:
        print("\n  [试运行] 不会实际提交")
        result = submit_plan(points, beg, end, cfg, dry_run=True)
        print(f"\n  请求 URL: {result['url']}")
        print(f"  请求数据: {json.dumps(result['payload'], ensure_ascii=False)[:200]}...")
        print(f"\n  ✅ 试运行完成")
        return

    confirm = input("\n  确认提交？(y/N) ")
    if confirm.lower() != "y":
        print("  已取消")
        return

    print("\n  正在提交...")
    result = submit_plan(points, beg, end, cfg)
    print(f"\n  服务器返回:")
    print(f"  {json.dumps(result, ensure_ascii=False, indent=2)[:2000]}")

    code = result.get("code")
    if str(code) == "0":
        print(f"\n  ✅ 提交成功！")
    else:
        print(f"\n  ⚠️  状态码: {code}，请检查返回信息")


if __name__ == "__main__":
    main()
