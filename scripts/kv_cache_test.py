#!/usr/bin/env python3
"""KV缓存命中测试(Actions侧)"""
import json, os, urllib.request
W = "https://api-proxy.577813472.workers.dev"
KEY = os.environ.get("PIN_KEY", "")
import urllib.parse
target = urllib.parse.quote(
    "https://guest.api.arcadia.pinnacle.com/0.1/sports/12/leagues?featured=true", safe="")
h = {"x-key": KEY, "Accept": "application/json"}
out = {}
for i in range(2):
    req = urllib.request.Request(W + "/?url=" + t if False else W + "/?url=" + target, headers=h)
    r = urllib.request.urlopen(req, timeout=15)
    out[f"req{i}"] = {"status": r.status,
        "cache": r.headers.get("X-Cache", "MISS"),
        "age": r.headers.get("X-Cache-Age", "-"),
        "proxy_status": r.headers.get("x-proxy-status", "-")}
    if i == 0:
        import time; time.sleep(2)
print(json.dumps(out))
