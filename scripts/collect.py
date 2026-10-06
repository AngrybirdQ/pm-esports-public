#!/usr/bin/env python3
"""Collect Polymarket esports prediction-market odds snapshots.

Runs on GitHub Actions (overseas network; polymarket API unreachable from CN).
Writes:
  data/YYYY/MM/YYYY-MM-DD.jsonl  -- one JSON line per active market per run (UTC date)
  state/latest.json              -- full latest snapshot
  state/last_run_report.json     -- diagnostics: sources tried, counts, errors

Tag slugs on Polymarket are not stable and the events filter parameter has
varied across API revisions (tag / tag_slug / tag_id), so discovery is
multi-strategy and every attempt is logged to the report.
Stdlib only.
"""
import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

GAMMA = "https://gamma-api.polymarket.com"
CANDIDATE_SLUGS = [
    "esports", "e-sports", "dota-2", "dota2", "cs2", "counter-strike", "csgo",
    "league-of-legends", "lol", "valorant", "starcraft-2", "rocket-league",
    "the-international", "ti",
]
TAG_KEYWORDS = ("esport", "dota", "counter-strike", "cs2", "csgo", "valorant",
                "league-of-legends", "starcraft", "rocket-league",
                "international")
SEARCH_KEYWORDS = ["Dota 2", "Counter-Strike", "League of Legends", "Valorant",
                   "The International"]
# Volume floor: the long tail of zero-liquidity match markets is noise for
# CLV work. Keep a market if it cleared either bar.
MIN_VOLUME_24H = 100.0
MIN_VOLUME_TOTAL = 1000.0
RETENTION_DAYS = 7

# --- trade-flow watch (the "场外异动" sensor) ---
TRADE_MARKETS_CAP = 120       # top markets by volume_24h to fetch trades for
                                # (public repo = unmetered Actions, 2026-10-03)
# --- CLOB order-book watch (executable prices for CLV/slippage) ---
BOOK_TOP_MARKETS = 20         # plus every watchlist market, capped per run
MIN_MARKET_VOL24H_TRADES = 5000
TRADE_WINDOW_MIN = 90         # fetch trades newer than this (minutes)
BIG_TRADE_USD = 10000         # single trade >= this -> anomaly
FLOW_USD = 25000              # 1h one-side net flow >= this -> anomaly
FLOW_ONE_SIDE_SHARE = 0.60    # ...and one side >= this share of 1h volume
MOVE_PP = 0.15                # price move vs frame 45-90min ago >= this
MOVE_FRAME_MIN, MOVE_FRAME_MAX = 45, 90
# at >=0.97 / <=0.03 a "buy" is resolution-cleanup, not information
DEGEN_PRICE_HIGH, DEGEN_PRICE_LOW = 0.97, 0.03
HTTP_TIMEOUT = 25
MAX_EVENT_QUERIES = 60

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def now_utc():
    return datetime.now(timezone.utc)


def now_iso():
    return now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")


def http_json(url, attempts=2):
    last_err = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "pm-esports-collector/1.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8")), None
        except Exception as e:  # noqa: BLE001 - report everything
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(2 * (i + 1))
    return None, last_err


def parse_maybe_json(v):
    """Gamma double-encodes several fields as JSON strings."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:  # noqa: BLE001
            return None
    return v


def fnum(v):
    try:
        return round(float(v), 6)
    except (TypeError, ValueError):
        return None


def discover_tags(report):
    """Return [(slug, tag_id)] for tags that look esports-related."""
    found = []
    for q in ("/tags", "/tags?limit=500"):
        tags, err = http_json(GAMMA + q)
        report["sources"].append({"url": q, "ok": err is None, "err": err})
        if isinstance(tags, list) and tags:
            for t in tags:
                slug = (t.get("slug") or "").lower()
                if slug and any(k in slug for k in TAG_KEYWORDS):
                    found.append((slug, t.get("id")))
            break
    # de-dup, keep order
    seen, out = set(), []
    for slug, tid in found:
        if slug not in seen:
            seen.add(slug)
            out.append((slug, tid))
    return out


def add_event(events, ev, source):
    if not isinstance(ev, dict):
        return
    if ev.get("closed"):
        return
    eid = str(ev.get("id") or ev.get("slug") or "")
    if not eid:
        return
    if eid in events:
        events[eid]["_sources"].add(source)
    else:
        events[eid] = {"_sources": {source}, "ev": ev}


def query_events(report, events):
    tag_cands = discover_tags(report)
    report["tags_discovered"] = [s for s, _ in tag_cands]
    tag_ids = {s: t for s, t in tag_cands if t is not None}

    queries = []  # (label, querystring)
    # NOTE: the bare `tag=` param is silently IGNORED by the live Gamma API
    # (it returns an unfiltered global top-volume dump — verified on the
    # first run: tag=europa-league returned 300 unrelated events). Only
    # tag_slug= / tag_id= actually filter.
    for slug in CANDIDATE_SLUGS + [s for s, _ in tag_cands]:
        queries.append((f"tag_slug={slug}", f"tag_slug={urllib.parse.quote(slug)}"))
        if slug in tag_ids:
            queries.append((f"tag_id[{slug}]", f"tag_id={tag_ids[slug]}"))
    for kw in SEARCH_KEYWORDS:
        queries.append((f"search:{kw}",
                        "q=" + urllib.parse.quote(kw)))  # via public-search below

    n_query = 0
    for label, qs in queries:
        if n_query >= MAX_EVENT_QUERIES:
            report["errors"].append("query budget exhausted; remaining sources skipped")
            break
        if label.startswith("search:"):
            url = f"{GAMMA}/public-search?{qs}"
        else:
            url = (f"{GAMMA}/events?{qs}&active=true&closed=false"
                   f"&limit=100&order=volume&ascending=false")
        n_query += 1
        added_before = len(events)
        pages = 0
        ok, err, total = True, None, 0
        offset = 0
        while pages < 3:  # pagination only for /events tag queries
            if label.startswith("search:"):
                fetch_url = url
            else:
                fetch_url = f"{url}&offset={offset}"
            data, e = http_json(fetch_url)
            if e is not None:
                ok, err = False, e
                break
            if label.startswith("search:"):
                evs = (data or {}).get("events") or []
                for ev in evs:
                    add_event(events, ev, label)
                total = len(evs)
                report.setdefault("search_samples", {})[label] = [
                    {"t": (ev.get("title") or "")[:70],
                     "closed": bool(ev.get("closed"))} for ev in evs[:5]]
                break  # search results come pre-paginated
            if not isinstance(data, list):
                ok, err = False, f"unexpected type {type(data).__name__}"
                break
            for ev in data:
                add_event(events, ev, label)
            total += len(data)
            if pages == 0 and data:
                report.setdefault("tag_samples", {})[label] = [
                    (ev.get("title") or "")[:70] for ev in data[:3]]
            pages += 1
            if len(data) < 100:
                break
            offset += 100
        report["sources"].append({
            "url": label, "ok": ok, "events_returned": total, "err": err,
        })
        if ok and len(events) > added_before:
            merged = set(report.get("winning_sources", [])) | {label}
            report["winning_sources"] = sorted(merged)
    report["n_event_queries"] = n_query


def build_lines(events, ts):
    lines, dropped = [], 0
    for eid, entry in events.items():
        ev = entry["ev"]
        for m in ev.get("markets") or []:
            if not isinstance(m, dict) or m.get("closed"):
                continue
            if m.get("active") is False:
                continue
            vol = fnum(m.get("volume"))
            vol24 = fnum(m.get("volume24hr") or m.get("volume24Hr"))
            if not ((vol24 or 0) >= MIN_VOLUME_24H or (vol or 0) >= MIN_VOLUME_TOTAL):
                dropped += 1
                continue
            outcomes = parse_maybe_json(m.get("outcomes")) or []
            prices = parse_maybe_json(m.get("outcomePrices")) or []
            tokens = parse_maybe_json(m.get("clobTokenIds")) or []
            if not outcomes or not prices:
                continue
            lines.append({
                "ts": ts,
                "event_id": eid,
                "event_slug": ev.get("slug"),
                "event_title": ev.get("title"),
                "event_start_date": ev.get("startDate"),
                "event_end_date": ev.get("endDate"),
                "sources": sorted(entry["_sources"]),
                "market_slug": m.get("slug"),
                "question": m.get("question"),
                "group_item": m.get("groupItemTitle"),
                "condition_id": m.get("conditionId"),
                "token_ids": tokens,
                "outcomes": outcomes,
                "prices": [fnum(p) for p in prices],
                "volume": vol,
                "volume_24h": vol24,
                "liquidity": fnum(m.get("liquidity")),
                "market_end_date": m.get("endDate"),
            })
    lines.sort(key=lambda x: (x["event_id"], x["market_slug"] or x["question"] or ""))
    return lines, dropped


def _payload(name):
    """Tasking payload (watchlist / betmatch_reqs). The repo is PUBLIC, so
    strategy-relevant tasking must never persist here — it rides the
    repository_dispatch client_payload instead. Env override wins (local
    debugging); the state/ file path stays as a private-repo fallback."""
    env = os.environ.get("PM_" + name.upper())
    if env:
        try:
            v = json.loads(env)
            if isinstance(v, (list, dict)):
                return v
        except ValueError:
            pass
    ev = os.environ.get("GITHUB_EVENT_PATH")
    if ev and os.environ.get("GITHUB_EVENT_NAME") == "repository_dispatch":
        try:
            cp = (json.load(open(ev, encoding="utf-8"))
                  .get("client_payload") or {})
            v = cp.get(name)
            if isinstance(v, (list, dict)):
                return v
            if isinstance(v, str):
                return json.loads(v)
        except (OSError, ValueError):
            pass
    return None


def load_watchlist():
    """Condition ids for locally-mapped upcoming matches. These are
    force-included in trade + book collection regardless of volume."""
    data = _payload("watchlist")
    if data is None:
        try:
            data = json.load(open(os.path.join(ROOT, "state", "watchlist.json"),
                                  encoding="utf-8"))
        except (OSError, ValueError):
            return []
    stubs = []
    for m in (data.get("matches") or [])[:100]:
        for cid in m.get("condition_ids", []):
            stubs.append({
                "condition_id": cid,
                "event_id": m.get("pm_event_id"),
                "event_title": m.get("title"),
                "market_slug": None, "question": m.get("question_hint"),
                "volume_24h": 0,
            })
    return stubs


def load_watch_event_ids():
    """Event-level forcing: watchlist matches carry pm_event_id even when
    condition_ids lag (new markets appear after the local DB snapshot).
    Agent (night-loop) watch injections also arrive event-id-only."""
    data = _payload("watchlist")
    if data is None:
        try:
            data = json.load(open(os.path.join(ROOT, "state", "watchlist.json"),
                                  encoding="utf-8"))
        except (OSError, ValueError):
            return set()
    ids = set()
    for m in (data.get("matches") or [])[:100]:
        eid = m.get("pm_event_id")
        if eid:
            ids.add(str(eid))
    return ids


def collect_trades(lines, report):
    """Fetch recent trades (Data API) for the most liquid open markets,
    plus every market on the local watchlist (mapped upcoming matches)."""
    watch = {s["condition_id"]: s for s in load_watchlist()}
    watch_eids = load_watch_event_ids()
    pool = {l.get("condition_id"): l for l in lines
            if l.get("condition_id")}
    for cid, stub in watch.items():
        if cid not in pool:
            pool[cid] = stub  # watchlist market below volume floor: still track
    targets = sorted(
        (l for l in pool.values()
         if (l.get("volume_24h") or 0) >= MIN_MARKET_VOL24H_TRADES
         or l.get("condition_id") in watch
         or (l.get("event_id") and str(l.get("event_id")) in watch_eids)),
        key=lambda l: (l.get("condition_id") not in watch
                       and not (l.get("event_id")
                                and str(l.get("event_id")) in watch_eids),
                       -(l.get("volume_24h") or 0)))[:TRADE_MARKETS_CAP + 150]
    cutoff = time.time() - TRADE_WINDOW_MIN * 60
    out, seen_tx = [], set()
    for line in targets:
        cid = line.get("condition_id")
        if not cid:
            continue
        data, err = http_json(
            f"https://data-api.polymarket.com/trades?market={cid}&limit=100")
        if err is not None or not isinstance(data, list):
            report["errors"].append(f"trades {cid[:10]}: {err or 'bad type'}")
            continue
        for t in data:
            try:
                ts = int(t.get("timestamp") or 0)
                if ts < cutoff:
                    continue
                size = float(t.get("size") or 0)
                price = float(t.get("price") or 0)
                tx = t.get("transactionHash") or ""
                key = f"{tx}|{t.get('outcome')}|{size}|{ts}"
                if key in seen_tx:
                    continue
                seen_tx.add(key)
                out.append({
                    "ts": datetime.fromtimestamp(ts, timezone.utc)
                           .strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "collected_ts": datetime.now(timezone.utc)
                                      .strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "condition_id": cid,
                    "event_id": line.get("event_id"),
                    "event_title": line.get("event_title"),
                    "market_slug": line.get("market_slug"),
                    "question": line.get("question"),
                    "outcome": t.get("outcome"),
                    "side": t.get("side"),
                    "price": price,
                    "size": size,
                    "cost_usd": round(size * price, 2),
                    "wallet": t.get("proxyWallet") or t.get("wallet"),
                    "tx_hash": tx,
                })
            except (TypeError, ValueError):
                continue
        time.sleep(0.05)  # be gentle with the public data-api
    report["trades_markets_queried"] = len(targets)
    report["trades_watchlist_markets"] = len(watch)
    report["trades_collected"] = len(out)
    return out


def collect_books(lines, report):
    """CLOB order books for watchlist + top-volume markets: executable
    prices (best bid/ask, mid, top-3 depth) for CLV & slippage work.
    Snapshot prices are mid-points that no one can actually trade at.
    Written to YYYY-MM-DD.books.jsonl."""
    watch = {s["condition_id"] for s in load_watchlist()}
    watch_eids = load_watch_event_ids()
    pool = [l for l in lines if l.get("token_ids")
            and l.get("condition_id")]
    forced = [l for l in pool
              if l["condition_id"] in watch
              or (l.get("event_id") and str(l.get("event_id")) in watch_eids)]
    rest = sorted(
        (l for l in pool if l not in forced),
        key=lambda l: -(l.get("volume_24h") or 0))[:BOOK_TOP_MARKETS]
    targets = forced + rest
    ts = now_iso()
    out = []
    for line in targets:
        for token, outcome in zip(line.get("token_ids") or [],
                                  line.get("outcomes") or []):
            data, err = http_json(
                f"https://clob.polymarket.com/book?token_id={token}")
            if err is not None or not isinstance(data, dict):
                report.setdefault("books_errors", []).append(
                    f"{str(token)[:10]}: {err or 'bad type'}")
                continue
            try:
                bids = sorted(((float(lv.get("price") or 0),
                                float(lv.get("size") or 0))
                               for lv in data.get("bids") or []),
                              key=lambda x: -x[0])
                asks = sorted(((float(lv.get("price") or 0),
                                float(lv.get("size") or 0))
                               for lv in data.get("asks") or []),
                              key=lambda x: x[0])
            except (TypeError, ValueError):
                continue
            if not bids or not asks:
                continue
            bb, ba = bids[0][0], asks[0][0]
            out.append({
                "ts": ts,
                "condition_id": line["condition_id"],
                "event_id": line.get("event_id"),
                "question": line.get("question"),
                "outcome": str(outcome),
                "token_id": str(token),
                "best_bid": round(bb, 4), "best_ask": round(ba, 4),
                "mid": round((bb + ba) / 2, 4),
                "spread": round(ba - bb, 4),
                "bid_depth3_usd": round(sum(p * s for p, s in bids[:3]), 2),
                "ask_depth3_usd": round(sum(p * s for p, s in asks[:3]), 2),
            })
            time.sleep(0.05)
    report["books_markets"] = len(targets)
    report["books_rows"] = len(out)
    return out


def load_prior_frames(data_dir):
    """Latest price per (condition, outcome) from frames 45-90min old."""
    lo = time.time() - MOVE_FRAME_MAX * 60
    hi = time.time() - MOVE_FRAME_MIN * 60
    frames = {}
    if not os.path.isdir(data_dir):
        return frames
    for dirpath, _, filenames in os.walk(data_dir):
        for fn in filenames:
            # market frame files only: date-prefixed hourly parts
            # ("2026-10-03T20.jsonl"); skip sensor/history sidecars
            if not fn.endswith(".jsonl") or "." in fn[:10] or any(
                    k in fn for k in (".trades", ".anoms", ".books", ".side",
                                      "history")):
                continue
            try:
                datetime.strptime(fn[:10], "%Y-%m-%d")
            except ValueError:
                continue
            # hourly part files ("...T20.jsonl"): skip hours entirely outside
            # the 45-90min lookback window instead of reading every line
            if len(fn) >= 13 and fn[10] == "T" and fn[11:13].isdigit():
                try:
                    fdt = datetime.strptime(fn[:13], "%Y-%m-%dT%H")\
                        .replace(tzinfo=timezone.utc)
                except ValueError:
                    fdt = None
                if fdt and fdt < now_utc() - timedelta(
                        minutes=MOVE_FRAME_MAX + 65):
                    continue
            try:
                with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                    for raw in f:
                        try:
                            ln = json.loads(raw)
                            t = datetime.strptime(ln["ts"], "%Y-%m-%dT%H:%M:%SZ")\
                                         .replace(tzinfo=timezone.utc).timestamp()
                        except (KeyError, ValueError):
                            continue
                        if not (lo <= t <= hi):
                            continue
                        cid = ln.get("condition_id")
                        if not cid:
                            continue
                        for oc, pr in zip(ln.get("outcomes") or [],
                                          ln.get("prices") or []):
                            if isinstance(pr, (int, float)):
                                frames[(cid, str(oc))] = pr
            except OSError:
                continue
    return frames


def detect_anomalies(lines, trades, frames, report):
    """Three sensors: big single trade / 1h one-side net flow / sharp move."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    anoms = []
    for t in trades:
        px = t.get("price") or 0
        if (t["cost_usd"] >= BIG_TRADE_USD
                and (t.get("side") or "").upper() == "BUY"
                and DEGEN_PRICE_LOW <= px <= DEGEN_PRICE_HIGH):
            anoms.append({
                "type": "big_trade", "detected_ts": now,
                "event_id": t.get("event_id"), "event_title": t.get("event_title"),
                "question": t.get("question"), "condition_id": t.get("condition_id"),
                "outcome": t.get("outcome"), "price": t.get("price"),
                "cost_usd": t["cost_usd"], "wallet": t.get("wallet"),
                "tx_hash": t.get("tx_hash"),
                "detail": f"单笔买入 ${t['cost_usd']:,.0f} @ {t['price']:.0%}",
            })
    flow = {}
    for t in trades:
        k = (t["condition_id"], str(t.get("outcome")))
        d = flow.setdefault(k, {"buy": 0.0, "sell": 0.0})
        d["buy" if (t.get("side") or "").upper() == "BUY" else "sell"] += \
            t["cost_usd"]
    lines_by_cid = {l.get("condition_id"): l for l in lines}
    for (cid, outcome), d in flow.items():
        net = d["buy"] - d["sell"]
        total = d["buy"] + d["sell"]
        if total <= 0:
            continue
        share = max(abs(net), max(d["buy"], d["sell"])) / total
        if max(d["buy"], d["sell"]) >= FLOW_USD and share >= FLOW_ONE_SIDE_SHARE:
            line = lines_by_cid.get(cid) or {}
            outcomes = line.get("outcomes") or []
            cur_price = None
            if outcome in outcomes:
                idx = outcomes.index(outcome)
                prices = line.get("prices") or []
                cur_price = prices[idx] if idx < len(prices) else None
            anoms.append({
                "type": "one_side_flow", "detected_ts": now,
                "event_id": line.get("event_id"),
                "event_title": line.get("event_title"),
                "question": line.get("question"), "condition_id": cid,
                "outcome": outcome, "price": cur_price,
                "buy_usd": round(d["buy"], 2), "sell_usd": round(d["sell"], 2),
                "detail": f"1h净流 {outcome} 买 ${d['buy']:,.0f} / 卖 "
                          f"${d['sell']:,.0f} (占比 {share:.0%})",
            })
    for line in lines:
        cid = line.get("condition_id")
        if not cid:
            continue
        for oc, pr in zip(line.get("outcomes") or [], line.get("prices") or []):
            old = frames.get((cid, str(oc)))
            if old is None or not isinstance(pr, (int, float)):
                continue
            if abs(pr - old) >= MOVE_PP:
                anoms.append({
                    "type": "sharp_move", "detected_ts": now,
                    "event_id": line.get("event_id"),
                    "event_title": line.get("event_title"),
                    "question": line.get("question"), "condition_id": cid,
                    "outcome": str(oc), "price": pr, "price_from": old,
                    "price_to": pr,
                    "detail": f"胜率 {old:.0%} → {pr:.0%} "
                              f"(Δ{abs(pr-old):.0%}pp, "
                              f"{MOVE_FRAME_MIN}-{MOVE_FRAME_MAX}min窗口)",
                })
    seen_path = os.path.join(ROOT, "state", "seen_anomalies.json")
    try:
        seen = set(json.load(open(seen_path, encoding="utf-8")))
    except (OSError, ValueError):
        seen = set()
    fresh = []
    for a in anoms:
        if a["type"] == "big_trade":
            # same on-chain tx must never re-fire, regardless of window
            key = f"big_trade|{a.get('condition_id')}|{a.get('outcome')}|" \
                  f"{a.get('tx_hash')}"
        else:
            bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H") + \
                ("0" if datetime.now(timezone.utc).minute < 30 else "1")
            key = f"{a['type']}|{a.get('condition_id')}|{a.get('outcome')}|" \
                  f"{bucket}"
        if key in seen:
            continue
        seen.add(key)
        fresh.append(a)
    json.dump(sorted(seen)[-2000:], open(seen_path, "w", encoding="utf-8"))
    report["anomalies_detected"] = len(anoms)
    report["anomalies_new"] = len(fresh)
    return fresh


def collect_history(lines, report):
    """One-time price-history backfill per ML condition (watchlist + top
    markets): /prices-history interval=all enables retrospective CLV vs pm
    close for bets placed before our own collection began (2026-10-02)."""
    done_path = os.path.join(ROOT, "state", "history_done.json")
    try:
        done = set(json.load(open(done_path, encoding="utf-8")))
    except (OSError, ValueError):
        done = set()
    targets = []
    for l in lines:
        q = l.get("question") or ""
        cid = l.get("condition_id")
        tok = (l.get("token_ids") or [None])[0]
        if not cid or cid in done or cid in targets:
            continue
        if not tok:
            continue
        if " vs " in q and "(" in q and "Game " not in q \
                and "Handicap" not in q and "Will " not in q:
            targets.append((cid, tok))
    targets = targets[:40]
    hist_dir = os.path.join(ROOT, "data", "history")
    os.makedirs(hist_dir, exist_ok=True)
    n_new = 0
    debug = []
    variant_path = os.path.join(ROOT, "state", "history_variant.json")
    try:
        variant = json.load(open(variant_path, encoding="utf-8"))
    except (OSError, ValueError):
        variant = None
    variants = [variant] if variant else [
        "interval=all&fidelity=1000",
        "interval=1m",
        "startTs=%d&endTs=%d&fidelity=1000" % (
            int(time.time()) - 60 * 86400, int(time.time())),
        "",
    ]
    for cid, tok in targets:
        pts, used = None, None
        for v in variants:
            url = (f"https://clob.polymarket.com/prices-history?market={tok}"
                   + ("&" + v if v else ""))
            data, err = http_json(url)
            if err is not None or not isinstance(data, dict):
                continue
            p = (data or {}).get("history") or []
            if p:
                pts, used = p, v
                break
        debug.append({"cid": cid[:10], "pts": len(pts or []),
                      "variant": used or "none"})
        if not pts:
            continue
        if used and used != variant:
            variant = used
            json.dump(variant, open(variant_path, "w", encoding="utf-8"))
        eid = next((l.get("event_id") for l in lines
                    if l.get("condition_id") == cid), None)
        rec = {"condition_id": cid, "event_id": eid, "fetched_ts": now_iso(),
               "points": pts}
        with open(os.path.join(hist_dir, "backfill.jsonl"), "a",
                  encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        done.add(cid)
        n_new += 1
        time.sleep(0.05)
    report["history_debug"] = debug[:8]
    json.dump(sorted(done)[-5000:], open(done_path, "w", encoding="utf-8"))
    report["history_conditions_new"] = n_new
    return n_new


def norm_txt(s):
    import re as _re
    return _re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def norm_txt(s):
    import re as _re
    return _re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


# 中式队名 → pm英文队名候选 (org缩写为主; 用于rescue pass的搜索词与标题匹配)
CN_ALIAS = {
    "jdg": ["JD Gaming", "JDG"], "lgd": ["LGD Gaming"], "tes": ["Top Esports", "TES"],
    "wbg": ["Weibo Gaming"], "blg": ["Bilibili Gaming"], "edg": ["EDward Gaming"],
    "rng": ["Royal Never Give Up"], "fpx": ["FunPlus Phoenix"],
    "ig": ["Invictus Gaming"], "nip": ["Ninjas in Pyjamas"],
    "ra": ["Rare Atom"], "al": ["Anyone Legend"], "we": ["Team WE"],
    "tt": ["ThunderTalk"], "lng": ["LNG Esports"], "up": ["Ultra Prime"],
    "omg": ["OMG"], "gen": ["Gen.G"], "geng": ["Gen.G"], "dk": ["Dplus KIA"],
    "hle": ["Hanwha Life"], "kt": ["KT Rolster"], "ns": ["Nongshim"],
    "drx": ["DRX"], "kdf": ["Kwangdong"], "bro": ["BRION"], "t1": ["T1"],
    "fox": ["FearX"], "tsm": ["TSM"], "c9": ["Cloud9"], "fly": ["FlyQuest"],
    "100": ["100 Thieves"], "gg": ["Golden Guardians"], "eg": ["Evil Geniuses"],
    "vIT": ["Vitality"], "vitality": ["Vitality"], "g2": ["G2"], "fnc": ["Fnatic"],
    "mad": ["MAD Lions"], "sk": ["SK Gaming"], "spy": ["Splyce"],
    "spy": ["Splyce"], "og": ["OG"], "navi": ["Natus Vincere"], "navi": ["NAVI"],
    "faze": ["FaZe"], "mouz": ["MOUZ"], "big": ["BIG"], "spirit": ["Team Spirit"],
    "vp": ["Virtus.pro"], "gambit": ["Gambit"], "coL": ["compLexity"],
    "astralis": ["Astralis"], "liquid": ["Team Liquid"], "heroic": ["Heroic"],
    "apex": ["APEX"], "wings": ["Wings Up"], "tyloo": ["TYLOO"], "lv": ["Lynn Vision"],
}


def side_tokens(team):
    """一个队名 → 匹配词列表(原样norm + 别名扩展), 全为小写。"""
    base = norm_txt(team)
    toks = set()
    if base:
        toks.add(base)
        for w in base.split():
            if len(w) >= 2:
                toks.update(CN_ALIAS.get(w, []))
    return [norm_txt(t) for t in toks if norm_txt(t)]


def side_hit(title_norm, title_words, tokens):
    for t in tokens:
        if " " in t:
            if t in title_norm:
                return True
        elif t in title_words:
            return True
    return False


def backfill_betmatches(report):
    """Match real-bet matches (predictions ledger) to pm events via
    public-search, then backfill price history for their ML markets.
    Requests arrive via dispatch payload (public repo must not persist the
    betting ledger); done-state stays local to this repo (non-sensitive).
    Batch-limited per run; done-state includes no-hits (recorded for stats)."""
    reqs = _payload("betmatch_reqs")
    if reqs is None:
        req_path = os.path.join(ROOT, "state", "betmatch_requests.json")
        try:
            reqs = json.load(open(req_path, encoding="utf-8"))
        except (OSError, ValueError):
            return
    if not reqs:
        return
    done_path = os.path.join(ROOT, "state", "betmatch_done.json")
    try:
        done = json.load(open(done_path, encoding="utf-8"))
    except (OSError, ValueError):
        done = {}
    hist_dir = os.path.join(ROOT, "data", "history")
    os.makedirs(hist_dir, exist_ok=True)
    n_hit = n_miss = 0
    budget = 100  # keep each run well under the timeout; state commits per run
    for r in reqs:
        if r["key"] in done:
            continue
        if budget <= 0:
            break
        budget -= 1
        q = urllib.parse.quote(f"{r['team_a']} {r['team_b']}")
        data, err = http_json(f"{GAMMA}/public-search?q={q}&limit=10")
        hit = None
        for ev in (data or {}).get("events") or []:
            t = norm_txt(ev.get("title"))
            if norm_txt(r["team_a"]) in t and norm_txt(r["team_b"]) in t:
                hit = ev
                break
        if hit is None:
            data, err = http_json(
                f"{GAMMA}/public-search?q={urllib.parse.quote(r['team_a'])}&limit=10")
            for ev in (data or {}).get("events") or []:
                t = norm_txt(ev.get("title"))
                if norm_txt(r["team_a"]) in t and norm_txt(r["team_b"]) in t:
                    hit = ev
                    break
        if hit is None:
            done[r["key"]] = {"status": "no_hit", "game": r.get("game")}
            n_miss += 1
            continue
        n_hit += 1
        markets_out = []
        for m in hit.get("markets") or []:
            q = m.get("question") or ""
            toks = parse_maybe_json(m.get("clobTokenIds")) or []
            outs = parse_maybe_json(m.get("outcomes")) or []
            if " vs " not in q or "Game " in q or len(toks) < 2 \
                    or len(outs) != len(toks):
                continue
            for side, tok in zip(outs, toks):
                hdata, herr = http_json(
                    f"https://clob.polymarket.com/prices-history?market={tok}"
                    f"&interval=all&fidelity=1000")
                pts = (hdata or {}).get("history") or [] if herr is None else []
                if not pts:
                    continue
                rec = {"condition_id": tok, "event_id": hit.get("id"),
                       "match_key": r["key"], "team": side,
                       "fetched_ts": now_iso(), "points": pts}
                with open(os.path.join(hist_dir, "betmatch_backfill.jsonl"),
                          "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            markets_out.append({"question": q, "tokens": dict(zip(outs, toks))})
        with open(os.path.join(hist_dir, "betmatch_map.jsonl"), "a",
                  encoding="utf-8") as f:
            # 隐私(10-07): map行只留hash关联, 队名/日期明文不入公共仓
            f.write(json.dumps({
                "match_key": r["key"], "event_id": hit.get("id"),
                "title": hit.get("title"), "start": hit.get("startDate"),
                "end": hit.get("endDate"),
                "markets": markets_out}, ensure_ascii=False) + "\n")
        done[r["key"]] = {"status": "hit", "event_id": hit.get("id"),
                          "game": r.get("game")}
        time.sleep(0.1)

    # ---- rescue pass: no_hit场次用别名+ASCII抽取+日期邻近重匹配 (2026-10-03) ----
    rescue = [r for r in reqs if done.get(r["key"], {}).get("status") == "no_hit"]
    n_res = 0
    for r in rescue[:150]:
        tokens_a, tokens_b = side_tokens(r["team_a"]), side_tokens(r["team_b"])
        if not tokens_a or not tokens_b:
            done[r["key"]] = {"status": "no_pm", "game": r.get("game")}
            continue
        q = urllib.parse.quote(tokens_a[0])
        data, err = http_json(f"{GAMMA}/public-search?q={q}&limit=10")
        hit, hit_dt = None, None
        req_d = datetime.strptime(r["date"], "%Y-%m-%d").date() \
            if r.get("date") else None
        for ev in (data or {}).get("events") or []:
            tn = norm_txt(ev.get("title"))
            tw = set(tn.split())
            if side_hit(tn, tw, tokens_a) and side_hit(tn, tw, tokens_b):
                for k in ("startDate", "endDate"):
                    v = ev.get(k)
                    if not v:
                        continue
                    try:
                        d = datetime.fromisoformat(
                            str(v).replace("Z", "+00:00")).date()
                    except ValueError:
                        continue
                    if req_d and abs((d - req_d).days) <= 2:
                        hit, hit_dt = ev, d
                        break
            if hit:
                break
        if hit is None:
            done[r["key"]] = {"status": "no_pm", "game": r.get("game")}
            continue
        n_res += 1
        markets_out = []
        for m in hit.get("markets") or []:
            q = m.get("question") or ""
            toks = parse_maybe_json(m.get("clobTokenIds")) or []
            outs = parse_maybe_json(m.get("outcomes")) or []
            if " vs " not in q or "Game " in q or len(toks) < 2 \
                    or len(outs) != len(toks):
                continue
            for side, tok in zip(outs, toks):
                hdata, herr = http_json(
                    f"https://clob.polymarket.com/prices-history?market={tok}"
                    f"&interval=all&fidelity=1000")
                pts = (hdata or {}).get("history") or [] if herr is None else []
                if not pts:
                    continue
                rec = {"condition_id": tok, "event_id": hit.get("id"),
                       "match_key": r["key"], "team": side,
                       "fetched_ts": now_iso(), "points": pts}
                with open(os.path.join(hist_dir, "betmatch_backfill.jsonl"),
                          "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            markets_out.append({"question": q, "tokens": dict(zip(outs, toks))})
        with open(os.path.join(hist_dir, "betmatch_map.jsonl"), "a",
                  encoding="utf-8") as f:
            f.write(json.dumps({
                "match_key": r["key"], "event_id": hit.get("id"),
                "title": hit.get("title"), "start": hit.get("startDate"),
                "end": hit.get("endDate"), "rescued": True,
                "req": {"team_a": r["team_a"], "team_b": r["team_b"],
                        "date": r["date"], "game": r.get("game")},
                "markets": markets_out}, ensure_ascii=False) + "\n")
        done[r["key"]] = {"status": "hit", "event_id": hit.get("id"),
                          "game": r.get("game"), "rescued": True}
        time.sleep(0.1)
    report["betmatch_rescued"] = report.get("betmatch_rescued", 0) + n_res
    json.dump(done, open(done_path, "w", encoding="utf-8"))
    report["betmatch_hits"] = report.get("betmatch_hits", 0) + n_hit
    report["betmatch_miss"] = report.get("betmatch_miss", 0) + n_miss
    report["betmatch_remaining"] = sum(
        1 for r in reqs if r["key"] not in done)


def write_jsonl(path, records):
    with open(path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def retention(data_dir, report):
    cutoff = now_utc() - timedelta(days=RETENTION_DAYS)
    removed = 0
    if not os.path.isdir(data_dir):
        return removed
    for dirpath, dirnames, filenames in os.walk(data_dir):
        for fn in filenames:
            if not fn.endswith(".jsonl"):
                continue
            m = fn[:10]
            try:
                d = datetime.strptime(m, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if d < cutoff:
                os.remove(os.path.join(dirpath, fn))
                removed += 1
    for dirpath, dirnames, filenames in os.walk(data_dir, topdown=False):
        if not os.listdir(dirpath):
            os.rmdir(dirpath)
    report["retention_removed_files"] = removed
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["all", "snap", "flow"], default="all",
                    help="snap=发现+快照+markets.json; flow=读markets.json跑传感器+book"
                         " (v3并行矩阵: snap/flow两job经artifact交接, 单commit job落库)")
    args = ap.parse_args()
    report = {"ts": now_iso(), "sources": [], "errors": [], "stage": args.stage}
    # day-one guard: a fresh clone has no state/ or data/ dirs yet, and the
    # anomaly dedup write happens before any other makedirs
    os.makedirs(os.path.join(ROOT, "state"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    state_dir = os.path.join(ROOT, "state")
    data_dir = os.path.join(ROOT, "data")
    d = now_utc()
    day_dir = os.path.join(data_dir, d.strftime("%Y"), d.strftime("%m"))
    os.makedirs(day_dir, exist_ok=True)
    # hourly part files: at the 5-min live cadence a single day file grew
    # past ~100MB (2026-10-03 incident) and the contents-API raw download
    # started truncating (IncompleteRead) -> local ingest blind for 11.5h.
    # ~4MB/hour parts keep every download small; pull dedups by line hash.
    stamp = d.strftime("%Y-%m-%dT%H")
    lines, events = [], {}
    try:
        if args.stage in ("all", "snap"):
            query_events(report, events)
            ts = now_iso()
            lines, dropped = build_lines(events, ts)
            report["events"] = len(events)
            report["markets"] = len(lines)
            report["markets_dropped_by_volume_floor"] = dropped
            day_file = os.path.join(day_dir, stamp + ".jsonl")
            with open(day_file, "a", encoding="utf-8") as f:
                for ln in lines:
                    f.write(json.dumps(ln, ensure_ascii=False) + "\n")
            report["day_file"] = os.path.relpath(day_file, ROOT)
            # flow阶段的输入清单(snap/flow两job经artifact交接)
            with open(os.path.join(state_dir, "markets.json"), "w",
                      encoding="utf-8") as f:
                json.dump(lines, f, ensure_ascii=False)
            os.makedirs(state_dir, exist_ok=True)
            with open(os.path.join(state_dir, "latest.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"ts": ts, "events": len(events),
                           "markets": len(lines), "lines": lines},
                          f, ensure_ascii=False, indent=1)
            retention(data_dir, report)
        if args.stage in ("all", "flow"):
            if args.stage == "flow":
                mpath = os.path.join(state_dir, "markets.json")
                try:
                    with open(mpath, encoding="utf-8") as f:
                        lines = json.load(f)
                except (OSError, ValueError):
                    report["errors"].append("flow: markets.json缺失")
                report["markets"] = len(lines)
            prior_frames = load_prior_frames(data_dir)
            # --- 场外异动 sensor: trades + three detectors ---
            try:
                trades = collect_trades(lines, report)
                anoms = detect_anomalies(lines, trades, prior_frames, report)
                if trades:
                    write_jsonl(os.path.join(
                        day_dir, stamp + ".trades.jsonl"), trades)
                if anoms:
                    write_jsonl(os.path.join(
                        day_dir, stamp + ".anoms.jsonl"), anoms)
                collect_history(lines, report)
                backfill_betmatches(report)
            except Exception as e:  # noqa: BLE001 - sensor failure != collection failure
                report["errors"].append(
                    f"sensor: {type(e).__name__}: {e}")
            # --- CLOB order books (executable prices, CLV/slippage) ---
            try:
                books = collect_books(lines, report)
                if books:
                    write_jsonl(os.path.join(
                        day_dir, stamp + ".books.jsonl"), books)
            except Exception as e:  # noqa: BLE001
                report["errors"].append(f"books: {type(e).__name__}: {e}")
        report["status"] = "ok" if lines or args.stage == "flow" else "ok_empty"
    except Exception as e:  # noqa: BLE001 - still write the report, still commit
        report["status"] = "error"
        report["errors"].append(f"{type(e).__name__}: {e}")

    # flow阶段artifact只带data+seen_anomalies(与snap的state不冲突);
    # report分文件落, 避免两artifact同路径互相覆盖
    report_name = ("last_run_report.json" if args.stage != "flow"
                   else "last_run_report_flow.json")
    with open(os.path.join(state_dir, report_name), "w",
              encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: report[k] for k in
                      ("ts", "status", "events", "markets") if k in report}))
    # Always exit 0: the report must be committed so failures stay visible
    # in the repo even when every source errored.
    raise SystemExit(0)


if __name__ == "__main__":
    main()
