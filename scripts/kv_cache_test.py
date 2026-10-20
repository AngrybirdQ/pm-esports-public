#!/usr/bin/env python3
import json, os, time, urllib.parse, urllib.request
W = "https://api-proxy.577813472.workers.dev"
KEY = os.environ.get("PIN_KEY", "")
target = urllib.parse.quote(
    "https://guest.api.arcadia.pinnacle.com/0.1/sports/12/leagues?featured=true", safe="")
h = {"x-key": KEY, "Accept": "application/json"}
out = {}
for i in range(2):
    req = urllib.request.Request(W + "/?url=" + target, headers=h)
    r = urllib.request.urlopen(req, timeout=15)
    out[f"req{i}"] = {"status": r.status,
        "cache": r.headers.get("X-Cache", "MISS"),
        "age": r.headers.get("X-Cache-Age", "-")}
    if i == 0:
        time.sleep(3)
print(json.dumps(out))
