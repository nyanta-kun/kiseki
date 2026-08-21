"""軸1を「脚質×拮抗」または「落車リスク」で入れ替える A/B（2026-08-21・ユーザー指示）。

## 何を測るか

軸が落車すると即外れなので、軸選定に「リスクが少ない」を入れられないか、という提案。
記述統計（`look-ahead` な `pred_top3_pct` 使用・上限把握のみ）では:

| 案 | 記述統計での見え方 |
|---|---|
| 落車リスクで入れ替え | ほぼ全セルで負。天井が **+1.20pt**（軸1の DNF 率）なのに、軸を2位へ落とす平均コストが **12.00pt** |
| **脚質×拮抗**（1位=追 ∧ 2位=逃 ∧ 指数差≤2.0pt） | n=1,208 で **+2.24pt** |

本スクリプトはこれを **honest**（窓ごとに学習した予測）で測り直す。

🔴 **閾値 2.0pt は掃引窓で見つけた値なので、採否は確認窓 w3/w4 で決める。**
🔴 **本番の軸選定関数を通す**（`rank_7s_select_axis` の3ヘッド版）。記述統計は
   3着内率で並べたが、本番の軸1は **1着率モデルの最上位**で定義が違う。

## アームは「同じ予測」への後処理

モデルは窓×seed ごとに3本（win / top3 / bad）だけ学習し、アームはその予測への
規則の違いにする。**seed ノイズが相殺され、対応のある比較になる**。

Usage:
    PYTHONPATH=. .venv/bin/python scripts/exp_axis_risk_swap.py [--windows w1,w2]
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.preprocessing.feature_wt import (   # noqa: E402
    FEATURE_COLS_WT, build_features_wt, load_raw_data_wt,
)
from src.database import get_connection      # noqa: E402
from src.strategy_wt import rank_7s_select_axis   # noqa: E402

TRAIN_FROM = "2024-04-01"
MIN_DATE = "2023-06-01"
WINDOWS = {
    "w1": ("2026-04-13", "2026-07-15"),
    "w2": ("2026-01-01", "2026-04-12"),
    "w3": ("2025-10-01", "2025-12-31"),
    "w4": ("2025-07-01", "2025-09-30"),
}
SEEDS = [42, 101, 202, 303, 404]
RISK_K = 200.0            # 落車性向の縮約（経験ベイズ）
P3_GAP_MAX = 2.0          # 「拮抗」の閾値（掃引窓で見つけた値・確認窓で採否を決める）
RISK_GAP_MIN = 0.005      # 比較アーム用
ARMS = ("base", "swap_style", "swap_risk", "oracle_dnf")


def crash_risk(df: pd.DataFrame) -> pd.Series:
    """point-in-time の落車性向（そのレースより前の実績のみ・経験ベイズ縮約）。"""
    d = df[["race_key", "player_id", "race_date", "finish_order"]].copy()
    d["dnf"] = (pd.to_numeric(d.finish_order, errors="coerce").fillna(0) < 1).astype(int)
    d = d.sort_values(["player_id", "race_date", "race_key"])
    p0 = d.dnf.mean()
    g = d.groupby("player_id")["dnf"]
    risk = (g.cumsum() - d["dnf"] + RISK_K * p0) / (g.cumcount() + RISK_K)
    return risk.reindex(df.index)


def fit_heads(train: pd.DataFrame, seed: int) -> dict:
    """3ヘッド（win / top3 / bad）。本番の軸選定が要求する3つ。"""
    fo = pd.to_numeric(train["finish_order"], errors="coerce").fillna(0)
    y = {"win": (fo == 1).astype(int),
         "top3": fo.between(1, 3).astype(int),
         # bad6 = **完走して**6着以下（落車は含めない・本番と同じ定義）
         "bad": ((fo >= 6)).astype(int)}
    out = {}
    for k, yy in y.items():
        m = lgb.LGBMClassifier(
            objective="binary", n_estimators=500, learning_rate=0.05,
            num_leaves=31, min_child_samples=20, subsample=0.8,
            colsample_bytree=0.8, random_state=seed,
            deterministic=True, force_row_wise=True, verbose=-1)
        m.fit(train[FEATURE_COLS_WT], yy)
        out[k] = m
    return out


def evaluate(test: pd.DataFrame, preds: dict, ne_map: dict) -> dict:
    """アームごとに 軸1の勝率・3着内率、二軸的中を返す。"""
    t = test.copy()
    for k, v in preds.items():
        t[f"p_{k}"] = v
    acc = {a: {"win": 0, "top3": 0, "two": 0} for a in ARMS}
    n = 0
    for rk, g in t.groupby("race_key"):
        if ne_map.get(rk) != 7 or len(g) != 7:
            continue
        fo = pd.to_numeric(g["finish_order"], errors="coerce")
        if (fo >= 1).sum() < 3:
            continue
        fr = g["frame_no"].astype(int).tolist()
        wp = dict(zip(fr, g["p_win"]))
        tp = dict(zip(fr, g["p_top3"]))
        bp = dict(zip(fr, g["p_bad"]))
        sel = rank_7s_select_axis(wp, tp, bp)
        if sel is None:
            continue
        a1, a2, _ = sel
        n += 1
        info = g.set_index(g["frame_no"].astype(int))
        # 入れ替え候補 = 軸1 以外で 1着率が最も高い車（＝「レース内2番目」）
        cand = max((f for f in fr if f != a1), key=lambda f: wp[f])
        gap = (tp[a1] - tp[cand]) * 100          # 指数差（pt）
        rgap = float(info.at[a1, "risk"] - info.at[cand, "risk"]) * 100
        st1 = info.at[a1, "style"]
        stc = info.at[cand, "style"]

        def place(f):
            v = pd.to_numeric(info.at[f, "finish_order"], errors="coerce")
            return 99 if pd.isna(v) or v < 1 else int(v)

        for arm in ARMS:
            x1, x2 = a1, a2
            if arm == "swap_style":
                if st1 == "追" and stc == "逃" and gap <= P3_GAP_MAX:
                    x1, x2 = cand, (a1 if a2 == cand else a2)
            elif arm == "swap_risk":
                if rgap >= RISK_GAP_MIN * 100 and gap <= P3_GAP_MAX:
                    x1, x2 = cand, (a1 if a2 == cand else a2)
            elif arm == "oracle_dnf":
                if place(a1) == 99 and place(cand) != 99:
                    x1, x2 = cand, (a1 if a2 == cand else a2)
            f1, f2 = place(x1), place(x2)
            acc[arm]["win"] += 1 if f1 == 1 else 0
            acc[arm]["top3"] += 1 if f1 <= 3 else 0
            acc[arm]["two"] += 1 if (f1 <= 3 and f2 <= 3) else 0
    return {a: {k: v / max(n, 1) for k, v in d.items()} | {"n": n}
            for a, d in acc.items()}


def run_window(df: pd.DataFrame, test_from: str, test_to: str) -> dict:
    with get_connection() as conn:
        ne_map = dict(conn.execute(
            "SELECT race_key, n_entries FROM wt_races WHERE race_date BETWEEN ? AND ?",
            (test_from, test_to)))
    train = df[(df["race_date"] >= TRAIN_FROM) & (df["race_date"] < test_from)]
    test = df[(df["race_date"] >= test_from) & (df["race_date"] <= test_to)]
    print(f"\n######## 窓 test={test_from}〜{test_to}  "
          f"train {len(train):,}行 / test {len(test):,}行 ########", flush=True)
    agg = {a: {"win": [], "top3": [], "two": []} for a in ARMS}
    n = 0
    for seed in SEEDS:
        heads = fit_heads(train, seed)
        preds = {k: m.predict_proba(test[FEATURE_COLS_WT])[:, 1] for k, m in heads.items()}
        r = evaluate(test, preds, ne_map)
        n = r["base"]["n"]
        for a in ARMS:
            for k in ("win", "top3", "two"):
                agg[a][k].append(r[a][k])
        print(f"  seed={seed} 済", flush=True)
    res = {a: {k: float(np.mean(v)) for k, v in d.items()} for a, d in agg.items()}
    for a in ARMS:
        print(f"== {a:<12} n={n}  1位勝率 {res[a]['win']*100:5.2f}% / "
              f"1位3着内 {res[a]['top3']*100:5.2f}% / 二軸 {res[a]['two']*100:5.2f}%",
              flush=True)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default="w1,w2")
    args = ap.parse_args()
    print("データ読み込み ...", flush=True)
    max_to = max(t for _, t in WINDOWS.values())
    df = build_features_wt(load_raw_data_wt(min_date=MIN_DATE, max_date=max_to))
    df["risk"] = crash_risk(df)
    print(f"  特徴量構築 {len(df):,}行 / 落車性向 sd {df.risk.std()*100:.3f}pt", flush=True)

    out = {w: run_window(df, *WINDOWS[w]) for w in args.windows.split(",")}
    print("\n" + "=" * 72)
    print("== 差分（各アーム − base）==")
    print(f"{'アーム':<14}" + "".join(f"{w:>26}" for w in out))
    for arm in ARMS:
        if arm == "base":
            continue
        line = f"{arm:<14}"
        for _, r in out.items():
            line += (f"  勝{(r[arm]['win'] - r['base']['win'])*100:+.2f}"
                     f" 3着{(r[arm]['top3'] - r['base']['top3'])*100:+.2f}"
                     f" 二軸{(r[arm]['two'] - r['base']['two'])*100:+.2f}")
        print(line)
    print("\n採用ライン: 確認窓 w3/w4 の両方で符号一致 かつ 平均 Δ軸1 3着内 ≥ +0.30pt")
    print("           oracle_dnf は天井（落車を完全予知した場合）であって案ではない")


if __name__ == "__main__":
    main()
