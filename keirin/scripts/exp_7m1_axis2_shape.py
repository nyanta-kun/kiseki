#!/usr/bin/env python3
"""7M1「軸1は抜けているが二軸に絞れないレース」を見送れるか（2026-08-26・ユーザー要望）。

## きっかけ

> 本日の玉野1R、3=5=7 の三連複1点で2.6倍・期待値0.41。結果 3=6=1 でハズレ。
> 指数として1着は3が抜けているが他は混戦で、1点で2.6倍に投入するようなレースでは
> ない。二軸に絞るのも難しく、推奨レースから外れるのが良さそう。

## 🔴 本番コードから確認した事実（測る前に読むこと・CLAUDE.md「検証の作法」）

玉野1R（`20260826_61_01`・軸 3=7・RANK_7M1・07:09 morning 入稿）。
本番の `trio_ev_and_odds_for_legs` を実データに当てた相手5車:

    相手  公式印  pw%   p3%   予測オッズ  PL同時p   EV
      6    無印    5.4  15.8     37.50    0.0500   1.876
      2    無印   10.2  33.9     15.90    0.1008   1.602
      1    無印    3.5  21.6     23.66    0.0317   0.750
      4     △     6.5  36.7     12.13    0.0611   0.741
      5     ○    14.6  59.4      2.65    0.1545   **0.409**  ← 買ったのはこれ

**2026-08-25 の和歌山10R と同一の機序**（`rank_7m1_maru_concentrates` の○1点集中が
EV 順より先に評価される）。EV 最下位の点を単独で買う。confident_ev 0.4089 は
この EV そのもの。既存3ゲートは想定払戻 26,500円 で全て通る。

🔴 **このレースは相手選定では救えない。** 着順は 3(1着)-6(2着)-1(3着) で
   **軸2の7が5着・○の5が6着**。軸2車が {3,7} に固定される以上、どの相手を
   買っても外れる。同レースの 7S 候補（3=7-1,2,4,5,6 の総流し5点）も同じく外れ。

🔴 **7S は同じレース・同じ軸を「薄い」と切っていた**（`submission_skips`
   08:47 `gate_mean_payout` 平均払戻 17,230円 <= 20,000円）。○1点へ集中すると
   同じ盤面が 26,000円 になりゲートを通る。2026-08-26 だけで同型が3件
   （`20260826_53_10` 29,951円 / `20260826_61_01` 26,000円 / `20260826_61_07` 25,008円）。
   ⚠️ `submission_skips` は 2026-08-25 開始なのでそれ以前の頻度は分からない。

## 何を測るか

ユーザーの条件を3つの形に落として、**レースを見送れるか**を測る:

  B  形状ゲート … p3(軸2)−p3(○) < 5pt ∧ pw(軸1) >= 45%（＝1着は抜け・二軸は拮抗）
  C  同条件で○集中をやめて EV 上位4点へ（レースは残す）
  D  ○1点集中を全廃（常に EV 上位4点）
  E  ○集中が発火したレースを全部見送り
  F  二軸そろい確率（PL 同時確率の相手方向の和）が閾値未満なら見送り

## プロトコル

`exp_7m1_ev_gate.py` と同じ台。母集団・軸は `picks_history` の 7M1 行に固定し
買い目だけ差し替える（`exp_7m1_partner_count.py` のキャッシュを共有・予測オッズは
vintage）。採点は本番のダッチング配分＋入稿ゲート（`MIN_POINT_ODDS` /
`MIN_MEAN_PAYOUT`）＋確定配当。窓は 掃引2026-01〜04 / 確認2026-05〜08 / 独立2025。
`pw` は `wt_entries.pred_win_pct`（p3 と同じ出所）を別途 bulk 取得する。

## 🔴🔴 結論（2026-08-26）: **どの形でも切れない。玉野1R は母集団の中央値**

### ① 玉野1R は外れ値ではない（○集中発火レースの中での位置）

    量                     掃引26前   確認26後   独立2025
    p3(軸1)−p3(軸2) 16.8pt   下48%点   下54%点   下52%点
    p3(軸2)−p3(○)   3.5pt   下49%点   下50%点   下44%点
    pw(軸1)         51.0%    下68%点   下62%点   下66%点
    二軸そろい確率   0.398    下29%点   下29%点   下29%点
    （形状3量は○集中発火レース内での位置。二軸そろい確率は 7M1 全体で見ても下30%点）

  ○集中が発火する帯（市場が {軸1,軸2,○} を2.0〜3.1倍に付ける）は**定義上
  「軸1が抜けていて2番手が拮抗している」形**なので、その形を切ることは
  帯そのものを切ることと同じになる。

### ② 腕B〜E — ❌ 全て不採用（件数だけ失う）

    腕                        件/日(独立2025)  Δ的中/日 CI95（掃引 / 確認 / 独立）
    B 形状ゲートで見送り        12.17   [-0.233,-0.092] [-0.241,-0.095] [-0.156,-0.088] 🔴🔴🔴
    C 同条件で EV4点へ          12.57   [-0.108,+0.075] [-0.198,-0.026] [-0.049,+0.041]
    D ○集中を全廃              12.31   [-0.267,+0.008] [-0.319,-0.034] [-0.252,-0.077] 🔴🔴
    E ○集中レースを全部見送り    10.74   [-0.558,-0.325] [-0.629,-0.379] [-0.630,-0.488] 🔴🔴🔴
    （現行 A は 11.56 / 12.25 / 12.64 件・的中/日 3.000 / 3.198 / 2.997）

  ROI の CI は**全ての腕・全ての窓で0を跨ぐ**（最大幅 ±13pt）。

### ③ 腕F（二軸そろい確率で見送り） — ❌ 不採用

    閾値   件/日(独立)  的中%  ROI%   Δ的中/日 CI95（掃引 / 確認 / 独立）
    現行    12.64      23.7   76.1
    p<0.30  10.79      23.5   74.8  [-0.533,-0.317] [-0.517,-0.293] [-0.521,-0.386] 🔴🔴🔴
    p<0.40   8.71      24.6   75.4  [-0.958,-0.658] [-1.155,-0.828] [-0.940,-0.764] 🔴🔴🔴

  五分位で見ると**実際の二軸そろい率は Q1 35〜37% → Q5 45〜53% と確かに動く**が、
  的中率は 22.5→27.7%（独立）しか動かず ROI は単調にならない（独立 78.2 / 79.9 /
  68.8 / 77.4 / 76.3%）。**当たりやすさが上がった分だけオッズが下がる**。

### ④ ○集中の上限オッズを下げる（3.1 → 2.4/2.6/2.8）— 効果なし

    上限   件/日(独立)  的中/日  ROI%   Δ的中/日 CI95（掃引 / 確認 / 独立）
    3.1(現行) 12.64     2.997   76.1
    2.6       12.48     2.951   76.3  [-0.092,+0.092] [-0.086,+0.112] [-0.107,+0.016]

  帯別成績は窓ごとに順位が入れ替わり（2.6〜2.9倍が 82.3 / 62.2 / 87.2%）、
  3窓で一貫して良いのは 2.0〜2.3倍だけ。ただしそこへ絞っても影響量が
  0.4〜0.6件/日 しかなく、どの指標も有意にならない。

## ✅ 唯一はっきり動くもの: **買い方が商品の払戻帯を決めている**

同じ母集団・同じ軸で買い目の作り方だけを変えると（3窓とも同じ向き）:

    買い方                    的中%     的中/日   ROI%     倍率中央   10倍+が的中に占める割合
    現行 EV順+○集中(4点)   23.7〜26.1  3.00〜3.20  76〜87   2.81〜2.91   0.5〜1.3%
    EV順のみ(4点)          23.0〜25.5  2.83〜3.02  76〜88   2.92〜3.04   0.8〜1.4%
    旧 位置規則(下位3車)    11.1〜12.3  1.42〜1.59  75〜88   5.75〜6.10  16.5〜20.7%

🔴 **ROI は3窓とも動かない。** 動くのは的中頻度と配当の交換だけ
（[[keirin_rank_priority_ev_rejected_2026_08_25]] の一般則と同型）。

実入稿でも同じ向きに出ている（2026-08 の 7M1・想定払戻の中央値）:

    〜08-20（位置規則）        n=14  平均2.50点  88,852円
    08-21〜23（EV順3点）      n=20  平均2.90点  40,270円
    08-24〜（EV順4点+○集中） n=26  平均3.35点  33,805円

7M1 の設立趣旨は「ベース層(1.45〜1.57倍)と 7H1(8.38倍)の間＝**3〜10倍の空白**を
埋めること」だった（`RANK_7M1_P3_SUM_MAX` 冒頭）。現行の中央 2.8〜2.9倍 は
7B(2.29倍)のすぐ上で、**空白は再び空いている**。
🔴 **これは測定で決まる問題ではなく店頭KPIの選択**（的中頻度を取るか配当を取るか）。

## 実行

    KEIRIN_DB_URL=... python scripts/exp_7m1_axis2_shape.py [--pw]   # --pw で pw を再取得
"""
from __future__ import annotations

import json
import os
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
    rank_7m1_maru_concentrates, rank_7m1_select_legs,
)

BUDGET, UNIT = 10_000, 100
CACHE_DIR = REPO / "data" / "exp"
PW_CACHE = CACHE_DIR / "7m1_pred_win.json"
WINDOWS = [
    ("掃引26前", "7m1_partner_count_2026.jsonl", "2026-01-01", "2026-04-30"),
    ("確認26後", "7m1_partner_count_2026.jsonl", "2026-05-01", "2026-08-31"),
    ("独立2025", "7m1_partner_count_2025.jsonl", "2025-01-01", "2025-12-31"),
]


def build_pw() -> None:
    """キャッシュ対象レースの `pred_win_pct` を1クエリでまとめて取る。

    p3 と同じ `wt_entries` 由来なので、キャッシュ本体と出所が揃う。
    """
    import psycopg2

    keys = set()
    for _, f, _, _ in WINDOWS:
        for line in (CACHE_DIR / f).open():
            keys.add(json.loads(line)["rk"])
    conn = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    cur = conn.cursor()
    cur.execute("SELECT race_key, frame_no, pred_win_pct FROM keirin.wt_entries "
                "WHERE race_key = ANY(%s)", (sorted(keys),))
    pw: dict[str, dict[str, float]] = {}
    for rk, fn, v in cur.fetchall():
        pw.setdefault(rk, {})[str(int(fn))] = float(v or 0.0)
    conn.close()
    PW_CACHE.write_text(json.dumps(pw))
    print(f"pw cache: {len(pw)} レース -> {PW_CACHE}")


PW: dict[str, dict[str, float]] = {}


def load(fname: str, lo: str, hi: str) -> list[dict]:
    out = []
    for line in (CACHE_DIR / fname).open():
        r = json.loads(line)
        if not (lo <= r["date"] <= hi):
            continue
        r["odds"] = {int(k): float(v) for k, v in r["odds"].items()}
        r["prob"] = {int(k): float(v) for k, v in r["prob"].items()}
        r["p3"] = {int(k): float(v) / 100.0 for k, v in r["p3"].items()}
        r["mark"] = {int(k): int(v) for k, v in r["mark"].items()}
        r["pw"] = {int(k): float(v) / 100.0 for k, v in PW.get(r["rk"], {}).items()}
        out.append(r)
    return out


def maru(r: dict) -> int | None:
    return next((t for t in r["others"] if r["mark"].get(t) == 2), None)


def fired(r: dict) -> int | None:
    m = maru(r)
    return m if rank_7m1_maru_concentrates(m, r["odds"]) else None


def _ev(r: dict) -> dict[int, float]:
    return {t: r["odds"][t] * r["prob"][t] for t in r["others"]}


def legs_cur(r: dict) -> list[int]:
    return rank_7m1_select_legs(r["others"], r["p3"], ev=_ev(r),
                               odds=r["odds"], marks=r["mark"])


def legs_ev(r: dict) -> list[int]:
    """○1点集中を通さない EV 上位 n 点。"""
    return rank_7m1_select_legs(r["others"], r["p3"], ev=_ev(r),
                               odds=None, marks=r["mark"])


def legs_pos(r: dict) -> list[int]:
    """2026-08-21 以前の位置規則（下位3車＋足切り）。"""
    return rank_7m1_select_legs(r["others"], r["p3"])


def pair_prob(r: dict) -> float:
    """二軸そろい確率（PL 同時確率を相手方向へ足したもの）。"""
    return sum(r["prob"][t] for t in r["others"])


def score(r: dict, legs: list[int]) -> dict | None:
    """本番のダッチング配分＋入稿ゲートで1レースを採点する。買わないなら None。"""
    if not legs:
        return None
    odds = {t: r["odds"][t] for t in legs}
    if cheap_point_odds(odds) is not None:
        return None
    stakes, _ = tilted_stakes(legs, None, r["p3"], budget=BUDGET, unit=UNIT,
                              predicted_odds=r["odds"])
    mp = mean_expected_payout(stakes, r["odds"])
    p3_sum = sum(sorted(r["p3"].values(), reverse=True)[:2])
    if mean_payout_gate_applies(7, p3_sum) and mp is not None and mp <= MIN_MEAN_PAYOUT:
        return None
    win = set(r["top3"])
    payout = 0
    for t in legs:
        if {r["a1"], r["a2"], t} == win:
            payout = r["trio"] * stakes[t] // 100
    return {"bet": BUDGET, "payout": payout, "hit": payout > 0}


def agg(rows: list[dict], nd: int) -> dict:
    n = len(rows)
    hits = [x for x in rows if x["hit"]]
    bet = sum(x["bet"] for x in rows)
    ratios = sorted(x["payout"] / x["bet"] for x in hits)
    return {
        "n": n, "per": n / nd if nd else 0,
        "hit": 100 * len(hits) / n if n else 0,
        "hitday": len(hits) / nd if nd else 0,
        "two": sum(1 for x in ratios if x >= 2) / nd if nd else 0,
        "roi": 100 * sum(x["payout"] for x in rows) / bet if bet else 0,
        "med": statistics.median(ratios) if ratios else 0,
        "m10": 100 * sum(1 for x in ratios if x >= 10) / len(ratios) if ratios else 0,
    }


def boot(pairs, nd, B=1000):
    """レース単位ブートストラップ。(Δ的中/日, Δ2倍+/日, ΔROI) の95%CI。"""
    rnd = random.Random(7)
    vec = []
    for a, b in pairs:
        def q(s):
            if not s:
                return (0, 0, 0, 0)
            rt = s["payout"] / s["bet"]
            return (1 if s["hit"] else 0, 1 if rt >= 2 else 0, s["payout"], s["bet"])
        vec.append((q(a), q(b)))
    n = len(vec)
    res: list[list[float]] = [[], [], []]
    for _ in range(B):
        ah = at = ap = ab = bh = bt = bp = bb = 0
        for _ in range(n):
            a, b = vec[rnd.randrange(n)]
            ah, at, ap, ab = ah + a[0], at + a[1], ap + a[2], ab + a[3]
            bh, bt, bp, bb = bh + b[0], bt + b[1], bp + b[2], bb + b[3]
        res[0].append((bh - ah) / nd)
        res[1].append((bt - at) / nd)
        res[2].append((100 * bp / bb if bb else 0) - (100 * ap / ab if ab else 0))
    out = []
    for r in res:
        r.sort()
        out.append((r[int(0.025 * B)], r[int(0.975 * B)]))
    return out


def shape_gate(r: dict) -> bool:
    """ユーザー条件: 1着は抜けている ∧ 軸2と○が拮抗している。"""
    m = fired(r)
    return bool(m and r["p3"][r["a2"]] - r["p3"][m] < 0.05
                and r["pw"].get(r["a1"], 0.0) >= 0.45)


def run(name: str, fname: str, lo: str, hi: str) -> None:
    rows = load(fname, lo, hi)
    nd = len({r["date"] for r in rows})
    fr = [r for r in rows if fired(r)]
    print(f"\n===== {name}  7M1 {len(rows)}件 / {nd}日 / ○集中発火 {len(fr)}件 "
          f"({100 * len(fr) / len(rows):.1f}%) =====")

    base = [score(r, legs_cur(r)) for r in rows]
    arms = {
        "A 現行": legs_cur,
        "B 形状ゲートで見送り": lambda r: ([] if shape_gate(r) else legs_cur(r)),
        "C 同条件で EV4点へ": lambda r: (legs_ev(r) if shape_gate(r) else legs_cur(r)),
        "D ○集中を全廃": legs_ev,
        "E ○集中レースを見送り": lambda r: ([] if fired(r) else legs_cur(r)),
        "F 二軸そろい確率<0.40 見送り": lambda r: ([] if pair_prob(r) < 0.40 else legs_cur(r)),
        "G 旧 位置規則(下位3車)": legs_pos,
    }
    hdr = (f"  {'腕':26s} {'件/日':>6s} {'的中%':>6s} {'的中/日':>7s} {'2倍+/日':>7s} "
           f"{'ROI%':>6s} {'倍率中央':>7s} {'10倍+':>6s}")
    print(hdr)
    for k, fn in arms.items():
        sc = [score(r, fn(r)) for r in rows]
        a = agg([s for s in sc if s], nd)
        line = (f"  {k:26s} {a['per']:6.2f} {a['hit']:6.1f} {a['hitday']:7.3f} "
                f"{a['two']:7.3f} {a['roi']:6.1f} {a['med']:7.2f} {a['m10']:5.1f}%")
        if k != "A 現行":
            (l1, h1), (l2, h2), (l3, h3) = boot(list(zip(base, sc)), nd)
            line += (f"  Δ的中/日[{l1:+.3f},{h1:+.3f}] Δ2倍+/日[{l2:+.3f},{h2:+.3f}]"
                     f" ΔROI[{l3:+.1f},{h3:+.1f}]")
        print(line)

    print("  [○集中発火レースの形状分位]")
    for lbl, fn in (("p3(軸2)-p3(○)", lambda r: r["p3"][r["a2"]] - r["p3"][fired(r)]),
                    ("pw(軸1)", lambda r: r["pw"].get(r["a1"], 0.0)),
                    ("二軸そろい確率", pair_prob)):
        v = sorted(fn(r) for r in fr)
        med = statistics.median(v)
        print(f"    {lbl:16s} 中央={med:.3f}")


def main() -> None:
    global PW
    if "--pw" in sys.argv or not PW_CACHE.exists():
        build_pw()
    PW = json.loads(PW_CACHE.read_text())
    for w in WINDOWS:
        run(*w)


if __name__ == "__main__":
    main()
