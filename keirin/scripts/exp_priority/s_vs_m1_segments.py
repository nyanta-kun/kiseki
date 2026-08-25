#!/usr/bin/env python3
"""7S と 7M1 の優先順位を**レースの型で条件分岐**できるか（2026-08-25・ユーザー選択）。

## 経緯

- 「期待値で並べる」は否定済み（`rank_arms.py`）。全商品の EV がほぼ同じで、
  EV 最大と EV 最小が同じ結果になる
- **同じ問いを EV ではなく構造で解く**のが本スクリプト。競合 313本／全期間なら
  2千本超が 7M1 vs 7S に集中しており、7M1 は候補の半分を譲っている

## 🔴 測る前に確認したこと（CLAUDE.md「検証の作法」）

- 母集団は `picks_history` の**本番記録**（rebuild が本番と同じ判定関数・同じ配分で
  書いた行）。7M1 は 2025-01〜 単一 `rule_version`（`f71b49b6a7b9` ＝ 現行と一致）で、
  月次件数に不連続が無いことは `exp_7s_vs_7m1_priority.py` が確認済み
- 採点・CI・指標はその既存ハーネスを **import して共有**する。ここに書き直さない
- 🔴 **主指標は「2倍以上で的中した率」**（2026-08-21 のユーザー方針
  「的中率そのものには意味がない・件数は減ってよい」）。ROI は**判定に使わない**
  （この層は ±2.5pt に収めるのに約15.6年かかる）
- 🔴 **確認窓は年をまたぐ**。探索 2025 / 確認 2026

## 🔴 多重比較の扱い

セグメントを 20 通り前後試すので、**探索窓で良かっただけのものは必ず出る**。
採用候補にするのは次を全て満たすものだけ:

  1. 探索窓（2025）で 2倍以上の的中が 7M1 > 7S
  2. **確認窓（2026）でも同じ符号**
  3. どちらの窓でも n >= 60（小さすぎるセルは読まない）

DB は読み取りのみ。

## 🔴🔴 結果（2026-08-25・競合 2,420R / 探索2025 1,477 + 確認2026 943）

### ① 条件分岐は成立しない — **どのセグメントでも取引の向きが同じ**

主指標「2倍以上で的中」では **19セグメント中、両窓で 7M1 が上回るものはゼロ**。
探索窓で 7M1 が勝ったもの（準決勝 +2.98 / 選抜 +0.74）は確認窓で反転した。

### ② ただし「5倍以上で的中」では **全セグメントで 7M1 が上**（両窓とも）

    競合全体   探索2025 +4.20pt [+2.91,+5.55]   確認2026 +3.39pt [+2.01,+4.77]

  決勝系・準決勝・予選・特選・一般・選抜・FI・FII・混戦・堅い・ライン2〜4本以上・
  開催日・7M1の点数——**18セグメントすべてで同符号**。
  ＝ **レースの型によって選び分ける余地は無い。取引は一様。**

### ③ 取引の中身（競合 2,420R ＝ 4.14件/日）

    指標              7S(現行)   7M1     差（CI が0を跨がないもののみ *）
    的中率             41.3%    12.9%   -28.4pt *
    表示的中率（店頭）    33.1%    12.8%   -20.2pt *
    2倍以上で的中        13.7%    11.9%    -1.82pt *
    5倍以上で的中         1.9%     5.7%    **+3.88pt** *
    10倍以上で的中        0.12%    2.11%   **約18倍**（実数 3件 → 51件）
    ROI               81.7%    80.7%    -1.0pt [-10.4,+8.7]（判別不能）
    倍率中央            1.57     4.65
    的中のうち2倍未満     66.9%     8.0%

  ⚠️ **両方的中した277件は 277/277＝100% で 7M1 の払戻が上**だが、
     総払戻はほぼ同額（7S 19,780,260円 ↔ 7M1 19,527,620円）。
     **個別のレースを見ると必ず 7M1 が良く見える**ので事例から判断しないこと。

### ✅ 結論

**「レースの型で条件分岐する」は不可。** 代わりに分かったのは、
7S↔7M1 が**単一の大域ダイヤル**だということ:

    表示的中 −20.2pt ・ 2倍以上の的中 −1.8pt を払って、
    5倍以上の的中 +3.9pt ・ 10倍以上の的中 約18倍 を買う。ROI は動かない。

**どちらを取るかは測定では決まらない**（ROI が同じなので）。店頭 KPI の設計判断。
論点整理は `docs/rank_priority_redesign_2026_08_25.md` 6節。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import psycopg2                                            # noqa: E402
import psycopg2.extras                                     # noqa: E402

from exp_7s_vs_7m1_priority import Acc, boot_rate, load    # noqa: E402


def race_features(keys: list[str]) -> dict[str, dict]:
    """レースごとの型（入稿の朝に分かるものだけ）。"""
    conn = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT race_key, race_type, cup_grade, day_index, n_entries
        FROM keirin.wt_races WHERE race_key = ANY(%s)""", (keys,))
    out = {r["race_key"]: dict(r) for r in cur.fetchall()}
    cur.execute("""
        SELECT race_key, frame_no, pred_top3_pct, line_group
        FROM keirin.wt_entries WHERE race_key = ANY(%s)""", (keys,))
    ent: dict[str, list] = {}
    for r in cur.fetchall():
        ent.setdefault(r["race_key"], []).append(r)
    conn.close()
    for rk, es in ent.items():
        p3 = sorted((float(e["pred_top3_pct"] or 0) for e in es), reverse=True)
        d = out.setdefault(rk, {})
        # ゲートが見ているのと同じ量（上位2車の3着内率合計）
        d["p3_sum2"] = (p3[0] + p3[1]) / 100 if len(p3) >= 2 else None
        d["n_lines"] = len({e["line_group"] for e in es if e["line_group"]}) or None
    return out


#: レースの型 → 判定関数。**朝の入稿時点で分かるものだけ**。
def _segments(meta: dict, n_m1: int) -> dict[str, bool]:
    t = str(meta.get("race_type") or "")
    g = meta.get("cup_grade")
    p = meta.get("p3_sum2")
    nl = meta.get("n_lines")
    di = meta.get("day_index")
    return {
        "決勝系（決勝/チャレンジ決勝）": ("決勝" in t and "準決勝" not in t),
        "準決勝": ("準決勝" in t),
        "予選系": ("予選" in t),
        "特選系": ("特選" in t),
        "一般": (t == "一般"),
        "選抜": ("選抜" in t),
        "GIII以上（cup_grade>=3）": (g is not None and int(g) >= 3),
        "FI（cup_grade=2）": (g == 2),
        "FII（cup_grade=1）": (g == 1),
        "混戦（p3_sum2 < 1.20）": (p is not None and p < 1.20),
        "中間（1.20<=p3_sum2<1.35）": (p is not None and 1.20 <= p < 1.35),
        "堅い（p3_sum2 >= 1.35）": (p is not None and p >= 1.35),
        "ライン2本以下": (nl is not None and nl <= 2),
        "ライン3本": (nl == 3),
        "ライン4本以上": (nl is not None and nl >= 4),
        "開催1-2日目": (di is not None and int(di) <= 2),
        "開催3日目以降": (di is not None and int(di) >= 3),
        "7M1 が2点": (n_m1 == 2),
        "7M1 が3点以上": (n_m1 >= 3),
    }


def _rate(acc: Acc, keys: list[str], mult: int) -> float:
    if not keys:
        return 0.0
    return 100 * sum(1 for k in keys
                     if acc.per_race[k][1] >= mult * acc.per_race[k][0]
                     and acc.per_race[k][1] > 0) / len(keys)


def _measure(keys: list[str], rows: dict) -> tuple[Acc, Acc]:
    s, m = Acc(), Acc()
    for k in keys:
        s.add(k, rows[k]["RANK_7S"]["bet"], rows[k]["RANK_7S"]["pay"])
        m.add(k, rows[k]["RANK_7M1"]["bet"], rows[k]["RANK_7M1"]["pay"])
    return s, m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d1", default="2025-01-01")
    ap.add_argument("--to", dest="d2", default="2026-08-24")
    ap.add_argument("--min-n", type=int, default=60)
    ap.add_argument("--mult", type=int, default=2,
                    help="主指標の倍率（2=2倍以上で的中 / 5=5倍以上）")
    args = ap.parse_args()

    rows, _meta0 = load(args.d1, args.d2)
    overlap = sorted(k for k, v in rows.items() if len(v) == 2)
    feats = race_features(overlap)

    win = {"探索2025": [k for k in overlap if rows[k]["RANK_7S"]["date"].startswith("2025")],
           "確認2026": [k for k in overlap if rows[k]["RANK_7S"]["date"].startswith("2026")]}
    print(f"競合 {len(overlap)}R  （探索2025 {len(win['探索2025'])} / 確認2026 {len(win['確認2026'])}）")

    # 全体（基準線）
    print("\n===== 競合全体 =====")
    print(f"  {'窓':<10}{'n':>6}{f'7S {args.mult}倍+%':>11}{f'7M1 {args.mult}倍+%':>11}{'差pt':>8}"
          f"{'[95%CI]':>18}{'7S ROI':>9}{'7M1 ROI':>9}")
    for wname, keys in win.items():
        s, m = _measure(keys, rows)
        a, b = _rate(s, keys, args.mult), _rate(m, keys, args.mult)
        lo, hi = boot_rate(s, m, keys, f"x{args.mult}")
        print(f"  {wname:<10}{len(keys):>6}{a:>11.2f}{b:>11.2f}{b-a:>+8.2f}"
              f"{f'[{lo:+.2f},{hi:+.2f}]':>18}"
              f"{100*s.pay/s.bet:>9.1f}{100*m.pay/m.bet:>9.1f}")

    names = list(_segments({}, 0))
    print(f"\n===== レースの型で層別（{len(names)}通りを試す＝多重比較に注意）=====")
    print(f"  {'セグメント':<28}{'探索n':>6}{'差pt':>8}{'[95%CI]':>18}"
          f"{'確認n':>6}{'差pt':>8}{'[95%CI]':>18}  判定")
    hits = []
    for name in names:
        cells = {}
        for wname, keys in win.items():
            sub = [k for k in keys
                   if _segments(feats.get(k, {}), rows[k]["RANK_7M1"]["n"] or 0)[name]]
            if len(sub) < args.min_n:
                cells[wname] = None
                continue
            s, m = _measure(sub, rows)
            d = _rate(m, sub, args.mult) - _rate(s, sub, args.mult)
            cells[wname] = (len(sub), d, *boot_rate(s, m, sub, f"x{args.mult}"))
        e, c = cells["探索2025"], cells["確認2026"]
        if e is None or c is None:
            note = "件数不足"
            es = f"{(e[0] if e else 0):>6}" + " " * 26
            cs = f"{(c[0] if c else 0):>6}" + " " * 26
        else:
            ok = e[1] > 0 and c[1] > 0
            note = "🟢 両窓で 7M1 が上" if ok else ("—" if e[1] * c[1] > 0 else "符号が反転")
            if ok:
                hits.append(name)
            es = f"{e[0]:>6}{e[1]:>+8.2f}{f'[{e[2]:+.2f},{e[3]:+.2f}]':>18}"
            cs = f"{c[0]:>6}{c[1]:>+8.2f}{f'[{c[2]:+.2f},{c[3]:+.2f}]':>18}"
        print(f"  {name:<28}{es}{cs}  {note}")

    print(f"\n両窓で 7M1 が上回ったセグメント: {hits if hits else 'なし'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
