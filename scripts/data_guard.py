#!/usr/bin/env python3
"""数据完整性自愈哨兵 — data_guard.py (10-07, 梯队⑤)

校验今日采集覆盖: 主日文件存在 + 小时part文件覆盖到当前UTC小时。
缺口≥3小时 → wecom_alert告警(采集断档=一切下游停摆的先兆, 昨日世博事件教训)。
自愈动作: commit job每次运行本身就会重采; 本哨兵负责把断档变成即时告警。
"""
import glob
import json
import os
import time
import urllib.request

DATA = os.environ.get("DATA_DIR", "data")


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
        lvl = f"小时part缺失{len(missing)}个: {missing[:8]}"
    elif stall:
        lvl = "主日文件不存在"
    if lvl:
        send(f"**🚨采集断档哨兵**\n{date} {lvl}\n"
             f"影响: 快照/异动/价差全下游停摆。\n"
             f"自愈: 本commit job已重采, 若下轮仍缺请查Actions跑批日志。")
    print(json.dumps({"date": date, "main_exists": os.path.exists(main_file),
                      "parts": len(parts), "missing_hours": missing,
                      "alerted": bool(lvl)}))


if __name__ == "__main__":
    main()
