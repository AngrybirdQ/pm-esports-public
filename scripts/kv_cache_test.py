#!/usr/bin/env python3
import json, os, time, urllib.parse, urllib.request, urllib.error
W = "https://api-proxy.577813472.workers.dev"
KEY = os.environ.get("PIN_KEY", "")
target = urllib.parse.quote(
    "https://guest.api.arcadia.pinnacle.com/0.1/sports/12/leagues?featured=true", safe="")
out = {"runs": []}
for i in range(3):
    rec = {}
    try:
        req = urllib.request.Request(W + "/?url=" + target,
            headers={"x-key": KEY, "Accept": "application/json"})
        r = urllib.request.urlopen(req, timeout=15)
        rec = {"status": r.status, "x-cache": r.headers.get("X-Cache", "none"),
               "x-cache-age": r.headers.get("X-Cache-Age", "-"),
               "proxy-status": r.headers.get("x-proxy-status", "-"),
               "body_len": len(r.read())}
    except urllib.error.HTTPError as e:
        rec = {"http_error": e.code, "body": e.read()[:80].decode("utf-8","ignore")}
    except Exception as e:
        rec = {"err": str(e)[:80]}
    out["runs"].append(rec)
    if i < 2:
        time.sleep(5)
os.makedirs("state", exist_ok=True)
json.dump(out, open("state/kv_test_result.json", "w"), indent=1)
print(json.dumps(out))
