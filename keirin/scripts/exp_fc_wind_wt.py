#!/usr/bin/env python3
"""予報風速 × 脚質 の増分情報検証（2026-08-18〜）

## 背景

`keirin.wt_race_conditions`（2026-08-18 新設）で気象データが揃い、記述統計では
**風速が強いほど 逃 が 追 に対して相対的に良くなる**ことが確認されている
（memory `keirin_wind_direction_2026_08_18`）。本番モデル `FEATURE_COLS_WT` は
気象特徴を1つも持たないので、これは原理的に未利用の情報である。

🔴 **実測風速（`wind_speed`）は特徴量にしない。** winticket が発表するのは発走後で、
   朝に推奨を作る本番では必ず欠損する。使えるのは予報（`fc_*`）だけ。
   したがって本スクリプトは **`fc_wind_speed` のみ**で検証する。

⚠️ 2026-06-13 の G06（`exp_wind_wt.py` / `docs/analysis/24-wind-feature.md`）は
   同型の検証を「AUC 差 ±0.001」で不通過にしている。だが keirin では
   **AUC だけで採否を決めないのが規約**（レース内で全車同値の特徴が AUC を上げても
   順位付けに寄与しなかった実例が CLAUDE.md にある）。逆に本件は「レース内で
   脚質ごとに向きが違う」特徴なので、AUC が動かなくても順位が動きうる。
   よって本スクリプトは **順位系の指標（二軸的中・軸1の3着内/1着）を主指標**にし、
   AUC/logloss は参考に留める。

## 段取り

- Phase 0（残差検定）: ベースモデルの残差（実績3着内 − 予測 p3）を
  脚質 × 予報風速帯で見る。**モデルが既に織り込んでいるなら残差は平ら**になる。
  ここが平らなら以降は不要。
- Phase 1（A/B）: TRAIN のみで学習したリーク無し LGBM に予報風速特徴を足し、
  VAL / TEST で AUC・logloss と**順位系指標**を比較する。
  seed 摂動（3本）で「学習の揺れ」の幅を出し、差がその中なら不採用。

期間: TRAIN 2023-07-01〜2025-06-30 / VAL 2025-07-01〜2026-02-28 /
      TEST 2026-03-01〜2026-07-15（特徴量キャッシュの範囲。7/16 以降は確認窓に温存）

## 結果（2026-08-18・不採用）

Phase 0 の残差は**傾いている**（モデルは織り込んでいない）が、Phase 1 では
AUC も順位系も seed の振れの中。**採用しなかった**。数値と考察は
`docs/analysis/55-fc-wind-style.md`、後段補正版は `exp_fc_wind_adjust_wt.py`。

使い方:
    KEIRIN_FEATURE_CACHE=1 PYTHONPATH=. .venv/bin/python scripts/exp_fc_wind_wt.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.metrics import log_loss, roc_auc_score  # noqa: E402

from src.preprocessing.feature_wt import (  # noqa: E402
    FEATURE_COLS_WT,
    load_features_wt,
    prepare_X,
)

TRAIN = ("2023-07-01", "2025-06-30")
VAL = ("2025-07-01", "2026-02-28")
TEST = ("2026-03-01", "2026-07-15")

LGB_PARAMS = dict(objective="binary", n_estimators=500, learning_rate=0.05,
                  num_leaves=31, min_child_samples=20, subsample=0.8,
                  colsample_bytree=0.8, verbose=-1)
SEEDS = (42, 7, 2024)

# 🔴 `venue_info.is_indoor` は誤っている（前橋22・小倉81 のドームが両方 0）。
#    ここでフラグを信用すると屋内の「予報風速」を特徴に流し込むことになる。
DOME_VENUES = {"22", "32", "81"}   # 前橋 / 千葉(TIPSTAR DOME) / 小倉(メディアドーム)

WIND_COLS = ["fc_wind", "fc_wind_x_style"]


# ─── データ ────────────────────────────────────────────────────────────────
def attach_forecast(df: pd.DataFrame) -> pd.DataFrame:
    """予報風速と「風速 × 脚質」を付与する。

    `fc_wind_x_style` は **符号つき**（逃 = −w / 両 = 0 / 追 = +w）。
    生の style_enc(0/1/2) との積にすると逃が常に 0 になり、
    「無風の逃」と「強風の逃」が同じ値になってしまう。
    """
    with_ = df.copy()
    from sqlalchemy import create_engine, text
    import os

    eng = create_engine(os.environ["KEIRIN_DB_URL"])
    with eng.connect() as conn:
        fc = pd.read_sql_query(
            text("SELECT race_key, fc_wind_speed FROM keirin.wt_race_conditions"),
            conn)
    eng.dispose()

    with_ = with_.merge(fc, on="race_key", how="left")
    with_["fc_wind"] = with_["fc_wind_speed"].astype(float)
    # ドームは風が吹かない。予報（屋外の 10m 風速）をそのまま入れない。
    is_dome = with_["venue_id"].astype(str).isin(DOME_VENUES)
    with_.loc[is_dome, "fc_wind"] = 0.0
    with_["fc_wind"] = with_["fc_wind"].fillna(0.0)
    with_["fc_wind_x_style"] = with_["fc_wind"] * (with_["style_enc"] - 1.0)
    return with_


def window(df: pd.DataFrame, w: tuple[str, str]) -> pd.DataFrame:
    return df[(df["race_date"] >= w[0]) & (df["race_date"] <= w[1])]


def fit_predict(tr: pd.DataFrame, evals: dict[str, pd.DataFrame],
                cols: list[str], seed: int) -> dict[str, np.ndarray]:
    model = lgb.LGBMClassifier(**LGB_PARAMS, random_state=seed)
    model.fit(tr.reindex(columns=cols).fillna(0), tr["top3_flag"])
    return {k: model.predict_proba(v.reindex(columns=cols).fillna(0))[:, 1]
            for k, v in evals.items()}


# ─── 指標 ──────────────────────────────────────────────────────────────────
def rank_metrics(df: pd.DataFrame, pred: np.ndarray, cars: int) -> dict:
    """順位系の指標。`cars` 車立て（結果確定・欠車なし）に限定する。"""
    d = df.assign(_p=pred)
    d = d[d["finish_order"] >= 1]
    n = d.groupby("race_key")["frame_no"].transform("count")
    d = d[n == cars]
    if d.empty:
        return {}
    d = d.sort_values(["race_key", "_p"], ascending=[True, False])
    r = d.groupby("race_key")
    first = r.head(1)
    top2 = r.head(2)
    two_axis = top2.groupby("race_key")["top3_flag"].sum().eq(2).mean()
    return {
        "n_race": int(d["race_key"].nunique()),
        "軸1の3着内": float(first["top3_flag"].mean()) * 100,
        "軸1の1着": float(first["win_flag"].mean()) * 100,
        "二軸的中": float(two_axis) * 100,
    }


def top2_change_rate(df: pd.DataFrame, a: np.ndarray, b: np.ndarray) -> float:
    """軸2車の組が変わったレースの割合（%）。ここが 0 なら下流は何も動かない。"""
    def _pairs(p):
        d = df.assign(_p=p).sort_values(["race_key", "_p"], ascending=[True, False])
        return (d.groupby("race_key").head(2).groupby("race_key")["frame_no"]
                .apply(lambda s: frozenset(s)))
    pa, pb = _pairs(a), _pairs(b)
    return float((pa != pb).mean()) * 100


# ─── Phase 0 ───────────────────────────────────────────────────────────────
def phase0(df: pd.DataFrame, pred: dict[str, np.ndarray]) -> None:
    print("\n" + "=" * 78)
    print("Phase 0: ベースモデルの残差（実績 − 予測）× 脚質 × 予報風速帯")
    print("  モデルが既に織り込んでいるなら残差は帯によらず平らになる")
    print("=" * 78)
    for name, w in (("VAL", VAL), ("TEST", TEST)):
        d = window(df, w).copy()
        d["resid"] = d["top3_flag"] - pred[name]
        d = d[d["finish_order"] >= 1]
        d["band"] = pd.cut(d["fc_wind"], [-.01, 1.5, 2.5, 3.5, 5.0, 99],
                           labels=["<1.5", "1.5-2.5", "2.5-3.5", "3.5-5", "5+"])
        for cars, tag in ((7, "7車"), (9, "9車")):
            n = d.groupby("race_key")["frame_no"].transform("count")
            s = d[(n == cars) & d["style"].isin(["逃", "両", "追"])]
            if s.empty:
                continue
            t = s.pivot_table(index="band", columns="style", values="resid",
                              aggfunc="mean", observed=True) * 100
            cnt = s.pivot_table(index="band", columns="style", values="resid",
                                aggfunc="count", observed=True)
            t["逃-追"] = t["逃"] - t["追"]
            t["n逃"] = cnt["逃"]
            print(f"\n  [{name} / {tag}] 残差(pt)  ＋なら実績が予測を上回る")
            print(t.round(2).to_string())


# ─── Phase 1 ───────────────────────────────────────────────────────────────
def phase1(df: pd.DataFrame) -> None:
    tr = window(df, TRAIN)
    evals = {"VAL": window(df, VAL), "TEST": window(df, TEST)}
    base_cols = list(FEATURE_COLS_WT)
    wind_cols = base_cols + WIND_COLS

    preds: dict[tuple[str, int], dict[str, np.ndarray]] = {}
    for seed in SEEDS:
        preds[("base", seed)] = fit_predict(tr, evals, base_cols, seed)
        preds[("wind", seed)] = fit_predict(tr, evals, wind_cols, seed)
        print(f"  学習完了 seed={seed}", flush=True)

    print("\n" + "=" * 78)
    print("Phase 1: A/B（リーク無し・TRAIN のみで学習）")
    print("=" * 78)
    for name, ev in evals.items():
        y = ev["top3_flag"].values
        print(f"\n■ {name}  n={len(ev):,}行 / {ev['race_key'].nunique():,}レース")
        rows = []
        for kind in ("base", "wind"):
            for seed in SEEDS:
                p = preds[(kind, seed)][name]
                row = {"model": kind, "seed": seed,
                       "AUC": roc_auc_score(y, p), "logloss": log_loss(y, p)}
                for cars in (7, 9):
                    for k, v in rank_metrics(ev, p, cars).items():
                        if k != "n_race":
                            row[f"{cars}車{k}"] = v
                rows.append(row)
        t = pd.DataFrame(rows)
        print(t.round(4).to_string(index=False))
        print("\n  平均差（wind − base）と seed の振れ幅（base の max−min）:")
        agg = t.groupby("model").agg(["mean", "min", "max"])
        for col in t.columns:
            if col in ("model", "seed"):
                continue
            d = agg[(col, "mean")]["wind"] - agg[(col, "mean")]["base"]
            spread = agg[(col, "max")]["base"] - agg[(col, "min")]["base"]
            flag = "★" if abs(d) > spread else " "
            print(f"   {flag} {col:<16} 差 {d:+.4f}   seed振れ {spread:.4f}")
        # 軸の組が動いた割合
        for cars in (7, 9):
            n = ev.groupby("race_key")["frame_no"].transform("count")
            s = ev[(n == cars) & (ev["finish_order"] >= 1)]
            if s.empty:
                continue
            idx = s.index
            pa = preds[("base", 42)][name][ev.index.get_indexer(idx)]
            pb = preds[("wind", 42)][name][ev.index.get_indexer(idx)]
            print(f"   {cars}車 軸2車の組が変わったレース: "
                  f"{top2_change_rate(s, pa, pb):.2f}%")


def main() -> None:
    print("特徴量ロード中…", flush=True)
    df = load_features_wt("2022-12-01", "2026-07-15")
    df = df[df["race_date"] >= TRAIN[0]].reset_index(drop=True)
    df = attach_forecast(df)
    print(f"  {len(df):,}行 / {df['race_key'].nunique():,}レース  "
          f"予報欠損 {df['fc_wind_speed'].isna().mean()*100:.2f}%")

    tr = window(df, TRAIN)
    evals = {"VAL": window(df, VAL), "TEST": window(df, TEST)}
    base_pred = fit_predict(tr, evals, list(FEATURE_COLS_WT), SEEDS[0])
    phase0(df, base_pred)
    phase1(df)


if __name__ == "__main__":
    main()
