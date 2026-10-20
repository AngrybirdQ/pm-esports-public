#!/usr/bin/env python3
"""DEX Swap topic Actions侧可达性测试"""
import json, os, urllib.request
RPC = "https://ethereum-rpc.publicnode.com"
SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
def rpc(method, params):
    body = json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    req = urllib.request.Request(RPC, data=body,
        headers={"Content-Type":"application/json","User-Agent":"dex-test/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())
head = int(rpc("eth_blockNumber", [])["result"], 16)
# 测100块范围的Swap topic
out = {}
for blocks, label in [(50, "50块"), (500, "500块")]:
    logs = rpc("eth_getLogs", [{
        "fromBlock": hex(head-blocks), "toBlock": hex(head),
        "topics": [SWAP]}])
    r = logs.get("result")
    out[label] = len(r) if isinstance(r, list) else str(r)[:60]
os.makedirs("state", exist_ok=True)
json.dump(out, open("state/dex_swap_probe.json", "w"))
print(json.dumps(out))
