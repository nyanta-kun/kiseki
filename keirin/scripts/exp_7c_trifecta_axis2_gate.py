#!/usr/bin/env python3
"""7C の三連単切替で、**2着を軸2に固定してよいか**を条件別に測る（2026-08-15）。

## なぜ要るのか（ユーザー指摘）

現在の切替条件は **`軸1の単勝率 >= RANK_7C_TRIFECTA_PW_MIN(0.70)` だけ**で、
1着を軸1に固定する根拠しか見ていない。**2着を軸2に固定する根拠が無い**:

  - 軸2の単勝率が軸1と差がない → 軸2が勝ちうる（1着-2着が逆）
  - 軸2の3着内率が3番手と差がない → そもそも軸2が2着とは限らない

どちらも「混戦」であり、その場合は多点買う／見送る等の別扱いが要るのではないか。

## 測るもの

母集団は 7C の三連単切替レース。次の2つの「軸2の抜け度」で層別する:

    gap_pw12 = 単勝率(軸1) − 単勝率(軸2)     … 小さいほど1着が入れ替わりうる
    gap_p3_23 = 3着内率(軸2) − 3着内率(3番手) … 小さいほど2着が入れ替わりうる

各層で現行（軸2固定）の的中率・ROI を出し、代替案と比べる:

    A 現行      1着=軸1 / 2着=軸2 / 3着=相手           （k点）
    B 2着2車    1着=軸1 / 2着={軸2,3番手} / 3着=相手    （最大2k点）
    C 三連複へ  軸2車流し（落差カット込み）              （2〜5点）
    D 見送り                                            （0点）

⚠️ 予算は全案とも1レース1万円（`unit_stake`）。点数が増えれば単価が下がる。
   ここを固定単価にすると「点数を増やすと投資が増えて ROI が良く見える」偽の改善が出る。
⚠️ オッズ（`tri_perm`/`trio_legs`）は最終オッズ。**精算にのみ使う**
   （層別はモデル確率だけで決まるのでオッズ由来の選択バイアスは入らない）。

    PYTHONPATH=. .venv/bin/python scripts/exp_7c_trifecta_axis2_gate.py
"""
from __future__ import annotations

import pickle
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.strategy_wt import (  # noqa: E402
    RANK_7C_LEGS_MIN,
    RANK_7C_P3_SUM_MIN,
    rank_7c_cut_legs_by_gap,
    rank_7c_select_axis,
    rank_7c_select_legs,
    rank_7c_use_trifecta,
    unit_stake,
)

CACHE = REPO / "data" / "exp_7c_cache"
CONFIRM_END = "2025-06-30"


def _load() -> list[dict]:
    out: list[dict] = []
    for p in sorted(CACHE.glob("*.pkl")):
        with p.open("rb") as f:
            out.extend(pickle.load(f))
    return out


def _population(races: list[dict]) -> list[dict]:
    """7C の**三連単へ切り替わる**レースだけを集める。"""
    pop = []
    for r in races:
        p3 = r.get("top3_probs") or {}
        pw = r.get("win_probs") or {}
        if len(p3) != 7 or not pw:
            continue
        sel = rank_7c_select_axis(p3)
        if not sel or len(sel) < 2:
            continue
        a1, a2 = sel[0], sel[1]
        if sum(sorted(p3.values(), reverse=True)[:2]) < RANK_7C_P3_SUM_MIN:
            continue
        others = [f for f in sorted(p3, key=lambda x: -p3[x]) if f not in (a1, a2)]
        legs = rank_7c_select_legs(others, p3)
        if len(legs) < RANK_7C_LEGS_MIN:
            continue
        if not rank_7c_use_trifecta(pw, a1):
            continue                       # 三連複側は対象外
        third = others[0]                  # 3着内率で軸2の次に高い車
        pop.append({**r, "a1": a1, "a2": a2, "legs": legs, "p3": p3, "pw": pw,
                    "third": third,
                    "gap_pw12": pw.get(a1, 0.0) - pw.get(a2, 0.0),
                    "gap_p3_23": p3.get(a2, 0.0) - p3.get(third, 0.0)})
    return pop


def _settle_tf(r: dict, points: list[tuple[int, ...]]) -> tuple[int, int, bool]:
    if not points:
        return 0, 0, False
    unit = unit_stake(len(points))
    bet = unit * len(points)
    res = tuple(r["order3"])
    if res not in set(points):
        return bet, 0, False
    o = (r.get("tri_perm") or {}).get(res)
    return bet, int(round(float(o or 0) * unit)), True


def _settle_trio(r: dict, legs: list[int]) -> tuple[int, int, bool]:
    if not legs:
        return 0, 0, False
    unit = unit_stake(len(legs))
    bet = unit * len(legs)
    top3 = frozenset(r["order3"])
    if top3 != frozenset({r["a1"], r["a2"]} | {legs[0]}) and \
       top3 not in {frozenset({r["a1"], r["a2"], x}) for x in legs}:
        return bet, 0, False
    o = (r.get("trio_legs") or {}).get(next(
        x for x in legs if frozenset({r["a1"], r["a2"], x}) == top3), None)
    return bet, int(round(float(o or 0) * unit)), True


def plans(r: dict) -> dict[str, tuple[int, int, bool]]:
    a1, a2, legs, third = r["a1"], r["a2"], r["legs"], r["third"]
    cur = [(a1, a2, c) for c in legs]

    # 🔴 「2着を2車に広げる」は**軸2が3着へ落ちる目も買う**こと。
    #    2着列に 3番手を足すだけで 3着列に軸2を入れないと、ユーザーが想定した
    #    「軸2が2着とは限らない（3着に落ちる）」ケースを買えていない
    #    ——最初の実装はこれを取りこぼしていた。
    second = [a2, third]
    thirds = sorted(set(legs) | {a2, third})
    two = [(a1, b, c) for b in second for c in thirds if c not in (a1, b)]

    # 🔴 現行は1着を軸1に固定しているので、**軸2が勝った瞬間に全点外れ**る。
    #    上位2車の順序を両建てにする案（点数はちょうど2倍）。
    swap = cur + [(a2, a1, c) for c in legs]

    return {
        "A 現行(軸2固定)": _settle_tf(r, cur),
        "B 2着2車": _settle_tf(r, two),
        "E 上位2車を両建て": _settle_tf(r, swap),
        "C 三連複へ": _settle_trio(r, rank_7c_cut_legs_by_gap(legs, r["p3"])),
        "D 見送り": (0, 0, False),
    }


def _show(title: str, buckets: dict[str, list[dict]]) -> None:
    print(f"\n{title}")
    names = list(plans(next(iter(buckets.values()))[0]))
    print(f"{'層':<26}{'R数':>6}" + "".join(f"{n[:9]:>20}" for n in names))
    print(f"{'':<32}" + "".join(f"{'的中/ROI':>20}" for _ in names))
    for label, rs in buckets.items():
        cells = []
        for n in names:
            bet = pay = hit = 0
            for r in rs:
                b, p, h = plans(r)[n]
                bet += b; pay += p; hit += int(h)
            cells.append(f"{hit/len(rs):>9.1%}/{(pay/bet if bet else 0):>9.1%}")
        print(f"{label:<26}{len(rs):>6}" + "".join(f"{c:>20}" for c in cells))


def main() -> int:
    pop = _population(_load())
    if not pop:
        print("母集団が空です", file=sys.stderr)
        return 1
    print(f"7C の三連単切替レース: {len(pop)} R")
    print(f"  掃引窓 〜{CONFIRM_END} / 確認窓 それ以降")

    # 誰が1着だったか（現行が1着を軸1に固定している根拠の確認）
    print("\n■ 実際に1着だったのは誰か")
    print(f"{'層（単勝率の差 軸1−軸2）':<26}{'R数':>6}{'軸1が1着':>10}{'軸2が1着':>10}{'その他':>10}")
    b1: dict[str, list[dict]] = defaultdict(list)
    for r in pop:
        v = r["gap_pw12"]
        k = ("① < 0.30" if v < 0.30 else "② 0.30〜0.45" if v < 0.45
             else "③ 0.45〜0.60" if v < 0.60 else "④ 0.60 以上")
        b1[k].append(r)
    for k, rs in sorted(b1.items()):
        w1 = sum(1 for r in rs if r["order3"][0] == r["a1"])
        w2 = sum(1 for r in rs if r["order3"][0] == r["a2"])
        print(f"{k:<26}{len(rs):>6}{w1/len(rs):>10.1%}{w2/len(rs):>10.1%}"
              f"{(len(rs)-w1-w2)/len(rs):>10.1%}")
    a1w = sum(1 for r in pop if r["order3"][0] == r["a1"])
    a2w = sum(1 for r in pop if r["order3"][0] == r["a2"])
    print(f"{'全体':<26}{len(pop):>6}{a1w/len(pop):>10.1%}{a2w/len(pop):>10.1%}"
          f"{(len(pop)-a1w-a2w)/len(pop):>10.1%}")

    # ── 混戦だけを三連複へ回すハイブリッド（ユーザー提案）──────────────────
    # 「混戦」の定義を変えながら、**全体**の成績で比べる。層別で良く見えても
    # 全体で効かなければ意味がない（対象が狭ければ全体は動かない）。
    import random
    defs = [
        ("現行（全部 三連単）", lambda r: False),
        ("単勝率の差 < 0.30", lambda r: r["gap_pw12"] < 0.30),
        ("単勝率の差 < 0.45", lambda r: r["gap_pw12"] < 0.45),
        ("3着内率の差 < 0.10", lambda r: r["gap_p3_23"] < 0.10),
        ("3着内率の差 < 0.20", lambda r: r["gap_p3_23"] < 0.20),
        ("どちらか(OR)", lambda r: r["gap_pw12"] < 0.45 or r["gap_p3_23"] < 0.10),
        ("両方(AND)", lambda r: r["gap_pw12"] < 0.45 and r["gap_p3_23"] < 0.10),
    ]
    print("\n■ 混戦だけ三連複へ回す（それ以外は現行の三連単）")
    print(f"{'混戦の定義':<22}{'該当':>6}{'的中率':>8}{'ROI':>8}{'ROI(掃引)':>11}"
          f"{'ROI(確認)':>11}{'vs現行':>9}")
    cache = {id(r): plans(r) for r in pop}

    def run(pred):
        tot = {"n": 0, "bet": 0, "pay": 0, "hit": 0, "sw": 0}
        win = {"s": [0, 0], "c": [0, 0]}
        for r in pop:
            pl = cache[id(r)]
            b, pa, h = pl["C 三連複へ"] if pred(r) else pl["A 現行(軸2固定)"]
            tot["n"] += 1; tot["bet"] += b; tot["pay"] += pa; tot["hit"] += int(h)
            tot["sw"] += int(pred(r))
            k = "s" if str(r.get("race_date", "")) <= CONFIRM_END else "c"
            win[k][0] += b; win[k][1] += pa
        return tot, win

    base_roi = run(defs[0][1])[0]["pay"] / run(defs[0][1])[0]["bet"]
    for label, pred in defs:
        tot, win = run(pred)
        roi = tot["pay"] / tot["bet"]
        sr = win["s"][1] / win["s"][0] if win["s"][0] else 0
        cr = win["c"][1] / win["c"][0] if win["c"][0] else 0
        d = "—" if label.startswith("現行") else f"{(roi - base_roi) * 100:+.2f}pt"
        print(f"{label:<22}{tot['sw']:>6}{tot['hit']/tot['n']:>8.1%}{roi:>8.1%}"
              f"{sr:>11.1%}{cr:>11.1%}{d:>9}")

    # 最有力候補について、レース単位 paired bootstrap で ROI 差の CI を出す
    best = defs[4][1]      # 3着内率の差 < 0.20
    random.seed(0); n = len(pop); ds = []
    for _ in range(2000):
        s = [pop[random.randrange(n)] for _ in range(n)]
        def roi_of(pred, sample):
            b = pa = 0
            for r in sample:
                x = cache[id(r)]["C 三連複へ"] if pred(r) else cache[id(r)]["A 現行(軸2固定)"]
                b += x[0]; pa += x[1]
            return pa / b if b else 0
        ds.append(roi_of(best, s) - roi_of(defs[0][1], s))
    ds.sort()
    print(f"\n『3着内率の差 < 0.20 を三連複へ』の ROI 差（paired bootstrap 2,000回）:")
    print(f"  95%CI [{ds[50]*100:+.2f}, {ds[1949]*100:+.2f}] pt  P(改善)={sum(1 for d in ds if d>0)/len(ds):.1%}")

    for key, label, edges in (
        ("gap_pw12", "単勝率の差（軸1−軸2）", (0.30, 0.45, 0.60)),
        ("gap_p3_23", "3着内率の差（軸2−3番手）", (0.0, 0.10, 0.20)),
    ):
        b: dict[str, list[dict]] = defaultdict(list)
        for r in pop:
            v = r[key]
            if v < edges[0]:
                k = f"① {label} < {edges[0]}"
            elif v < edges[1]:
                k = f"② {edges[0]}〜{edges[1]}"
            elif v < edges[2]:
                k = f"③ {edges[1]}〜{edges[2]}"
            else:
                k = f"④ {edges[2]} 以上"
            b[k].append(r)
        _show(f"■ {label} で層別", dict(sorted(b.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
