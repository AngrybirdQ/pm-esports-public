#!/usr/bin/env python3
"""世博站Actions可达性探针 — shibo_probe.py (10-11)
云端访问世博通道页: ①HTTPS通否 ②302跳转带token否 → state/shibo_probe.json"""
import json, os, time, urllib.request
TARGET = "https://dtpsg1awsapip01.3u9akgp.com"
out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "tests": []}
class NoRedir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None
op = urllib.request.build_opener(NoRedir)
# 1) 首跳(302→token页?)
try:
    req = urllib.request.Request(TARGET, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with op.open(req, timeout=20) as r:
            out["tests"].append({"step": "first", "code": r.status})
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
        out["tests"].append({"step": "first", "code": e.code, "location": loc[:120],
                             "has_token": "token=" in loc})
except Exception as e:
    out["tests"].append({"step": "first", "err": f"{type(e).__name__}: {e}"[:100]})
# 2) API端点直访(带旧token测数据接口)
try:
    tok = "204143149993004724"
    req = urllib.request.Request(
        f"{TARGET}/game/index?game_id=0&flag=1&day=0&page_size=1&page=1",
        headers={"token": tok, "device": "2", "lang": "cn", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read(200).decode("utf-8", "ignore")
        out["tests"].append({"step": "api", "code": r.status, "body": body[:120]})
except Exception as e:
    out["tests"].append({"step": "api", "err": f"{type(e).__name__}: {e}"[:100]})
os.makedirs("state", exist_ok=True)
json.dump(out, open("state/shibo_probe.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False))
