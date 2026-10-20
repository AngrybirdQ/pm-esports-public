#!/usr/bin/env python3
"""pin_kv_rest.py — Pinnacle KV REST直读通道(10-20)
链路: CF api-proxy worker cron(*/5)在CF内部走完 leagues→matchups→markets
全链 → KV(pin:rows:<UTC小时>, 与pin_cloud_sweep.py同契约行)。
本地经 api.cloudflare.com REST(实测可达, 不受workers.dev 403/DNS污染影响)
直读 → 复用pull_and_ingest.pinnacle_rows_from_file → pm_book_odds。
即: Pinnacle锚价不再依赖Actions runner可用性, 全程CF+本地。
幂等: line_hash含行ts, 每tick=新快照(快照表语义, 与Actions sweep一致);
state记录last_ts避免同值重复处理。
"""
import fcntl
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"

sys.path.insert(0, "/home/Admin/polymarket-sync")
from pull_and_ingest import (BOOK_ODDS_INSERT, DSN,  # noqa: E402
                             pinnacle_rows_from_file)

ACCOUNT = "555d6807c57df633162e675a4ab45a92"
NAMESPACE = "227da8d940394fb3b5ddfeb6e23bfd17"
CF_API = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}" \
         f"/storage/kv/namespaces/{NAMESPACE}"
CONFIG_DIR = os.path.expanduser("~/.config/polymarket-sync")
TOKEN_FILE = os.path.join(CONFIG_DIR, "cf_token")
STATE_FILE = os.path.join(CONFIG_DIR, "pin_kv_state.json")
LOCK_FILE = "/tmp/pin_kv_rest.lock"
OUT_DIR = "/home/Admin/polymarket-sync/data/pinnacle"


def cf_get(path, token, binary=False):
    req = urllib.request.Request(CF_API + path,
                                 headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return raw if binary else json.loads(raw.decode())


def main():
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(datetime.now(timezone.utc).isoformat() + " SKIP: lock held")
        return
    token = open(TOKEN_FILE).read().strip()
    state = {}
    if os.path.exists(STATE_FILE):
        state = json.load(open(STATE_FILE))
    last_ts = state.get("last_ts", 0)

    keys = cf_get("/keys?prefix=" + urllib.parse.quote("pin:rows:", safe=""),
                  token).get("result", [])
    hour_keys = sorted(k["name"] for k in keys)[-2:]  # 最新2个小时桶, 防边界漏
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(DSN)
    n_rows = n_new = 0
    try:
        with conn, conn.cursor() as cur:
            for hk in hour_keys:
                val = json.loads(cf_get(
                    "/values/" + urllib.parse.quote(hk, safe=""), token,
                    binary=True).decode())
                if val.get("ts", 0) <= last_ts:
                    continue
                rows_jsonl = "\n".join(json.dumps(r, ensure_ascii=False)
                                       for r in val.get("rows", []))
                if not rows_jsonl:
                    continue
                rows, bad = pinnacle_rows_from_file(rows_jsonl)
                n_rows += len(rows)
                for i in range(0, len(rows), 1000):
                    psycopg2.extras.execute_values(
                        cur, BOOK_ODDS_INSERT, rows[i:i + 1000], page_size=1000)
                # 审计轨迹: 本地文件同Actions布局, 可重放
                os.makedirs(OUT_DIR, exist_ok=True)
                fp = os.path.join(OUT_DIR,
                                  hk.replace("pin:rows:", "") + ".pinnacle.jsonl")
                with open(fp, "a", encoding="utf-8") as f:
                    f.write(rows_jsonl + "\n")
                last_ts = max(last_ts, val.get("ts", 0))
        state["last_ts"] = last_ts
        state["updated"] = datetime.now(timezone.utc).isoformat()
        json.dump(state, open(STATE_FILE, "w"))
    finally:
        conn.close()
    print(datetime.now(timezone.utc).isoformat()
          + f" hours={len(hour_keys)} rows={n_rows}")


if __name__ == "__main__":
    main()
