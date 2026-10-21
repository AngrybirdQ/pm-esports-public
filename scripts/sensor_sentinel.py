#!/usr/bin/env python3
"""采集静默死亡哨兵 — sensor_sentinel.py (10-21设计, 本日真部署)

[背景] 10-21根治包声称部署了本哨兵, 全库审计发现仓库里根本没有此文件
(幻影防线) — 现在补上。防线目标: collect"成功跑完但产出为零/报告停更"
的静默死亡(preflight只拦语法坏码, 拦不住逻辑性零产出)。

判据(读 state/last_run_report.json + last_run_report_flow.json):
  A. 报告停更: 两份report的最新mtime/内部ts > 40min未更新
  B. 零产出: 最新report的 trades_collected==0 且 anomalies_new==0
     且 books_rows==0 (三路全零=采集空转; 单路零=正常波动不报)
  C. status字段异常(report.status != ok类)
告警: WECOM_WEBHOOK, 同类告警6h冷却(state/sensor_sentinel_state.json),
     恢复时发一条恢复通知(状态转移)。
持久化: commit job git add -A data state ✓
"""
import glob
import json
import os
import time
import urllib.request

STATE = "state/sensor_sentinel_state.json"
STALE_MIN = 40
COOLDOWN_S = 6 * 3600


def send(md):
    hook = os.environ.get("WECOM_WEBHOOK")
    if not hook:
        return False
    body = {"msgtype": "markdown", "markdown": {"content": md[:2000]}}
    req = urllib.request.Request(hook, method="POST", headers={
        "Content-Type": "application/json"},
        data=json.dumps(body, ensure_ascii=False).encode())
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:  # noqa: BLE001
        print("alert failed:", e)
        return False


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main():
    now = time.time()
    reports = {}
    for p in glob.glob("state/last_run_report*.json"):
        try:
            r = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ts = r.get("ts")
        if isinstance(ts, str):
            from datetime import datetime
            try:
                ts = datetime.fromisoformat(
                    ts.replace("Z", "+00:00")).timestamp()
            except ValueError:
                ts = None
        if not ts:
            ts = os.path.getmtime(p)
        reports[os.path.basename(p)] = (ts, r)
    problems = []
    if not reports:
        problems.append("找不到任何last_run_report(collect从未产出?)")
    for name, (ts, r) in sorted(reports.items()):
        stale_min = (now - ts) / 60
        if stale_min > STALE_MIN:
            problems.append("%s 停更%.0fmin(>%.0f)" % (
                name, stale_min, STALE_MIN))
            continue
        tc = int(r.get("trades_collected") or 0)
        an = int(r.get("anomalies_new") or 0)
        bk = int(r.get("books_rows") or 0)
        if tc == 0 and an == 0 and bk == 0:
            problems.append("%s 三路零产出(trades/anoms/books全0)" % name)
        st = str(r.get("status") or "").lower()
        if st and st not in ("ok", "success", "done", "partial"):
            problems.append("%s status=%s" % (name, st[:30]))
    st = load_state()
    prev_bad = st.get("bad", False)
    last_ts = st.get("alert_ts", 0)
    acted = False
    if problems:
        if (not prev_bad) or (now - last_ts > COOLDOWN_S):
            ok = send("**🕵采集静默死亡哨兵**\n" + "\n".join(
                "- " + p for p in problems[:6]) +
                "\n影响: 下游全链路无新数据。查Actions日志定位。")
            if ok:
                st.update({"bad": True, "alert_ts": now,
                           "problems": problems[:6]})
                acted = True
        else:
            acted = "cooldown"
    elif prev_bad:
        send("✅ 采集恢复: 各路report新鲜且产出非零")
        st.update({"bad": False, "alert_ts": 0, "problems": []})
        acted = "recovered"
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"))
    print(json.dumps({"reports": len(reports), "problems": problems,
                      "acted": acted}, ensure_ascii=False))


if __name__ == "__main__":
    main()
