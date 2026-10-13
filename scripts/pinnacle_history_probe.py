#!/usr/bin/env python3
"""Pinnacle 历史数据面探测 v2 — pinnacle_history_probe.py (10-13)
v1缺陷: 自动选的测试联赛是活跃联赛, 历史问题没测到; commit撞race静默丢结果。
v2: 专测历史联赛(all=true中 matchupCount=0 的样例10个) → matchups是否返回
    已结束场次(startTime分布) → 已结束场的markets是否还活着(价在/closed标记)。
"""
import json
import os
import time
import urllib.parse
import urllib.request

KEY = os.environ.get("PINNACLE_KEY", "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R")
W = "https://pinnacle-anchor.577813472.workers.dev"
G = "https://guest.api.arcadia.pinnacle.com/0.1"
RES = {"probe": "history-v2", "ts": time.strftime("%FT%TZ"), "results": []}


def via_worker(path, timeout=45):
    r = urllib.request.Request(W + "/?url=" + urllib.parse.quote(G + path, safe=""),
        headers={"x-key": KEY, "Accept": "application/json", "User-Agent": "probe-v2"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return 0, str(e)[:80]


def rec(label, status, data, extra=None):
    n = len(data) if isinstance(data, list) else "-"
    e = dict(extra or {})
    RES["results"].append({"label": label, "status": status, "count": n, **e})
    print(label, "->", status, "count:", n, str(e)[:100], flush=True)


def main():
    st, d = via_worker("/sports/12/leagues?all=true", timeout=120)
    rec("all=true", st, d)
    if not isinstance(d, list):
        print("catalog dead"); return
    # 历史联赛候选: matchupCount=0 (赛季已结束), 按id排序取多样本(小id=更老)
    hist = sorted([l for l in d if (l.get("matchupCount") or 0) == 0],
                  key=lambda l: l["id"])
    print(f"historical candidates (matchupCount=0): {len(hist)}", flush=True)
    RES["historical_candidates"] = len(hist)
    if not hist:
        print("no zero-count leagues; all leagues live?"); return
    # 取样: 最老10个 + 中段5个 + 最新5个
    sample = hist[:10] + hist[len(hist)//2:len(hist)//2+5] + hist[-5:]
    past_found = 0
    for lg in sample:
        lid, name = lg["id"], (lg.get("name") or "")[:40]
        st, m = via_worker(f"/leagues/{lid}/matchups")
        if not isinstance(m, list):
            rec(f"lg{lid} {name}", st, None, {"err": True})
            continue
        mus = [x for x in m if isinstance(x, dict) and x.get("startTime")]
        starts = sorted(x["startTime"] for x in mus)
        now = time.strftime("%Y-%m-%d", time.gmtime())
        past = [s for s in starts if s[:10] < now]
        if mus and starts and starts[-1][:10] < now:
            past_found += 1
        rec(f"lg{lid} {name}", st, mus,
            {"range": f"{starts[0][:10]}~{starts[-1][:10]}" if starts else "none",
             "past": len(past), "total": len(mus)})
        if past and not RES.get("settled_market_test"):
            mid = next((x["id"] for x in mus
                        if isinstance(x, dict) and x.get("startTime","")[:10] < now
                        and x.get("hasMarkets")), None)
            if mid:
                st2, ods = via_worker(f"/matchups/{mid}/markets/related/straight")
                alive = isinstance(ods, list) and len(ods) > 0
                sample_prices = (ods[0].get("prices") or [{}])[0] if alive else {}
                RES["settled_market_test"] = {
                    "matchup": mid, "status": st2, "markets_alive": alive,
                    "n_markets": len(ods) if alive else 0,
                    "sample_price": sample_prices}
                rec(f"  settled-market mid={mid}", st2, ods if alive else None,
                    {"alive": alive})
        time.sleep(0.4)
        if past_found >= 3 and RES.get("settled_market_test"):
            break
    RES["verdict"] = ("FEASIBLE" if RES.get("settled_market_test", {}).get("markets_alive")
                      else "NOT_FEASIBLE")
    print("VERDICT:", RES["verdict"], flush=True)
    os.makedirs("data/pinnacle", exist_ok=True)
    with open("data/pinnacle/probe_results.json", "w", encoding="utf-8") as f:
        json.dump(RES, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
