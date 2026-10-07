#!/usr/bin/env python3
"""权威结算解析器 — resolve_conditions.py (2026-10-07)

对本地已交易的全部conditionId, 从Gamma拉官方结算结果(结算后outcomePrices
含1/0) → data/resolved/resolved.jsonl。替代快照推断(有覆盖幸存者偏差)。
请求经 state/resolve_reqs_*.json 分块; 每轮预算3000req(约10min)。"""
import glob
import json
import os
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "resolved", "resolved.jsonl")
UA = "pm-esports-research/1.0 (resolution resolver)"
BUDGET_S = int(os.environ.get("RESOLVE_BUDGET_S", "1500"))


def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def parse_outcomes(m):
    """返回 (outcomes列表, prices列表[float], question)"""
    outs = m.get("outcomes")
    prs = m.get("outcomePrices")
    try:
        outs = json.loads(outs) if isinstance(outs, str) else (outs or [])
    except ValueError:
        outs = []
    try:
        prs = json.loads(prs) if isinstance(prs, str) else (prs or [])
    except ValueError:
        prs = []
    return outs, [float(p) for p in prs if str(p).replace(".", "").isdigit()], \
        m.get("question") or ""


def winner_from_prices(outs, prs):
    best, bp = None, 0.0
    for o, p in zip(outs, prs):
        if p > bp:
            best, bp = o, p
    return best if bp >= 0.98 else None


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    done_p = os.path.join(ROOT, "state", "resolve_done.json")
    try:
        done = set(json.load(open(done_p, encoding="utf-8")))
    except Exception:
        done = set()
    t0 = time.time()
    n_ok = n_no = 0
    chunks = sorted(glob.glob(os.path.join(
        ROOT, "state", "resolve_reqs_*.json")))
    for ch in chunks:
        cids = json.load(open(ch, encoding="utf-8"))
        for cid in cids:
            if cid in done or time.time() - t0 > BUDGET_S:
                continue
            rec = None
            try:
                ms = get("https://gamma-api.polymarket.com/markets"
                         "?condition_ids=%s" % cid)
                if isinstance(ms, list) and ms:
                    outs, prs, q = parse_outcomes(ms[0])
                    winner = winner_from_prices(outs, prs)
                    rec = {"condition_id": cid,
                           "question": q,
                           "outcomes": outs, "prices": prs,
                           "winner": winner,
                           "closed": ms[0].get("closed")}
            except Exception:
                pass
            if rec is None:
                # 兜底: CLOB market
                try:
                    m = get("https://clob.polymarket.com/markets/%s" % cid)
                    outs = [t.get("outcome") for t in m.get("tokens") or []]
                    prs = [float(t.get("price") or 0) for t in m.get("tokens") or []]
                    winner = None
                    bp = 0.0
                    for o, p in zip(outs, prs):
                        if p > bp:
                            best_o, bp = o, p
                    if bp >= 0.98:
                        winner = best_o
                    rec = {"condition_id": cid, "question": m.get("question") or "",
                           "outcomes": outs, "prices": prs, "winner": winner,
                           "closed": m.get("closed")}
                except Exception as e:
                    rec = {"condition_id": cid, "error": str(e)[:80]}
            if rec.get("winner"):
                n_ok += 1
            else:
                n_no += 1
            done.add(cid)
            with open(OUT, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            time.sleep(0.08)
    json.dump(sorted(done), open(done_p, "w", encoding="utf-8"))
    print(json.dumps({"resolved_ok": n_ok, "unresolved": n_no,
                      "done_total": len(done)}))


if __name__ == "__main__":
    main()
