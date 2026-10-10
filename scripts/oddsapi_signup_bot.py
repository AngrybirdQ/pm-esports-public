#!/usr/bin/env python3
"""oddsapi_signup_bot v2 — 全阶段容错+HTML快照+永远落报告(远程可调试)"""
import base64, json, os, re, time, urllib.request

STATE = {"stages": []}

def snap(pg, name):
    try:
        html = pg.content()
        STATE["stages"].append({"name": name, "url": pg.url,
                                "html_b64": base64.b64encode(
                                    html.encode("utf-8", "ignore")).decode()[:120000]})
    except Exception as e:
        STATE["stages"].append({"name": name, "err": str(e)[:120]})

def api(url, method="GET", payload=None, headers=None):
    h = {"User-Agent": "Mozilla/5.0 pm-bot/1.0",
         "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, method=method, headers=h,
        data=json.dumps(payload).encode() if payload else None)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())

def write_report(path="state/oddsapi_signup_report.json"):
    os.makedirs("state", exist_ok=True)
    json.dump(STATE, open(path, "w"), ensure_ascii=False)
    print("REPORT_WRITTEN", json.dumps(
        {k: v for k, v in STATE.items() if k != "stages"},
        ensure_ascii=False)[:300])

def imap_fetch_verify_link(since_min=20):
    """QQ邮箱IMAP读最近验证邮件"""
    import imaplib, email
    from email.header import decode_header
    cfg = json.load(open(os.path.expanduser(
        "~/.config/polymarket-sync/qqmail_imap.json")))
    M = imaplib.IMAP4_SSL("imap.qq.com", 993)
    M.login(cfg["email"], cfg["auth_code"])
    M.select("INBOX")
    since = time.strftime("%d-%b-%Y",
                          time.gmtime(time.time() - since_min * 60))
    typ, data = M.search(None, f'(SINCE "{since}")')
    link = None
    for i in reversed(data[0].split()):
        typ, d = M.fetch(i, "(BODY[TEXT])")
        body = d[0][1].decode("utf-8", "ignore")
        for ln in body.split():
            if ln.startswith("https://") and "the-odds-api.com" in ln:
                link = ln.rstrip(").,>\""); break
        if link:
            break
    M.logout()
    return link


def setup_mail():
    """QQ邮箱IMAP通道: 注册用真邮箱, 验证信走IMAP"""
    cfg = json.load(open(os.path.expanduser(
        "~/.config/polymarket-sync/qqmail_imap.json")))
    return cfg["email"], cfg["auth_code"], cfg, "qq-imap"


def main():
    try:
        addr, mpwd, mtok, prov = setup_mail()
        STATE["email"] = addr
        STATE["mail_provider"] = prov
        print("signup email:", addr, "| provider:", prov)
    except Exception as e:
        STATE["fail"] = f"mail setup {e}"[:200]; write_report(); return
    pwd = f"Zq{os.urandom(5).hex()}!A"
    STATE["site_pwd"] = pwd
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            br = p.chromium.launch(headless=True)
            pg = br.new_page()
            pg.goto("https://the-odds-api.com/account/", timeout=60000)
            pg.wait_for_load_state("domcontentloaded"); time.sleep(2)
            snap(pg, "account_landed")
            # 若有"request access / sign up"入口, 点它
            for label in ("Request access", "request access", "Sign up",
                          "sign up", "Create account"):
                loc = pg.get_by_text(label, exact=False)
                if loc.count():
                    try:
                        loc.first.click(timeout=3000)
                        pg.wait_for_load_state("domcontentloaded"); time.sleep(2)
                        time.sleep(2)
                        snap(pg, f"clicked_{label}")
                        break
                    except Exception:
                        continue
            # 填表: 邮箱(任何email型或名字含mail), 密码(password型)
            filled = {}
            try:
                e = pg.locator("input[type=email], input[name*='mail' i], "
                               "input[placeholder*='mail' i]").first
                e.fill(addr, timeout=8000); filled["email"] = True
                pws = pg.locator("input[type=password]")
                pw = pws.first
                pw.fill(pwd, timeout=8000); filled["password"] = True
                if pws.count() > 1:  # Amplify: confirm_password
                    pws.nth(1).fill(pwd, timeout=8000)
                    filled["confirm_password"] = True
                n = pg.locator("input[name*='name' i], "
                               "input[placeholder*='name' i]").first
                try:
                    if n.count() and n.is_visible():
                        n.fill("pm esport", timeout=3000); filled["name"] = True
                except Exception:
                    pass
            except Exception as e:
                STATE["fail"] = f"fill {e}"[:200]
                snap(pg, "fill_failed"); write_report(); br.close(); return
            STATE["filled"] = filled
            time.sleep(6)  # recaptcha v3 execute
            try:
                # Amplify Authenticator: 提交钮文本"Create Account"(无type=submit)
                btn = pg.locator(
                    "button.amplify-button:has-text('Create Account'), "
                    "button:has-text('Create Account')").first
                btn.click(timeout=8000)
                # Cognito可能弹邮件验证码输入框 — 等待并可处理
                time.sleep(4)
                code_in = pg.locator(
                    "input[name='confirmation_code'], input[placeholder*='code' i], "
                    "input[name*='code' i]")
                if code_in.count():
                    # 从guerrillamail抓验证码
                    code = None
                    t0 = time.time()
                    while time.time() - t0 < 120 and not code:
                        try:
                            box = api("https://api.guerrillamail.com/ajax.php"
                                      "?f=check_email&sid_token=" + mtok + "&seq=0")
                            for m in (box.get("list") or []):
                                det = api("https://api.guerrillamail.com/ajax.php"
                                          "?f=fetch_email&sid_token=" + mtok +
                                          "&mail_id=" + str(m.get("mail_id")))
                                mm = re.search(r"\b(\d{6})\b",
                                               det.get("mail_body") or "")
                                if mm:
                                    code = mm.group(1); break
                        except Exception:
                            pass
                        time.sleep(6)
                    if code:
                        code_in.first.fill(code, timeout=8000)
                        filled["confirmation_code"] = code
                        try:
                            pg.locator(
                                "button:has-text('Confirm'), "
                                "button.amplify-button:has-text('Confirm')"
                            ).first.click(timeout=8000)
                        except Exception:
                            pass
                        time.sleep(4)
            except Exception as e:
                STATE["fail"] = f"submit {e}"[:200]
                snap(pg, "submit_failed"); write_report(); br.close(); return
            pg.wait_for_load_state("domcontentloaded"); time.sleep(2)
            time.sleep(3)
            snap(pg, "after_submit")
            STATE["after_url"] = pg.url
            # 邮箱等验证链接
            link = imap_fetch_verify_link(since_min=20)
            STATE["verify_link"] = link
            if not link:
                STATE["fail"] = "no verify link"
                write_report(); br.close(); return
            pg.goto(link, timeout=60000)
            pg.wait_for_load_state("domcontentloaded"); time.sleep(2)
            time.sleep(3)
            snap(pg, "after_verify")
            # dashboard找key(v4): 等异步渲染(最长25s), 每轮全文本扫
            key = None
            for _ in range(10):
                time.sleep(2.5)
                for cand in pg.locator("code, pre, td, span, div, p").all_text_contents():
                    m = re.search(r"\b([0-9a-f]{32}|[0-9a-f]{64})\b", cand or "")
                    if m:
                        key = m.group(1); break
                if key:
                    break
            # 若dashboard要求登录: 用凭据登录再抓
            if not key:
                try:
                    e = pg.locator("input[type=email], input[name*='mail' i]").first
                    pw = pg.locator("input[type=password]").first
                    e.fill(addr, timeout=6000)
                    pw.fill(pwd, timeout=6000)
                    time.sleep(6)
                    pg.locator("button[type=submit], button:has-text('Log in'), "
                               "button:has-text('Sign in')").first.click(timeout=8000)
                    pg.wait_for_load_state("domcontentloaded"); time.sleep(2)
                    for _ in range(8):
                        time.sleep(2.5)
                        for cand in pg.locator(
                                "code, pre, td, span, div, p").all_text_contents():
                            m = re.search(r"\b([0-9a-f]{32}|[0-9a-f]{64})\b",
                                          cand or "")
                            if m:
                                key = m.group(1); break
                        if key:
                            break
                    snap(pg, "after_login_attempt")
                except Exception as e:
                    STATE["login_try"] = str(e)[:120]
            if not key:
                STATE["fail"] = "no key on dashboard"
                write_report(); br.close(); return
            STATE["key_found"] = True
            br.close()
        # RSA加密
        pub = api("https://api.github.com/repos/AngrybirdQ/pm-esports-public"
                  "/contents/state/oddsapi_pub.pem?ref=main",
                  headers={"Authorization": f"Bearer {os.environ.get('GH_TOKEN','')}",
                           "Accept": "application/vnd.github+json"})
        open("/tmp/pub.pem", "wb").write(base64.b64decode(pub["content"]))
        import subprocess
        subprocess.run(["bash", "-c",
            f"echo -n '{key}' | openssl pkeyutl -encrypt -pubin -inkey /tmp/pub.pem "
            f"| base64 -w0 > state/oddsapi_key.enc"], check=True)
        STATE["ok"] = True
        write_report()
    except Exception as e:
        STATE["fail"] = f"outer {type(e).__name__}: {e}"[:250]
        write_report()

if __name__ == "__main__":
    main()