#!/usr/bin/env python3
"""C-6 + N-1: 7S のゲートを 66特徴の指数の上で測り直す（2026-08-21）。

## 何を一度に解くか

- **N-1** `axis_sum` だけレース内合計で割っていない（`entropy` と `_honmei_share`
  は割っている）。`strategy_wt.py` の「`pred_top3_pct` はレース内合計が 3.0 に
  正規化される」というコメントは**事実と違う**。正規化するとゲート判定が
  入れ替わるので、**C-6 の掃引の前提そのものが変わる**。同時に扱う。
- **C-6** `RANK_7S_AXIS_SUM_MAX` を厳しくする掃引。前回（2026-08-19・60特徴）は
  「ROI +3.1pt だが件数 5.41→0.98/日（1/5.5）」＝**件数交絡で判定不能**だった。

## 🔴 前回できなかったことを2つ直す

1. **連続値の axis_sum で掃引する。** `picks_history.pred_combo` の記録値は
   小数第1位（実測7値・1.3/1.4 で76%）で掃引に耐えない。ただし **pred_combo には
   軸2車そのもの（`5=3-...`）が入っている**ので、軸を読み取って
   `wt_entries.pred_top3_pct`（**月次凍結 vintage でバックフィルされた我々の指数**・
   リークなし）から連続値で組み直せる。`pred_bad` は不要（軸は既に確定している）。
2. **件数を揃えて比べる。** 「厳しくする」は必ず件数が減るので、
   [[keirin_race_selection_meta_2026_08_18]]「改善の正体は少なく賭けただけ」に
   なる。**同じ件数をランダムに残した帰無**（`null_same_count`）と比較する。
   これは [[keirin_n15_upset_routing_rejected_2026_08_21]] で混合比に対して
   使ったのと同じ道具。

## 🔴 軸は60特徴時代・確率は66特徴 vintage という食い違いがある

記録された軸は当時（60特徴）のモデルが選んだもので、確率は 08-20 に 66特徴で
入れ直した vintage。**厳密には本番の再現ではない**。再現度は「記録値(1桁)と
再計算値の丸めが一致する率」で出すので、低ければこの掃引は信用しないこと。

## 掃引窓 / 確認窓

掃引 2025-01〜2025-12 / 確認 2026-01〜2026-08-20。閾値は掃引窓で選び確認窓で判定。

DB は読み取りのみ。
"""
from __future__ import annotations

import argparse
import math
import pickle
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection  # noqa: E402
from src.preprocessing.upset_features import (  # noqa: E402
    build_upset_row, feature_vector,
)

SWEEP = ("2025-01-01", "2025-12-31")
CONFIRM = ("2026-01-01", "2026-08-20")
AXIS_RE = re.compile(r"^\s*(\d+)\s*=\s*(\d+)")
SUM_RE = re.compile(r"axis_sum=([0-9.]+)")


def load(upset_model) -> list[dict]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT split_part(race_key,'#',1) rk, race_date, pred_combo, "
            "       bet_amount, payout "
            "FROM picks_history WHERE rank='RANK_7S' AND bet_amount>0 "
            "  AND race_date BETWEEN ? AND ? AND pred_combo LIKE '%axis_sum%'",
            (SWEEP[0], CONFIRM[1]))
        picks = {}
        for r in cur:
            m, s = AXIS_RE.match(r["pred_combo"] or ""), SUM_RE.search(r["pred_combo"] or "")
            if not m or not s or not r["bet_amount"]:
                continue
            picks[r["rk"]] = dict(
                date=r["race_date"], a1=int(m.group(1)), a2=int(m.group(2)),
                rec=float(s.group(1)), bet=int(r["bet_amount"]),
                pay=int(r["payout"] or 0))
        keys = list(picks)

        cur.execute(
            "SELECT race_key, frame_no, pred_top3_pct FROM wt_entries "
            "WHERE race_key = ANY(?) AND pred_top3_pct IS NOT NULL", (keys,))
        p3: dict[str, dict[int, float]] = defaultdict(dict)
        for e in cur:
            p3[e["race_key"]][int(e["frame_no"])] = float(e["pred_top3_pct"])

        cur.execute("""
            SELECT e.race_key, r.n_entries, r.grade, r.race_type, r.day_index,
                   r.distance, r.start_at, e.frame_no, e.race_point, e.line_group,
                   e.line_size, e.style, e.player_class, e.s_count, e.b_count,
                   e.first_rate, e.third_rate, e.prediction_mark, e.finish_order,
                   v.bank_length, v.is_indoor
            FROM wt_entries e JOIN wt_races r USING(race_key)
            LEFT JOIN venue_info v ON v.venue_code=r.venue_id
            WHERE r.cancel=0 AND e.race_key = ANY(?)""", (keys,))
        ents: dict[str, list[dict]] = defaultdict(list)
        for e in cur:
            ents[e["race_key"]].append(dict(e))

    rows = []
    for rk, v in picks.items():
        probs = p3.get(rk)
        if not probs or v["a1"] not in probs or v["a2"] not in probs:
            continue
        total = sum(probs.values())
        if total <= 0:
            continue
        raw = (probs[v["a1"]] + probs[v["a2"]]) / 100.0     # pct → 0-1 (合計≈3)
        norm = (probs[v["a1"]] + probs[v["a2"]]) / total * 3.0   # 合計3.0へ揃える
        ent = -sum((p / total) * math.log(max(p / total, 1e-9)) for p in probs.values())
        up = None
        es = ents.get(rk)
        if es:
            race = {k: es[0].get(k) for k in
                    ("n_entries", "grade", "race_type", "day_index", "distance",
                     "start_at", "bank_length", "is_indoor")}
            f = build_upset_row(es, race)
            if f is not None:
                up = float(upset_model.predict(
                    np.array([feature_vector(f)], dtype=float))[0])
        rows.append(dict(rk=rk, date=v["date"], rec=v["rec"], raw=raw, norm=norm,
                         ent=ent, up=up, total=total / 100.0,
                         ratio=v["pay"] / v["bet"]))
    return rows


def kpi(rs: list[dict], x: float) -> float:
    return 100.0 * sum(1 for r in rs if r["ratio"] >= x) / len(rs) if rs else 0.0


def roi(rs: list[dict]) -> float:
    return 100.0 * sum(r["ratio"] for r in rs) / len(rs) if rs else 0.0


def null_same_count(rs: list[dict], k: int, x: float,
                    n_draw: int = 4000, seed: int = 0) -> tuple[float, float]:
    """🔴 同じ件数をランダムに残した帰無。「厳しくすると良くなる」の大半はこれ。"""
    rng = random.Random(seed)
    vals = [1.0 if r["ratio"] >= x else 0.0 for r in rs]
    draws = []
    for _ in range(n_draw):
        draws.append(100.0 * sum(rng.sample(vals, k)) / k)
    draws.sort()
    return sum(draws) / len(draws), draws


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="lgbm_upset_screen_n15v2412")
    a = ap.parse_args()
    with open(REPO / "data" / "models" / f"{a.model}.pkl", "rb") as f:
        upset = pickle.load(f)

    rows = load(upset)
    print(f"7S {len(rows)}R（軸と連続 axis_sum を再現できたもの）")

    agree = sum(1 for r in rows if abs(round(r["raw"], 1) - r["rec"]) < 1e-9)
    print(f"再現度: 記録値(1桁)と再計算値の丸めが一致 {100*agree/len(rows):.1f}%")

    tot = sorted(r["total"] for r in rows)
    n = len(tot)
    print(f"\n=== N-1: レース内合計 Σpred_top3 は 3.0 に正規化されていない ===")
    print(f"  中央 {tot[n//2]:.4f} / 5%tile {tot[int(.05*n)]:.4f} "
          f"/ 95%tile {tot[int(.95*n)]:.4f} / 幅 {tot[-1]-tot[0]:.4f}")
    flip = sum(1 for r in rows
               if (r["raw"] <= 1.40) != (r["norm"] <= 1.40))
    print(f"  現行ゲート(<=1.40)の判定が正規化で入れ替わる: "
          f"{flip}件 / {len(rows)} = {100*flip/len(rows):.1f}%")

    for tag, (d1, d2) in (("掃引窓 2025", SWEEP), ("確認窓 2026", CONFIRM)):
        sub = [r for r in rows if d1 <= r["date"] <= d2]
        print(f"\n=== {tag}  n={len(sub)}  "
              f"2倍+ {kpi(sub,2):.2f}% / 5倍+ {kpi(sub,5):.2f}% / ROI {roi(sub):.1f}% ===")
        print(f"{'軸':<12}{'残す率':>7}{'n':>6}{'2倍+':>8}{'帰無':>8}{'%点':>7}"
              f"{'5倍+':>8}{'帰無':>8}{'%点':>7}{'ROI':>8}")
        for key, label, low_is_strict in (("raw", "axis_sum 生", True),
                                          ("norm", "axis_sum 正規", True),
                                          ("ent", "entropy", True),
                                          ("up", "波乱スコア", False)):
            cand = [r for r in sub if r[key] is not None]
            if not cand:
                continue
            for frac in (0.75, 0.50, 0.25):
                k = max(int(len(cand) * frac), 20)
                srt = sorted(cand, key=lambda r: r[key] if low_is_strict else -r[key])
                keep = srt[:k]
                out = []
                for x in (2.0, 5.0):
                    mean, draws = null_same_count(cand, k, x)
                    act = kpi(keep, x)
                    pct = 100.0 * sum(1 for d in draws if d < act) / len(draws)
                    out.append((act, mean, pct))
                print(f"{label:<12}{100*frac:>6.0f}%{k:>6}"
                      f"{out[0][0]:>7.2f}%{out[0][1]:>7.2f}%{out[0][2]:>6.1f}%"
                      f"{out[1][0]:>7.2f}%{out[1][1]:>7.2f}%{out[1][2]:>6.1f}%"
                      f"{roi(keep):>7.1f}%")


if __name__ == "__main__":
    main()
