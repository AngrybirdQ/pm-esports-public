#!/usr/bin/env python3
"""pin worker probe — Actions侧测CF Worker生死+真IP+catalog取样"""
import json, os, subprocess, time, urllib.request
W = "https://pinnacle-anchor.577813472.workers.dev"
out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ")}
# 1) DoH真IP
for doh in ("https://dns.google/resolve?name=pinnacle-anchor.577813472.workers.dev&type=A",
            "https://cloudflare-dns.com/dns-query?name=pinnacle-anchor.577813472.workers.dev&type=A"):
    try:
        req = urllib.request.Request(doh, headers={"Accept": "application/dns-json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            ans = json.loads(r.read().decode()).get("Answer") or []
            ips = [a["data"] for a in ans if a.get("type") == 1]
            if ips:
                out["doh_ips"] = ips
                break
    except Exception as e:
        out.setdefault("doh_errs", []).append(str(e)[:60])
# 2) worker根
try:
    req = urllib.request.Request(W + "/", headers={"User-Agent": "probe/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        out["root"] = {"code": r.status, "body": r.read(120).decode("utf-8", "ignore")}
except Exception as e:
    out["root"] = {"err": f"{type(e).__name__}: {e}"[:100]}
# 3) catalog实调(collect同款形态: ?url=<enc> + x-key)
KEY = os.environ.get("PIN_KEY", "")
if KEY:
    import urllib.parse
    target = urllib.parse.quote("https://guest.api.pinnacle.com/matchup/esports", safe="")
    try:
        req = urllib.request.Request(W + "/?url=" + target,
            headers={"x-key": KEY, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read(150).decode("utf-8", "ignore")
            out["catalog"] = {"code": r.status, "head": body[:120]}
    except Exception as e:
        out["catalog"] = {"err": f"{type(e).__name__}: {e}"[:100]}
os.makedirs("state", exist_ok=True)
json.dump(out, open("state/pin_worker_probe.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False))
