#!/usr/bin/env python3
"""孪生主动扫描 + pm结算回填 — twin_and_settle.py (10-06, 优化①④)

跑在Actions上(gamma-api本机不可达)。输入 state/watchlist.json:
  matches       → 映射pm场: 开赛>3h的事件查Gamma结算价 → state/pm_settled.jsonl
  twin_requests → 未映射星源场: public-search队名扫描 → state/twin_hits.jsonl
本地 pull_and_ingest 把两个文件入库: pm_match_map / esports_results(source='pm')。
幂等: settled增量跳过(读旧文件), hits整文件重写(入库侧ON CONFLICT DO NOTHING)。
"""
import json
import os
import sys
import urllib.parse
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# 双布局兼容: 实仓(scripts/在根)=HERE/..; 本地镜像(collector/scripts/)=HERE/../..
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from collect import GAMMA, http_json, norm_txt  # noqa: E402
from pm_star_twin import MIN_SCORE, name_score  # noqa: E402 权威评分勿内联

STATE_DIR = os.path.join(HERE, "..", "state")
STALE_H = 96   # 开赛超96h的未结算事件放弃回填
FRESH_H = 3    # 开赛<3h视为未完赛


def load_watchlist():
    """与collect.py _payload同构: dispatch payload(env)优先, state文件兜底"""
    ev = os.environ.get("GITHUB_EVENT_PATH")
    if ev:
        try:
            with open(ev, encoding="utf-8") as f:
                data = (json.load(f).get("client_payload") or {}).get(
                    "watchlist")
            if data:
                return data
        except (OSError, ValueError):
            pass
    try:
        with open(os.path.join(STATE_DIR, "watchlist.json"),
                  encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def split_title_teams(title):
    """'Counter-Strike: M80 vs TYLOO (BO3) - ESL...' → (左队, 右队) 或 None"""
    core = (title or "").split(" - ")[0]
    if " vs " not in core:
        return None
    left, _, right = core.partition(" vs ")
    left = left.split(":", 1)[-1].strip()
    return left.strip(), right.strip()


def score_event(ta, tb, title):
    """星源双队 vs pm标题队名的权威评分, 任一侧<MIN_SCORE拒配"""
    parsed = split_title_teams(title)
    if not parsed:
        return None
    pl, pr = parsed
    sa = max(name_score(ta, pl), name_score(ta, pr))
    sb = max(name_score(tb, pl), name_score(tb, pr))
    if min(sa, sb) < MIN_SCORE:
        return None
    return (sa + sb) / 2


def scan_twins():
    reqs = load_watchlist().get("twin_requests") or []
    hits, queries = [], 0
    for r in reqs:
        ta, tb = r.get("team_a"), r.get("team_b")
        if not ta or not tb:
            continue
        best = None
        for q in (f"{ta} {tb}", ta):
            if queries >= 40:
                break
            queries += 1
            data, err = http_json(
                f"{GAMMA}/public-search?q={urllib.parse.quote(q)}&limit=10")
            for ev in (data or {}).get("events") or []:
                title = ev.get("title") or ""
                t = norm_txt(title)
                if norm_txt(ta) in t and norm_txt(tb) in t:
                    score = 0.90  # 全名包含=最高置信
                else:
                    score = score_event(ta, tb, title)
                if score is None:
                    continue
                if best is None or score > best[0]:
                    best = (score, ev)
            if best and best[0] >= 0.90:
                break
        if best and best[0] >= MIN_SCORE:
            score, ev = best
            hits.append({"local_match_id": r.get("local_match_id"),
                         "team_a": ta, "team_b": tb,
                         "game_type": r.get("game"),
                         "pm_event_id": str(ev.get("id")),
                         "pm_title": ev.get("title"),
                         "pm_end_date": ev.get("endDate"),
                         "score": round(score, 3)})
            print(f"twin HIT [{r.get('game')}] {ta} vs {tb} -> "
                  f"pm:{ev.get('id')} score={score:.2f}", flush=True)
    with open(os.path.join(STATE_DIR, "twin_hits.jsonl"), "w",
              encoding="utf-8") as f:
        for h in hits:
            f.write(json.dumps(h, ensure_ascii=False) + "\n")
    return len(hits)


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def ml_winner(ev):
    """事件内主盘(独赢, 非Game N)市场的已结算胜方 outcome 名; 未结算返回 None"""
    for m in ev.get("markets") or []:
        q = m.get("question") or ""
        if " vs " not in q or "Game " in q or "Map " in q:
            continue
        outs = m.get("outcomes")
        prices = m.get("outcomePrices")
        try:
            outs = json.loads(outs) if isinstance(outs, str) else (outs or [])
            prices = json.loads(prices) if isinstance(prices, str) else (
                prices or [])
        except ValueError:
            continue
        if len(outs) != 2 or len(prices) != 2:
            continue
        try:
            p = [float(x) for x in prices]
        except (TypeError, ValueError):
            continue
        if max(p) < 0.99:  # 未结算(或有货损0.5)
            continue
        return outs[p.index(max(p))]
    return None


def settle():
    matches = load_watchlist().get("matches") or []
    out_path = os.path.join(STATE_DIR, "pm_settled.jsonl")
    done = set()
    if os.path.exists(out_path):
        try:
            with open(out_path, encoding="utf-8") as f:
                done = {json.loads(ln).get("pm_event_id")
                        for ln in f if ln.strip()}
        except (OSError, ValueError):
            done = set()
    now = datetime.now(timezone.utc)
    rows, queries = [], 0
    for m in matches:
        eid = str(m.get("pm_event_id") or "")
        if not eid or eid in done:
            continue
        st = parse_iso(m.get("start_time"))
        if st is None:
            continue
        age_h = (now - st).total_seconds() / 3600
        if age_h < FRESH_H or age_h > STALE_H:
            continue
        if queries >= 20:
            break
        queries += 1
        data, err = http_json(f"{GAMMA}/events?id={eid}")
        evs = data if isinstance(data, list) else (
            (data or {}).get("data") or [])
        if not evs:
            continue
        winner = ml_winner(evs[0])
        if not winner:
            continue
        rows.append({"pm_event_id": eid,
                     "local_match_id": m.get("local_match_id"),
                     "winner_outcome": winner,
                     "teams": m.get("teams"),
                     "start_time": m.get("start_time")})
        print(f"settled pm:{eid} winner={winner}", flush=True)
    if rows:
        with open(out_path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


if __name__ == "__main__":
    n1 = scan_twins()
    n2 = settle()
    print(json.dumps({"twin_hits": n1, "new_settled": n2}))
