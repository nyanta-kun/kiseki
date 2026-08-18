#!/usr/bin/env python3
"""レース選別: p3合計ゲート vs 二軸的中を直接学習するメタモデル（2026-08-18）

## 問い

7C の選別は `pred_top3_pct 上位2車の合計 >= 1.44`（`RANK_7C_P3_SUM_MIN`）という
**1次元のヒューリスティック**。二軸的中（軸2車がともに3着内）を目的にしているなら、
**その事象を直接学習したレース単位のモデル**のほうが同じ件数でより当たる帯を選べるはず。

⚠️ 母集団の**人手セグメント**（場・グレード・種別）は否定記録が複数ある
（memory `keirin_layer2_pair_ceiling_2026_08_10`）。本件はそれと違い、
**学習させる**（＝木に分岐を決めさせる）ので同じ轍ではない。

## 設計（リーク無し）

| 段 | 窓 | 使うモデル |
|---|---|---|
| 層1学習 | 2023-07〜2024-12 (FIT) | — |
| メタ学習 | 2025-01〜06 (CAL) | FIT モデルの予測（out-of-sample） |
| 評価 | VAL 2025-07〜2026-02 / TEST 2026-03〜07-15 | TRAIN(〜2025-06) モデルの予測 |

比較は**同じ採用件数**で行う（閾値ではなく採用率で揃える）。

使い方:
    KEIRIN_FEATURE_CACHE=1 PYTHONPATH=. .venv/bin/python scripts/exp_race_selection_meta_wt.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.metrics import roc_auc_score  # noqa: E402

from src.preprocessing.feature_wt import FEATURE_COLS_WT, load_features_wt  # noqa: E402

FIT = ("2023-07-01", "2024-12-31")
CAL = ("2025-01-01", "2025-06-30")
TRAIN = ("2023-07-01", "2025-06-30")
VAL = ("2025-07-01", "2026-02-28")
TEST = ("2026-03-01", "2026-07-15")
CARS = 7

LGB1 = dict(objective="binary", n_estimators=500, learning_rate=0.05, num_leaves=31,
            min_child_samples=20, subsample=0.8, colsample_bytree=0.8,
            random_state=42, verbose=-1)
LGB2 = dict(objective="binary", n_estimators=300, learning_rate=0.04, num_leaves=15,
            min_child_samples=60, subsample=0.8, colsample_bytree=0.8,
            random_state=42, verbose=-1)

META_COLS = [
    "p1", "p2", "p3", "p4", "sum2", "gap12", "gap23", "gap24", "p_sd", "p_ent",
    "tail_max", "same_line", "ax1_leader", "ax2_leader", "ax1_line_size",
    "n_lines", "max_line_size", "n_isolated", "n_senko",
    "rp_top", "rp_sd", "rp_gap12", "grade_enc", "bank_length_enc",
    "is_final", "is_semi", "cls_max", "cls_min",
]


def win(df, w):
    return df[(df["race_date"] >= w[0]) & (df["race_date"] <= w[1])]


def fit_layer1(tr):
    m = lgb.LGBMClassifier(**LGB1)
    m.fit(tr.reindex(columns=FEATURE_COLS_WT).fillna(0), tr["top3_flag"])
    return m


def race_table(ev: pd.DataFrame, p: np.ndarray) -> pd.DataFrame:
    """エントリ表 → レース単位の特徴 + 二軸的中フラグ。"""
    d = ev.assign(_p=p)
    d = d[d["finish_order"] >= 1]
    n = d.groupby("race_key")["frame_no"].transform("count")
    d = d[n == CARS].sort_values(["race_key", "_p"], ascending=[True, False])
    g = d.groupby("race_key", sort=False)
    ps = np.stack(g["_p"].apply(lambda s: s.values[:CARS]).values)
    first, second = g.nth(0), g.nth(1)
    ent = -(ps / ps.sum(1, keepdims=True) *
            np.log(ps / ps.sum(1, keepdims=True) + 1e-12)).sum(1)
    out = pd.DataFrame({
        "race_key": list(g.groups.keys()),
        "p1": ps[:, 0], "p2": ps[:, 1], "p3": ps[:, 2], "p4": ps[:, 3],
        "p_sd": ps.std(1), "p_ent": ent, "tail_max": ps[:, 2:].max(1),
        "same_line": (first["line_group"].values == second["line_group"].values).astype(float),
        "ax1_leader": first["is_line_leader"].values,
        "ax2_leader": second["is_line_leader"].values,
        "ax1_line_size": first["line_size"].values,
        "n_lines": first["n_lines"].values,
        "n_senko": first["n_senko"].values if "n_senko" in first else 0.0,
        "max_line_size": g["line_size"].max().values,
        "n_isolated": g["is_isolated"].sum().values,
        "rp_top": g["race_point"].max().values,
        "rp_sd": g["race_point"].std().values,
        "grade_enc": first["grade_enc"].values,
        "bank_length_enc": first["bank_length_enc"].values,
        "cls_max": g["player_class_enc"].max().values,
        "cls_min": g["player_class_enc"].min().values,
        "hit": g["top3_flag"].apply(lambda s: float(s.values[:2].sum() == 2)).values,
        "race_date": first["race_date"].values,
        "race_type": first["race_type"].values if "race_type" in first else "",
    })
    rp2 = g["race_point"].apply(lambda s: np.sort(s.values)[::-1][:2])
    out["rp_gap12"] = [a[0] - a[1] for a in rp2]
    out["sum2"] = out["p1"] + out["p2"]
    out["gap12"] = out["p1"] - out["p2"]
    out["gap23"] = out["p2"] - out["p3"]
    out["gap24"] = out["p2"] - out["p4"]
    rt = out["race_type"].fillna("").astype(str)
    out["is_semi"] = rt.str.contains("準決").astype(float)
    out["is_final"] = (rt.str.contains("決勝") & ~rt.str.contains("準決")).astype(float)
    return out


def report(name: str, t: pd.DataFrame, meta) -> None:
    t = t.copy()
    t["meta"] = meta.predict_proba(t.reindex(columns=META_COLS).fillna(0))[:, 1]
    base = t["hit"].mean() * 100
    print(f"\n■ {name}  n={len(t):,}R  全体の二軸的中 {base:.2f}%")
    print(f"   選別指標としての AUC:  p3合計 {roc_auc_score(t['hit'], t['sum2']):.4f}"
          f"   メタ {roc_auc_score(t['hit'], t['meta']):.4f}")
    rows = []
    for frac in (0.15, 0.25, 0.35, 0.50, 0.65):
        k = int(len(t) * frac)
        a = t.nlargest(k, "sum2")["hit"].mean() * 100
        b = t.nlargest(k, "meta")["hit"].mean() * 100
        ov = len(set(t.nlargest(k, "sum2").index) & set(t.nlargest(k, "meta").index)) / k
        rows.append({"採用率": f"{frac*100:.0f}%", "件数": k,
                     "p3合計": round(a, 2), "メタ": round(b, 2),
                     "差": round(b - a, 2), "重なり": f"{ov*100:.0f}%"})
    print(pd.DataFrame(rows).to_string(index=False))
    # 本番相当（p3合計 >= 1.44）
    sel = t[t["sum2"] >= 1.44]
    if len(sel):
        k = len(sel)
        b = t.nlargest(k, "meta")["hit"].mean() * 100
        print(f"   本番相当 p3合計>=1.44: 通過 {k/len(t)*100:.1f}% "
              f"({k:,}R)  的中 {sel['hit'].mean()*100:.2f}%  "
              f"→ 同件数をメタで選ぶと {b:.2f}%  差 {b - sel['hit'].mean()*100:+.2f}pt")


def main() -> None:
    print("特徴量ロード中…", flush=True)
    df = load_features_wt("2022-12-01", "2026-07-15")
    df = df[df["race_date"] >= FIT[0]].reset_index(drop=True)

    print("層1（FIT）学習中…", flush=True)
    m_fit = fit_layer1(win(df, FIT))
    cal = win(df, CAL)
    t_cal = race_table(cal, m_fit.predict_proba(
        cal.reindex(columns=FEATURE_COLS_WT).fillna(0))[:, 1])
    print(f"  メタ学習データ {len(t_cal):,}R  二軸的中 {t_cal['hit'].mean()*100:.2f}%")

    meta = lgb.LGBMClassifier(**LGB2)
    meta.fit(t_cal.reindex(columns=META_COLS).fillna(0), t_cal["hit"])
    imp = pd.Series(meta.feature_importances_, index=META_COLS).sort_values(ascending=False)
    print("  メタの重要度 上位8:", ", ".join(f"{k}={v}" for k, v in imp.head(8).items()))

    print("層1（TRAIN）学習中…", flush=True)
    m_tr = fit_layer1(win(df, TRAIN))
    dump = {"CAL": t_cal}
    for name, w in (("VAL", VAL), ("TEST", TEST)):
        ev = win(df, w)
        t = race_table(ev, m_tr.predict_proba(
            ev.reindex(columns=FEATURE_COLS_WT).fillna(0))[:, 1])
        dump[name] = t
        report(name, t, meta)
    import pickle as _p
    out = Path("/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/"
               "10b197b5-6e8c-4817-8963-24f9f82158cc/scratchpad/race_tables.pkl")
    _p.dump(dump, open(out, "wb"))
    print(f"\nレース表を保存: {out}")


if __name__ == "__main__":
    main()
