#!/usr/bin/env python3
"""Phase 0 突き合わせ: 7S の `build_rows()` と**入稿が実際に読んだ候補JSON**を比べる。

（2026-08-23・§35）**書き込みはしない。**

## なぜ

§34 で、入稿は `cli/main.py` が朝に書いた候補JSONを読み、
`backfill_*_rank_wt.build_rows()` は後からDBを組み直す＝**別経路**だと分かった。
Phase 0 はこの2つを一致させるところから始まる。正解は**保存済み候補JSON**。

    VPS: ~/GitHub/kiseki/keirin/data/picks/wave_picks_wt_{日付}[_night]_{key}_candidates.json

7S は旧 7S / 7A / 7SS の統合なので、**サブランクごとに**比べて差分の位置を特定する
（統合後だけ見ると、どのゲートがずれているのか分からない）。

## 使い方

    # 1) VPS から候補JSONを取る（読み取りのみ）
    ssh sekito "cd ~/GitHub/kiseki/keirin/data/picks && tar cf - wave_picks_wt_2026-08-*_s7*_candidates.json" \
      | tar xf - -C /tmp/picks
    # 2) 突き合わせ
    PYTHONPATH=. .venv/bin/python scripts/phase0_reconcile_7s.py --picks /tmp/picks \
      --from 2026-08-01 --to 2026-08-22

🔴 本番モデルで過去を採点するので `KEIRIN_ALLOW_PRODUCTION_MODELS_FOR_PAST=1` を
立てる。**これは経路の突き合わせ専用**で、ここから成績を語ってはいけない。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("KEIRIN_ALLOW_PRODUCTION_MODELS_FOR_PAST", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EVAL, WIN, BAD = "lgbm_wt_eval", "lgbm_wt_win", "lgbm_wt_bad"

# 候補JSONの key → backfill モジュール名
SUBS = {
    "s7": "backfill_7s_rank_wt",
    "s7a": "backfill_7a_rank_wt",
    "s7ss": "backfill_7ss_rank_wt",
}
_RE = re.compile(r"wave_picks_wt_(\d{4}-\d{2}-\d{2})(_night)?_(s7|s7a|s7ss)"
                 r"_candidates\.json$")


def load_json_pool(picks_dir: str, d_from: str, d_to: str):
    """{key: {レースキー}}。朝(day)と夜(night)は同じ日の別波なので**足し合わせる**。"""
    out = defaultdict(set)
    for f in sorted(glob.glob(os.path.join(picks_dir, "*.json"))):
        m = _RE.search(os.path.basename(f))
        if not m:
            continue
        d, _night, key = m.groups()
        if not (d_from <= d <= d_to):
            continue
        try:
            recs = json.load(open(f))
        except Exception as e:                       # noqa: BLE001
            print(f"  ⚠️ 読めない: {os.path.basename(f)} — {e}")
            continue
        for r in recs:
            out[key].add(r["race_key"])
    return out


def load_backfill_pool(d_from: str, d_to: str):
    import importlib
    out = {}
    for key, mod_name in SUBS.items():
        mod = importlib.import_module(f"scripts.{mod_name}")
        try:
            rows = mod.build_rows(EVAL, d_from, d_to, WIN, BAD)
        except TypeError:
            rows = mod.build_rows(EVAL, d_from, d_to)
        out[key] = {str(x["race_key"]).split("#")[0] for x in rows}
        print(f"  {key:>5}: build_rows {len(rows):,} 行 / {len(out[key]):,} レース")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--picks", required=True, help="候補JSONを置いたディレクトリ")
    ap.add_argument("--from", dest="d_from", default="2026-08-01")
    ap.add_argument("--to", dest="d_to", default="2026-08-22")
    ap.add_argument("--show", type=int, default=5)
    args = ap.parse_args()

    print(f"===== 正解: 入稿が読んだ候補JSON（{args.d_from}〜{args.d_to}）=====")
    js = load_json_pool(args.picks, args.d_from, args.d_to)
    for k in SUBS:
        print(f"  {k:>5}: {len(js.get(k, ())):,} レース")

    print("\n===== 比較対象: backfill の build_rows（本番モデル）=====")
    bf = load_backfill_pool(args.d_from, args.d_to)

    print("\n===== 突き合わせ =====")
    print(f"{'key':>6}{'JSON':>7}{'backfill':>10}{'一致':>7}"
          f"{'JSONのみ':>10}{'backfillのみ':>13}{'一致率':>8}")
    total_only_json = []
    for k in SUBS:
        a, b = js.get(k, set()), bf.get(k, set())
        both = a & b
        rate = len(both) / max(len(a | b), 1)
        print(f"{k:>6}{len(a):>7}{len(b):>10}{len(both):>7}"
              f"{len(a - b):>10}{len(b - a):>13}{rate:>8.1%}")
        total_only_json += [(k, x) for x in sorted(a - b)]

    print("\n🔴 **JSON にあって backfill に無い**＝入稿されたのに再構築が再現できない分")
    for k, x in total_only_json[:args.show * 3]:
        print(f"    {k:>5} {x}")
    if len(total_only_json) > args.show * 3:
        print(f"    …ほか {len(total_only_json) - args.show * 3} 件")

    ja = set().union(*js.values()) if js else set()
    ba = set().union(*bf.values()) if bf else set()
    print(f"\n  統合後（7S 全体）: JSON {len(ja):,} / backfill {len(ba):,} / "
          f"一致 {len(ja & ba):,} ({len(ja & ba) / max(len(ja | ba), 1):.1%})")
    print("\n  → 一致率 100% になるまで Phase 0 は完了しない。"
          "差分レースを1件ずつ `cli/main.py` と突き合わせること。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
