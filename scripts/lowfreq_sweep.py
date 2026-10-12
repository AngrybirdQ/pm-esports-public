#!/usr/bin/env python3
"""低频任务合并执行器 — lowfreq_sweep.py (2026-10-11)
三个原独立workflow(解除concurrency互砍)按时间闸合并进collect节奏:
  resolve-conditions: 每4小时   (结算解析)
  archive-backfill:   每1小时   (token历史回填)
  liquipedia:         每12小时  (转会+赛果核对)
状态: state/lowfreq_last.json。collect.yml在longrun迭代和tick flow里各调一次。"""
import json
import os
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state", "lowfreq_last.json")
TASKS = {"resolve_conditions": ("resolve_conditions", 4 * 3600),
         "archive_backfill": ("archive_backfill", 3600),
         "liquipedia_monitor": ("liquipedia_monitor", 12 * 3600),
         "liquipedia_results": ("liquipedia_results", 12 * 3600)}


def main():
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        st = {}
    now = time.time()
    ran = []
    for name, (script, period) in TASKS.items():
        if now - st.get(name, 0) < period:
            continue
        st[name] = now
        script_p = os.path.join(ROOT, "scripts", script + ".py")
        if os.path.exists(script_p):
            try:
                subprocess.run(["python3", script_p], timeout=1500,
                               capture_output=True)
                ran.append(name)
            except Exception as e:
                print(f"{name} failed: {e}")
    json.dump(st, open(STATE, "w"))
    print(json.dumps({"ran": ran}))


if __name__ == "__main__":
    main()
