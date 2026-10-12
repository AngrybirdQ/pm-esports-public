#!/usr/bin/env python3
"""Pinnacle anchor collection (Actions side) — pinnacle_anchor_collect.py

Replaces local pinnacle_collect.py: overseas runner hits CF Worker directly
(no mihomo needed). Delta-capture: rows whose line_hash appeared in the last
committed hash-set are skipped (odds unchanged = nothing to store).
Output: data/pinnacle/YYYY-MM-DD.jsonl (same row shape as pm_book_odds).
Env: PINNACLE_KEY (repo secret). Stdlib only."""
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request

WORKER = "https://pinnacle-anchor.577813472.workers.dev"
KEY = os.environ.get("PINNACLE_KEY", "")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME_MAP = {"league of legends": "lol", "dota 2": "dota2", "cs2": "cs",
            "counter": "cs", "valorant": "valorant", "rainbow six": "r6",
            "king of glory": "hok", "arena of valor": "aov"}
LEAGUE_CAP, MATCHUP_CAP = 15, 12


def wreq(url):
    req = urllib.request.Request(
        WORKER + "/?url=" + urllib.parse.quote(url, safe=""),
        headers={"x-key": KEY, "Accept": "application/json",
                 "User-Agent": "pinnacle-anchor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=35) as r:
            return True, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return False, None, e.code
    except Exception:
        return False, None, 0


def amer_to_dec(price):
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    return round(p + 100, 3) if p > 0 else round(100 / abs(p) + 1, 3)


def main():
    if not KEY:
        print("pinnacle_anchor: no PINNACLE_KEY, skip")
        return
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hash_p = os.path.join(root, "state", "pinnacle_last_hashes.json")
    try:
        last_hashes = set(json.load(open(hash_p, encoding="utf-8")))
    except Exception:
        last_hashes = set()

    out_dir = os.path.join(root, "data", "pinnacle")
    os.makedirs(out_dir, exist_ok=True)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    out_f = os.path.join(out_dir, day + ".jsonl")
    snap_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    ok, leagues, code = wreq(
        "https://guest.api.arcadia.pinnacle.com/0.1/sports/12/leagues?featured=false")
    if not ok or not isinstance(leagues, list):
        print(json.dumps({"error": "catalog http=%s" % code}))
        return
    live = [l for l in leagues
            if ((l.get("eventCount") or 0) + (l.get("matchupCount") or 0)) > 0]
    rows = []
    n_match = 0
    for lg in live[:LEAGUE_CAP]:
        lname = (lg.get("name") or "").lower()
        game = next((g for k, g in GAME_MAP.items() if k in lname), "esports")
        ok2, mus, code2 = wreq(
            "https://guest.api.arcadia.pinnacle.com/0.1/leagues/%s/matchups"
            % lg["id"])
        if not ok2 or not isinstance(mus, list):
            continue
        real = [m for m in mus if m.get("type") == "matchup" and m.get("participants")]
        n_match += len(real)
        for m in real[:MATCHUP_CAP]:
            names = [p.get("name", "?") for p in m["participants"]]
            home, away = (names + ["?", "?"])[:2]
            start = m.get("startTime")
            ok3, odds, code3 = wreq(
                "https://guest.api.arcadia.pinnacle.com/0.1/matchups/%s/markets/related/straight"
                % m["id"])
            if not ok3 or not isinstance(odds, list):
                continue
            for mk in odds:
                mtype = mk.get("type")
                if mtype == "moneyline":
                    if mk.get("key") != "s;0;m":
                        continue
                elif mtype == "spread":
                    if mk.get("period") != 0:
                        continue
                else:
                    continue
                for pr in mk.get("prices") or []:
                    dec = amer_to_dec(pr.get("price"))
                    side = {"home": home, "away": away, "draw": "draw"}.get(
                        pr.get("designation"), pr.get("designation"))
                    if not dec or not side or side == "?":
                        continue
                    if mtype == "spread":
                        pts = pr.get("points")
                        if pts is None:
                            continue
                        side = "%s%+g" % (side, pts)
                    lh = hashlib.sha256(
                        ("pin|%s|%s|%s|%s" % (m["id"], mtype, side, dec))
                        .encode()).hexdigest()
                    if lh in last_hashes:
                        continue
                    last_hashes.add(lh)
                    rows.append({
                        "snap_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "sport_key": "pin-%s" % game, "event_id": "pin:%s" % m["id"],
                        "home": home, "away": away, "commence_time": start,
                        "bookmaker": "pinnacle", "outcome": side, "price": dec,
                        "line_hash": lh})
            time.sleep(0.2)
    if rows:
        with open(out_f, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + chr(10))
        # 保留最近14天文件(repo体积控制)
        cutoff = time.time() - 14 * 86400
        for f in glob.glob(os.path.join(out_dir, "*.jsonl")):
            if os.path.getmtime(f) < cutoff:
                os.remove(f)
    json.dump(sorted(last_hashes)[-20000:], open(hash_p, "w"))
    print(json.dumps({"rows_new": len(rows), "matchups": n_match,
                      "leagues": len(live)}))


import glob
if __name__ == "__main__":
    main()
