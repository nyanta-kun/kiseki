#!/usr/bin/env python3
"""統合後 RANK_7S（旧 7S ∪ 7A ∪ 7SS）の行を組み立てる（2026-08-14 新設）。

## なぜ3つを呼ぶのか

3ランクは互いに排他で、統合は**和集合＝ラベルの付け替え**（買うレースは1件も
増減しない）。選別条件をここで書き直すと「片方だけ直る」を作れるので、
**既存の3つの `build_rows` をそのまま呼んで rank/suffix だけ差し替える**。

排他性はここで検算する。同じレースが2つの選別から来たら**黙って二重計上せず
落ちる**（投資額と件数が静かに二重になるのが最悪の壊れ方）。

⚠️ 3モジュールが各々 `build_features_wt` とモデル推論を回すので**3倍遅い**。
   正しさを優先している（共通化して速くするのは、統合が安定してから）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import (  # noqa: E402
    backfill_7a_rank_wt, backfill_7s_rank_wt, backfill_7ss_rank_wt,
)

#: (モジュール, 元のsuffix) — ログで内訳を出すために持つ
_SOURCES = (
    (backfill_7s_rank_wt, "7S"),
    (backfill_7a_rank_wt, "7A"),
    (backfill_7ss_rank_wt, "7SS"),
)


def build_rows(model_name: str, date_from: str, date_to: str,
               win_model_name: str | None = None,
               bad_model_name: str | None = None) -> list[dict]:
    """統合後 RANK_7S の行（採点済み）を返す。"""
    rows: list[dict] = []
    counts: dict[str, int] = {}
    for mod, origin in _SOURCES:
        kwargs = {}
        if win_model_name is not None:
            kwargs["win_model_name"] = win_model_name
        if bad_model_name is not None:
            kwargs["bad_model_name"] = bad_model_name
        got = mod.build_rows(model_name, date_from, date_to, **kwargs)
        counts[origin] = len(got)
        rows += got

    # 🔴 排他性の検算。壊れていたら二重計上する前に落とす。
    bases = [r["race_key"].split("#")[0] for r in rows]
    if len(bases) != len(set(bases)):
        dup = sorted({b for b in bases if bases.count(b) > 1})
        raise AssertionError(
            "旧 7S/7A/7SS が同じレースを選んだ（排他のはず）: " + ", ".join(dup[:10]))

    for r in rows:
        r["race_key"] = r["race_key"].split("#")[0] + "#7S"
        r["rank"] = "RANK_7S"
        # ゲート由来の内訳（SS/S）は統合で意味を失うので落とす。
        r["gate_label"] = None
    rows.sort(key=lambda r: (r["race_date"], r["race_key"]))
    print(f"[backfill-7s-merged] {date_from}〜{date_to} 内訳 "
          + " / ".join(f"旧{k}:{v}" for k, v in counts.items())
          + f" → 計{len(rows)}", flush=True)
    return rows
