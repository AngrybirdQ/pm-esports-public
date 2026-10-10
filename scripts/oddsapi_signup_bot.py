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

def main():
    try:
        try:
                g = api("https://api.guerrillamail.com/ajax.php?f=get_email_address")
                addr, mpwd, mtok = g["email_addr"], "n/a", g["sid_token"]
                STATE["mail_provider"] = "guerrilla"
        except Exception as e1:
                print("guerrilla fail:", str(e1)[:100])
                doms = api("https://api.mail.tm/domains")["hydra:member"]
                dom = doms[0]["domain"]
                addr = f"pm.esports.{os.urandom(3).hex()}@{dom}"
                mpwd = f"Pm!{os.urandom(4).hex()}"
                api("https://api.mail.tm/accounts", "POST",
                    {"address": addr, "password": mpwd})
                mtok = api("https://api.mail.tm/token", "POST",
                           {"address": addr, "password": mpwd})["token"]
                STATE["mail_provider"] = "mailtm"
        STATE["email"] = addr
        STATE["mail_pwd"] = mpwd
        print("temp email:", addr)
    except Exception as e:
        STATE["fail"] = f"mailtm {e}"[:200]; write_report(); return
    pwd = f"Zq{os.urandom(5).hex()}!A"
    STATE["site_pwd"] = pwd
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            br = p.chromium.launch(headless=True)
            pg = br.new_page()
            pg.goto("https://the-odds-api.com/account/", timeout=60000)
            pg.wait_for_load_state("networkidle")
            snap(pg, "account_landed")
            # 若有"request access / sign up"入口, 点它
            for label in ("Request access", "request access", "Sign up",
                          "sign up", "Create account"):
                loc = pg.get_by_text(label, exact=False)
                if loc.count():
                    try:
                        loc.first.click(timeout=3000)
                        pg.wait_for_load_state("networkidle")
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
                pw = pg.locator("input[type=password]").first
                pw.fill(pwd, timeout=8000); filled["password"] = True
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
                pg.locator("button[type=submit], input[type=submit], "
                           "button:has-text('Request'), button:has-text('Sign up'), "
                           "button:has-text('Create'), button:has-text('Submit')"
                           ).first.click(timeout=8000)
            except Exception as e:
                STATE["fail"] = f"submit {e}"[:200]
                snap(pg, "submit_failed"); write_report(); br.close(); return
            pg.wait_for_load_state("networkidle")
            time.sleep(3)
            snap(pg, "after_submit")
            STATE["after_url"] = pg.url
            # 邮箱等验证链接
            link = None
            prov = STATE.get("mail_provider")
            t0 = time.time()
            while time.time() - t0 < 180 and not link:
                try:
                    if prov == "guerrilla":
                        box = api("https://api.guerrillamail.com/ajax.php"
                                  "?f=check_email&sid_token=" + mtok + "&seq=0")
                        for m in (box.get("list") or []):
                            mid = m.get("mail_id")
                            if not mid or str(mid) == "1":
                                continue
                            det = api("https://api.guerrillamail.com/ajax.php"
                                      "?f=fetch_email&sid_token=" + mtok +
                                      "&mail_id=" + str(mid))
                            txt = det.get("mail_body") or ""
                            for ln in txt.split():
                                if ln.startswith("http") and any(
                                        k in ln for k in ("verify", "confirm",
                                                          "activate", "token",
                                                          "account", "access")):
                                    link = ln.rstrip('").,'); break
                            if link:
                                break
                    else:
                        msgs = api("https://api.mail.tm/messages",
                                   headers={"Authorization": f"Bearer {mtok}"})
                        for m in msgs.get("hydra:member") or []:
                            txt = api(f"https://api.mail.tm/messages/{m['id']}",
                                      headers={"Authorization": f"Bearer {mtok}"})["text"]
                            for ln in (txt or "").split():
                                if ln.startswith("http") and any(
                                        k in ln for k in ("verify", "confirm",
                                                          "activate", "token",
                                                          "account")):
                                    link = ln.rstrip(").,"); break
                            if link:
                                break
                except Exception:
                    pass
                time.sleep(8)
            STATE["verify_link"] = link
            if not link:
                STATE["fail"] = "no verify link"
                write_report(); br.close(); return
            pg.goto(link, timeout=60000)
            pg.wait_for_load_state("networkidle")
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
                    pg.wait_for_load_state("networkidle")
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