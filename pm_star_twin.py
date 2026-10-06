#!/usr/bin/env python3
"""星源孪生解析共享模块 — pm_star_twin.py (2026-10-04, v2评分版)

唯一权威的 pm事件 ↔ 星源行 解析实现。此前三处副本(compare/dashboard/临时
脚本)各自演化, 先后产出三代盲区: 按um.id查(键空间错) → 精确lower队名查
(写法错) → token匹配带stale变量(循环变量错)。凡需要跨命名空间的, 一律
import本模块, 禁止再写内联解析 (audit_star_resolution.py 会扫指纹)。

约定基线(全表统一, 2026-10-04以写入方代码定案):
  upcoming_matches.start_time   = UTC裸值 (collect/feed/battle_card/tz_utils一致)
  odds_snapshots.snapshot_time  = UTC裸值
  star行 external_match_id      = 大数字(世博命名空间), source='star'
  pm行  external_match_id       = 'pm<eid>', source='polymarket'
  pm_feed合成行                  = external 'pm*' (跨oracle比对必须排除, 否则循环自比)

v2: 候选评分(name_score双侧取弱+时间距离), MIN_SCORE以下直接拒配——
宁缺勿错(GAM Esports→LGD Gaming假阳性教训, 2026-10-04)。
"""
import difflib
import re

STOP = {"team", "esports", "gaming", "the", "vs", "fc", "esport"}
MIN_SCORE = 0.45          # 低于此分不建映射(返回None) — 错配比缺配危害大
TIME_BONUS_H = 48.0       # 时间距离衰减尺度(小时)


def toks(s):
    return [t for t in re.findall(r"[A-Za-z0-9]{2,}", s or "")
            if t.lower() not in STOP]


def name_score(a, b):
    """归一化名相似度(与align_pm_matches同源逻辑): difflib + 词序无关 + 包含奖励"""
    def norm1(s):
        s = re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
        return " ".join(t for t in s.split())
    na, nb = norm1(a), norm1(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    r = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = " ".join(sorted(na.split())), " ".join(sorted(nb.split()))
    r = max(r, difflib.SequenceMatcher(None, ta, tb).ratio())
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short) >= 4 and short in long_:
        r = max(r, 0.93)
    elif short and set(short.split()) <= set(long_.split()) \
            and min(len(t) for t in short.split()) >= 3:
        r = max(r, 0.90)   # "LGD" ⊆ "LGD Gaming"
    return round(r, 4)


def pair_score(a1, b1, a2, b2):
    """双向取最优: 主客顺序两边任意"""
    direct = min(name_score(a1, a2), name_score(b1, b2))
    swapped = min(name_score(a1, b2), name_score(b1, a2))
    return max(direct, swapped)


def selftest(cur, sample=5):
    """金丝雀自检: 对pm_match_map中高分本地映射的样例, 验证解析器能否
    复现同一星源行。返回 dict(n_ok, n_bad, n_skip, bad_examples)。"""
    out = {"n_ok": 0, "n_bad": 0, "n_skip": 0, "bad_examples": []}
    # 真值设计: 星源行自身就是真值(external=own)。解析器拿到该行的
    # 队名+时间应返回自身; 返回同token的别行=token污染回归信号。
    cur.execute("""
        SELECT id, team_a, team_b, start_time, external_match_id
        FROM upcoming_matches
        WHERE source = 'star' AND external_match_id IS NOT NULL
          AND start_time > now() - interval '20 hours'
          AND start_time < now() + interval '30 hours'
        ORDER BY random() LIMIT %s""", (sample * 3,))
    rows = cur.fetchall()
    checked = 0
    for lid, ta, tb, st, own in rows:
        if checked >= sample:
            break
        ext, _sst, score, _n = resolve_twin_full(cur, ta, tb, st)
        checked += 1
        if ext == own:
            out["n_ok"] += 1
        else:
            out["n_bad"] += 1
            if len(out["bad_examples"]) < 3:
                out["bad_examples"].append(
                    f"{ta} vs {tb}: 解析={ext} 实际={own} score={score}")
    return out


def resolve_twin_full(cur, team_a, team_b, start_utc_naive,
                      min_score=MIN_SCORE):
    """评分版: 返回 (star_ext, star_start, score, n_cand)。
    找不到达标孪生返回 (None, None, 0.0, n_cand)。"""
    pa = ["%" + t + "%" for t in toks(team_a)]
    pb = ["%" + t + "%" for t in toks(team_b)]
    if not pa or not pb:
        return None, None, 0.0, 0
    cur.execute("""
        SELECT external_match_id, team_a, team_b, start_time
        FROM upcoming_matches
        WHERE source = 'star'
          AND ((team_a ILIKE ANY(%s) AND team_b ILIKE ANY(%s))
            OR (team_a ILIKE ANY(%s) AND team_b ILIKE ANY(%s)))
          AND start_time > %s::timestamp - interval '30 hours'
          AND start_time < %s::timestamp + interval '40 hours'
        ORDER BY abs(extract(epoch FROM (start_time - %s::timestamp)))
        LIMIT 5""",
        (pa, pb, pb, pa, start_utc_naive, start_utc_naive, start_utc_naive))
    cands = cur.fetchall()
    best = (None, None, 0.0)
    for ext, sa, sb, sst in cands:
        ns = pair_score(team_a, team_b, sa, sb)
        if sst is not None and start_utc_naive is not None:
            dh = abs((sst - start_utc_naive).total_seconds()) / 3600.0
            sc = round(ns * max(0.5, 1.0 - dh / TIME_BONUS_H), 4)
        else:
            sc = ns
        if sc > best[2]:
            best = (ext, sst, sc)
    ext, sst, sc = best
    if sc < min_score:
        return None, None, sc, len(cands)
    return ext, sst, sc, len(cands)


def resolve_twin(cur, team_a, team_b, start_utc_naive):
    """兼容签名: 返回 (star_ext, star_start) 或 (None, None)。"""
    ext, sst, _sc, _n = resolve_twin_full(cur, team_a, team_b, start_utc_naive)
    return ext, sst
