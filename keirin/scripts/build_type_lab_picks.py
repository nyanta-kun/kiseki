#!/usr/bin/env python3
"""型ラボの買い目を作って `keirin.type_lab_picks` へ書く（2026-08-27 新設）。

    # ペーパー検証（過去・vintage walk-forward の予測を使う）
    python scripts/build_type_lab_picks.py --mode paper --from 2026-01-01 --to 2026-08-26

    # 実地検証（当日・本番モデル）
    python scripts/build_type_lab_picks.py --mode live --date 2026-08-27

🔴 **既存商品には一切触らない。** 書き込むのは `type_lab_picks` だけで、
   `picks_history` / `netkeirin_submissions` / 入稿経路は読むことすらしない。
🔴 型判定・買い目・配分の正本は `src/type_lab.py`。**ここには規則を書かない**
   （ペーパーと実地で別物になるのを構造的に防ぐ）。

## paper と live の違いは「予測をどこから取るか」だけ

| | 3着内率/1着率 | 三連単の予測オッズ |
|---|---|---|
| paper | vintage walk-forward（`/tmp/race_type_board.npz`） | `odds_tf_n7`（train_end 2025-12-31） |
| live  | 本番モデル（`lgbm_wt_eval` / `lgbm_wt_win`） | `odds_prediction_tf.predict_board` |

⚠️ paper の台が無ければ `python scripts/build_race_type_board.py` で作る。
⚠️ **三連複の予測オッズは三連単板から導く**（Σ_perm 1/PO の逆数）。
   確定オッズとの中央比 0.967・±2倍以内 90.5% で、本番の予測オッズ精度と同水準。
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection            # noqa: E402
from src.type_lab import (                          # noqa: E402
    BUDGET, PLANS, allocate, build_legs, mean_expected_payout,
    min_expected_payout, plans_for, race_shape, rule_version,
)

PERMS = list(itertools.permutations(range(1, 8), 3))
PAYBACK = 0.75
N_ENTRIES = 7                     # 型ラボは 7車立て専用（設計・検証とも7車）


# ───────────────────────── 共通: 1レースぶんを行にする ─────────────────────────

def rows_for_race(meta: dict, cars: dict, tf_odds: dict, tf_prob: dict,
                  mode: str) -> list[dict]:
    """1レースから、その型の全プランぶんの行を作る。"""
    shape = race_shape(
        {c: v["p3"] for c, v in cars.items()},
        {c: v["line_group"] for c, v in cars.items()},
        {c: v["line_pos"] for c, v in cars.items()},
        {c: v["style"] for c, v in cars.items()},
        {c: v["race_point"] for c, v in cars.items()},
        {c: v["behind"] for c, v in cars.items()},
        meta.get("day_index") or 0,
    )
    if shape is None:
        return []
    trio_odds, trio_prob = _fold_to_trio(tf_odds, tf_prob)
    out = []
    for plan in plans_for(shape.type_label):
        odds = trio_odds if plan.bet_type == "trio" else tf_odds
        prob = trio_prob if plan.bet_type == "trio" else tf_prob
        legs = build_legs(shape, plan, odds, prob)
        if not legs:
            continue
        stakes = allocate(legs, odds, prob, plan)
        if not stakes:
            continue
        detail = [
            {"combo": _combo_str(c, plan.bet_type),
             "stake": int(stakes[c]),
             "pred_odds": round(float(odds[c]), 2),
             "prob": round(float(prob.get(c, 0.0)), 6)}
            for c in legs
        ]
        out.append(dict(
            race_key=meta["race_key"], race_date=meta["race_date"],
            venue_name=meta.get("venue_name"), race_no=meta.get("race_no"),
            race_type=meta.get("race_type"), n_entries=N_ENTRIES,
            day_index=meta.get("day_index"),
            type_label=shape.type_label, axis_sum=round(shape.axis_sum, 4),
            arare=shape.arare, gap=round(shape.gap, 4),
            axis1=shape.order[0], axis2=shape.order[1],
            # 🔴 **並びを行へ焼き付ける**。後から `wt_entries` を引き直しても
            #    モデルが再学習されていれば別の並びになり、答え合わせにならない。
            p3_order="-".join(str(c) for c in shape.order),
            mode=mode, plan_key=plan.key, bet_type=plan.bet_type,
            n_legs=len(legs), budget=BUDGET,
            legs=json.dumps(detail, ensure_ascii=False),
            pred_mean_payout=round(mean_expected_payout(stakes, odds), 1),
            pred_min_payout=round(min_expected_payout(stakes, odds), 1),
            rule_version=rule_version(),
        ))
    return out


def _combo_str(c, bet_type: str) -> str:
    return ("=".join(str(x) for x in sorted(c)) if bet_type == "trio"
            else "-".join(str(x) for x in c))


def _fold_to_trio(tf_odds: dict, tf_prob: dict) -> tuple[dict, dict]:
    """三連単の予測オッズ・確率を三連複へ畳む。

    PO_perm = 払戻率/p_perm なので Σ_perm 1/PO_perm = P(trio)/払戻率。
    よって三連複の予測オッズ = 払戻率/P(trio) = 1/Σ_perm(1/PO_perm)。
    🔴 `払戻率 / Σ(1/PO)` と書くと払戻率を二重に掛けて 0.75 倍ずれる
       （2026-08-27 に実際に踏んで中央比 0.725 で気づいた）。
    """
    q, p = defaultdict(float), defaultdict(float)
    for perm, o in tf_odds.items():
        if not o or o <= 0:
            continue
        s = frozenset(perm)
        q[s] += 1.0 / float(o)
        p[s] += float(tf_prob.get(perm, 0.0))
    return ({s: 1.0 / v for s, v in q.items() if v > 0}, dict(p))


# ───────────────────────── paper（vintage 台） ─────────────────────────

def run_paper(date_from: str, date_to: str) -> list[dict]:
    import numpy as np
    path = Path("/tmp/race_type_board.npz")
    if not path.exists():
        raise SystemExit("[type_lab] /tmp/race_type_board.npz がありません。"
                         "`python scripts/build_race_type_board.py` で作ってください")
    zf = np.load(path, allow_pickle=True)
    # 🔴 **NpzFile を添字アクセスのたびに引いてはいけない。**
    #    `z["PO"][i]` は毎回 30MB の配列を丸ごと伸長するので、
    #    レース×買い目のループに入れると桁違いに遅くなる（2026-08-27 に実際に踏んだ）。
    z = {k: zf[k] for k in ("KEY", "DATE", "OKPRED", "P3", "PO", "PROB")}
    keys = [str(k) for k in z["KEY"]]
    dates = [str(d) for d in z["DATE"]]
    sel = [i for i, d in enumerate(dates) if date_from <= d <= date_to]
    print(f"[paper] 対象 {len(sel):,}R  {date_from}〜{date_to}")

    meta_all = _load_race_meta([keys[i] for i in sel])
    ent_all = _load_entries([keys[i] for i in sel])
    out = []
    for i in sel:
        rk = keys[i]
        if not z["OKPRED"][i]:
            continue
        ent = ent_all.get(rk)
        if not ent or len(ent) != N_ENTRIES:
            continue
        cars = {c: dict(p3=float(z["P3"][i][c - 1]), **ent[c]) for c in ent}
        tf_odds = {PERMS[t]: float(z["PO"][i][t]) for t in range(210)
                   if np.isfinite(z["PO"][i][t]) and z["PO"][i][t] > 0}
        tf_prob = {PERMS[t]: float(z["PROB"][i][t]) for t in range(210)}
        m = meta_all.get(rk)
        if not m:
            continue
        out.extend(rows_for_race(m, cars, tf_odds, tf_prob, "paper"))
    return out


# ───────────────────────── live（本番モデル） ─────────────────────────

def predict_p3_pw(day: str, eval_model: str = "lgbm_wt_eval",
                  win_model: str = "lgbm_wt_win",
                  day_to: str | None = None) -> tuple[dict, dict]:
    """指定期間の {race_key: {車番: 3着内率}} と {race_key: {車番: 1着率}}。

    🔴 `run_live` と**答え合わせのバックフィル**の両方がここを呼ぶ。
       別々に書くと「行を作ったときの並び」と「後から復元した並び」がずれる。
    ⚠️ `day_to` を渡すと `day`〜`day_to` をまとめて1回で予測する。特徴量の構築は
       期間の長さにほとんど比例しない（履歴の読み込みが支配的）ので、
       過去のバックフィルは**1日ずつ回すより桁で速い**。同じモデルで良い期間
       （＝同じ vintage 窓の中）でだけまとめること。
    """
    from src.models.trainer import load_model
    from src.preprocessing.feature_wt import (
        build_features_wt, load_raw_data_wt, prepare_X,
    )

    feats = build_features_wt(load_raw_data_wt(min_date=day, max_date=day_to or day))
    if feats is None or not len(feats):
        return {}, {}
    X = prepare_X(feats)
    p3v = load_model(eval_model).predict_proba(X)[:, 1]
    pwv = load_model(win_model).predict_proba(X)[:, 1]
    p3, pw = defaultdict(dict), defaultdict(dict)
    for rk, fn, a, b in zip(feats["race_key"], feats["frame_no"], p3v, pwv):
        p3[rk][int(fn)] = float(a)
        pw[rk][int(fn)] = float(b)
    return dict(p3), dict(pw)


def run_live(day: str, eval_model: str = "lgbm_wt_eval",
             win_model: str = "lgbm_wt_win", mode: str = "live") -> list[dict]:
    """指定日の買い目を作る。

    eval_model / win_model を差し替えると**その日に使ってよいモデル**で組める。
    ペーパーを vintage で埋めるとき（`run_paper_vintage`）に使う。
    """
    # 🔴 import 元は `build_7t3_candidates.py` と揃える。
    #    `src.features_wt` は存在しない（正しくは `src.preprocessing.feature_wt`）。
    from src import odds_prediction_tf as odds_tf

    keys = _keys_of_date(day)
    if not keys:
        print(f"[live] {day}: 7車立てのレースがありません")
        return []
    print(f"[live] {day}: 7車立て {len(keys)}R")
    meta_all = _load_race_meta(keys)
    ent_all = _load_entries(keys)

    p3, pw = predict_p3_pw(day, eval_model, win_model)
    if not p3:
        print("[live] 特徴量が作れませんでした")
        return []

    out = []
    for rk in keys:
        ent = ent_all.get(rk)
        if not ent or len(ent) != N_ENTRIES or rk not in p3:
            continue
        if len(p3[rk]) != N_ENTRIES:
            continue
        cars = {c: dict(p3=p3[rk][c], **ent[c]) for c in ent if c in p3[rk]}
        try:
            board = odds_tf.predict_board(sorted(p3[rk]), p3[rk], pw[rk],
                                          {c: ent[c]["meta"] for c in ent})
        except Exception as e:                                   # noqa: BLE001
            print(f"  {rk}: 予測オッズを作れず skip（{e}）")
            continue
        tf_odds = {tuple(k): float(v) for k, v in board.items() if v and v > 0}
        tf_prob = _pl_board(p3[rk], pw[rk])
        m = meta_all.get(rk)
        if not m:
            continue
        out.extend(rows_for_race(m, cars, tf_odds, tf_prob, mode))
    return out


def run_paper_vintage(date_from: str, date_to: str) -> list[dict]:
    """ペーパーを**月次 vintage モデル**で埋める（`/tmp/race_type_board.npz` を使わない）。

    月 M のレースは `lgbm_wt_{eval,win}_mYYMM`（学習は M の前月末まで）でだけ採点する
    ＝ `src/wt_vintage_config.monthly_windows()` の契約。
    🔴 **本番モデルを過去へ当ててはいけない**（全期間学習なので in-sample になる）。
       `assert_vintage_for_past` と同じ思想で、ここではモデル名を月から導いて固定する。

    ⚠️ 既存の 2026-01-01〜08-04 は四半期 walk-forward（`wf_preds_*.pkl`）由来で、
       こちらは月次 vintage。**どちらも学習はレースより前**だが再学習の刻みが違う。
       同じ `mode='paper'` に両方が入ることを承知して読むこと。
    """
    from src.wt_vintage_config import monthly_windows

    out: list[dict] = []
    for w_from, w_to, eval_model, win_model in monthly_windows():
        lo = max(w_from, date_from)
        hi = min(w_to, date_to)
        if lo > hi:
            continue
        print(f"[paper/vintage] {lo}〜{hi}  {eval_model} / {win_model}", flush=True)
        d = date.fromisoformat(lo)
        end = date.fromisoformat(hi)
        while d <= end:
            out.extend(run_live(d.isoformat(), eval_model, win_model, mode="paper"))
            d += timedelta(days=1)
    return out


def _pl_board(p3: dict, pw: dict) -> dict:
    """三連単の買い目確率（位置別合成 PL）。正本は `strategy_wt.rank_7t3_blend_probs`。"""
    from src.strategy_wt import rank_7t3_blend_probs
    return rank_7t3_blend_probs(sorted(p3), pw, p3)


# ───────────────────────── DB ─────────────────────────

def _keys_of_date(day: str) -> list[str]:
    with get_connection() as c:
        return [r[0] for r in c.execute(
            "SELECT race_key FROM wt_races WHERE race_date = ? AND n_entries = ? "
            "ORDER BY race_key", (day, N_ENTRIES)).fetchall()]


def _load_race_meta(keys: list[str]) -> dict:
    out = {}
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            q = ("SELECT r.race_key, r.race_date, r.race_no, r.race_type, r.day_index, "
                 "       v.name "
                 "FROM wt_races r LEFT JOIN venue_info v "
                 "  ON v.venue_code = substr(r.race_key, 10, 2) "
                 f"WHERE r.race_key IN ({','.join('?' * len(ch))})")
            for row in c.execute(q, ch).fetchall():
                out[row[0]] = dict(race_key=row[0], race_date=str(row[1]),
                                   race_no=row[2], race_type=row[3],
                                   day_index=row[4], venue_name=row[5])
    return out


_META_COLS = ("race_point", "line_group", "line_size", "line_pos", "is_line_leader",
              "player_class", "style", "first_rate", "second_rate", "third_rate")


def _load_entries(keys: list[str]) -> dict:
    out = defaultdict(dict)
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            q = ("SELECT race_key, frame_no, line_group, line_pos, style, race_point, "
                 "       ex_left_behind_pct, line_size, is_line_leader, player_class, "
                 "       first_rate, second_rate, third_rate, prediction_mark "
                 f"FROM wt_entries WHERE race_key IN ({','.join('?' * len(ch))})")
            for r in c.execute(q, ch).fetchall():
                d = dict(zip(
                    ("race_key", "frame_no", "line_group", "line_pos", "style",
                     "race_point", "behind", "line_size", "is_line_leader",
                     "player_class", "first_rate", "second_rate", "third_rate",
                     "mark"), r))
                fn = int(d["frame_no"])
                d["race_point"] = float(d["race_point"] or 0)
                d["behind"] = float(d["behind"] or 0)
                d["meta"] = {k: d.get(k) for k in _META_COLS if k in d} | {"mark": d["mark"]}
                out[d["race_key"]][fn] = d
    return dict(out)


COLS = ("race_key race_date venue_name race_no race_type n_entries day_index "
        "type_label axis_sum arare gap axis1 axis2 p3_order mode plan_key bet_type "
        "n_legs budget legs pred_mean_payout pred_min_payout rule_version").split()


def save(rows: list[dict]) -> int:
    if not rows:
        return 0
    ph = ",".join("?" * len(COLS))
    upd = ", ".join(f"{c}=excluded.{c}" for c in COLS
                    if c not in ("race_key", "plan_key", "mode"))
    sql = (f"INSERT INTO type_lab_picks ({', '.join(COLS)}) VALUES ({ph}) "
           f"ON CONFLICT (race_key, plan_key, mode) DO UPDATE SET {upd}, "
           f"generated_at = NOW()")
    with get_connection() as c:
        for r in rows:
            c.execute(sql, tuple(r[k] for k in COLS))
        c.commit()
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("paper", "live"), required=True)
    ap.add_argument("--from", dest="date_from")
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--date")
    ap.add_argument("--models", choices=("board", "vintage"), default="board",
                    help="paper の予測をどこから取るか。board=/tmp/race_type_board.npz "
                         "（四半期 walk-forward）/ vintage=月次 vintage モデル")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.mode == "paper":
        if not (a.date_from and a.date_to):
            raise SystemExit("--from と --to が要ります")
        rows = (run_paper_vintage(a.date_from, a.date_to) if a.models == "vintage"
                else run_paper(a.date_from, a.date_to))
    else:
        day = a.date or date.today().isoformat()
        rows = run_live(day)
    from collections import Counter
    print("プラン別:", dict(Counter(r["plan_key"] for r in rows)))
    print("型別:", dict(Counter(r["type_label"] for r in rows)))
    if a.dry_run:
        print(f"[dry-run] {len(rows)} 行（保存しない）")
        return
    print(f"保存 {save(rows)} 行  rule_version={rule_version()}")


if __name__ == "__main__":
    main()
