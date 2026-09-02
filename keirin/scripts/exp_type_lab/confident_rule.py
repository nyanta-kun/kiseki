#!/usr/bin/env python3
"""「自信あり」の選定ルールを差し替える前後を比べる（2026-09-02・ユーザー指示）。

## 指示

> 自信ありフラグについて、現在終日のレースで選んでいるが、**夕方くらいまでのレース**の
> うち、**合成が3倍以上**で**期待値が最も高い**レースとしてください。

現行（2026-08-28 ユーザー決定）は
  候補 … `pred_min_payout >= 20,000`（どの目が当たっても2万円）・**終日**
  順位 … Σp（買い目の的中確率の合計）が最大
なので、**候補条件も順位付けも両方が変わる**。

## 3つの量（すべて行に焼き付いた `legs` から出せる。モデルを引き直さない）

    合成オッズ = 1 / Σ(1/予測オッズ)          … 買い目の集合だけで決まる（配分に依らない）
    EV         = Σ(確率×賭け金×予測オッズ) ÷ Σ賭け金
    Σp         = Σ確率                        … 現行の順位

🔴 **合成オッズは `pred_mean_payout / 予算` ではない。** ダッチ配分のプランでは一致するが、
   信頼度傾斜（`alloc="conf"`＝A_hit / F_hit / F_pay）では一致しない。必ず 1/Σ(1/o) で出す。

🔴 **「夕方まで」は `meeting_wave.NIGHT_FROM_HOUR`（18時）を使う。** 新しい定数を
   作らない——入稿の波の境界と食い違うと「夕の波で入稿したのに夕方扱いされない」
   レースが出る。

⚠️ EV は購入判断に使ってはいけない（競輪の市場は効率的で、モデル由来の期待値による
   選別は繰り返し否定されている）。ここでの用途は **1つしか無い枠をどこに置くか**の
   相対比較に限られる。絶対値が 1.0 を超えているかには意味が無い。

## 実測（本番の分岐とゲートを通した「売る1商品/レース」・1日1件を選ぶ）

    腕                              表示的中   ROI [95%CI]        払戻中央   10万+
    探索 2025（365日・44.9件/日）
      現行 終日 × 最低払戻2万 → Σp    29.9%   72.7 [ 59.8, 86.0]  22,500円   0件
      **新 <18時 × 合成3倍+ → EV**   12.9%  109.9 [ 67.7,160.1]  45,850円  13件
      参考 <18時 × 合成3倍+ → Σp     30.2%   92.4 [ 76.6,108.4]  27,370円   0件
      無作為対照20本の中央値           24.6%   80.0
    確認 2026-01〜08-04（216日・34.4件/日）
      現行 終日 × 最低払戻2万 → Σp    33.8%   88.4 [ 70.4,106.0]  23,400円   0件
      **新 <18時 × 合成3倍+ → EV**   18.1%  102.9 [ 64.2,147.4]  35,490円   5件
      参考 <18時 × 合成3倍+ → Σp     28.7%  101.8 [ 78.4,126.4]  32,675円   0件
      無作為対照20本の中央値           26.4%   88.5

🔴🔴 **新ルールはアイコンの性格を変える。** 表示的中が **両窓で約16pt 落ち**
   （29.9→12.9% / 33.8→18.1%）、**無作為に1件選ぶより低くなる**（24.6 / 26.4%）。
   n=365/216 で SE ≈ 2.6pt なのでこの差は確か。**「当たりやすい1本」から
   「大きく獲りにいく1本」への転換**で、EV 最大化は定義上こう振る舞う
   （高オッズ・低確率の買い目ほど EV が大きく出る）。
🔴 **ROI と 10万+ が上がって見えるが ROI では判定できない**（CI が [67.7,160.1] /
   [64.2,147.4] と壁も 100% も跨ぐ）。確かなのは**払戻中央が 1.5〜2倍**になることと
   **10万+ が 0件 → 13/5件**になること。
🟢 **順位を Σp のままにして候補条件だけ新しくする**と、表示的中を保ったまま
   （30.2 / 28.7%）払戻中央が上がる（27,370 / 32,675円）。10万+ は 0件のまま。
   ＝ 3つの要素のうち **表示的中を削っているのは「EV 最大」の部分**。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/confident_rule.py
"""
from __future__ import annotations

import importlib.util
import json
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.confident_pick import legs_expected_value, synthetic_odds  # noqa: E402
from src.database import get_connection  # noqa: E402
from src.meeting_wave import NIGHT_FROM_HOUR  # noqa: E402
from src.type_lab import ANA_PW_ENT_MIN, sell_plans_for  # noqa: E402

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
_G = importlib.util.module_from_spec(_s)
_s.loader.exec_module(_G)  # type: ignore[union-attr]

MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0

Q = """
SELECT t.race_date AS d, t.race_key, t.plan_key, t.type_label, t.n_entries,
       t.race_type, t.legs, t.axis_sum, t.pw_ent, t.pred_min_payout,
       t.pred_mean_payout, t.hit, t.payout, t.budget, t.settled_at, r.start_at
FROM type_lab_picks t JOIN wt_races r ON r.race_key = t.race_key
WHERE t.mode = ? AND t.race_date BETWEEN ? AND ?
"""


def _hour(start_at) -> float | None:
    try:
        return ((int(start_at) + 9 * 3600) % 86400) / 3600
    except (TypeError, ValueError):
        return None


def load(mode: str, d1: str, d2: str) -> list[dict]:
    """その窓の**実際に売る1商品/レース**（本番の分岐とゲートを通したもの）。"""
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(Q, (mode, d1, d2)).fetchall()]
    by_race: dict[str, dict] = defaultdict(dict)
    for r in rows:
        legs = r["legs"]
        r["legs"] = json.loads(legs) if isinstance(legs, str) else (legs or [])
        by_race[str(r["race_key"])][str(r["plan_key"])] = r
    out = []
    for rk, plans in by_race.items():
        any_row = next(iter(plans.values()))
        pw = any_row.get("pw_ent")
        trio = plans.get("A_trio")
        trio_ok = bool(trio and _passes(trio))
        keys = [p.key for p in sell_plans_for(
            str(any_row["type_label"]), int(any_row["n_entries"] or 7),
            any_row.get("race_type"),
            pw_ent=(float(pw) if pw is not None else None), trio_ok=trio_ok)]
        for k in keys:
            r = plans.get(k)
            if not r or not _passes(r):
                continue
            if not _G.passes_axis_gate(k, float(r["axis_sum"]) if r["axis_sum"] is not None
                                       else None, int(r["n_entries"] or 7)):
                continue
            odds = [float(x["pred_odds"]) for x in r["legs"] if x.get("pred_odds")]
            if len(odds) != len(r["legs"]):
                continue
            r["synth"] = synthetic_odds(r["legs"])
            r["ev"] = legs_expected_value(r["legs"])
            r["sump"] = sum(float(x["prob"]) for x in r["legs"]
                            if x.get("prob") is not None)
            r["hh"] = _hour(r["start_at"])
            r["d"] = str(r["d"])
            if r["synth"] is None or r["ev"] is None:
                continue
            out.append(r)
    return out


def _passes(r: dict) -> bool:
    """入稿ゲート（平均想定払戻 > 2万・1点でも予測 < 2.0倍なら見送り）。"""
    try:
        if r["pred_mean_payout"] is None or float(r["pred_mean_payout"]) <= MIN_MEAN_PAYOUT:
            return False
        odds = [float(x["pred_odds"]) for x in r["legs"] if x.get("pred_odds")]
        return bool(odds) and len(odds) == len(r["legs"]) and min(odds) >= MIN_POINT_ODDS
    except (TypeError, ValueError, KeyError):
        return False


RULES = {
    "現行 終日 × 最低払戻2万 → Σp最大":
        (lambda r: (r["pred_min_payout"] or 0) >= 20_000, lambda r: r["sump"]),
    "新   <18時 × 合成3倍+ → EV最大":
        (lambda r: r["hh"] is not None and r["hh"] < NIGHT_FROM_HOUR and r["synth"] >= 3.0,
         lambda r: r["ev"]),
    "参考 <18時 × 合成3倍+ → Σp最大":
        (lambda r: r["hh"] is not None and r["hh"] < NIGHT_FROM_HOUR and r["synth"] >= 3.0,
         lambda r: r["sump"]),
    "参考 終日 × 合成3倍+ → EV最大":
        (lambda r: r["synth"] >= 3.0, lambda r: r["ev"]),
}


def _boot(done: list[dict], n_boot: int = 2000, seed: int = 0) -> tuple[float, float]:
    """ROI の 95%CI（日単位のブートストラップ）。

    🔴 **1日1件なので n は日数そのもの。** 払戻は 10万円級の裾に支配されるので
       点推定だけ見ると「ROI 110%」に見える。必ず CI を添えること。
    """
    rnd = random.Random(seed)
    out = []
    for _ in range(n_boot):
        sample = [done[rnd.randrange(len(done))] for _ in range(len(done))]
        inv = sum(r["budget"] for r in sample) or 1
        out.append(sum(r["payout"] or 0 for r in sample) / inv * 100)
    out.sort()
    return out[int(n_boot * 0.025)], out[int(n_boot * 0.975)]


def summarize(picked: list[dict], ndays: int) -> str:
    if not picked:
        return "  （選定なし）"
    done = [r for r in picked if r["settled_at"] is not None]
    hits = [r for r in done if r["hit"]]
    shown = [r for r in hits if (r["payout"] or 0) > (r["budget"] or 0)]
    inv = sum(r["budget"] for r in done) or 1
    pays = sorted((r["payout"] or 0) for r in shown)
    lo, hi = _boot(done)
    roi = sum(r["payout"] or 0 for r in done) / inv * 100
    return (f"  選定 {len(picked):3d}/{ndays}日  採点 {len(done):3d}  "
            f"表示的中 {len(shown)/len(done)*100:5.1f}%  "
            f"ROI {roi:6.1f}% [{lo:5.1f},{hi:6.1f}]  "
            f"払戻中央 {(st.median(pays) if pays else 0):7,.0f}円  "
            f"10万+ {sum(1 for p in pays if p >= 100_000)}件")


def main() -> None:
    for mode, d1, d2, lab in (("paper", "2025-01-01", "2025-12-31", "探索 2025"),
                              ("paper", "2026-01-01", "2026-08-04", "確認 2026-01〜08-04")):
        rows = load(mode, d1, d2)
        by_day: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_day[r["d"]].append(r)
        nd = len(by_day)
        print("")
        print("=" * 100)
        print(f"=== {lab}  売る商品 {len(rows):,}件 / {nd}日（{len(rows)/nd:.1f}件/日）===")
        for name, (cond, key) in RULES.items():
            picked, empty = [], 0
            for d, rs in by_day.items():
                cand = [r for r in rs if cond(r)]
                if not cand:
                    empty += 1
                    continue
                # 同値は race_key → plan_key で決める（本番 `pick_best` と同じ）
                top = max(key(r) for r in cand)
                picked.append(sorted((r for r in cand if key(r) == top),
                                     key=lambda r: (r["race_key"], r["plan_key"]))[0])
            print(f"  [{name}]  選べない日 {empty}日")
            print(summarize(picked, nd))
        # 🔴 無作為対照: その日の売った商品から1件を無作為に選ぶ（20本の中央値）
        rs_shown, rs_roi = [], []
        for seed in range(20):
            rnd = random.Random(seed)
            pk = [rnd.choice(rs) for rs in by_day.values() if rs]
            done = [r for r in pk if r["settled_at"] is not None]
            if not done:
                continue
            sh = [r for r in done if r["hit"] and (r["payout"] or 0) > (r["budget"] or 0)]
            rs_shown.append(len(sh) / len(done) * 100)
            rs_roi.append(sum(r["payout"] or 0 for r in done)
                          / (sum(r["budget"] for r in done) or 1) * 100)
        print(f"  [無作為対照20本の中央値]  表示的中 {st.median(rs_shown):5.1f}%  "
              f"ROI {st.median(rs_roi):6.1f}%")


if __name__ == "__main__":
    main()
