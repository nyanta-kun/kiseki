#!/usr/bin/env python3
"""型A の中で「実際は荒れた」レースを事前に選べるか（2026-08-31・ユーザー指示）。

## なぜ

型A は買い方を6通り変えても **分岐割れ 72〜88%** が動かず、「堅いレースを買う」
こと自体が割に合わないと分かった。

ユーザーの指摘:「**型A と見たレースで、実際には型A よりオッズがついたレースは
ないか。それを拾えれば人気が集中しているレースなので価値がある**」。

＝ 型A（＝市場も自社も堅いと見ている層）の中で荒れたレースは、
**人気が1〜2車に集中しているぶん配当が跳ねる**。事前に選べるなら価値がある。

## 測り方

型A のレースを、**発走前に確定している量**だけで割り、確定三連単オッズの分布を見る。

    axis_sum（軸の堅さ）/ arare（荒れ度）/ gap（相手の開き）/ レース種別 / 開催日目

🔴 **上限（オラクル）も出す。** 「荒れた型A だけ買えたら」がどれだけ良いかを先に
   知らないと、選別に投資する価値があるか判断できない。
🔴 既知の否定結果（[[keirin_highpay_race_classifier_2026_08_24]]）:
   「1万/3万が出るレースか」の事前判定は AUC 0.641 / 0.569 で、
   **72特徴を積んでも実体は「1着率エントロピー」1本**。ここは型A に限った再確認。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/type_a_upset.py
"""
from __future__ import annotations

import importlib.util
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.database import get_connection            # noqa: E402
from src.marquee import is_fill_target             # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_GATE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_GATE)                    # type: ignore[union-attr]

WINDOWS = {"探索 2025": ("2025-01-01", "2025-12-31"),
           "確認 2026": ("2026-01-01", "2026-08-26")}


def show(title: str, groups: dict[str, list[float]], min_n: int = 100) -> None:
    print(f"\n  {title}")
    print(f"    {'群':<16}{'R数':>7}{'中央倍率':>10}{'30倍+':>8}{'100倍+':>8}")
    for k, v in sorted(groups.items(), key=lambda kv: -median(kv[1]) if kv[1] else 0):
        if len(v) < min_n:
            continue
        print(f"    {k:<16}{len(v):>7,}{median(v):>9.1f}倍"
              f"{sum(1 for x in v if x >= 30) / len(v):>8.1%}"
              f"{sum(1 for x in v if x >= 100) / len(v):>8.1%}")


def main() -> int:
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT race_date, axis_sum, arare, gap, race_type, day_index, "
            "       win_tf_odds FROM type_lab_picks "
            "WHERE mode = ? AND settled_at IS NOT NULL AND plan_key = ? "
            "  AND n_entries = 7 AND win_tf_odds IS NOT NULL", ("paper", "A_hit"))]
    rows = [d for d in rows
            if is_fill_target(d.get("race_type"), None)
            or _GATE.passes_axis_gate(
                "A_hit", float(d["axis_sum"]) if d["axis_sum"] is not None else None, 7)]

    for win, (lo, hi) in WINDOWS.items():
        rs = [d for d in rows if lo <= str(d["race_date"]) <= hi]
        if not rs:
            continue
        odds = [float(d["win_tf_odds"]) for d in rs]
        print(f"\n=== {win}（型A {len(rs):,}R）===")
        print(f"  確定三連単オッズ  中央 {median(odds):.1f}倍 / "
              f"30倍+ {sum(1 for x in odds if x >= 30) / len(odds):.1%} / "
              f"100倍+ {sum(1 for x in odds if x >= 100) / len(odds):.1%}")

        def by(fn, title, min_n=100):
            g = defaultdict(list)
            for d in rs:
                g[str(fn(d))].append(float(d["win_tf_odds"]))
            show(title, g, min_n)

        srt = sorted(rs, key=lambda d: float(d["axis_sum"] or 0))
        n = len(srt)
        q = {id(d): i for i, d in enumerate(srt)}
        by(lambda d: ("axis 低1/3" if q[id(d)] < n // 3
                      else "axis 中1/3" if q[id(d)] < 2 * n // 3 else "axis 高1/3"),
           "① 軸の堅さ（axis_sum）")
        by(lambda d: f"arare {int(d['arare'])}" if d.get("arare") is not None else "—",
           "② 荒れ度（arare）")
        srt2 = sorted(rs, key=lambda d: float(d["gap"] or 0))
        q2 = {id(d): i for i, d in enumerate(srt2)}
        by(lambda d: ("gap 小1/3" if q2[id(d)] < n // 3
                      else "gap 中1/3" if q2[id(d)] < 2 * n // 3 else "gap 大1/3"),
           "③ 相手の開き（gap）")
        by(lambda d: str(d.get("race_type") or "—"), "④ レース種別", 200)

        # 🔴 上限（オラクル）: 荒れた型A だけを買えたら
        hi30 = [d for d in rs if float(d["win_tf_odds"]) >= 30]
        print(f"\n  上限（オラクル）: 30倍以上で決着した型A は {len(hi30):,}R "
              f"({len(hi30) / len(rs):.1%})・中央 "
              f"{median([float(d['win_tf_odds']) for d in hi30]):.0f}倍")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
