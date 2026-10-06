#!/usr/bin/env python3
"""Liquipedia赛果第二源交叉核对 — liquipedia_results.py (2026-10-07)

对近48h已结算的pm来源比赛, 用Liquipedia search+wikitext找比分,
与esports_results比分交叉核对 → data/liquipedia/results_check.jsonl
(mismatch即⚠结算错误候选)。best-effort: 覆盖率取决于Liquipedia收录。"""
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "liquipedia", "results_check.jsonl")
UA = "pm-esports-research/1.0 (automated cross-check)"
SCORE_RE = re.compile(r"(\d)\s*[:–—-]\s*(\d)")


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "pm-esports-research/1.0 (automated)",
        "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        import gzip
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def wiki_search(game, q):
    try:
        d = json.loads(fetch("https://liquipedia.net/%s/api.php?action=query"
                             "&list=search&srsearch=%s&srlimit=3&format=json"
                             % (game, urllib.parse.quote(q))))
        return [x["title"] for x in (d.get("query", {}).get("search") or [])]
    except Exception:
        return []


def wiki_text(game, title):
    try:
        d = json.loads(fetch("https://liquipedia.net/%s/api.php?action=parse"
                             "&page=%s&prop=wikitext&format=json"
                             % (game, title.replace(" ", "_"))))
        return d.get("parse", {}).get("wikitext", {}).get("*", "")
    except Exception:
        return ""


def main():
    import psycopg2
    conn = psycopg2.connect("postgresql://prediction:pred_local_2026@localhost:5432/prediction_db")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    checked = mismatch = noref = 0
    with conn, conn.cursor() as cur:
        cur.execute("""
            SELECT match_id, game_type, team_a_name, team_b_name, score
            FROM esports_results
            WHERE source = 'polymarket' AND score IS NOT NULL
              AND collected_at > now() - interval '48 hours'""")
        rows = cur.fetchall()
    GAME_WIKI = {"dota2": "dota2", "lol": "leagueoflegends",
                 "cs": "counterstrike", "valorant": "valorant"}
    seen_pairs = set()
    out_rows = []
    for mid, gt, ta, tb, score in rows:
        pair = (norm(ta), norm(tb))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        wiki = GAME_WIKI.get((gt or "").lower())
        if not wiki:
            noref += 1
            continue
        titles = wiki_search(wiki, "%s %s" % (ta, tb))
        ref_score = None
        page = None
        for t in titles:
            text = wiki_text(wiki, t)
            if not text:
                continue
            both = [m for m in re.finditer(
                r"%s[\s\S]{0,400}?%s" % (re.escape(ta), re.escape(tb)), text)]
            both += [m for m in re.finditer(
                r"%s[\s\S]{0,400}?%s" % (re.escape(tb), re.escape(ta)), text)]
            for m in both:
                sm = SCORE_RE.search(m.group(0))
                if sm:
                    ref_score = "%s:%s" % (sm.group(1), sm.group(2))
                    page = t
                    break
            if ref_score:
                break
            time.sleep(1)
        if not ref_score:
            noref += 1
            continue
        checked += 1
        ok = (ref_score == score)
        if not ok:
            mismatch += 1
        out_rows.append({"match_id": mid, "gt": gt, "teams": "%s vs %s" % (ta, tb),
                         "er_score": score, "liquipedia_score": ref_score,
                         "page": page, "match": ok})
        time.sleep(1)
    with open(OUT, "a", encoding="utf-8") as f:
        ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        for r in out_rows:
            f.write(json.dumps({"ts": ts, **r}, ensure_ascii=False) + "\n")
    print(json.dumps({"checked": checked, "mismatch": mismatch,
                      "no_ref": noref}))


def norm(s):
    return (s or "").lower().strip()


from datetime import datetime  # noqa: E402

if __name__ == "__main__":
    main()
