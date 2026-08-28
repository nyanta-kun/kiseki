#!/usr/bin/env python3
"""勝負アイコン「自信あり」を付ける1レースを選ぶ（2026-08-13 新設）。

netkeirin の「自信あり」は **1日に1つしか付けられない**。従来は 7SS の入稿すべてに
付けており、7SS が複数出た日は**先に入稿したものが取っていた**（選定ではなかった）。

ユーザー決定（2026-08-13）:
**朝の時点で当日全レースを見て、期待値が最も高い1レースだけに付ける。**

## 期待値

    EV = Σ(的中確率 × 賭け金 × オッズ) ÷ 総賭け金

- オッズは**予測オッズ**（`src.odds_prediction`）。朝の板は夜開催で 63.4% が
  未確定なので、板で比べると**朝に開催がある場だけが有利**になる。
  終日を同じ土俵に載せるために予測で統一する。
- 的中確率は Plackett-Luce の三連複確率。
- **三連複の買い目だけ**が対象（三連単は着順つきでこの確率モデルに載らない）。

計算の実体は `src/confident_pick.py`。

## 🔴 型ラボ（2026-08-28〜）は別の尺度で選ぶ

ユーザー決定（2026-08-28）:
**「20,000円以上の払い戻しになりそうで、最も的中率が高そうなレース」**。

    候補 … `type_lab_picks.pred_min_payout >= 20,000`
           （＝**どの目が当たっても** 2万円以上。「平均」では約束にならない）
    順位 … Σp（買い目の的中確率の合計）が最大のもの

- 確率は `type_lab_picks.legs[].prob` を**そのまま使う**（モデルを引き直さない）
- 実測（両窓）: Σp 五分位の表示的中は 探索 7.26 → 25.56% / 確認 6.33 → 27.06% と
  対応する。選ばれた1レースの表示的中は **探索 28.8% / 確認 34.9%**
  （全体は 20.16%）。選定できなかった日は **0日**（365/365・238/238）

🔴 **既存の EV とは尺度が違うので混ぜて max を取らない。**
   型ラボの入稿が1件でもあればそちらだけで選ぶ。

🔴 **移行後に旧 EV 経路をそのまま使ってはいけない。** `race_expected_value` は
   **三連複の買い目しか対象にしない**（`bet_type != "3連複"` を全て None にする）。
   型ラボ6プランのうち三連複は `D_hit` だけなので、旧経路のままだと
   「自信あり」が **D_hit にしか付かない**（1日3.6件からの選定になる）。

## いつ走らせるか

朝の日次バッチの**入稿のあと**に1回。昼・夕の波では走らせない
（当日2回目を選ぶと1日1件が壊れる）。

🔴 **型ラボでは `type_lab_daily.sh` から呼ぶ。** `daily_picks_wt.sh`（07:00）は
   型ラボの生成・入稿（07:15）より**前**に走るので、そこで呼んでも
   当日の型ラボの入稿はまだ1件も無い。

## 冪等性

実行のたびに **その日を全部 false にしてから1件だけ true** にする。
途中で落ちてもやり直せる。同値のときは race_key → rank_key で決めるので
何度走らせても同じ結果になる。

## 使い方

    PYTHONPATH=. .venv/bin/python scripts/pick_confident_race_wt.py [YYYY-MM-DD] [--dry-run]

DB は netkeirin_submissions の is_confident / confident_ev のみ更新する。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.confident_pick import (  # noqa: E402
    TYPE_LAB_MIN_PAYOUT,
    pick_best,
    race_expected_value,
    type_lab_hit_probability,
)
from src.database import get_connection  # noqa: E402
from src.type_lab import SELL_PLANS  # noqa: E402

# 取消済みは対象外（人が落としたものに自信アイコンを置かない）。
_ALIVE = "COALESCE(status, 'submitted') <> 'deleted'"


def _load_alive(date: str) -> list[dict]:
    ymd = date.replace("-", "")
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT race_key, rank_key, venue_name, race_no, bet_detail "
            f"FROM netkeirin_submissions WHERE race_key LIKE ? AND {_ALIVE} "
            "ORDER BY race_key, rank_key",
            (f"{ymd}%",),
        ).fetchall()
    return [dict(r) for r in rows]


def _load_type_lab(date: str) -> list[dict]:
    """その日の**型ラボの入稿**（買い目と想定払戻つき）。

    `netkeirin_submissions.rank_key` は型ラボのプラン名そのものなので、
    `type_lab_picks.plan_key` と直接つながる（結合キーを別に持たない）。
    """
    ymd = date.replace("-", "")
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT s.race_key, s.rank_key, s.venue_name, s.race_no,"
            "       t.legs, t.pred_min_payout "
            "FROM netkeirin_submissions s "
            "JOIN type_lab_picks t "
            "  ON t.race_key = s.race_key AND t.plan_key = s.rank_key "
            f"WHERE s.race_key LIKE ? AND {_ALIVE} AND t.mode IN (?, ?) "
            "ORDER BY s.race_key, s.rank_key",
            (f"{ymd}%", "live", "live9"),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d["rank_key"] not in SELL_PLANS:
            continue
        d["legs"] = json.loads(d["legs"]) if isinstance(d["legs"], str) else (d["legs"] or [])
        out.append(d)
    return out


def pick(date: str, dry_run: bool = False) -> tuple[str, str] | None:
    """その日の「自信あり」を1件決めて記録する。決められなければ None。"""
    # 🔴 **型ラボが1件でもあればそちらで選ぶ。** 尺度（EV ↔ Σp）が違うものを
    #    一緒に並べて max を取ってはいけない。全面置換後は既存ランクが出ないので
    #    通常は型ラボだけになるが、移行期・ロールバック中は両方が並びうる。
    tl_rows = _load_type_lab(date)
    if tl_rows:
        rows, metric = tl_rows, "Σp"
        scored = [(r["race_key"], r["rank_key"],
                   type_lab_hit_probability(r["legs"], r["pred_min_payout"]))
                  for r in rows]
    else:
        rows, metric = _load_alive(date), "EV"
        scored = [(r["race_key"], r["rank_key"],
                   race_expected_value(r["race_key"], r.get("bet_detail")))
                  for r in rows]
    if not rows:
        print(f"[confident] {date}: 生きている入稿がありません", flush=True)
        return None

    usable = [(rk, rank, v) for rk, rank, v in scored if v is not None]
    print(f"[confident] {date}: 対象 {len(rows)}件 / {metric}算出 {len(usable)}件"
          + (f"（型ラボ・最低想定払戻 {TYPE_LAB_MIN_PAYOUT:,}円以上に限る）"
             if metric == "Σp" else ""), flush=True)
    label = {(r["race_key"], r["rank_key"]):
             f"{r['venue_name']}{r['race_no']}R({r['rank_key']})" for r in rows}
    for rk, rank, v in sorted(usable, key=lambda t: -t[2])[:10]:
        print(f"    {metric}={v:.3f}  {label.get((rk, rank), rk)}", flush=True)

    best = pick_best(scored)
    if best is None:
        # 🔴 **黙って終わらない。** 全件算出できないのは予測モデル未配備や
        #    「最低想定払戻が2万円に届く商品が1つも無い」といった異常。
        print(f"[confident] {date}: {metric} を出せる入稿が1件も無く、自信アイコンは付けません",
              flush=True)
        return None
    race_key, rank_key = best
    print(f"[confident] {date}: 自信あり → {label.get(best, race_key)}", flush=True)
    if dry_run:
        print("[confident] dry-run のため DB は更新しません", flush=True)
        return best

    ymd = date.replace("-", "")
    with get_connection() as conn:
        # 🔴 **先に当日を全部 false にする**。1日1件はこの2文で担保している。
        conn.execute(
            "UPDATE netkeirin_submissions SET is_confident = FALSE "
            "WHERE race_key LIKE ?", (f"{ymd}%",))
        conn.execute(
            "UPDATE netkeirin_submissions SET is_confident = TRUE "
            "WHERE race_key = ? AND rank_key = ?", (race_key, rank_key))
        # 選定に使った値を全件に残す。**確認画面はこの値を出す**ので、
        # 「なぜこのレースが選ばれたか」を後から読める。
        # ⚠️ 列名は `confident_ev` のままだが、型ラボのときの中身は **Σp**
        #    （買い目の的中確率の合計）。尺度が違うので**日をまたいで比べない**。
        for rk, rank, ev in scored:
            conn.execute(
                "UPDATE netkeirin_submissions SET confident_ev = ? "
                "WHERE race_key = ? AND rank_key = ?", (ev, rk, rank))
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("date", nargs="?", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    pick(args.date, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
