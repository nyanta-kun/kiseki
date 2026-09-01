#!/usr/bin/env python3
"""型A を ①軸信頼の分位 ②着順パターン に分けて見る（2026-08-31・ユーザー指示）。

## なぜ

型A は 素の的中 35.9% に対し **的中の75%が損益分岐（2.79倍）割れ**。
配分・合成下限・相手ずらし・全210点の確率順、の4軸をすべて測っても
**分岐割れ 73〜82% が動かなかった**。

残る2つを測る:

① **軸信頼（axis_sum）の分位** — 型A の中でさらに堅い層だけなら分岐を超えるか
② **着順パターン**（ユーザー指摘）— 「◎1着・○2着」と「○1着・◎2着」「◎1着・○3着」
   …は**別の商品として期待値が違う**はず。現行は最初の1つだけを買っている。
   細分化して、**割に合うパターンが存在するか**を見る。

🔴 パターンは「指数1位(◎)と指数2位(○)が、1・2・3着のどこに入るか」で定義する。
   各パターンは複数の目を含む（3着＝残り5車 など）ので、**その集合を均等で買う**
   と考えて 的中率・ROI・分岐割れ を出す。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/type_a_patterns.py
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

#: 着順パターン。`(◎の着順, ○の着順)`。0 は3着圏外。
PATTERNS = {
    (1, 2): "◎1着・○2着（現行）",
    (2, 1): "○1着・◎2着",
    (1, 3): "◎1着・○3着",
    (3, 1): "○1着・◎3着",
    (2, 3): "◎2着・○3着",
    (3, 2): "○2着・◎3着",
    (1, 0): "◎1着・○圏外",
    (2, 0): "◎2着・○圏外",
    (3, 0): "◎3着・○圏外",
    (0, 1): "○1着・◎圏外",
    (0, 2): "○2着・◎圏外",
    (0, 3): "○3着・◎圏外",
    (0, 0): "◎○とも圏外",
}


def load():
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
    out = []
    for d in rows:
        order = [int(x) for x in str(d["p3_order"]).replace(",", "-").split("-") if x]
        f = sorted(top.get(d["race_key"], []))
        if len(order) < 7 or len(f) < 3:
            continue
        res = [c for _o, c in f[:3]]
        pos = {c: i + 1 for i, c in enumerate(res)}
        out.append({"date": str(d["race_date"]), "axis_sum": float(d["axis_sum"] or 0),
                    "order": order, "res": res,
                    "pat": (pos.get(order[0], 0), pos.get(order[1], 0)),
                    "board": fin.get(d["race_key"], {})})
    return out


def stat(rs, combos_of):
    bet = pay = hit = 0
    pays = []
    ks = []
    for d in rs:
        cs = combos_of(d)
        if not cs:
            continue
        k = len(cs)
        st = BUDGET // k // UNIT * UNIT
        ks.append(k)
        bet += st * k
        act = "-".join(str(c) for c in d["res"])
        if act in cs and d["board"].get(act):
            p = int(st * d["board"][act])
            pay += p
            hit += 1
            pays.append(p)
    if not bet or not hit:
        return None
    pays.sort()
    n = len(ks)
    hr = hit / n
    need = (bet / n) / hr
    return (n, sum(ks) / n, hr, pay / bet, pays[len(pays) // 2], need,
            sum(1 for x in pays if x < need) / len(pays))


def main() -> int:
    data = load()
    for win, (lo, hi) in WINDOWS.items():
        rs = [d for d in data if lo <= d["date"] <= hi]
        if not rs:
            continue
        print(f"\n=== ① 軸信頼（axis_sum）の分位 — {win}（{len(rs):,}R）===")
        rs2 = sorted(rs, key=lambda d: -d["axis_sum"])
        n = len(rs2)
        cur = (lambda d: [f"{d['order'][0]}-{d['order'][1]}-{d['order'][p - 1]}"
                          for p in (3, 4, 5)])
        print(f"  {'層':<12}{'R数':>6}{'素の的中':>9}{'ROI':>8}{'的中中央':>10}"
              f"{'分岐に必要':>11}{'分岐割れ':>9}")
        for lab, sl in (("上位1/4", rs2[:n // 4]), ("上位1/2", rs2[:n // 2]),
                        ("下位1/2", rs2[n // 2:]), ("全体", rs2)):
            s = stat(sl, cur)
            if s:
                print(f"  {lab:<12}{s[0]:>6,}{s[2]:>9.1%}{s[3]:>8.1%}"
                      f"{s[4]:>9,}円{s[5]:>10,.0f}円{s[6]:>9.0%}")

        print(f"\n=== ② 着順パターン別 — {win} ===")
        print(f"  {'パターン':<20}{'出現率':>8}{'点':>4}{'ROI':>8}"
              f"{'的中中央':>10}{'分岐に必要':>11}{'分岐割れ':>9}")
        cnt = defaultdict(int)
        for d in rs:
            cnt[d["pat"]] += 1
        for pat, lab in PATTERNS.items():
            if cnt[pat] / len(rs) < 0.02:
                continue
            a, b = pat
            if a == 0 or b == 0:
                continue      # 集合が大きすぎて商品にならない

            def combos(d, a=a, b=b):
                o = d["order"]
                slot = {a: o[0], b: o[1]}
                third = [c for c in o if c not in (o[0], o[1])]
                free = ({1, 2, 3} - {a, b}).pop()
                out = []
                for c in third:
                    s = dict(slot)
                    s[free] = c
                    out.append("-".join(str(s[i]) for i in (1, 2, 3)))
                return out
            s = stat(rs, combos)
            if s:
                print(f"  {lab:<20}{cnt[pat] / len(rs):>8.1%}{s[1]:>4.0f}{s[3]:>8.1%}"
                      f"{s[4]:>9,}円{s[5]:>10,.0f}円{s[6]:>9.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
