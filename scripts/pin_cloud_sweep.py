#!/usr/bin/env python3
"""pin_cloud_sweep.py — Pinnacle云端扫描(Actions版, 10-13)
链路: Actions → CF Worker(pinnacle-anchor) → guest.api.arcadia.pinnacle.com
输出: data/pinnacle/YYYY-MM-DDTHH.jsonl (契约=pinnacle_rows_from_file:
  {ts, source:"pinnacle", kind:"market", obs_key:event_id,
   payload:{event_id, home, away, start, league, prices:[{designation,price,points}]}})
本地 pull_and_ingest 自动拉取入库 pm_book_odds(line_hash幂等)。
预算: featured联赛≤20, 每联赛matchup≤12, 每 matchup 直调 markets;
     moneyline(s;0;m)+全场spread(period=0) 双市场, 美价转欧。
env: PIN_KEY, PIN_WORKER(默认硬编码)
"""
import json
import os
import time
import urllib.parse
import urllib.request

WORKER = os.environ.get(
    "PIN_WORKER", "https://pinnacle-anchor.577813472.workers.dev")
PIN_KEY = os.environ.get("PIN_KEY", "")
BASE = "https://guest.api.arcadia.pinnacle.com/0.1"
SCHEMA_LEAGUES = ("id", "name")
SCHEMA_MATCHUPS = ("id", "type")


def wreq(url, timeout=35):
    req = urllib.request.Request(
        WORKER + "/?url=" + urllib.parse.quote(url, safe=""),
        headers={"x-key": PIN_KEY, "Accept": "application/json",
                 "User-Agent": "pin-cloud-sweep/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, json.loads(r.read().decode()), r.status
    except urllib.error.HTTPError as e:
        return False, None, e.code
    except Exception:
        return False, None, 0


def amer_to_dec(a):
    try:
        a = float(a)
    except (TypeError, ValueError):
        return None
    return round(1 + (a / 100 if a > 0 else 100 / -a), 3) if a else None


def main():
    if not PIN_KEY:
        print(json.dumps({"error": "no PIN_KEY"}))
        return
    ok, leagues, code = wreq(f"{BASE}/sports/12/leagues?featured=true")
    if not ok or not isinstance(leagues, list):
        print(json.dumps({"error": f"catalog http={code}"}))
        return
    if leagues and any(k not in leagues[0] for k in SCHEMA_LEAGUES):
        print(json.dumps({"error": "schema drift leagues"}))
        return
    live = [l for l in leagues
            if ((l.get("eventCount") or 0) + (l.get("matchupCount") or 0)) > 0]
    now = time.gmtime()
    day_dir = os.path.join("data", "pinnacle")
    os.makedirs(day_dir, exist_ok=True)
    # 文件名必须含 .pinnacle.jsonl 字面量 — pull_and_ingest按此路由到
    # pinnacle_rows_from_file(本契约); 否则被旧pin平铺管道抢路由→NOT NULL崩
    fp = os.path.join(
        day_dir, time.strftime("%Y-%m-%dT%H", now) + ".pinnacle.jsonl")
    n_mu = n_rows = n_err = 0
    with open(fp, "a", encoding="utf-8") as f:
        for lg in live[:20]:
            ok2, mus, code2 = wreq(f"{BASE}/leagues/{lg['id']}/matchups")
            if not ok2 or not isinstance(mus, list):
                n_err += 1
                continue
            real = [m for m in mus
                    if m.get("type") == "matchup" and m.get("participants")]
            n_mu += len(real)
            for m in real[:12]:
                names = [p.get("name", "?") for p in m["participants"]]
                home, away = (names + ["?", "?"])[:2]
                if home == "?" and away == "?":
                    continue
                ok3, odds, code3 = wreq(
                    f"{BASE}/matchups/{m['id']}/markets/related/straight")
                if not ok3 or not isinstance(odds, list):
                    n_err += 1
                    continue
                # 契约对齐: ingest按prices[0]=home/[1]=away(2路)消化,
                # 只收moneyline双腿(s;0;m), spread另轨后续扩展
                prices = []
                for mk in odds:
                    if mk.get("type") != "moneyline" or mk.get("key") != "s;0;m":
                        continue
                    for pr in mk.get("prices") or []:
                        dec = amer_to_dec(pr.get("price"))
                        if not dec:
                            continue
                        prices.append({"designation": pr.get("designation"),
                                       "price": dec})
                by_des = {p["designation"]: p for p in prices
                          if p.get("designation") in ("home", "away")}
                if len(by_des) < 2:
                    continue
                prices = [by_des["home"], by_des["away"]]
                rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "source": "pinnacle", "kind": "market",
                       "obs_key": str(m["id"]),
                       "payload": {"event_id": str(m["id"]), "home": home,
                                   "away": away,
                                   "start": m.get("startTime"),
                                   "league": lg.get("name"),
                                   "prices": prices}}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_rows += 1
    os.makedirs("state", exist_ok=True)
    json.dump({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "leagues": len(live), "matchups": n_mu,
               "events": n_rows, "errors": n_err},
              open("state/pin_cloud_report.json", "w"), ensure_ascii=False)
    print(json.dumps({"leagues": len(live), "matchups": n_mu,
                      "events": n_rows, "errors": n_err}))


if __name__ == "__main__":
    main()
