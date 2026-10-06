#!/usr/bin/env python3
"""pm历史全量档案扩建 — archive_sweep.py (2026-10-07)

枚举 Gamma 全部电竞事件(active+closed分页), 逐事件取markets +
ML token的prices-history → data/archive/events.jsonl。游标
state/archive_cursor.json 断点续扫; 每轮硬预算(BUDGET_S)适配cron。
数据=公开市场数据, 公共仓合规。"""
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request

BUDGET_S = int(os.environ.get("ARCHIVE_BUDGET_S", "1500"))
GAMES = ("esports", "dota-2", "cs2", "league-of-legends", "valorant")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CURSOR = os.path.join(ROOT, "state", "archive_cursor.json")
OUT = os.path.join(ROOT, "data", "archive", "events.jsonl")
UA = "pm-esports-research/1.0 (automated archive sweep)"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    try:
        cur = json.load(open(CURSOR, encoding="utf-8"))
    except Exception:
        cur = {"closed_done": False, "closed_page": 1, "active_done": False,
               "active_page": 1}
    t0 = time.time()
    written = 0
    events_scanned = 0
    for mode in ("closed", "active"):
        if cur.get(mode + "_done"):
            continue
        page = cur.get(mode + "_page", 1)
        while time.time() - t0 < BUDGET_S:
            qs = ("active=true&closed=%s" % ("true" if mode == "closed" else "false"))
            data = get("https://gamma-api.polymarket.com/events?%s&limit=100"
                       "&offset=%d&order=id&ascending=true" % (qs, (page - 1) * 100))
            evs = data if isinstance(data, list) else []
            if not evs:
                cur[mode + "_done"] = True
                break
            for ev in evs:
                events_scanned += 1
                title = ev.get("title") or ""
                if " vs " not in title and not any(
                        k in title.lower() for k in ("winner", "champion")):
                    continue
                markets = ev.get("markets") or []
                rec = {"event_id": str(ev.get("id")), "title": title,
                       "slug": ev.get("slug"),
                       "start": ev.get("startDate"), "end": ev.get("endDate"),
                       "closed": ev.get("closed"),
                       "markets": [{"q": m.get("question"),
                                    "cid": m.get("conditionId"),
                                    "tokens": json.loads(m.get("clobTokenIds") or "[]")
                                    if m.get("clobTokenIds") else []}
                                   for m in markets[:12]]}
                with open(OUT, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1
                # 逐事件历史留待二阶段(游标推进后按budget逐token拉)
            if len(evs) < 100:
                cur[mode + "_done"] = True
                break
            page += 1
            cur[mode + "_page"] = page
            json.dump(cur, open(CURSOR, "w", encoding="utf-8"))
        json.dump(cur, open(CURSOR, "w", encoding="utf-8"))
    print(json.dumps({"written": written, "scanned": events_scanned,
                      "cursor": {k: v for k, v in cur.items()
                                 if not str(k).endswith("page") or True}}))


if __name__ == "__main__":
    main()
