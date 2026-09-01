#!/usr/bin/env python3
"""予測オッズを「朝の2車系の板」で後段補正できるか（2026-08-30・ユーザー指示）。

## 発端

型ラボは予測オッズで **買い目の帯・点数・配分・入稿ゲート**を決めている。
ところが `odds_tf_meta.json` の実測で **2倍以内 83.5% / 確定が予測の半分未満 10.7%**。
2026-08-30 四日市6R は的中した目が 実/予 0.31 で払戻が投資とほぼ同額になった。

🔴 決定的なのは **62特徴がすべて自社モデル由来**（p3/pw・ライン・印・競走得点）で、
   **市場のオッズが1つも入っていない**こと。自社の確率だけで
   「市場が付ける値段」を当てにいっている。

## 前提の確認（済み）

- 朝のスナップショットは**全時間帯で板が100%埋まっている**（三連単も 99.9〜100%）
- ただし **朝の三連単の板をそのまま使うのは劣る**（logMAE 1.19 ↔ 予測 0.39）。
  210点に金が散っていないため外れ値が激しい
- **厚い2車系（2車単42点・2車複21点）には情報が残っている**:
  予測の残差との相関が 2車単 +0.112 / 2車複 +0.072（レース内で基準化）

## ここで測ること

    log(確定) = log(予測) + β1・x_exacta + β2・x_quinella + b

x は**レース内で基準化した** log(朝の板)。基準化するのはレース全体の水準差
（＝予測が既に持っている情報）を落として、**点ごとのズレだけ**を見るため。
基準化に使う量はすべて発走前に確定しているので、入稿時点で計算できる。

🔴 **学習窓と評価窓を分ける。** 係数を当てた窓で精度を測ると必ず良く出る。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/odds_market_correction.py
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from src.database import get_connection            # noqa: E402

# 🔴 **朝のスナップショットは 2026-06-08 以降しか無い**（4,226レース）。
#    窓が短いので、係数が動くかどうかは前向きに追う必要がある。
FIT = ("2026-06-08", "2026-07-31")
EVAL = ("2026-08-01", "2026-08-26")


def load(lo: str, hi: str) -> list[tuple[float, float, float, float]]:
    """`(log(確定/予測), 基準化した log朝2車単, 同 2車複, log(予測))` の一覧。"""
    with get_connection() as c:
        picks = [dict(r) for r in c.execute(
            "SELECT race_key, legs FROM type_lab_picks "
            "WHERE mode IN (?, ?) AND race_date BETWEEN ? AND ? "
            "  AND settled_at IS NOT NULL AND bet_type = ?",
            ("paper", "paper9", lo, hi, "trifecta"))]
        keys = sorted({p["race_key"] for p in picks})
        fin: dict = defaultdict(dict)
        ex: dict = defaultdict(dict)
        qu: dict = defaultdict(dict)
        for i in range(0, len(keys), 300):
            ch = keys[i:i + 300]
            ph = ",".join("?" * len(ch))
            for r in c.execute(f"SELECT race_key, combination, odds_value FROM wt_odds "
                               f"WHERE bet_type = 'trifecta' AND race_key IN ({ph})",
                               tuple(ch)):
                d = dict(r)
                fin[d["race_key"]][d["combination"]] = float(d["odds_value"])
            for r in c.execute(
                    f"SELECT race_key, bet_type, combination, odds_value "
                    f"FROM wt_odds_snapshot WHERE snapshot_type = 'morning' "
                    f"AND bet_type IN ('exacta','quinella') AND race_key IN ({ph})",
                    tuple(ch)):
                d = dict(r)
                (ex if d["bet_type"] == "exacta" else qu)[d["race_key"]][
                    d["combination"]] = float(d["odds_value"])

    out: list[tuple[float, float, float, float]] = []
    for p in picks:
        legs = p["legs"] if isinstance(p["legs"], list) else json.loads(p["legs"] or "[]")
        F, E, Q = fin.get(p["race_key"], {}), ex.get(p["race_key"], {}), qu.get(p["race_key"], {})
        if not (F and E and Q):
            continue
        buf = []
        for l in legs:
            cb = str(l["combo"])
            pr = float(l.get("pred_odds") or 0)
            fo = F.get(cb)
            if not (pr > 0 and fo and fo > 0):
                continue
            a, b, _c = cb.split("-")
            eo = E.get(f"{a}-{b}")
            qo = Q.get("-".join(sorted([a, b], key=int)))
            if not (eo and eo > 0 and qo and qo > 0):
                continue
            buf.append((math.log(fo / pr), math.log(eo), math.log(qo), math.log(pr)))
        if len(buf) < 3:
            continue
        # 🔴 レース内で基準化（レース全体の水準は予測が既に持っている）
        mx = sum(b[1] for b in buf) / len(buf)
        mq = sum(b[2] for b in buf) / len(buf)
        out.extend((y, x - mx, q - mq, lp) for y, x, q, lp in buf)
    return out


def fit2(rows) -> tuple[float, float, float]:
    """最小二乗（2変数）。外部ライブラリを増やさないため手で解く。"""
    n = len(rows)
    y = [r[0] for r in rows]
    x1 = [r[1] for r in rows]
    x2 = [r[2] for r in rows]
    my, m1, m2 = sum(y) / n, sum(x1) / n, sum(x2) / n
    s11 = sum((x1[i] - m1) ** 2 for i in range(n))
    s22 = sum((x2[i] - m2) ** 2 for i in range(n))
    s12 = sum((x1[i] - m1) * (x2[i] - m2) for i in range(n))
    s1y = sum((x1[i] - m1) * (y[i] - my) for i in range(n))
    s2y = sum((x2[i] - m2) * (y[i] - my) for i in range(n))
    det = s11 * s22 - s12 * s12
    if abs(det) < 1e-12:
        return 0.0, 0.0, my
    b1 = (s22 * s1y - s12 * s2y) / det
    b2 = (s11 * s2y - s12 * s1y) / det
    return b1, b2, my - b1 * m1 - b2 * m2


def report(lab: str, rows, b1: float, b2: float, b0: float) -> None:
    def stats(adj):
        e = [abs(r[0] - a) for r, a in zip(rows, adj)]
        e2 = sorted(e)
        n = len(e)
        return (sum(e) / n, sum(1 for x in e if x < math.log(2)) / n,
                sum(1 for r, a in zip(rows, adj) if (r[0] - a) < math.log(0.5)) / n)
    before = stats([0.0] * len(rows))
    after = stats([b0 + b1 * r[1] + b2 * r[2] for r in rows])
    print(f"  {lab}（{len(rows):,}点）")
    print(f"    {'':<10}{'logMAE':>9}{'2倍以内':>9}{'半分未満':>9}")
    print(f"    {'補正なし':<10}{before[0]:>9.4f}{before[1]:>9.1%}{before[2]:>9.1%}")
    print(f"    {'補正あり':<10}{after[0]:>9.4f}{after[1]:>9.1%}{after[2]:>9.1%}")


def main() -> int:
    tr = load(*FIT)
    te = load(*EVAL)
    b1, b2, b0 = fit2(tr)
    print(f"=== 係数（学習 {FIT[0]}〜{FIT[1]}・{len(tr):,}点）===")
    print(f"  log(確定/予測) = {b0:+.4f} {b1:+.4f}×2車単 {b2:+.4f}×2車複（レース内基準化）")
    print()
    report(f"学習窓 {FIT[0]}〜{FIT[1]}", tr, b1, b2, b0)
    print()
    report(f"**評価窓 {EVAL[0]}〜{EVAL[1]}**", te, b1, b2, b0)
    print()
    # 🔴 **改善が市場情報か水準補正かを切り分ける。** 切片だけで同じだけ良くなるなら
    #    2車系の板は何も足していない（相関 +0.11 は logMAE を動かさない）。
    print("  切り分け（評価窓）")
    report("  切片のみ（市場情報なし）", te, 0.0, 0.0, b0)
    report("  市場情報のみ（切片なし）", te, b1, b2, 0.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
