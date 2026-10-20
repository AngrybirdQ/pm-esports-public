// 通用API代理worker — api-proxy.js (10-20 KV预填版)
// ⚠️ 网络限制(10-20实测): workers.dev域名从Actions runner访问=CF自身403,
// 从本机访问=DNS污染超时。因此fetch端点不可达, KV预填只能靠scheduled(cron)侧。
// /pin/latest端点同样受此限制 — 需自定义域名绑定后才能解锁(用户需购域名)。
const ALLOWED = [
  "guest.api.arcadia.pinnacle.com",
  "api.pinnacle.com",
  "api.the-odds-api.com",
  "api.elections.kalshi.com",
  "gamma-api.polymarket.com",
  "clob.polymarket.com",
];
const SECRET_KEY = "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R";

// 10-20 KV全链版: workers.dev HTTP从Actions=CF 403/本地=DNS污染,均不可达。
// 正轨=scheduled(cron)在CF内部走完 leagues→matchups→markets 全链,
// 产出与pin_cloud_sweep.py同契约的行存KV(pin:rows:<UTC小时>),
// 本地pin_kv_rest.py经api.cloudflare.com REST直读→pm_book_odds。
// 子请求预算(免费版50/次): 1 + 6联赛×(1+5) = ≤37。
const PIN_BASE = "https://guest.api.arcadia.pinnacle.com/0.1";
const PIN_ESPORTS_KW = [
  "esports", "lol", "league of legends", "csgo", "counter strike",
  "counter-strike", "dota", "valorant", "vct", "lec", "lck", "lpl",
  "blast", "esl", "iem", "overwatch", "starcraft", "rocket league",
  "call of duty", "king of glory", "mlbb", "arena of valor",
];
const PIN_LEAGUE_CAP = 6;
const PIN_MU_CAP = 5;

function amerToDec(a) {
  const n = Number(a);
  if (!n || !isFinite(n)) return null;
  return Math.round((1 + (n > 0 ? n / 100 : 100 / -n)) * 1000) / 1000;
}

async function pinSweep(env) {
  const H = {"Accept": "application/json", "User-Agent": "api-proxy-worker/1.0"};
  const lgResp = await fetch(PIN_BASE + "/sports/12/leagues?featured=true", {headers: H});
  if (lgResp.status !== 200) return {error: "leagues http " + lgResp.status};
  const leagues = await lgResp.json();
  const live = (leagues || []).filter(l =>
    ((l.eventCount || 0) + (l.matchupCount || 0)) > 0);
  // 电竞关键词优先, 其余热门联赛补位(大满贯期间featured含电竞)
  const isEsports = l => PIN_ESPORTS_KW.some(k => (l.name || "").toLowerCase().includes(k));
  const picked = [...live.filter(isEsports), ...live.filter(l => !isEsports(l))]
    .slice(0, PIN_LEAGUE_CAP);
  const rows = [];
  let nMu = 0, nErr = 0;
  for (const lg of picked) {
    let mus;
    try {
      const r = await fetch(`${PIN_BASE}/leagues/${lg.id}/matchups`, {headers: H});
      if (r.status !== 200) { nErr++; continue; }
      mus = await r.json();
    } catch (e) { nErr++; continue; }
    const real = (mus || []).filter(m =>
      m.type === "matchup" && m.participants).slice(0, PIN_MU_CAP);
    nMu += real.length;
    for (const m of real) {
      const names = m.participants.map(p => p.name || "?");
      const [home, away] = [names[0] || "?", names[1] || "?"];
      if (home === "?" && away === "?") continue;
      let odds;
      try {
        const r = await fetch(
          `${PIN_BASE}/matchups/${m.id}/markets/related/straight`, {headers: H});
        if (r.status !== 200) { nErr++; continue; }
        odds = await r.json();
      } catch (e) { nErr++; continue; }
      const byDes = {};
      for (const mk of (odds || [])) {
        if (mk.type !== "moneyline" || mk.key !== "s;0;m") continue;
        for (const pr of (mk.prices || [])) {
          const dec = amerToDec(pr.price);
          if (dec && (pr.designation === "home" || pr.designation === "away")) {
            byDes[pr.designation] = {designation: pr.designation, price: dec};
          }
        }
      }
      if (!byDes.home || !byDes.away) continue;
      rows.push({ts: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
        source: "pinnacle", kind: "market", obs_key: String(m.id),
        payload: {event_id: String(m.id), home, away,
          start: m.startTime, league: lg.name,
          prices: [byDes.home, byDes.away]}});
    }
  }
  const hourKey = "pin:rows:" + new Date().toISOString().slice(0, 13);
  const meta = {ts: Date.now(), leagues: picked.length, matchups: nMu,
    rows: rows.length, errors: nErr};
  await env.PIN_CACHE.put(hourKey, JSON.stringify({ts: Date.now(), rows}),
    {expirationTtl: 10800});
  await env.PIN_CACHE.put("pin:meta", JSON.stringify(meta), {expirationTtl: 10800});
  return meta;
}

export default {
  async scheduled(event, env, ctx) {
    console.log("cron tick", new Date(event.scheduledTime).toISOString());
    // 心跳自报(10-20排查): 无论后续成败, 先落KV证明tick已触发
    try { await env.PIN_CACHE.put("pin:hb",
      JSON.stringify({t: Date.now(), sched: event.scheduledTime}),
      {expirationTtl: 1800}); } catch (e) {}
    // 全链预填(10-20): cron侧CF内部拉Pinnacle全链写KV(pin:rows:*),
    // 本地REST直读 → 不依赖workers.dev HTTP可达性
    if (env.PIN_CACHE) {
      try {
        const meta = await pinSweep(env);
        console.log("pinSweep", JSON.stringify(meta));
      } catch (e) {
        console.log("pinSweep fail", e.message, e.stack);
        try { await env.PIN_CACHE.put("pin:meta",
          JSON.stringify({ts: Date.now(), error: String(e && e.message),
            stack: String(e && e.stack).slice(0, 500)}),
          {expirationTtl: 10800}); } catch (e2) {}
      }
    }
    // ⑥ Actions schedule备援: 每小时第5分触发collect dispatch
    const d = new Date(event.scheduledTime);
    if (d.getUTCMinutes() < 5 && d.getUTCHours() % 1 === 0) {
      try {
        const tok = env.GH_DISPATCH_TOKEN;
        if (tok) {
          await fetch(
            "https://api.github.com/repos/AngrybirdQ/pm-esports-public/dispatches",
            {method: "POST",
             headers: {"Authorization": `Bearer ${tok}`,
                        "Accept": "application/vnd.github+json"},
             body: JSON.stringify({event_type: "collect",
               client_payload: {mode: "tick", source: "cf-cron-backup"}})});
        }
      } catch(e) { console.log("dispatch fail", e.message); }
    }
  },
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return new Response(JSON.stringify({ok: true, ts: Date.now()}),
        {headers: {"Content-Type": "application/json"}});
    }
    // /pin/latest: 返回KV缓存的最新Pinnacle联赛数据(本地脚本低延迟查)
    if (url.pathname === "/pin/latest" && env.PIN_CACHE) {
      const key = request.headers.get("x-cache-key") ||
        "proxy:https://guest.api.arcadia.pinnacle.com/0.1/sports/12/leagues?featured=true";
      const cached = await env.PIN_CACHE.get(key, "json");
      if (cached) {
        return new Response(JSON.stringify(cached), {
          headers: {"Content-Type": "application/json",
                    "X-Cache-Age": String(Math.round((Date.now()-cached.ts)/1000))}});
      }
      return new Response(JSON.stringify({error: "no cache"}), {status: 404});
    }
    const key = request.headers.get("x-key");
    if (key !== SECRET_KEY) {
      return new Response("forbidden", {status: 403});
    }
    const target = url.searchParams.get("url");
    if (!target) {
      return new Response("missing url param", {status: 400});
    }
    const tUrl = new URL(target);
    if (!ALLOWED.includes(tUrl.hostname)) {
      return new Response(`host ${tUrl.hostname} not in allowlist`, {status: 403});
    }

    // KV缓存命中: 上游响应缓存(TTL=cache_ttl秒, 默认300s)
    const cacheKey = "proxy:" + target;
    if (env.PIN_CACHE) {
      try {
        const cached = await env.PIN_CACHE.get(cacheKey, "json");
        if (cached && (Date.now() - cached.ts) < (parseInt(url.searchParams.get("cache_ttl") || "300") * 1000)) {
          return new Response(JSON.stringify(cached.data), {
            headers: {"Content-Type": "application/json", "X-Cache": "HIT",
                      "X-Cache-Age": String(Math.round((Date.now()-cached.ts)/1000))},
          });
        }
      } catch(e) {}
    }

    const resp = await fetch(target, {
      headers: {
        "User-Agent": "api-proxy-worker/1.0",
        "Accept": "application/json",
        ...Object.fromEntries(
          [...request.headers.entries()].filter(([h]) => h.startsWith("x-pass-"))
            .map(([h, v]) => [h.slice(7), v])
        ),
      },
    });
    const bodyBuf = await resp.arrayBuffer();

    // KV缓存写入(仅200+JSON)
    if (env.PIN_CACHE && resp.status === 200) {
      const ttl = parseInt(url.searchParams.get("cache_ttl") || "300") || 300;
      try {
        const cacheData = JSON.parse(new TextDecoder().decode(bodyBuf));
        ctx.waitUntil(env.PIN_CACHE.put(cacheKey,
          JSON.stringify({ts: Date.now(), data: cacheData}),
          {expirationTtl: Math.min(ttl, 86400)}));
      } catch(e) {}
    }

    return new Response(bodyBuf, {
      status: resp.status,
      headers: {"Content-Type": resp.headers.get("content-type") || "application/json",
                "x-proxy-status": String(resp.status)},
    });
  },
};
