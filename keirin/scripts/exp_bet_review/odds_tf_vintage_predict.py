"""探索用 vintage（train_end 2024-12-31）の三連単予測オッズを 2025 全レースへ出力し、本番モデルと品質比較する。

本番モデルは読むだけ（書かない）。入力 p3/pw は学習スクリプトと同じ walk-forward（vintage）予測。
    KEIRIN_DB_URL 必須（readonly）。
出力: data/exp_bet_review/odds_tf_vintage2024_2025.npz (race_key, n_car, combination, pred_odds)
      data/exp_bet_review/odds_tf_vintage_quality.json
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts"))
import lightgbm as lgb
import train_odds_prediction_tf as T
from src.odds_prediction_tf import FEATURE_NAMES, build_race_features

OUT = REPO / "data" / "exp_bet_review"
PROD = REPO / "data" / "models"
VIN = OUT / "models"
Y0, Y1 = "2025-01-01", "2025-12-31"


def spearman(a, b):
    return float(pd.Series(a).rank().corr(pd.Series(b).rank()))


def main():
    pm = json.loads((PROD / "odds_tf_meta.json").read_text())
    vm = json.loads((VIN / "odds_tf_meta.json").read_text())
    assert tuple(vm["feature_names"]) == FEATURE_NAMES == tuple(pm["feature_names"])
    rows, quality = [], {}
    for n in (7, 9):
        T.N_CAR = n; T.N_COMBO = n * (n - 1) * (n - 2)
        preds = {k: v for k, v in T._load_wf_preds().items() if Y0 <= v[2] <= Y1}
        keys = sorted(preds)
        bv = lgb.Booster(model_file=str(VIN / f"odds_tf_n{n}.txt"))
        bp = lgb.Booster(model_file=str(PROD / f"odds_tf_n{n}.txt"))
        tv, tp = vm["target_sum"][str(n)], pm["target_sum"][str(n)]
        lc, ls, lp_c, lp_s, nr, skipped = [], [], [], [], 0, 0
        per_race = []
        for i in range(0, len(keys), 800):
            ch = keys[i:i + 800]
            ent, boards = T._load_entries(ch), T._load_final_boards(ch)
            for rk in ch:
                p3, pw, _ = preds[rk]
                meta = ent.get(rk)
                if not meta or len(meta) != n:
                    skipped += 1; continue
                try:
                    combos, X = build_race_features(sorted(p3), p3, pw, meta)
                except Exception:
                    skipped += 1; continue
                def board(b, t):
                    raw = np.clip(np.power(10.0, b.predict(X)), 1.0, None)
                    return raw * ((1 / raw).sum() / t)
                ov, op = board(bv, tv), board(bp, tp)
                for c, o in zip(combos, ov):
                    rows.append((rk, n, "-".join(map(str, c)), float(o)))
                fb = boards.get(rk)
                if fb:
                    k = [j for j, c in enumerate(combos) if c in fb]
                    if len(k) >= 0.85 * len(combos):
                        y = np.log10([fb[combos[j]] for j in k])
                        a, b_ = np.log10(ov[k]), np.log10(op[k])
                        per_race.append((np.corrcoef(a, y)[0, 1], spearman(a, y),
                                         np.corrcoef(b_, y)[0, 1], spearman(b_, y),
                                         np.abs(a - y).mean(), np.abs(b_ - y).mean()))
                        nr += 1
            print(n, min(i + 800, len(keys)), len(keys), flush=True)
        q = np.array(per_race)
        quality[str(n)] = {
            "races_predicted": len(keys) - skipped, "skipped": skipped, "races_with_final": nr,
            "vintage": {"logcorr": q[:, 0].mean(), "spearman": q[:, 1].mean(), "logMAE": q[:, 4].mean()},
            "prod_insample": {"logcorr": q[:, 2].mean(), "spearman": q[:, 3].mean(), "logMAE": q[:, 5].mean()},
        }
        print(json.dumps(quality[str(n)], indent=1), flush=True)
    df = pd.DataFrame(rows, columns=["race_key", "n_car", "combination", "pred_odds"])
    np.savez_compressed(OUT / "odds_tf_vintage2024_2025.npz", race_key=df.race_key.to_numpy().astype("U14"), n_car=df.n_car.to_numpy().astype("int8"), combination=df.combination.to_numpy().astype("U5"), pred_odds=df.pred_odds.to_numpy().astype("float64"))  # pandas/numpy 版数に依存しない形式
    (OUT / "odds_tf_vintage_quality.json").write_text(json.dumps(quality, indent=1, default=float))
    print("saved", len(df), df.race_key.nunique())


if __name__ == "__main__":
    main()
