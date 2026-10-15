#!/usr/bin/env python3
"""api-proxy worker健康验证(Actions侧)"""
import json, os, urllib.request
W = os.environ.get("API_PROXY_URL", "https://api-proxy.577813472.workers.dev")
KEY = os.environ.get("PIN_KEY", "")
out = {"worker": W}
# 1) health
try:
    r = urllib.request.urlopen(urllib.request.Request(W + "/health",
        headers={"User-Agent": "probe"}), timeout=10)
    out["health"] = json.loads(r.read().decode())
except Exception as e:
    out["health"] = str(e)[:80]
# 2) Pinnacle代理
if KEY:
    import urllib.parse
    target = urllib.parse.quote(
        "https://guest.api.arcadia.pinnacle.com/0.1/sports/12/leagues?featured=true", safe="")
    try:
        req = urllib.request.Request(W + "/?url=" + target,
            headers={"x-key": KEY, "Accept": "application/json"})
        r = urllib.request.urlopen(req, timeout=15)
        d = json.loads(r.read().decode())
        out["pinnacle"] = {"code": r.status, "leagues": len(d) if isinstance(d, list) else "?"}
    except Exception as e:
        out["pinnacle"] = str(e)[:80]
# 3) oddsapi代理
try:
    import urllib.parse
    target = urllib.parse.quote(
        "https://api.the-odds-api.com/v4/sports/?apiKey=demo", safe="")
    req = urllib.request.Request(W + "/?url=" + target,
        headers={"x-key": KEY})
    r = urllib.request.urlopen(req, timeout=15)
    out["oddsapi"] = {"code": r.status}
except Exception as e:
    out["oddsapi"] = str(e)[:80]
os.makedirs("state", exist_ok=True)
json.dump(out, open("state/api_proxy_probe.json", "w"), indent=1)
print(json.dumps(out))
