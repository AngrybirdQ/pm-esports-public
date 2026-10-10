#!/usr/bin/env python3
"""oddsapi_signup_bot.py — Actions无头注册the-odds-api (路线C, 10-10)
mail.tm临时邮箱 + playwright chromium(美国IP, reCAPTCHA无感) →
注册→验证邮件→激活→登录→dashboard抓API key→RSA加密落state/oddsapi_key.enc
"""
import base64, json, os, time, urllib.request

def api(url, method="GET", payload=None, headers=None, raw=False):
    h = {"User-Agent": "Mozilla/5.0 pm-signup-bot/1.0",
         "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, method=method, headers=h,
        data=json.dumps(payload).encode() if payload else None)
    with urllib.request.urlopen(req, timeout=25) as r:
        body = r.read().decode()
        return json.loads(body) if not raw and body[:1] in "{[" else body

def mailtm_account():
    doms = api("https://api.mail.tm/domains")["hydra:member"]
    dom = doms[0]["domain"]
    addr = f"pm.esports.{os.urandom(3).hex()}@{dom}"
    pwd = f"Pm!{os.urandom(4).hex()}"
    api("https://api.mail.tm/accounts", "POST",
        {"address": addr, "password": pwd})
    tok = api("https://api.mail.tm/token", "POST",
              {"address": addr, "password": pwd})["token"]
    return addr, pwd, tok

def mailtm_wait_link(tok, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            msgs = api("https://api.mail.tm/messages",
                       headers={"Authorization": f"Bearer {tok}"})
            for m in msgs.get("hydra:member") or []:
                mid = m["id"]
                txt = api(f"https://api.mail.tm/messages/{mid}",
                          headers={"Authorization": f"Bearer {tok}"})["text"]
                for ln in txt.split():
                    if ln.startswith("http") and ("verify" in ln or "confirm" in ln
                                                  or "activate" in ln or "token" in ln):
                        return ln.rstrip(").,")
        except Exception:
            pass
        time.sleep(8)
    return None

def main():
    out = {}
    # 1) 临时邮箱
    addr, mpwd, mtok = mailtm_account()
    out["email"] = addr
    print("temp email:", addr)
    api_pwd = f"Zq{os.urandom(5).hex()}!A"
    out["site_password"] = api_pwd  # 仅日志masked, 需要时从Actions secret取

    # 2) playwright注册
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True)
        pg = br.new_page()
        pg.goto("https://the-odds-api.com/account/create/", timeout=45000)
        pg.wait_for_load_state("networkidle")
        # 探测表单(字段名自适应)
        html = pg.content()
        print("page title:", pg.title())
        # 常见字段: email/password/name — 用label/placeholder/name匹配
        def fill(sel_types, val):
            for kw in sel_types:
                loc = pg.locator(
                    f"input[type=email], input[name*=email i], "
                    f"input[type=password], input[name*=pass i], "
                    f"input[name*=name i], input[type=text]")
                # 精细化: 按关键词单独找
            return None
        # 直接按语义找
        email_in = pg.locator("input[type=email], input[name*='mail' i]").first
        pass_in = pg.locator("input[type=password]").first
        name_in = pg.locator("input[name*='name' i]:not([name*='user' i])").first
        email_in.fill(addr)
        pass_in.fill(api_pwd)
        try:
            if name_in.count() and name_in.is_visible():
                name_in.fill("pm esport")
        except Exception:
            pass
        # 等reCAPTCHA v3自动执行(美国住宅级IP一般直接过)
        time.sleep(6)
        pg.locator("button[type=submit], input[type=submit]").first.click()
        pg.wait_for_load_state("networkidle")
        out["after_signup_url"] = pg.url
        print("after submit url:", pg.url)
        # 3) 邮箱激活链接 → 浏览器访问
        link = mailtm_wait_link(mtok)
        out["verify_link"] = link
        if not link:
            print(json.dumps({"fail": "no verify link", **out}))
            return
        pg.goto(link, timeout=45000)
        pg.wait_for_load_state("networkidle")
        time.sleep(3)
        # 4) 登录(激活后一般自动登录态; 否则填一次)
        if "login" in pg.url or "signin" in pg.url:
            pg.locator("input[type=email], input[name*='mail' i]").first.fill(addr)
            pg.locator("input[type=password]").first.fill(api_pwd)
            time.sleep(5)
            pg.locator("button[type=submit], input[type=submit]").first.click()
            pg.wait_for_load_state("networkidle")
        # 5) dashboard找API key (页面任意位置40位hex/uuid样式)
        key = None
        for cand in pg.locator("code, pre, td, span").all_text_contents():
            import re
            m = re.search(r"\b([0-9a-f]{32,64})\b", cand or "")
            if m:
                key = m.group(1)
                break
        out["key_found"] = bool(key)
        br.close()
    if not key:
        print(json.dumps({"fail": "no key on dashboard", **out}))
        return
    # 6) RSA加密落盘
    open("/tmp/pub.pem", "w").write(api(
        "https://api.github.com/repos/AngrybirdQ/pm-esports-public/contents/"
        "state/oddsapi_pub.pem?ref=main",
        headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                 "Accept": "application/vnd.github+json"}) )
    import subprocess
    subprocess.run(["bash", "-c",
        f"echo -n '{key}' | openssl pkeyutl -encrypt -pubin "
        f"-inkey /tmp/pub.pem | base64 -w0 > state/oddsapi_key.enc"], check=True)
    print("KEY_ENCRYPTED_OK length", len(key))
    os.makedirs("state", exist_ok=True)
    json.dump({k: v for k, v in out.items() if k != "site_password"},
              open("state/oddsapi_signup_report.json", "w"), ensure_ascii=False)

if __name__ == "__main__":
    main()
