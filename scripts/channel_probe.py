#!/usr/bin/env python3
"""渠道探针(Actions侧) — channel_probe.py (10-10)
验证 Actions runner 对候选赔率渠道的可达性, 结果落 state/channel_probe.json"""
import json, os, time, urllib.request
TARGETS = [
    ("pinnacle_api", "https://api.pinnacle.com/odds/esports?oddsFormat=decimal"),
    ("pinnacle_guest_odds", "https://guest.api.pinnacle.com/odds/esports?oddsFormat=decimal"),
    ("pinnacle_guest_matchups", "https://guest.api.pinnacle.com/matchups/esports"),
    ("theoddsapi", "https://api.the-odds-api.com/v4/sports?apiKey=demo"),
    ("sofascore_esports", "https://api.sofascore.com/api/v1/sport/esports/scheduled-events/2026-10-11"),
    ("bet365_home", "https://www.bet365.com/"),
    ("marathonbet", "https://www.marathonbet.com/en/betting/esports"),
    ("ggbench", "https://ggbench.com/api/matches"),
    ("polymarket_gamma", "https://gamma-api.polymarket.com/events?limit=1"),
    ("kalshi", "https://api.elections.kalshi.com/trade-api/v2/markets?limit=1"),
]
out = []
for name, url in TARGETS:
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/126.0 Safari/537.36",
            "Accept": "application/json, text/html, */*",
            "Accept-Language": "en-US,en;q=0.9"})
        with urllib.request.urlopen(req, timeout=12) as r:
            body = r.read(400)
            out.append({"name": name, "ok": True, "code": r.status,
                        "ms": int((time.time()-t0)*1000),
                        "head": body.decode("utf-8", "ignore")[:160]})
    except Exception as e:
        out.append({"name": name, "ok": False,
                    "ms": int((time.time()-t0)*1000),
                    "err": f"{type(e).__name__}: {str(e)[:70]}"})
os.makedirs("state", exist_ok=True)
json.dump(out, open("state/channel_probe.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps([{k: r.get(k) for k in ("name", "ok", "code", "ms")} for r in out],
                 ensure_ascii=False))
