#!/usr/bin/env python3
"""Side-source collector: second oracle prices & cross-check sources that
complement Polymarket. Best-effort by design — every source attempt lands in
the run report, failures never fail the run (same philosophy as collect.py's
multi-variant discovery).

Sources (2026-10-03 v1):
  kalshi      US regulated prediction market, public read API, no auth.
              Esports coverage is thin-to-none; when present it is a
              genuinely independent second oracle for the same matches.
              VERIFIED ALIVE: 9-28 esports hits per run.
  liquipedia  DEAD (2026-10-03): the Matches hub pages are now Lua-widget
              rendered — action=parse wikitext returns ~250B of widget
              scaffolding, zero match data. Kept below for the day a
              stable alternative endpoint shows up.
  pinnacle    DEAD from datacenter IPs (2026-10-03): guest endpoints 403
              on every variant from Actions runners. Would revive on a
              residential-IP self-hosted runner.

Writes data/YYYY/MM/YYYY-MM-DD.side.jsonl, one line per observation:
  {ts, source, kind, obs_key, payload}
Local pull_and_ingest lands these in pm_side_observations (JSONB wide table)
so new sources need zero schema migration.
Stdlib only.
"""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HTTP_TIMEOUT = 25
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ESPORTS_KEYWORDS = (
    "esport", "dota", "counter-strike", "counter strike", "cs2", "csgo",
    "valorant", "league of legends", "lol ", "lol:", "starcraft",
    "rocket league", "overwatch", "the international",
)

KALSHI_VARIANTS = [
    "https://api.elections.kalshi.com/trade-api/v2",
    "https://demo-api.kalshi.co/trade-api/v2",
]
LIQUIPEDIA_GAMES = ["dota2", "counterstrike", "leagueoflegends", "valorant"]
PINNACLE_VARIANTS = [
    "https://guest.api.arcadia.pinnacle.com/0.1",
    "https://api.pinnacle.com/0.1",
]
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_json(url, headers=None, attempts=2):
    last_err = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": BROWSER_UA,
                "Accept": "application/json",
                # Liquipedia API terms: gzip is REQUIRED
                "Accept-Encoding": "gzip",
                **(headers or {}),
            })
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                raw = r.read()
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8")), None
        except Exception as e:  # noqa: BLE001 - report everything
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(2 * (i + 1))
    return None, last_err


def http_text(url, headers=None, attempts=2):
    last_err = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": BROWSER_UA,
                **(headers or {}),
            })
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                return r.read().decode("utf-8", "replace"), None
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(2 * (i + 1))
    return None, last_err


def looks_esports(text):
    t = (text or "").lower()
    return any(k in t for k in ESPORTS_KEYWORDS)


def collect_kalshi(report):
    """All open markets, filter esports-related by title/ticker."""
    base = None
    for v in KALSHI_VARIANTS:
        data, err = http_json(f"{v}/markets?status=open&limit=1000")
        if err is None and isinstance(data, dict):
            base = v
            break
    if base is None:
        report["side_sources"].append(
            {"source": "kalshi", "ok": False, "err": "all variants failed"})
        return []
    obs = []
    cursor, pages, n_all = None, 0, 0
    while pages < 5:
        url = f"{base}/markets?status=open&limit=1000"
        if cursor:
            url += "&cursor=" + urllib.parse.quote(cursor)
        data, err = http_json(url)
        if err is not None or not isinstance(data, dict):
            break
        markets = data.get("markets") or []
        n_all += len(markets)
        for m in markets:
            label = " ".join(filter(None, [
                m.get("title"), m.get("subtitle"), m.get("ticker"),
                (m.get("yes_sub_title") or "")]))
            if not looks_esports(label):
                continue
            obs.append({
                "ts": now_iso(), "source": "kalshi",
                "kind": "market",
                "obs_key": str(m.get("id") or m.get("ticker") or ""),
                "payload": {
                    "id": m.get("id"), "ticker": m.get("ticker"),
                    "title": m.get("title"), "subtitle": m.get("subtitle"),
                    "yes_bid": m.get("yes_bid"), "yes_ask": m.get("yes_ask"),
                    "no_bid": m.get("no_bid"), "no_ask": m.get("no_ask"),
                    "last_price": m.get("last_price"),
                    "volume": m.get("volume"),
                    "open_interest": m.get("open_interest"),
                    "close_time": m.get("close_time"),
                    "rfq_enabled": m.get("rfq_enabled"),
                },
            })
        cursor = data.get("cursor")
        pages += 1
        if not cursor:
            break
        time.sleep(0.2)
    report["side_sources"].append({
        "source": "kalshi", "ok": True, "variant": base,
        "markets_scanned": n_all, "esports_hits": len(obs)})
    obs += collect_kalshi_orderbook(base, obs, report)
    return obs


def collect_kalshi_orderbook(base, market_obs, report):
    """v2(A4): 对电竞tickers逐个拉完整order book深度(top3档)。
    数据已在采, 只差解析——深度=可下注容量与滑点的直接度量。"""
    tickers = [o["payload"].get("ticker") for o in market_obs
               if o.get("payload", {}).get("ticker")]
    books, fails = [], 0
    for tk in tickers[:10]:
        data, err = http_json(f"{base}/orderbook/{tk}")
        if err is not None or not isinstance(data, dict):
            fails += 1
            continue
        ob = data.get("orderbook") or {}
        def top3(side):
            levels = ob.get(side) or []
            out = []
            for lv in levels[:3]:
                try:
                    price = float(lv[0]) if isinstance(lv, list) else float(
                        lv.get("price", 0))
                    qty = float(lv[1]) if isinstance(lv, list) else float(
                        lv.get("quantity", 0))
                    out.append([price / 100.0, qty])  # cents → 概率
                except (TypeError, ValueError, IndexError):
                    continue
            return out
        books.append({
            "ts": now_iso(), "source": "kalshi", "kind": "kalshi_orderbook",
            "obs_key": tk,
            "payload": {"ticker": tk, "yes_top3": top3("yes"),
                        "no_top3": top3("no")},
        })
        time.sleep(0.15)
    report["side_sources"].append({
        "source": "kalshi_orderbook", "ok": True,
        "books": len(books), "fails": fails})
    return books


def collect_liquipedia(report):
    """Raw wikitext snapshot of each game's matches hub. Parse locally —
    never in the pipe. Liquipedia API etiquette: descriptive UA."""
    obs = []
    ua = ("pm-esports-research/1.0 (public-market data cross-check; "
          "github.com/AngrybirdQ/pm-esports-public)")
    for game in LIQUIPEDIA_GAMES:
        url = (f"https://liquipedia.net/{game}/api.php?action=parse"
               f"&page=Liquipedia:Matches&prop=wikitext&format=json"
               f"&formatversion=2")
        data, err = http_json(url, headers={"User-Agent": ua})
        err = err if err is None else err
        wikitext = None
        if err is None and isinstance(data, dict):
            parse = data.get("parse") or {}
            wikitext = parse.get("wikitext") if isinstance(parse, dict) else None
            if wikitext is None:
                err = "no wikitext (page moved?)"
        if wikitext is None:
            report["side_sources"].append(
                {"source": "liquipedia", "ok": False, "game": game,
                 "err": err or "empty"})
            continue
        obs.append({
            "ts": now_iso(), "source": "liquipedia",
            "kind": f"{game}_matches_wikitext",
            "obs_key": game,
            "payload": {"wikitext": wikitext[:400000]},
        })
        time.sleep(1.0)  # wiki etiquette: slow down between games
    if obs:
        report["side_sources"].append(
            {"source": "liquipedia", "ok": True, "games": len(obs)})
    return obs


def collect_pinnacle(report):
    """Guest endpoints: league list + e-sports matchups, best-effort.
    Datacenter 403s are expected; whatever sticks is a bonus oracle."""
    obs = []
    base = None
    for v in PINNACLE_VARIANTS:
        data, err = http_json(f"{v}/sports?type=upcoming")
        if err is None and isinstance(data, dict):
            base = v
            break
    if base is None:
        report["side_sources"].append(
            {"source": "pinnacle", "ok": False, "err": "all variants failed"})
        return obs
    sports = data.get("sports") or []
    es_ids = [s.get("id") for s in sports
              if looks_esports(s.get("name") or "")]
    report["side_sources"].append({
        "source": "pinnacle", "ok": True, "variant": base,
        "esports_sports": len(es_ids)})
    for sid in es_ids[:8]:
        data, err = http_json(f"{base}/leagues/all?sportId={sid}")
        if err is None and isinstance(data, dict):
            leagues = data.get("leagues") or []
            if leagues:
                obs.append({
                    "ts": now_iso(), "source": "pinnacle",
                    "kind": f"leagues_sport_{sid}",
                    "obs_key": str(sid),
                    "payload": {"sport_id": sid,
                                "leagues": leagues[:200]},
                })
        time.sleep(0.3)
    return obs


def collect_azuro(report):
    """实验性: Azuro链上盘公共GraphQL探针(未实证, 默认PM_AZURO=1才激活)。
    拿到什么存什么 — 观察几轮报告后再决定是否解析。"""
    query = '{"sports(first:50){id name} games(first:50,orderBy:startsAt){id leagueId startsAt} }'
    data, err = http_json(
        "https://api.azuro.org/graphql?query=" + urllib.parse.quote(query))
    ok = err is None and isinstance(data, dict)
    report["side_sources"].append({
        "source": "azuro", "ok": ok, "err": err,
        "keys": list((data or {}).keys())[:6] if ok else None})
    if not ok:
        return []
    return [{"ts": now_iso(), "source": "azuro", "kind": "probe",
             "obs_key": "probe", "payload": {k: data.get(k)
                                             for k in list(data)[:6]}}]


def main():
    report = {"ts": now_iso(), "side_sources": []}
    obs = []
    try:
        obs += collect_kalshi(report)
    except Exception as e:  # noqa: BLE001 - one dead source != dead run
        report["side_sources"].append(
            {"source": "kalshi", "ok": False, "err": f"{type(e).__name__}: {e}"})
    if os.environ.get("PM_AZURO") == "1":
        try:
            obs += collect_azuro(report)
        except Exception as e:  # noqa: BLE001
            report["side_sources"].append(
                {"source": "azuro", "ok": False,
                 "err": f"{type(e).__name__}: {e}"})
    # dead sources stay visible in the report (skipped, not silent)
    for src in ("liquipedia", "pinnacle"):
        report["side_sources"].append(
            {"source": src, "ok": False, "skipped": "dead source, see docstring"})

    d = datetime.now(timezone.utc)
    day_dir = os.path.join(ROOT, "data", d.strftime("%Y"), d.strftime("%m"))
    os.makedirs(day_dir, exist_ok=True)
    if obs:
        with open(os.path.join(day_dir, d.strftime("%Y-%m-%d") + ".side.jsonl"),
                  "a", encoding="utf-8") as f:
            for o in obs:
                f.write(json.dumps(o, ensure_ascii=False) + "\n")
    report["observations"] = len(obs)
    side_path = os.path.join(ROOT, "state", "last_side_report.json")
    os.makedirs(os.path.dirname(side_path), exist_ok=True)
    with open(side_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, default=str)
    print(json.dumps({"ts": report["ts"], "observations": len(obs),
                      "sources": [s.get("source") for s in
                                  report["side_sources"]]}))
    raise SystemExit(0)


if __name__ == "__main__":
    main()
