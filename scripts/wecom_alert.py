#!/usr/bin/env python3
"""Push fresh pm anomalies straight to WeCom group webhook (cloud-side,
zero local relay — cuts big-order alert latency to detection+seconds).

Runs inside Actions after collect (longrun 90s loop + tick flow stage).
Dedup state: data/wecom_pushed.json (rides data artifact + git commit).
Env: WECOM_WEBHOOK (repo secret). Stdlib only."""
import glob
import json
import os
import time
import urllib.request

PUSH_TYPES = {"big_trade", "sharp_wallet", "big_premarket",
              "one_side_flow", "concentrated_entry", "sharp_move"}
TTL_H = 6
FRESH_FILE_MIN = 15
CAP = 8


def send(md):
    body = {"msgtype": "markdown", "markdown": {"content": md[:3800]}}
    req = urllib.request.Request(
        os.environ["WECOM_WEBHOOK"], data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode()).get("errcode")


def main():
    webhook = os.environ.get("WECOM_WEBHOOK", "")
    if not webhook:
        print("wecom_alert: no WECOM_WEBHOOK, skip")
        return
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    state_p = os.path.join(root, "data", "wecom_pushed.json")
    try:
        pushed = json.load(open(state_p, encoding="utf-8"))
    except Exception:
        pushed = {}
    now = time.time()
    pushed = {k: t for k, t in pushed.items() if now - t < TTL_H * 3600}

    fresh = []
    for f in glob.glob(os.path.join(root, "data", "**", "*.anoms.jsonl"),
                       recursive=True):
        if now - os.path.getmtime(f) > FRESH_FILE_MIN * 60:
            continue
        for line in open(f, encoding="utf-8"):
            try:
                a = json.loads(line)
            except ValueError:
                continue
            if a.get("type") not in PUSH_TYPES:
                continue
            key = "%s|%s|%s" % (a.get("type"), a.get("condition_id"),
                                a.get("outcome"))
            if key in pushed:
                continue
            pushed[key] = now
            fresh.append(a)
    if not fresh:
        return

    LABEL = {"big_trade": "🚨大单", "sharp_wallet": "🧠聪明钱",
             "big_premarket": "💰赛前大单", "one_side_flow": "⚔单边净流",
             "concentrated_entry": "🎯集中建仓", "sharp_move": "⚡胜率急动"}
    segs = ["**🚨 pm场外异动 ×%d**" % len(fresh)]
    for a in fresh[:CAP]:
        segs.append("**%s** %s\n%s" % (
            LABEL.get(a.get("type"), a.get("type")),
            (a.get("event_title") or "")[:60],
            (a.get("detail") or a.get("question") or "")[:120]))
    if len(fresh) > CAP:
        segs.append("(另有%d条略)" % (len(fresh) - CAP))
    err = send("\n".join(segs))
    with open(state_p, "w", encoding="utf-8") as f:
        json.dump(pushed, f)
    print("wecom_alert: pushed %d errcode=%s" % (len(fresh), err))


if __name__ == "__main__":
    main()
