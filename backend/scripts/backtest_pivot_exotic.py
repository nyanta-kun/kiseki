"""keirin思想の移植検証②: gap12ゲート × 集中pivot型エキゾチック (JRA)。

競輪AI(keirin)の勝ち筋 = 小頭数 × pivot型集中買い目(2軸固定+3着流し3点) × gap12信頼度
× ライン構造。kisekiの過去検証は「上位5頭BOX」を頭数層別しROI 0.76-0.92(全て<1)だったが、
keirin流の「pivot集中3点(BOX非依存)」は未検証。頭数制約は外す(JRAは競輪ほど小頭数化しない)。

bet 構造:
  v26 win_probability でレース内ランク。pivot1=1位, pivot2=2位, thirds={3,4,5位}。
  - 3連複(trio): {pivot1,pivot2,X}  for X in thirds  → 3点 (5頭BOX 10点の高確率部分集合)
  - 3連単(trifecta): pivot1→pivot2→X for X in thirds → 3点 (keirin SSと同型)
  比較baseline: 5頭BOX trio (C(5,3)=10点) ... 旧検証 ROI≈0.76 の再現確認。

評価: race_payouts の実払戻(per100円)。gap12バケット別・train/test分割・ブートストラップCI。
オッズは賭け対象/集計のみで特徴量化しない(ユーザー原則)。

使い方: PYTHONPATH=. .venv/bin/python scripts/backtest_pivot_exotic.py
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

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

RNG = np.random.default_rng(20260607)

HORSE_Q = """
WITH bv AS (
  SELECT race_id, LEAST(MAX(version),26) v FROM keiba.calculated_indices
  WHERE win_probability IS NOT NULL GROUP BY race_id)
SELECT ci.race_id, r.date, r.head_count, re.horse_number hn,
       ci.win_probability wp
FROM keiba.calculated_indices ci
JOIN bv ON bv.race_id=ci.race_id AND bv.v=ci.version
JOIN keiba.race_entries re ON re.race_id=ci.race_id AND re.horse_id=ci.horse_id
JOIN keiba.races r ON r.id=ci.race_id
WHERE ci.win_probability IS NOT NULL AND re.horse_number IS NOT NULL
  AND r.date BETWEEN '20230506' AND '20260607'
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
"""

PAY_Q = """
SELECT race_id, bet_type, combination, payout
FROM keiba.race_payouts WHERE bet_type IN ('trio','trifecta')
"""


def _conn():
    dsn = (f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
           f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
           f"password={os.getenv('DB_PASSWORD')}")
    return psycopg2.connect(dsn)


def _stat(cost: np.ndarray, ret: np.ndarray) -> dict:
    """cost/ret は1レース1行(そのレースで賭けた合計コストと回収)。"""
    n = len(cost)
    if n == 0:
        return dict(n=0, hit=0, hr=0.0, roi=float("nan"), lo=0.0, hi=0.0)
    roi = ret.sum() / cost.sum()
    hits = int((ret > 0).sum())
    # ブートストラップ: レース単位リサンプルで ROI 分布
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
    cur = conn.cursor()
    cur.execute(HORSE_Q)
    cols = [c[0] for c in cur.description]
    h = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.execute(PAY_Q)
    pcols = [c[0] for c in cur.description]
    pay = pd.DataFrame(cur.fetchall(), columns=pcols)
    cur.close()
    conn.close()

    h["wp"] = h["wp"].astype(float)
    h["hn"] = h["hn"].astype(int)
    h["head_count"] = pd.to_numeric(h["head_count"], errors="coerce")
    pay["payout"] = pay["payout"].astype(float)

    # payout を race_id 別に dict 化
    trio_combo: dict[int, frozenset] = {}
    trio_pay: dict[int, float] = {}
    tri_combo: dict[int, tuple] = {}
    tri_pay: dict[int, float] = {}
    for rid, bt, comb, p in pay[["race_id", "bet_type", "combination", "payout"]].itertuples(index=False):
        try:
            nums = [int(x) for x in str(comb).replace("=", "-").split("-")]
        except ValueError:
            continue
        if bt == "trio":
            trio_combo[rid] = frozenset(nums)
            trio_pay[rid] = p
        else:
            tri_combo[rid] = tuple(nums)
            tri_pay[rid] = p

    # レース単位で評価
    rows = []
    for rid, g in h.groupby("race_id"):
        if rid not in trio_combo or rid not in tri_combo:
            continue
        g = g.sort_values("wp", ascending=False)
        hns = g["hn"].tolist()
        if len(hns) < 5:
            continue
        date = g["date"].iloc[0]
        hc = g["head_count"].iloc[0]
        p1, p2 = hns[0], hns[1]
        thirds = hns[2:5]
        top5 = hns[:5]

        actual_trio = trio_combo[rid]
        actual_tri = tri_combo[rid]

        # --- pivot 3連複: {p1,p2,X} ---
        pv_trio_cost = 3 * 100.0
        pv_trio_ret = 0.0
        for x in thirds:
            if frozenset((p1, p2, x)) == actual_trio:
                pv_trio_ret = trio_pay[rid]
        # --- pivot 3連単: p1->p2->X ---
        pv_tri_cost = 3 * 100.0
        pv_tri_ret = 0.0
        for x in thirds:
            if (p1, p2, x) == actual_tri:
                pv_tri_ret = tri_pay[rid]
        # --- baseline 5頭BOX 3連複: C(5,3)=10点 ---
        box_combos = list(combinations(top5, 3))
        box_cost = len(box_combos) * 100.0
        box_ret = trio_pay[rid] if actual_trio in {frozenset(c) for c in box_combos} else 0.0

        gap12 = float(g["wp"].iloc[0] - g["wp"].iloc[1])
        rows.append((rid, date, hc, gap12,
                     pv_trio_cost, pv_trio_ret, pv_tri_cost, pv_tri_ret, box_cost, box_ret))

    df = pd.DataFrame(rows, columns=["rid", "date", "hc", "gap12",
                                     "pvtrio_c", "pvtrio_r", "pvtri_c", "pvtri_r", "box_c", "box_r"])
    print(f"対象レース: {len(df)} (DM不問・全頭数・trio/trifecta払戻あり)")

    def split(d):
        tr = d[d["date"] <= "20250630"]
        te = d[(d["date"] >= "20250701")]
        return tr, te

    GAP_BUCKETS = [(0.0, 0.06), (0.06, 0.10), (0.10, 0.15), (0.15, 0.25), (0.25, 1.01)]

    def report(name, cost_col, ret_col):
        print(f"\n{'='*72}\n【{name}】 gap12バケット別 (pivot=2軸固定+3着流し3点)\n{'='*72}")
        for lo, hi in GAP_BUCKETS:
            sub = df[(df["gap12"] >= lo) & (df["gap12"] < hi)]
            print(f"\n gap12[{lo:.2f},{hi:.2f}):")
            for label, d in (("ALL ", sub), ("train", split(sub)[0]), ("test", split(sub)[1])):
                print(f"   {label}" + _fmt(_stat(d[cost_col].to_numpy(), d[ret_col].to_numpy())))
        # 全体
        print("\n 全レース(gapフィルタ無し):")
        for label, d in (("ALL ", df), ("train", split(df)[0]), ("test", split(df)[1])):
            print(f"   {label}" + _fmt(_stat(d[cost_col].to_numpy(), d[ret_col].to_numpy())))

    # baseline 再現
    print(f"\n{'='*72}\n[baseline] 5頭BOX 3連複 (C(5,3)=10点) — 旧検証 ROI≈0.76 の再現確認\n{'='*72}")
    for label, d in (("ALL ", df), ("train", split(df)[0]), ("test", split(df)[1])):
        print(f"   {label}" + _fmt(_stat(d["box_c"].to_numpy(), d["box_r"].to_numpy())))

    report("3連複 pivot 3点", "pvtrio_c", "pvtrio_r")
    report("3連単 pivot 3点 (keirin SS型)", "pvtri_c", "pvtri_r")


if __name__ == "__main__":
    main()
