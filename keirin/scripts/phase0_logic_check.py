#!/usr/bin/env python3
"""Phase 0 の **0a「論理の一致」** を測る（2026-08-23・§34）。**書き込みはしない**。

## なぜ 0a が要るのか

§33 で、事前登録の完了条件「現行の優先順位方式を再現して**現行実績と一致**」は
字義どおりには達成できないと分かった。プール(`picks_history`) は**月次 vintage**、
入稿は**本番モデル**（full_refit）で判定しており、p3 が違えばゲートの通過も違う
（実装バグではなく設計上の必然）。

そこで完了条件を2つに分ける:

    0a 論理の一致 : **同じ p3 を入れたとき**プール側のゲートが入稿側と同じ判定を出すか
    0b 被覆       : 事前登録の両窓で全ランクにプール行があるか

本スクリプトは **0a** を測る。各ランクの `build_rows()` を
**本番モデルで**呼び、`RANK_ORDER` と `enabled` を当てて実入稿と突き合わせる。
ここが合えば「ゲートの実装は正しい。残る差はモデル版だけ」と言い切れる。

## 🔴 本番モデルを過去へ当てる件

`assert_vintage_for_past()` が本番モデル名を弾くので
`KEIRIN_ALLOW_PRODUCTION_MODELS_FOR_PAST=1` を立てて呼ぶ。
**これは調査専用**であり、ここで出た的中率・ROI は in-sample なので
**成績の評価には絶対に使わない**（本スクリプトも成績は一切出さない）。

## 🔴 安全性

- 各 backfill モジュールの **`build_rows()` だけ**を呼ぶ。`wipe_rows()` /
  `insert_rows()` / `main()` は呼ばない（書き込みはそこにしかない・実測で確認済み）
- 比較対象の `netkeirin_submissions` も SELECT のみ
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

# 🔴 import より前に立てる。モジュール読み込み時に検査が走る実装があるため。
os.environ.setdefault("KEIRIN_ALLOW_PRODUCTION_MODELS_FOR_PAST", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.phase0_pool_audit import enabled_ranks, load_rank_order  # noqa: E402
from src.database import get_connection  # noqa: E402

EVAL, WIN, BAD = "lgbm_wt_eval", "lgbm_wt_win", "lgbm_wt_bad"
FAVBUST = "lgbm_wt_favbust"


def _rows_positional(mod, d_from, d_to):
    return mod.build_rows(EVAL, d_from, d_to, WIN, BAD)


def _rows_kw(mod, d_from, d_to):
    return mod.build_rows(d_from, d_to, eval_model=EVAL, win_model=WIN)


def _rows_7h1(mod, d_from, d_to):
    return mod.build_rows(d_from, d_to, eval_model=EVAL, win_model=WIN,
                          bad_model=BAD, favbust_model=FAVBUST)


# {ランク: (モジュール名, 呼び出し方)}。**7車ランクだけ**（9車は別母集団）。
BUILDERS = {
    "7H2": ("backfill_7h2_rank_wt", _rows_kw),
    "7S":  ("backfill_7s_merged_rank_wt", _rows_positional),
    "7B":  ("backfill_7b_rank_wt", _rows_positional),
    "7C":  ("backfill_7c_rank_wt", _rows_positional),
    "7T1": ("backfill_7t1_rank_wt", _rows_kw),
    "7H1": ("backfill_7h1_rank_wt", _rows_7h1),
    "7M1": ("backfill_7m1_rank_wt", _rows_positional),
}


def build_pool(d_from: str, d_to: str, ranks: list[str]) -> dict[str, set[str]]:
    """本番モデルでプールを組む。{レースキー: {ランク}}。"""
    import importlib
    pool: dict[str, set[str]] = defaultdict(set)
    for r in ranks:
        if r not in BUILDERS:
            continue
        mod_name, call = BUILDERS[r]
        try:
            mod = importlib.import_module(f"scripts.{mod_name}")
            rows = call(mod, d_from, d_to)
        except Exception as e:                      # noqa: BLE001
            print(f"  {r:>4}: 🔴 build_rows が失敗 — {type(e).__name__}: {e}")
            continue
        keys = {str(x["race_key"]).split("#")[0] for x in rows}
        for k in keys:
            pool[k].add(r)
        print(f"  {r:>4}: {len(rows):,} 行 / {len(keys):,} レース")
    return pool


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from", default="2026-08-15")
    ap.add_argument("--to", dest="d_to", default="2026-08-22")
    args = ap.parse_args()

    order = load_rank_order()
    with get_connection() as conn:
        off = enabled_ranks(conn)
        en = [r for r in order if r not in off and r in BUILDERS]
        print(f"実効の優先順位（7車のみ）: {' > '.join(en)}")
        print("⚠️ 本番モデルで過去を採点している（0a 専用・成績には使わない）\n")

        print(f"===== プール構築（{args.d_from}〜{args.d_to}・本番モデル）=====")
        pool = build_pool(args.d_from, args.d_to, en)
        print(f"  → プール {len(pool):,} レース\n")

        subs = conn.execute(
            "SELECT race_key, rank_key FROM keirin.netkeirin_submissions "
            "WHERE race_key >= ? AND race_key < ? AND deleted_at IS NULL "
            "AND origin = 'rank'",
            (args.d_from.replace("-", ""),
             str(int(args.d_to.replace("-", "")) + 1))).fetchall()
        subs = [x for x in subs if x["rank_key"] in BUILDERS]
        match = 0
        why, by_rank = Counter(), Counter()
        for x in subs:
            have = pool.get(x["race_key"], set())
            pred = next((r for r in en if r in have), None)
            if pred == x["rank_key"]:
                match += 1
            elif x["rank_key"] not in have:
                # 🔴 「レースごと処理されていない」のか「処理はされたがゲートが
                #    否と言った」のかで原因がまったく違う。必ず分けて数える。
                lab = ("A1 レースがプールに1行も無い" if not have
                       else "A2 レースは在るがそのランクのゲートが通らない")
                why[lab] += 1
                by_rank[f'{x["rank_key"]}/{lab[:2]}'] += 1
            else:
                why["B 上位が居るのに下位が売れた（波・承認待ち）"] += 1
        tot = len(subs)
        print(f"===== 0a 判定（7車・origin='rank' {tot:,} 件）=====")
        print(f"  一致 {match:,} 件 ({match / max(tot, 1):.1%})")
        for k, v in why.most_common():
            print(f"    {v:>5} 件  {k}")
        if by_rank:
            print("    A の内訳: " + " / ".join(f"{k} {v}" for k, v in
                                                by_rank.most_common()))
        ok = tot and match / tot >= 0.99
        print(f"\n  → 0a（論理の一致）: {'✅ 満たす' if ok else '❌ 満たさない'}")
        if not ok:
            print("  🔴 モデル版を揃えても合わない＝**ゲートの実装が入稿側と違う**。"
                  "残差を1件ずつ潰すまで Phase 1 へ進まないこと。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
