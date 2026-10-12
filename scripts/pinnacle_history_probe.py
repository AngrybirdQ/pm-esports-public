#!/usr/bin/env python3
"""Pinnacle 历史数据面探测 — pinnacle_history_probe.py (2026-10-11)

目标: 判定 guest API 能否取到过去两年的已结算盘口。
探测矩阵:
  1) Worker vs runner直连两条网络路径
  2) leagues 端点参数变体 (featured/all/settled)
  3) matchups 端点参数变体 (含老联赛id)
  4) 单场 markets 是否含 closed/settled 标记
输出: 探测矩阵打印 + data/pinnacle/probe_results.json
"""
import json
import os
import time
import urllib.parse
import urllib.request

KEY = os.environ.get("PINNACLE_KEY", "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R")
W = "https://pinnacle-anchor.577813472.workers.dev"
G = "https://guest.api.arcadia.pinnacle.com/0.1"
RES = {"probe": "history-endpoints", "ts": time.strftime("%FT%TZ"),
       "results": []}


def req(url, timeout=30):
    r = urllib.request.Request(url, headers={
        "x-key": KEY, "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (probe)"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return 0, str(e)[:80]


def via_worker(path):
    return req(W + "/?url=" + urllib.parse.quote(G + path, safe=""))


def direct(path):
    return req(G + path)


def rec(label, status, data):
    n = len(data) if isinstance(data, list) else "-"
    sample = ""
    if isinstance(data, list) and data:
        sample = sorted(data[0].keys())[:8]
    RES["results"].append({"label": label, "status": status,
                           "count": n, "sample_keys": sample})
    print(label, "->", status, "count:", n, str(sample)[:70])


def main():
    # 路径1: worker可用性
    st, d = via_worker("/sports/12/leagues?featured=false")
    rec("worker:leagues-featured", st, d)
    worker_ok = st == 200
    # 路径2: runner直连pinnacle(海外runner可能可达)
    st2, d2 = direct("/sports/12/leagues?featured=false")
    rec("direct:leagues-featured", st2, d2)
    use_direct = st2 == 200
    fetch = direct if use_direct else via_worker
    tag = "direct" if use_direct else "worker"

    if not (worker_ok or use_direct):
        print("both paths dead")
        return

    # leagues参数变体
    for v in ("?all=true", "?settled=true", "?featured=false&all=true", ""):
        st, d = fetch("/sports/12/leagues" + v)
        rec("leagues" + (v or "-none"), st, d)
        time.sleep(0.4)

    # 取一个真实联赛id(现场找), 再试matchups变体 + 老联赛
    st, d = fetch("/sports/12/leagues?featured=false")
    lid = None
    if isinstance(d, list):
        for l in d:
            if (l.get("eventCount") or 0) + (l.get("matchupCount") or 0) > 0:
                lid = l.get("id")
                break
    if lid:
        for v in ("", "?settled=true", "?all=true", "?includePast=true"):
            st, d = fetch("/leagues/%s/matchups%s" % (lid, v))
            rec("matchups lid=%s %s" % (lid, v or "-none"), st, d)
            time.sleep(0.4)
        # 单场markets: 看closed标记
        st, mus = fetch("/leagues/%s/matchups" % lid)
        if isinstance(mus, list) and mus:
            mid = mus[0].get("id")
            st, d = fetch("/matchups/%s/markets/related/straight" % mid)
            rec("markets mid=%s" % mid, st, d)
            if isinstance(d, list) and d:
                RES["results"].append({"label": "market-sample",
                                       "keys": sorted(d[0].keys())})

    os.makedirs("data/pinnacle", exist_ok=True)
    with open("data/pinnacle/probe_results.json", "w", encoding="utf-8") as f:
        json.dump(RES, f, ensure_ascii=False, indent=1)
    print("saved data/pinnacle/probe_results.json")


if __name__ == "__main__":
    main()
