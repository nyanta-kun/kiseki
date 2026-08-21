"""落車・失格レースを学習から外す A/B（2026-08-21・ユーザー指示）。

## 仮説（N-16 の「波乱除外」とは別物）

N-16 は**結果が驚きだったレース**（決着オッズ上位分位）を抜いた——不成立だった。
あれは「難例を捨てた」ので鈍って当然だった、と読める。

こちらは**入力そのものが壊れたレース**を抜く。落車・失格が出たレースは
**当初想定のメンバーと実際に走ったメンバーが違う**。さらに、完走した有力選手も

  - 落車を避ける／外を回る／接触の影響を受ける
  - 展開が壊れて全力で走る場面が無くなる

という形で**能力どおりに走っていない**可能性がある。この着順を「実力の表現」として
学習させることは、正しい学習に寄与しない。

🔴 **これはユーザーの明示判断（2026-08-21）。** 2026-08-04 の調査は
   「1人でも落車したら除外は粗すぎる。汚染は得点上位が消えた 2.2% に限定される」と
   記録したが、その根拠は**勝者が1位評価である率**（＝結果側の指標）だけで、
   「完走した有力選手が本来の走りをできたか」は測っていない。

## この A/B が決めること

アームの設計自体が仮説を切り分ける:

| もし… | 結果はこうなる |
|---|---|
| 汚染がレース全体に及ぶ（ユーザーの主張） | **`-dnf_all` > `-dnf_top2`** |
| 汚染が「得点上位が消えた場合」に限る（旧記録） | `-dnf_top2` >= `-dnf_all` |
| どちらでもなく単に学習行が減っただけ | 両方 ≒ `-random` |

## 設計は N-2 / N-16 と同一（比較できるようにするため）

窓・seed・特徴量・`race_metrics`・採用ラインまで `exp_form_features_ab.py` と揃える。
🔴 **評価側（test）は一切いじらない。** 本番で当たるのは落車が起きうる母集団なので、
   test から DNF レースを抜くと「起きない世界」を測ることになる。

⚠️ 採用した場合の副作用: 学習母集団が変わると `pred_top3_pct` の水準が動くので、
   **絶対閾値のゲート**（7C 1.44 / 9C 1.30 / `RANK_7S_AXIS_SUM_MAX`）は再較正が要る。
   2026-08-04 の記録が「除外より重み付け」を勧めていたのはこの理由（`w_dnf0.5` で
   その版も同時に測る）。

Usage:
    PYTHONPATH=. .venv/bin/python scripts/exp_dnf_exclusion_ab.py [--windows w1,w2]
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
    FEATURE_COLS_WT, TARGET_COL_WT, build_features_wt, load_raw_data_wt,
)
from src.database import get_connection      # noqa: E402

# 🔴 `exp_form_features_ab.py` / `exp_pure_base_ab.py` と**同一**にすること。
TRAIN_FROM = "2024-04-01"
MIN_DATE = "2023-06-01"
WINDOWS = {
    "w1": ("2026-04-13", "2026-07-15"),   # 掃引窓
    "w2": ("2026-01-01", "2026-04-12"),   # 掃引窓
    "w3": ("2025-10-01", "2025-12-31"),   # 確認窓
    "w4": ("2025-07-01", "2025-09-30"),   # 確認窓
}
SEEDS = [42, 101, 202, 303, 404]
ARMS = ("base", "-dnf_all", "-dnf_top2", "w_dnf0.5", "-random")
DNF_WEIGHT = 0.5


def dnf_race_keys(df: pd.DataFrame) -> tuple[set[str], set[str]]:
    """(DNF を含むレース, 得点上位2車が DNF したレース)。

    🔴 `finish_order == 0` は **DNF（発走後の落車・失格・棄権）**。
       事前欠車は `wt_entries` に行自体が作られない（物理削除）ので、
       ここに混ざることはない。
    """
    fo = pd.to_numeric(df["finish_order"], errors="coerce")
    is_dnf = fo.fillna(0) < 1
    any_dnf = set(df.loc[is_dnf, "race_key"].unique())
    top2 = pd.to_numeric(df["score_rank"], errors="coerce") <= 2
    top2_dnf = set(df.loc[is_dnf & top2, "race_key"].unique())
    return any_dnf, top2_dnf


def race_metrics(test: pd.DataFrame, prob: np.ndarray, ne_map: dict) -> dict:
    """7車レースの 指数1位の勝率・3着内率、および二軸（上位2車とも3着内）。

    ⚠️ `exp_form_features_ab.py` と同一実装（数字を横に並べられるようにするため）。
    """
    t = test.copy()
    t["p"] = prob
    win = top3 = two = n = 0
    for rk, g in t.groupby("race_key"):
        if ne_map.get(rk) != 7 or len(g) != 7:
            continue
        fo = pd.to_numeric(g["finish_order"], errors="coerce")
        if (fo >= 1).sum() < 3:
            continue
        order = g["p"].values.argsort()[::-1]
        f1, f2 = fo.iloc[order[0]], fo.iloc[order[1]]
        f1 = 99 if pd.isna(f1) or f1 < 1 else f1
        f2 = 99 if pd.isna(f2) or f2 < 1 else f2
        n += 1
        win += 1 if f1 == 1 else 0
        top3 += 1 if f1 <= 3 else 0
        two += 1 if (f1 <= 3 and f2 <= 3) else 0
    if not n:
        return {"win": 0.0, "top3": 0.0, "two": 0.0, "n": 0}
    return {"win": win / n, "top3": top3 / n, "two": two / n, "n": n}


def run_window(df: pd.DataFrame, test_from: str, test_to: str) -> dict:
    with get_connection() as conn:
        ne_map = dict(conn.execute(
            "SELECT race_key, n_entries FROM wt_races WHERE race_date BETWEEN ? AND ?",
            (test_from, test_to)))
    train = df[(df["race_date"] >= TRAIN_FROM) & (df["race_date"] < test_from)]
    test = df[(df["race_date"] >= test_from) & (df["race_date"] <= test_to)]
    keys = set(train["race_key"].unique())
    any_dnf, top2_dnf = dnf_race_keys(train)
    any_dnf &= keys
    top2_dnf &= keys
    print(f"\n######## 窓 test={test_from}〜{test_to}  train {len(train):,}行 / "
          f"{len(keys):,}R（DNF含む {len(any_dnf):,}R = {len(any_dnf)/len(keys)*100:.1f}% / "
          f"うち得点上位2車が DNF {len(top2_dnf):,}R = {len(top2_dnf)/len(keys)*100:.1f}%）"
          f" / test {len(test):,}行 ########", flush=True)

    rng = np.random.default_rng(20260821)
    rand = set(rng.choice(sorted(keys), size=len(any_dnf), replace=False))

    from sklearn.metrics import roc_auc_score
    res = {}
    for arm in ARMS:
        w = None
        if arm == "base":
            tr = train
        elif arm == "-dnf_all":
            tr = train[~train["race_key"].isin(any_dnf)]
        elif arm == "-dnf_top2":
            tr = train[~train["race_key"].isin(top2_dnf)]
        elif arm == "-random":
            tr = train[~train["race_key"].isin(rand)]
        else:                                   # w_dnf0.5
            tr = train
            w = np.where(tr["race_key"].isin(any_dnf), DNF_WEIGHT, 1.0)
        print(f"== {arm} ==  学習 {len(tr):,}行 / {tr['race_key'].nunique():,}R"
              + (f"（重み {DNF_WEIGHT} を {int((w < 1).sum()):,}行へ）" if w is not None else ""),
              flush=True)
        acc = {"auc": [], "win": [], "top3": [], "two": []}
        n = 0
        for seed in SEEDS:
            m = lgb.LGBMClassifier(
                objective="binary", n_estimators=500, learning_rate=0.05,
                num_leaves=31, min_child_samples=20, subsample=0.8,
                colsample_bytree=0.8, random_state=seed,
                deterministic=True, force_row_wise=True, verbose=-1)
            m.fit(tr[FEATURE_COLS_WT], tr[TARGET_COL_WT], sample_weight=w)
            p = m.predict_proba(test[FEATURE_COLS_WT])[:, 1]
            acc["auc"].append(roc_auc_score(test[TARGET_COL_WT], p))
            r = race_metrics(test, p, ne_map)
            for k in ("win", "top3", "two"):
                acc[k].append(r[k])
            n = r["n"]
        res[arm] = {k: float(np.mean(v)) for k, v in acc.items()}
        res[arm]["auc_sd"] = float(np.std(acc["auc"]))
        print(f"   n={n}  AUC {res[arm]['auc']:.5f} ±{res[arm]['auc_sd']:.5f} / "
              f"1位勝率 {res[arm]['win']*100:.2f}% / "
              f"1位3着内 {res[arm]['top3']*100:.2f}% / 二軸 {res[arm]['two']*100:.2f}%",
              flush=True)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default="w1,w2")
    args = ap.parse_args()

    print("データ読み込み ...", flush=True)
    max_to = max(t for _, t in WINDOWS.values())
    df = build_features_wt(load_raw_data_wt(min_date=MIN_DATE, max_date=max_to))
    print(f"  特徴量構築 {len(df):,}行", flush=True)

    out = {w: run_window(df, *WINDOWS[w]) for w in args.windows.split(",")}

    print("\n" + "=" * 78)
    print("== 差分（各アーム − base）==")
    print(f"{'アーム':<12}" + "".join(f"{w:>30}" for w in out))
    for arm in ARMS:
        if arm == "base":
            continue
        line = f"{arm:<12}"
        for _, r in out.items():
            line += (f"  AUC{r[arm]['auc'] - r['base']['auc']:+.5f}"
                     f" 勝{(r[arm]['win'] - r['base']['win']) * 100:+.2f}"
                     f" 3着{(r[arm]['top3'] - r['base']['top3']) * 100:+.2f}"
                     f" 二軸{(r[arm]['two'] - r['base']['two']) * 100:+.2f}")
        print(line)
    print("\n== 仮説の切り分け ==")
    for w, r in out.items():
        d_all = (r["-dnf_all"]["top3"] - r["-random"]["top3"]) * 100
        d_t2 = (r["-dnf_top2"]["top3"] - r["base"]["top3"]) * 100
        print(f"  {w}: -dnf_all − -random(同数無作為) = {d_all:+.2f}pt"
              f" / -dnf_top2 − base = {d_t2:+.2f}pt")
    print("     レース全体が汚染 → -dnf_all が -random を明確に上回る")
    print("     得点上位の消失だけ  → -dnf_top2 が -dnf_all 以上")
    print("\n採用ライン: 確認窓 w3/w4 の両方で符号一致 かつ 平均 Δ1位3着内 ≥ +0.30pt")


if __name__ == "__main__":
    main()
