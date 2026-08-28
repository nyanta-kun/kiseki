"""「最低払戻」の保守倍率が**買う点数を見ていない**ことを測る（2026-08-29）。

    KEIRIN_ODDS_MODEL_DIR=data/backup/odds_model_20260816 \
      PYTHONPATH=. .venv/bin/python scripts/exp_oddspred_gap/04_floor_by_k.py

本番は `_conservative_trio_board` が **点数によらず一定の** c(p25)=0.843（7車）を
掛けて「最低払戻」を判定・表示している。だが判定している量は
**k点の最小値**で、独立な誤差がある限り k が増えるほど深く食い込む
（順序統計量）。実測すると 1点 1.07 → 5点 0.72 → 7点 0.54 と単調に落ちる。

つまり c は「1点あたりの下振れ分位」なのに「k点の最小」の約束に使われている。
2026-08-28 に乖離が目立ったのは点数の多い商品（7H1 8点・9C 6点・7S 5点）で、
これはこの取り違えの現れ方そのもの。

出力は2つ:
  A) vintage モデル（honest）で作った合成の台 … 機序と c_k の推定値
  B) 実入稿（`bet_detail`・予測ソースのみ）… 本番の点数別の実測（Aと一致する）
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

import numpy as np

from _common import CACHE, q  # noqa: E402

TRIO = CACHE / "oddspred_resid_trio_2026.pkl"
TF = CACHE / "oddspred_resid_tf_2026.pkl"
KS = (1, 2, 3, 4, 5, 6, 7, 8, 10)


def _table(path, name: str) -> None:
    import pandas as pd
    if not path.exists():
        print(f"  {path} がありません（03b_build_resid.py で作ること）")
        return
    d = pd.read_pickle(path).sort_values(["rk", "pred"])
    print(f"\n=== {name}（レース {d.rk.nunique():,}・レース内の安い順に k点をダッチ）")
    print("  k   確定/計画 最低払戻  中央    p25    p10   最低>=予算")
    g = list(d.groupby("rk"))
    for k in KS:
        rows = []
        for _, gg in g:
            if len(gg) < k:
                continue
            h = gg.iloc[:k]
            w = 1.0 / h.pred.to_numpy()
            w /= w.sum()
            plan, act = w * 10000 * h.pred.to_numpy(), w * 10000 * h.final.to_numpy()
            rows.append((act.min() / plan.min(), act.min() / 10000))
        a = np.array(rows)
        print(f" {k:>3}                      {np.median(a[:,0]):.3f}  {np.quantile(a[:,0],.25):.3f}"
              f"  {np.quantile(a[:,0],.10):.3f}   {100*(a[:,1]>=1).mean():5.1f}%")


def _submissions(date_from: str = "20260805") -> None:
    """実入稿での点数別。**予測ソースの行だけ**（板由来が混ざると比較にならない）。"""
    subs = q("""SELECT race_key, rank_key, bet_detail FROM keirin.netkeirin_submissions
                WHERE bet_detail IS NOT NULL AND race_key >= %s ORDER BY race_key""", (date_from,))
    keys = sorted({s["race_key"] for s in subs})
    fin: dict = defaultdict(dict)
    for bt, kf in (("trio", frozenset), ("trifecta", tuple)):
        for i in range(0, len(keys), 400):
            for r in q("SELECT race_key, combination, odds_value FROM keirin.wt_odds "
                       "WHERE bet_type=%s AND race_key = ANY(%s)", (bt, keys[i:i + 400])):
                fin[(r["race_key"], bt)][kf(int(v) for v in re.split(r"[-=>]", r["combination"]))] = \
                    float(r["odds_value"] or 0)
    by: dict = defaultdict(list)
    for s in subs:
        d = json.loads(s["bet_detail"])
        lines = d.get("lines") or []
        if not lines or lines[0].get("odds_source") != "predicted":
            continue
        bt = "trio" if lines[0]["bet_type"] == "3連複" else "trifecta"
        kf = frozenset if bt == "trio" else tuple
        board = fin.get((s["race_key"], bt))
        if not board:
            continue
        pts = []
        for ln in lines:
            o = ln.get("odds")
            v = board.get(kf(int(x) for x in re.split(r"[-=>]", str(ln["combo"]))))
            if not o or not v:
                pts = []
                break
            pts.append((int(ln["stake"]), float(o), float(v)))
        if pts:
            by[(bt, len(pts))].append(min(v * k for k, _, v in pts) / min(p * k for k, p, _ in pts))
    print(f"\n=== 実入稿（{date_from}〜・予測ソースのみ）")
    print("  券種      点数  件数   中央    p25")
    for k in sorted(by, key=lambda x: (x[0], x[1])):
        a = np.array(by[k])
        if len(a) < 4:
            continue
        print(f"  {k[0]:<9}{k[1]:>4}{len(a):>6}  {np.median(a):.3f}  {np.quantile(a,.25):.3f}")


def main() -> None:
    _table(TRIO, "三連複7車（vintage・honest 2026）")
    _table(TF, "三連単7車（train_end 2025-12-31・honest 2026）")
    _submissions()
    print("\n本番の保守倍率は点数によらず c(p25)=0.843（7車）。上の表と比べること。")


if __name__ == "__main__":
    main()
