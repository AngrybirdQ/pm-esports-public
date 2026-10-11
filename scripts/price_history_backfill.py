#!/usr/bin/env python3
"""pm价格史回填 — price_history_backfill.py (10-07, 梯队③)

研究转向⑨的燃料: 对watchlist场(经当日books的token_id)批量拉
clob prices-history(interval=all), 写data/history/ph_<date>.jsonl
(格式=hist_rows_from_file契约: {condition_id, event_id, points:[{t,p}]})。
本地ingest经HIST_INSERT入pm价格史表 → 世博滞后定价回测样本扩至全历史。
预算25 token/run, done状态state/ph_done.json(增量)。
"""
import glob
import json
import os
import time
import urllib.request

DATA = os.environ.get("DATA_DIR", "data")
HIST_DIR = os.path.join("data", "history")
STATE = os.path.join("state", "ph_done.json")
BUDGET = int(os.environ.get('BUDGET', 25))
API = "https://clob.polymarket.com/prices-history"


def load_done():
    try:
        return set(json.load(open(STATE, encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def main():
    done = load_done()
    tokens = {}
    for fp in sorted(glob.glob(os.path.join(DATA, time.strftime("%Y/%m"),
                                             f"*.{time.strftime('%Y-%m-%d')}"
                                             f".books.jsonl")))[-1:]:
        for ln in open(fp, encoding="utf-8"):
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if r.get("token_id") and r.get("condition_id"):
                tokens[r["token_id"]] = (r["condition_id"], r.get("event_id"))
    todo = [t for t in tokens if t not in done][:BUDGET]
    os.makedirs(HIST_DIR, exist_ok=True)
    out_fp = os.path.join(HIST_DIR, "ph_%s.jsonl" % time.strftime("%Y-%m-%d"))
    n_ok = n_err = 0
    if todo:
        with open(out_fp, "a", encoding="utf-8") as f:
            for t in todo:
                cid, eid = tokens[t]
                url = f"{API}?market={t}&interval=all&fidelity=600"
                try:
                    req = urllib.request.Request(
                        url, headers={"User-Agent": "pm-ph-backfill/1.0"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        pts = (json.loads(r.read().decode()).get("history")
                               or [])
                except Exception:  # noqa: BLE001
                    n_err += 1
                    continue
                if pts:
                    f.write(json.dumps(
                        {"condition_id": cid, "event_id": eid,
                         "fetched_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "points": pts}, ensure_ascii=False) + "\n")
                    n_ok += 1
                done.add(t)
        json.dump(sorted(done), open(STATE, "w", encoding="utf-8"))
    print(json.dumps({"tokens_seen": len(tokens), "fetched": n_ok,
                      "errors": n_err, "done_total": len(done)}))


if __name__ == "__main__":
    main()
