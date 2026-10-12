#!/usr/bin/env python3
"""Pinnacle 历史全量扫描 — pinnacle_history_sweep.py (2026-10-11)

leagues?all=true (5669联赛) → 过滤电竞历史赛季 → 逐联赛matchups →
已结算matchup的straight markets价 → data/pinnacle_archive/leagues.jsonl + odds.jsonl。
游标state/ph_sweep_cursor.json断点续扫; 每轮预算BUDGET_S。
判定历史: eventCount==0(已打完) 且 名字含年份。"""
import glob
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request

KEY = os.environ.get("PINNACLE_KEY", "")
W = "https://pinnacle-anchor.577813472.workers.dev"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUR = os.path.join(ROOT, "state", "ph_sweep_cursor.json")
LGF = os.path.join(ROOT, "data", "pinnacle_archive", "leagues.jsonl")
ODF = os.path.join(ROOT, "data", "pinnacle_archive", "odds.jsonl")
BUDGET_S = int(os.environ.get("PH_BUDGET_S", "1200"))
GAME_KW = ["counter", "csgo", "cs2", "dota", "league of legends", "lol",
           "valorant", "starcraft", "overwatch", "king of glory", "rainbow",
           "arena of valor"]
UA = "pm-esports-research/1.0"


def wreq(path):
    url = W + "/?url=" + urllib.parse.quote(
        "https://guest.api.arcadia.pinnacle.com/0.1" + path, safe="")
    req = urllib.request.Request(url, headers={
        "x-key": KEY, "Accept": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode())


def amer_to_dec(price):
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    return round(p + 100, 3) if p > 0 else round(100 / abs(p) + 1, 3)


def main():
    if not KEY:
        print("no PINNACLE_KEY")
        return
    os.makedirs(os.path.dirname(LGF), exist_ok=True)
    try:
        cur = json.load(open(CUR, encoding="utf-8"))
    except Exception:
        cur = {"offset": 0}
    off = cur.get("offset", 0)
    t0 = time.time()
    n_lg = n_odds = n_hist = 0
    while time.time() - t0 < BUDGET_S:
        d = wreq("/sports/12/leagues?all=true&featured=false&offset=%d" % off)
        if not isinstance(d, list) or not d:
            cur["done"] = True
            json.dump(cur, open(CUR, "w"))
            break
        for lg in d:
            nm = (lg.get("name") or "").lower()
            if not any(k in nm for k in GAME_KW):
                continue
            if lg.get("eventCount", 0) != 0:
                continue  # 只扫已打完的(历史)
            if not re.search(r"202[3-6]", nm):
                continue
            n_hist += 1
            lg_rec = {"league_id": lg.get("id"), "name": lg.get("name"),
                      "eventCount": lg.get("eventCount"),
                      "matchupCount": lg.get("matchupCount")}
            with open(LGF, "a", encoding="utf-8") as f:
                f.write(json.dumps(lg_rec, ensure_ascii=False) + "\n")
            if time.time() - t0 < BUDGET_S * 0.6:
                try:
                    mus = wreq("/leagues/%s/matchups" % lg["id"])
                    real = [m for m in (mus if isinstance(mus, list) else [])
                            if m.get("type") == "matchup" and m.get("participants")]
                    for m in real[:20]:
                        names = [p.get("name", "?") for p in m.get("participants") or []]
                        home, away = (names + ["?", "?"])[:2]
                        try:
                            odds = wreq("/matchups/%s/markets/related/straight"
                                        % m["id"])
                        except Exception:
                            continue
                        if not isinstance(odds, list):
                            continue
                        for mk in odds:
                            mtype = mk.get("type")
                            if mtype == "moneyline" and mk.get("key") != "s;0;m":
                                continue
                            if mtype == "spread" and mk.get("period") != 0:
                                continue
                            if mtype not in ("moneyline", "spread"):
                                continue
                            for pr in mk.get("prices") or []:
                                dec = amer_to_dec(pr.get("price"))
                                side = {"home": home, "away": away}.get(
                                    pr.get("designation"),
                                    pr.get("designation") or "")
                                if not dec or not side:
                                    continue
                                if mtype == "spread":
                                    pts = pr.get("points")
                                    if pts is None:
                                        continue
                                    side = "%s%+g" % (side, pts)
                                lh = hashlib.sha256(
                                    ("pinh|%s|%s|%s|%s" % (m["id"], mtype, side, dec))
                                    .encode()).hexdigest()
                                rec = {"snap_ts": None, "sport_key": "pin-%s" % (
                                           "cs" if ("counter" in nm or "csg" in nm) else "esports"),
                                       "event_id": "pinh:%s" % m["id"],
                                       "home": home, "away": away,
                                       "commence_time": m.get("startTime"),
                                       "bookmaker": "pinnacle-history",
                                       "outcome": side, "price": dec,
                                       "line_hash": lh}
                                with open(ODF, "a", encoding="utf-8") as f:
                                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                                n_odds += 1
                        time.sleep(0.15)
                except Exception as e:
                    print("lg %s err: %s" % (lg["id"], str(e)[:60]))
        off += len(d)
        cur["offset"] = off
        json.dump(cur, open(CUR, "w"))
        if len(d) < 100:
            cur["done"] = True
            json.dump(cur, open(CUR, "w"))
            break
    json.dump(cur, open(CUR, "w"))
    print(json.dumps({"leagues_hist": n_hist, "odds_rows": n_odds,
                      "offset": cur.get("offset")}))


if __name__ == "__main__":
    main()
