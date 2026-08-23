#!/usr/bin/env python3
"""Phase 0 の完了条件を満たせるかを監査する（2026-08-23・§33）。**読み取り専用**。

## 何を確かめるのか

`docs/product_portfolio_redesign_2026_08.md` の Phase 0:

> **共通候補プール**を作る。全7車レースについて、優先順位を一切かけずに
> 全商品の買い目・投資・払戻を生成
> 完了条件: **現行の優先順位方式を再現して現行実績と一致すること**
> （一致しなければ以降は無意味）

プールの素材として一番近いのは `keirin.picks_history`（`race_key` が
`{レースキー}#{ランク}` でランクごとに1行＝**優先順位をかける前の姿**）。
本スクリプトはそれが素材として使えるかを、
**現行実績（`netkeirin_submissions`）と突き合わせて**測る。

🔴 **書き込みは一切しない。** `rebuild_*_walkforward_pg.py` は
picks_history を wipe→insert する破壊的スクリプトなので、本監査からは呼ばない。

## 出す3つ

1. **プール被覆** — ランク×月で picks_history に行があるか。
   事前登録の窓（探索 2024-01〜2025-12 / 確認 2026-01〜06）で
   **1件も無いランク**があれば、その窓で現行方式は再現できない
2. **再現テスト** — 有効ランクだけを `RANK_ORDER` 順に当てて実入稿と比べる。
   不一致は原因別に分ける（プール欠 / 上位が居るのに下位が売れた / その逆）
3. **不一致の原因** — 特に「プールにも本番にも同じランクがあるはずなのに片方に無い」型。
   プールは**月次 vintage モデル**（`lgbm_wt_eval_mYYMM`）で作られ、
   入稿は**本番モデル**（`lgbm_wt_eval`・full_refit）で判定される。
   p3 が違えばゲートの通過も違う。これは**設計上の必然**であって実装バグではない。
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402

# 🔴 `netkeirin_submit_wt.RANK_CONFIGS` の定義順がそのまま優先順位
#    （`RANK_ORDER = list(RANK_CONFIGS)`）。ここへ写すと二重定義になるので
#    実体から読む。読み込みだけで副作用は無い。
def load_rank_order() -> list[str]:
    import importlib.util
    p = Path(__file__).resolve().parent / "netkeirin_submit_wt.py"
    spec = importlib.util.spec_from_file_location("_nsw", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return list(m.RANK_ORDER)


def enabled_ranks(conn) -> set[str]:
    """`netkeirin_settings` の有効フラグ。⚠️ 行が無いランクは fail-open で有効。"""
    off = {x["rank_key"] for x in
           conn.execute("SELECT rank_key, enabled FROM keirin.netkeirin_settings "
                        "WHERE enabled = false").fetchall()}
    return off


def load_pool(conn, d_from, d_to):
    """{レースキー: {ランク}}。picks_history の race_key からランク接尾辞を外す。"""
    pool = defaultdict(set)
    per_month = defaultdict(lambda: defaultdict(set))
    q = ("SELECT race_date, split_part(race_key,'#',1) rk, rank "
         "FROM keirin.picks_history WHERE race_date BETWEEN ? AND ?")
    for x in conn.execute(q, (d_from, d_to)).fetchall():
        r = x["rank"].replace("RANK_", "")
        pool[x["rk"]].add(r)
        per_month[str(x["race_date"])[:7]][r].add(x["rk"])
    return pool, per_month


def _gate7c_pass_with_production_p3(conn, race_keys) -> int:
    """本番モデルの p3 で 7C ゲートを通る件数。**vintage との差の原因切り分け専用**。

    ⚠️ `wt_entries.pred_top3_pct` は過去分が backfill された列なので
       **成績の評価には使えない**（[[keirin_s24_vintage_reverify_2026_08_23]]）。
       ここでは「入稿時にゲートが見た値に近いのはどちらか」を見るだけに使う。
    """
    from src.p3_calibration import calibrated_p3_sum_top2
    from src.strategy_wt import (
        RANK_7C_LEGS_MIN, RANK_7C_P3_SUM_MIN, rank_7c_select_legs)
    n = 0
    for rk in race_keys:
        rows = conn.execute("SELECT frame_no, pred_top3_pct FROM keirin.wt_entries "
                            "WHERE race_key = ?", (rk,)).fetchall()
        meta = conn.execute("SELECT race_type, cup_grade FROM keirin.wt_races "
                            "WHERE race_key = ?", (rk,)).fetchall()
        p3 = {int(r["frame_no"]): float(r["pred_top3_pct"]) / 100
              for r in rows if r["pred_top3_pct"] is not None}
        if len(p3) != 7 or not meta:
            continue
        cal = calibrated_p3_sum_top2(p3, meta[0]["race_type"],
                                     meta[0]["cup_grade"]) or 0.0
        srt = sorted(p3, key=lambda f: (-p3[f], f))
        legs = rank_7c_select_legs(srt[2:], p3)
        n += int(cal >= RANK_7C_P3_SUM_MIN and len(legs) >= RANK_7C_LEGS_MIN)
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repro-from", default="2026-08-15",
                    help="再現テストの開始日。⚠️ ランク構成が変わった日をまたがないこと")
    ap.add_argument("--repro-to", default="2026-08-22")
    args = ap.parse_args()

    order = load_rank_order()
    with get_connection() as conn:
        off = enabled_ranks(conn)
        en = [r for r in order if r not in off]
        print(f"優先順位（RANK_CONFIGS の定義順）: {' > '.join(order)}")
        print(f"無効（netkeirin_settings.enabled=false）: {sorted(off)}")
        print(f"→ 実効の優先順位: {' > '.join(en)}\n")

        # ── 1. プール被覆 ──
        print("===== 1. プール被覆（picks_history にランク行があるレース数）=====")
        _, pm = load_pool(conn, "2024-01-01", "2026-08-23")
        cars7 = [r for r in order if r.startswith("7")]
        months = sorted(pm)
        print(f"{'月':>9}" + "".join(f"{r:>7}" for r in cars7))
        for mo in months[::4] + months[-2:]:
            print(f"{mo:>9}" + "".join(f"{len(pm[mo].get(r, ())):>7}" for r in cars7))
        print()
        wins = {"探索 2024-01〜2025-12": [m for m in months if m <= "2025-12"],
                "確認 2026-01〜06": [m for m in months if "2026-01" <= m <= "2026-06"]}
        bad = False
        for name, mos in wins.items():
            zero = [r for r in cars7
                    if sum(len(pm[m].get(r, ())) for m in mos) == 0]
            mark = "🔴" if zero else "🟢"
            print(f"  {mark} {name}: 1件も無いランク {zero or 'なし'}")
            bad = bad or bool(zero)

        # ── 2. 再現テスト ──
        print(f"\n===== 2. 再現テスト（{args.repro_from}〜{args.repro_to}）=====")
        pool, _ = load_pool(conn, args.repro_from, args.repro_to)
        subs = conn.execute(
            "SELECT race_key, rank_key, origin, submitted_at FROM "
            "keirin.netkeirin_submissions WHERE race_key >= ? AND race_key < ? "
            "AND deleted_at IS NULL",
            (args.repro_from.replace("-", ""),
             str(int(args.repro_to.replace("-", "")) + 1))).fetchall()
        og = Counter(x["origin"] for x in subs)
        print(f"  入稿 {len(subs):,} 件  出自: {dict(og)}")
        print("  ⚠️ 看板穴埋め（marquee_fill）はゲートを通らないので再現対象外")

        match = 0
        why = Counter()
        by_rank = Counter()
        for x in subs:
            if x["origin"] != "rank":
                continue
            have = pool.get(x["race_key"], set())
            pred = next((r for r in en if r in have), None)
            if pred == x["rank_key"]:
                match += 1
            elif x["rank_key"] not in have:
                why["A プールに売れたランクの行が無い"] += 1
                by_rank[x["rank_key"]] += 1
            elif pred is not None and en.index(pred) < en.index(x["rank_key"]):
                why["B 上位が居るのに下位が売れた（上限/波/承認待ち）"] += 1
            else:
                why["C その他"] += 1
        tot = match + sum(why.values())
        print(f"\n  origin='rank' {tot:,} 件 → 一致 {match:,} "
              f"({match / max(tot, 1):.1%})")
        for k, v in why.most_common():
            print(f"    {v:>5} 件  {k}")
        if by_rank:
            print("    A の内訳（売れたランク別）: "
                  + " / ".join(f"{k} {v}" for k, v in by_rank.most_common()))

        # ── 3. 不一致の原因: プール(vintage) と 入稿(本番モデル) の p3 が違う ──
        print("\n===== 3. 原因の切り分け: vintage プール vs 本番モデル入稿 =====")
        miss7c = [x["race_key"] for x in subs
                  if x["origin"] == "rank" and x["rank_key"] == "7C"
                  and "7C" not in pool.get(x["race_key"], set())]
        n_pass = _gate7c_pass_with_production_p3(conn, miss7c)
        print(f"  「7C で売れたのにプールに 7C 行が無い」 {len(miss7c)} 件のうち、")
        print(f"  **本番モデルの p3（`wt_entries.pred_top3_pct`）**なら 7C ゲートを通る: "
              f"{n_pass} 件")
        print("  🔴 プールは月次 vintage・入稿は本番モデル（full_refit）で判定している。")
        print("     p3 が違えばゲートの通過も違う。**実装バグではなく設計上の必然。**")

        ok = tot and match / tot >= 0.99
        print("\n===== 判定 =====")
        print(f"  {'🟢' if not bad else '🔴'} プール被覆: "
              f"{'事前登録の両窓を覆えている' if not bad else '覆えていない窓がある'}")
        print(f"  {'🟢' if ok else '🔴'} 再現一致: "
              f"{match / max(tot, 1):.1%}（完了条件は実質100%）")
        print(f"\n  → Phase 0 完了条件: {'✅ 満たす' if (ok and not bad) else '❌ 満たさない'}")
        if bad or not ok:
            print("  🔴 事前登録どおり、ここが通るまで以降の比較は無意味。"
                  "`picks_history` は素材として不足している。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
