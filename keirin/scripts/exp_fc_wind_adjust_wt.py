#!/usr/bin/env python3
"""予報風速 × 脚質を「後段の1パラメータ補正」として検証する（2026-08-18〜）

`exp_fc_wind_wt.py`（特徴量として素朴に追加する A/B）は **AUC も順位系も動かなかった**。
LightGBM 60特徴・500本の中では、風という小さな交互作用は掘り出されない。

そこで本スクリプトは効果を**直接1パラメータで書き下し**、後段で当てる:

    logit(p') = logit(p) + λ · s · max(w − W0, 0)
      s = +1(逃) / 0(両) / −1(追)   w = 予報風速(m/s)   W0 = しきい値

- λ は **TRAIN 内の較正窓**（モデル学習に使っていない後半）で推定する。
  in-sample 予測に当てると過学習した残差を拾って λ が潰れる。
- 評価は VAL / TEST。**2026-07-16 以降は確認窓として温存する**。

指標は順位系（軸1の3着内・二軸的中）。p' は単調変換ではないので**順位が動く**
＝これが狙い。ゲート（7C の 1.44 等）に載せるかどうかは別問題なので、
ここでは順位だけを見る。

## 結果（2026-08-18・不採用）

λ は 3窓とも有意に推定できる（z=+4.9/+4.3/+3.2）のに、当てても軸2車の組は
0.7% しか変わらず的中は動かない。λ を倍にすると悪化する。
数値と考察は `docs/analysis/55-fc-wind-style.md`。

使い方:
    KEIRIN_FEATURE_CACHE=1 PYTHONPATH=. .venv/bin/python scripts/exp_fc_wind_adjust_wt.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.preprocessing.feature_wt import FEATURE_COLS_WT, load_features_wt  # noqa: E402
from exp_fc_wind_wt import (  # noqa: E402
    LGB_PARAMS, TEST, TRAIN, VAL, attach_forecast, rank_metrics, window,
)

FIT = ("2023-07-01", "2024-12-31")     # λ 推定用モデルの学習窓
CAL = ("2025-01-01", "2025-06-30")     # λ を当てる窓（FIT に含まれない）
W0_GRID = (0.0, 1.5, 2.5, 3.5)
SEED = 42


def _style_sign(df: pd.DataFrame) -> np.ndarray:
    """逃 +1 / 両 0 / 追 −1。style_enc は 0/1/2。"""
    return -(df["style_enc"].astype(float) - 1.0).clip(-1, 1).values


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def fit_model(tr: pd.DataFrame) -> lgb.LGBMClassifier:
    m = lgb.LGBMClassifier(**LGB_PARAMS, random_state=SEED)
    m.fit(tr.reindex(columns=FEATURE_COLS_WT).fillna(0), tr["top3_flag"])
    return m


def residual_slope(df: pd.DataFrame, p: np.ndarray, w0: float) -> tuple[float, float]:
    """残差 ~ β·s·max(w−w0,0) の β と、レースでクラスタした z を返す。"""
    s = _style_sign(df)
    x = s * np.clip(df["fc_wind"].values - w0, 0, None)
    r = df["top3_flag"].values - p
    keep = x != 0
    x, r = x[keep], r[keep]
    if len(x) < 500:
        return float("nan"), float("nan")
    beta = float((x * r).sum() / (x * x).sum())
    # レース単位クラスタ SE
    grp = df.loc[keep, "race_key"].values
    e = r - beta * x
    u = pd.Series(x * e).groupby(grp).sum().values
    var = (u ** 2).sum() / ((x * x).sum() ** 2)
    return beta, beta / np.sqrt(var)


def apply_adjust(df: pd.DataFrame, p: np.ndarray, lam: float, w0: float) -> np.ndarray:
    s = _style_sign(df)
    x = s * np.clip(df["fc_wind"].values - w0, 0, None)
    return 1.0 / (1.0 + np.exp(-(_logit(p) + lam * x)))


def main() -> None:
    print("特徴量ロード中…", flush=True)
    df = load_features_wt("2022-12-01", "2026-07-15")
    df = df[df["race_date"] >= FIT[0]].reset_index(drop=True)
    df = attach_forecast(df)

    # ── λ の推定（FIT で学習 → CAL の残差から）────────────────────────────
    print("λ 推定用モデルを学習中…", flush=True)
    m_fit = fit_model(window(df, FIT))
    cal = window(df, CAL).copy()
    cal = cal[cal["finish_order"] >= 1]
    p_cal = m_fit.predict_proba(cal.reindex(columns=FEATURE_COLS_WT).fillna(0))[:, 1]

    print("\n== CAL 窓での残差の傾き（logit ではなく確率スケール）==")
    lam_prob = {}
    for w0 in W0_GRID:
        b, z = residual_slope(cal, p_cal, w0)
        lam_prob[w0] = b
        print(f"  W0={w0:>4}  β={b:+.5f} /(m/s)  z={z:+.2f}")

    # 確率スケールの β を logit スケールの λ へ換算（p(1−p) で割る）
    var_p = float(np.mean(p_cal * (1 - p_cal)))
    print(f"  平均 p(1−p) = {var_p:.4f} → λ ≒ β / p(1−p)")

    # ── 本体モデル（TRAIN 全体）で VAL / TEST を評価 ──────────────────────
    print("\n本体モデルを学習中…", flush=True)
    m = fit_model(window(df, TRAIN))
    for name, w in (("VAL", VAL), ("TEST", TEST)):
        ev = window(df, w).copy()
        ev = ev[ev["finish_order"] >= 1]
        p = m.predict_proba(ev.reindex(columns=FEATURE_COLS_WT).fillna(0))[:, 1]
        print(f"\n■ {name}  {ev['race_key'].nunique():,}レース")
        b, z = residual_slope(ev, p, 2.5)
        print(f"   残差の傾き（W0=2.5）β={b:+.5f}  z={z:+.2f}"
              "   ＝ 本体モデルも織り込めていないか")
        rows = []
        for w0 in W0_GRID:
            lam = lam_prob[w0] / var_p
            for mult, tag in ((0.0, "補正なし"), (1.0, "λ"), (2.0, "2λ")):
                if mult == 0.0 and w0 != W0_GRID[0]:
                    continue
                p2 = apply_adjust(ev, p, lam * mult, w0)
                row = {"W0": w0 if mult else "-", "λ": round(lam * mult, 4), "案": tag}
                for cars in (7, 9):
                    for k, v in rank_metrics(ev, p2, cars).items():
                        if k != "n_race":
                            row[f"{cars}車{k}"] = round(v, 3)
                rows.append(row)
        print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
