#!/usr/bin/env python3
import json, socket, urllib.request
# 从Actions侧解析api-proxy.577813472.workers.dev的真实IP
host = "api-proxy.577813472.workers.dev"
try:
    ips = socket.getaddrinfo(host, 443, socket.AF_INET)
    ip = ips[0][4][0]
    # 验证可达
    r = urllib.request.urlopen(f"https://{host}/health", timeout=10)
    print(json.dumps({"host": host, "ip": ip, "health_status": r.status}))
except Exception as e:
    print(json.dumps({"err": str(e)[:100]}))
