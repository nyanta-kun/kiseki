"""地方競馬 人気薄(単勝10-15倍)複勝圏リランカー 学習スクリプト.

検証記録: memory upset_place_extraction.md 地方編 (2026-06-11)
  - train/val/test 3分割再実施: test A2=37.5% CI[0.352,0.399] / 発走前-10分 30.7% (市場23.3%)
  - ウォークフォワード3fold 34.8/35.1/37.1%で全fold市場超え
  - v10 モデル確率は train_range が全期間(in-sample)のため特徴に使わない

実行:
    cd backend
    .venv/bin/python scripts/train_chihou_upset_reranker.py                    # 本番用(全期間)
    .venv/bin/python scripts/train_chihou_upset_reranker.py --holdout 20260101 # 検証付き
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

from src.indices.chihou_upset import (
    CHIHOU_IDX_COLUMNS,
    CHIHOU_UPSET_BAND_MAX,
    CHIHOU_UPSET_BAND_MIN,
)

DSN = (
    f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
    f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
    f"password={os.getenv('DB_PASSWORD')}"
)

ARTIFACT_PATH = _root / "models" / "chihou_upset_reranker.v1.json"

# 採用閾値の分位。train/val/test 3分割の検証期間で {0.50, 2/3, 0.75} から選定
THRESHOLD_QUANTILE: float = 0.75

SQL = r"""
SELECT ci.race_id, r.date::int date, r.head_count hc,
  re.horse_number hn, rr.finish_position fp, rr.win_odds,
  COALESCE(rr.abnormality_code,0) abn,
  ci.speed_index, ci.last3f_index, ci.jockey_index, ci.rotation_index,
  ci.last_margin_index,
  CASE WHEN nk.idx_ave ~ '^-?[0-9]+\*?$'
       THEN regexp_replace(nk.idx_ave, '\*', '')::float ELSE NULL END AS nk_idx,
  kc.sp_score kc_sp
FROM chihou.calculated_indices ci
JOIN chihou.races r ON r.id = ci.race_id
JOIN chihou.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN chihou.race_results rr ON rr.race_id = ci.race_id AND rr.horse_number = re.horse_number
JOIN sekito.racecourse rc ON rc.netkeiba_id = r.course
LEFT JOIN sekito.netkeiba nk
  ON nk.course_code=rc.code AND nk.date=to_date(r.date,'YYYYMMDD')
     AND nk.race_no=r.race_number AND nk.horse_no=re.horse_number AND nk.is_time_index = true
LEFT JOIN sekito.kichiuma kc
  ON kc.course_code=rc.code AND kc.date=to_date(r.date,'YYYYMMDD')
     AND kc.race_no=r.race_number AND kc.horse_no=re.horse_number
WHERE ci.version = %(version)s AND r.course != '83' AND r.head_count >= 8
  AND r.date >= %(start)s AND rr.finish_position IS NOT NULL
"""


def load_dataset(start: str, version: int) -> pd.DataFrame:
    """学習データを取得し特徴量を構築する（バックテストと同一定義）."""
    conn = psycopg2.connect(DSN)
    try:
        df = pd.read_sql(SQL, conn, params={"start": start, "version": version})
    finally:
        conn.close()
    df = df[df.abn == 0].copy()
    for c in ["win_odds", "kc_sp", "nk_idx", *CHIHOU_IDX_COLUMNS]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["top3"] = (df.fp <= 3).astype(int)

    g = df.groupby("race_id")
    for c in CHIHOU_IDX_COLUMNS:
        df[c + "_rk"] = g[c].rank(ascending=False, method="min")
    df["kc_sp_rk"] = g["kc_sp"].rank(ascending=False, method="min")
    df["nk_idx_rk"] = g["nk_idx"].rank(ascending=False, method="min")
    df["b_kc"] = (df.kc_sp_rk <= 3).fillna(False).astype(int)
    df["b_nk"] = (df.nk_idx_rk <= 3).fillna(False).astype(int)
    df["badge_cnt"] = df.b_kc + df.b_nk

    uni = df[df.win_odds >= CHIHOU_UPSET_BAND_MIN].copy()
    uni["n_unpop"] = uni.groupby("race_id")["hn"].transform("size")
    return uni


FEATURES: list[str] = (
    list(CHIHOU_IDX_COLUMNS)
    + [c + "_rk" for c in CHIHOU_IDX_COLUMNS]
    + ["kc_sp_rk", "nk_idx_rk", "b_kc", "b_nk", "badge_cnt", "hc", "n_unpop"]
)


def fit(uni: pd.DataFrame) -> dict:
    """logistic 回帰を学習し、アーティファクト dict を返す."""
    med = uni[FEATURES].median()
    x = uni[FEATURES].fillna(med)
    sc = StandardScaler().fit(x)
    model = LogisticRegression(max_iter=2000, C=0.5)
    model.fit(sc.transform(x), uni.top3)

    band = uni[
        (uni.win_odds >= CHIHOU_UPSET_BAND_MIN) & (uni.win_odds < CHIHOU_UPSET_BAND_MAX)
    ]
    ns_band = model.predict_proba(sc.transform(band[FEATURES].fillna(med)))[:, 1]
    threshold = float(np.quantile(ns_band, THRESHOLD_QUANTILE))

    return {
        "version": 1,
        "model": "logistic",
        "indices_version": int(os.getenv("CHIHOU_UPSET_INDICES_VERSION", "10")),
        "trained_at": datetime.now(UTC).isoformat(),
        "train_start": int(uni.date.min()),
        "train_end": int(uni.date.max()),
        "n_train": int(len(uni)),
        "band": [CHIHOU_UPSET_BAND_MIN, CHIHOU_UPSET_BAND_MAX],
        "threshold": threshold,
        "features": FEATURES,
        "median": {f: float(med[f]) for f in FEATURES},
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
    """ホールドアウト期間で A2 運用点の精度を検証する."""
    band = holdout[
        (holdout.win_odds >= CHIHOU_UPSET_BAND_MIN)
        & (holdout.win_odds < CHIHOU_UPSET_BAND_MAX)
    ].copy()
    band["ns"] = score_with_artifact(artifact, band)
    th = artifact["threshold"]
    a2 = band[(band.ns >= th) & (band.badge_cnt >= 1)]
    return {
        "holdout_start": int(holdout.date.min()),
        "holdout_end": int(holdout.date.max()),
        "band_n": int(len(band)),
        "band_base": round(float(band.top3.mean()), 4),
        "a2_n": int(len(a2)),
        "a2_precision": round(float(a2.top3.mean()), 4) if len(a2) else None,
    }


def main() -> None:
    """エントリポイント."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20240101", help="学習データ開始日 YYYYMMDD")
    parser.add_argument("--version", type=int, default=10, help="chihou calculated_indices version")
    parser.add_argument("--holdout", default=None, help="この日付以降をホールドアウトに")
    parser.add_argument("--out", default=str(ARTIFACT_PATH))
    args = parser.parse_args()

    uni = load_dataset(args.start, args.version)
    print(f"universe: {len(uni)} rows / {uni.race_id.nunique()} races "
          f"({uni.date.min()}〜{uni.date.max()})")

    if args.holdout:
        cut = int(args.holdout)
        train, hold = uni[uni.date < cut], uni[uni.date >= cut]
        artifact = fit(train)
        artifact["validation"] = validate(artifact, hold)
        print(json.dumps(artifact["validation"], indent=2))
    else:
        artifact = fit(uni)
        recent = uni[uni.date >= int(pd.Timestamp.now().strftime("%Y%m%d")) - 10000]
        if len(recent):
            artifact["validation"] = validate(artifact, recent)
            artifact["validation"]["note"] = "in-sample reference (trained on full period)"

    print(f"threshold(band ns {THRESHOLD_QUANTILE} quantile): {artifact['threshold']:.4f}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, ensure_ascii=False, indent=1))
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
