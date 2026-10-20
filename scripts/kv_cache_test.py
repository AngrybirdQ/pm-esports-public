#!/usr/bin/env python3
import json, os, sys, time, urllib.parse, urllib.request, urllib.error
W = "https://api-proxy.577813472.workers.dev"
KEY = os.environ.get("PIN_KEY", "")
target = urllib.parse.quote(
    "https://guest.api.arcadia.pinnacle.com/0.1/sports/12/leagues?featured=true", safe="")
out = {}
for i in range(2):
    try:
        req = urllib.request.Request(W + "/?url=" + target,
            headers={"x-key": KEY, "Accept": "application/json"})
        r = urllib.request.urlopen(req, timeout=15)
        out[f"req{i}"] = {"status": r.status,
            "cache": r.headers.get("X-Cache", "MISS"),
            "age": r.headers.get("X-Cache-Age", "-"),
            "body_len": len(r.read())}
    except urllib.error.HTTPError as e:
        out[f"req{i}"] = {"http_error": e.code, "body": e.read()[:80].decode("utf-8","ignore")}
    except Exception as e:
        out[f"req{i}"] = {"err": str(e)[:60]}
    if i == 0:
        time.sleep(3)
print(json.dumps(out))
sys.exit(0)
