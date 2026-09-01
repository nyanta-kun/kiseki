#!/usr/bin/env python3
"""型A の「3着に流す相手」を少し下の信頼度へずらす（2026-08-30・ユーザー指示）。

## 発端

型A（A_hit・3点）は確認窓 2026 で **素の的中 35.9% / 損益分岐に必要 2.79倍**
なのに **的中時の中央が 18,500円（1.85倍）＝的中の75%が分岐割れ**。
ROI 83.5% は上位25%の的中だけで作られていて、「鉄板・的中体験を売る」という
存在理由と噛み合っていない。

ユーザーの指摘:「一番くるかもしれない買い目に絞った結果、2倍の目でも外れる確率は
50%以上ある。最上位が割に合わないなら、**多少下の信頼度でオッズがついているところ**
を買うべき」。

## 測り方

型A の買い目は `1着=◎（指数1位）・2着=○（指数2位）固定・3着＝指数3,4,5番手`。
**3着に流す相手だけをずらす**（軸は動かさない）。

    現行     3着 = 指数 3,4,5 番手
    ずらし   3着 = 指数 4,5,6 / 5,6,7 番手
    広げる   3着 = 指数 3〜6 / 3〜7 番手

🔴 **配分は全案とも均等に揃える**。配分の違いで結論が動かないようにするため
   （型A は現行 `conf` だが、ダッチにしても的中中央は 18,500円で変わらないことを確認済み）。
🔴 **確定オッズは実際の決着でしか使わない**（当たった目の払戻）。買っていない目の
   確定オッズは `wt_odds` から引く＝**発走後の情報だが、比較のためだけに使う**。
   どの案も同じ条件なので比較は公平。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/type_a_partner_shift.py
"""
from __future__ import annotations

import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.database import get_connection            # noqa: E402
from src.marquee import is_fill_target             # noqa: E402
from src.type_lab import BUDGET, UNIT              # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_GATE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_GATE)                    # type: ignore[union-attr]

WINDOWS = {"探索 2025": ("2025-01-01", "2025-12-31"),
           "確認 2026": ("2026-01-01", "2026-08-26")}
#: 3着に流す相手の「指数順位」（1始まり）。現行は 3,4,5 番手。
SETS = {
    "現行 3,4,5": (3, 4, 5),
    "4,5,6": (4, 5, 6),
    "5,6,7": (5, 6, 7),
    "3〜6（4点）": (3, 4, 5, 6),
    "3〜7（5点）": (3, 4, 5, 6, 7),
    "4〜7（4点）": (4, 5, 6, 7),
}


def main() -> int:
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT race_key, race_date, axis_sum, race_type, p3_order "
            "FROM type_lab_picks WHERE mode = ? AND settled_at IS NOT NULL "
            "  AND plan_key = ? AND n_entries = 7 AND p3_order IS NOT NULL",
            ("paper", "A_hit"))]
        rows = [d for d in rows
                if is_fill_target(d.get("race_type"), None)
                or _GATE.passes_axis_gate(
                    "A_hit",
                    float(d["axis_sum"]) if d["axis_sum"] is not None else None, 7)]
        keys = sorted({d["race_key"] for d in rows})
        fin: dict = defaultdict(dict)
        top: dict = defaultdict(list)
        for i in range(0, len(keys), 300):
            ch = keys[i:i + 300]
            ph = ",".join("?" * len(ch))
            for r in c.execute(f"SELECT race_key, combination, odds_value FROM wt_odds "
                               f"WHERE bet_type = 'trifecta' AND race_key IN ({ph})",
                               tuple(ch)):
                d = dict(r)
                fin[d["race_key"]][d["combination"]] = float(d["odds_value"])
            for r in c.execute(f"SELECT race_key, frame_no, finish_order FROM wt_entries "
                               f"WHERE finish_order BETWEEN 1 AND 3 "
                               f"AND race_key IN ({ph})", tuple(ch)):
                d = dict(r)
                top[d["race_key"]].append((int(d["finish_order"]), int(d["frame_no"])))

    for win, (lo, hi) in WINDOWS.items():
        sub = []
        for d in rows:
            if not (lo <= str(d["race_date"]) <= hi):
                continue
            order = [int(x) for x in str(d["p3_order"]).replace(",", "-").split("-") if x]
            f = sorted(top.get(d["race_key"], []))
            if len(order) < 7 or len(f) < 3:
                continue
            actual = "-".join(str(c) for _o, c in f[:3])
            sub.append((order, actual, fin.get(d["race_key"], {})))
        print(f"\n=== {win}（{len(sub):,}レース・配分は全案とも均等）===")
        print(f"  {'3着の相手':<14}{'点':>3}{'素の的中':>9}{'ROI':>8}"
              f"{'的中中央':>10}{'分岐に必要':>11}{'分岐割れ':>9}")
        for lab, pos in SETS.items():
            k = len(pos)
            st = BUDGET // k // UNIT * UNIT
            bet = pay = hit = 0
            pays = []
            for order, actual, board in sub:
                a1, a2 = order[0], order[1]
                combos = [f"{a1}-{a2}-{order[p - 1]}" for p in pos]
                bet += st * k
                if actual in combos and board.get(actual):
                    p = int(st * board[actual])
                    pay += p
                    hit += 1
                    pays.append(p)
            if not hit:
                continue
            pays.sort()
            hr = hit / len(sub)
            need = (st * k) / hr
            broke = sum(1 for x in pays if x < need) / len(pays)
            print(f"  {lab:<14}{k:>3}{hr:>9.1%}{pay / bet:>8.1%}"
                  f"{pays[len(pays) // 2]:>9,}円{need:>10,.0f}円{broke:>9.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
