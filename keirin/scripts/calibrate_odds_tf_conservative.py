"""既に学習済みの三連単モデルへ、後から保守倍率を入れる（2026-08-29 新設）。

    KEIRIN_DB_URL=... PYTHONPATH=. .venv/bin/python \
      scripts/calibrate_odds_tf_conservative.py [--n-car 7] [--max-races 12000] [--write]

## なぜ要るのか

`odds_tf_meta.json` には `conservative`（下側分位）が無く、そのため
`netkeirin_submit_wt._conservative_board` は三連単の盤面へ**三連複の倍率**
（7車 0.8428）を掛けていた。券種が違うのに同じ倍率という状態で、
三連単のほうがばらつきは大きい（honest 2026 の ±2倍以内 80.6% ↔ 三連複 91.6%）。

以後の再学習では `train_odds_prediction_tf.py` が自分で書くので、
**このスクリプトが要るのは「今ある配布物へ後から入れる」ときだけ**。

## 🔴 較正の窓は **honest 側**（学習終端より後）を使う

三連複は学習窓で較正しているが、三連単で同じことはできない:
**学習に使った行を再現できない**。`build_dataset` のレース抽出は
`np.linspace` で決定的だが、採否は `wt_odds` の板が揃っているかに依存し、
その後に板が増えているため今日走らせると学習時より多くのレースが通る
（実測: メタの `n_train_races` 7,145 に対し再現は 8,266）。

窓を honest 側にすると **in-sample の甘さも入らない**（三連複は同じ量が
学習窓 0.8428 ↔ honest 窓 0.8048 と 5% 甘い）。三連複側の窓を揃える話は
`docs/oddspred_gap_2026_08_29.md` の「提案4」として別に置いてある。

⚠️ **母集団は全点**（三連複側と同じ定義）。買う帯だけで測ると別の数字になる
   （三連単は 20〜500倍の帯で p25 ≒ 0.82・全点だと 0.866。全点の分位は
   点数の51%を占める500倍超の帯に引っ張られる）。**商品としての最低払戻**は
   この列ではなく `backend/src/services/keirin_payout_floor.py` の表が持つ。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import scripts.train_odds_prediction_tf as T  # noqa: E402
from src.odds_prediction_tf import (  # noqa: E402
    FEATURE_NAMES, META_PATH, MODEL_DIR, conservative_quantiles,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-car", type=int, default=7, choices=(7, 9))
    ap.add_argument("--max-races", type=int, default=12000,
                    help="データセットの抽出上限（既定は学習スクリプトの既定）")
    ap.add_argument("--window", choices=("honest", "train"), default="honest",
                    help="較正に使う窓。既定 honest（学習終端より後）")
    ap.add_argument("--write", action="store_true", help="メタへ書き込む（既定は表示のみ）")
    a = ap.parse_args()

    import lightgbm as lgb
    T.N_CAR = a.n_car
    T.N_COMBO = a.n_car * (a.n_car - 1) * (a.n_car - 2)

    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    per = meta.get("per_n_car", {}).get(str(a.n_car))
    if not per:
        raise SystemExit(f"メタに {a.n_car}車がありません")
    train_end = str(per["train_end"])
    tgt = float(meta["target_sum"][str(a.n_car)])
    booster = lgb.Booster(model_file=str(MODEL_DIR / f"odds_tf_n{a.n_car}.txt"))

    df = T.build_dataset(a.max_races)
    tr = df[df.date <= train_end] if a.window == "train" else df[df.date > train_end]
    if tr.empty:
        raise SystemExit(f"{a.window} 窓が空です（train_end={train_end}）")
    print(f"{a.window} 窓 {len(tr)}行 / {tr.rk.nunique()}レース "
          f"({tr.date.min()}〜{tr.date.max()})")

    raw = np.clip(np.power(10.0, booster.predict(tr[list(FEATURE_NAMES)])), 1.0, None)
    p = pd.DataFrame({"rk": tr.rk.to_numpy(), "raw": raw})
    scale = p.groupby("rk").raw.transform(lambda s: (1 / s).sum() / tgt)
    cons = conservative_quantiles(tr.odds.to_numpy(), (p.raw * scale).to_numpy())
    print("保守倍率（実際/整合板 の下側分位）: "
          + "  ".join(f"{k}:{v:.4f}" for k, v in cons.items()))

    if not a.write:
        print("（--write を付けるとメタへ書き込みます）")
        return
    meta["per_n_car"][str(a.n_car)]["conservative"] = cons
    meta["per_n_car"][str(a.n_car)]["conservative_window"] = a.window
    tmp = META_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(META_PATH)
    print(f"メタを更新: {META_PATH}")
    print("🔴 VPS へは scripts/sync_models_to_vps.sh で配ること"
          "（メタだけ古いと三連単の odds_low が出なくなる）")


if __name__ == "__main__":
    main()
