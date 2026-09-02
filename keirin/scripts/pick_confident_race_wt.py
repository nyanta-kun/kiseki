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

## 🔴 型ラボ（2026-09-02〜）は「夕方まで × 合成3倍以上 × EV最大」

ユーザー指示（2026-09-02）:
> **夕方くらいまでのレース**のうち、**合成が3倍以上**で**期待値が最も高い**レース

    候補 … 発走 JST < 18時（`CONFIDENT_BEFORE_HOUR` = `meeting_wave.NIGHT_FROM_HOUR`）
         ∧ 合成オッズ >= 3.0倍（`CONFIDENT_MIN_SYNTH_ODDS`）
    順位 … EV = Σ(確率×賭け金×予測オッズ) ÷ Σ賭け金 が最大

判定は `src.confident_pick.type_lab_confident_score`（唯一の正本）。
**行に焼き付いた `legs` だけで完結する**ので、モデルを再学習しても当時の選定を
再現できるし、三連単のプランも同じ尺度に載る。

⚠️ **これは 2026-08-28 の決定（`pred_min_payout >= 20,000` → Σp 最大）を置き換える。**
   旧ルールの実測（参考）: 選ばれた1レースの表示的中 探索 28.8% / 確認 34.9%
   （全体は 20.16%）。新旧の比較は `scripts/exp_type_lab/confident_rule.py`。

🔴 **`confident_ev` 列の中身は世代で変わる**（旧EV → Σp → EV）。**日をまたいで比べない。**
   2026-09-02 以降は再び EV だが、旧 EV（`race_expected_value`・三連複のみ・盤面を
   引き直す）とは**計算方法も母集団も違う**ので、そちらとも比べないこと。

🔴 **既存ランクとは尺度が違うので混ぜて max を取らない。**
   型ラボの入稿が1件でもあればそちらだけで選ぶ。

🔴 **移行後に旧 EV 経路をそのまま使ってはいけない。** `race_expected_value` は
   **三連複の買い目しか対象にしない**（`bet_type != "3連複"` を全て None にする）。
   型ラボ8プランのうち三連複は `D_hit` / `A_trio` だけなので、旧経路のままだと
   「自信あり」が**その2つにしか付かない**。

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
    CONFIDENT_BEFORE_HOUR,
    CONFIDENT_MIN_SYNTH_ODDS,
    pick_best,
    race_expected_value,
    type_lab_confident_score,
)
from src.database import get_connection  # noqa: E402
from src.type_lab import SELL_PLANS  # noqa: E402

# 取消済みは対象外（人が落としたものに自信アイコンを置かない）。
#
# 🔴 **必ず `s.` で修飾すること。** `wt_races` にも `status` 列があるので、
#    `wt_races` を JOIN したクエリで裸の `status` を書くと PostgreSQL が
#    `AmbiguousColumn` で落ちる（2026-09-02 に発走時刻の JOIN を足して実際に踏んだ）。
#    そのため両方のクエリで `netkeirin_submissions` に `s` の別名を付けている。
_ALIVE = "COALESCE(s.status, 'submitted') <> 'deleted'"


def _load_alive(date: str) -> list[dict]:
    ymd = date.replace("-", "")
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT s.race_key, s.rank_key, s.venue_name, s.race_no, s.bet_detail "
            f"FROM netkeirin_submissions s WHERE s.race_key LIKE ? AND {_ALIVE} "
            "ORDER BY s.race_key, s.rank_key",
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
            # 🔴 発走時刻は `wt_races` からしか取れない（`type_lab_picks` に列が無い）。
            #    INNER JOIN なのは時刻の取れないレースを候補にしないため
            #    （`type_lab_confident_score` の「読めなければ None」と揃える）。
            "SELECT s.race_key, s.rank_key, s.venue_name, s.race_no,"
            "       t.legs, r.start_at "
            "FROM netkeirin_submissions s "
            "JOIN type_lab_picks t "
            "  ON t.race_key = s.race_key AND t.plan_key = s.rank_key "
            "JOIN wt_races r ON r.race_key = s.race_key "
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
    # 🔴 **型ラボが1件でもあればそちらで選ぶ。** 尺度（旧EV ↔ 新EV）が違うものを
    #    一緒に並べて max を取ってはいけない。全面置換後は既存ランクが出ないので
    #    通常は型ラボだけになるが、移行期・ロールバック中は両方が並びうる。
    tl_rows = _load_type_lab(date)
    if tl_rows:
        rows, metric = tl_rows, "EV"
        scored = [(r["race_key"], r["rank_key"],
                   type_lab_confident_score(r["legs"], r["start_at"]))
                  for r in rows]
    else:
        rows, metric = _load_alive(date), "旧EV"
        scored = [(r["race_key"], r["rank_key"],
                   race_expected_value(r["race_key"], r.get("bet_detail")))
                  for r in rows]
    if not rows:
        print(f"[confident] {date}: 生きている入稿がありません", flush=True)
        return None

    usable = [(rk, rank, v) for rk, rank, v in scored if v is not None]
    print(f"[confident] {date}: 対象 {len(rows)}件 / {metric}算出 {len(usable)}件"
          + (f"（型ラボ・発走 {CONFIDENT_BEFORE_HOUR}時前 かつ "
             f"合成 {CONFIDENT_MIN_SYNTH_ODDS}倍以上に限る）"
             if tl_rows else ""), flush=True)
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
        # ⚠️ 列名は `confident_ev`。中身は世代で変わっている（旧EV → Σp → EV）。
        #    いまは EV だが**旧EV とも計算方法が違う**ので、日をまたいで比べない。
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
