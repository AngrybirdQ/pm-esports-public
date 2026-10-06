#!/usr/bin/env python3
"""OpenDota 战队统计采集 — odota_stats.py (2026-10-07)

DOTA2唯一免key开放API。对 watchlist 里未开赛的DOTA2映射比赛,
取双方战队近期战绩(match winrate, n场) → data/odota/stats.jsonl
供夜loop角度挖掘。OpenDota限速60req/min, 每轮预算30req。"""
import json
import os
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "odota", "stats.jsonl")
UA = "pm-esports-research/1.0 (automated)"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def search_team(name):
    d = get("https://api.opendota.com/api/search?q=%s"
            % urllib.parse.quote(name))
    for t in d or []:
        if t.get("team_id"):
            return t["team_id"]
    return None


def main():
    import urllib.parse  # noqa: F401
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    cur_p = os.path.join(ROOT, "state", "odota_teamid.json")
    try:
        tids = json.load(open(cur_p, encoding="utf-8"))
    except Exception:
        tids = {}
    budget = 30
    out_rows = []
    # watchlist里的DOTA2未开赛映射比赛
    import psycopg2
    conn = psycopg2.connect("postgresql://prediction:pred_local_2026@localhost:5432/prediction_db")
    with conn, conn.cursor() as cur:
        cur.execute("""
            SELECT m.local_match_id, um.team_a, um.team_b, um.start_time
            FROM pm_match_map m
            JOIN upcoming_matches um ON um.id = m.local_match_id
            WHERE upper(um.game_type) = 'DOTA2'
              AND um.start_time > now() - interval '1 hour'
              AND um.start_time < now() + interval '3 days'""")
        matches = cur.fetchall()
    for mid, ta, tb, st in matches[:10]:
        sides = {}
        for team in (ta, tb):
            if team not in tids:
                if budget <= 0:
                    break
                budget -= 1
                tids[team] = search_team(team)
                time.sleep(1.2)
            sides[team] = tids.get(team)
        if not all(sides.values()):
            continue
        rec = {"match_id": mid, "teams": {ta: sides[ta], tb: sides[tb]},
               "start": str(st), "fetched": time.time()}
        for team, tid in sides.items():
            if budget <= 0:
                break
            budget -= 1
            try:
                rec[team] = {"recent": get(
                    "https://api.opendota.com/api/teams/%s/recentMatches"
                    % tid)[:12]}
                time.sleep(1.2)
            except Exception as e:
                rec[team] = {"error": str(e)}
        out_rows.append(rec)
    json.dump(tids, open(cur_p, "w", encoding="utf-8"))
    if out_rows:
        with open(OUT, "a", encoding="utf-8") as f:
            for r in out_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({"matches": len(out_rows), "budget_left": budget}))


if __name__ == "__main__":
    main()
