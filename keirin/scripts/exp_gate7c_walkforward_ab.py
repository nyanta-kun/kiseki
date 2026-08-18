#!/usr/bin/env python3
"""7C の選別差し替え（p3合計 → 4特徴スコア）の walk-forward A/B（2026-08-18）

`docs/analysis/56-race-selection-meta.md` で二軸的中は +1.2〜2.4pt と出たが、
測ったのは**二軸的中と的中時オッズまで**で、相手の点数・ガミ・買い方は見ていない。
このリポジトリの規約どおり、**実装したうえで同一レース・同一 vintage の A/B** を行う。

## 結果（2026-08-18・**不採用**）

| | 件数 | 素の的中 | 表示的中 | ROI |
|---|---|---|---|---|
| 旧（p3合計 >= 1.44） | 3,502 | 41.01% | 31.90% | 77.02% |
| 新 閾値 0.1099 | 3,165 (−10.0%) | 42.12% | 32.39% | 77.90% |
| 新 閾値 0.0130 | 3,276 (−6.5%) | 41.64% | 32.20% | 77.37% |

**件数を戻すほど差が縮み、3,502件へ外挿すると Δ表示的中 ≒ −0.09pt。**
🔴 改善の正体は「良いレースを選んだ」ではなく **「少なく賭けた」** だった。
本番の判定は `p3合計 >= 1.44` のまま据え置いた。詳細 `docs/analysis/56-race-selection-meta.md`。

## やり方

- 月次凍結 vintage（`lgbm_wt_eval_mYYMM` / `lgbm_wt_win_mYYMM`）で
  `backfill_7c_rank_wt.build_rows` を**そのまま**呼ぶ（本番と同じ買い方の正本を通る）
- 同じ月に対して **2回**回す（旧ゲート / 新ゲート）
- DB へは一切書かない（`insert_rows` を呼ばない）

🔴 **本番コードには手を入れない。** 新ゲート側は `strategy_wt._gate_p3_sum` を
   **代理値**（`RANK_7C_P3_SUM_MIN + (score − 閾値)`）に差し替えて実現する。
   こうすると `rank_7c_daily_select` の中の `>= RANK_7C_P3_SUM_MIN` が
   そのまま `score >= 閾値` と同値になり、選別以外は一切変わらない。
   ⚠️ この差し替えは **7C の判定にしか使えない**（`_gate_p3_sum` は 9C / 7M1 も
   使う共有関数。本ハーネスは 7C の行しか作らないので影響しない）。

⚠️ 「変更の効果を測るなら**同じスクリプトを2回**」（memory `keirin_line_structure_2026_08_18`）。
   picks_history の実データと自前の再構成を混ぜて比べない。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_gate7c_walkforward_ab.py \\
        [--from 2025-07] [--to 2026-08]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

import src.strategy_wt as S  # noqa: E402
from scripts.backfill_7c_rank_wt import build_rows  # noqa: E402
from src.race_gate_7c import THRESHOLD as GATE_THRESHOLD  # noqa: E402
from src.wt_vintage_config import monthly_windows  # noqa: E402

_ORIG_GATE_P3_SUM = S._gate_p3_sum


def _score_as_p3_sum(threshold: float):
    """新ゲートを「`_gate_p3_sum` の代理値」として表現する。

    `rank_7c_daily_select` は `_gate_p3_sum(c) >= RANK_7C_P3_SUM_MIN` で判定する。
    そこへ `RANK_7C_P3_SUM_MIN + (score − threshold)` を返せば、
    比較は `score >= threshold` と同値になる。**本番コードは触らない。**

    ⚠️ `gate7c_score` が無い候補は旧値へ落とす（判定不能を全滅にしない）。
    """
    def _fn(c: dict) -> float:
        v = c.get("gate7c_score")
        if v is None:
            return _ORIG_GATE_P3_SUM(c)
        return S.RANK_7C_P3_SUM_MIN + (float(v) - threshold)
    return _fn


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"件数": 0}
    d = pd.DataFrame(rows)
    bet, pay = d["bet_amount"].sum(), d["payout"].sum()
    return {
        "件数": len(d),
        "素の的中": round(100 * d["hit"].mean(), 2),
        # netkeirin の表示的中はガミ（払戻 <= 賭け金）を不的中と数える
        "表示的中": round(100 * (d["payout"] > d["bet_amount"]).mean(), 2),
        "ROI": round(100 * pay / bet, 2) if bet else 0.0,
        "平均点数": round(d["n_combos"].mean(), 2),
        "投資": int(bet), "払戻": int(pay),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="ym_from", default="2025-07")
    ap.add_argument("--to", dest="ym_to", default="2026-08")
    # 件数を揃えた再試行のため、新ゲートの閾値を上書きできるようにする。
    # 🔴 A/B の交絡（新ゲートは毎窓 10% 少なく賭ける）を分離するのが目的。
    #    「選別が良くなった」のか「少なく賭けただけ」なのかは、件数を揃えないと分からない。
    ap.add_argument("--threshold", type=float, default=GATE_THRESHOLD)
    ap.add_argument("--arms", default="both", choices=["both", "new"])
    args = ap.parse_args()
    print(f"[ab] 新ゲートの閾値 {args.threshold}", flush=True)

    windows = [w for w in monthly_windows()
               if args.ym_from <= w[0][:7] <= args.ym_to]
    print(f"[ab] {len(windows)} 窓  {windows[0][0]}〜{windows[-1][1]}", flush=True)

    acc: dict[str, list[dict]] = {"旧": [], "新": []}
    per_month = []
    new_gate = _score_as_p3_sum(args.threshold)
    for date_from, date_to, ev, wn in windows:
        got = {}
        arms = (("旧", _ORIG_GATE_P3_SUM), ("新", new_gate))
        if args.arms == "new":
            arms = (("新", new_gate),)
        for arm, fn in arms:
            S._gate_p3_sum = fn
            try:
                rows = build_rows(ev, date_from, date_to, win_model_name=wn)
            finally:
                S._gate_p3_sum = _ORIG_GATE_P3_SUM
            acc[arm].extend(rows)
            got[arm] = summarize(rows)
        if args.arms == "new":
            per_month.append({"月": date_from[:7], "旧_件数": None,
                              "新_件数": got["新"].get("件数"), "旧_表示的中": None,
                              "新_表示的中": got["新"].get("表示的中"),
                              "旧_ROI": None, "新_ROI": got["新"].get("ROI")})
            print(f"  {date_from[:7]}  新 {got['新']}", flush=True)
            continue
        per_month.append({"月": date_from[:7],
                          **{f"{k}_{a}": v.get(k) for a in ("旧", "新")
                             for k, v in ((kk, got[a]) for kk in ("件数",))},
                          "旧_件数": got["旧"].get("件数"), "新_件数": got["新"].get("件数"),
                          "旧_表示的中": got["旧"].get("表示的中"),
                          "新_表示的中": got["新"].get("表示的中"),
                          "旧_ROI": got["旧"].get("ROI"), "新_ROI": got["新"].get("ROI")})
        print(f"  {date_from[:7]}  旧 {got['旧']}\n           新 {got['新']}", flush=True)

    print("\n=== 月次 ===")
    cols = ["月", "旧_件数", "新_件数", "旧_表示的中", "新_表示的中", "旧_ROI", "新_ROI"]
    print(pd.DataFrame(per_month)[cols].to_string(index=False))
    print("\n=== 全期間 ===")
    print(pd.DataFrame([{"arm": a, **summarize(r)} for a, r in acc.items() if r]
                       ).to_string(index=False))


if __name__ == "__main__":
    main()
