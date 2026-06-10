"""人気薄(単勝10-15倍)複勝圏リランカー 学習スクリプト.

「人気がない馬が3着以内に好走する馬」の抽出器（精度特化・ROI目的でない）。
検証記録: memory upset_place_extraction.md (2026-06-11)
  - ウォークフォワード4fold / 2026純フォワード精度35.3% (帯base27.0%)
  - 発走前オッズ(-10分)判定でも34.8%（実運用入力で成立）
  - エッジの源泉は外部指数バッジ（吉馬2024+/netkeiba2025+のため過去foldは検証不能）

オッズを特徴に使わない logistic 回帰（市場の写像化を防ぎ、帯内の並べ替えに特化）。
アーティファクトは純JSON（serving 側は sklearn 不要・係数の内積のみ）。

実行:
    cd backend
    .venv/bin/python scripts/train_upset_reranker.py                    # 全期間学習(本番用)
    .venv/bin/python scripts/train_upset_reranker.py --holdout 20260101 # 検証レポート付き
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
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from src.indices.upset_reranker import (
    BASE_FEATURES,
    SUB_INDEX_COLUMNS,
    UPSET_BAND_MAX,
    UPSET_BAND_MIN,
    UPSET_MIN_ODDS,
)

DSN = (
    f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
    f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
    f"password={os.getenv('DB_PASSWORD')}"
)

ARTIFACT_PATH = _root / "models" / "upset_reranker.v1.json"

SQL = """
WITH m(jra,sek) AS (VALUES
 ('01','JSPK'),('02','JHKD'),('03','JFKS'),('04','JNGT'),('05','JTOK'),
 ('06','JNKY'),('07','JCKO'),('08','JKYO'),('09','JHSN'),('10','JKKR'))
SELECT r.id race_id, r.date::int date, r.head_count hc,
  re.horse_number hn,
  rr.finish_position fp, rr.win_odds, COALESCE(rr.abnormality_code,0) abn,
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
    """学習データを取得し特徴量を構築する（バックテストと同一の定義）."""
    conn = psycopg2.connect(DSN)
    try:
        df = pd.read_sql(SQL, conn, params={"start": start, "version": version})
    finally:
        conn.close()
    df = df[df.abn == 0].copy()
    df["win_odds"] = pd.to_numeric(df.win_odds, errors="coerce")
    for c in ["comp", "wp", "pp", "nk_ave", "kc_sp", "tdm", "bdm", *SUB_INDEX_COLUMNS]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["top3"] = (df.fp <= 3).astype(int)

    g = df.groupby("race_id")
    df["comp_rank"] = g["comp"].rank(ascending=False, method="min")
    df["bdm_rank"] = g["bdm"].rank(ascending=False, method="min")
    df["tdm_rank"] = g["tdm"].rank(ascending=False, method="min")
    df["nk_rank"] = g["nk_ave"].rank(ascending=False, method="min")
    df["kc_rank"] = g["kc_sp"].rank(ascending=False, method="min")
    for c in SUB_INDEX_COLUMNS:
        df[c + "_rk"] = g[c].rank(ascending=False, method="min")

    df["b_ana"] = df.ag_rank.isin(["A", "B", "C"]).astype(int)
    df["b_nk"] = (df.nk_rank <= 3).fillna(False).astype(int)
    df["b_kc"] = (df.kc_rank <= 3).fillna(False).astype(int)
    df["b_dm"] = (df.bdm_rank <= 2).fillna(False).astype(int)
    df["badge_cnt"] = df.b_ana + df.b_nk + df.b_kc + df.b_dm

    # 人気薄ユニバース（>=10倍）。n_unpop はユニバース内頭数
    uni = df[df.win_odds >= UPSET_MIN_ODDS].copy()
    uni["n_unpop"] = uni.groupby("race_id")["hn"].transform("size")
    return uni


def select_features(uni: pd.DataFrame) -> list[str]:
    """カバレッジ>=50% の特徴のみ採用（netkeiba順位等の疎な列を自動除外）."""
    candidates = list(BASE_FEATURES) + list(SUB_INDEX_COLUMNS) + [
        c + "_rk" for c in SUB_INDEX_COLUMNS
    ]
    return [f for f in candidates if uni[f].notna().mean() > 0.5]


def fit(uni: pd.DataFrame, feats: list[str]) -> dict:
    """logistic 回帰を学習し、アーティファクト dict を返す."""
    med = uni[feats].median()
    x = uni[feats].fillna(med)
    sc = StandardScaler().fit(x)
    model = LogisticRegression(max_iter=2000, C=0.5)
    model.fit(sc.transform(x), uni.top3)

    # 閾値 = 帯[10,15) 内 ns スコアの学習期 2/3 分位（上位1/3 を採用）
    band = uni[(uni.win_odds >= UPSET_BAND_MIN) & (uni.win_odds < UPSET_BAND_MAX)]
    ns_band = model.predict_proba(sc.transform(band[feats].fillna(med)))[:, 1]
    threshold = float(np.quantile(ns_band, 2 / 3))

    return {
        "version": 1,
        "model": "logistic",
        "indices_version": int(os.getenv("UPSET_INDICES_VERSION", "26")),
        "trained_at": datetime.now(UTC).isoformat(),
        "train_start": int(uni.date.min()),
        "train_end": int(uni.date.max()),
        "n_train": int(len(uni)),
        "band": [UPSET_BAND_MIN, UPSET_BAND_MAX],
        "threshold": threshold,
        "features": feats,
        "median": {f: float(med[f]) for f in feats},
        "mean": [float(v) for v in sc.mean_],
        "scale": [float(v) for v in sc.scale_],
        "coef": [float(v) for v in model.coef_[0]],
        "intercept": float(model.intercept_[0]),
    }


def score_with_artifact(artifact: dict, uni: pd.DataFrame) -> np.ndarray:
    """アーティファクトの係数で ns スコアを計算（serving と同一計算）."""
    feats = artifact["features"]
    x = uni[feats].copy()
    for f in feats:
        x[f] = x[f].fillna(artifact["median"][f])
    z = (x.values - np.array(artifact["mean"])) / np.array(artifact["scale"])
    logit = z @ np.array(artifact["coef"]) + artifact["intercept"]
    return 1.0 / (1.0 + np.exp(-logit))


def validate(artifact: dict, holdout: pd.DataFrame) -> dict:
    """ホールドアウト期間で A2/A3 運用点の精度を検証する."""
    band = holdout[
        (holdout.win_odds >= UPSET_BAND_MIN) & (holdout.win_odds < UPSET_BAND_MAX)
    ].copy()
    band["ns"] = score_with_artifact(artifact, band)
    th = artifact["threshold"]
    a2 = band[(band.ns >= th) & (band.badge_cnt >= 1)]
    a3 = band[(band.ns >= th) & (band.badge_cnt >= 2)]
    report = {
        "holdout_start": int(holdout.date.min()),
        "holdout_end": int(holdout.date.max()),
        "band_n": int(len(band)),
        "band_base": round(float(band.top3.mean()), 4),
        "a2_n": int(len(a2)),
        "a2_precision": round(float(a2.top3.mean()), 4) if len(a2) else None,
        "a3_n": int(len(a3)),
        "a3_precision": round(float(a3.top3.mean()), 4) if len(a3) else None,
    }
    return report


def main() -> None:
    """エントリポイント."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20230101", help="学習データ開始日 YYYYMMDD")
    parser.add_argument("--version", type=int, default=26, help="calculated_indices version")
    parser.add_argument(
        "--holdout", default=None,
        help="この日付以降をホールドアウトにして検証レポートを出す（本番学習では省略=全期間学習）",
    )
    parser.add_argument("--out", default=str(ARTIFACT_PATH))
    args = parser.parse_args()

    uni = load_dataset(args.start, args.version)
    print(f"universe: {len(uni)} rows / {uni.race_id.nunique()} races "
          f"({uni.date.min()}〜{uni.date.max()})")

    if args.holdout:
        cut = int(args.holdout)
        train, hold = uni[uni.date < cut], uni[uni.date >= cut]
        feats = select_features(train)
        artifact = fit(train, feats)
        artifact["validation"] = validate(artifact, hold)
        print(json.dumps(artifact["validation"], indent=2))
    else:
        feats = select_features(uni)
        artifact = fit(uni, feats)
        # 全期間学習でも直近6ヶ月の参考精度（in-sample 楽観含む）を残す
        recent = uni[uni.date >= int(pd.Timestamp.now().strftime("%Y%m%d")) - 10000]
        if len(recent):
            artifact["validation"] = validate(artifact, recent)
            artifact["validation"]["note"] = "in-sample reference (trained on full period)"

    print(f"features({len(artifact['features'])}): {artifact['features']}")
    print(f"threshold(band ns 2/3 quantile): {artifact['threshold']:.4f}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, ensure_ascii=False, indent=1))
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
