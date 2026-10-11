#!/usr/bin/env python3
"""世博token云端猎手 — shibo_token_hunter.py (10-11)
Actions Playwright访问入口站(完整浏览器环境复现JS跳转) →
监听所有请求URL/响应/localStorage → 抓token数字 → 提交state/shibo_token_hunt.json"""
import base64
import json
import os
import re
import time
import urllib.request

Q = chr(34)  # 双引号
SQ = chr(39)  # 单引号
PAT_TOKEN = re.compile(r"token[^0-9]{0,14}?(\d{15,20})")
PAT_URLTOK = re.compile(r"token=(\d{15,20})")


def put_repo(path, local):
    tok = os.environ["GH_TOKEN"]
    R = os.environ.get("REPO", "AngrybirdQ/pm-esports-public")
    content = base64.b64encode(open(local, "rb").read()).decode()
    body = {"message": "shibo token hunt " + time.strftime("%H:%MZ"),
            "content": content, "branch": "main"}
    url = "https://api.github.com/repos/" + R + "/contents/" + path
    try:
        req = urllib.request.Request(url + "?ref=main", headers={
            "Authorization": "Bearer " + tok,
            "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            body["sha"] = json.loads(r.read().decode())["sha"]
    except Exception:
        pass
    req = urllib.request.Request(url, method="PUT", headers={
        "Authorization": "Bearer " + tok,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json"},
        data=json.dumps(body).encode())
    urllib.request.urlopen(req, timeout=30)


def main():
    from playwright.sync_api import sync_playwright
    tokens = set()
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True)
        pg = br.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/126.0 Safari/537.36")

        def on_req(r):
            for m in PAT_URLTOK.findall(r.url):
                tokens.add(m)

        def on_resp(resp):
            try:
                u = resp.url
                if "token" in u or "game" in u or "userver" in u:
                    t = resp.text()
                    for m in PAT_TOKEN.findall(t):
                        tokens.add(m)
            except Exception:
                pass

        pg.on("request", on_req)
        pg.on("response", on_resp)
        for url in ("https://dtpsg1awsapip01.3u9akgp.com/",
                    "https://dtoph5ali03.k5eh8uo.com/?domain=default&lang=cn"):
            try:
                pg.goto(url, timeout=45000, wait_until="domcontentloaded")
                time.sleep(8)
                for m in PAT_URLTOK.findall(pg.url):
                    tokens.add(m)
            except Exception as e:
                print("goto", url[:44], str(e)[:80])
        try:
            for store_js in ("localStorage", "sessionStorage"):
                vals = pg.evaluate(
                    "() => JSON.stringify(localStorage)")
                for m in re.findall(r"\d{15,20}", vals or ""):
                    tokens.add(m)
                break  # localStorage足够
        except Exception:
            pass
        br.close()
    out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "tokens": sorted(tokens)}
    print(json.dumps(out))
    os.makedirs("state", exist_ok=True)
    json.dump(out, open("state/shibo_token_hunt.json", "w"), ensure_ascii=False)
    put_repo("state/shibo_token_hunt.json", "state/shibo_token_hunt.json")


if __name__ == "__main__":
    main()
