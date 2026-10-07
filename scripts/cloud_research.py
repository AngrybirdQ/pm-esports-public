#!/usr/bin/env python3
"""云上研究作业 — cloud_research.py (A3, 2026-10-04)

在 Actions runner 上从仓内数据文件重建全链路(不需要PG):
  data/**/*.{jsonl} → 价格序列/结算推断/成交流水 → sharp钱包评分
  → sharp跟单参数矩阵扫描(延迟×入场门槛×sharp线) → analysis/*.md

用法: python3 scripts/cloud_research.py [days=7]
GitHub Actions: research.yml (workflow_dispatch) 触发, 结果commit回analysis/。
本地PG仍是权威深历史; 本作业的价值=免本地CPU的大矩阵参数扫描。
Stdlib only.
"""
import glob
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEGEN = (0.03, 0.97)
import os as _os
DELAYS = tuple(int(x) for x in _os.environ.get(
    "RESEARCH_DELAYS", "30,60,120,240").split(","))   # 跟单延迟(分钟)
ENTRY_MIN = tuple(float(x) for x in _os.environ.get(
    "RESEARCH_ENTRY", "1000,2000,5000").split(","))   # 入场门槛($)
THRESH = ((5, 0.45, 0.10), (10, 0.45, 0.10), (10, 0.50, 0.20))  # (n, wr, roi)


def utc(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc) if s else None


def load(days):
    """读最近N天的 market/trades jsonl → (frames[(cid,outcome)]按时间序, trades)"""
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    frames, trades = {}, []
    files = glob.glob(os.path.join(ROOT, "data", "*", "*", "*.jsonl"))
    for fp in files:
        base = os.path.basename(fp)
        try:
            day = datetime.strptime(base[:10], "%Y-%m-%d").replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        if day < cutoff - 86400:
            continue
        with open(fp, encoding="utf-8") as f:
            for raw in f:
                try:
                    ln = json.loads(raw)
                except ValueError:
                    continue
                if ".trades." in base:
                    trades.append(ln)
                elif ".books." in base or ".anoms." in base or ".side." in base:
                    continue
                else:
                    ts = utc(ln.get("ts"))
                    if not ts:
                        continue
                    cid = ln.get("condition_id")
                    if not cid:
                        continue
                    for oc, pr in zip(ln.get("outcomes") or [],
                                      ln.get("prices") or []):
                        if isinstance(pr, (int, float)):
                            frames.setdefault(
                                (cid, str(oc)), []).append((ts, float(pr)))
    for v in frames.values():
        v.sort(key=lambda x: x[0])
    trades.sort(key=lambda t: utc(t.get("ts")) or datetime.min.replace(
        tzinfo=timezone.utc))
    return frames, trades


def build_resolutions(frames):
    """末帧≥0.98判结算 (与pm_smart_money同口径)"""
    res = {}
    for (cid, oc), series in frames.items():
        if not series:
            continue
        last_t, last_p = series[-1]
        if last_p >= 0.98:
            res[(cid, oc)] = last_t
    return res


def wallet_scores(trades, res):
    """钱包P&L账本: BUY开仓-SELL平仓 × 结算 → (n_resolved, wins, pnl, staked)"""
    pos = {}
    for t in trades:
        w = t.get("wallet")
        cid, oc = t.get("condition_id"), str(t.get("outcome"))
        if not w or not cid:
            continue
        try:
            size = float(t.get("size") or 0)
            cost = float(t.get("cost_usd") or 0)
        except (TypeError, ValueError):
            continue
        side = (t.get("side") or "").upper()
        p = pos.setdefault((w, cid, oc), {"sh": 0.0, "cost": 0.0,
                                          "sh_sell": 0.0, "proc": 0.0})
        if side == "BUY":
            p["sh"] += size
            p["cost"] += cost
        elif side == "SELL":
            p["sh_sell"] += size
            p["proc"] += cost
    score = {}
    for (w, cid, oc), p in pos.items():
        if p["sh"] <= 0 or (cid, oc) not in res:
            continue
        won = 1.0 if True else 0.0  # winner check below
        # (cid,oc)在res中=该outcome以≥0.98收盘 → 该腿获胜
        pnl = p["sh"] - p["sh_sell"] + p["proc"] - p["cost"]
        s = score.setdefault(w, {"n": 0, "wins": 0, "pnl": 0.0, "staked": 0.0})
        s["n"] += 1
        s["wins"] += 1          # 收盘≥0.98的腿必胜
        s["pnl"] += pnl
        s["staked"] += p["cost"]
    return score


def sharp_set(score, n_min, wr_min, roi_min):
    return {w for w, s in score.items()
            if s["n"] >= n_min and s["staked"] > 0
            and s["wins"] / s["n"] >= wr_min
            and s["pnl"] / s["staked"] >= roi_min}


def follow_matrix(frames, res, trades, score, sharp, entry_min):
    """对sharp入场, 首帧跟价 T+delay — 返回按延迟聚合的 (n, wr, roi)"""
    series_by = {}
    entries = {}
    for t in trades:
        w = t.get("wallet")
        if w not in sharp:
            continue
        cid, oc = t.get("condition_id"), str(t.get("outcome"))
        if (cid, oc) not in res or (cid, oc) not in frames:
            continue
        try:
            cost = float(t.get("cost_usd") or 0)
            price = float(t.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if cost < entry_min or not (DEGEN[0] <= price <= DEGEN[1]):
            continue
        ts = utc(t.get("ts"))
        if not ts:
            continue
        key = (w, cid, oc)
        if key not in entries or ts < entries[key][0]:
            entries[key] = (ts, price)
    agg = {d: {"n": 0, "wins": 0, "roi": 0.0} for d in DELAYS}
    for (w, cid, oc), (ts0, p_entry) in entries.items():
        series = sorted(frames[(cid, oc)], key=lambda x: x[0])
        for d in DELAYS:
            from datetime import timedelta
            t_end = ts0 + timedelta(minutes=d)
            follow = next((pr for ts, pr in series
                           if ts0 <= ts <= t_end and DEGEN[0] <= pr <= DEGEN[1]),
                          None)
            if follow is None:
                continue
            won = 1.0 if res[(cid, oc)] else 0.0
            roi = ((1.0 - follow) / follow) if won else -1.0
            agg[d]["n"] += 1
            agg[d]["wins"] += won
            agg[d]["roi"] += roi
    return agg


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    frames, trades = load(days)
    res = build_resolutions(frames)
    score = wallet_scores(trades, res)
    out_dir = os.path.join(ROOT, "analysis")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    lines = ["# sharp跟单参数矩阵 (云上重建, 数据窗口=%dd)" % days,
             "重建自仓内jsonl: frames=%d legs, trades=%d, resolutions=%d, "
             "wallets=%d" % (len(frames), len(trades), len(res), len(score)),
             ""]
    for (n_min, wr_min, roi_min) in THRESH:
        sharp = sharp_set(score, n_min, wr_min, roi_min)
        lines.append("## sharp线: n≥%d 胜率≥%d%% ROI≥%d%% → %d钱包"
                     % (n_min, wr_min * 100, roi_min * 100, len(sharp)))
        for em in ENTRY_MIN:
            agg = follow_matrix(frames, res, trades, score, sharp, em)
            lines.append("| 跟单延迟 | n | 胜率 | 等权ROI |")
            lines.append("|---|---|---|---|")
            for d in DELAYS:
                a = agg[d]
                if a["n"]:
                    lines.append("| T+%dmin | %d | %.0f%% | %+.1f%% |"
                                 % (d, a["n"], a["wins"] / a["n"] * 100,
                                    a["roi"] / a["n"] * 100))
                else:
                    lines.append("| T+%dmin | 0 | - | - |" % d)
            lines.append("")
    out = os.path.join(out_dir, "sharp_matrix_%s.md" % stamp)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(json.dumps({"out": os.path.relpath(out, ROOT),
                      "frames": len(frames), "trades": len(trades),
                      "res": len(res), "wallets": len(score)}))


if __name__ == "__main__":
    main()
