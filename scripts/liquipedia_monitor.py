#!/usr/bin/env python3
"""Liquipedia 转会监控 — liquipedia_monitor.py (2026-10-07)

抓四款游戏转会门户(api.php?action=parse, 唯一可用通道: 描述UA+--compressed
等价Accept-Encoding), diff出新增转会 → 企微推送 + data/liquipedia/transfers.jsonl
(入库供本地/夜loop消费)。去重状态: state/liq_seen.json (随仓提交)。
cron: 每日 2次 (23 */12 * * * 类) 或 dispatch。"""
import hashlib
import json
import os
import re
import time
import urllib.request

GAMES = {"dota2": "dota2", "lol": "leagueoflegends",
         "cs2": "counterstrike", "valorant": "valorant"}
UA = "pm-esports-research/1.0 (contact: repo owner; automated monitoring)"
STATE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "state", "liq_seen.json")
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "data", "liquipedia")
TAG_RE = re.compile(r"<[^>]+>")


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        import gzip
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def api_text(game, page):
    url = ("https://liquipedia.net/%s/api.php?action=parse&prop=text&page=%s"
           "&format=json&disablelimitreport=1" % (game, page.replace(" ", "_")))
    d = json.loads(fetch(url))
    return d.get("parse", {}).get("text", {}).get("*", "")


def parse_rows(html):
    """每个divRow块 → {date, player, from, to, key}。容忍结构漂移。"""
    out = []
    for block in re.findall(r'<div class="divRow[^"]*"[^>]*>(.*?)</div>\s*</div>',
                            html, re.S):
        txt = TAG_RE.sub("|", block)
        txt = re.sub(r"\|+", "|", txt)
        parts = [p.strip() for p in txt.split("|") if len(p.strip()) > 1]
        if len(parts) < 3:
            continue
        dm = re.search(r"(\w+ \d{1,2},? \d{4}|\d{4}-\d{2}-\d{2})", parts[0])
        date = dm.group(1) if dm else parts[0][:16]
        # 球队链接的title属性才是全名
        titles = re.findall(r'title="([^"]{2,40})"', block)
        teams = [t for t in titles if "Team" in t or "/" not in t][:4]
        player = parts[1] if len(parts) > 1 else ""
        frm = parts[2] if len(parts) > 2 else ""
        to = parts[3] if len(parts) > 3 else (teams[-1] if teams else "")
        key = hashlib.sha1(
            ("|".join([date, player, frm, to])).encode()).hexdigest()[:12]
        out.append({"date": date, "player": player[:40], "from": frm[:32],
                    "to": to[:32], "key": key})
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    state_p = STATE
    try:
        seen = json.load(open(state_p, encoding="utf-8"))
    except Exception:
        seen = {}
    now = time.time()
    seen = {k: v for k, v in seen.items() if now - v < 21 * 86400}
    fresh, allrows = [], []
    for game, wiki in GAMES.items():
        try:
            html = api_text(wiki, "Portal:Transfers")
        except Exception as e:
            print("liquipedia %s failed: %s" % (game, e))
            continue
        for row in parse_rows(html):
            row["game"] = game
            allrows.append(row)
            if row["key"] not in seen:
                seen[row["key"]] = now
                fresh.append(row)
        time.sleep(2)  # Liquipedia API 礼貌间隔(1req/2s)
    json.dump(seen, open(state_p, "w", encoding="utf-8"))
    if allrows:
        with open(os.path.join(OUT_DIR, "transfers.jsonl"), "a",
                  encoding="utf-8") as f:
            ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            for r in allrows:
                f.write(json.dumps({"ts": ts, **r}, ensure_ascii=False) + "\n")
    if fresh:
        webhook = os.environ.get("WECOM_WEBHOOK", "")
        if webhook:
            segs = ["**📋 Liquipedia转会 ×%d** (近12h新变动)" % len(fresh)]
            for r in fresh[:10]:
                segs.append("[%s] **%s**: %s → **%s** (%s)"
                            % (r["game"], r["player"] or "?", r["from"] or "?",
                               r["to"] or "?", r["date"]))
            body = {"msgtype": "markdown",
                    "markdown": {"content": "\n".join(segs)[:3800]}}
            req = urllib.request.Request(webhook, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15)
    print(json.dumps({"rows_today": len(allrows), "fresh": len(fresh)}))


from datetime import datetime  # noqa: E402

if __name__ == "__main__":
    main()
