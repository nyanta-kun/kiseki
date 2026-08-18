#!/usr/bin/env python3
"""「強風 × 向かい風」で逃を下げる案の一度きり検定（2026-08-18）

## 背景

`docs/analysis/55-fc-wind-style.md` で **風速**の交互作用（強風ほど逃が良い）は
確定したが、決定を動かす大きさが無く不採用にした。ユーザー提案は逆向き
（強風で逃を下げる）で、風速だけを条件にすると **確実に悪化する**ことも確認済み。

残るのは**風向**版だけ。「B（バック線先頭）を取る逃げが風をまともに受ける向きの日は
逃が沈む」なら、それは風速ではなく風向に乗る。G06 は wind_dir を読みながら
特徴量に入れておらず、**着順に対して一度も検定されていない**（memory
`keirin_wind_direction_2026_08_18` は上がり200mでのみ確認）。

## 設計（事前登録）

1. **φ 推定**: 会場ごとに「逃が最も有利になる風向」φ_v を **TRAIN+CAL
   （2023-07-01〜2025-06-30）だけ**で推定する。
   逃有利度 `g = -rel_c · s`（rel_c=会場×年月で中心化した正規化着順・
   s=逃+1/追−1）を、会場ごとに `g ~ a + b0·w + b1·w·cosθ + b2·w·sinθ` で回帰。
   φ_v = atan2(b2, b1)。**2パラメータ自由に当てるので推定窓では必ず何か出る。**
2. **検定**: VAL / TEST で `x_dir = s·w·cos(θ−φ_v)` の係数を測る。
   - (a) 記述: `g ~ x_speed + x_dir`
   - (b) 増分: `本番モデルの残差 ~ x_speed + x_dir`（レースでクラスタした z）
   φ が本物なら **VAL/TEST 両方で正**になる。
3. **プラセボ**: 会場ラベルをシャッフルした φ で同じ検定を 200 回まわし、
   実測の係数が帰無分布のどこに落ちるかを見る。
4. **A/B**: 2 が通ったときだけ、強風×向かい風に限って逃を下げる補正を当て、
   二軸的中で比較する。

⚠️ ドーム（前橋22 / 千葉32 / 小倉81）は除外する。
⚠️ `fc_wind_dir` は「風が吹いてくる方角」。φ の物理的な意味（どちらが向かい風か）は
   会場の向きを別途調べないと決まらないが、**本検定には不要**
   （必要なのは「同じ向きが翌年も再現するか」だけ）。

## 結果（2026-08-18・不採用）

- (c) 上がり200m の軸は **out-of-sample で再現する**（β +0.016〜0.025 秒/(m/s)・
  z +7.06/+7.60/+2.78・会場別 27/36・26/34・17/27）。測定は効いている。
- (d) **その軸で着順は動かない**。向かい風の係数は3窓とも正（仮説は負を予測）で
  いずれも有意でない。着順から φ を自由推定した版もプラセボの中央（上位 62.5%/52.0%）。
- 提案どおりの補正を当てると、ウェイトを上げるほど単調に悪化する
  （μ=0.264 で強風帯の二軸 −5.33/−3.88pt）。

数値と考察は `docs/analysis/55-fc-wind-style.md` の追記。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_fc_wind_dir_wt.py
"""
from __future__ import annotations

import os
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

FIT_END = "2025-06-30"        # φ 推定に使う終端（VAL の開始前）
FIT_START = "2023-07-01"
DOME = {"22", "32", "81"}
MIN_ENTRIES = 1500            # φ を推定する最低エントリ数（逃+追）
PRED_PKL = Path("/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/"
                "10b197b5-6e8c-4817-8963-24f9f82158cc/scratchpad/preds.pkl")

SQL = """
SELECT e.race_key, r.race_date, r.venue_id, e.style, e.finish_order, e.final_half,
       c.fc_wind_speed AS w, c.fc_wind_dir AS dir
FROM keirin.wt_entries e
JOIN keirin.wt_races r ON r.race_key = e.race_key
LEFT JOIN keirin.wt_race_conditions c ON c.race_key = e.race_key
WHERE r.cancel = 0 AND r.race_date >= '2023-07-01'
"""


def load_all() -> pd.DataFrame:
    """脚質で絞らない生データ（上がり200m の φ 推定に使う）。"""
    eng = create_engine(os.environ["KEIRIN_DB_URL"])
    with eng.connect() as conn:
        df = pd.read_sql_query(text(SQL), conn)
    eng.dispose()
    df = df[(df["finish_order"] >= 1) & df["w"].notna() & df["dir"].notna()]
    df = df[~df["venue_id"].astype(str).isin(DOME)].copy()
    df["ym"] = df["race_date"].str[:7]
    th = np.deg2rad(df["dir"].astype(float))
    df["wc"] = df["w"] * np.cos(th)
    df["ws"] = df["w"] * np.sin(th)
    return df


def phi_from_final_half(d: pd.DataFrame) -> dict[str, float]:
    """会場ごとに「上がり200m が最も遅くなる風向」φ を推定する。

    🔴 **会場×年月で中心化してから**当てる。季節性を残すと屋外の振幅が倍に見える
    （memory `keirin_wind_direction_2026_08_18`）。
    """
    d = d[d["final_half"].notna()].copy()
    d["fh_c"] = d["final_half"] - d.groupby(["venue_id", "ym"])["final_half"].transform("mean")
    out = {}
    for v, g in d.groupby(d["venue_id"].astype(str)):
        if len(g) < MIN_ENTRIES:
            continue
        X = np.column_stack([np.ones(len(g)), g["w"], g["wc"], g["ws"]])
        b, *_ = np.linalg.lstsq(X, g["fh_c"].values, rcond=None)
        out[v] = float(np.arctan2(b[3], b[2]))   # 上がりが最も遅くなる向き
    return out


def load() -> pd.DataFrame:
    eng = create_engine(os.environ["KEIRIN_DB_URL"])
    with eng.connect() as conn:
        df = pd.read_sql_query(text(SQL), conn)
    eng.dispose()
    df = df[(df["finish_order"] >= 1) & df["w"].notna() & df["dir"].notna()]
    df = df[~df["venue_id"].astype(str).isin(DOME)].copy()
    df["n"] = df.groupby("race_key")["finish_order"].transform("max")
    df = df[df["n"] == 7].copy()                    # 7車立てに限定
    df = df[df["style"].isin(["逃", "追"])].copy()
    df["s"] = np.where(df["style"] == "逃", 1.0, -1.0)
    df["rel"] = (df["finish_order"] - 1) / (df["n"] - 1)
    df["ym"] = df["race_date"].str[:7]
    df["rel_c"] = df["rel"] - df.groupby(["venue_id", "ym"])["rel"].transform("mean")
    df["g"] = -df["rel_c"] * df["s"]                # 高いほど「逃有利」
    th = np.deg2rad(df["dir"].astype(float))
    df["wc"] = df["w"] * np.cos(th)
    df["ws"] = df["w"] * np.sin(th)
    return df


def estimate_phi(fit: pd.DataFrame) -> dict[str, float]:
    """会場ごとに φ_v を推定する。"""
    phi = {}
    for v, d in fit.groupby(fit["venue_id"].astype(str)):
        if len(d) < MIN_ENTRIES:
            continue
        X = np.column_stack([np.ones(len(d)), d["w"], d["wc"], d["ws"]])
        b, *_ = np.linalg.lstsq(X, d["g"].values, rcond=None)
        phi[v] = float(np.arctan2(b[3], b[2]))
    return phi


def project(d: pd.DataFrame, phi: dict[str, float]) -> np.ndarray:
    """x_dir = s·w·cos(θ−φ_v)。φ が無い会場は 0。"""
    p = d["venue_id"].astype(str).map(phi)
    th = np.deg2rad(d["dir"].astype(float))
    return np.where(p.isna(), 0.0,
                    d["s"] * d["w"] * np.cos(th - p.fillna(0.0)))


def ols_cluster(y, X, groups):
    """係数とレースクラスタ z。X は切片を含めない。"""
    X = np.column_stack([np.ones(len(y)), X])
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ b
    XtX_inv = np.linalg.pinv(X.T @ X)
    meat = np.zeros((X.shape[1], X.shape[1]))
    dfm = pd.DataFrame(X * e[:, None])
    for _, blk in dfm.groupby(groups):
        u = blk.values.sum(axis=0)
        meat += np.outer(u, u)
    V = XtX_inv @ meat @ XtX_inv
    return b[1:], b[1:] / np.sqrt(np.diag(V)[1:])


def main() -> None:
    df = load()
    fit = df[(df["race_date"] >= FIT_START) & (df["race_date"] <= FIT_END)]
    phi = estimate_phi(fit)
    print(f"φ を推定した会場: {len(phi)} / "
          f"{fit['venue_id'].nunique()}（エントリ {MIN_ENTRIES} 未満は除外）")

    windows = {"VAL": ("2025-07-01", "2026-02-28"),
               "TEST": ("2026-03-01", "2026-07-15"),
               "確認窓(2026-07-16〜)": ("2026-07-16", "2026-12-31")}

    print("\n" + "=" * 72)
    print("(a) 記述: g ~ s·w(速度) + s·w·cos(θ−φ)(向き)")
    print("=" * 72)
    for name, (lo, hi) in windows.items():
        d = df[(df["race_date"] >= lo) & (df["race_date"] <= hi)]
        if len(d) < 2000:
            print(f"  {name}: n={len(d)} 少なすぎ・スキップ")
            continue
        x_sp = (d["s"] * np.clip(d["w"] - 2.5, 0, None)).values
        x_di = project(d, phi)
        b, z = ols_cluster(d["g"].values, np.column_stack([x_sp, x_di]),
                           d["race_key"].values)
        print(f"  {name:<18} n={len(d):>7,}  "
              f"速度 β={b[0]:+.5f} z={z[0]:+.2f}   "
              f"向き β={b[1]:+.5f} z={z[1]:+.2f}")
        # 会場ごとの再現（射影の符号が正の会場数）
        rep = []
        for v, dv in d.groupby(d["venue_id"].astype(str)):
            if v not in phi or len(dv) < 200:
                continue
            xv = project(dv, phi)
            bb, _ = ols_cluster(dv["g"].values, xv.reshape(-1, 1),
                                dv["race_key"].values)
            rep.append(bb[0] > 0)
        if rep:
            print(f"  {'':<18} 会場別に符号が再現: {sum(rep)}/{len(rep)}")

    # (b) 本番モデルの残差に対する増分
    if PRED_PKL.exists():
        print("\n" + "=" * 72)
        print("(b) 増分: 本番モデルの残差 ~ 速度 + 向き")
        print("=" * 72)
        P = pickle.load(open(PRED_PKL, "rb"))
        dirs = df[["race_key", "dir"]].drop_duplicates("race_key")
        for name, ev in P.items():
            e = ev.merge(dirs, on="race_key", how="inner")
            e = e[e["style"].isin(["逃", "追"])].copy()
            n = e.groupby("race_key")["frame_no"].transform("count")
            e = e[~e["venue_id"].astype(str).isin(DOME)]
            e["s"] = np.where(e["style"] == "逃", 1.0, -1.0)
            e["w"] = e["fc_wind"]
            e["resid"] = e["top3_flag"] - e["p"]
            x_sp = (e["s"] * np.clip(e["w"] - 2.5, 0, None)).values
            x_di = project(e, phi)
            b, z = ols_cluster(e["resid"].values,
                               np.column_stack([x_sp, x_di]), e["race_key"].values)
            print(f"  {name:<6} n={len(e):>7,}  "
                  f"速度 β={b[0]:+.5f} z={z[0]:+.2f}   "
                  f"向き β={b[1]:+.5f} z={z[1]:+.2f}")

            # プラセボ: 会場ラベルをシャッフルした φ
            rng = np.random.default_rng(42)
            keys = list(phi)
            null = []
            for _ in range(200):
                sh = dict(zip(keys, [phi[k] for k in rng.permutation(keys)]))
                xd = project(e, sh)
                bb, _zz = ols_cluster(e["resid"].values,
                                      np.column_stack([x_sp, xd]),
                                      e["race_key"].values)
                null.append(bb[1])
            null = np.array(null)
            pct = float((null >= b[1]).mean()) * 100
            print(f"  {'':<6} プラセボ200回: 実測が上位 {pct:.1f}%  "
                  f"(帰無 平均 {null.mean():+.5f} / sd {null.std():.5f})")

    straight_test()


def straight_test() -> None:
    """(c)(d) 上がり200m から取った「向かい風の軸」で着順を検定する。"""
    allrows = load_all()
    fit = allrows[(allrows["race_date"] >= FIT_START) & (allrows["race_date"] <= FIT_END)]
    phi = phi_from_final_half(fit)
    print("\n" + "=" * 72)
    print("(c) φ を上がり200m（final_half）から推定 — 物理的に意味のある軸")
    print("=" * 72)
    print(f"  推定できた会場: {len(phi)}")

    windows = {"VAL": ("2025-07-01", "2026-02-28"),
               "TEST": ("2026-03-01", "2026-07-15"),
               "確認窓": ("2026-07-16", "2026-12-31")}

    # (c-1) φ 自体が out-of-sample で再現するか（上がりが本当に遅くなるか）
    for name, (lo, hi) in windows.items():
        d = allrows[(allrows["race_date"] >= lo) & (allrows["race_date"] <= hi)]
        d = d[d["final_half"].notna()].copy()
        if len(d) < 2000:
            continue
        d["fh_c"] = d["final_half"] - d.groupby(["venue_id", "ym"])["final_half"].transform("mean")
        p = d["venue_id"].astype(str).map(phi)
        th = np.deg2rad(d["dir"].astype(float))
        h = np.where(p.isna(), 0.0, d["w"] * np.cos(th - p.fillna(0.0)))
        b, z = ols_cluster(d["fh_c"].values, h.reshape(-1, 1), d["race_key"].values)
        rep = []
        for v, dv in d.groupby(d["venue_id"].astype(str)):
            if v not in phi or len(dv) < 200:
                continue
            pv = np.deg2rad(dv["dir"].astype(float)) - phi[v]
            bb, _ = ols_cluster(dv["fh_c"].values,
                                (dv["w"] * np.cos(pv)).values.reshape(-1, 1),
                                dv["race_key"].values)
            rep.append(bb[0] > 0)
        print(f"  {name:<6} 上がりが遅くなる: β={b[0]:+.5f}秒/(m/s) z={z[0]:+.2f}  "
              f"会場別再現 {sum(rep)}/{len(rep)}")

    # (c-2) その軸で着順（逃有利度）が動くか＝ユーザー提案の本体
    print("\n" + "=" * 72)
    print("(d) その軸で着順が動くか（逃有利度 g ~ 速度 + 向かい風）")
    print("   ユーザー仮説が正しければ **向かい風の係数は負**")
    print("=" * 72)
    df = load()
    for name, (lo, hi) in windows.items():
        d = df[(df["race_date"] >= lo) & (df["race_date"] <= hi)]
        if len(d) < 2000:
            continue
        p = d["venue_id"].astype(str).map(phi)
        th = np.deg2rad(d["dir"].astype(float))
        h = np.where(p.isna(), 0.0, d["w"] * np.cos(th - p.fillna(0.0)))
        x_sp = (d["s"] * np.clip(d["w"] - 2.5, 0, None)).values
        x_hd = d["s"].values * h
        b, z = ols_cluster(d["g"].values, np.column_stack([x_sp, x_hd]),
                           d["race_key"].values)
        print(f"  {name:<6} n={len(d):>7,}  速度 β={b[0]:+.5f} z={z[0]:+.2f}   "
              f"向かい風 β={b[1]:+.5f} z={z[1]:+.2f}")
        # 強風時だけ
        st = d[d["w"] >= 3.5]
        if len(st) > 2000:
            ph = st["venue_id"].astype(str).map(phi)
            hh = np.where(ph.isna(), 0.0,
                          st["w"] * np.cos(np.deg2rad(st["dir"].astype(float))
                                           - ph.fillna(0.0)))
            b2, z2 = ols_cluster(st["g"].values,
                                 (st["s"].values * hh).reshape(-1, 1),
                                 st["race_key"].values)
            print(f"  {'':<6} 風>=3.5 のみ n={len(st):,}  "
                  f"向かい風 β={b2[0]:+.5f} z={z2[0]:+.2f}")


if __name__ == "__main__":
    main()
