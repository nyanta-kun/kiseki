"""毎レース1頭の人気薄推奨 複勝EVモデル 学習スクリプト。

複勝圏確率 P を logistic + isotonic 較正で算出し、複勝最低オッズ近似と掛けて
EV を出す。的中率フロア(P_cal >= floor)を満たす候補から EV 最大の1頭を選ぶ。

検証記録: memory place_ev_model.md (train<2025-07 / test 2025-07〜2026-06)
  - 採用案 EV最大+P>=0.20: 的中25.7% / 複勝ROI 0.806 CI[0.750,0.864] (cov83%)
  - 2026純フォワード: 的中26.6% / ROI 0.809 (市場最低オッズ 26.2%)
  - 較正 test ECE 0.006。ROI は ~0.81 で +EV ではない(効率市場・精度/表示用途)

アーティファクトは純JSON(serving 側は sklearn 不要)。

実行:
    cd backend
    .venv/bin/python scripts/train_place_ev_model.py                     # 全期間学習(本番用)
    .venv/bin/python scripts/train_place_ev_model.py --holdout 20250705  # 検証レポート付き
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import numpy as np
import pandas as pd
import psycopg2
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from src.betting.place_ev import MIN_PLACE_ODDS, SUB_INDEX_COLUMNS, UNDERDOG_MIN_ODDS

DSN = (
    f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
    f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
    f"password={os.getenv('DB_PASSWORD')}"
)

# P モデル特徴量の候補(serving の score_race と同一の超集合)。
# upset_reranker と違い log_odds を含む: EV には最も正確な確率が要るため。
BASE_FEATURES: tuple[str, ...] = (
    "log_odds", "pp", "wp", "comp_rank", "pp_rank", "bdm_rank", "tdm_rank",
    "kc_rank", "b_ana", "b_anaAB", "badge_cnt", "badge_any",
    "hc", "n_unpop", "is_turf", "distance",
)

SQL = """
WITH m(jra,sek) AS (VALUES
 ('01','JSPK'),('02','JHKD'),('03','JFKS'),('04','JNGT'),('05','JTOK'),
 ('06','JNKY'),('07','JCKO'),('08','JKYO'),('09','JHSN'),('10','JKKR'))
SELECT r.id race_id, r.date::int date, r.head_count hc, r.surface, r.distance,
  re.horse_number hn,
  rr.finish_position fp, rr.win_odds, rr.place_odds, COALESCE(rr.abnormality_code,0) abn,
  ci.composite_index comp, ci.win_probability wp, ci.place_probability pp,
  ci.speed_index, ci.adjusted_speed_index, ci.last_3f_index, ci.course_aptitude,
  ci.distance_aptitude, ci.position_advantage, ci.jockey_index, ci.pace_index,
  ci.rotation_index, ci.rebound_index, ci.career_phase_index, ci.distance_change_index,
  re.jvan_time_dm tdm, re.jvan_battle_dm bdm,
  nk.idx_ave nk_ave, kc.sp_score kc_sp, ag.rank ag_rank
FROM keiba.races r
JOIN keiba.race_entries re ON re.race_id=r.id
JOIN keiba.race_results rr ON rr.race_id=r.id AND rr.horse_number=re.horse_number
JOIN keiba.calculated_indices ci ON ci.race_id=r.id AND ci.horse_id=re.horse_id
  AND ci.version=%(version)s
LEFT JOIN m ON m.jra=r.course
LEFT JOIN sekito.netkeiba nk ON nk.course_code=m.sek AND nk.date=r.date::date
  AND nk.race_no=r.race_number AND nk.horse_no=re.horse_number AND nk.is_time_index=true
LEFT JOIN sekito.kichiuma kc ON kc.course_code=m.sek AND kc.date=r.date::date
  AND kc.race_no=r.race_number AND kc.horse_no=re.horse_number
LEFT JOIN sekito.anagusa ag ON ag.course_code=m.sek AND ag.date=r.date::date
  AND ag.race_no=r.race_number AND ag.horse_no=re.horse_number
WHERE r.date >= %(start)s AND r.head_count >= 8
"""


def load_dataset(start: str, version: int) -> pd.DataFrame:
    """学習データを取得し人気薄ユニバースの特徴量を構築する(serving と同一定義)。"""
    conn = psycopg2.connect(DSN)
    try:
        df = pd.read_sql(SQL, conn, params={"start": start, "version": version})
    finally:
        conn.close()
    df = df[df.abn == 0].copy()
    for c in ["win_odds", "place_odds", "comp", "wp", "pp", "nk_ave", "kc_sp",
              "tdm", "bdm", "distance", "hc", *SUB_INDEX_COLUMNS]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["top3"] = (df.fp <= 3).astype(int)  # hc>=8 限定なので複勝圏=3着以内
    df["log_odds"] = np.log(df.win_odds)
    df["is_turf"] = (df.surface == "芝").astype(int)

    g = df.groupby("race_id")
    df["comp_rank"] = g["comp"].rank(ascending=False, method="min")
    df["pp_rank"] = g["pp"].rank(ascending=False, method="min")
    df["bdm_rank"] = g["bdm"].rank(ascending=False, method="min")
    df["tdm_rank"] = g["tdm"].rank(ascending=False, method="min")
    df["nk_rank"] = g["nk_ave"].rank(ascending=False, method="min")
    df["kc_rank"] = g["kc_sp"].rank(ascending=False, method="min")
    for c in SUB_INDEX_COLUMNS:
        df[c + "_rk"] = g[c].rank(ascending=False, method="min")

    df["b_ana"] = df.ag_rank.isin(["A", "B", "C"]).astype(int)
    df["b_anaAB"] = df.ag_rank.isin(["A", "B"]).astype(int)
    df["b_nk"] = (df.nk_rank <= 3).fillna(False).astype(int)
    df["b_kc"] = (df.kc_rank <= 3).fillna(False).astype(int)
    df["b_dm"] = (df.bdm_rank <= 2).fillna(False).astype(int)
    df["badge_cnt"] = df.b_ana + df.b_nk + df.b_kc + df.b_dm
    df["badge_any"] = (df.badge_cnt > 0).astype(int)

    uni = df[df.win_odds >= UNDERDOG_MIN_ODDS].copy()
    uni["n_unpop"] = uni.groupby("race_id")["hn"].transform("size")
    return uni


def select_features(uni: pd.DataFrame) -> list[str]:
    """カバレッジ>=50% の特徴のみ採用(netkeiba順位等の疎な列を自動除外)。"""
    candidates = list(BASE_FEATURES) + list(SUB_INDEX_COLUMNS) + [
        c + "_rk" for c in SUB_INDEX_COLUMNS
    ]
    return [f for f in candidates if f in uni.columns and uni[f].notna().mean() > 0.5]


def fit_odds_impute(uni: pd.DataFrame) -> list[float]:
    """入着馬の実 place_odds から複勝最低オッズ近似係数を最小二乗でフィット。"""
    src = uni[(uni.place_odds.notna()) & (uni.place_odds > 0)]
    lo = np.log(src.win_odds.values)
    a = np.column_stack([np.ones(len(src)), lo, src.hc.values, lo * lo])
    coef, *_ = np.linalg.lstsq(a, np.log(src.place_odds.values), rcond=None)
    return [float(v) for v in coef]


def fit(uni: pd.DataFrame, feats: list[str], floor: float) -> dict:
    """logistic + isotonic 較正 + オッズ近似を学習し artifact dict を返す。"""
    med = uni[feats].median()
    x = uni[feats].fillna(med)
    sc = StandardScaler().fit(x)
    clf = LogisticRegression(max_iter=3000, C=0.5)
    clf.fit(sc.transform(x), uni.top3)

    p_raw = clf.predict_proba(sc.transform(x))[:, 1]
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(p_raw, uni.top3)
    # 較正写像を間引いて格納(serving は線形補間)。単調性は isotonic 由来で保証。
    order = np.argsort(p_raw)
    xs = p_raw[order]
    ys = iso.predict(xs)
    idx = np.unique(np.linspace(0, len(xs) - 1, 200).astype(int))
    cal_x = [float(v) for v in xs[idx]]
    cal_y = [float(v) for v in ys[idx]]

    return {
        "version": 1,
        "model": "logistic+isotonic",
        "indices_version": 26,
        "trained_at": datetime.now(UTC).isoformat(),
        "n_train": int(len(uni)),
        "min_odds": UNDERDOG_MIN_ODDS,
        "min_place_odds": MIN_PLACE_ODDS,
        "floor": floor,
        "features": feats,
        "median": {f: float(med[f]) for f in feats},
        "mean": [float(v) for v in sc.mean_],
        "scale": [float(v) for v in sc.scale_],
        "coef": [float(v) for v in clf.coef_[0]],
        "intercept": float(clf.intercept_[0]),
        "calibration": {"x": cal_x, "y": cal_y},
        "odds_impute": fit_odds_impute(uni),
    }


def _score(art: dict, df: pd.DataFrame) -> np.ndarray:
    feats = art["features"]
    x = df[feats].copy()
    for f in feats:
        x[f] = x[f].fillna(art["median"][f])
    z = (x.values - np.array(art["mean"])) / np.array(art["scale"])
    p_raw = 1.0 / (1.0 + np.exp(-(z @ np.array(art["coef"]) + art["intercept"])))
    return np.interp(p_raw, art["calibration"]["x"], art["calibration"]["y"])


def validate(art: dict, hold: pd.DataFrame) -> dict:
    """ホールドアウトで採用案(EV最大+フロア)と各ベースラインを比較する。"""
    rng = np.random.default_rng(42)
    hold = hold.copy()
    hold["p_cal"] = _score(art, hold)
    c0, c1, c2, c3 = art["odds_impute"]
    lo = np.log(hold.win_odds.values)
    hold["odds_hat"] = np.exp(c0 + c1 * lo + c2 * hold.hc.values + c3 * lo * lo)
    hold["ev"] = hold.p_cal * hold.odds_hat
    n_races = hold.race_id.nunique()

    def realized(sel):
        return np.where(sel.top3 == 1, sel.place_odds.fillna(0).values, 0.0)

    def boot(ret, n=2000):
        ms = [rng.choice(ret, len(ret), replace=True).mean() for _ in range(n)]
        return float(np.percentile(ms, 2.5)), float(np.percentile(ms, 97.5))

    def pick(score_col, floor=None):
        d = hold[hold.p_cal >= floor] if floor is not None else hold
        return hold.loc[d.groupby("race_id")[score_col].idxmax()]

    rows = {}
    for label, sel in [
        ("ev_no_floor", pick("ev")),
        ("market_min_odds", hold.loc[hold.groupby("race_id").win_odds.idxmin()]),
        ("ev_floor", pick("ev", art["floor"])),
    ]:
        ret = realized(sel)
        lo_ci, hi_ci = boot(ret)
        rows[label] = {
            "n": int(len(sel)), "coverage": round(sel.race_id.nunique() / n_races, 3),
            "hit_rate": round(float(sel.top3.mean()), 4),
            "place_roi": round(float(ret.mean()), 4), "roi_ci": [round(lo_ci, 4), round(hi_ci, 4)],
        }
    # 較正 ECE
    q = pd.qcut(hold.p_cal, 10, labels=False, duplicates="drop")
    g = hold.groupby(q).agg(pred=("p_cal", "mean"), act=("top3", "mean"), n=("p_cal", "size"))
    ece = float((np.abs(g.pred - g.act) * g.n).sum() / g.n.sum())
    return {
        "holdout_start": int(hold.date.min()), "holdout_end": int(hold.date.max()),
        "n_races": int(n_races), "ece": round(ece, 4), "strategies": rows,
    }


def main() -> None:
    """CLI エントリポイント。"""
    parser = argparse.ArgumentParser(description="Train place-EV underdog model")
    parser.add_argument("--start", default="20230101", help="学習データ開始日 YYYYMMDD")
    parser.add_argument("--version", type=int, default=26, help="指数バージョン")
    parser.add_argument("--floor", type=float, default=0.20, help="的中率フロア(較正P下限)")
    parser.add_argument("--holdout", default=None,
                        help="この日付以降をホールドアウト検証に使う YYYYMMDD")
    args = parser.parse_args()

    uni = load_dataset(args.start, args.version)
    print(f"人気薄ユニバース: {len(uni):,}頭 / {uni.race_id.nunique():,}レース "
          f"({uni.date.min()}-{uni.date.max()}) 複勝圏率={uni.top3.mean():.4f}")
    feats = select_features(uni)
    print(f"採用特徴 {len(feats)}: {feats}")

    if args.holdout:
        cut = int(args.holdout)
        tr = uni[uni.date < cut]
        te = uni[uni.date >= cut]
        art = fit(tr, feats, args.floor)
        rep = validate(art, te)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        art["validation"] = rep
        # 検証後、全期間で再学習して本番アーティファクトにする
        art_full = fit(uni, feats, args.floor)
        art_full["validation"] = rep
        art = art_full
    else:
        art = fit(uni, feats, args.floor)

    out = _root / "models" / "place_ev_model.v1.json"
    out.write_text(json.dumps(art, ensure_ascii=False, indent=1))
    print(f"保存: {out} (n_train={art['n_train']:,} floor={art['floor']} "
          f"odds_impute={[round(c, 3) for c in art['odds_impute']]})")


if __name__ == "__main__":
    main()
