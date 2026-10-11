#!/usr/bin/env python3
"""Push fresh pm anomalies straight to WeCom group webhook (cloud-side).

v2 (2026-10-10 内容复核): 治"数量多内容模糊"——
  ① sharp_move(昨日1744条=87%噪音)不再推送, 只落digest
  ② 同一(事件,方向)聚合成一条: 列各类型+最大单笔, 不逐条刷
  ③ 文案统一人话结构: 谁在打谁/几点开赛/买了哪边/多少钱/怎么看
Dedup state: data/wecom_pushed.json. Env: WECOM_WEBHOOK. Stdlib only."""
import glob
import json
import os
import re
import time
import urllib.request

PUSH_TYPES = {"big_trade", "sharp_wallet", "big_premarket",
              "one_side_flow", "concentrated_entry", "insider_wallet"}
QUIET_TYPES = {"sharp_move"}          # 只进digest, 不推企微
TTL_H = 6
FRESH_FILE_MIN = 15
LABEL = {"big_trade": "🐋大单", "sharp_wallet": "🧠聪明钱",
         "big_premarket": "💰赛前大单", "one_side_flow": "⚔单边资金流",
         "concentrated_entry": "🎯集中建仓", "insider_wallet": "🔒S级内幕"}


def send(md):
    body = {"msgtype": "markdown", "markdown": {"content": md[:3800]}}
    req = urllib.request.Request(
        os.environ["WECOM_WEBHOOK"], data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode()).get("errcode")


def ev_of(a):
    return a.get("event_id") or a.get("condition_id") or "?"


def side_of(a):
    return str(a.get("outcome") or "")


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

    groups = {}   # (event_id, outcome) -> [anoms]
    quiet = 0
    for f in glob.glob(os.path.join(root, "data", "**", "*.anoms.jsonl"),
                       recursive=True):
        if now - os.path.getmtime(f) > FRESH_FILE_MIN * 60:
            continue
        for line in open(f, encoding="utf-8"):
            try:
                a = json.loads(line)
            except ValueError:
                continue
            if a.get("type") in QUIET_TYPES:
                quiet += 1
                continue
            if a.get("type") not in PUSH_TYPES:
                continue
            gk = (ev_of(a), side_of(a))
            if any(k[0] == gk[0] and k[1] == gk[1] for k in pushed):
                continue
            groups.setdefault(gk, []).append(a)
    if not groups:
        json.dump(pushed, open(state_p, "w", encoding="utf-8"))
        return

    segs = []
    events = sorted(
        groups.items(),
        key=lambda kv: -max(float(a.get("cost_usd") or 0) for a in kv[1]))
    for (eid, outcome), anoms in events[:6]:
        first = anoms[0]
        detail = first.get("detail") or first.get("question") or ""
        # 10-11: 已结束比赛的清仓异动不推(detail里有"·MM-DD HH:MM开赛")
        sm = re.search(r"(\d{2})-(\d{2}) (\d{2}):(\d{2})开赛", detail)
        if sm:
            import datetime as _dt
            try:
                st_ = _dt.datetime.strptime(
                    f"2026-{sm.group(1)}-{sm.group(2)} {sm.group(3)}:{sm.group(4)}",
                    "%Y-%m-%d %H:%M").replace(
                    tzinfo=_dt.timezone(_dt.timedelta(hours=8)))
                if st_ < _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8))) \
                        - _dt.timedelta(hours=2):
                    continue
            except ValueError:
                pass
        m = re.search(r"\[([a-z0-9]+)\]\s*([^·|]+?)\s*·\s*([0-9-]+ [0-9:]+)开赛",
                      detail)
        if m:
            game, pairing, start = m.group(1), m.group(2).strip(), m.group(3)
        else:
            game, pairing, start = "", (first.get("event_title")
                                        or "")[:50], ""
        types_in = []
        max_cost = 0.0
        wallets = set()
        for a in anoms:
            types_in.append(LABEL.get(a.get("type"), a.get("type")))
            max_cost = max(max_cost, float(a.get("cost_usd") or 0))
            if a.get("wallet"):
                wallets.add(a["wallet"][:10])
        title_txt = pairing or (first.get("event_title") or "")[:50]
        segs.append("**%s | %s**%s" % (
            LABEL.get(first.get("type"), first.get("type")), title_txt,
            f"（{start}开赛）" if start else ""))
        segs.append("方向: **%s** | 信号: %s | 最大单笔 $%s%s"
                    % (outcome, "+".join(dict.fromkeys(types_in)),
                       f"{max_cost:,.0f}",
                       f" | 钱包{len(wallets)}个" if wallets else ""))
        segs.append("怎么看: " + _read(first, max_cost))
    if len(events) > 6:
        segs.append("(另有%d场略)" % (len(events) - 6))
    if quiet:
        segs.append(f"(另{quiet}条小幅波动已汇总进摘要文件, 不再刷屏)")

    err = send("\n".join(segs)[:3800])
    for (eid, outcome), anoms in groups.items():
        for a in anoms:
            key = "%s|%s|%s" % (a.get("type"), a.get("condition_id"),
                                a.get("outcome"))
            pushed[key] = now
    with open(state_p, "w", encoding="utf-8") as f:
        json.dump(pushed, f)
    print("wecom_alert: pushed %d events (+%d quiet) errcode=%s"
          % (len(events), quiet, err))


def _read(a, max_cost):
    """按类型给人话解读(统一口径)."""
    t = a.get("type")
    if t == "insider_wallet":
        return "回测验证钱包赛前真金入场, 参考价值最高; 小仓跟随可以, 不重仓"
    if t == "sharp_wallet":
        return "有战绩的钱包下注, 方向值得看一眼"
    if t in ("big_premarket", "big_trade"):
        return ("有人开赛前砸了$%s买这一边——大钱通常有理由, 但也可能是错钱; "
                "跟不跟看世博价有没有肉(≥pm公平价+3%%)" % f"{max_cost:,.0f}")
    if t == "one_side_flow":
        return "这一边1小时内只进不出, 资金明显站队; 注意追高风险"
    if t == "concentrated_entry":
        return "有人在这一边悄悄吸筹(占市场成交比例高), 先观察再决定"
    return "资金/价格异动, 参考"


if __name__ == "__main__":
    main()
