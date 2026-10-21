#!/usr/bin/env python3
"""数据完整性自愈哨兵 — data_guard.py (10-07建, 10-21修)

校验今日采集覆盖: 主日文件存在 + 小时part文件覆盖到当前UTC小时。
缺口≥3小时 → wecom告警(采集断档=一切下游停摆的先兆)。
自愈动作: commit job每次运行本身就会重采; 本哨兵负责把断档变成即时告警。

[10-21审计修复 ×2]
  ① 哑炮bug: collect.yml commit job从未给本步骤传WECOM_WEBHOOK env
     → send()里get返回None直接return → 哨兵上线以来一次都没响过
     (修在workflow侧: 步骤加env)
  ② 无冷却轰炸隐患: 暗窗是常态(日~460min), 接通env后会每30min重发
     → 加state/data_guard_state.json冷却: 同类断档6h内只报一次,
       缺口数增长(新断档)≥30min间隔可重报, 断档消除→冷却重置
状态持久化: commit job每轮git add -A data state ✓
"""
import glob
import json
import os
import time
import urllib.request

DATA = os.environ.get("DATA_DIR", "data")
STATE = "state/data_guard_state.json"
COOLDOWN_S = 6 * 3600


def send(md):
    hook = os.environ.get("WECOM_WEBHOOK")
    if not hook:
        return
    body = {"msgtype": "markdown", "markdown": {"content": md[:2000]}}
    req = urllib.request.Request(hook, method="POST", headers={
        "Content-Type": "application/json"},
        data=json.dumps(body, ensure_ascii=False).encode())
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:  # noqa: BLE001
        print("alert failed:", e)


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main():
    d = time.gmtime()
    date = time.strftime("%Y-%m-%d", d)
    dirp = os.path.join(DATA, time.strftime("%Y/%m"))
    main_file = os.path.join(dirp, date + ".jsonl")
    parts = {os.path.basename(p) for p in
             glob.glob(os.path.join(dirp, date + "T*.jsonl"))}
    now_h = d.tm_hour
    missing = [h for h in range(now_h)
               if f"{date}T{h:02d}.jsonl" not in parts]
    stall = (not os.path.exists(main_file) and now_h >= 1)
    lvl = None
    if len(missing) >= 3:
        lvl = "小时part缺失%d个: %s" % (len(missing), missing[:8])
    elif stall:
        lvl = "主日文件不存在"
    st = load_state()
    now = time.time()
    alerted = False
    if lvl:
        # 冷却+新缺口双闸: 同类断档6h冷却; 缺口增长且距上报>30min可重报
        last_ts = st.get("alert_ts", 0)
        last_n = st.get("missing_n", -1)
        if (now - last_ts > COOLDOWN_S
                or (len(missing) > last_n and now - last_ts > 1800)):
            send("**🚨采集断档哨兵**\n%s %s\n"
                 "影响: 快照/异动/价差全下游停摆。\n"
                 "自愈: 本commit job已重采, 若下轮仍缺请查Actions跑批日志。"
                 % (date, lvl))
            alerted = True
            st.update({"alert_ts": now, "missing_n": len(missing)})
    else:
        st.update({"alert_ts": 0, "missing_n": -1})  # 恢复: 重置冷却
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"))
    print(json.dumps({"date": date, "main_exists": os.path.exists(main_file),
                      "parts": len(parts), "missing_hours": missing,
                      "alerted": alerted,
                      "in_cooldown": bool(lvl) and not alerted}))


if __name__ == "__main__":
    main()
