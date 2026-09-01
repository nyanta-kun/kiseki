#!/usr/bin/env python3
"""予測オッズモデルの特徴量を削ると最終オッズを再現しやすくなるか（2026-08-30）。

## 仮説（ユーザー）

現行は 62特徴。**その多くは「レースの予想」のための量**（競走得点・枠・級班・脚質・
ライン構成…）で、**「市場がどう値付けするか」には余計なパラメータ**かもしれない。
オッズ向けに特徴を絞ったほうが、最終オッズの偏りを再現できるのではないか。

## 測り方

`train_odds_prediction_tf.build_dataset` を**1回だけ**回し、
**同じデータ・同じハイパーパラメータ**で特徴量セットだけ差し替えて学習する。
評価は本番と同じ honest 分割（学習 ≤ `--train-end` / 検証 それ以降）。

🔴 見るのは logMAE だけにしない。**商品に効くのは下振れ側**
   （予測より確定が安いと、的中しても払戻が小さい）。`under05`（確定 < 予測×0.5）
   と `within2x` を必ず併記する。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/odds_feature_ablation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import numpy as np                                    # noqa: E402
import lightgbm as lgb                                # noqa: E402

from src.odds_prediction_tf import FEATURE_NAMES      # noqa: E402
import scripts.train_odds_prediction_tf as T          # noqa: E402

PROB = ["lp_pl", "lp_prod", "p3_1", "p3_2", "p3_3", "p3sum",
        "pw_1", "pw_2", "pw_3", "pwsum"]
MARK = ["mk_1", "mk_2", "mk_3", "n_marked"]
RANK = ["rk_1", "rk_2", "rk_3", "rksum", "rk_spread", "rw_1", "rw_2", "rw_3"]
FIELD = ["ent_p3", "ent_pw", "pw_max", "pw_gap12", "p3_max", "p3_sum2"]
LINE = ["same_line_12", "same_line_23", "same_line_13", "n_line_in",
        "line_order_12", "line_order_23", "lead_at_1", "solo_at_1", "lpos_1",
        "has_top_line", "solo_in", "lead_in", "n_lines", "n_solo", "max_line"]

SETS = {
    "現行（62）": list(FEATURE_NAMES),
    "確率のみ（10）": PROB,
    "確率+印（14）": PROB + MARK,
    "確率+印+順位（22）": PROB + MARK + RANK,
    "確率+印+場（20）": PROB + MARK + FIELD,
    "確率+印+順位+ライン（37）": PROB + MARK + RANK + LINE,
}


def report(y: np.ndarray, pred: np.ndarray) -> tuple[float, float, float]:
    """logMAE / 2倍以内 / 確定が予測の半分未満。"""
    lo = np.log10(np.clip(pred, 1.0, None))
    ly = np.log10(np.clip(y, 1.0, None))
    e = np.abs(ly - lo)
    return (float(e.mean()),
            float((e < np.log10(2)).mean()),
            float(((ly - lo) < np.log10(0.5)).mean()))


def main() -> int:
    T.N_CAR = 7
    df = T.build_dataset(12000)
    df["y"] = np.log10(df.odds)
    tr = df[df.date <= "2025-12-31"]
    te = df[df.date > "2025-12-31"]
    print(f"学習 {len(tr):,}行（{tr.rk.nunique():,}R）／ 検証 {len(te):,}行"
          f"（{te.rk.nunique():,}R）")
    params = dict(objective="regression", metric="l1", learning_rate=0.05,
                  num_leaves=63, min_data_in_leaf=200, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, verbose=-1)
    print(f"\n  {'特徴量セット':<24}{'logMAE':>9}{'2倍以内':>9}{'半分未満':>9}")
    for name, cols in SETS.items():
        b = lgb.train(params, lgb.Dataset(tr[cols], tr.y), num_boost_round=600)
        pred = np.power(10.0, b.predict(te[cols]))
        m, w, u = report(te.odds.to_numpy(), pred)
        print(f"  {name:<24}{m:>9.4f}{w:>9.1%}{u:>9.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
