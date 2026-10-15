// 通用API代理worker — api-proxy.js (10-16 KV缓存版)
const ALLOWED = [
  "guest.api.arcadia.pinnacle.com",
  "api.pinnacle.com",
  "api.the-odds-api.com",
  "api.elections.kalshi.com",
  "gamma-api.polymarket.com",
  "clob.polymarket.com",
];
const SECRET_KEY = "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R";

export default {
  async scheduled(event, env, ctx) {
    console.log("cron tick", new Date(event.scheduledTime).toISOString());
  },
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return new Response(JSON.stringify({ok: true, ts: Date.now()}),
        {headers: {"Content-Type": "application/json"}});
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
