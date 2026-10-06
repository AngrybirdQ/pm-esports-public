# pm-esports-public

Polymarket 电竞盘口公开数据仓（GitHub Actions 海外 runner 采集，CN 本机经
api.github.com 拉取入库）。**public 仓 = Actions 免费分钟无上限**，支撑高频
采集与多源扩展。

## 为什么是 public 新仓（2026-10-03 从私有仓迁移）

- 私有仓 2000 免费 minutes/月卡住提频；public 仓无限。
- 旧私有仓 git 历史含 watchlist（策略敏感），不能直接转 public，故新建干净仓。
- **策略敏感的任务单不落仓**：watchlist（即将开赛的关注市场）与 betmatch
  请求（本地注单清单）由本地 dispatcher 通过 `repository_dispatch` 的
  `client_payload` 即时下发，云端 done-state（去重键/已回填清单）不敏感、留仓内。

## 结构

- `scripts/collect.py` — 主采集器（stdlib only）：
  - 多策略事件发现（tag_slug/tag_id/public-search，全部尝试记入运行报告）
  - 快照 `data/YYYY/MM/YYYY-MM-DD.jsonl`（每活跃市场一行/轮）
  - 场外异动传感器（`*.trades.jsonl` + `*.anoms.jsonl`）：大单 / 1h 单边净流 /
    45-90min 胜率急动
  - CLOB order book（`*.books.jsonl`）：watchlist + top 成交市场的
    best bid/ask、mid、点差、前三档深度 —— 可执行价，CLV/滑点用
  - 价格历史回填（`data/history/`）与 betmatch 匹配回填（payload 任务单驱动）
- `scripts/collect_side.py` — 旁源采集（best-effort，失败只记报告）：
  Kalshi（第二预测市场 oracle）、Liquipedia（各游戏 Matches hub 原始
  wikitext，本地解析）、Pinnacle guest 盘口 → `*.side.jsonl`
- `state/latest.json` / `state/last_run_report.json` / `state/last_side_report.json`

## 触发

- 主心跳：本地 `pm_dispatch.py`（CN 可达 api.github.com）→
  `repository_dispatch (collect)`，payload 带 watchlist + betmatch_reqs；
  频率分级：比赛进行/临场 2h 内 5 分钟、白天 15 分钟、夜间 30 分钟。
- 冗余：仓内 schedule（历史证明对该账号不可靠，仅留作备份层）+
  freshness 巡检自愈 dispatch。

## 本机侧（~/polymarket-sync/）

`pull_and_ingest.py`（每 10 分钟）拉 data/**/*.jsonl 幂等入本地 PG：
`pm_odds_snapshots` / `pm_trades` / `pm_anomalies` / `pm_order_books` /
`pm_side_observations` / `pm_price_history` / `pm_betmatch_map`。
旧私有仓 `AngrybirdQ/polymarket-esports-odds` 保留为存档（workflow 已停）。
