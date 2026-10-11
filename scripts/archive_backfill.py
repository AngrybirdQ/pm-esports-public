#!/usr/bin/env python3
"""档案阶段2 — archive_backfill.py (2026-10-11)
对data/archive/events.jsonl里每个token拉prices-history(interval=all),
写data/archive/token_history.jsonl。游标state/archive_bf_cursor.json(整文件行号),
每轮预算BUDGET_S(默认1200s), 断点续扫。"""
import json
import os
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "archive", "events.jsonl")
OUT = os.path.join(ROOT, "data", "archive", "token_history.jsonl")
CUR = os.path.join(ROOT, "state", "archive_bf_cursor.json")
UA = "pm-esports-research/1.0 (archive backfill)"
BUDGET_S = int(os.environ.get("BF_BUDGET_S", "1200"))


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def main():
    if not os.path.exists(SRC):
        print("no events.jsonl yet")
        return
    try:
        cur = json.load(open(CUR, encoding="utf-8"))
    except Exception:
        cur = {"line": 0, "tok_idx": 0}
    t0 = time.time()
    n_tok = 0
    with open(SRC, encoding="utf-8") as f:
        lines = f.readlines()
    li = cur.get("line", 0)
    ti = cur.get("tok_idx", 0)
    while li < len(lines) and time.time() - t0 < BUDGET_S:
        try:
            ev = json.loads(lines[li])
        except ValueError:
            li += 1; ti = 0; continue
        toks = []
        for mk in ev.get("markets") or []:
            for t, cid in (mk.get("tokens") or {}).items():
                toks.append((mk.get("q") or "", t, cid))
        while ti < len(toks) and time.time() - t0 < BUDGET_S:
            q, tok, cid = toks[ti]
            ti += 1
            try:
                d = fetch("https://clob.polymarket.com/prices-history?market=%s"
                          "&interval=all&fidelity=600" % tok)
                pts = (d or {}).get("history") or []
            except Exception:
                pts = []
            if pts:
                rec = {"token": tok, "condition_id": cid, "question": q[:80],
                       "event_id": ev.get("event_id"), "title": ev.get("title"),
                       "points": pts}
                with open(OUT, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_tok += 1
            time.sleep(0.05)
        if ti >= len(toks):
            li += 1; ti = 0
            json.dump({"line": li, "tok_idx": 0}, open(CUR, "w"))
    json.dump({"line": li, "tok_idx": ti}, open(CUR, "w"))
    print(json.dumps({"tokens_new": n_tok, "line": li,
                      "total_lines": len(lines),
                      "elapsed": round(time.time() - t0)}))


if __name__ == "__main__":
    main()
