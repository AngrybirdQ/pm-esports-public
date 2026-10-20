#!/usr/bin/env python3
import json, os, urllib.parse, urllib.request, urllib.error
W = "https://api-proxy.577813472.workers.dev"
KEY = os.environ.get("PIN_KEY", "")
target = urllib.parse.quote(
    "https://guest.api.arcadia.pinnacle.com/0.1/sports/12/leagues?featured=true&kvtest=1", safe="")
out = {"all_headers": []}
for i in range(3):
    try:
        req = urllib.request.Request(W + "/?url=" + target,
            headers={"x-key": KEY, "Accept": "application/json"})
        r = urllib.request.urlopen(req, timeout=15)
        hdrs = {k: v for k, v in r.headers.items()}
        out["all_headers"].append(hdrs)
        r.read()
    except urllib.error.HTTPError as e:
        out["all_headers"].append({"http_error": e.code, "body": e.read()[:100].decode("utf-8","ignore")})
    except Exception as e:
        out["all_headers"].append({"err": str(e)[:80]})
    if i < 2:
        import time; time.sleep(4)
print(json.dumps(out))
