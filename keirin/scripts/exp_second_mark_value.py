#!/usr/bin/env python3
"""「他社 AI 印をもう1ソース足したら効くか」を**取得する前に**測る（2026-09-14）。

背景: `lgbm_wt_eval` の gain の 32.17% は winticket の AI 印 `prediction_mark` 1本
（`docs/type_lab/model_headroom_2026_09_14.md` §2）。DB 内の未使用列は尽きたので、
残るのは「いま DB に無いデータ」。その筆頭候補が **2ソース目の予想印**。
本稿はスクレイピングを実装する前に価値の上限を出すための代理実験。

3つ測る（すべて既存 DB のみ・外部アクセスなし）:

  E1 上限   `prediction_mark` を**外した**ときの劣化。
            ＝ 69特徴に対する「1本目の印」の限界寄与。
            2本目の印はこれを超えられない（1本目と無相関でも同等が上限）。
  E2 冗長性 69特徴から `prediction_mark` の ◎ を当てられるか。
            当てられるなら、同じ公開情報から作られた2本目も冗長。
  E3 較正曲線 「1本目と一致率 A・同等の質」の**合成2本目の印**を足したときの ΔAUC。
            A を振って曲線にする。実サイトを N レースだけ手で集めて一致率を測れば、
            この曲線から期待 ΔAUC が読める（＝取得前に採否が決まる）。

方法論は `exp_model_headroom_ab.py` / `exp_meeting_form_ab.py` と同一
（2窓 × seed・deterministic・指標に上位2車そろい率を含む）。

    PYTHONPATH=. .venv/bin/python scripts/exp_second_mark_value.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.metrics import roc_auc_score

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.preprocessing.feature_wt import FEATURE_COLS_WT, TARGET_COL_WT  # noqa: E402

CACHE = Path("/tmp/secondmark_df.pkl")
TRAIN_FROM = "2024-04-01"
WINDOWS = {"w1": ("2026-04-13", "2026-07-15"), "w2": ("2026-01-01", "2026-04-12")}
SEEDS = [42, 101, 202]
#: 合成2本目の印の「私的情報」の質。単独 AUC = Φ(γ/√2) = 0.74（＝1本目と同等）。
GAMMA = norm.ppf(0.74) * np.sqrt(2.0)
#: 共有成分の重み。1.0 = 1本目のコピー（健全性チェック）／0.0 = 完全独立。
SHARE_GRID = [1.0, 0.8, 0.6, 0.4, 0.0]
#: 印の強さ順（0=なし は最弱）。数値符号 1◎/2○/3▲/4△ は強さ順と一致しない。
STRENGTH = {1: 4.0, 2: 3.0, 3: 2.0, 4: 1.0, 0: 0.0}


def zscore_by_race(s: pd.Series, key: pd.Series) -> np.ndarray:
    g = s.groupby(key)
    return ((s - g.transform("mean")) / g.transform("std").replace(0, np.nan)).fillna(0.0).to_numpy()


def make_mark2(df: pd.DataFrame, share: float, rng: np.random.Generator) -> np.ndarray:
    """一致率 `share` で1本目と共有する「2本目の印」を合成する。"""
    strength = df["prediction_mark"].map(STRENGTH).astype(float)
    shared = zscore_by_race(strength, df["race_key"])
    y = df[TARGET_COL_WT].astype(float).to_numpy()
    private = GAMMA * y + rng.standard_normal(len(df))
    private = zscore_by_race(pd.Series(private, index=df.index), df["race_key"])
    s2 = share * shared + (1.0 - share) * private + 1e-6 * rng.standard_normal(len(df))
    out = np.zeros(len(df), dtype=float)
    pos = pd.Series(s2, index=df.index).groupby(df["race_key"]).rank(ascending=False, method="first")
    for rank_, code in ((1, 1.0), (2, 2.0), (3, 3.0), (4, 4.0)):
        out[(pos == rank_).to_numpy()] = code
    return out


def race_metrics(test: pd.DataFrame, prob: np.ndarray) -> tuple:
    t = test.copy()
    t["p"] = prob
    win = top3 = pair = n = 0
    for _rk, g in t.groupby("race_key"):
        if g["n_entries"].iloc[0] != 7 or len(g) != 7:
            continue
        fo = pd.to_numeric(g["finish_order"], errors="coerce")
        if (fo.notna() & (fo >= 1)).sum() < 3:
            continue
        g = g.assign(_fo=fo.fillna(99))
        o = g.sort_values("p", ascending=False)
        f1, f2 = o.iloc[0]["_fo"], o.iloc[1]["_fo"]
        n += 1
        win += 1 if f1 == 1 else 0
        top3 += 1 if 1 <= f1 <= 3 else 0
        pair += 1 if (1 <= f1 <= 3 and 1 <= f2 <= 3) else 0
    return (win / n, top3 / n, pair / n, n)


def fit_eval(train, test, cols, seed):
    m = lgb.LGBMClassifier(
        objective="binary", n_estimators=500, learning_rate=0.05, num_leaves=31,
        min_child_samples=20, subsample=0.8, colsample_bytree=0.8, random_state=seed,
        deterministic=True, force_row_wise=True, verbose=-1)
    m.fit(train[cols], train[TARGET_COL_WT])
    p = m.predict_proba(test[cols])[:, 1]
    return roc_auc_score(test[TARGET_COL_WT], p), *race_metrics(test, p)


def main() -> None:
    df = pd.read_pickle(CACHE)
    print(f"rows={len(df):,}  baseline={len(FEATURE_COLS_WT)}特徴", flush=True)

    rng = np.random.default_rng(7)
    sim_cols = {}
    for share in SHARE_GRID:
        col = f"mark2_s{share:.1f}"
        df[col] = make_mark2(df, share, rng)
        sim_cols[share] = col

    BASE = list(FEATURE_COLS_WT)
    NOMARK = [c for c in BASE if c != "prediction_mark"]
    arms = [("baseline(70)", BASE), ("E1 −印(69)", NOMARK)]
    arms += [(f"E3 +2本目 share={s:.1f}", BASE + [sim_cols[s]]) for s in SHARE_GRID]

    for wn, (tf, tt) in WINDOWS.items():
        train = df[(df["race_date"] >= TRAIN_FROM) & (df["race_date"] < tf)]
        test = df[(df["race_date"] >= tf) & (df["race_date"] <= tt)]
        print(f"\n######## {wn} test={tf}〜{tt}  train {len(train):,} / test {len(test):,}", flush=True)

        # --- 合成2本目の実際の性質（一致率・単独 AUC）を test 窓で出す ---
        print("-- 合成2本目の性質（test 窓）--")
        for share in SHARE_GRID:
            col = sim_cols[share]
            t = test
            m1_top = t[t["prediction_mark"] == 1].set_index("race_key")["frame_no"]
            m2_top = t[t[col] == 1].set_index("race_key")["frame_no"]
            j = m1_top.to_frame("a").join(m2_top.to_frame("b"), how="inner")
            agree = float((j["a"] == j["b"]).mean())
            set1 = t[t["prediction_mark"] > 0].groupby("race_key")["frame_no"].apply(set)
            set2 = t[t[col] > 0].groupby("race_key")["frame_no"].apply(set)
            ov = pd.concat([set1.rename("a"), set2.rename("b")], axis=1).dropna()
            overlap = float(ov.apply(lambda r: len(r["a"] & r["b"]), axis=1).mean())
            auc2 = roc_auc_score(t[TARGET_COL_WT], t[col].map({0: 0.0, 4: 1.0, 3: 2.0, 2: 3.0, 1: 4.0}))
            print(f"   share={share:.1f}  ◎一致率 {agree*100:5.1f}%  印4車の重なり {overlap:.2f}/4"
                  f"  単独AUC {auc2:.4f}", flush=True)
        auc1 = roc_auc_score(test[TARGET_COL_WT],
                             test["prediction_mark"].map({0: 0.0, 4: 1.0, 3: 2.0, 2: 3.0, 1: 4.0}))
        print(f"   （参考）1本目 `prediction_mark` の単独AUC {auc1:.4f}", flush=True)

        # --- E2: 69特徴から ◎ を当てられるか ---
        tr2 = train.copy(); te2 = test.copy()
        m = lgb.LGBMClassifier(objective="binary", n_estimators=400, learning_rate=0.05,
                               num_leaves=31, random_state=42, deterministic=True,
                               force_row_wise=True, verbose=-1)
        m.fit(tr2[NOMARK], (tr2["prediction_mark"] == 1).astype(int))
        te2["q"] = m.predict_proba(te2[NOMARK])[:, 1]
        hit = tot = 0
        for _rk, g in te2.groupby("race_key"):
            if (g["prediction_mark"] == 1).sum() != 1:
                continue
            tot += 1
            hit += int(g.loc[g["q"].idxmax(), "prediction_mark"] == 1)
        rnd = float((1.0 / te2.groupby("race_key").size()).mean())
        print(f"-- E2 69特徴から ◎ を当てる: top1 {hit/tot*100:.2f}%  (n={tot:,} / 無作為 {rnd*100:.2f}%)",
              flush=True)

        res = {}
        for arm, cols in arms:
            a, w, t3, pr = [], [], [], []
            n = 0
            for seed in SEEDS:
                auc, x, y, z, n = fit_eval(train, test, cols, seed)
                a.append(auc); w.append(x); t3.append(y); pr.append(z)
            res[arm] = (a, w, t3, pr)
            print(f"== {arm} ({len(cols)}特徴) AUC {np.mean(a):.5f}±{np.std(a):.5f}"
                  f"  1位勝率 {np.mean(w)*100:.2f}%  1位3着内 {np.mean(t3)*100:.2f}%"
                  f"  上位2車そろい {np.mean(pr)*100:.2f}%  (n={n:,})", flush=True)
        b = res["baseline(70)"]
        for arm, _ in arms[1:]:
            v = res[arm]
            print(f"-- Δ({arm} − baseline)  ΔAUC {np.mean(v[0])-np.mean(b[0]):+.5f}"
                  f"  Δ1位勝率 {(np.mean(v[1])-np.mean(b[1]))*100:+.2f}pt"
                  f"  Δ1位3着内 {(np.mean(v[2])-np.mean(b[2]))*100:+.2f}pt"
                  f"  Δ上位2車そろい {(np.mean(v[3])-np.mean(b[3]))*100:+.2f}pt", flush=True)


if __name__ == "__main__":
    main()
