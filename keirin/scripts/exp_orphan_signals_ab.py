#!/usr/bin/env python3
"""誤り②で放置された残り2信号の A/B — 連戦負荷 / Elo残差（2026-08-21）。

## 背景

[[keirin_verification_audit_2026_08_20]] の系統的な誤り②
「**ROI 1.333 の基準を特徴量候補に当てた**」で閉じられた信号のうち、
[[keirin_n3_audit_round3_2026_08_21]] で**新たに2件**が見つかった。

`keirin_c_candidates_market_test_2026_07_30` は自ら

> **2つの本物の新しい信号を発見した**（連戦疲労=直近90日出走数・elo残差）。
> どちらも人気統制後も生き残り、TRAIN/TESTで単調性が再現する、統計的に堅い信号。
> **しかし全部積み上げても ROI 79%**（比 1.056 < 必要な 1.333）

と書いて閉じている。1.333 は**単独ベッティング規則**の基準であって特徴量の
合格ラインではない。実装を確認すると **どちらも今もモデルに入っていない**:

- 直近90日の**出走回数**: `FEATURE_COLS_WT` の `_90` 系は
  `rp_delta_90` / `b_rate_90` / `s_rate_90` / `fh_rel_90` / `fh_best_rate_90` で
  **すべて率**。回数（負荷量）は無い。`days_since`（前走からの間隔）は別物
- **Elo**: `src/` に実装ゼロ（`scripts/exp_elo_*` にしか無い）

⚠️ 誤り②の4件のうち残る2件（単独先行・結果条件つきローリング）は
**N-2 で A/B 済み・全案不採用**（[[keirin_form_features_ab_2026_08_20]]）。
本スクリプトは**未検証の2件だけ**を扱う。

## 🔴 ハーネスは N-2 と同一のものを使う

`exp_form_features_ab` の `run_window` を **import して呼ぶ**（コピーしない）。
窓・seed・学習器・評価指標・採用ラインを N-2 と1文字も違わせないため。
数字が N-2 の表とそのまま並べられる。

    採用ライン（先に固定・事後に動かさない）:
      **確認窓 w3/w4 の両方で符号一致** かつ **その平均 Δ1位3着内 ≥ +0.30pt**

## アーム

| アーム | 特徴 | 定義 |
|---|---|---|
| `+load` | `n_starts_90` | 直近90日の出走回数（point-in-time・当日を含まない） |
| `+elo` | `elo_resid`, `elo_z` | レース内 z(Elo) − z(競走得点)。正 = Elo が表示得点より高評価 |
| `+both` | 上記すべて | |

Usage:
    PYTHONPATH=. .venv/bin/python scripts/exp_orphan_signals_ab.py [--windows w3,w4]
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import scripts.exp_form_features_ab as H  # noqa: E402
from src.preprocessing.feature_wt import (  # noqa: E402
    build_features_wt, load_raw_data_wt,
)

# ── 追加検証（2026-08-21・**一次判定の結果を見た後に書いた**）─────────────────
#
# 一次判定（Δ1位3着内 ≥ +0.30pt）は **全アーム不採用**。
# ただし `+elo` は **AUC が全4窓で +0.0021〜0.0029**（N-2 の全アーム
# +0.00036〜+0.00076 の約4倍）で、**二軸も全4窓で正**だった。
# 二軸（上位2車とも3着内）は 7C/7S/7M1 が実際に買っている形そのもの。
#
# 🔴 **目的指標を事後に変えるので w1〜w4 は使えない**（発見と検証が同じ窓になる。
#    [[keirin_verification_design_audit_2026_08_21]] で確認窓を1つ無駄にした型）。
#    **未使用の w5/w6 で事前登録して1回だけ開ける。**
#
#    追加の採用ライン（結果を見る前に確定・事後に動かさない）:
#      **w5/w6 の両方で Δ二軸 > 0** かつ **その平均 Δ二軸 ≥ +0.30pt**
#
#    ⚠️ w5/w6 は `TRAIN_FROM=2024-04-01` より後で、かつ w4(2025-07-01) より前。
#       これ以上古い窓は学習期間が足りず作れないので、**これが最後の未使用窓**。
EXTRA_WINDOWS = {
    "w5": ("2025-04-01", "2025-06-30"),
    "w6": ("2025-01-01", "2025-03-31"),
}

SHIPPED = False

LOAD_COLS = ["n_starts_90"]
ELO_COLS = ["elo_resid", "elo_z"]
ELO_K, ELO_SCALE, ELO_INIT = 24.0, 400.0, 1500.0   # exp_elo_linecoop_wt と同値


def add_load_features(df: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    """直近90日の**出走回数**（当日を含まない）。

    🔴 既存の `_90` 系は `.rolling("90D", closed="left").mean()` の**率**。
       ここは同じ窓の `.count()` ＝ **量**で、別の情報。
    """
    h = raw[["race_key", "player_id", "race_date"]].dropna().copy()
    h["_dt"] = pd.to_datetime(h["race_date"])
    h = h.sort_values(["player_id", "_dt"])
    h["n_starts_90"] = (h.set_index("_dt").groupby("player_id")["race_key"]
                        .rolling("90D", closed="left").count()
                        .reset_index(level=0, drop=True).values)
    out = df.merge(h[["race_key", "player_id", "n_starts_90"]],
                   on=["race_key", "player_id"], how="left")
    out["n_starts_90"] = out["n_starts_90"].fillna(0.0)
    return out


def add_elo_features(df: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    """point-in-time Elo と、その**競走得点に対する残差**。

    残差 = レース内 z(Elo) − レース内 z(競走得点)。
    正なら「表示得点より実力が上」と Elo が見ている（＝得点の陳腐化ラグ）。
    🔴 **レース前の rating で特徴を作り、レース後に更新する**（point-in-time）。
    """
    d = raw[["race_key", "race_date", "start_at", "player_id",
             "finish_order", "race_point"]].copy()
    d["fin"] = pd.to_numeric(d["finish_order"], errors="coerce")
    order = (d.groupby("race_key")
             .agg(race_date=("race_date", "first"), start_at=("start_at", "first"))
             .sort_values(["race_date", "start_at"]).index.tolist())
    rating: dict = defaultdict(lambda: ELO_INIT)
    groups = {rk: g for rk, g in d.groupby("race_key", sort=False)}
    rows = []
    for rk in order:
        g = groups[rk]
        pids = g["player_id"].tolist()
        pre = [rating[p] for p in pids]
        rps = pd.to_numeric(g["race_point"], errors="coerce").tolist()
        ze = (float(np.mean(pre)), float(np.std(pre)) or 1.0)
        valid_rp = [x for x in rps if x == x]
        zr = ((float(np.mean(valid_rp)), float(np.std(valid_rp)) or 1.0)
              if valid_rp else (0.0, 1.0))
        for p, e, rp in zip(pids, pre, rps):
            z_e = (e - ze[0]) / (ze[1] or 1.0)
            z_r = ((rp - zr[0]) / (zr[1] or 1.0)) if rp == rp else 0.0
            rows.append((rk, p, z_e - z_r, z_e))
        # レース後に更新（完走者間の全ペア比較）
        fins = g["fin"].tolist()
        for i, (pi, fi) in enumerate(zip(pids, fins)):
            if fi != fi or fi < 1:
                continue
            for j, (pj, fj) in enumerate(zip(pids, fins)):
                if i >= j or fj != fj or fj < 1:
                    continue
                ex = 1.0 / (1.0 + 10 ** ((rating[pj] - rating[pi]) / ELO_SCALE))
                sc = 1.0 if fi < fj else (0.0 if fi > fj else 0.5)
                rating[pi] += ELO_K * (sc - ex)
                rating[pj] -= ELO_K * (sc - ex)
    e = pd.DataFrame(rows, columns=["race_key", "player_id", "elo_resid", "elo_z"])
    out = df.merge(e, on=["race_key", "player_id"], how="left")
    for c in ELO_COLS:
        out[c] = out[c].fillna(0.0)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default="w1,w2,w3,w4")
    ap.add_argument("--shipped", action="store_true",
                    help="出荷実装 `feature_wt.add_elo_features_wt` で再現する"
                         "（A/B は raw 期間だけのウォームアップだったので値が違う）")
    a = ap.parse_args()
    global SHIPPED
    SHIPPED = a.shipped

    print("データ読み込み ...", flush=True)
    max_to = max(t for _, t in H.WINDOWS.values())
    raw = load_raw_data_wt(min_date="2023-06-01", max_date=max_to)
    df = build_features_wt(raw)
    print(f"  特徴量構築 {len(df):,}行", flush=True)
    df = add_load_features(df, raw)
    if not SHIPPED:
        df = add_elo_features(df, raw)   # A/B 時の実装（raw 期間だけでウォームアップ）
    print(f"  n_starts_90 中央 {df['n_starts_90'].median():.1f} / "
          f"elo_resid sd {df['elo_resid'].std():.3f}", flush=True)

    if SHIPPED:
        # 🔴 出荷後は `FEATURE_COLS_WT` に Elo が入っているので、そのままだと
        #    `+elo` アームが二重に足して LightGBM が
        #    "Feature (elo_resid) appears more than one time" で落ちる。
        #    base から抜いて、アームで戻す形にする（比較の意味は同じ）。
        from src.preprocessing.feature_wt import (
            FEATURE_COLS_WT as FULL, ELO_COLS_WT,
        )
        H.FEATURE_COLS_WT = [c for c in FULL if c not in ELO_COLS_WT]
        print(f"  出荷実装モード: base {len(H.FEATURE_COLS_WT)}列 "
              f"（Elo {len(ELO_COLS_WT)}列を base から除外してアームで戻す）", flush=True)

    # 🔴 N-2 と同じ経路で回すためアーム定義だけ差し替える（run_window は共有）。
    H.ARMS = {"base": [], "+load": LOAD_COLS, "+elo": ELO_COLS,
              "+both": LOAD_COLS + ELO_COLS}

    wins = {**H.WINDOWS, **EXTRA_WINDOWS}
    out = {w: H.run_window(df, *wins[w]) for w in a.windows.split(",")}

    print("\n" + "=" * 72)
    print("== 差分（各アーム − base）==")
    print(f"{'アーム':<8}" + "".join(f"{w:>30}" for w in out))
    for arm in H.ARMS:
        if arm == "base":
            continue
        line = f"{arm:<8}"
        for w, r in out.items():
            line += (f"  AUC{r[arm]['auc'] - r['base']['auc']:+.5f}"
                     f" 勝{(r[arm]['win'] - r['base']['win'])*100:+.2f}"
                     f" 3着{(r[arm]['top3'] - r['base']['top3'])*100:+.2f}"
                     f" 二軸{(r[arm]['two'] - r['base']['two'])*100:+.2f}")
        print(line)
    ex = [w for w in ("w5", "w6") if w in out]
    if len(ex) == 2:
        print("\n--- 追加判定（未使用窓 w5/w6・Δ二軸・事前登録）---")
        for arm in H.ARMS:
            if arm == "base":
                continue
            ds = [(out[w][arm]["two"] - out[w]["base"]["two"]) * 100 for w in ex]
            ok = ds[0] > 0 and ds[1] > 0 and float(np.mean(ds)) >= 0.30
            print(f"  {arm:<8} Δ二軸 w5 {ds[0]:+.2f} / w6 {ds[1]:+.2f} "
                  f"平均 {float(np.mean(ds)):+.2f}pt → "
                  f"{'🟢 採用ライン到達' if ok else '❌ 不採用'}")

    conf = [w for w in ("w3", "w4") if w in out]
    if len(conf) == 2:
        print("\n--- 採用判定（確認窓 w3/w4・事前登録）---")
        for arm in H.ARMS:
            if arm == "base":
                continue
            ds = [(out[w][arm]["top3"] - out[w]["base"]["top3"]) * 100 for w in conf]
            same = ds[0] * ds[1] > 0
            avg = float(np.mean(ds))
            ok = same and avg >= 0.30
            print(f"  {arm:<8} Δ1位3着内 w3 {ds[0]:+.2f} / w4 {ds[1]:+.2f} "
                  f"平均 {avg:+.2f}pt  符号一致={'○' if same else '×'}  "
                  f"→ {'🟢 採用ライン到達' if ok else '❌ 不採用'}")


if __name__ == "__main__":
    main()
