#!/usr/bin/env python3
"""Push fresh pm anomalies straight to WeCom group webhook (cloud-side).

v3 (2026-10-21 轰炸根治): 用户连续多日被已结束比赛的重复消息轰炸。尸检发现v2两个致命bug:
  ① 去重双钥匙: 检查用(event_id,outcome)元组, 保存用"type|condition|outcome"字符串
     → 永不匹配 → 去重从未生效, 每轮重推全部新异动(实测单轮87组=约15条消息)
  ② "已结束"闸门的正则瞄准detail里的"HH:MM开赛"字样, 但异动payload的detail
     只有"单笔买入 $XX,XXX @ XX%" → 正则永不命中 → 闸门从未拦截任何东西
v3修复:
  ① 钥匙统一: 检查与保存同键("ev|事件|方向"事件级 + "an|类型|条件|方向"单笔级, 6h TTL)
  ② 死活判据改结构化: 从近36h快照文件(YYYY-MM-DDTHH.jsonl)查event_end_date,
     市场已到期→跳过; 快照里查无此事件→跳过(fail-closed宁漏勿轰, 本地pm_signal_push
     有DB级闸门兜底赛前信号)
  ③ 运行级限速: 距上次推送<20min整轮跳过(防异常波次)
Dedup state: data/wecom_pushed.json. Env: WECOM_WEBHOOK. Stdlib only."""
import glob
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone

PUSH_TYPES = {"big_trade", "sharp_wallet", "big_premarket",
              "concentrated_entry", "insider_wallet"}
QUIET_TYPES = {"sharp_move", "one_side_flow"}
TTL_H = 6
FRESH_FILE_MIN = 15
RUN_COOLDOWN_MIN = 20
END_LOOKBACK_H = 36
TZ_BJT = timezone(timedelta(hours=8))
LABEL = {"big_trade": "🐋大单", "sharp_wallet": "🧠聪明钱",
         "big_premarket": "💰赛前大单", "one_side_flow": "⚔单边资金流",
         "concentrated_entry": "🎯集中建仓", "insider_wallet": "🔒S级内幕"}


def send(md):
    body = {"msgtype": "markdown", "markdown": {"content": md[:3800]}}
    req = urllib.request.Request(
        os.environ["WECOM_WEBHOOK"], data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode()).get("errcode")


def ev_of(a):
    return a.get("event_id") or a.get("condition_id") or "?"


def side_of(a):
    return str(a.get("outcome") or "")


def akey_an(a):
    return "an|%s|%s|%s" % (a.get("type"), a.get("condition_id"),
                            a.get("outcome"))


def akey_ev(a):
    return "ev|%s|%s" % (ev_of(a), side_of(a))


def _parse_ts(s):
    """ISO时间串→epoch; 解析失败返回None"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(
            str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def end_dates_for(eids, root):
    """近36h快照文件(排除anoms/books/trades/side) → {event_id: end_epoch}
    按小时文件名倒序扫, 找齐即停; 只信快照, 不做猜测。"""
    want = set(eids)
    found = {}
    now = time.time()
    files = []
    for f in glob.glob(os.path.join(root, "data", "**", "*.jsonl"),
                       recursive=True):
        base = os.path.basename(f)
        if any(t in base for t in (".anoms.", ".books.", ".trades.",
                                   ".side.", ".pinnacle.")):
            continue
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2})\.jsonl", base)
        if not m:
            continue
        fts = datetime(*map(int, m.groups()), tzinfo=timezone.utc).timestamp()
        if fts > now or now - fts > END_LOOKBACK_H * 3600:
            continue
        files.append((fts, f))
    files.sort(reverse=True)
    for _, f in files:
        if not want:
            break
        try:
            fh = open(f, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                if not any(e in line for e in want):   # 廉价预过滤
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                eid = str(row.get("event_id") or "")
                if eid in want:
                    ts = _parse_ts(row.get("event_end_date"))
                    if ts is not None:
                        found[eid] = ts
                        want.discard(eid)
    return found


def _persist_state(root, state_p):
    """[v3.1] 状态自持久化: flow模式job无git commit步(longrun才有),
    runner上写的状态随job蒸发→去重跨轮失效→活事件重复推送。
    用checkout持久化的凭据自己commit单文件; 失败不挡主流程(下次重写)。"""
    import subprocess
    try:
        subprocess.run(["git", "-C", root, "add",
                        os.path.relpath(state_p, root)],
                       timeout=30, capture_output=True)
        r = subprocess.run(
            ["git", "-C", root, "commit", "-m",
             "state: wecom_pushed (v3.1 self-persist)"],
            timeout=30, capture_output=True, text=True)
        if r.returncode == 0:
            subprocess.run(["git", "-C", root, "push"],
                           timeout=60, capture_output=True)
    except Exception:
        pass


def main():
    webhook = os.environ.get("WECOM_WEBHOOK", "")
    if not webhook:
        print("wecom_alert: no WECOM_WEBHOOK, skip")
        return
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    state_p = os.path.join(root, "data", "wecom_pushed.json")
    try:
        pushed = json.load(open(state_p, encoding="utf-8"))
    except Exception:
        pushed = {}
    now = time.time()
    # 运行级限速: 距上次实推<20min → 整轮跳过(防异常波次连发)
    if now - pushed.get("_last_push", 0) < RUN_COOLDOWN_MIN * 60:
        print("wecom_alert: run-cooldown, skip")
        return
    pushed = {k: t for k, t in pushed.items()
              if now - t < TTL_H * 3600 and not k.startswith("_")}

    # 收集本轮候选(每笔先过单笔级+事件级去重)
    groups, quiet = {}, 0
    for f in glob.glob(os.path.join(root, "data", "**", "*.anoms.jsonl"),
                       recursive=True):
        if now - os.path.getmtime(f) > FRESH_FILE_MIN * 60:
            continue
        for line in open(f, encoding="utf-8"):
            try:
                a = json.loads(line)
            except ValueError:
                continue
            if a.get("type") in QUIET_TYPES:
                quiet += 1
                continue
            if a.get("type") not in PUSH_TYPES:
                continue
            if akey_ev(a) in pushed or akey_an(a) in pushed:
                continue
            groups.setdefault(akey_ev(a), []).append(a)
    if not groups:
        json.dump({**pushed, "_last_push": pushed.get("_last_push", 0)},
                  open(state_p, "w", encoding="utf-8"))
        return  # 无新组: 状态无变化, 不必持久化

    # ★死活闸: 快照查event_end_date; 已到期→跳过, 查无→跳过(fail-closed)
    eids = {str(ev_of(g[0]) if g else "?") for g in groups.values()}
    ends = end_dates_for(eids, root)
    live_groups, dead = {}, 0
    for k, anoms in groups.items():
        eid = str(ev_of(anoms[0]))
        end_ts = ends.get(eid)
        if end_ts is None or end_ts < now - 30 * 60:
            dead += 1
            continue
        live_groups[k] = anoms
    if not live_groups:
        for k in groups:                      # 死单也记钥匙, 防每轮重扫重判
            pushed[k] = now
        json.dump({**pushed, "_last_push": pushed.get("_last_push", 0)},
                  open(state_p, "w", encoding="utf-8"))
        _persist_state(root, state_p)         # 死单钥匙必须跨轮存活
        print("wecom_alert: all %d groups dead/stale, none pushed" % dead)
        return

    segs = []
    events = sorted(
        live_groups.items(),
        key=lambda kv: -max(float(a.get("cost_usd") or 0) for a in kv[1]))
    for k, anoms in events[:6]:
        first = anoms[0]
        title_txt = (first.get("event_title")
                     or first.get("question") or "")[:50]
        types_in, max_cost, wallets = [], 0.0, set()
        for a in anoms:
            types_in.append(LABEL.get(a.get("type"), a.get("type")))
            max_cost = max(max_cost, float(a.get("cost_usd") or 0))
            if a.get("wallet"):
                wallets.add(str(a["wallet"])[:10])
        segs.append("**%s | %s**" % (
            LABEL.get(first.get("type"), first.get("type")), title_txt))
        segs.append("方向: **%s** | 信号: %s | 最大单笔 $%s%s"
                    % (first.get("outcome"), "+".join(dict.fromkeys(types_in)),
                       f"{max_cost:,.0f}",
                       f" | 钱包{len(wallets)}个" if wallets else ""))
        segs.append("怎么看: " + _read(first, max_cost))
    if len(events) > 6:
        segs.append("(另有%d场略)" % (len(events) - 6))
    if dead:
        segs.append(f"(已拦{dead}条已结束/过期市场消息)")
    if quiet:
        segs.append(f"(另{quiet}条小幅波动已汇总进摘要文件, 不再刷屏)")

    # 先记后发(10-11教训): 事件级+单笔级钥匙都落盘, 崩溃不重推
    for k, anoms in live_groups.items():
        pushed[k] = now
        for a in anoms:
            pushed[akey_an(a)] = now
    with open(state_p, "w", encoding="utf-8") as f:
        json.dump({**pushed, "_last_push": now}, f)
    _persist_state(root, state_p)             # [v3.1] flow模式无commit步
    err = send("\n".join(segs)[:3800])
    print("wecom_alert: pushed %d events (+%d quiet, %d dead-blocked) "
          "errcode=%s" % (len(events), quiet, dead, err))


def _read(a, max_cost):
    """按类型给人话解读(统一口径)."""
    t = a.get("type")
    if t == "insider_wallet":
        return "回测验证钱包赛前真金入场, 参考价值最高; 小仓跟随可以, 不重仓"
    if t == "sharp_wallet":
        return "有战绩的钱包下注, 方向值得看一眼"
    if t in ("big_premarket", "big_trade"):
        return ("有人砸了$%s买这一边——大钱通常有理由, 但也可能是错钱; "
                "跟不跟看价有没有肉" % f"{max_cost:,.0f}")
    if t == "one_side_flow":
        return "这一边1小时内只进不出, 资金明显站队; 注意追高风险"
    if t == "concentrated_entry":
        return "有人在这一边悄悄吸筹(占市场成交比例高), 先观察再决定"
    return "资金/价格异动, 参考"


if __name__ == "__main__":
    main()
