#!/usr/bin/env python3
"""Liquipedia赛事交叉验证 — liquipedia_schedule.py (10-07, 梯队⑥)

转会监控同款通道(api.php+curl --compressed+描述UA)。抓四大游戏门户页的
"Upcoming tournaments"区已链接赛事页, 与上次快照diff → 新赛事=世博可能
漏挂的候选源(每场漏挂=孪生候选+价值线机会漏损) → data/liquipedia/。
赛程Matches页是widget化(JS), 只做门户层赛事发现, 不解析单场。
"""
import json
import os
import re
import subprocess

GAMES = ["counterstrike", "leagueoflegends", "dota2", "valorant"]
UA = "pm-esports-lp-intel/1.0 (contact: repo owner)"
STATE_DIR = os.path.join("state")


def fetch_portal(game):
    url = f"https://liquipedia.net/{game}?action=parse&prop=text&format=json"
    try:
        raw = subprocess.run(
            ["curl", "-sS", "--compressed", "-m", "40", "-A", UA, url],
            capture_output=True, text=True, timeout=60).stdout
        html = json.loads(raw).get("parse", {}).get("text", {}).get("*", "")
        return html
    except Exception as e:  # noqa: BLE001
        print(f"{game} fetch fail: {e}")
        return ""


def extract_upcoming(html):
    """门户页Upcoming区的赛事页链接(去重, 排除无日期导航项)"""
    links = set()
    for m in re.finditer(r'href="/[^"]*?/(?:[^"/"]+)"[^>]*>([^<]{4,60})<', html):
        href, txt = m.group(1), m.group(2)
        if "index.php" in href or "redlink=1" in href:
            continue
        low = txt.lower()
        if any(k in low for k in ("major", "cup", "league", "masters",
                                  "championship", "series", "open", "clash",
                                  "tournament", "invitational", "split")):
            links.add(txt.strip())
    return sorted(links)


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(os.path.join("data", "liquipedia"), exist_ok=True)
    added_total = {}
    for g in GAMES:
        path = os.path.join(STATE_DIR, f"lp_tournaments_{g}.json")
        try:
            prev = set(json.load(open(path, encoding="utf-8")))
        except (OSError, ValueError):
            prev = set()
        html = fetch_portal(g)
        if not html:
            added_total[g] = "fetch_fail"
            continue
        cur = extract_upcoming(html)
        new = [t for t in cur if t not in prev]
        json.dump(cur, open(path, "w", encoding="utf-8"),
                  ensure_ascii=False)
        json.dump({"game": g, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "tournaments": cur},
                  open(os.path.join("data", "liquipedia",
                                    f"upcoming_tournaments_{g}.json"), "w",
                       encoding="utf-8"), ensure_ascii=False)
        added_total[g] = new
        print(f"{g}: {len(cur)} tournaments, new={len(new)}")
    alerts = {g: v for g, v in added_total.items()
              if isinstance(v, list) and v}
    if alerts:
        md = "**🎮 Liquipedia新赛事发现**\n" + "\n".join(
            f"{g}: {', '.join(v[:6])}" for g, v in alerts.items()) + \
            "\n> 世博若未挂这些赛事=候选机会漏损, 建议核查star赛程覆盖。"
        try:
            import wecom_alert
            wecom_alert.send(md)
        except Exception as e:  # noqa: BLE001
            print("alert failed:", e)
    print(json.dumps({g: (len(v) if isinstance(v, list) else v)
                      for g, v in added_total.items()}))


if __name__ == "__main__":
    import time
    main()
