"""ベース指数の「純化」A/B — 学習から波乱レースを抜くと残りへの識別が鋭くなるか（N-16）。

## 仮説

モデルは全レースを同じ重みで学習している。波乱レース（実力どおりに決まらなかった回）は
定義上ノイズが大きく、そこへ合わせにいくぶんだけ**平常レースへの識別が鈍っている**
可能性がある。学習から抜けば残りが鋭くなるのではないか。

**ルーティングは不要**（波乱を除いて学習し、全レースへ適用する）ので実装は最小。
配信時に「このレースは波乱か」を当てる必要が無いのが、N-15（商品振り分け）との違い。

## 波乱の定義は既存の正本を使う

`scripts/train_upset_screen.py::_target` と同じ——**決着した三連単オッズが車数ごとの
上位 q 分位以上**。🔴 絶対オッズ閾値にしてはいけない（基準率が 7車 9.8% / 9車 24.0% と
違うので「これは9車か」を当てるだけの指標になる）。

## アーム

| アーム | 学習に使う行 |
|---|---|
| `base` | 全レース |
| `-upset25` | 波乱上位25%（`train_upset_screen.TARGET_Q` と同じ）を除く |
| `-upset10` | 波乱上位10%を除く |
| **`-random25`** | **無作為に25%のレースを除く（対照群）** |

🔴 **対照群 `-random25` が要点。** 学習行を減らすこと自体が精度を動かすので、
   これが無いと「純化が効いた」のか「単にデータが減った」のかを分離できない。
   `-upset25` が `-random25` を上回って初めて「純化」に意味がある。

## 採用ライン（事前登録・事後に動かさない）

**確認窓 w3/w4 の両方で符号一致 かつ その平均 Δ1位3着内 ≥ +0.30pt**
（N-2 と同一。`[[keirin_form_features_ab_2026_08_20]]`）

🔴 **N-2 の教訓を持ち込む。** 効果量（+0.15〜0.29pt）は `min_date` の選択で動く量
（+0.20pt）と同オーダーだった。だから **データ範囲・窓・seed・特徴量は
`exp_form_features_ab.py` と完全に同じにし、動かすのは学習行の選び方だけ**にする。
2つ同時に変えたら、その時点で測定は無効。

Usage:
    PYTHONPATH=. .venv/bin/python scripts/exp_pure_base_ab.py [--windows w1,w2]
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

# 🔴 以下4つは `exp_form_features_ab.py` と**同一でなければならない**（上記の教訓）。
TRAIN_FROM = "2024-04-01"
MIN_DATE = "2023-06-01"
WINDOWS = {
    "w1": ("2026-04-13", "2026-07-15"),   # 掃引窓
    "w2": ("2026-01-01", "2026-04-12"),   # 掃引窓
    "w3": ("2025-10-01", "2025-12-31"),   # 確認窓
    "w4": ("2025-07-01", "2025-09-30"),   # 確認窓
}
SEEDS = [42, 101, 202, 303, 404]

ODDS_MAX = 9000.0        # winticket の未確定センチネル 9999.9 を捨てる
ARMS = ("base", "-upset25", "-upset10", "-random25")
DROP_Q = {"-upset25": 0.25, "-upset10": 0.10, "-random25": 0.25}


def load_win_odds() -> dict[str, tuple[float, int]]:
    """{race_key: (決着した三連単オッズ, 車数)}。

    ⚠️ 絞り込みは `wt_races` への JOIN で書く（`train_upset_screen._load` と同じ理由。
       `race_key IN (SELECT ...)` にすると `wt_odds` のプランが崩れて桁違いに遅くなる）。
    """
    sql = f"""
        WITH fin AS (
          SELECT e.race_key,
                 concat(max(CASE WHEN e.finish_order=1 THEN e.frame_no END), '-',
                        max(CASE WHEN e.finish_order=2 THEN e.frame_no END), '-',
                        max(CASE WHEN e.finish_order=3 THEN e.frame_no END)) combo
          FROM wt_entries e JOIN wt_races r USING(race_key)
          WHERE r.cancel=0 AND r.race_date >= '{MIN_DATE}'
          GROUP BY e.race_key)
        SELECT o.race_key, r.n_entries,
               max(CASE WHEN o.combination=f.combo THEN o.odds_value END) win_odds
        FROM wt_odds o
        JOIN wt_races r USING(race_key)
        JOIN fin f ON f.race_key=o.race_key
        WHERE o.bet_type='trifecta' AND r.cancel=0 AND r.race_date >= '{MIN_DATE}'
          AND o.odds_value>0 AND o.odds_value<{ODDS_MAX}
        GROUP BY o.race_key, r.n_entries
    """
    out: dict[str, tuple[float, int]] = {}
    with get_connection() as conn:
        for rk, ne, od in conn.execute(sql):
            if od is not None:
                out[str(rk)] = (float(od), int(ne or 0))
    return out


def upset_race_keys(keys: set[str], win: dict[str, tuple[float, int]],
                    q: float) -> set[str]:
    """車数ごとの分位で「波乱」レースを選ぶ（`train_upset_screen._target` と同じ規則）。"""
    by_ne: dict[int, list[str]] = {}
    for rk in keys:
        if rk in win:
            by_ne.setdefault(win[rk][1], []).append(rk)
    out: set[str] = set()
    for ne, rks in by_ne.items():
        thr = float(np.quantile([win[rk][0] for rk in rks], 1 - q))
        out |= {rk for rk in rks if win[rk][0] >= thr}
    return out


def race_metrics(test: pd.DataFrame, prob: np.ndarray, ne_map: dict) -> dict:
    """7車レースの 指数1位の勝率・3着内率、および二軸（上位2車とも3着内）。

    ⚠️ `exp_form_features_ab.py` と同一実装（数字を比較できるようにするため）。
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


def run_window(df: pd.DataFrame, win: dict, test_from: str, test_to: str) -> dict:
    with get_connection() as conn:
        ne_map = dict(conn.execute(
            "SELECT race_key, n_entries FROM wt_races WHERE race_date BETWEEN ? AND ?",
            (test_from, test_to)))
    train = df[(df["race_date"] >= TRAIN_FROM) & (df["race_date"] < test_from)]
    test = df[(df["race_date"] >= test_from) & (df["race_date"] <= test_to)]
    train_keys = set(train["race_key"].unique())
    print(f"\n######## 窓 test={test_from}〜{test_to}  "
          f"train {len(train):,}行 / {len(train_keys):,}R / test {len(test):,}行 ########",
          flush=True)

    drops = {"base": set()}
    for arm in ("-upset25", "-upset10"):
        drops[arm] = upset_race_keys(train_keys, win, DROP_Q[arm])
    # 🔴 対照群は**同じ数**だけ無作為に抜く（少なくなった効果を分離するため）。
    rng = np.random.default_rng(20260821)
    n_rand = len(drops["-upset25"])
    drops["-random25"] = set(rng.choice(sorted(train_keys), size=n_rand, replace=False))

    from sklearn.metrics import roc_auc_score
    res = {}
    for arm in ARMS:
        tr = train[~train["race_key"].isin(drops[arm])] if drops[arm] else train
        print(f"== {arm} ==  学習 {len(tr):,}行 / {tr['race_key'].nunique():,}R"
              f"（除外 {len(drops[arm]):,}R）", flush=True)
        acc = {"auc": [], "win": [], "top3": [], "two": []}
        n = 0
        for seed in SEEDS:
            m = lgb.LGBMClassifier(
                objective="binary", n_estimators=500, learning_rate=0.05,
                num_leaves=31, min_child_samples=20, subsample=0.8,
                colsample_bytree=0.8, random_state=seed,
                deterministic=True, force_row_wise=True, verbose=-1)
            m.fit(tr[FEATURE_COLS_WT], tr[TARGET_COL_WT])
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
    win = load_win_odds()
    print(f"  決着オッズ {len(win):,}R", flush=True)

    out = {w: run_window(df, win, *WINDOWS[w]) for w in args.windows.split(",")}

    print("\n" + "=" * 72)
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
    print("\n== 純化の正味（-upset25 − -random25。同じ行数を抜いた対照との差）==")
    for w, r in out.items():
        print(f"  {w}: 3着内 {(r['-upset25']['top3'] - r['-random25']['top3']) * 100:+.2f}pt"
              f" / 勝率 {(r['-upset25']['win'] - r['-random25']['win']) * 100:+.2f}pt")
    print("\n採用ライン: 確認窓 w3/w4 の両方で符号一致 かつ 平均 Δ1位3着内 ≥ +0.30pt")
    print("           かつ -random25 を上回ること（データ量の効果ではない証拠）")


if __name__ == "__main__":
    main()
