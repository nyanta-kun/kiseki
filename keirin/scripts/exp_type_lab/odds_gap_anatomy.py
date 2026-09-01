#!/usr/bin/env python3
"""予測オッズが大きく外れる目を解剖して「読めていない量」を探す（2026-08-30）。

## なぜ

精度向上を **12案**（γ補正・PL再パラメータ化・選手の人気・開催メタ・再学習・
脚別分位較正・分類器・下振れ分位配分・朝の三連単板・朝の2車系板での後段補正・
配分の差し替え・特徴量の削減）試して全滅した。**当てずっぽうに特徴を足すのをやめ、
外している側から逆に探す。**

## 測り方

本番と同じ honest 分割（学習 ≤2025-12-31 / 検証 2026年）で予測し、
残差 `log10(確定/予測)` を出す。そのうえで **モデルが見ていない属性**で群に割り、
残差の偏りと散らばりを比べる。

見るのは2つ:

    偏り（中央）  その群を系統的に高く/安く見積もっているか → 較正で直せる
    散らばり(IQR) その群だけ当たらないか → 情報が足りない

🔴 **下振れ（確定 < 予測）だけが商品に効く。** 予測より安いと、的中しても払戻が
   小さい（2026-08-30 四日市6R）。`under05` を必ず併記する。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/odds_gap_anatomy.py
"""
from __future__ import annotations

import datetime as dt
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import numpy as np                                    # noqa: E402
import lightgbm as lgb                                # noqa: E402

from src.database import get_connection               # noqa: E402
from src.odds_prediction_tf import FEATURE_NAMES      # noqa: E402
import scripts.train_odds_prediction_tf as T          # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))


def race_meta(keys: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with get_connection() as c:
        for i in range(0, len(keys), 500):
            ch = keys[i:i + 500]
            ph = ",".join("?" * len(ch))
            for r in c.execute(
                    f"SELECT race_key, race_type, cup_grade, grade, day_index, "
                    f"       distance, venue_id, start_at, n_entries "
                    f"FROM wt_races WHERE race_key IN ({ph})", tuple(ch)):
                d = dict(r)
                h = (dt.datetime.fromtimestamp(int(d["start_at"]), JST).hour
                     if d.get("start_at") else None)
                d["hour"] = h
                out[str(d["race_key"])] = d
    return out


def band(h):
    if h is None:
        return "unknown"
    return ("〜11時" if h < 11 else "11〜15時" if h < 15
            else "15〜18時" if h < 18 else "18時〜")


def show(title: str, groups: dict[str, np.ndarray], min_n: int = 2000) -> None:
    print(f"\n=== {title} ===")
    print(f"  {'群':<20}{'点数':>9}{'中央':>8}{'IQR':>8}{'半分未満':>9}")
    rows = [(k, v) for k, v in groups.items() if len(v) >= min_n]
    for k, v in sorted(rows, key=lambda kv: -np.median(kv[1])):
        q1, q2, q3 = np.percentile(v, [25, 50, 75])
        u = float((v < np.log10(0.5)).mean())
        print(f"  {k:<20}{len(v):>9,}{10 ** q2:>8.2f}{q3 - q1:>8.3f}{u:>9.1%}")


def main() -> int:
    T.N_CAR = 7
    df = T.build_dataset(12000)
    df["y"] = np.log10(df.odds)
    tr = df[df.date <= "2025-12-31"]
    te = df[df.date > "2025-12-31"].copy()
    params = dict(objective="regression", metric="l1", learning_rate=0.05,
                  num_leaves=63, min_data_in_leaf=200, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, verbose=-1)
    b = lgb.train(params, lgb.Dataset(tr[list(FEATURE_NAMES)], tr.y),
                  num_boost_round=600)
    te["resid"] = te.y.to_numpy() - b.predict(te[list(FEATURE_NAMES)])
    print(f"検証 {len(te):,}点 / {te.rk.nunique():,}R  "
          f"（残差 = log10(確定/予測)。1.00 が一致）")

    meta = race_meta(sorted(te.rk.unique().tolist()))
    res = te.resid.to_numpy()
    rk = te.rk.to_numpy()

    def by(fn, name, min_n=2000):
        g = defaultdict(list)
        for i in range(len(res)):
            m = meta.get(rk[i])
            if not m:
                continue
            g[str(fn(m))].append(res[i])
        show(name, {k: np.array(v) for k, v in g.items()}, min_n)

    by(lambda m: m.get("race_type") or "—", "レース種別（モデル未使用）", 4000)
    by(lambda m: band(m.get("hour")), "発走時刻帯（＝板の厚み・モデル未使用）")
    by(lambda m: f"グレード{m.get('cup_grade')}", "開催グレード（モデル未使用）")
    by(lambda m: f"{m.get('day_index')}日目", "開催日目（モデル未使用）")
    by(lambda m: f"{m.get('distance')}m", "バンク周長（モデル未使用）")

    # 予測オッズ帯（モデルは使っているが、帯ごとの偏りは較正で直せる）
    pred = te.y.to_numpy() - res
    g = defaultdict(list)
    for i in range(len(res)):
        o = 10 ** pred[i]
        k = ("〜10倍" if o < 10 else "10〜30倍" if o < 30 else "30〜100倍"
             if o < 100 else "100〜300倍" if o < 300 else "300倍〜")
        g[k].append(res[i])
    show("予測オッズ帯（参考・較正で直せる側）", {k: np.array(v) for k, v in g.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
