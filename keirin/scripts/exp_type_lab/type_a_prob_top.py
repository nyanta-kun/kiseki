#!/usr/bin/env python3
"""型A を「全210点を確率で並べて上位k点」に替えたら（2026-08-30・ユーザー指示）。

## 発端

型A（A_hit）は **1着=◎・2着=○ を固定して3着に3車流す**固定構成。
確認窓 2026 で 素の的中 35.9% なのに **的中の75%が損益分岐（2.79倍）割れ**。

配分の差し替え・合成オッズの下限・3着の相手ずらし、の3案とも
**分岐割れ 74〜82% を動かせなかった**。

🔴 ユーザー指摘:「**3着の相手が変わる場合と、軸の順序が変わる場合の期待値の順は
   混在している。点で買う以上、買い目の点で期待値を算出する必要がある**」。

そのとおりで、前回の「3着だけずらす」検証は**期待値順の空間を斜めに切っていた**。
◎1着・○2着に固定した時点で、○1着・◎2着や ◎1着・3番手2着 といった
**もっと確率の高い目**を落としている可能性がある。

## 測り方

同じ型A のレースで、**全210点を位置別合成PL の確率で並べ、上位k点を買う**
（＝B/C/E が使っている `prob_top` と同じ考え方。型A だけが固定構成だった）。

    現行        1着=◎・2着=○ 固定 × 3着 3車（3点）
    確率上位k点  210点を確率降順に並べて上位 k 点

🔴 配分は全案とも均等に揃える（配分の違いで結論が動かないようにする）。
🔴 払戻は確定オッズ。どの案も同じ条件なので比較は公平。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/type_a_prob_top.py
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
from src.strategy_wt import rank_7t3_blend_probs   # noqa: E402
from src.type_lab import BUDGET, UNIT              # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_GATE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_GATE)                    # type: ignore[union-attr]

WINDOWS = {"探索 2025": ("2025-01-01", "2025-12-31"),
           "確認 2026": ("2026-01-01", "2026-08-26")}


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
        ent: dict = defaultdict(dict)
        for i in range(0, len(keys), 300):
            ch = keys[i:i + 300]
            ph = ",".join("?" * len(ch))
            for r in c.execute(f"SELECT race_key, combination, odds_value FROM wt_odds "
                               f"WHERE bet_type = 'trifecta' AND race_key IN ({ph})",
                               tuple(ch)):
                d = dict(r)
                fin[d["race_key"]][d["combination"]] = float(d["odds_value"])
            for r in c.execute(f"SELECT race_key, frame_no, finish_order, "
                               f"       pred_win_pct, pred_top3_pct FROM wt_entries "
                               f"WHERE race_key IN ({ph})", tuple(ch)):
                d = dict(r)
                if d["pred_win_pct"] is not None and d["pred_top3_pct"] is not None:
                    ent[d["race_key"]][int(d["frame_no"])] = (
                        float(d["pred_win_pct"]) / 100.0,
                        float(d["pred_top3_pct"]) / 100.0)
                if d["finish_order"] and 1 <= int(d["finish_order"]) <= 3:
                    top[d["race_key"]].append((int(d["finish_order"]),
                                               int(d["frame_no"])))

    KS = [1, 2, 3, 4, 5, 6, 8]
    for win, (lo, hi) in WINDOWS.items():
        sub = []
        for d in rows:
            if not (lo <= str(d["race_date"]) <= hi):
                continue
            e = ent.get(d["race_key"], {})
            f = sorted(top.get(d["race_key"], []))
            order = [int(x) for x in str(d["p3_order"]).replace(",", "-").split("-") if x]
            if len(e) != 7 or len(f) < 3 or len(order) < 5:
                continue
            pw = {c: v[0] for c, v in e.items()}
            p3 = {c: v[1] for c, v in e.items()}
            prob = rank_7t3_blend_probs(sorted(p3), pw, p3)
            ranked = sorted(prob, key=lambda t: -float(prob[t]))
            actual = "-".join(str(c) for _o, c in f[:3])
            cur = [f"{order[0]}-{order[1]}-{order[p - 1]}" for p in (3, 4, 5)]
            sub.append((ranked, cur, actual, fin.get(d["race_key"], {})))
        if not sub:
            continue
        print(f"\n=== {win}（{len(sub):,}レース・配分は全案とも均等）===")
        print(f"  {'買い方':<16}{'点':>3}{'素の的中':>9}{'ROI':>8}"
              f"{'的中中央':>10}{'分岐に必要':>11}{'分岐割れ':>9}")

        def run(lab, pick):
            bet = pay = hit = 0
            pays = []
            k = None
            for ranked, cur, actual, board in sub:
                cs = pick(ranked, cur)
                k = len(cs)
                st = BUDGET // k // UNIT * UNIT
                bet += st * k
                if actual in cs and board.get(actual):
                    p = int(st * board[actual])
                    pay += p
                    hit += 1
                    pays.append(p)
            if not hit:
                return
            pays.sort()
            hr = hit / len(sub)
            need = (BUDGET // k // UNIT * UNIT) * k / hr
            broke = sum(1 for x in pays if x < need) / len(pays)
            print(f"  {lab:<16}{k:>3}{hr:>9.1%}{pay / bet:>8.1%}"
                  f"{pays[len(pays) // 2]:>9,}円{need:>10,.0f}円{broke:>9.0%}")

        run("現行（固定3点）", lambda r, cur: cur)
        for k in KS:
            run(f"確率上位{k}点", lambda r, cur, k=k: ["-".join(str(x) for x in t)
                                                    for t in r[:k]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
