#!/usr/bin/env python3
"""9車の型Fの打ち手を測る（2026-08-28）。

分解の結果（`typef_anatomy.py`）:
    9車 的中 16.08% = ① 軸2車そろい 31.35% × ② 相手カバー 51.29%
    7車 的中 24.80% = ①            39.36% × ②            63.00%
＝ **①と②がほぼ半分ずつ効いている**（片方だけ7車並みにしても 19.8〜20.2% 止まり）。

②は「3着目が買った相手2車の中か」。9車は指数3位の割合は7車と同じ（32.3 vs 30.8%）
なのに **4位が 25.1→19.0% へ落ちて5位以下へ散る**＝選択肢が2車多いぶん裾が広い。

→ 打ち手の候補は「相手を増やす」。ただし点数が増えると1点あたりが下がるので
   **的中率と払戻の交換にしかならない可能性が高い。測って確かめる。**
"""
from __future__ import annotations

import json
import statistics as stx
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from src.database import get_connection  # noqa: E402

BUDGET = 10_000
WALL = 74.85


def load(mode: str, d1: str, d2: str, label: str):
    q = ("SELECT race_key, race_date, axis1, axis2, legs, arare, axis_sum "
         "FROM type_lab_picks WHERE mode = ? AND settled_at IS NOT NULL "
         "  AND plan_key = 'F_hit' AND type_label = ? "
         "  AND race_date BETWEEN ? AND ?")
    cols = ("race_key", "race_date", "axis1", "axis2", "legs", "arare", "axis_sum")
    with get_connection() as c:
        return [dict(zip(cols, tuple(r)))
                for r in c.execute(q, (mode, label, d1, d2)).fetchall()]


def order_of(keys: list[str]) -> dict[str, list[int]]:
    """3着内率の降順（型ラボと同じ並び）。"""
    out = {}
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            q = ("SELECT race_key, frame_no, pred_top3_pct FROM wt_entries "
                 f"WHERE race_key IN ({','.join('?' * len(ch))})")
            got = defaultdict(dict)
            for rk, fn, p in (tuple(r) for r in c.execute(q, ch).fetchall()):
                if p is not None:
                    got[str(rk)][int(fn)] = float(p)
            for rk, d in got.items():
                out[rk] = sorted(d, key=lambda c_: (-d[c_], c_))
    return out


def finishes(keys: list[str]) -> dict[str, tuple[int, int, int]]:
    out = {}
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            q = ("SELECT race_key, frame_no, finish_order FROM wt_entries "
                 f"WHERE race_key IN ({','.join('?' * len(ch))}) "
                 "AND finish_order BETWEEN 1 AND 3")
            got = defaultdict(dict)
            for rk, fn, fo in (tuple(r) for r in c.execute(q, ch).fetchall()):
                got[str(rk)][int(fo)] = int(fn)
            for rk, d in got.items():
                if set(d) == {1, 2, 3}:
                    out[rk] = (d[1], d[2], d[3])
    return out


def odds_of(keys: list[str], combos: dict[str, str]) -> dict[str, float]:
    """{race_key: 的中した目の確定オッズ}。"""
    out = {}
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = [k for k in keys[i:i + 900] if k in combos]
            if not ch:
                continue
            q = ("SELECT race_key, combination, odds_value FROM wt_odds "
                 f"WHERE race_key IN ({','.join('?' * len(ch))}) "
                 "AND bet_type = 'trifecta'")
            board = {}
            for rk, comb, od in (tuple(r) for r in c.execute(q, ch).fetchall()):
                board[(str(rk), str(comb))] = float(od or 0)
            for rk in ch:
                v = board.get((rk, combos[rk]))
                if v:
                    out[rk] = v
    return out


def run(rows: list[dict], n_partners: int, title: str) -> None:
    keys = [r["race_key"] for r in rows]
    order = order_of(keys)
    fin = finishes(keys)
    combos, kept = {}, []
    for r in rows:
        rk = r["race_key"]
        if rk not in order or rk not in fin:
            continue
        a1, a2 = int(r["axis1"]), int(r["axis2"])
        rest = [c for c in order[rk] if c not in (a1, a2)][:n_partners]
        n_legs = 6 * len(rest)
        if n_legs == 0:
            continue
        stake = BUDGET // n_legs // 100 * 100
        if stake <= 0:
            continue
        f = fin[rk]
        won = set(f) == {a1, a2, x} if False else None
        hit = (a1 in f and a2 in f and (set(f) - {a1, a2}) <= set(rest))
        kept.append((r, n_legs, stake, hit))
        if hit:
            combos[rk] = "-".join(str(x) for x in f)
    od = odds_of([k for k, _ in ((r["race_key"], 0) for r, *_ in kept)], combos)
    inv = ret = nhit = nshown = nbig = 0
    pays = []
    days = set()
    for r, n_legs, stake, hit in kept:
        days.add(r["race_date"])
        inv += stake * n_legs
        if hit and r["race_key"] in od:
            pay = int(od[r["race_key"]] * stake)
            ret += pay
            nhit += 1
            pays.append(pay)
            if pay > stake * n_legs:
                nshown += 1
            if pay >= 100_000:
                nbig += 1
    n = len(kept)
    nd = max(len(days), 1)
    print(f"{title:22}{n:6d}{n / nd:7.1f}{n_legs:5d}{BUDGET // n_legs // 100 * 100:8,}"
          f"{nhit / n * 100:8.2f}%{nshown / n * 100:9.2f}%"
          f"{(stx.median(pays) if pays else 0):10,.0f}{nbig / nd:9.3f}"
          f"{(ret / inv * 100 if inv else 0):7.1f}%")


def main() -> None:
    W = ("2026-01-01", "2026-08-26")
    EX = ("2025-01-01", "2025-12-31")
    for lab, w in (("確認窓 2026", W), ("探索窓 2025", EX)):
        rows = load("paper9", *w, "F")
        print(f"\n== 9車 型F・相手の数を振る（{lab}）  n={len(rows)}")
        print(f"{'相手':22}{'n':>6}{'件/日':>7}{'点数':>5}{'1点':>8}"
              f"{'的中':>9}{'表示的中':>10}{'払戻中央':>10}{'10万+/日':>9}{'ROI':>8}")
        for k in (2, 3, 4, 5):
            run(rows, k, f"  相手{k}車")

        print(f"\n-- 荒れ度別（相手2車＝現行・{lab}）")
        g = defaultdict(list)
        for r in rows:
            g[min(int(r["arare"] or 0), 4)].append(r)
        for s in sorted(g):
            if len(g[s]) < 40:
                continue
            run(g[s], 2, f"  荒れ度 s={s}")


if __name__ == "__main__":
    main()
