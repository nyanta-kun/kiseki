"""keirin思想の地方(NAR)移植検証: 真のOOSで pivot型エキゾチック + win/place を評価。

JRAでは pivot集中×gap12 は OOSで黒字化せず(backtest_pivot_exotic.py)。地方は
keirinと同じ半効率市場かつ小頭数(8-12が大半)なので効く可能性がある。

⚠️ 本番 chihou v10(version=10)は全期間学習=全期間インサンプル。罠を避けるため
本スクリプトは train_chihou_prod_lgb の学習基盤を再利用し:
  1. cutoff(20250630)まででモデル学習(is_top3 / is_win 2ヘッド)
  2. held-out(20250701+)を予測 ← 真のOOS
  3. その期間で pivot trio/trifecta・5頭BOX・win・place の ROI を実払戻で評価

pivot = v10流: top3スコアでレース内ランク。p1=1位,p2=2位,thirds=3-5位。
  trio {p1,p2,X} 3点 / trifecta p1->p2->X 3点 / 5頭BOX trio 10点(baseline)。
オッズは賭け対象/集計のみ(特徴量化しない)。

使い方: PYTHONPATH=. .venv/bin/python scripts/backtest_chihou_pivot_exotic.py
"""

from __future__ import annotations

import os
import sys
from itertools import combinations
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_root.parent / ".env")

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

from scripts.train_chihou_prod_lgb import (  # noqa: E402
    FEATURES,
    NUM_ROUNDS,
    PARAMS,
    fetch_hist,
    prep,
)

RNG = np.random.default_rng(20260607)
CUTOFF = "20250630"
TEST_START = "20250701"
TEST_END = "20260607"

# BASE_QUERY と同一だが re.horse_number を追加(払戻照合用)
MY_QUERY = """
SELECT
    ci.race_id, r.date, r.course_name, r.prize_1st AS curr_prize,
    re.horse_id, re.horse_number, r.surface, r.condition, r.distance, r.head_count,
    re.frame_number, re.horse_age, re.weight_carried,
    COALESCE(re.horse_weight, 500) AS horse_weight,
    COALESCE(re.weight_change, 0)  AS weight_change,
    COALESCE(ci.speed_index, 50.0)       AS speed_index,
    COALESCE(ci.last3f_index, 50.0)      AS last3f_index,
    COALESCE(ci.jockey_index, 50.0)      AS jockey_index,
    COALESCE(ci.rotation_index, 50.0)    AS rotation_index,
    COALESCE(ci.last_margin_index, 50.0) AS last_margin_index,
    rr.finish_position, rr.win_odds
FROM chihou.calculated_indices ci
JOIN chihou.races r ON r.id = ci.race_id
JOIN chihou.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN chihou.race_results rr ON rr.race_id = ci.race_id AND rr.horse_number = re.horse_number
WHERE ci.version = 9
  AND r.course != '83' AND r.head_count >= 6
  AND r.date BETWEEN %(start)s AND %(end)s
  AND COALESCE(rr.abnormality_code, 0) = 0 AND rr.finish_position IS NOT NULL
ORDER BY r.date, ci.race_id
"""


def _conn():
    dsn = (f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
           f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
           f"password={os.getenv('DB_PASSWORD')}")
    return psycopg2.connect(dsn)


def _my_fetch(conn, start, end):
    cur = conn.cursor()
    cur.execute(MY_QUERY, {"start": start, "end": end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    return df


def _stat(cost: np.ndarray, ret: np.ndarray) -> dict:
    n = len(cost)
    if n == 0:
        return dict(n=0, hit=0, hr=0.0, roi=float("nan"), lo=0.0, hi=0.0)
    roi = ret.sum() / cost.sum()
    hits = int((ret > 0).sum())
    idx = np.arange(n)
    boot = []
    for _ in range(2000):
        s = RNG.choice(idx, n, replace=True)
        c = cost[s].sum()
        boot.append(ret[s].sum() / c if c > 0 else 0.0)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return dict(n=n, hit=hits, hr=hits * 100.0 / n, roi=roi, lo=lo, hi=hi)


def _fmt(s: dict) -> str:
    if s["n"] == 0:
        return "  (n=0)"
    star = "★" if s["lo"] > 1.0 else ("◯" if s["roi"] > 1.0 else " ")
    return (f"  n={s['n']:>5} 的中={s['hit']:>5} hit={s['hr']:5.1f}% "
            f"ROI={s['roi']:.3f} CI[{s['lo']:.2f},{s['hi']:.2f}] {star}")


def main() -> None:
    conn = _conn()
    df_hist = fetch_hist(conn)
    print("学習データ取得中...")
    tr = prep(conn, _my_fetch(conn, "20240101", CUTOFF), df_hist)
    te = prep(conn, _my_fetch(conn, TEST_START, TEST_END), df_hist)
    print(f"train {tr['race_id'].nunique()}R / test {te['race_id'].nunique()}R")

    fp_tr = pd.to_numeric(tr["finish_position"], errors="coerce")
    Xtr = tr[FEATURES].values.astype(float)
    Xte = te[FEATURES].values.astype(float)
    m_top3 = lgb.train(PARAMS, lgb.Dataset(Xtr, (fp_tr <= 3).astype(int).values, feature_name=FEATURES),
                       num_boost_round=NUM_ROUNDS)
    m_win = lgb.train(PARAMS, lgb.Dataset(Xtr, (fp_tr == 1).astype(int).values, feature_name=FEATURES),
                      num_boost_round=NUM_ROUNDS)
    te = te.copy()
    te["s_top3"] = m_top3.predict(Xte)
    te["s_win"] = m_win.predict(Xte)
    te["hn"] = te["horse_number"].astype(int)
    te["fp"] = pd.to_numeric(te["finish_position"], errors="coerce")
    te["odds"] = pd.to_numeric(te["win_odds"], errors="coerce")

    # 払戻取得 (test レースのみ)
    rids = tuple(int(x) for x in te["race_id"].unique())
    cur = conn.cursor()
    cur.execute("""SELECT race_id,bet_type,combination,payout FROM chihou.race_payouts
                   WHERE race_id IN %s AND bet_type IN ('trio','trifecta','win','place')""", (rids,))
    trio_c, trio_p, tri_c, tri_p = {}, {}, {}, {}
    win_p, place_p = {}, {}
    for rid, bt, comb, p in cur.fetchall():
        comb = str(comb)
        if bt == "trio":
            try:
                trio_c[rid] = frozenset(int(x) for x in comb.replace("=", "-").split("-"))
                trio_p[rid] = float(p)
            except ValueError:
                pass
        elif bt == "trifecta":
            try:
                tri_c[rid] = tuple(int(x) for x in comb.replace("=", "-").split("-"))
                tri_p[rid] = float(p)
            except ValueError:
                pass
        elif bt == "win":
            try:
                win_p.setdefault(rid, {})[int(comb)] = float(p)
            except ValueError:
                pass
        elif bt == "place":
            try:
                place_p.setdefault(rid, {})[int(comb)] = float(p)
            except ValueError:
                pass
    cur.close()
    conn.close()

    rows = []
    for rid, g in te.groupby("race_id"):
        if rid not in trio_c or rid not in tri_c:
            continue
        g = g.sort_values("s_top3", ascending=False)
        hns = g["hn"].tolist()
        if len(hns) < 5:
            continue
        p1, p2 = hns[0], hns[1]
        thirds = hns[2:5]
        top5 = hns[:5]
        gap12 = float(g["s_top3"].iloc[0] - g["s_top3"].iloc[1])

        pv_trio_r = sum(trio_p[rid] for x in thirds if frozenset((p1, p2, x)) == trio_c[rid])
        pv_tri_r = sum(tri_p[rid] for x in thirds if (p1, p2, x) == tri_c[rid])
        box = {frozenset(c) for c in combinations(top5, 3)}
        box_r = trio_p[rid] if trio_c[rid] in box else 0.0

        # win/place: win頭でランク
        gw = g.sort_values("s_win", ascending=False)
        win_pick = int(gw["hn"].iloc[0])
        win_fp = float(gw["fp"].iloc[0])
        win_ret = win_p.get(rid, {}).get(win_pick, 0.0) if win_fp == 1 else 0.0
        place_ret = place_p.get(rid, {}).get(win_pick, 0.0)  # 該当馬がplace圏ならpayout存在
        win_odds_pick = float(gw["odds"].iloc[0]) if pd.notna(gw["odds"].iloc[0]) else None

        rows.append((rid, gap12, 300.0, pv_trio_r, 300.0, pv_tri_r, len(box) * 100.0, box_r,
                     100.0, win_ret, 100.0, place_ret, win_odds_pick))

    df = pd.DataFrame(rows, columns=["rid", "gap12", "pvtrio_c", "pvtrio_r", "pvtri_c", "pvtri_r",
                                     "box_c", "box_r", "win_c", "win_r", "place_c", "place_r", "win_odds"])
    print(f"\nOOS評価レース: {len(df)} (test {TEST_START}〜{TEST_END}・真のOOS)")

    GAP = [(0.0, 0.06), (0.06, 0.10), (0.10, 0.15), (0.15, 0.25), (0.25, 1.01)]

    def by_gap(name, cc, rc):
        print(f"\n{'='*70}\n【{name}】gap12別 (OOS)\n{'='*70}")
        for lo, hi in GAP:
            sub = df[(df["gap12"] >= lo) & (df["gap12"] < hi)]
            print(f" gap12[{lo:.2f},{hi:.2f})" + _fmt(_stat(sub[cc].to_numpy(), sub[rc].to_numpy())))
        print(" 全体          " + _fmt(_stat(df[cc].to_numpy(), df[rc].to_numpy())))

    print(f"\n{'='*70}\n[baseline] 5頭BOX 3連複 (10点) OOS\n{'='*70}")
    print(" 全体          " + _fmt(_stat(df["box_c"].to_numpy(), df["box_r"].to_numpy())))
    print(f"\n{'='*70}\n[単勝/複勝] モデル1位 (地方=半効率の確認) OOS\n{'='*70}")
    print(" 単勝 model1位 " + _fmt(_stat(df["win_c"].to_numpy(), df["win_r"].to_numpy())))
    print(" 複勝 model1位 " + _fmt(_stat(df["place_c"].to_numpy(), df["place_r"].to_numpy())))
    # 単勝×高オッズ(穴)
    for thr in (5.0, 10.0):
        sub = df[df["win_odds"] >= thr]
        print(f" 単勝 model1位×odds≥{thr:>4.0f}" + _fmt(_stat(sub["win_c"].to_numpy(), sub["win_r"].to_numpy())))

    by_gap("3連複 pivot 3点", "pvtrio_c", "pvtrio_r")
    by_gap("3連単 pivot 3点(keirin SS型)", "pvtri_c", "pvtri_r")


if __name__ == "__main__":
    main()
