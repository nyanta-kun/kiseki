#!/usr/bin/env python3
"""7M1 の相手を1〜2点増やすと「ガミなしの的中」が増えるか（2026-08-21・ユーザー要望）。

## ユーザーの見立て

> 7M1 は中穴狙いとして二軸が期待の走りをしている。しかし相手がハマらず
> 外れになることが多い。大勝ちは難しくなるが、あと1点2点相手を増やせば
> **ガミなく的中を計上できそう**。

## 現行の相手選択

軸2車を除く5車を3着内率の降順に並べ、**下位3車（＝全体の指数5〜7番手）だけ**を買う。
上位2枚（全体3・4番手）を捨てるのが払戻帯を作る操作
（`rank_7m1_select_legs` / `RANK_7M1_LEG_START = 2`）。
「相手を増やす」＝この捨てている上位側を戻す操作になる。

## 🔴 予算は固定なので、増やすと1点あたりが薄まる

`unit_stake` は均等割り（`budget // n // unit * unit`）で予算枠は 10,000円のまま。
したがって

    払戻比 = trio払戻 ÷ (100 × 点数)

**単位金額が約分で消える**。つまり **ガミ（払戻比<1）の境界は「三連複オッズ < 点数」**
という単純な形になり、既存の的中の払戻比は 3点→4点で 0.75倍・3点→5点で 0.60倍に薄まる。
増やして得なのは「**追加した相手で拾える的中のオッズが点数を上回るとき**」だけ。

## 測り方

- 母集団は `picks_history` の RANK_7M1・`rule_version='5fc27f1982b0'`
  （2025-01-01〜2026-08-20 の **7,747件が単一世代**。世代混在なし）
- **同じレース・同じ軸**の上で相手集合だけを差し替える＝件数は動かない。
  [[keirin_race_selection_meta_2026_08_18]]「改善の正体は少なく賭けただけ」は起こらない
- `trio_payout` は**外れレースも含め100%埋まっている**ので反実仮想の払戻が出せる
- 掃引窓 2025 / 確認窓 2026-01〜08-20

## 見る指標（ユーザーの目的関数）

ROI ではなく **「ガミなしの的中が1日あたり何回あるか」**。
率ではなく**絶対数**で見る（[[keirin_7s_gate_resweep_2026_08_21]] の教訓——
率が上がっても回数が減れば体験は悪化する）。

DB は読み取りのみ。
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection  # noqa: E402

RULE = "5fc27f1982b0"
SWEEP = ("2025-01-01", "2025-12-31")
CONFIRM = ("2026-01-01", "2026-08-20")
COMBO_RE = re.compile(r"^\s*(\d+)\s*=\s*(\d+)\s*-\s*([\d,]+)")


def load() -> list[dict]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT split_part(race_key,'#',1) rk, race_date, pred_combo, "
            "       n_combos, trio_payout "
            "FROM picks_history WHERE rank='RANK_7M1' AND bet_amount>0 "
            "  AND rule_version=? AND trio_payout>0", (RULE,))
        picks = {}
        for r in cur:
            m = COMBO_RE.match(r["pred_combo"] or "")
            if not m:
                continue
            picks[r["rk"]] = dict(
                date=r["race_date"], a1=int(m.group(1)), a2=int(m.group(2)),
                legs=[int(x) for x in m.group(3).split(",")],
                trio=int(r["trio_payout"]))
        keys = list(picks)
        cur.execute(
            "SELECT race_key, frame_no, pred_top3_pct, finish_order "
            "FROM wt_entries WHERE race_key = ANY(?)", (keys,))
        ent: dict[str, list[dict]] = defaultdict(list)
        for e in cur:
            ent[e["race_key"]].append(dict(e))

    rows = []
    for rk, v in picks.items():
        es = ent.get(rk) or []
        top3 = {int(e["frame_no"]) for e in es
                if e["finish_order"] in (1, 2, 3)}
        if len(top3) != 3:
            continue                      # 着順が揃わないレースは母集団外
        p3 = {int(e["frame_no"]): float(e["pred_top3_pct"] or 0.0) for e in es}
        others = [f for f in p3 if f not in (v["a1"], v["a2"])]
        if len(others) < 5:
            continue                      # 欠車で5車に満たない盤面は外す
        ranked = sorted(others, key=lambda f: (-p3[f], f))
        rows.append({**v, "rk": rk, "top3": top3, "ranked": ranked})
    return rows


def ratio(trio: int, n: int) -> float:
    """払戻比。単位金額は約分で消えるので trio払戻 ÷ (100 × 点数)。"""
    return trio / (100.0 * n)


def evaluate(rows: list[dict], add: int) -> dict:
    """相手を `add` 点だけ**上位側から**足したときの成績。"""
    n_hit = n_gami = n_ok = n_2x = 0
    ratios: list[float] = []
    total_ret = 0.0
    for r in rows:
        legs = list(r["legs"])
        for f in r["ranked"]:             # 指数の高い順に、未採用のものを足す
            if len(legs) >= len(r["legs"]) + add:
                break
            if f not in legs:
                legs.append(f)
        n = len(legs)
        third = r["top3"] - {r["a1"], r["a2"]}
        hit = len(third) == 1 and r["a1"] in r["top3"] and r["a2"] in r["top3"] \
            and next(iter(third)) in legs
        if hit:
            x = ratio(r["trio"], n)
            n_hit += 1
            ratios.append(x)
            total_ret += x
            if x < 1.0:
                n_gami += 1
            else:
                n_ok += 1
            if x >= 2.0:
                n_2x += 1
    ratios.sort()
    return dict(n=len(rows), hit=n_hit, gami=n_gami, ok=n_ok, x2=n_2x,
                med=ratios[len(ratios) // 2] if ratios else 0.0,
                roi=100.0 * total_ret / len(rows) if rows else 0.0)


def main() -> None:
    rows = load()
    print(f"7M1 {len(rows)}R（rule_version={RULE}・着順と5車が揃ったもの）\n")

    # 再現度: 記録された相手が「下位3車」と一致するか
    same = sum(1 for r in rows
               if set(r["legs"]) <= set(r["ranked"][2:]))
    print(f"再現度: 記録の相手が ranked[2:] に収まる割合 {100*same/len(rows):.1f}%\n")

    for tag, (d1, d2) in (("掃引窓 2025", SWEEP), ("確認窓 2026", CONFIRM)):
        sub = [r for r in rows if d1 <= r["date"] <= d2]
        days = len({r["date"] for r in sub})
        print(f"=== {tag}  {len(sub)}R / {days}日 ===")
        print(f"{'相手':<10}{'点数':>5}{'的中':>7}{'的中/日':>8}"
              f"{'ガミ':>6}{'ガミなし的中/日':>16}{'2倍+/日':>9}{'払戻中央':>9}{'ROI':>8}")
        for add, label in ((0, "現行"), (1, "+1点"), (2, "+2点")):
            e = evaluate(sub, add)
            avg_n = sum(len(r["legs"]) + add for r in sub) / max(len(sub), 1)
            print(f"{label:<10}{avg_n:>5.1f}{e['hit']:>7}"
                  f"{e['hit']/days:>8.2f}{e['gami']:>6}"
                  f"{e['ok']/days:>16.2f}{e['x2']/days:>9.2f}"
                  f"{e['med']:>9.2f}{e['roi']:>7.1f}%")
        print()
        report_added(sub, tag)


def added_only(rows, add):
    """**追加した相手でだけ拾えた的中**の払戻比の分布。増やす価値はここに全部ある。"""
    out=[]
    for r in rows:
        legs=list(r["legs"]); base=set(legs)
        for f in r["ranked"]:
            if len(legs) >= len(r["legs"])+add: break
            if f not in legs: legs.append(f)
        third = r["top3"] - {r["a1"], r["a2"]}
        if len(third)!=1 or r["a1"] not in r["top3"] or r["a2"] not in r["top3"]:
            continue
        t=next(iter(third))
        if t in legs and t not in base:          # 追加分でだけ当たった
            out.append(ratio(r["trio"], len(legs)))
    return sorted(out)


def report_added(rows, tag):
    days=len({r["date"] for r in rows})
    print(f"[{tag}] 追加した相手でだけ拾えた的中")
    print(f"{'':<8}{'件数':>6}{'件/日':>8}{'中央':>7}{'<1倍(ガミ)':>11}{'1-2倍':>8}{'>=2倍':>8}")
    for add,label in ((1,"+1点"),(2,"+2点")):
        v=added_only(rows,add); n=len(v)
        if not n: continue
        print(f"{label:<8}{n:>6}{n/days:>8.2f}{v[n//2]:>7.2f}"
              f"{100*sum(1 for x in v if x<1)/n:>10.0f}%"
              f"{100*sum(1 for x in v if 1<=x<2)/n:>7.0f}%"
              f"{100*sum(1 for x in v if x>=2)/n:>7.0f}%")
    print()


if __name__ == "__main__":
    main()
