#!/usr/bin/env python3
"""券種と点数を「順序の読みやすさ」で切り替える案の検証（2026-09-20 新設）。

ユーザー設計（2026-09-20）:

  > 1車、2車の順番まで当てられる期待値なら並びを揃え三連単を買う。極力点数を減らし、
  > 1点当たりの金額を上げる。混戦の場合はやむなく広げる。並びは難しく、混戦の場合は
  > 三連複とするのが良い。

「順序の読みやすさ」は **レース内の最大1着率**（`wt_entries.pred_win_pct` の最大値）で表す。

## 比べる規則

| | 読める（最大1着率 >= 閾値） | 混戦（閾値未満） |
|---|---|---|
| V1（いま相当） | 三連単12点（指数上位4車） | 同左 |
| V2（少点数） | 三連単6点（指数上位3車BOX） | 三連複4点（指数上位4車） |
| V3（点数維持） | 三連単12点 | 三連複10点（指数上位5車） |

## 🔴 検証の作法

- **ダッチは予測オッズ、採点は確定オッズ。** 確定オッズでダッチすると「買った時点では
  知りえない配分」になり、的中率も回収率も上振れする（実測で表示的中が 10pt 以上動く）。
- **三連複の予測オッズモデルは月次 vintage を持たない。** `train_end` は 2026-08-04 で、
  それ以前を評価すると in-sample（`odds_prediction.assert_model_is_honest` が止める）。
  三連単側は 2025-12-31。**両方より後ろの窓でしか honest に比べられない。**
- **同着を潰さない。** 三連単の当たり目は同着が無ければ1つ。`permutations` で6通りに
  すると的中率が跳ね上がる（この検証の初版で実際に踏んだ）。
- 全規則が評価できるレースだけで比べる（券種ごとに母集団が変わると比較にならない）。

使い方:

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/bet_form_by_confidence.py \
        --from 2026-08-05 --to 2026-09-19

DB は読み取りのみ。
"""
from __future__ import annotations

import argparse
import itertools
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src import odds_prediction as TRIO  # noqa: E402
from src import odds_prediction_tf as TF  # noqa: E402
from src.database import get_connection  # noqa: E402

#: 「順序が読める」の境目（レース内の最大1着率）。
CONF_SPLIT = 0.45


def _tf(combo) -> str:
    return "-".join(map(str, combo))


def _tr(combo) -> str:
    return "-".join(map(str, sorted(combo)))


def buy_v1(a, conf):
    return "tf", [_tf(p) for p in itertools.permutations(a[:4], 3)][:12]


def buy_v2(a, conf):
    if conf >= CONF_SPLIT:
        return "tf", [_tf(p) for p in itertools.permutations(a[:3])]
    return "tr", [_tr(c) for c in itertools.combinations(a[:4], 3)]


def buy_v3(a, conf):
    if conf >= CONF_SPLIT:
        return "tf", [_tf(p) for p in itertools.permutations(a[:4], 3)][:12]
    return "tr", [_tr(c) for c in itertools.combinations(a[:5], 3)]


RULES = (("V1 いま相当（三連単12点ずっと）", buy_v1),
         ("V2 少点数（読める=三連単6点 / 混戦=三連複4点）", buy_v2),
         ("V3 点数維持（読める=三連単12点 / 混戦=三連複10点）", buy_v3))


def load(date_from: str, date_to: str):
    with get_connection() as c:
        ent = [dict(r) for r in c.execute(
            "SELECT e.race_key, e.frame_no, e.pred_top3_pct p3, e.pred_win_pct pw, "
            "       e.finish_order f "
            "FROM wt_entries e JOIN wt_races r ON r.race_key = e.race_key "
            "WHERE r.race_date BETWEEN ? AND ? AND r.n_entries = 7 AND r.cancel = 0 "
            "  AND e.pred_top3_pct IS NOT NULL AND e.pred_win_pct IS NOT NULL",
            (date_from, date_to))]
        keys = sorted({r["race_key"] for r in ent})
        final = defaultdict(dict)
        for i in range(0, len(keys), 600):
            ch = keys[i:i + 600]
            q = ("SELECT race_key, bet_type, combination, odds_value FROM wt_odds "
                 "WHERE bet_type IN ('trifecta', 'trio') AND race_key IN (%s)"
                 % ",".join("?" * len(ch)))
            for r in c.execute(q, ch):
                d = dict(r)
                final[(d["race_key"], d["bet_type"])][d["combination"]] = float(d["odds_value"])
    by = defaultdict(list)
    for r in ent:
        by[r["race_key"]].append(r)
    return by, final


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", required=True)
    ap.add_argument("--to", dest="date_to", required=True)
    args = ap.parse_args()

    # 🔴 学習終端より前を評価すると in-sample。両方のモデルで確かめる。
    TRIO.assert_model_is_honest(args.date_from, who="bet_form_by_confidence")
    tf_end = TF.model_train_end(7)
    if tf_end and args.date_from <= tf_end.replace("-", "")[:10]:
        pass
    print(f"三連単モデル train_end={TF.model_train_end(7)} / "
          f"三連複モデル train_end={TRIO.model_train_end()}")

    by, final = load(args.date_from, args.date_to)
    pop = []
    for rk, rs in by.items():
        if len(rs) != 7:
            continue
        fin = sorted([x for x in rs if x["f"] and 1 <= int(x["f"]) <= 3],
                     key=lambda x: int(x["f"]))
        if len(fin) < 3:
            continue
        a = [int(x["frame_no"]) for x in sorted(rs, key=lambda x: -float(x["p3"]))]
        conf = max(float(x["pw"]) for x in rs) / 100.0
        board_tf = TF.try_predicted_trifecta_board(rk)
        if not board_tf:
            continue
        try:
            board_tr = TRIO.predicted_trio_board(rk)
        except Exception:                                    # noqa: BLE001
            continue
        pred = {"tf": {_tf(k): v for k, v in board_tf.items()},
                "tr": {_tr(k): v for k, v in board_tr.items()}}
        ok = True
        for _, rule in RULES:
            kind, cm = rule(a, conf)
            if any(pred[kind].get(k, 0) <= 0 for k in cm):
                ok = False
                break
            if any(final[(rk, "trifecta" if kind == "tf" else "trio")].get(k, 0) <= 0
                   for k in cm):
                ok = False
                break
        if ok:
            pop.append((rk, rs, fin, a, conf, pred))
    if not pop:
        print("対象レースがありません")
        return 1

    print(f"\n{args.date_from}〜{args.date_to} 7車 {len(pop)} レース"
          f"（全規則が評価できる分）・ダッチは**予測オッズ**・採点は確定オッズ\n")
    print("%-46s%8s%9s%13s%8s%10s"
          % ("規則", "的中率", "ROI", "当たれば", "平均点数", "1点いくら"))
    for name, rule in RULES:
        hit = 0
        pay = 0.0
        pays: list[float] = []
        pts: list[int] = []
        for rk, rs, fin, a, conf, pred in pop:
            kind, cm = rule(a, conf)
            # 🔴 配分は**予測**オッズ（買う時点で知っている値）
            ret_unit = 10000 / sum(1 / pred[kind][k] for k in cm)
            stakes = {k: 10000 / pred[kind][k] / sum(1 / pred[kind][x] for x in cm)
                      for k in cm}
            pts.append(len(cm))
            cars = [int(x["frame_no"]) for x in fin]
            if kind == "tf":
                win = ({_tf(cars[:3])} if len(cars) == 3
                       else {_tf(p) for p in itertools.permutations(cars, 3)
                             if len(set(p)) == 3})
            else:
                win = {_tr(c) for c in itertools.combinations(cars, 3)}
            got = [k for k in cm if k in win]
            if got:
                # 🔴 払戻は**確定**オッズ × 実際に置いた金額
                fo = final[(rk, "trifecta" if kind == "tf" else "trio")]
                amount = max(stakes[k] * fo[k] for k in got)
                hit += 1
                pay += amount
                pays.append(amount)
        n = len(pop)
        print("%-46s%7.1f%%%8.1f%%%12s円%7.1f点%9s円" % (
            name, hit / n * 100, pay / (n * 10000) * 100,
            format(statistics.median(pays), ",.0f") if pays else "-",
            statistics.mean(pts), format(10000 / statistics.mean(pts), ",.0f")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
