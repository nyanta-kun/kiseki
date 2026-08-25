#!/usr/bin/env python3
"""7M1 に「期待値が取れないレースは見送る」条件を足せるか（2026-08-25・ユーザー要望）。

## きっかけ

> 2026-08-25 和歌山10R は期待値 0.16 と低いのに 7M1 で推奨が出て、外れた。
> 一定の期待値が取れない場合は見送る条件を検討してほしい。

## 🔴 本番コードから確認した事実（測る前に読むこと・CLAUDE.md「検証の作法」）

和歌山10R（`20260825_55_10`・軸 5=3）の相手5車の EV（`trio_ev_for_legs`
＝ 予測オッズ × PL同時確率）は次のとおりで、**実際に買った1点が最悪だった**:

    相手  予測オッズ  同時確率p   EV
      1      2.55     0.0641   **0.163**   ← 買ったのはこれ（○1点集中）
      2     12.93     0.0579     0.748
      4     54.59     0.0156     0.850
      6     15.46     0.0572     0.884
      7     19.23     0.0022     0.042

理由は `rank_7m1_select_legs` の**順序**。「○を含む点の予測オッズが 2.0〜3.1倍なら
○1点」（`rank_7m1_maru_concentrates`）が **EV 順より先に**評価されるため、
EV 最下位の点でも単独で買う。既存の3ゲート
（`MIN_POINT_ODDS` 2.0倍 / `expected_payout_floor` 1.5倍 /
`MIN_MEAN_PAYOUT` 2万円）はいずれも**通ってしまう**（想定払戻 25,000円）。

⚠️ Web の確認画面が出す期待値（0.24）は `keirin_router._trio_probabilities` の
   独立近似で、**本番の PL 同時確率（0.163）とは別の量**。片方だけを見て
   閾値を決めないこと。

## 何を測るか

1. **EV は実際の成績と単調に対応するか**（対応しないなら閾値を置く根拠が無い）
2. 置くとしてどこに置くか。3つの形を比べる:

     A 現行
     B ○1点集中に EV 下限を掛け、割れたら EV 上位4点へ戻す（レースは残す）
     C レース期待値（Σ p·o·stake ÷ 予算）が下限未満なら**見送る**

## プロトコル

- 母集団・軸は `picks_history` の 7M1 行に固定し、**買い目だけ**を差し替える
  （`exp_7m1_partner_count.py` が作るキャッシュを共有）
- 予測オッズは vintage: 2026 窓は train_end 2025-12-31、2025 窓は 2024-12-31
- 窓: 掃引 2026-01〜04 / 確認 2026-05〜08 / 独立 2025（年をまたぐ確認窓）
- 採点は**本番と同じダッチング配分**（`tilted_stakes` に予測オッズを渡す）＋
  確定配当（`picks_history.trio_payout`）
- 入稿ゲート（`MIN_POINT_ODDS` / `MIN_MEAN_PAYOUT`）も本番どおり掛ける。
  全アーム共通なので Δ には効かないが、絶対水準を本番へ寄せる
- 🔴 **日次上限は無い**（7M1 は最下位優先で上限を持たない）

DB は読まない（キャッシュのみ）。

## 🔴🔴 結論（2026-08-25）: **期待値の下限は置けない。データは逆を指している**

### ① 期待値は成績と単調に対応しない（現行の買い方のまま・五分位）

    窓        EV最下位  →                                    → EV最上位   （ROI%）
    掃引26前    76.4   90.0   83.8   87.0   91.0
    確認26後    83.7   76.9  103.9   92.1   76.8   ← 最下位が最上位より上
    独立2025    84.4   73.9   75.1   70.5   76.9   ← **最下位が最良**

  的中率は EV と**逆相関**（3窓とも単調減少 28.9→25.8 / 30.3→23.9 / 28.9→21.9%）。
  期待値が低い＝オッズが安い＝当たりやすい、という当たり前の関係が支配していて、
  「割安さ」の情報は残っていない。

### ② 和歌山10R が入る帯（EV 0.0〜0.3）は**最も成績が良い**

    窓        n     的中%    ROI%      （○1点集中の EV 細帯）
    掃引26前   39    38.5    91.5   ← 最良
    確認26後   44    34.1    86.8   ← 2位
    独立2025  170    34.1    90.7   ← 最良

  最悪の帯は 0.5〜0.7（65.6 / 51.6 / 62.3%）で、**低い側でも高い側でもない**。

### ③ 腕B（○1点集中に EV 下限・割れたら EV 上位4点へ）— ❌ 不採用

  下限 0.3 / 0.5 / 0.7 / 0.9 のいずれでも **的中件/日 が3窓とも減る**
  （独立2025 の CI95 は全閾値で 0 を跨がない: [-0.129,-0.030] / [-0.156,-0.041] /
  [-0.151,-0.014] / [-0.197,-0.038]）。ROI は ±1.5pt で符号も揺れる。

### ④ 腕C（レース期待値が下限未満なら見送る）＝ ユーザー提案そのもの — ❌ 不採用

    下限   見送り(独立2025)   Δ的中件/日 CI95（掃引 / 確認 / 独立）
    0.3     213/4,614   [-0.250,-0.108] [-0.259,-0.112] [-0.236,-0.151]  🔴🔴🔴
    0.5     428/4,614   [-0.508,-0.283] [-0.483,-0.267] [-0.444,-0.321]  🔴🔴🔴
    0.7     872/4,614   [-0.783,-0.508] [-0.862,-0.569] [-0.770,-0.605]  🔴🔴🔴

  2倍以上の的中件/日も同じだけ落ちる。ROI は動かない（0.3 で −0.4/+0.1/−0.7pt）。
  **件数だけを失う。**

### ⑤ 逆に、市場価格（○の予測オッズ）には勾配がある——ただし**安いほど良い**

    ○の予測オッズ    掃引26前        確認26後        独立2025      （的中% / ROI%）
    2.0〜2.3      53.3 / 127.7   45.2 / 101.3   37.7 /  89.8   ← 最良
    2.3〜2.6      37.1 /  93.4   35.7 /  94.1   29.3 /  74.8   ← 和歌山10R(2.55倍)
    2.6〜2.9      30.2 /  82.3   22.2 /  62.2   29.4 /  87.2
    2.9〜3.1      17.1 /  52.0   30.2 /  86.7   22.0 /  72.0

  EV = オッズ × 確率 なので、**安い＝EV が低い＝良い**。下限を置く発想と真逆。

### ⑥ 根の原因: **どちらの期待値も較正されていない**

  買った点の的中を2つの確率で較正比較した（確認26後 n=5,408 / 独立2025 n=17,139）:

    実測的中率            0.0743 / 0.0694
    PL（本番 `_pl_trio`） 平均 0.092/0.094  Brier 0.0697/0.0655
       五分位 予測→実測   0.009→0.030 … 0.271→**0.151**   ← 上位帯で強い過信
    Web（確認画面）        平均 0.055/0.055  Brier 0.0664/0.0617
       五分位 予測→実測   0.022→0.019 … 0.103→**0.173**   ← 上位帯で過小

  **確率の掛かる側が壊れているので、積である EV も順序を持てない。**
  ⚠️ 表示（0.24）と本番（0.16）で値が違うのはこのため。**どちらも絶対値としては
     信用できない**ので、片方に揃えても改善にはならない（Brier は Web のほうが
     わずかに良い＝本番へ揃えると表示は**悪くなる**）。

### ✅ 何なら残るか

  期待値ではなく**払戻の絶対額**で切る既存ゲートは既に入っている
  （`MIN_POINT_ODDS` 2.0倍 / `expected_payout_floor` 1.5倍 / `MIN_MEAN_PAYOUT` 2万円）。
  和歌山10R は想定払戻 25,000円 でこの3つを通る。**「薄い配当を切る」方針では
  この1件は捕まらない**——捕まえるには EV が要るが、上のとおり EV は使えない。
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.stake_allocation import (  # noqa: E402
    MIN_MEAN_PAYOUT, cheap_point_odds, mean_expected_payout,
    mean_payout_gate_applies, tilted_stakes,
)
from src.strategy_wt import (  # noqa: E402
    RANK_7M1_LEGS, RANK_7M1_MARU_CONC_ODDS_MAX, RANK_7M1_MARU_CONC_ODDS_MIN,
    rank_7m1_maru_concentrates, rank_7m1_select_legs,
)

BUDGET = 10_000
UNIT = 100
CACHE_DIR = REPO / "data" / "exp"
WINDOWS = [
    ("掃引26前", "7m1_partner_count_2026.jsonl", "2026-01-01", "2026-04-30"),
    ("確認26後", "7m1_partner_count_2026.jsonl", "2026-05-01", "2026-08-31"),
    ("独立2025", "7m1_partner_count_2025.jsonl", "2025-01-01", "2025-12-31"),
]


def _load(fname: str, lo: str, hi: str) -> list[dict]:
    out = []
    with (CACHE_DIR / fname).open() as fh:
        for line in fh:
            r = json.loads(line)
            if lo <= r["date"] <= hi:
                r["odds"] = {int(k): float(v) for k, v in r["odds"].items()}
                r["prob"] = {int(k): float(v) for k, v in r["prob"].items()}
                r["p3"] = {int(k): float(v) / 100.0 for k, v in r["p3"].items()}
                r["mark"] = {int(k): int(v) for k, v in r["mark"].items()}
                out.append(r)
    return out


def _maru(r: dict) -> int | None:
    """相手プールに居る ○(mark2) の車番。"""
    return next((t for t in r["others"] if r["mark"].get(t) == 2), None)


def _legs_current(r: dict) -> list[int]:
    ev = {t: r["odds"][t] * r["prob"][t] for t in r["others"]}
    return rank_7m1_select_legs(
        r["others"], r["p3"], ev=ev, odds=r["odds"], marks=r["mark"])


def _legs_ev_order(r: dict) -> list[int]:
    """○1点集中を通さない EV 上位 n 点（○/△は後回し）。B案のフォールバック先。"""
    ev = {t: r["odds"][t] * r["prob"][t] for t in r["others"]}
    return rank_7m1_select_legs(
        r["others"], r["p3"], ev=ev, odds=None, marks=r["mark"])


def _score(r: dict, legs: list[int]) -> dict | None:
    """本番のダッチング配分＋入稿ゲートで1レースを採点する。買わないなら None。"""
    if not legs:
        return None
    odds = {t: r["odds"][t] for t in legs}
    if cheap_point_odds(odds) is not None:          # MIN_POINT_ODDS（2.0倍）
        return None
    stakes, _ = tilted_stakes(legs, None, r["p3"], budget=BUDGET, unit=UNIT,
                              predicted_odds=r["odds"])
    mean_pay = mean_expected_payout(stakes, r["odds"])
    p3_sum = sum(sorted(r["p3"].values(), reverse=True)[:2])
    if (mean_payout_gate_applies(7, p3_sum) and mean_pay is not None
            and mean_pay <= MIN_MEAN_PAYOUT):       # MIN_MEAN_PAYOUT（2万円）
        return None
    ev_race = sum(r["prob"][t] * r["odds"][t] * stakes[t] for t in legs) / BUDGET
    win = set(r["top3"])
    payout = 0
    for t in legs:
        if {r["a1"], r["a2"], t} == win:
            payout = r["trio"] * stakes[t] // 100
    return {"date": r["date"], "bet": BUDGET, "payout": payout,
            "hit": payout > 0, "ev": ev_race, "n": len(legs)}


def _agg(rows: list[dict], n_days: int) -> dict:
    n = len(rows)
    hits = [x for x in rows if x["hit"]]
    bet = sum(x["bet"] for x in rows)
    pay = sum(x["payout"] for x in rows)
    ratios = [x["payout"] / x["bet"] for x in hits]
    return {
        "n": n, "件/日": n / n_days if n_days else 0,
        "的中%": 100 * len(hits) / n if n else 0,
        "的中/日": len(hits) / n_days if n_days else 0,
        "2倍+/日": sum(1 for x in ratios if x >= 2) / n_days if n_days else 0,
        "ガミ%": 100 * sum(1 for x in ratios if x < 1) / len(hits) if hits else 0,
        "倍率中央": statistics.median(ratios) if ratios else 0,
        "ROI%": 100 * pay / bet if bet else 0,
    }


def _boot(pairs: list[tuple[float, float]], n_days: int, iters: int = 2000) -> tuple[float, float]:
    """レース単位 paired bootstrap の 95%CI（件/日 換算の差）。"""
    rng = random.Random(20260825)
    diffs = [a - b for a, b in pairs]
    n = len(diffs)
    if not n:
        return (0.0, 0.0)
    out = []
    for _ in range(iters):
        s = sum(diffs[rng.randrange(n)] for _ in range(n))
        out.append(s / n_days)
    out.sort()
    return (out[int(iters * 0.025)], out[int(iters * 0.975)])


def main() -> int:
    print("=" * 100)
    print("① EV は成績と単調に対応するか（現行の買い方のまま・レース期待値の五分位）")
    print("=" * 100)
    for name, fname, lo, hi in WINDOWS:
        races = _load(fname, lo, hi)
        scored = [s for s in (_score(r, _legs_current(r)) for r in races) if s]
        scored.sort(key=lambda x: x["ev"])
        q = max(1, len(scored) // 5)
        print(f"\n[{name}] n={len(scored)}")
        print(f"  {'EV帯':<16}{'件数':>6}{'平均EV':>8}{'的中%':>8}{'ROI%':>8}{'倍率中央':>9}")
        for i in range(5):
            part = scored[i * q:(i + 1) * q] if i < 4 else scored[4 * q:]
            if not part:
                continue
            a = _agg(part, 1)
            print(f"  {i+1}/5 "
                  f"{part[0]['ev']:.2f}〜{part[-1]['ev']:.2f}".ljust(18)
                  + f"{a['n']:>6}"
                  + f"{statistics.mean(x['ev'] for x in part):>8.2f}"
                  + f"{a['的中%']:>8.1f}{a['ROI%']:>8.1f}{a['倍率中央']:>9.2f}")

    print("\n" + "=" * 100)
    print("② ○1点集中の内訳（EV がいくつの点を単独で買っているか）")
    print("=" * 100)
    for name, fname, lo, hi in WINDOWS:
        races = _load(fname, lo, hi)
        conc = []
        for r in races:
            m = _maru(r)
            if m is None:
                continue
            pt = {t: r["odds"][t] for t in r["others"]}
            if not rank_7m1_maru_concentrates(m, pt):
                continue
            s = _score(r, [m])
            if s:
                conc.append((r["odds"][m] * r["prob"][m], s))
        conc.sort(key=lambda x: x[0])
        h = max(1, len(conc) // 2)
        print(f"\n[{name}] ○1点集中 n={len(conc)}")
        for lab, part in (("EV 下半分", conc[:h]), ("EV 上半分", conc[h:])):
            if not part:
                continue
            a = _agg([s for _, s in part], 1)
            print(f"  {lab}  EV {part[0][0]:.2f}〜{part[-1][0]:.2f}  "
                  f"n={a['n']:>4}  的中 {a['的中%']:>5.1f}%  ROI {a['ROI%']:>6.1f}%  "
                  f"倍率中央 {a['倍率中央']:.2f}")
    print("\n" + "=" * 100)
    print("③ ○1点集中を EV の細帯で切る（和歌山10R は EV 0.16）")
    print("=" * 100)
    bands = [(0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1.0), (1.0, 9.9)]
    for name, fname, lo, hi in WINDOWS:
        races = _load(fname, lo, hi)
        conc = []
        for r in races:
            m = _maru(r)
            if m is None:
                continue
            if not rank_7m1_maru_concentrates(m, {t: r["odds"][t] for t in r["others"]}):
                continue
            s2 = _score(r, [m])
            if s2:
                conc.append((r["odds"][m] * r["prob"][m], s2))
        print(f"\n[{name}] ○1点集中 n={len(conc)}")
        print(f"  {'EV帯':<12}{'件数':>6}{'的中%':>8}{'ROI%':>9}{'倍率中央':>9}")
        for blo, bhi in bands:
            part = [s2 for ev, s2 in conc if blo <= ev < bhi]
            if not part:
                continue
            a = _agg(part, 1)
            print(f"  {blo:.1f}〜{bhi:.1f}".ljust(14)
                  + f"{a['n']:>6}{a['的中%']:>8.1f}{a['ROI%']:>9.1f}{a['倍率中央']:>9.2f}")

    print("\n" + "=" * 100)
    print("④ 腕B: ○1点集中に EV 下限を掛け、割れたら EV 上位4点へ戻す")
    print("=" * 100)
    for theta in (0.3, 0.5, 0.7, 0.9):
        print(f"\n--- EV 下限 {theta} ---")
        for name, fname, lo, hi in WINDOWS:
            races = _load(fname, lo, hi)
            n_days = len({r["date"] for r in races})
            cur, alt, pairs_pay, pairs_hit = [], [], [], []
            n_fire = 0
            for r in races:
                lc = _legs_current(r)
                m = _maru(r)
                if (len(lc) == 1 and m is not None and lc == [m]
                        and r["odds"][m] * r["prob"][m] < theta):
                    la = _legs_ev_order(r)
                    n_fire += 1
                else:
                    la = lc
                sc, sa = _score(r, lc), _score(r, la)
                if sc:
                    cur.append(sc)
                if sa:
                    alt.append(sa)
                pairs_pay.append(((sa["payout"] - sa["bet"]) if sa else 0,
                                  (sc["payout"] - sc["bet"]) if sc else 0))
                pairs_hit.append((1 if (sa and sa["hit"]) else 0,
                                  1 if (sc and sc["hit"]) else 0))
            ac, aa = _agg(cur, n_days), _agg(alt, n_days)
            ci_hit = _boot(pairs_hit, n_days)
            print(f"  [{name}] 差し替え {n_fire}件")
            for lab, a in (("現行", ac), ("腕B", aa)):
                print(f"    {lab}  件/日 {a['件/日']:.2f}  的中 {a['的中%']:.1f}%  "
                      f"的中/日 {a['的中/日']:.2f}  2倍+/日 {a['2倍+/日']:.2f}  "
                      f"ROI {a['ROI%']:.1f}%  倍率中央 {a['倍率中央']:.2f}")
            print(f"    Δ的中件/日 CI95 [{ci_hit[0]:+.3f}, {ci_hit[1]:+.3f}]")
    print("\n" + "=" * 100)
    print("⑤ ○1点集中を『○の予測オッズ』で切る（市場は較正されている量）")
    print("=" * 100)
    obands = [(2.0, 2.3), (2.3, 2.6), (2.6, 2.9), (2.9, 3.11)]
    for name, fname, lo, hi in WINDOWS:
        races = _load(fname, lo, hi)
        conc = []
        for r in races:
            m = _maru(r)
            if m is None:
                continue
            if not rank_7m1_maru_concentrates(m, {t: r["odds"][t] for t in r["others"]}):
                continue
            s2 = _score(r, [m])
            if s2:
                conc.append((r["odds"][m], s2))
        print(f"\n[{name}] ○1点集中 n={len(conc)}")
        print(f"  {'○の予測オッズ':<16}{'件数':>6}{'的中%':>8}{'ROI%':>9}")
        for blo, bhi in obands:
            part = [s2 for o, s2 in conc if blo <= o < bhi]
            if not part:
                continue
            a = _agg(part, 1)
            print(f"  {blo:.1f}〜{bhi:.2f}".ljust(18)
                  + f"{a['n']:>6}{a['的中%']:>8.1f}{a['ROI%']:>9.1f}")
    print("\n" + "=" * 100)
    print("⑥ 腕C: レース期待値が下限未満なら**見送る**（ユーザー提案そのもの）")
    print("=" * 100)
    for theta in (0.3, 0.5, 0.7, 0.9, 1.0):
        print(f"\n--- 期待値 下限 {theta} ---")
        for name, fname, lo, hi in WINDOWS:
            races = _load(fname, lo, hi)
            n_days = len({r["date"] for r in races})
            cur, kept, pairs_hit = [], [], []
            for r in races:
                sc = _score(r, _legs_current(r))
                if sc:
                    cur.append(sc)
                    if sc["ev"] >= theta:
                        kept.append(sc)
                pairs_hit.append((1 if (sc and sc["hit"] and sc["ev"] >= theta) else 0,
                                  1 if (sc and sc["hit"]) else 0))
            ac, ak = _agg(cur, n_days), _agg(kept, n_days)
            ci = _boot(pairs_hit, n_days)
            print(f"  [{name}] 見送り {ac['n'] - ak['n']}件 / {ac['n']}件")
            for lab, a in (("現行", ac), ("腕C", ak)):
                print(f"    {lab}  件/日 {a['件/日']:.2f}  的中 {a['的中%']:.1f}%  "
                      f"的中/日 {a['的中/日']:.2f}  2倍+/日 {a['2倍+/日']:.2f}  "
                      f"ROI {a['ROI%']:.1f}%")
            print(f"    Δ的中件/日 CI95 [{ci[0]:+.3f}, {ci[1]:+.3f}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
