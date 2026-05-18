#!/usr/bin/env python3
"""
uom_semiauto_submit.py - UOM 半自动新增页调试脚本

作用：
- 复用 uom_persistent.py 的持久化浏览器能力
- 自动进入一般飞行活动并打开新增页
- 自动填入最近计划内容和目标时间
- 记录 precheck/postcheck 调试信息
- 重点定位为什么前端 validate() 仍然报 uavs/drivers

说明：
- 这是当前主线调试脚本之一
- 它主要用于排查“页面看起来有航空器/操控员，但前端仍不认已选中”的问题
- 不应把它当成已经稳定可一键提交成功的正式脚本
"""
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright
import uom_persistent as up

SCRIPT_DIR = Path(__file__).parent
LOG_FILE = SCRIPT_DIR / 'manual_selection_log.json'


def js_eval(page, expression, fallback_label, arg=None):
    try:
        if arg is None:
            return page.evaluate(expression)
        return page.evaluate(expression, arg)
    except Exception as e:
        return {"ok": False, "error": str(e), "stage": fallback_label}


def get_form_debug_snapshot(page, stage):
    return js_eval(page, """
    ([stage]) => {
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
    """, stage, [stage])


print('UOM 半自动提交脚本')
print('流程：自动打开新增页并按更接近人工的顺序逐项填入 -> 你确认 -> 回车后脚本自动提交并检查结果')

cfg = up.load_config()
drone_name = cfg.get("drone", {}).get("proName", "config.json 中的 drone.proName")
drone_uas_code = cfg.get("drone", {}).get("uasCode", "config.json 中的 drone.uasCode")
driver_name = cfg.get("driver", {}).get("name", "config.json 中的 driver.name")

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(up.PERSIST_DIR),
        headless=False,
        viewport={'width': 1280, 'height': 900},
        args=['--no-sandbox', '--disable-dev-shm-usage'],
    )
    page = context.pages[0] if context.pages else context.new_page()

    page.goto(f'{up.BASE_URL}/#/main', wait_until='domcontentloaded', timeout=120000)
    time.sleep(8)
    up.dismiss_popup(page)
    print('主站状态:')
    print(json.dumps(up.check_main_login(page), ensure_ascii=False, indent=2))

    print('进入 一般飞行活动 ...')
    up.open_fly_activity(page)
    time.sleep(12)

    latest = up.get_latest_plan(page)
    if not latest.get('ok'):
        print('读取最近计划失败:')
        print(json.dumps(latest, ensure_ascii=False, indent=2))
        context.close()
        raise SystemExit(1)

    detail = up.get_plan_detail(page, latest['latest']['planId'])
    full_profile = up.load_full_submit_profile()
    if full_profile:
        detail['uavs'] = full_profile['uavs']
        detail['drivers'] = full_profile['drivers']

    next_beg, next_end = up.get_next_tuesday_same_time(detail['planBeg'], detail['planEnd'])
    print(f'目标时间: {next_beg} ~ {next_end}')

    add_res = up.open_new_fly_form(page)
    print('打开新增页:')
    print(json.dumps(add_res, ensure_ascii=False, indent=2))

    fly_add = up.wait_for_fly_add(page)
    print('等待新增页:')
    print(json.dumps(fly_add, ensure_ascii=False, indent=2)[:4000])
    if not fly_add:
        context.close()
        raise SystemExit(1)

    fill = up.fill_new_form_from_detail(page, detail, next_beg, next_end)
    print('填充结果:')
    print(json.dumps(fill, ensure_ascii=False, indent=2)[:4000])

    precheck = get_form_debug_snapshot(page, 'precheck_auto_fill')
    print('自动填充后的前端校验/快照:')
    print(json.dumps(precheck, ensure_ascii=False, indent=2)[:5000])

    page.evaluate("""
    () => {
      const iframe = document.querySelector('iframe');
      if (!iframe) return false;
      const doc = iframe.contentDocument;
      if (!doc) return false;
      const btn = Array.from(doc.querySelectorAll('button')).find(b => ((b.textContent||'').replace(/\s+/g,' ').trim()) === '我知道了');
      if (btn) btn.click();
      return true;
    }
    """)
    time.sleep(1)

    print('\n请你肉眼确认下面几点：')
    print('1. 时间是本周二同时间')
    print('2. 页面是否真的打开过航空器/操控员的添加动作（看终端 clickedUavAdd / clickedDriverAdd）')
    print(f'3. 航空器信息里是否有 {drone_name} / {drone_uas_code}')
    print(f'4. 操控员信息里是否有 {driver_name}')
    print('5. 不要自己点“提交申请”，确认无误后回终端按回车让我继续')
    input('确认页面无误后，按回车继续自动提交...')

    postcheck = get_form_debug_snapshot(page, 'postcheck_manual_before_submit')
    print('手动确认后的前端校验/快照:')
    print(json.dumps(postcheck, ensure_ascii=False, indent=2)[:5000])

    LOG_FILE.write_text(json.dumps({
        'target_time': {'planBeg': next_beg, 'planEnd': next_end},
        'fill': fill,
        'precheck': precheck,
        'postcheck': postcheck,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'日志已保存: {LOG_FILE}')

    submit_res = up.trigger_submit_copied_form(page)
    print('触发提交:')
    print(json.dumps(submit_res, ensure_ascii=False, indent=2))
    time.sleep(8)

    latest2 = up.get_latest_plan(page)
    print('提交后最近计划:')
    print(json.dumps(latest2, ensure_ascii=False, indent=2)[:4000])

    input('检查结果后，按回车关闭浏览器...')
    context.close()
