#!/usr/bin/env python3
"""Pinnacle guest API电竞赔率 — pinnacle_odds.py (10-10, 世博替代)
matchups(队名/开赛) × odds(价格) 按event id join → data/.../YYYY-MM-DD.pinnacle.jsonl
行: {ts, source:"pinnacle", kind:"market", obs_key:event_id,
     payload:{home, away, start, league, prices:[{name,price}], raw_odds}}
price序: prices数组按pinnacle惯例=[主队,客队](2路), 报告里同时存raw保底可重解。
"""
import json, os, time, urllib.request
BASE = "https://guest.api.pinnacle.com"
HDRS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Accept": "application/json"}
def gj(url, host=None):
    """优先urllib; 失败则DoH解析IP + curl --resolve绕DNS(Actions上pinnacle不解析)"""
    import subprocess
    try:
        req = urllib.request.Request(url, headers=HDRS)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception as e1:
        if host is None:
            host = url.split("/")[2]
        doh = ("https://dns.google/resolve?name=" + host + "&type=A")
        req = urllib.request.Request(doh, headers={"Accept": "application/dns-json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            ans = json.loads(r.read().decode()).get("Answer") or []
        ips = [a["data"] for a in ans if a.get("type") == 1]
        if not ips:
            raise RuntimeError(f"DoH no A record for {host}; first error: {e1}")
        out = subprocess.run(
            ["curl", "-sS", "-m", "20", "--resolve", f"{host}:443:{ips[0]}",
             "-A", HDRS["User-Agent"], "-H", "Accept: application/json", url],
            capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            raise RuntimeError(f"curl --resolve failed: {out.stderr[:120]}")
        return json.loads(out.stdout)
def main():
    reps = []
    errs = []
    try:
        mus = gj(f"{BASE}/matchups/esports", host="guest.api.pinnacle.com")
    except Exception as e:
        errs.append(f"matchups {type(e).__name__}: {e}")
    try:
        odds = gj(f"{BASE}/odds/esports?oddsFormat=decimal", host="guest.api.pinnacle.com")
    except Exception as e:
        errs.append(f"odds {type(e).__name__}: {e}")
    os.makedirs("state", exist_ok=True)
    if errs:
        json.dump({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "errors": errs},
                  open("state/pinnacle_report.json", "w"), ensure_ascii=False)
        print(json.dumps({"errors": errs})); return
    by_id = {}
    for o in odds if isinstance(odds, list) else []:
        if isinstance(o, dict) and o.get("prices"):
            by_id[str(o.get("id"))] = o
    n_events = 0
    for m in mus if isinstance(mus, list) else []:
        if not isinstance(m, dict) or m.get("type") != "matchup":
            continue
        eid = str(m.get("id"))
        od = by_id.get(eid)
        if not od:
            continue
        home = m.get("home") or ""
        away = m.get("away") or ""
        if not home or not away:
            # 防participants形态
            parts = m.get("participants") or []
            names = [p.get("name") for p in parts if isinstance(p, dict)]
            if len(names) >= 2:
                home, away = names[0], names[1]
        prices = [{"cutId": p.get("cutId"), "price": p.get("price")}
                  for p in (od.get("prices") or [])]
        d = time.strftime("%Y/%m")
        os.makedirs(os.path.join("data", d), exist_ok=True)
        fp = os.path.join("data", d, time.strftime("%Y-%m-%d") + ".pinnacle.jsonl")
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "source": "pinnacle", "kind": "market", "obs_key": eid,
               "payload": {"event_id": eid, "home": home, "away": away,
                           "start": m.get("startTime"),
                           "league": (m.get("league") or {}).get("name"),
                           "prices": prices,  # [0]=home, [1]=away(2路惯例)
                           "raw": od}}
        with open(fp, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        n_events += 1
    os.makedirs("state", exist_ok=True)
    json.dump({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "matchups": len(mus) if isinstance(mus, list) else 0,
               "odds_entries": len(by_id), "events_written": n_events},
              open("state/pinnacle_report.json", "w"), ensure_ascii=False)
    print(json.dumps({"matchups": len(mus) if isinstance(mus, list) else 0,
                      "odds": len(by_id), "written": n_events}))
if __name__ == "__main__":
    main()
