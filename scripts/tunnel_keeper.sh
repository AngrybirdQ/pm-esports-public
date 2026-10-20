#!/bin/bash
# tunnel_keeper.sh — trycloudflared免费公网域名守护(10-20)
# 职责: ① 8976 pin_kv_serve与cloudflared quick tunnel常驻(挂了自动拉起)
#       ② tunnel URL变更(quick tunnel重启会换URL)→同步GitHub仓库变量TUNNEL_URL
#         → Actions工作流可用{{ vars.TUNNEL_URL }}读本地pin数据(实测200, 不受
#         workers.dev数据中心403影响)
# cron: */5 * * * * /home/Admin/polymarket-sync/tunnel_keeper.sh >> .../logs/tunnel_keeper.log
CF_BIN=/home/Admin/tools/cloudflared
LOG=/home/Admin/polymarket-sync/logs/cloudflared.log
STATE=/home/Admin/.config/polymarket-sync/tunnel_url.txt
GH_TOK=$(head -1 /home/Admin/.config/polymarket-sync/token 2>/dev/null)
REPO=AngryBirdQ/pm-esports-public

# ① 8976服务
if ! curl -s --noproxy '*' --max-time 5 http://127.0.0.1:8976/health > /dev/null 2>&1; then
  pkill -f "pin_kv_serve[.]py" 2>/dev/null
  nohup /usr/bin/python3 /home/Admin/polymarket-sync/pin_kv_serve.py \
    >> /home/Admin/polymarket-sync/logs/pin_kv_serve.log 2>&1 &
  sleep 2
  echo "$(date -u +%FT%TZ) restarted pin_kv_serve"
fi

# ② cloudflared
if ! pgrep -f "cloudflared tunne[l]" > /dev/null; then
  nohup "$CF_BIN" tunnel --url http://localhost:8976 --no-autoupdate \
    >> "$LOG" 2>&1 &
  sleep 8
  echo "$(date -u +%FT%TZ) restarted cloudflared"
fi

# ③ URL同步: 日志尾部取最新trycloudflare域名
URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOG" 2>/dev/null | tail -1)
[ -z "$URL" ] && exit 0
OLD=$(cat "$STATE" 2>/dev/null)
if [ "$URL" = "$OLD" ]; then exit 0; fi
if ! curl -s --noproxy '*' --max-time 15 "$URL/health" | grep -q '"ok": *true'; then
  exit 0  # URL还没就绪, 下轮再同步
fi
if [ -n "$GH_TOK" ]; then
  CODE=$(curl -s --noproxy '*' --max-time 20 -o /dev/null -w '%{http_code}' -X PATCH \
    -H "Authorization: Bearer $GH_TOK" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$REPO/actions/variables/TUNNEL_URL" \
    -d "{\"name\":\"TUNNEL_URL\",\"value\":\"$URL\"}")
  if [ "$CODE" = "404" ]; then
    curl -s --noproxy '*' --max-time 20 -o /dev/null -X POST \
      -H "Authorization: Bearer $GH_TOK" -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/$REPO/actions/variables" \
      -d "{\"name\":\"TUNNEL_URL\",\"value\":\"$URL\"}"
    CODE=create
  fi
  echo "$(date -u +%FT%TZ) var sync http=$CODE"
fi
echo "$URL" > "$STATE"
echo "$(date -u +%FT%TZ) TUNNEL_URL=$URL synced"
