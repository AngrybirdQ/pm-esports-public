#!/usr/bin/env python3
"""套利扫描器 — arb_scan.py (10-07, 梯队①②)

跑在commit job(同run的books/side工件已checkout)。两类检查:
①跨平台: pm买Yes成本 + Kalshi买No成本(+Kalshi手续费) < 0.99 → 无风险价差,
  靠pm question与Kalshi title的队名token匹配(与betmatch同法);
②内部一致性: 同condition双腿best_ask之和 < 0.98 → 买全腿锁定利润。
告警经wecom_alert企微直推; 明细(alpha)不落仓, 只提交计数报告与去重状态(6h TTL)。
env: DATA_DIR(默认data) 可覆盖便于本地烟测。
"""
import glob
import hashlib
import json
import os
import time
import urllib.request

import wecom_alert

DATA = os.environ.get("DATA_DIR", "data")
STATE = os.path.join("state", "arb_seen.json")
REPORT = os.path.join("state", "arb_report.json")
KALSHI_FEE = 0.07  # c*(1-c)*0.07 per contract
BUFFER = 0.01


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(st):
    os.makedirs("state", exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"))


def toks(s):
    stop = {"vs", "the", "a", "of", "cs", "lol", "dota2", "dota", "will",
            "win", "match", "esports", "gaming", "team", "bo1", "bo3", "bo5"}
    return {w for w in (s or "").lower().replace("!", " ").split()
            if w and w not in stop and not w.startswith("(")}


def today_files(kind):
    d = time.strftime("%Y/%m")
    return sorted(glob.glob(os.path.join(DATA, d, f"*.{kind}.jsonl")))[-1:]


def main():
    alerts, seen = [], load_state()
    cutoff = time.time() - 6 * 3600
    for k in [k for k, v in seen.items() if v < cutoff]:
        del seen[k]

    # ── ②内部一致性: 同condition双腿ask和<0.98 ──
    books = {}
    for fp in today_files("books"):
        for ln in open(fp, encoding="utf-8"):
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if r.get("best_ask") is None:
                continue
            key = (r.get("event_id"), r.get("condition_id"))
            books.setdefault(key, []).append(r)
    internal = 0
    for (eid, cid), rows in books.items():
        asks = [(r.get("outcome"), float(r["best_ask"])) for r in rows]
        if len(asks) < 2:
            continue
        total = sum(a for _, a in asks)
        if total < 1 - BUFFER:
            internal += 1
            alerts.append(
                "**⚡内部套利** %s\n双腿ask和=%.3f<0.98: %s"
                % ((rows[0].get("question") or "")[:60], total,
                   ", ".join("%s@%.3f" % (o, a) for o, a in asks)))

    # ── ①跨平台: pm events × Kalshi markets 队名匹配 ──
    kalshi = {}
    for fp in today_files("side"):
        for ln in open(fp, encoding="utf-8"):
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if r.get("source") != "kalshi" or r.get("kind") != "market":
                continue
            p = r.get("payload") or {}
            kalshi[r.get("obs_key") or p.get("ticker")] = p
    cross = 0
    for (eid, cid), rows in books.items():
        q = rows[0].get("question") or ""
        if " vs " not in q or len(rows) < 2:
            continue
        qt = toks(q)
        for p in kalshi.values():
            kt = toks(" ".join([p.get("title") or "", p.get("subtitle") or ""]))
            if len(qt & kt) < 2 or len(qt & kt) / max(len(qt), 1) < 0.5:
                continue
            for r in rows:
                o = (r.get("outcome") or "").lower()
                otoks = toks(o)
                if not otoks or not (otoks & kt):
                    continue  # 只对能对上的腿做跨平台
                pm_ask = r.get("best_ask")
                if pm_ask is None:
                    continue
                pm_ask = float(pm_ask)
                no_cost = (100 - (p.get("yes_bid") or 0)) / 100.0
                fee = KALSHI_FEE * no_cost * (1 - no_cost)
                cost = pm_ask + no_cost + fee
                if cost < 1 - BUFFER:
                    cross += 1
                    key = hashlib.sha256(f"{eid}|{o}|{cost:.3f}".encode()
                                         ).hexdigest()[:16]
                    if seen.get(key, 0) < time.time() - 6 * 3600:
                        seen[key] = time.time()
                        alerts.append(
                            "**💰跨平台套利** %s\n买pm `%s` @%.3f + Kalshi NO "
                            "@%.3f + fee %.3f = **%.3f** < 1\nKalshi: %s"
                            % (q[:60], o[:20], pm_ask, no_cost, fee, cost,
                               (p.get("title") or "")[:50]))
    if alerts:
        md = ("**pm套利扫描 %s**\n内部%d条/跨平台%d条\n\n%s"
              % (time.strftime("%m-%d %H:%M"), internal, cross,
                 "\n\n".join(alerts[:5])))
        try:
            wecom_alert.send(md)
        except Exception as e:  # noqa: BLE001
            print("alert failed:", e)
    json.dump({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "internal": internal, "cross": cross, "alerts": len(alerts),
               "kalshi_markets": len(kalshi), "book_events": len(books)},
              open(REPORT, "w", encoding="utf-8"), ensure_ascii=False)
    save_state(seen)
    print(json.dumps({"internal": internal, "cross": cross,
                      "alerts": len(alerts)}))


if __name__ == "__main__":
    main()
