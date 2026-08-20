#!/usr/bin/env python3
"""【N-7】予測オッズで低配当レースを事前に外すと「2倍以上で的中」がどこまで上がるか。

## なぜこれを測るのか

ユーザーの体感（[[keirin_7c7s_hit_experience_2026_08_20]]）:

    全出走に対する「2倍以上で的中」は 7C 14.4% / 7S 12.3% ＝ 7〜8回に1回。
    的中時の払戻中央値は 7C 1.60倍 / 7S 1.53倍で、**的中の6割強が 1.x倍**。
    🔴 精度が上位2割の週でも体感は伴わなかった＝**的中率を上げても体験は改善しない**。

さらに [[keirin_irregular_layer_screening_2026_08_20]] で「市場を出し抜いて穴を取る」
経路が閉じた（4定義とも市場のほうが上手く当てる）。**予測精度で殴る道が無い以上、
残る打ち手は「どの推奨を出すか」を予測配当で選ぶこと**しかない。

そこで KPI を「的中率」から **「2倍以上で的中した率」** へ置き換えて掃引する。

## `exp_expected_payout_band_by_rank.py`（2026-08-19）との違い

土台は同じ（picks_history の実際の買い目から復元 → 本番と同じ `stakes_for_combos`
で配分 → 確定オッズで採点）。変えたのは2点:

| | 既存（08-19） | 本スクリプト |
|---|---|---|
| 配分・想定払戻に使う板 | **朝の板**（`wt_odds_snapshot` morning） | **予測オッズ**（`odds_prediction`） |
| KPI | 表示的中（払戻 >= 賭け金） | **2倍以上で的中**（払戻 >= 2×賭け金） |

🔴 **朝の板では母集団が作れない。** 実測で朝の板が買う点すべてに揃うレースは **8.9%**
（[[keirin_odds_availability_by_posttime_2026_08_07]]）で、しかも
`wt_odds_snapshot` は **2026-06-08 以降しか無い**。既存スクリプトが
`all(c in board)` で落としている分がそのまま選択バイアスになる。
予測オッズは構造だけから作れるので全レースで引ける。

🟢 予測オッズの学習終端は **2025-12** なので 2026 の評価は honest。
   レース間比較に耐えることも確認済み（[[keirin_predicted_odds_cross_race_2026_08_20]]）。

## 測る量

    想定払戻(下限) = min_i (予測オッズ_i × 賭け金_i) ÷ 予算
      ＝「当たっても最悪これしか返らない」倍率。**発走前に分かる**（朝7:00 の入稿時点で計算可）

    素の的中     = 買い目が当たった
    表示的中     = 払戻 >= 賭け金（ガミでない）
    **2倍以上的中 = 払戻 >= 2×賭け金**   ← KPI

## 出力

閾値 N を掃引して「想定払戻(下限) < N のレースを外す」と何が起きるかを出す。
**残る件数と 2倍以上的中率はトレードオフ**なので、率だけでなく
**1日あたりの残り件数**を必ず併記する（件数が消えたら商品にならない）。

DB は読み取り専用 SELECT のみ。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_gami_cut_by_predicted_odds.py \
        --ranks RANK_7C,RANK_7S --from 2026-01-01 --to 2026-08-19
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src import odds_prediction  # noqa: E402
from src.database import get_connection  # noqa: E402
from src.rebuild_stakes import stakes_for_combos  # noqa: E402

BUDGET = 10_000
#: 掃引する足切り閾値（想定払戻(下限)がこの倍率未満のレースを外す）
CUTS = [0.0, 1.0, 1.2, 1.4, 1.5, 1.6, 1.8, 2.0, 2.5, 3.0]

#: 🔴 honest な三連複オッズモデル（train_end 2025-12-31）。
#   本番の `data/models` は **train_end 2026-08-04** で、2026 の評価に使うと
#   in-sample になる。
#   ⚠️ 2026-08-21 から `odds_prediction.assert_model_is_honest()` が
#      **差し替え忘れを機械的に弾く**（この定数はその既定値でしかない）。
HONEST_ODDS_DIR = REPO / "data" / "backup" / "odds_model_20260816"

#: 的中した目に限った `確定オッズ ÷ 予測オッズ` の分位（2026-01〜08・7車・honest モデル実測）。
#  予測オッズ自体は不偏（全目の中央 1.02〜1.08）だが、**当たった目だけ系統的に低く出る**
#  （勝者の呪い）。ガミはこの条件つき分布で決まるので、判定にはこちらを使う。
#  ⚠️ 歪みは**高オッズほど大きい**（10〜20倍 -0.164 / 20〜50倍 -0.218）。
HIT_COND = {            # (予測オッズ上限, 中央, p25)
    5.0:   (1.02, 0.89),
    10.0:  (0.96, 0.77),
    20.0:  (0.91, 0.69),
    1e9:   (0.85, 0.66),
}


def haircut_factor(pred_odds: float, mode: str) -> float:
    """ガミ判定に使う予測オッズの割引率。**配分には使わない。**

    `landing_weights` の重みは `1/オッズ` に比例するので、レース内一律の係数では
    配分は動かないが、**帯依存の係数は比率を変えて買い方まで変えてしまう**。
    そのため割引は想定払戻(下限)の評価だけに掛ける。
    """
    if mode == "none":
        return 1.0
    if mode == "u06":                       # ユーザー提案: 10倍以下を 0.6 倍
        return 0.6 if pred_odds <= 10.0 else 1.0
    idx = 0 if mode == "hitmed" else 1      # hitmed=中央 / hitp25=p25
    for hi, vals in HIT_COND.items():
        if pred_odds < hi:
            return vals[idx]
    return 1.0


def _parse(s):
    return [int(x) for x in re.split(r"[-=>]+", str(s)) if x.strip().isdigit()]


def axes_legs(combo: str):
    """`7=3-1,2,4,5,6` → ([7,3], [1,2,4,5,6])。三連単・固定目は対象外。"""
    s = (combo or "").split(" ")[0]
    if s.startswith("三単") or "-" not in s:
        return None
    head, tail = s.split("-", 1)
    ax = [int(x) for x in re.split(r"=", head) if x.strip().isdigit()]
    legs = [int(x) for x in re.split(r",", tail) if x.strip().isdigit()]
    return (ax, legs) if len(ax) == 2 and legs else None


def load_picks(d1: str, d2: str, ranks: list[str]):
    """picks_history の買い目 + 確定オッズ + p3 + 着順をまとめて読む。"""
    with get_connection() as conn:
        q = ",".join("?" * len(ranks))
        cur = conn.execute(
            f"SELECT split_part(race_key,'#',1) rk, race_date, rank, pred_combo "
            f"FROM picks_history WHERE race_date BETWEEN ? AND ? AND bet_amount > 0 "
            f"  AND rank IN ({q})", (d1, d2, *ranks))
        picks = [dict(rk=x[0], date=str(x[1]), rank=x[2], combo=x[3])
                 for x in cur.fetchall()]
        keys = sorted({p["rk"] for p in picks})
        fin: dict = defaultdict(dict)
        od: dict = defaultdict(dict)
        p3: dict = defaultdict(dict)
        for i in range(0, len(keys), 700):
            ch = keys[i:i + 700]
            ph = ",".join("?" * len(ch))
            for rk, fn, pp, fo in conn.execute(
                    f"SELECT race_key, frame_no, pred_top3_pct, finish_order "
                    f"FROM wt_entries WHERE race_key IN ({ph})", ch).fetchall():
                if pp is not None:
                    p3[rk][int(fn)] = float(pp) / 100.0
                if fo:
                    fin[rk][int(fn)] = int(fo)
            for rk, cb, o in conn.execute(
                    f"SELECT race_key, combination, odds_value FROM wt_odds "
                    f"WHERE race_key IN ({ph}) AND bet_type='trio' AND odds_value>0",
                    ch).fetchall():
                od[rk][frozenset(_parse(cb))] = float(o)
    return picks, fin, od, p3


def build_rows(picks, fin, od, p3, rank: str, mode: str):
    rows, skips = [], defaultdict(int)
    for p in picks:
        if p["rank"] != rank:
            continue
        got = axes_legs(p["combo"])
        if not got:
            skips["買い目が三連複でない"] += 1
            continue
        od_final = od.get(p["rk"]) or {}
        probs = p3.get(p["rk"]) or {}
        if not od_final or not probs:
            skips["確定オッズ/p3 が無い"] += 1
            continue
        top3 = {n for n, o in (fin.get(p["rk"]) or {}).items() if o <= 3}
        if len(top3) != 3:
            skips["着順が3着まで揃わない"] += 1
            continue
        (a1, a2), legs = got
        combos = [frozenset({a1, a2, t}) for t in legs]
        if not all(c in od_final for c in combos):
            skips["確定オッズに買い目が無い"] += 1
            continue
        try:
            board = odds_prediction.predicted_trio_board(p["rk"])
        except odds_prediction.OddsPredictionUnavailable:
            skips["予測オッズを作れない"] += 1
            continue
        if not all(c in board for c in combos):
            skips["予測盤面に買い目が無い"] += 1
            continue
        try:
            st = stakes_for_combos(a1, a2, combos, probs, board=board, budget=BUDGET)
        except Exception:
            skips["配分に失敗"] += 1
            continue
        bet = sum(st.values())
        if bet <= 0:
            skips["賭け金0"] += 1
            continue
        # 🔴 `stakes_for_combos` のキーは**目（frozenset）**であって車番ではない。
        #    配分（st）は**素の予測オッズ**で決めたものをそのまま使い、
        #    割引は想定払戻の評価にだけ掛ける。
        exp_lo = min(board[c] * haircut_factor(board[c], mode) * st[c] / bet
                     for c in combos)
        win = frozenset(top3)
        pay = int(od_final[win] * st[win]) if win in st else 0
        rows.append(dict(date=p["date"], exp_lo=exp_lo, bet=bet, pay=pay,
                         hit=pay > 0, net=pay >= bet and pay > 0,
                         hit2=pay >= 2 * bet and pay > 0))
    return rows, skips


def report(rank: str, rows, skips, d1: str, d2: str):
    if not rows:
        print(f"\n{rank}: 対象0件  除外内訳={dict(skips)}")
        return
    days = len({r["date"] for r in rows})
    tot = len(rows)
    print(f"\n===== {rank}  {tot}R / {days}日  [{d1}〜{d2}] =====")
    if skips:
        print(f"  除外内訳: {dict(skips)}")
    print(f"  {'足切り':>8}{'残R':>7}{'残存%':>7}{'R/日':>7}"
          f"{'素の的中%':>10}{'表示的中%':>11}{'2倍以上%':>10}{'ROI%':>8}{'倍率中央':>9}")
    print("  " + "-" * 78)
    for cut in CUTS:
        b = [r for r in rows if r["exp_lo"] >= cut]
        if not b:
            continue
        rat = [r["pay"] / r["bet"] for r in b if r["hit"]]
        lbl = "なし" if cut <= 0 else f"{cut:.1f}倍"
        print(f"  {lbl:>8}{len(b):>7}{100 * len(b) / tot:>7.1f}{len(b) / days:>7.2f}"
              f"{100 * sum(r['hit'] for r in b) / len(b):>10.1f}"
              f"{100 * sum(r['net'] for r in b) / len(b):>11.1f}"
              f"{100 * sum(r['hit2'] for r in b) / len(b):>10.1f}"
              f"{100 * sum(r['pay'] for r in b) / sum(r['bet'] for r in b):>8.1f}"
              f"{(statistics.median(rat) if rat else 0):>9.2f}")
    base = 100 * sum(r["hit2"] for r in rows) / tot
    print(f"\n  ベース（足切りなし）の2倍以上的中 = {base:.1f}%"
          f"  / 1日あたり {tot / days:.2f}R")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ranks", default="RANK_7C,RANK_7S")
    ap.add_argument("--from", dest="d1", default="2026-01-01")
    ap.add_argument("--to", dest="d2", default="2026-08-19")
    ap.add_argument("--haircut", default="none",
                    choices=["none", "u06", "hitmed", "hitp25"],
                    help="ガミ判定に使う予測オッズの割引: none=無補正 / "
                         "u06=10倍以下を0.6倍 / hitmed,hitp25=的中時条件つき分位")
    ap.add_argument("--production-odds-model", action="store_true",
                    help="本番の三連複オッズモデルを使う（train_end 2026-08-04＝"
                         "2026年の評価では in-sample。比較目的でのみ）")
    a = ap.parse_args()

    if not a.production_odds_model:
        odds_prediction.MODEL_DIR = HONEST_ODDS_DIR
        odds_prediction.META_PATH = HONEST_ODDS_DIR / "odds_trio_meta.json"
        odds_prediction._MODEL_CACHE.clear()
        odds_prediction._META_CACHE = None
    te = odds_prediction.load_meta()["per_n_car"]["7"]["train_end"]
    print(f"[odds] 三連複オッズモデル train_end = {te} / 割引 = {a.haircut}", flush=True)
    # 🔴 差し替えたつもりで in-sample になっていないかを機械的に検査する。
    #    `--production-odds-model` は「承知の上で使う」明示の逃げ道。
    if not a.production_odds_model:
        odds_prediction.assert_model_is_honest(a.d1, who="exp_gami_cut")
    ranks = a.ranks.split(",")

    print(f"[load] picks_history {a.d1}〜{a.d2} {ranks} ...", flush=True)
    picks, fin, od, p3 = load_picks(a.d1, a.d2, ranks)
    print(f"[load] {len(picks):,}件", flush=True)

    for rank in ranks:
        rows, skips = build_rows(picks, fin, od, p3, rank, a.haircut)
        report(rank, rows, skips, a.d1, a.d2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
