"""地方 外部指数の「穴馬(value/disagreement)」角度の検証 (kichiuma/netkeiba)。

(3)の本命コンセンサス(両指数1位)は低オッズ本命に帰着しROI<1だった。確立済みのエッジ機構=
「高オッズ × 直交シグナル」([[betting_strategy_findings]] JRA 指数1位×NBコース1位×odds≥7→OOS1.18,
sweet_spot odds≥10∧EV∧バッジ)。外部指数の本来の妙味は「外部は高評価だが市場が人気薄に置く馬」。
本スクリプトはその外部穴馬/disagreement角度を厳密検証する(ブートCI+OOS時間分割)。

シグナル(各々 該当馬の単勝/複勝ROI):
  K1   : kichiuma sp_score 1位 (市場非依存・外部評価トップ)
  N1   : netkeiba idx_ave 1位
  KN1  : K1 ∧ N1 (外部コンセンサス)
  +odds閾: 上記 ∧ win_odds≥{7,10} (市場ディスカウント=穴)
  DIS  : 外部トップ(KN1) ∧ composite(v10) 非1位 (=モデルと不一致・外部だけが推す)
  KABS : kichiuma sp_score 最大かつ絶対値≥{65,70} ∧ odds≥10 (絶対能力高×人気薄)

ROIは race_results.win_odds(NAR充足) で単勝、place_odds(2026偏重・参考) で複勝。
OOS時間分割 train(〜2025-06)/test(2025-07〜)・レース単位ブートCI・drop1。

使い方: PYTHONPATH=. .venv/bin/python scripts/analyze_chihou_external_darkhorse.py
"""

from __future__ import annotations

import os
import sys
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

QUERY = """
SELECT r.id race_id, r.date, r.course_name,
       rr.horse_number hn, rr.finish_position fp, rr.win_odds, rr.place_odds,
       nk.idx_ave nk_idx, kc.sp_score kc_sp, ci.composite_index comp
FROM chihou.races r
JOIN keiba.racecourse_map rc ON rc.netkeiba_id = r.course
JOIN chihou.race_results rr ON rr.race_id = r.id
LEFT JOIN sekito.netkeiba nk
  ON nk.course_code=rc.code AND nk.date=to_date(r.date,'YYYYMMDD')
     AND nk.race_no=r.race_number AND nk.horse_no=rr.horse_number
LEFT JOIN sekito.kichiuma kc
  ON kc.course_code=rc.code AND kc.date=to_date(r.date,'YYYYMMDD')
     AND kc.race_no=r.race_number AND kc.horse_no=rr.horse_number
LEFT JOIN chihou.calculated_indices ci
  ON ci.race_id=r.id AND ci.horse_id=rr.horse_id AND ci.version=10
WHERE LEFT(rc.code,1)='N' AND rc.netkeiba_id <> '65'
  AND r.date BETWEEN '20230101' AND '20260607'
  AND COALESCE(rr.abnormality_code,0)=0 AND rr.finish_position IS NOT NULL
  AND r.head_count >= 6 AND rr.win_odds > 0
"""


def _stat(pick: pd.DataFrame) -> dict:
    """pick=該当馬行(複数レース)。単勝: win_odds配当。"""
    n = len(pick)
    if n == 0:
        return dict(n=0, hit=0, hr=0.0, roi=float("nan"), d1=float("nan"), lo=0.0, hi=0.0)
    ret = np.where(pick["fp"].to_numpy() == 1, pick["win_odds"].to_numpy(), 0.0)
    hits = int((pick["fp"] == 1).sum())
    drop1 = (ret.sum() - ret.max()) / (n - 1) if n > 1 else float("nan")
    boot = [ret[RNG.choice(n, n, replace=True)].mean() for _ in range(2000)]
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return dict(n=n, hit=hits, hr=hits * 100.0 / n, roi=float(ret.mean()),
                d1=float(drop1), lo=float(lo), hi=float(hi))


def _fmt(s: dict) -> str:
    if s["n"] == 0:
        return "n=0"
    star = "★" if s["lo"] > 1.0 else ("◯" if s["roi"] > 1.0 else " ")
    return (f"n={s['n']:>5} 的中={s['hit']:>4} hit={s['hr']:5.1f}% "
            f"ROI={s['roi']:.3f} drop1={s['d1']:.3f} CI[{s['lo']:.2f},{s['hi']:.2f}] {star}")


def main() -> None:
    dsn = (f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
           f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
           f"password={os.getenv('DB_PASSWORD')}")
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(QUERY)
    cols = [c[0] for c in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    conn.close()
    for c in ("nk_idx", "kc_sp", "comp", "win_odds", "place_odds", "fp"):
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["nk_rank"] = df.groupby("race_id")["nk_idx"].rank(ascending=False, method="min")
    df["kc_rank"] = df.groupby("race_id")["kc_sp"].rank(ascending=False, method="min")
    df["cp_rank"] = df.groupby("race_id")["comp"].rank(ascending=False, method="min")
    valid = df.groupby("race_id").filter(lambda g: g["kc_sp"].notna().any() and g["nk_idx"].notna().any())
    print(f"対象: 外部指数揃いNAR {valid['race_id'].nunique()}レース (2023-2026)")

    def one_per_race(d):
        return d.sort_values("win_odds", ascending=False).groupby("race_id").head(1)

    def report(mask, name):
        sig = one_per_race(valid[mask])
        print(f"\n【{name}】")
        for label, dd in (("全 ", sig),
                          ("tr ", sig[sig["date"] <= "20250630"]),
                          ("te ", sig[sig["date"] >= "20250701"])):
            print(f"   {label} {_fmt(_stat(dd))}")

    K1 = valid["kc_rank"] == 1
    N1 = valid["nk_rank"] == 1
    KN1 = K1 & N1
    DIS = KN1 & (valid["cp_rank"] > 1)
    print("\n" + "=" * 78 + "\n外部穴馬 / disagreement 角度 (単勝・OOS時間分割)\n" + "=" * 78)
    report(K1, "kichiuma1位 (全オッズ)")
    report(K1 & (valid["win_odds"] >= 7), "kichiuma1位 ∧ odds≥7 (外部穴)")
    report(K1 & (valid["win_odds"] >= 10), "kichiuma1位 ∧ odds≥10 (外部穴)")
    report(N1 & (valid["win_odds"] >= 10), "netkeiba1位 ∧ odds≥10 (外部穴)")
    report(KN1 & (valid["win_odds"] >= 7), "外部コンセンサス ∧ odds≥7")
    report(KN1 & (valid["win_odds"] >= 10), "外部コンセンサス ∧ odds≥10")
    report(DIS & (valid["win_odds"] >= 7), "DIS:外部top∧composite非1位 ∧ odds≥7 (モデルと不一致)")
    report(K1 & (valid["kc_sp"] >= 65) & (valid["win_odds"] >= 10), "kichiuma1位 ∧ sp_score≥65 ∧ odds≥10")
    report(K1 & (valid["kc_sp"] >= 70) & (valid["win_odds"] >= 10), "kichiuma1位 ∧ sp_score≥70 ∧ odds≥10")


if __name__ == "__main__":
    main()
