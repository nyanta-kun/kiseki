#!/usr/bin/env python3
"""型ラボの夜間レビュー（2026-08-29 新設・ユーザー要望）。

その日の最終レースが確定した後に走らせ、**事実だけ**を4つの層に分けて出す。
「改善するかどうか」の判断材料を作るのがここの仕事で、**判断はここでしない**。

## なぜ層を分けるのか

1日の商品は 40件前後しかない。ROI も的中率もこの件数では**ほとんど何も言えない**
（§2 の参照分布がそれを毎晩数字で見せる）。それでも毎晩見る価値があるのは、
**単日でも黒白がつく層**が混ざっているからで、両者を同じ表に並べると
「今日は ROI 68% だった → 何か直そう」という**最悪の読み方**を誘発する。

| 層 | 単日で判断してよいか | 何を見るか |
|---|---|---|
| §1 異常検知 | **してよい** | 動くはずのものが動いたか（二値の事実） |
| §2 当日成績 | **してはいけない** | 参照分布の中のどこか。外れ値かどうかだけ |
| §3 外れの分解 | **してはいけない** | 台帳へ積む。発火条件を超えた型だけ昇格 |
| §4 ゲートの答え合わせ | **してはいけない** | 前向き検証の累積。当日の行は1日分の点 |

## 🔴 この道具の最大の危険は「後知恵の積み上げ」である

どのレースにも「こう買えば当たった」は必ず存在する。それを毎晩拾って
その都度ルールを足すと、**過去にだけ最適化された規則の山**ができる。
`daily_review_wt.py`（2026-08-17・旧ランク用）と同じ構造で防ぐ:

- **反実仮想を出さない。** 出すのは決着クラス（順当／軸2+穴／軸崩壊…）への
  分類だけで、個別レースの買い目案は作らない
- **採否をここで決めない。** 台帳に積み、`ESCALATE_MIN_N` 件たまった型だけを
  「全期間で検証する候補」として名前を挙げる
- **参照分布を必ず添える。** 生の数字だけを出さない

## 判定はすべて正本へ委譲する（写経しない）

| 何 | 正本 |
|---|---|
| 売った商品の採点 | `backend/src/services/keirin_settlement.py`（`src/sold_performance` 経由） |
| 決着クラスの分類 | `backend/src/services/keirin_type_lab_outcome.py` |
| 軸信頼ゲート | `backend/src/services/keirin_type_lab_gate.py` |
| 看板レース | `backend/src/services/keirin_marquee.py`（`src/marquee` 経由） |
| 売るプラン | `src/type_lab.SELL_PLANS` |

## 使い方

    PYTHONPATH=. .venv/bin/python scripts/nightly_review_type_lab.py [YYYY-MM-DD]
        --no-append      台帳へ書かない（表示だけ）
        --no-discord     Discord へ送らない
        --boot N         参照分布のブートストラップ回数（既定 2000）

台帳: `data/analysis/type_lab_nightly_ledger.csv`（追記のみ・1日1プラン1行）

⚠️ **過去日へ遡って実行しないこと。** §4 のゲート判定は `axis_sum`＝その日の
   本番モデルの出力に依存する。モデルを再学習した後に遡ると、別のモデルの目で
   過去を裁くことになり台帳が汚れる（`daily_review_wt.py` と同じ制約）。
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import random
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection                       # noqa: E402
from src.evaluation.backtest_wt import _load_payouts_wt       # noqa: E402
from src.marquee import is_fill_target                        # noqa: E402
from src.notify.discord import send                           # noqa: E402
from src.sold_performance import (                            # noqa: E402
    build_sold_races, group_by, summarize, winning_combo_labels,
)
from src.type_lab import SELL_PLANS                           # noqa: E402

LEDGER = REPO / "data" / "analysis" / "type_lab_nightly_ledger.csv"

#: 参照分布を作る窓。**ペーパー行（`mode='paper'`）の窓**であって検証窓ではない。
#: 2025年は vintage オッズ・2026年は板 npz で作られており、どちらも OOS
#: （[[keirin_type_lab_vintage_odds_models_2026_08_28]]）。
BASELINE_WINDOW = ("2025-01-01", "2026-08-26")

#: 台帳がこの件数たまったプランだけを「検証候補」として名前を挙げる。
#: 🔴 **日次では絶対に昇格させない。** 40件/日では ROI の 90% 区間が
#:    おおむね [40%, 130%] に広がる（§2 が毎晩それを実測で見せる）。
ESCALATE_MIN_N = 100

#: 異常検知で「入稿が少なすぎる」と言う閾値（直近7日の中央値に対する比）。
LOW_SUBMIT_RATIO = 0.5


def _bind(rel: str, name: str) -> ModuleType:
    """kiseki 側の正本をファイル読み込みで束縛する（`src/marquee.py` と同じ形）。

    🔴 写経しないこと。閾値や分類規則が2箇所に分かれた瞬間、
       「画面の答え」と「夜のレビューの答え」が静かに食い違う。
    """
    path = REPO.parent / rel
    if not path.exists():                      # pragma: no cover - 配備漏れの検知
        raise SystemExit(f"[nightly_review] 正本が見つかりません: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)               # type: ignore[union-attr]
    return mod


_GATE = _bind("backend/src/services/keirin_type_lab_gate.py", "keirin_type_lab_gate")
_OUTCOME = _bind("backend/src/services/keirin_type_lab_outcome.py",
                 "keirin_type_lab_outcome")


# ───────────────────────────── 読み出し ─────────────────────────────

def _sold(day: str) -> tuple[list, int, list[dict]]:
    """その日に**売った**商品（`netkeirin_submissions` + `bet_detail`）。

    情報源を `picks_history` にしてはいけない（`bet_amount>0` は発走15分前の
    名目値でゲートを一切見ない＝[[keirin_sold_source_of_truth]]）。
    """
    with get_connection() as c:
        subs = [dict(r) for r in c.execute(
            "SELECT ns.race_key, ns.rank_key, ns.origin, ns.bet_detail, ns.session, "
            "       ns.venue_name, ns.race_no, ns.deleted_at, ns.cancel_reason, "
            "       wr.race_date "
            "FROM netkeirin_submissions ns "
            "JOIN wt_races wr ON wr.race_key = ns.race_key "
            "WHERE wr.race_date = ?", (day,))]
    alive = [s for s in subs if s["deleted_at"] is None]
    keys = sorted({s["race_key"] for s in alive})
    finishes, payouts = _results(keys)
    races, n_skipped = build_sold_races(alive, finishes, payouts)
    return races, n_skipped, subs


def _results(keys: list[str]) -> tuple[dict, dict]:
    """`(着順, 車番)` の並びと確定払戻。`sold_performance_report._fetch` と同形。"""
    if not keys:
        return {}, {}
    fins: dict[str, list[tuple[int, int]]] = {}
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            chunk = keys[i:i + 900]
            q = ("SELECT race_key, frame_no, finish_order FROM wt_entries "
                 "WHERE race_key IN (%s) AND finish_order BETWEEN 1 AND 3"
                 % ",".join("?" * len(chunk)))
            for r in c.execute(q, chunk):
                d = dict(r)
                fins.setdefault(d["race_key"], []).append(
                    (int(d["finish_order"]), int(d["frame_no"])))
    # ⚠️ 車番だけに畳むと同着が潰れる。`(着順, 車番)` のまま渡す。
    finishes = {k: sorted(v) for k, v in fins.items()}
    raw = _load_payouts_wt(keys)
    payouts: dict[str, dict[str, int]] = {}
    for rk, rows in finishes.items():
        pm = raw.get(rk, {})
        m: dict[str, int] = {}
        for label in winning_combo_labels(rows):
            if "=" in label:
                got = pm.get(("trio", frozenset(int(x) for x in label.split("="))))
            else:
                got = pm.get(("trifecta", tuple(int(x) for x in label.split("-"))))
            if got:
                m[label] = int(got)
        payouts[rk] = m
    return finishes, payouts


def _live_rows(day: str) -> list[dict]:
    """その日の型ラボの行（live / live9）。**最新の型だけ**に絞る。

    🔴 組み直しで型が変わると古い型の行が残る（2026-08-29 に4レースで実際に
       起きた）。`netkeirin_submit_type_lab._load_rows` と同じ絞りをここでも行う。
       やらないと §4 のゲート集計に「売られなかった古い型」が混ざる。
    """
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT t.race_key, t.race_date, t.venue_name, t.race_no, t.race_type, "
            "       t.n_entries, t.type_label, t.axis_sum, t.mode, t.plan_key, "
            "       t.budget, t.payout, t.hit, t.settled_at, t.win_combo, "
            "       t.p3_order, t.win_tf_odds, t.generated_at, r.cup_grade "
            "FROM type_lab_picks t LEFT JOIN wt_races r ON r.race_key = t.race_key "
            "WHERE t.race_date = ? AND t.mode IN (?, ?)", (day, "live", "live9"))]
    current: dict[tuple[str, str], tuple] = {}
    for d in rows:
        key = (str(d["race_key"]), str(d["mode"]))
        gen = d.get("generated_at")
        if key not in current or (gen is not None and current[key][0] is not None
                                  and gen > current[key][0]):
            current[key] = (gen, str(d["type_label"]))
    return [d for d in rows
            if str(d["type_label"]) == current[(str(d["race_key"]),
                                                str(d["mode"]))][1]]


def _gate_ok(row: dict) -> bool:
    """軸信頼ゲートを通るか。**看板は素通し**（入稿側 `_passes_axis_gate` と同一）。"""
    if is_fill_target(row.get("race_type"), row.get("cup_grade")):
        return True
    return bool(_GATE.passes_axis_gate(
        str(row["plan_key"]),
        float(row["axis_sum"]) if row.get("axis_sum") is not None else None,
        int(row["n_entries"]) if row.get("n_entries") is not None else None))


def _baseline_pool() -> dict[str, list[tuple[int, int]]]:
    """参照分布の母集団。プラン → `(賭け金, 払戻)` の一覧。

    🔴 **本番と同じゲートを掛けてから積む。** 掛けないと「ゲートで底を外した今日」を
       「底も含む過去」と比べることになり、今日が実力より良く見える。
    """
    lo, hi = BASELINE_WINDOW
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT t.plan_key, t.axis_sum, t.n_entries, t.race_type, t.budget, "
            "       t.payout, r.cup_grade "
            "FROM type_lab_picks t LEFT JOIN wt_races r ON r.race_key = t.race_key "
            "WHERE t.mode = ? AND t.race_date BETWEEN ? AND ? "
            "  AND t.settled_at IS NOT NULL AND t.budget > 0",
            ("paper", lo, hi))]
    pool: dict[str, list[tuple[int, int]]] = {}
    for d in rows:
        if d["plan_key"] not in SELL_PLANS or not _gate_ok(d):
            continue
        pool.setdefault(str(d["plan_key"]), []).append(
            (int(d["budget"]), int(d["payout"] or 0)))
    return pool


# ───────────────────────────── §1 異常検知 ─────────────────────────────

def section_alerts(day: str, sold, n_skipped: int, subs: list[dict],
                   live: list[dict]) -> tuple[list[str], int]:
    """動くはずのものが動いたか。**ここだけは単日で黒白がつく。**"""
    out: list[str] = []
    n_ng = 0

    def ok(msg: str) -> None:
        out.append(f"  [OK] {msg}")

    def ng(msg: str) -> None:
        nonlocal n_ng
        n_ng += 1
        out.append(f"  [NG] {msg}")

    alive = [s for s in subs if s["deleted_at"] is None]
    sess = Counter(str(s["session"] or "—") for s in alive)
    med = _recent_median_submits(day)
    detail = " / ".join(f"{k} {v}" for k, v in sorted(sess.items()))
    if med and len(alive) < med * LOW_SUBMIT_RATIO:
        ng(f"入稿 {len(alive)}件（{detail}）— 直近7日の中央値 {med}件 を大きく下回る")
    else:
        ok(f"入稿 {len(alive)}件（{detail}）— 直近7日の中央値 {med if med else '—'}件")

    # 1レース2商品（2026-08-29 に実際に起きた型。生成側・読み側・入稿ループの
    # 3重ガードが入っているが、**壊れたときに気づけるのはここだけ**）。
    dup = [rk for rk, n in Counter(s["race_key"] for s in alive).items() if n > 1]
    if dup:
        names = ", ".join(sorted(dup)[:5])
        ng(f"1レース2商品が {len(dup)}レース: {names}")
    else:
        ok("1レース1商品（重複なし）")

    # 見送り理由。並び・印の欠測は1件でも異常（2026-08-26 熊本の型）。
    skips = _skips(day)
    lineup = sum(n for code, n in skips.items() if "LINEUP" in code.upper())
    if skips:
        out.append("  ---- 見送り理由 " +
                   ", ".join(f"{k}={v}" for k, v in sorted(skips.items())))
    if lineup:
        ng(f"並び予想・AI印の欠測で見送り {lineup}件（上流のスクレイプを確認）")
    else:
        ok("並び・印の欠測なし")

    # 未採点。00 時台に走らせても残るなら、着順か確定オッズが来ていない。
    unsettled = [d for d in live if d["settled_at"] is None]
    if unsettled:
        ng(f"未採点の型ラボ行 {len(unsettled)}件"
           f"（{len({d['race_key'] for d in unsettled})}レース）"
           f" — 発走前なら正常・確定後なら intraday_results を確認")
    else:
        ok("型ラボの行は全て採点済み")

    if n_skipped:
        ng(f"売った商品のうち採点できなかったもの {n_skipped}件"
           f"（bet_detail 無し／結果未確定／未知の券種）")
    else:
        ok("売った商品は全て採点できた")

    cancels = Counter(str(s["cancel_reason"] or "—")
                      for s in subs if s["deleted_at"] is not None)
    if cancels:
        out.append("  ---- 取消 " + ", ".join(f"{k}={v}" for k, v in cancels.items()))

    return out, n_ng


def _recent_median_submits(day: str) -> int | None:
    """直近7日（当日を除く）の入稿件数の中央値。開催が無い日は数えない。"""
    end = date.fromisoformat(day) - timedelta(days=1)
    start = end - timedelta(days=6)
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT wr.race_date d, count(*) n FROM netkeirin_submissions ns "
            "JOIN wt_races wr ON wr.race_key = ns.race_key "
            "WHERE ns.deleted_at IS NULL AND wr.race_date BETWEEN ? AND ? "
            "GROUP BY 1", (start.isoformat(), end.isoformat()))]
    vals = sorted(int(d["n"]) for d in rows if int(d["n"]) > 0)
    return vals[len(vals) // 2] if vals else None


def _skips(day: str) -> dict[str, int]:
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT reason_code, count(*) n FROM submission_skips "
            "WHERE race_date = ? GROUP BY 1", (day,))]
    return {str(d["reason_code"]): int(d["n"]) for d in rows}


# ─────────────────── §2 当日成績 + 参照分布 ───────────────────

def _bootstrap(pool: dict[str, list[tuple[int, int]]], mix: dict[str, int],
               n_boot: int, seed: int) -> list[tuple[float, float]]:
    """今日と同じプラン構成・同じ件数を過去から復元抽出したときの (ROI, 表示的中)。

    🔴 プランごとに引く。全体から引くと「今日たまたま E_hit が多かった」ぶんが
       比較から消える。参照にすべきは**構成を揃えた**分布。
    """
    rng = random.Random(seed)
    out: list[tuple[float, float]] = []
    usable = {p: n for p, n in mix.items() if pool.get(p)}
    if not usable:
        return out
    for _ in range(n_boot):
        bet = pay = 0
        n = hits = 0
        for plan, cnt in usable.items():
            rows = pool[plan]
            for _ in range(cnt):
                b, p = rows[rng.randrange(len(rows))]
                bet += b
                pay += p
                n += 1
                hits += int(p >= b)      # 表示的中の定義は `SoldRace.net_hit` に合わせる
        if bet and n:
            out.append((pay / bet, hits / n))
    return out


def _pct(sorted_vals: list[float], v: float) -> float:
    """`v` が分布の何%点か。"""
    lo = sum(1 for x in sorted_vals if x < v)
    return 100.0 * lo / len(sorted_vals) if sorted_vals else float("nan")


def _q(sorted_vals: list[float], p: float) -> float:
    return sorted_vals[min(len(sorted_vals) - 1, int(p * len(sorted_vals)))]


def section_today(sold, n_skipped: int, pool, n_boot: int,
                  seed: int) -> tuple[list[str], dict]:
    out: list[str] = []
    total = summarize(sold, n_no_detail=n_skipped)
    by_plan = group_by(sold, "rank_key")

    def pct(v):
        return f"{v:.1%}" if v is not None else "—"

    head = (f"  {'':<8}{'R数':>4}{'素的中':>8}{'表示的中':>9}{'投資':>10}"
            f"{'払戻':>10}{'ROI':>8}{'的中中央':>10}")
    out.append(head)
    out.append(f"  {'合計':<8}{total.n_races:>4}{pct(total.hit_rate):>8}"
               f"{pct(total.net_hit_rate):>9}{total.bet:>10,}{total.payout:>10,}"
               f"{pct(total.roi):>8}{(total.median_payout or 0):>10,}")
    out.append("  " + "-" * (len(head) - 2))
    for label, s in by_plan.items():
        out.append(f"  {label:<8}{s.n_races:>4}{pct(s.hit_rate):>8}"
                   f"{pct(s.net_hit_rate):>9}{s.bet:>10,}{s.payout:>10,}"
                   f"{pct(s.roi):>8}{(s.median_payout or 0):>10,}")

    mix = {label: s.n_races for label, s in by_plan.items()}
    boot = _bootstrap(pool, mix, n_boot, seed)
    stats: dict = {"n": total.n_races, "roi": total.roi,
                   "net_hit": total.net_hit_rate}
    if boot and total.roi is not None:
        rois = sorted(b[0] for b in boot)
        hits = sorted(b[1] for b in boot)
        stats["roi_pct"] = _pct(rois, total.roi)
        stats["hit_pct"] = _pct(hits, total.net_hit_rate or 0.0)
        out.append("")
        out.append(f"  参照分布（{BASELINE_WINDOW[0]}〜{BASELINE_WINDOW[1]} の"
                   f"ペーパー行から同じプラン構成・同じ件数を {len(boot):,}回 復元抽出）")
        out.append(f"    ROI      今日 {total.roi:6.1%}"
                   f"  → 分布の {stats['roi_pct']:5.1f}%点"
                   f"   [5% {_q(rois, .05):.1%} / 中央 {_q(rois, .50):.1%}"
                   f" / 95% {_q(rois, .95):.1%}]")
        out.append(f"    表示的中 今日 {(total.net_hit_rate or 0):6.1%}"
                   f"  → 分布の {stats['hit_pct']:5.1f}%点"
                   f"   [5% {_q(hits, .05):.1%} / 中央 {_q(hits, .50):.1%}"
                   f" / 95% {_q(hits, .95):.1%}]")
        out.append("    🔴 5〜95%の幅がそのまま「1日では何も言えない」ことの実測。"
                   "この幅の中なら今日の数字は情報を持たない。")
    else:
        missing = sorted(set(mix) - set(pool))
        joined = "、".join(missing)
        why = (f"売ったプランが参照母集団に無い（{joined}）"
               if missing else "売った商品が無い")
        out.append(f"  参照分布: 作れない — {why}")
    return out, stats


# ─────────────────── §3 外れの分解（台帳へ積む） ───────────────────

def section_breakdown(sold, live: list[dict]) -> tuple[list[str], dict]:
    """売った商品の決着を分類する。**反実仮想は出さない。**

    分類は `keirin_type_lab_outcome.finish_class` に委譲する（順当／軸2+穴／
    片軸+中位／片軸+穴／軸崩壊）。`p3_order` が無い行は分類しない——後から
    `wt_entries` を引き直すと、モデルの再学習ぶんだけ当時と違う並びになる。
    """
    out: list[str] = []
    idx = {(str(d["race_key"]), str(d["plan_key"])): d for d in live}
    counts: Counter = Counter()
    per_plan: dict[str, Counter] = {}
    n_unclassified = 0
    n_gami = 0
    gami_by_plan: Counter = Counter()
    n_hits_cls = 0
    for r in sold:
        d = idx.get((r.race_key, r.rank_key))
        cls = None
        if d is not None:
            cls = _OUTCOME.finish_class(d.get("win_combo"), d.get("p3_order"))
        if cls is None:
            n_unclassified += 1
            continue
        counts[cls] += 1
        per_plan.setdefault(r.rank_key, Counter())[cls] += 1
        n_hits_cls += int(r.hit)
        if r.hit and not r.net_hit:
            n_gami += 1
            gami_by_plan[r.rank_key] += 1

    labels = {c["key"]: c["label"] for c in _OUTCOME.FINISH_CLASSES}
    order = [c["key"] for c in _OUTCOME.FINISH_CLASSES]
    n = sum(counts.values())
    if n:
        out.append("  決着クラス（売った商品の母集団）")
        for k in order:
            v = counts.get(k, 0)
            out.append(f"    {labels[k]:<8}{v:>4}件  {v / n:6.1%}")
        out.append(f"    ガミ（当たったが払戻 < 賭け金）: {n_gami}件")
    if n_unclassified:
        out.append(f"  ⚠️ 分類できなかった商品 {n_unclassified}件"
                   f"（p3_order 無し／未確定。母集団から外している）")

    # 軸2車そろい率 × 相手カバー率 — 型ラボの的中はこの積に分解できる
    # （`docs/type_lab/carcount_2026_08_27.md`：9車 16.08% = 31.35% × 51.29%）。
    firm = counts.get("firm34", 0) + counts.get("firm_ana", 0)
    if n:
        # 🔴 分子と分母を揃える。`sold` 全体の的中を使うと、分類できなかった行の
        #    的中が「そろった中で当たった」側にだけ入って相手カバーが水増しされる。
        hit = n_hits_cls
        cover = hit / firm if firm else None
        out.append("")
        out.append(f"  分解  軸2車そろい {firm}/{n} = {firm / n:.1%}"
                   f"   × 相手カバー "
                   f"{'—' if cover is None else format(cover, '.1%')}"
                   f"   = 素の的中 {hit}/{n} = {hit / n:.1%}")
        out.append("    ※ どちらが効いているかで打ち手が変わる（軸＝モデル側／"
                   "相手＝買い目側）。1日ぶんでは判断しない。台帳に積む。")
    return out, {"counts": dict(counts), "per_plan": per_plan,
                 "n_unclassified": n_unclassified, "n_gami": n_gami,
                 "gami_by_plan": gami_by_plan}


# ─────────────────── §4 軸信頼ゲートの答え合わせ ───────────────────

def section_gate(day: str, live: list[dict]) -> list[str]:
    """ゲートで**外した側**が本当に悪いままかを毎晩並べる。

    🔴 これは前向き実地検証（2026-08-27〜）そのもの。ゲートは
       「確認窓を消費して選んだ」ため、採否は**この累積**で決める。
       当日の行は1日ぶんの点でしかないので、累積の表を必ず併記する。
    """
    out: list[str] = []

    def tally(rows: list[dict]) -> tuple[int, int, int, int]:
        n = bet = pay = hits = 0
        for d in rows:
            if d["settled_at"] is None or not d.get("budget"):
                continue
            b, p = int(d["budget"]), int(d["payout"] or 0)
            n += 1
            bet += b
            pay += p
            hits += int(p >= b)
        return n, bet, pay, hits

    def line(label: str, rows: list[dict]) -> str:
        n, bet, pay, hits = tally(rows)
        if not n:
            return f"    {label:<12}    0件"
        return (f"    {label:<12}{n:>4}件  表示的中 {hits / n:6.1%}"
                f"  ROI {pay / bet:6.1%}")

    sellable = [d for d in live if d["plan_key"] in SELL_PLANS]
    out.append("  ※ 母集団は `type_lab_picks`（モデル側の行）。売った商品ではない\n  　 ——ゲートで落ちた側は売っていないので、売った側だけでは比べられない。")
    out.append(f"  当日（{day}）")
    out.append(line("ゲート通過", [d for d in sellable if _gate_ok(d)]))
    out.append(line("ゲート落ち", [d for d in sellable if not _gate_ok(d)]))

    cum = _live_since(_GATE_TRIAL_START, day)
    cum = [d for d in cum if d["plan_key"] in SELL_PLANS]
    out.append(f"  累積（{_GATE_TRIAL_START} 〜 {day}・前向き実地検証）")
    out.append(line("ゲート通過", [d for d in cum if _gate_ok(d)]))
    out.append(line("ゲート落ち", [d for d in cum if not _gate_ok(d)]))
    out.append("    ※ 期待は「落ちた側がはっきり悪い」（20か月の台では 通過 27.2%/83.1%"
               " ↔ 落ち 18.7%/68.7%）。逆転が続くならゲートを見直す。")
    return out


#: 軸信頼ゲートの前向き実地検証の開始日（`keirin_type_lab_gate.py` の経緯より）。
_GATE_TRIAL_START = "2026-08-27"


def _live_since(start: str, end: str) -> list[dict]:
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT t.race_key, t.race_date, t.race_type, t.n_entries, t.type_label, "
            "       t.axis_sum, t.mode, t.plan_key, t.budget, t.payout, t.hit, "
            "       t.settled_at, t.generated_at, r.cup_grade "
            "FROM type_lab_picks t LEFT JOIN wt_races r ON r.race_key = t.race_key "
            "WHERE t.mode IN (?, ?) AND t.race_date BETWEEN ? AND ?",
            ("live", "live9", start, end))]
    current: dict[tuple[str, str], tuple] = {}
    for d in rows:
        key = (str(d["race_key"]), str(d["mode"]))
        gen = d.get("generated_at")
        if key not in current or (gen is not None and current[key][0] is not None
                                  and gen > current[key][0]):
            current[key] = (gen, str(d["type_label"]))
    return [d for d in rows
            if str(d["type_label"]) == current[(str(d["race_key"]),
                                                str(d["mode"]))][1]]


# ─────────────────── §5 台帳と発火条件 ───────────────────

FIELDS = ["date", "plan_key", "n", "bet", "payout", "n_hits", "n_net_hits",
          "firm34", "firm_ana", "half34", "half_ana", "broken", "n_gami"]


def append_ledger(day: str, sold, brk: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    new = not LEDGER.exists()
    rows = []
    for plan, s in group_by(sold, "rank_key").items():
        c = brk["per_plan"].get(plan, Counter())
        rows.append({
            "date": day, "plan_key": plan, "n": s.n_races, "bet": s.bet,
            "payout": s.payout, "n_hits": s.n_hits, "n_net_hits": s.n_net_hits,
            "firm34": c.get("firm34", 0), "firm_ana": c.get("firm_ana", 0),
            "half34": c.get("half34", 0), "half_ana": c.get("half_ana", 0),
            "broken": c.get("broken", 0),
            "n_gami": int(brk["gami_by_plan"].get(plan, 0)),
        })
    if not rows:
        return
    # 🔴 同じ日を二度書かない（採点が進んでから再実行することがある）。
    existing = []
    if LEDGER.exists():
        with LEDGER.open(encoding="utf-8") as f:
            existing = [r for r in csv.DictReader(f) if r["date"] != day]
    with LEDGER.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in existing:
            w.writerow({k: r.get(k, "") for k in FIELDS})
        for r in rows:
            w.writerow(r)
    if new:
        print(f"[nightly_review] 台帳を新規作成: {LEDGER}")


def section_escalate(pool) -> list[str]:
    """台帳が閾値までたまったプランだけを「検証候補」として挙げる。

    🔴 ここでも採否は決めない。挙げるのは「全期間で検証する価値がある」まで。
    """
    out: list[str] = []
    if not LEDGER.exists():
        return ["  台帳がまだ無い（次回から積み上がる）"]
    agg: dict[str, dict[str, int]] = {}
    with LEDGER.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            a = agg.setdefault(r["plan_key"], {k: 0 for k in
                                               ("n", "bet", "payout", "n_net_hits")})
            for k in a:
                a[k] += int(r.get(k) or 0)
    for plan in sorted(agg):
        a = agg[plan]
        if not a["n"]:
            continue
        roi = a["payout"] / a["bet"] if a["bet"] else 0.0
        hit = a["n_net_hits"] / a["n"]
        mark = ""
        if a["n"] >= ESCALATE_MIN_N:
            base = pool.get(plan) or []
            if base:
                b_roi = sum(p for _, p in base) / sum(b for b, _ in base)
                b_hit = sum(1 for b, p in base if p >= b) / len(base)
                if roi < b_roi * 0.85 or hit < b_hit * 0.85:
                    mark = "  ← 検証候補（参照より15%以上低い）"
        out.append(f"  {plan:<8}累積 {a['n']:>4}件  表示的中 {hit:6.1%}"
                   f"  ROI {roi:6.1%}"
                   f"{'' if a['n'] >= ESCALATE_MIN_N else f'  （{ESCALATE_MIN_N}件まで判定しない）'}"
                   f"{mark}")
    return out


# ───────────────────────────── 本体 ─────────────────────────────

def build_report(day: str, n_boot: int, append: bool = True) -> tuple[str, str, int]:
    """(全文, Discord 用の要約, 異常件数) を返す。"""
    sold, n_skipped, subs = _sold(day)
    live = _live_rows(day)
    pool = _baseline_pool()

    lines: list[str] = []
    wd = "月火水木金土日"[date.fromisoformat(day).weekday()]
    lines.append(f"# 型ラボ 夜間レビュー  {day}（{wd}）")
    lines.append(f"生成 {datetime.now():%Y-%m-%d %H:%M}  "
                 f"／ 売った商品 = netkeirin_submissions + bet_detail")
    lines.append("")

    lines.append("## §1 異常検知 — **単日で黒白がつく唯一の層。ここだけは今日直す**")
    alerts, n_ng = section_alerts(day, sold, n_skipped, subs, live)
    lines += alerts
    lines.append("")

    lines.append("## §2 当日成績（売った商品）— **単日では判断しない**")
    today, _ = section_today(sold, n_skipped, pool, n_boot,
                             seed=int(day.replace("-", "")))
    lines += today
    lines.append("")

    lines.append("## §3 外れの分解 — **台帳へ積む。反実仮想は出さない**")
    brk_lines, brk = section_breakdown(sold, live)
    lines += brk_lines
    lines.append("")

    lines.append("## §4 軸信頼ゲートの答え合わせ — **累積で見る**")
    lines += section_gate(day, live)
    lines.append("")

    if append:
        append_ledger(day, sold, brk)
    lines.append(f"## §5 台帳（{LEDGER.name}）と発火条件")
    lines += section_escalate(pool)

    total = summarize(sold, n_no_detail=n_skipped)
    head = "🔴 異常あり" if n_ng else "🟢 異常なし"
    summary = (f"**型ラボ 夜間レビュー {day}（{wd}）** {head}（NG {n_ng}件）\n"
               f"売った商品 {total.n_races}件 / 表示的中 "
               f"{(total.net_hit_rate or 0):.1%} / ROI "
               f"{(total.roi or 0):.1%} / 払戻 {total.payout:,}円\n"
               + "\n".join(a for a in alerts if a.strip().startswith("[NG]")))
    return "\n".join(lines), summary[:1900], n_ng


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("day", nargs="?", default=date.today().isoformat())
    ap.add_argument("--no-append", action="store_true")
    ap.add_argument("--no-discord", action="store_true")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    report, summary, n_ng = build_report(args.day, args.boot,
                                         append=not args.no_append)
    print(report)

    out = Path(args.out) if args.out else (
        REPO / "data" / "analysis" / "nightly" / f"{args.day}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report + "\n", encoding="utf-8")
    print(f"\n[nightly_review] 保存: {out}")

    if not args.no_discord:
        # 異常は system、通常のレビューは results へ。
        send(summary, channel="system" if n_ng else "results")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
