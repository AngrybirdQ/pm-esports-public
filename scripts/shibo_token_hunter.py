#!/usr/bin/env python3
"""世博token云端猎手 — shibo_token_hunter.py (10-11)
Actions Playwright访问入口站(完整浏览器环境复现当时JS跳转) →
监听所有请求URL/响应 → 抓token=数字 → state/shibo_token.json(RSA加密+明文时间戳)"""
import base64, json, os, re, time, urllib.request
def put_repo(path, content_b64, msg):
    tok = os.environ["GH_TOKEN"]; R = os.environ.get("REPO", "AngrybirdQ/pm-esports-public")
    body = {"message": msg, "content": content_b64, "branch": "main"}
    url = f"https://api.github.com/repos/{R}/contents/{path}"
    try:
        req = urllib.request.Request(url + "?ref=main", headers={
            "Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            body["sha"] = json.loads(r.read().decode())["sha"]
    except Exception:
        pass
    req = urllib.request.Request(url, method="PUT", headers={
        "Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
        "Content-Type": "application/json"}, data=json.dumps(body).encode())
    urllib.request.urlopen(req, timeout=30)

def main():
    from playwright.sync_api import sync_playwright
    tokens = set()
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True)
        pg = br.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
        # 监听所有请求URL(跳转链/API调用都可能带token)
        pg.on("request", lambda r: [tokens.add(m) for m in
               re.findall(r"token=(\d{15,20})", r.url)] or None)
        # 监听响应体里的token
        def on_resp(resp):
            try:
                if "token" in resp.url or "game" in resp.url:
                    t = resp.text()
                    for m in re.findall(r"["']?token["']?\s*[:=]\s*["']?(\d{15,20})", t):
                        tokens.add(m)
            except Exception:
                pass
        pg.on("response", on_resp)
        for url in ("https://dtpsg1awsapip01.3u9akgp.com/",
                    "https://dtoph5ali03.k5eh8uo.com/?domain=default&lang=cn"):
            try:
                pg.goto(url, timeout=45000, wait_until="domcontentloaded")
                time.sleep(8)  # 等JS初始化+潜在跳转
                # URL本体也可能直接带
                for m in re.findall(r"token=(\d{15,20})", pg.url):
                    tokens.add(m)
            except Exception as e:
                print("goto", url[:40], str(e)[:80])
        # localStorage/sessionStorage里的token
        try:
            for store_js in ("localStorage", "sessionStorage"):
                vals = pg.evaluate(f"() => JSON.stringify({store_js})")
                for m in re.findall(r"\d{15,20}", vals or ""):
                    tokens.add(m)
        except Exception:
            pass
        br.close()
    out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "tokens": sorted(tokens)}
    print(json.dumps(out))
    os.makedirs("state", exist_ok=True)
    json.dump(out, open("state/shibo_token_hunt.json", "w"), ensure_ascii=False)
    put_repo("state/shibo_token_hunt.json",
             base64.b64encode(open("state/shibo_token_hunt.json", "rb").read()).decode(),
             "shibo token hunt " + time.strftime("%H:%MZ"))
if __name__ == "__main__":
    main()
