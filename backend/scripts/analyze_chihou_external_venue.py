"""地方 外部指数コンセンサスの場別有効性 再検証 (Bの分析目的)。

背景: [[chihou_external_consensus]] は「3指標一致でhit49%・園田単ROI1.207」を報告したが、
[[chihou_improvement_investigation_2026_06_07]] の調査で園田1.207はn=45・3ヶ月の薄サンプルと判明。
NAR netkeiba/kichiuma は2023-2026の全履歴(30万行)が揃ったので、場別ROIを
**ブートストラップCI + train/test時間分割**で厳密に再評価し、実妙味のある場/条件を確定する。

シグナル:
  (A) 外部のみコンセンサス: netkeiba idx_ave 1位 ∧ kichiuma sp_score 1位 (同一馬)
      = 市場直交・kisekiモデル非依存(in-sample問題なし)。
  (B) 3指標一致: (A) ∧ composite(v10) 1位。 ※compositeは全期間in-sampleの楽観に注意。
評価: 該当馬の 単勝/複勝 ROI(race_results.win_odds/place_odds)・hit・ブートCI・場別・train(〜2025-06)/test(2025-07〜)。

使い方: PYTHONPATH=. .venv/bin/python scripts/analyze_chihou_external_venue.py
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
       rr.horse_number hn, rr.finish_position fp,
       rr.win_odds, rr.place_odds,
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
  AND r.head_count >= 6
"""


def _roi_ci(pick: pd.DataFrame, odds_col: str, win_cond) -> dict:
    """pick=1レース1行(該当馬)。win_cond=的中判定(bool series)。odds_col=配当倍率。"""
    n = len(pick)
    if n == 0:
        return dict(n=0, hit=0, hr=0.0, roi=float("nan"), lo=0.0, hi=0.0)
    ret = np.where(win_cond, pick[odds_col].to_numpy(), 0.0)
    cost = np.ones(n)
    hits = int(win_cond.sum())
    boot = []
    idx = np.arange(n)
    for _ in range(2000):
        s = RNG.choice(idx, n, replace=True)
        boot.append(ret[s].sum() / cost[s].sum())
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return dict(n=n, hit=hits, hr=hits * 100.0 / n, roi=float(ret.mean()), lo=float(lo), hi=float(hi))


def _fmt(s: dict) -> str:
    if s["n"] == 0:
        return "n=0"
    star = "★" if s["lo"] > 1.0 else ("◯" if s["roi"] > 1.0 else " ")
    return (f"n={s['n']:>5} hit={s['hr']:5.1f}% ROI={s['roi']:.3f} "
            f"CI[{s['lo']:.2f},{s['hi']:.2f}] {star}")


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

    for c in ("nk_idx", "comp", "win_odds", "place_odds", "kc_sp", "fp"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["win"] = (df["fp"] == 1).astype(int)
    df["place"] = (df["fp"] <= 3).astype(int)

    # レース内ランク(高い=1位)。indexごとに非null馬のみ対象。
    df["nk_rank"] = df.groupby("race_id")["nk_idx"].rank(ascending=False, method="min")
    df["kc_rank"] = df.groupby("race_id")["kc_sp"].rank(ascending=False, method="min")
    df["cp_rank"] = df.groupby("race_id")["comp"].rank(ascending=False, method="min")

    # 各レースで両外部指数が揃う(=コンセンサス判定可能)レースに限定
    valid = df.groupby("race_id").filter(
        lambda g: g["nk_idx"].notna().any() and g["kc_sp"].notna().any()
    )
    print(f"対象: 全行={len(df)} / 外部指数揃いレース行={len(valid)} "
          f"({valid['race_id'].nunique()}レース・2023-2026 NAR)")

    sig_ext = valid[(valid["nk_rank"] == 1) & (valid["kc_rank"] == 1)].copy()
    sig_tri = sig_ext[sig_ext["cp_rank"] == 1].copy()

    def report(sig: pd.DataFrame, name: str):
        print(f"\n{'='*74}\n【{name}】(1レース最大1頭・同一馬が両/三指標1位)\n{'='*74}")
        # 同一レースで複数該当(タイ)を1行に: race_idで先頭
        sig1 = sig.sort_values("win_odds").groupby("race_id").head(1)
        for split, d in (("全期間", sig1),
                         ("train(〜25/6)", sig1[sig1["date"] <= "20250630"]),
                         ("test(25/7〜)", sig1[sig1["date"] >= "20250701"])):
            w = _roi_ci(d, "win_odds", d["win"] == 1)
            p = _roi_ci(d, "place_odds", d["place"] == 1)
            print(f"  {split:12} 単: {_fmt(w)}")
            print(f"  {' '*12} 複: {_fmt(p)}")

    report(sig_ext, "外部のみコンセンサス: netkeiba1位 ∧ kichiuma1位")
    report(sig_tri, "3指標一致: + composite(v10)1位 ※compositeはin-sample")

    # 場別(外部のみコンセンサス・複勝/単勝・train/testで生存する場を探す)
    print(f"\n{'='*74}\n【場別】外部のみコンセンサス 単勝/複勝ROI (train/test時間分割で生存確認)\n{'='*74}")
    sig1 = sig_ext.sort_values("win_odds").groupby("race_id").head(1)
    for venue in sorted(sig1["course_name"].dropna().unique()):
        v = sig1[sig1["course_name"] == venue]
        if len(v) < 30:
            continue
        tr = v[v["date"] <= "20250630"]
        te = v[v["date"] >= "20250701"]
        wa = _roi_ci(v, "win_odds", v["win"] == 1)
        wt = _roi_ci(te, "win_odds", te["win"] == 1)
        pa = _roi_ci(v, "place_odds", v["place"] == 1)
        pt = _roi_ci(te, "place_odds", te["place"] == 1)
        print(f"\n {venue}")
        print(f"   単 全:{_fmt(wa)}")
        print(f"   単 test:{_fmt(wt)}")
        print(f"   複 全:{_fmt(pa)}")
        print(f"   複 test:{_fmt(pt)}")


if __name__ == "__main__":
    main()
