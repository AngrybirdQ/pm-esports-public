#!/usr/bin/env python3
"""api-proxy worker Actions侧健康验证"""
import json, os, urllib.request
W = "https://api-proxy.577813472.workers.dev"
KEY = os.environ.get("PIN_KEY", "")
import urllib.parse
out = {"worker": W}
# health
try:
    r = urllib.request.urlopen(urllib.request.Request(W + "/health",
        headers={"User-Agent": "probe"}), timeout=10)
    out["health"] = json.loads(r.read().decode())
except Exception as e:
    out["health"] = str(e)[:60]
# pinnacle proxy
if KEY:
    t = urllib.parse.quote(
        "https://guest.api.arcadia.pinnacle.com/0.1/sports/12/leagues?featured=true", safe="")
    try:
        req = urllib.request.Request(W + "/?url=" + t,
            headers={"x-key": KEY, "Accept": "application/json"})
        r = urllib.request.urlopen(req, timeout=15)
        d = json.loads(r.read().decode())
        out["pinnacle"] = {"status": r.status, "leagues": len(d)}
    except Exception as e:
        out["pinnacle"] = str(e)[:60]
# oddsapi proxy
try:
    t = urllib.parse.quote(
        "https://api.the-odds-api.com/v4/sports/?apiKey=7ce9c5c38336a94d62ea41b53d40151d", safe="")
    req = urllib.request.Request(W + "/?url=" + t, headers={"x-key": KEY})
    r = urllib.request.urlopen(req, timeout=15)
    out["oddsapi"] = {"status": r.status, "body_len": len(r.read())}
except Exception as e:
    out["oddsapi"] = str(e)[:60]
os.makedirs("state", exist_ok=True)
json.dump(out, open("state/api_proxy_probe.json", "w"), indent=1)
print(json.dumps(out))
