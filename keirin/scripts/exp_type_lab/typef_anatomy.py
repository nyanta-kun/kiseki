#!/usr/bin/env python3
"""9車の型Fがなぜ当たらないのかを**段階に分解**する（2026-08-28）。

型F のプランは軸2車を前提にしている:
    F_hit  軸1・軸2・相手1車 の6順列すべて（12点＝相手2車ぶん）
    F_pay  1着=軸1固定 × 2着2車 → 3着流し

したがって的中は次の積に分解できる（F_hit の場合）:
    的中率 = P(軸2車がともに3着以内)          … 層①「軸の堅さ」
           × P(残り1車が相手2車の中 | 軸2車そろい)  … 「相手のカバー」
    （順序は6順列すべて買うので落ちない）

🔴 **どちらが効いているかで打ち手が変わる。**
   軸が悪いなら軸選定、相手なら点数か相手の選び方。
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from src.database import get_connection  # noqa: E402


def load(mode: str, d1: str, d2: str, label: str | None = None) -> list[dict]:
    q = ("SELECT race_key, race_date, type_label, axis1, axis2, n_entries, "
         "       plan_key, legs, hit, payout, budget, win_combo, axis_sum "
         "FROM type_lab_picks WHERE mode = ? AND settled_at IS NOT NULL "
         "  AND race_date BETWEEN ? AND ? AND plan_key = 'F_hit'")
    cols = ("race_key", "race_date", "type_label", "axis1", "axis2", "n_entries",
            "plan_key", "legs", "hit", "payout", "budget", "win_combo", "axis_sum")
    with get_connection() as c:
        rows = [dict(zip(cols, tuple(r))) for r in c.execute(q, (mode, d1, d2)).fetchall()]
    if label:
        rows = [r for r in rows if r["type_label"] == label]
    return rows


def finishes(keys: list[str]) -> dict[str, list[int]]:
    """{race_key: [1着, 2着, 3着]}。3着まで確定したレースだけ。"""
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
                    out[rk] = [d[1], d[2], d[3]]
    return out


def p3_rank(keys: list[str]) -> dict[str, dict[int, int]]:
    """{race_key: {車番: 3着内率の順位(1始まり)}}。型ラボが使ったのと同じ並び。"""
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
                order = sorted(d, key=lambda c_: (-d[c_], c_))
                out[rk] = {c_: i + 1 for i, c_ in enumerate(order)}
    return out


def anatomy(rows: list[dict], title: str) -> None:
    keys = [r["race_key"] for r in rows]
    fin = finishes(keys)
    rows = [r for r in rows if r["race_key"] in fin]
    if not rows:
        print(f"\n== {title}: データなし")
        return
    n = len(rows)
    both = partner = 0
    a1_in = a2_in = 0
    third_rank = Counter()
    for r in rows:
        top3 = set(fin[r["race_key"]])
        a1, a2 = int(r["axis1"]), int(r["axis2"])
        a1_in += a1 in top3
        a2_in += a2 in top3
        if a1 in top3 and a2 in top3:
            both += 1
            rest = (top3 - {a1, a2}).pop()
            # 買った相手（legs から軸以外の車を集める）
            import json
            legs = r["legs"]
            legs = json.loads(legs) if isinstance(legs, str) else (legs or [])
            cars = set()
            for lg in legs:
                for x in str(lg.get("combo", "")).replace("=", "-").split("-"):
                    if x.isdigit():
                        cars.add(int(x))
            partner += rest in (cars - {a1, a2})
    print(f"\n== {title}   n={n}")
    print(f"  軸1が3着以内          {a1_in / n * 100:6.2f}%")
    print(f"  軸2が3着以内          {a2_in / n * 100:6.2f}%")
    print(f"  ① 軸2車ともに3着以内   {both / n * 100:6.2f}%")
    if both:
        print(f"  ② 残り1車が買った相手  {partner / both * 100:6.2f}%（①が起きた中で）")
        print(f"  ①×② = 的中率          {partner / n * 100:6.2f}%")
    hits = sum(1 for r in rows if r["hit"])
    print(f"  実測の的中率           {hits / n * 100:6.2f}%  ← 分解の答え合わせ")

    # 3着に来た車が指数で何番手だったか（軸2車ともに来たとき）
    pr = p3_rank([r["race_key"] for r in rows])
    cnt = Counter()
    for r in rows:
        top3 = set(fin[r["race_key"]])
        a1, a2 = int(r["axis1"]), int(r["axis2"])
        if a1 in top3 and a2 in top3 and r["race_key"] in pr:
            rest = (top3 - {a1, a2}).pop()
            cnt[pr[r["race_key"]].get(rest, 99)] += 1
    tot = sum(cnt.values()) or 1
    print("  ③ 軸2車そろったときの「3着目」の指数順位:")
    print("     " + "  ".join(f"{k}位 {cnt[k] / tot * 100:.1f}%"
                              for k in sorted(cnt) if k < 99))


def main() -> None:
    W = ("2026-01-01", "2026-08-26")
    for mode, label, name in (("paper9", "F", "9車 型F"),
                              ("paper", "F", "7車 型F"),
                              ("paper9", "C", "9車 型C（対照・良い型）")):
        anatomy(load(mode, *W, label), f"{name}（確認窓）")


if __name__ == "__main__":
    main()
