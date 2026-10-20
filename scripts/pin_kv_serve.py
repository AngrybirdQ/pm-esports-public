#!/usr/bin/env python3
"""pin_kv_serve.py — 8976端口pin数据只读服务(10-20)
trycloudflared隧道指向本服务: 外网(含Actions)可读KV里的Pinnacle行,
绕过workers.dev 403/DNS污染。仅暴露非敏感赔率数据, 5s本地缓存
压住KV REST频率。端点: /health /pin/meta /pin/latest?leagues=6
"""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"

ACCOUNT = "555d6807c57df633162e675a4ab45a92"
NAMESPACE = "227da8d940394fb3b5ddfeb6e23bfd17"
CF_API = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}" \
         f"/storage/kv/namespaces/{NAMESPACE}"
TOKEN_FILE = os.path.expanduser("~/.config/polymarket-sync/cf_token")
CACHE = {"t": 0.0, "data": None, "meta": None}
CACHE_TTL = 5.0


def cf_get(path):
    token = open(TOKEN_FILE).read().strip()
    req = urllib.request.Request(CF_API + path,
                                 headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def latest_rows():
    now = time.monotonic()
    if CACHE["data"] is not None and now - CACHE["t"] < CACHE_TTL:
        return CACHE["data"], CACHE["meta"]
    keys = cf_get("/keys?prefix=" + urllib.parse.quote("pin:rows:", safe=""))
    names = sorted(k["name"] for k in keys.get("result", []))
    data, meta = [], {}
    if names:
        val = cf_get("/values/" + urllib.parse.quote(names[-1], safe=""))
        data = val.get("rows", [])
        meta = {"hour_key": names[-1], "ts": val.get("ts")}
    try:
        m = cf_get("/values/" + urllib.parse.quote("pin:meta", safe=""))
        meta.update({k: m[k] for k in ("leagues", "matchups", "rows", "errors")
                     if k in m})
    except Exception:
        pass
    CACHE.update(t=now, data=data, meta=meta)
    return data, meta


class H(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/health":
                self._send({"ok": True, "ts": int(time.time() * 1000),
                            "svc": "pin-kv-serve/1.0"})
                return
            if path == "/pin/meta":
                _, meta = latest_rows()
                self._send(meta)
                return
            if path == "/pin/latest":
                data, meta = latest_rows()
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                limit = min(int(q.get("limit", ["200"])[0]), 500)
                self._send({"meta": meta,
                            "served_at": datetime.now(timezone.utc).isoformat(),
                            "rows": data[:limit]})
                return
            self._send({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            self._send({"error": f"{type(e).__name__}: {e}"}, 502)

    def log_message(self, fmt, *args):
        print(datetime.now(timezone.utc).isoformat(), fmt % args, flush=True)


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", 8976), H)
    print(datetime.now(timezone.utc).isoformat() + " pin-kv-serve on :8976",
          flush=True)
    srv.serve_forever()
