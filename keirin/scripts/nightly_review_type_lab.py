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
| §5 自信ありの精度 | **してはいけない** | 同日内の無作為対照の分布の中の位置 |

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
| 売るプラン | `src/type_lab.SELLABLE_PLAN_KEYS` |

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
import json
import random
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
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
from src.type_lab import SELLABLE_PLAN_KEYS, sell_plans_for   # noqa: E402

LEDGER = REPO / "data" / "analysis" / "type_lab_nightly_ledger.csv"

JST = timezone(timedelta(hours=9))

#: 参照分布を作る窓。**ペーパー行（`mode='paper'`）の窓**であって検証窓ではない。
#: 2025年は vintage オッズ・2026年は板 npz で作られており、どちらも OOS
#: （[[keirin_type_lab_vintage_odds_models_2026_08_28]]）。
BASELINE_WINDOW = ("2025-01-01", "2026-08-26")

#: 🔴 **前向き確認の起点。累積を数える層はすべてここから数える。**
#:
#: 2026-08-29 に売る商品が総入れ替えになった（旧ランク 7S/7C/7B/7M1/9C/7T1… を
#: すべて enabled=false にし、型ラボの 6プラン A_hit/B_hit/C_hit/D_hit/E_hit/F_pay
#: だけを売る）。**商品が違えば母集団も買い方も違う**ので、ここより前の累積を
#: 混ぜると「別の商品の成績」を型ラボの実績として読むことになる。
#:
#: ⚠️ ここより前のデータを消すわけではない。§2 の参照分布（ペーパー行の20か月）は
#:    比較の相手として引き続き使う。分けるのは**前向きに数え上げる累積**だけ。
REVIEW_EPOCH = "2026-08-29"

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
            "       ns.title, ns.venue_name, ns.race_no, ns.deleted_at, ns.cancel_reason, "
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
            "       t.p3_order, t.win_tf_odds, t.generated_at, t.legs, t.bet_type, "
            "       t.pred_mean_payout, r.cup_grade "
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


def _race_meta(day: str) -> dict[str, dict]:
    """レースの属性（種別・発走時刻・看板か）。台帳の軸を作るために引く。

    🔴 発走時刻は `wt_races.start_at`（UNIX秒）。**JST へ直してから**時を取る
       （そのまま `hour` を取ると9時間ずれ、夜の帯が昼に化ける）。
    """
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT race_key, race_type, start_at, cup_grade FROM wt_races "
            "WHERE race_date = ?", (day,))]
    out: dict[str, dict] = {}
    for d in rows:
        sa = d.get("start_at")
        hour = None
        if sa:
            hour = datetime.fromtimestamp(int(sa), JST).hour
        out[str(d["race_key"])] = {
            "race_type": d.get("race_type"),
            "hour": hour,
            "marquee": bool(is_fill_target(d.get("race_type"), d.get("cup_grade"))),
        }
    return out


def _gate_ok(row: dict) -> bool:
    """軸信頼ゲートを通るか。**看板は素通し**（入稿側 `_passes_axis_gate` と同一）。"""
    if is_fill_target(row.get("race_type"), row.get("cup_grade")):
        return True
    return bool(_GATE.passes_axis_gate(
        str(row["plan_key"]),
        float(row["axis_sum"]) if row.get("axis_sum") is not None else None,
        int(row["n_entries"]) if row.get("n_entries") is not None else None))


def _sellable(d: dict) -> bool:
    """いまの売り方でそのプランを売るか（車数・種別まで見る）。

    🔴 **`SELLABLE_PLAN_KEYS` に入っているかだけで判定しない。** 9車の型F は
       決勝で `F_pay`・それ以外で `F_hit` と種別で分かれる（2026-08-30〜）。
    """
    want = {p.key for p in sell_plans_for(
        str(d.get("type_label") or ""), int(d.get("n_entries") or 7),
        d.get("race_type"))}
    return str(d.get("plan_key")) in want


def _baseline_pool() -> dict[tuple[str, int], list[tuple[int, int]]]:
    """参照分布の母集団。**(プラン, 車数)** → `(賭け金, 払戻)` の一覧。

    🔴 **7車だけで作らない**（2026-08-30 是正）。それまで `mode='paper'`（＝7車）
       しか見ておらず、**9車を売った日を7車の物差しで測っていた**。
       2026-08-30 は売った商品の **38% が9車**で、7車の参照
       （表示的中 21.6% / ROI 75.3%）に対し 9車は 19.4% / 75.9%。
       期待を高く置いたまま「分布の 6.5%点」と報告していた。
    🔴 **本番と同じゲートを掛けてから積む。** 掛けないと「ゲートで底を外した今日」を
       「底も含む過去」と比べることになり、今日が実力より良く見える。
    """
    lo, hi = BASELINE_WINDOW
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT t.plan_key, t.type_label, t.axis_sum, t.n_entries, t.race_type, "
            "       t.budget, t.payout, r.cup_grade "
            "FROM type_lab_picks t LEFT JOIN wt_races r ON r.race_key = t.race_key "
            "WHERE t.mode IN (?, ?) AND t.race_date BETWEEN ? AND ? "
            "  AND t.settled_at IS NOT NULL AND t.budget > 0",
            ("paper", "paper9", lo, hi))]
    pool: dict[tuple[str, int], list[tuple[int, int]]] = {}
    for d in rows:
        if not _sellable(d) or not _gate_ok(d):
            continue
        pool.setdefault((str(d["plan_key"]), int(d["n_entries"] or 7)), []).append(
            (int(d["budget"]), int(d["payout"] or 0)))
    return pool


def _band_baseline() -> dict[str, dict]:
    """プラン別の「決着帯」の参照分布（paper・本番と同じゲート適用後）。

    🔴 **狙った帯で決着したかは `win_tf_odds`（確定三連単オッズ）で見る。**
       券種にも的中にも関係なく全行に入っている唯一の「レースの荒れ具合」で、
       `settle_type_lab_picks.py` がまさにこの答え合わせのために入れている
       （三連複プランの行にも三連単の値を入れて型どうしを同じ物差しで比べる）。
    🔴 `final_odds` を使ってはいけない——**的中時しか入らない**ので、
       外れたレースの荒れ具合が測れず「狙い違い」と「買い目違い」を分離できない。
    """
    lo, hi = BASELINE_WINDOW
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT t.plan_key, t.type_label, t.axis_sum, t.n_entries, t.race_type, "
            "       t.win_tf_odds, "
            "       r.cup_grade FROM type_lab_picks t "
            "LEFT JOIN wt_races r ON r.race_key = t.race_key "
            "WHERE t.mode IN (?, ?) AND t.race_date BETWEEN ? AND ? "
            "  AND t.settled_at IS NOT NULL AND t.win_tf_odds IS NOT NULL",
            ("paper", "paper9", lo, hi))]
    out: dict[str, dict] = {}
    for d in rows:
        if not _sellable(d) or not _gate_ok(d):
            continue
        b = out.setdefault(str(d["plan_key"]), {"bands": Counter(), "odds": []})
        band = _OUTCOME.payout_band(d["win_tf_odds"])
        if band:
            b["bands"][band] += 1
        b["odds"].append(float(d["win_tf_odds"]))
    for b in out.values():
        b["odds"].sort()
        b["median"] = b["odds"][len(b["odds"]) // 2] if b["odds"] else None
        n = sum(b["bands"].values())
        b["n"] = n
        # 狙い帯 = 参照でそのプランが最も多く落ちる帯（＋隣接1帯まで許容）。
        b["target"] = b["bands"].most_common(1)[0][0] if n else None
    return out


def section_landing(sold, live: list[dict], base: dict[str, dict],
                    titles: dict[str, str] | None = None) -> list[str]:
    """狙ったオッズ帯で決着したか（2026-08-29・ユーザー要望）。

    型ラボの各プランは**配当帯を狙って**買い目を組み、**タイトルでそれを客に宣言する**
    （本線の三連単 → 中配当 → 混戦 → 高配当 → 一撃の三連単）。
    だから外れには2種類ある:

      ① **帯は想定どおりだったが目を外した** → 買い目側の問題（相手の取り方）
      ② **そもそも想定と違う帯で決着した**   → 型判定・荒れ度側の問題

    ①と②は打ち手がまったく違うので、混ぜて「外れ○件」と数えても改善に繋がらない。

    🔴 1日ぶんでは判断しない。参照分布との**ずれの向き**だけを見て台帳に積む。

    🔴 **タイトルは「その1レースの約束」ではなく「分布の約束」**（2026-08-29 実測）。
       確認窓で「本線の三連単」でも 100倍以上が 18〜21%、「一撃の三連単」でも
       30倍未満が 31% ある。序列（中央 A 18倍 < B 25 < C 32 < D 47 < E 52 < F 57倍）は
       両窓で完全に単調なので**設計としては効いている**が、1レース単位では帯が重なる。
       ここで見るのは分布のずれであって、個別レースの「約束違反」ではない。
    """
    out: list[str] = []
    idx = {(str(d["race_key"]), str(d["plan_key"])): d for d in live}
    order = [b["key"] for b in _OUTCOME.PAYOUT_BANDS]
    labels = {b["key"]: b["label"] for b in _OUTCOME.PAYOUT_BANDS}
    rows: dict[str, dict] = {}
    for r in sold:
        d = idx.get((r.race_key, r.rank_key))
        if d is None or d.get("win_tf_odds") is None:
            continue
        b = _OUTCOME.payout_band(d["win_tf_odds"])
        if not b:
            continue
        g = rows.setdefault(r.rank_key, {"bands": Counter(), "odds": [],
                                         "hit_in": 0, "n_in": 0, "n": 0})
        g["bands"][b] += 1
        g["odds"].append(float(d["win_tf_odds"]))
        g["n"] += 1
        tgt = (base.get(r.rank_key) or {}).get("target")
        if tgt and _near(order, b, tgt):
            g["n_in"] += 1
            g["hit_in"] += int(r.net_hit)
    if not rows:
        return ["  決着帯を出せる商品が無い（`win_tf_odds` 未確定）"]

    titles = titles or {}
    out.append(f"  {'plan':<7}{'タイトル':<14}{'狙い帯':<10}{'当日の決着帯':<22}"
               f"{'当日中央':>9}{'参照中央':>9}{'狙い帯で決着':>13}")
    tot_in = tot_n = tot_hit_in = 0
    for plan in sorted(rows):
        g = rows[plan]
        b = base.get(plan) or {}
        g["odds"].sort()
        med = g["odds"][len(g["odds"]) // 2]
        dist = " ".join(f"{labels[k]}×{g['bands'][k]}" for k in order if g["bands"][k])
        tgt = b.get("target")
        base_rate = (sum(v for k, v in b.get("bands", {}).items()
                         if _near(order, k, tgt)) / b["n"]) if b.get("n") else None
        ref = f"（参照 {base_rate:.0%}）" if base_rate is not None else ""
        out.append(f"  {plan:<7}{titles.get(plan, '—')[:13]:<14}"
                   f"{labels.get(tgt, '—'):<10}{dist:<22}"
                   f"{med:>8,.1f}倍{(b.get('median') or 0):>8,.1f}倍"
                   f"{g['n_in']:>4}/{g['n']}{ref}")
        tot_in += g["n_in"]
        tot_n += g["n"]
        tot_hit_in += g["hit_in"]
    if tot_n:
        out.append("")
        out.append(f"  狙い帯（±1帯）で決着 {tot_in}/{tot_n} = {tot_in / tot_n:.1%}")
        if tot_in:
            out.append(f"  そのうち的中 {tot_hit_in}/{tot_in} = {tot_hit_in / tot_in:.1%}")
        out.append("    ※ 帯が合っているのに外れる＝**買い目側**（相手の取り方）。"
                   "帯自体が外れる＝**型判定・荒れ度側**。打ち手が違うので分けて積む。")
    return out


def section_bet_band(sold, live: list[dict], titles: dict[str, str]) -> list[str]:
    """**買った買い目そのもの**が狙った帯にあったかを確定オッズで答え合わせする。

    §3.5（レースがどの帯で決着したか）は**結果**なので制御できない。
    こちらは「何倍の目を何点買ったか」＝**完全に自分で決めた量**で、
    狙いとずれていれば買い目の作り方を直せる。

    見るもの（プランごと）:

      計画   入稿時の `pred_mean_payout`（平均想定払戻）と `min(stake × pred_odds)`
      確定   同じ買い目を**確定オッズ**で引き直した `stake × odds` の中央と最小
      実/予  その比。**1 を大きく割るなら予測オッズが上振れている**

    🔴 予測オッズは系統的に上振れる（勝者の呪い）。全体で平均 ×1.10・
       6倍超の帯では 実/予 0.64（`docs/type_lab/time_and_race_type_2026_08_29.md`）。
       **プランごとにこの比を毎晩見ておくと、モデル配布漏れや帯の偏りが早く出る。**
    🔴 「最低払戻」の下振れ幅は点数で変わる。正本は
       `backend/src/services/keirin_payout_floor.py`（券種×点数の表）。
       ここでは実測の最小をそのまま出す（推定値と実測を混ぜない）。
    """
    out: list[str] = []
    idx = {(str(d["race_key"]), str(d["plan_key"])): d for d in live}
    want: dict[tuple[str, str], set[str]] = {}
    rows: list[dict] = []
    for r in sold:
        d = idx.get((r.race_key, r.rank_key))
        if d is None:
            continue
        legs = d.get("legs")
        if isinstance(legs, str):
            legs = json.loads(legs or "[]")
        if not legs:
            continue
        rows.append({"plan": r.rank_key, "race_key": r.race_key,
                     "bet_type": str(d.get("bet_type") or ""), "legs": legs,
                     "pred_mean": d.get("pred_mean_payout")})
        want.setdefault((r.race_key, str(d.get("bet_type") or "")), set()).update(
            str(x["combo"]) for x in legs)
    if not rows:
        return ["  買い目を引ける商品が無い"]

    final: dict[tuple[str, str, str], float] = {}
    with get_connection() as c:
        for (rk, bt), combos in want.items():
            cs = sorted(combos)
            q = ("SELECT combination, odds_value FROM wt_odds WHERE race_key = ? "
                 "AND bet_type = ? AND combination IN (%s)" % ",".join("?" * len(cs)))
            for x in c.execute(q, tuple([rk, bt] + cs)):
                d = dict(x)
                final[(rk, bt, str(d["combination"]))] = float(d["odds_value"])

    by_plan: dict[str, dict] = {}
    for r in rows:
        g = by_plan.setdefault(r["plan"], {"n": 0, "pts": [], "plan_mean": [],
                                           "plan_min": [], "real_med": [],
                                           "real_min": [], "bands": Counter(),
                                           "n_missing": 0})
        pay_plan = [float(x["stake"]) * float(x.get("pred_odds") or 0)
                    for x in r["legs"]]
        real = [(float(x["stake"]),
                 final.get((r["race_key"], r["bet_type"], str(x["combo"]))))
                for x in r["legs"]]
        if any(o is None for _, o in real):
            g["n_missing"] += 1
            continue
        pay_real = sorted(st * o for st, o in real)
        g["n"] += 1
        g["pts"].append(len(r["legs"]))
        if r["pred_mean"]:
            g["plan_mean"].append(float(r["pred_mean"]))
        g["plan_min"].append(min(pay_plan) if pay_plan else 0.0)
        g["real_med"].append(pay_real[len(pay_real) // 2])
        g["real_min"].append(pay_real[0])
        for _, o in real:
            b = _OUTCOME.payout_band(o)
            if b:
                g["bands"][b] += 1

    def med(xs: list[float]) -> float:
        return sorted(xs)[len(xs) // 2] if xs else 0.0

    labels = {b["key"]: b["label"] for b in _OUTCOME.PAYOUT_BANDS}
    order = [b["key"] for b in _OUTCOME.PAYOUT_BANDS]
    out.append(f"  {'plan':<7}{'タイトル':<14}{'点':>3}{'計画 平均':>10}{'計画 最低':>10}"
               f"{'確定 中央':>10}{'確定 最低':>10}{'実/予':>7}   買い目の確定オッズ帯")
    for plan in sorted(by_plan):
        g = by_plan[plan]
        if not g["n"]:
            continue
        pm, rm = med(g["plan_mean"]), med(g["real_med"])
        nb = sum(g["bands"].values())
        dist = " ".join(f"{labels[b]} {g['bands'][b] / nb:.0%}"
                        for b in order if g["bands"][b])
        out.append(f"  {plan:<7}{titles.get(plan, '—')[:13]:<14}"
                   f"{med([float(x) for x in g['pts']]):>3.0f}"
                   f"{pm:>9,.0f}円{med(g['plan_min']):>9,.0f}円"
                   f"{rm:>9,.0f}円{med(g['real_min']):>9,.0f}円"
                   f"{(rm / pm if pm else 0):>7.2f}   {dist}")
    n_missing = sum(g["n_missing"] for g in by_plan.values())
    if n_missing:
        out.append(f"  ⚠️ 確定オッズを引けなかった商品 {n_missing}件"
                   f"（板に無い目・未確定。**0 で埋めずに母集団から外している**）")
    out.append("    ※ ここは**自分で決めた量**なので、狙いとずれていれば買い目の作り方を直せる。"
               "実/予 が 1 を大きく割るなら予測オッズが上振れている。")
    return out


def _near(order: list[str], band: str | None, target: str | None) -> bool:
    """`band` が `target` と同じか隣（±1帯）か。

    🔴 帯はちょうど1桁ずつ広いので、境界のすぐ外を「狙い違い」と数えると
       ほとんどの日が狙い違いになる。隣までは「想定どおり」として扱う。
    """
    if band is None or target is None:
        return False
    try:
        return abs(order.index(band) - order.index(target)) <= 1
    except ValueError:
        return False


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


def section_today(sold, n_skipped: int, pool, n_boot: int, seed: int,
                  cars_of: dict[str, int] | None = None) -> tuple[list[str], dict]:
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

    # 🔴 **車数まで込みで構成を揃える**（2026-08-30）。9車は7車より当たりにくいので、
    #    プランだけで揃えると 9車を売った日の期待が高く出る。
    mix: dict[tuple[str, int], int] = {}
    for r in sold:
        cars = int((cars_of or {}).get(r.race_key) or 7)
        mix[(r.rank_key, cars)] = mix.get((r.rank_key, cars), 0) + 1
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
        joined = "、".join(sorted(f"{p}/{c}車" for p, c in set(mix) - set(pool)))
        missing = joined
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

    🔴 これは前向き実地検証そのもの。ゲートは
       「確認窓を消費して選んだ」ため、採否は**この累積**で決める。
       当日の行は1日ぶんの点でしかないので、累積の表を必ず併記する。

    ⚠️ 累積は `REVIEW_EPOCH`（＝型ラボ全面移行日）から数え直す。ゲートの試験自体は
       2026-08-27 に始まっているが、その2日間は**売っていた商品が旧ランク**で
       母集団が違う。混ぜると別商品の成績が混入する。
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

    sellable = [d for d in live if d["plan_key"] in SELLABLE_PLAN_KEYS]
    out.append("  ※ 母集団は `type_lab_picks`（モデル側の行）。売った商品ではない\n  　 ——ゲートで落ちた側は売っていないので、売った側だけでは比べられない。")
    out.append(f"  当日（{day}）")
    out.append(line("ゲート通過", [d for d in sellable if _gate_ok(d)]))
    out.append(line("ゲート落ち", [d for d in sellable if not _gate_ok(d)]))

    cum = _live_since(REVIEW_EPOCH, day)
    cum = [d for d in cum if d["plan_key"] in SELLABLE_PLAN_KEYS]
    out.append(f"  累積（{REVIEW_EPOCH} 〜 {day}・前向き実地検証）")
    out.append(line("ゲート通過", [d for d in cum if _gate_ok(d)]))
    out.append(line("ゲート落ち", [d for d in cum if not _gate_ok(d)]))
    out.append("    ※ 期待は「落ちた側がはっきり悪い」（20か月の台では 通過 27.2%/83.1%"
               " ↔ 落ち 18.7%/68.7%）。逆転が続くならゲートを見直す。")
    return out


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


# ─────────────────── §5 「自信あり」フラグの精度 ───────────────────

#: 「自信あり」が実際に付き始めた日（2026-08-13 は選定が走ったが0件）。
CONFIDENT_SINCE = "2026-08-14"


def section_confident(day: str, n_boot: int, seed: int) -> list[str]:
    """1日1つしか付けられない「自信あり」が、その日の他の商品より良かったか。

    🔴 **同日内の無作為対照を必ず置く。** 「自信ありの表示的中は 25%」だけでは
       何も言えない。比べる相手は全期間の平均ではなく
       **その日に売った商品から無作為に1件選んだとき**で、
       日ごとの当たりやすさ（開催の質・件数）を対照側にも同じだけ入れないと、
       良い日が多かっただけの差を実力と読む。

    🔴 **指標の世代を混ぜない。** 2026-08-28 までは EV（三連複の
       Σ(的中確率×賭け金×オッズ)÷総賭け金）、型ラボへ全面移行した 2026-08-29
       からは **Σp（買い目の的中確率の合計）**。`confident_ev` 列は同じだが
       中身が別物で、大きさも桁が違う（1.3〜2.9 ↔ 0.32）。
       世代の判定は日付ではなく **売ったプランが `SELLABLE_PLAN_KEYS` か**で行う
       （移行が段階的でも自動で分かれる）。

    ⚠️ 1日1件なので件数はゆっくりしか増えない。**当日の1件では絶対に判断しない。**
    """
    out: list[str] = []
    with get_connection() as c:
        subs = [dict(r) for r in c.execute(
            "SELECT ns.race_key, ns.rank_key, ns.origin, ns.bet_detail, "
            "       ns.is_confident, ns.confident_ev, wr.race_date "
            "FROM netkeirin_submissions ns "
            "JOIN wt_races wr ON wr.race_key = ns.race_key "
            "WHERE ns.deleted_at IS NULL AND wr.race_date BETWEEN ? AND ?",
            (CONFIDENT_SINCE, day))]
    if not subs:
        return ["  対象期間の入稿が無い"]
    finishes, payouts = _results(sorted({s["race_key"] for s in subs}))
    races, _ = build_sold_races(subs, finishes, payouts)
    flag = {(str(s["race_key"]), str(s["rank_key"])): bool(s["is_confident"])
            for s in subs}

    by_day: dict[str, list] = {}
    for r in races:
        by_day.setdefault(str(r.race_date), []).append(r)

    eras: dict[str, list[str]] = {}
    for d, rows in by_day.items():
        picked = [r for r in rows if flag.get((r.race_key, r.rank_key))]
        if not picked:
            continue
        # 世代は「その日の自信ありが型ラボの商品か」で決める。
        era = "Σp（型ラボ）" if picked[0].rank_key in SELLABLE_PLAN_KEYS else "EV（旧ランク・三連複）"
        eras.setdefault(era, []).append(d)

    n_no_flag = sum(1 for d, rows in by_day.items()
                    if rows and not any(flag.get((r.race_key, r.rank_key))
                                        for r in rows))
    for era in sorted(eras, reverse=True):
        days = sorted(eras[era])
        # 起点より前の世代は**参考**（別指標・別商品）。前向きに数える対象ではない。
        ref = "" if days[-1] >= REVIEW_EPOCH else "（参考・起点より前の別商品）"
        picked = [r for d in days for r in by_day[d]
                  if flag.get((r.race_key, r.rank_key))]
        s = summarize(picked)
        out.append(f"  【{era}】{days[0]} 〜 {days[-1]}・{len(days)}日{ref}")
        out.append(f"    自信あり   {s.n_races:>3}件  表示的中 "
                   f"{(s.net_hit_rate or 0):6.1%}  ROI {(s.roi or 0):6.1%}"
                   f"  払戻 {s.payout:,}円")
        rng = random.Random(seed)
        boot_roi: list[float] = []
        boot_hit: list[float] = []
        for _ in range(n_boot):
            bet = pay = n = hits = 0
            for d in days:
                rows = by_day[d]
                r = rows[rng.randrange(len(rows))]
                bet += r.bet
                pay += r.payout
                n += 1
                hits += int(r.net_hit)
            if bet and n:
                boot_roi.append(pay / bet)
                boot_hit.append(hits / n)
        if boot_roi:
            boot_roi.sort()
            boot_hit.sort()
            out.append(f"    無作為対照      表示的中 [5% {_q(boot_hit, .05):.1%}"
                       f" / 中央 {_q(boot_hit, .50):.1%}"
                       f" / 95% {_q(boot_hit, .95):.1%}]"
                       f"  ROI [5% {_q(boot_roi, .05):.1%}"
                       f" / 中央 {_q(boot_roi, .50):.1%}"
                       f" / 95% {_q(boot_roi, .95):.1%}]")
            out.append(f"    → 自信ありは 表示的中 "
                       f"{_pct(boot_hit, s.net_hit_rate or 0):.0f}%点"
                       f" / ROI {_pct(boot_roi, s.roi or 0):.0f}%点"
                       f"（同じ日から無作為に1件選ぶ {len(boot_roi):,}通りの中で）")
    if n_no_flag:
        out.append(f"  ⚠️ 自信ありが付かなかった日 {n_no_flag}日"
                   f"（選定が落ちた／対象の買い目が無かった）")
    out.append("    ※ 1日1件しか付かない。**当日の1件では判断しない。**"
               "対照の 5〜95% の外へ出て、かつ日数が伸びてから見ること。")
    return out


# ─────────────────── §6 台帳と発火条件 ───────────────────

#: 台帳の列。**1行 = 1日 × 1軸 × 1値**。
#:
#: 🔴 プラン別だけでは足りない（2026-08-29 に拡張）。20か月の台で
#:    **レース種別**（表示的中の順位が両窓で Spearman +0.907・チャレンジ予選 36% ↔
#:    一般 14%）と**発走時刻帯**（18〜20時が両窓で最弱）という再現する差が見つかったが、
#:    どちらの窓も一度は別の目的で使われており **前向きにしか確定させられない**。
#:    積んでいなければ半年後にも同じことしか言えないので、軸ごとに毎晩積む。
#: 🔴 決着クラスの内訳は `dim="plan"` の行にだけ入れる（他の軸で足すと二重に数える）。
FIELDS = ["date", "dim", "key", "n", "bet", "payout", "n_hits", "n_net_hits",
          "firm34", "firm_ana", "half34", "half_ana", "broken", "n_gami"]

#: 積む軸。値の作り方は `_segments()`。
LEDGER_DIMS = ("plan", "race_type", "band", "marquee", "payout_band")


def _band(hour: int | None) -> str:
    """発走時刻帯。境界は `backend/src/api/keirin_meeting.py` の 9/12/18 に合わせる
    （そちらは開催の第1R、ここはレース自身の発走時刻を見る）。"""
    if hour is None:
        return "unknown"
    for lo, hi, label in ((0, 11, "〜10時"), (11, 15, "11〜14時"),
                          (15, 18, "15〜17時"), (18, 21, "18〜20時"),
                          (21, 24, "21時〜")):
        if lo <= hour < hi:
            return label
    return "unknown"


def _segments(plan: str, meta: dict | None) -> list[tuple[str, str]]:
    """1商品が属する (軸, 値) の一覧。"""
    out = [("plan", plan)]
    if meta is None:
        return out
    rt = str(meta.get("race_type") or "—")
    out.append(("race_type", rt))
    out.append(("band", _band(meta.get("hour"))))
    out.append(("marquee", "看板" if meta.get("marquee") else "看板でない"))
    # 🔴 決着帯（確定三連単オッズ）。**狙った帯で決着したか**を積むための軸で、
    #    プラン別の狙い帯と突き合わせて初めて意味を持つ（§3.5）。
    pb = meta.get("payout_band")
    if pb:
        out.append(("payout_band", pb))
    return out


def append_ledger(day: str, sold, brk: dict, race_meta: dict | None = None) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    new = not LEDGER.exists()
    race_meta = race_meta or {}
    buckets: dict[tuple[str, str], dict] = {}
    for r in sold:
        meta = race_meta.get(r.race_key)
        for dim, key in _segments(r.rank_key, meta):
            b = buckets.setdefault((dim, key), {k: 0 for k in FIELDS[3:]})
            b["n"] += 1
            b["bet"] += r.bet
            b["payout"] += r.payout
            b["n_hits"] += int(r.hit)
            b["n_net_hits"] += int(r.net_hit)
    # 決着クラスとガミは plan 軸にだけ入れる（他の軸へ足すと二重計上になる）。
    for plan, c in brk["per_plan"].items():
        b = buckets.get(("plan", plan))
        if b is None:
            continue
        for k in ("firm34", "firm_ana", "half34", "half_ana", "broken"):
            b[k] = c.get(k, 0)
        b["n_gami"] = int(brk["gami_by_plan"].get(plan, 0))
    rows = [{"date": day, "dim": dim, "key": key, **vals}
            for (dim, key), vals in sorted(buckets.items())]
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
    """台帳が閾値までたまった**軸の値**だけを「検証候補」として挙げる。

    🔴 ここでも採否は決めない。挙げるのは「全期間で検証する価値がある」まで。
    🔴 **プラン以外の軸には参照分布が無い**（`pool` はプラン別の paper 行）。
       種別・時間帯・看板は**同じ軸の他の値との比較**しかできないので、
       件数が足りたときに「最下位がどれだけ離れているか」だけを出す。
    """
    out: list[str] = []
    if not LEDGER.exists():
        return ["  台帳がまだ無い（次回から積み上がる）"]
    agg: dict[tuple[str, str], dict[str, int]] = {}
    with LEDGER.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            k = (r.get("dim") or "plan", r.get("key") or r.get("plan_key") or "—")
            a = agg.setdefault(k, {x: 0 for x in
                                   ("n", "bet", "payout", "n_net_hits")})
            for x in a:
                a[x] += int(r.get(x) or 0)

    def rate(a: dict) -> tuple[float, float]:
        return (a["n_net_hits"] / a["n"] if a["n"] else 0.0,
                a["payout"] / a["bet"] if a["bet"] else 0.0)

    for dim in LEDGER_DIMS:
        items = sorted(((k, v) for (d, k), v in agg.items() if d == dim and v["n"]),
                       key=lambda kv: -rate(kv[1])[0])
        if not items:
            continue
        label = {"plan": "プラン", "race_type": "レース種別",
                 "band": "発走時刻帯", "marquee": "看板か",
                 "payout_band": "決着帯（確定三連単）"}[dim]
        out.append(f"  【{label}】")
        for k, a in items:
            hit, roi = rate(a)
            mark = ""
            if dim == "plan" and a["n"] >= ESCALATE_MIN_N:
                base = [x for (pk, _c), v in pool.items() if pk == k for x in v]
                if base:
                    b_roi = sum(p for _, p in base) / sum(b for b, _ in base)
                    b_hit = sum(1 for b, p in base if p >= b) / len(base)
                    if roi < b_roi * 0.85 or hit < b_hit * 0.85:
                        mark = "  ← 検証候補（参照より15%以上低い）"
            todo = "" if a["n"] >= ESCALATE_MIN_N else \
                f"  （{ESCALATE_MIN_N}件まで判定しない）"
            out.append(f"    {k:<14}累積 {a['n']:>4}件  表示的中 {hit:6.1%}"
                       f"  ROI {roi:6.1%}{todo}{mark}")
    out.append("    ※ 種別・時間帯・看板には参照分布が無い（paper 側にゲート後の"
               "同一軸が無い）。**同じ軸の中の並びだけ**を見ること。")
    return out


# ───────────────────────────── 本体 ─────────────────────────────

def build_report(day: str, n_boot: int, append: bool = True) -> tuple[str, str, int]:
    """(全文, Discord 用の要約, 異常件数) を返す。"""
    sold, n_skipped, subs = _sold(day)
    live = _live_rows(day)
    pool = _baseline_pool()
    race_meta = _race_meta(day)
    # 決着帯はレース単位の量（`win_tf_odds` は同じレースなら全プラン行で同じ）。
    # `wt_races` には無いので型ラボの行から拾って `race_meta` へ足す。
    for d in live:
        if d.get("win_tf_odds") is None:
            continue
        m = race_meta.get(str(d["race_key"]))
        if m is not None and "payout_band" not in m:
            m["payout_band"] = _OUTCOME.payout_band(d["win_tf_odds"])

    lines: list[str] = []
    wd = "月火水木金土日"[date.fromisoformat(day).weekday()]
    lines.append(f"# 型ラボ 夜間レビュー  {day}（{wd}）")
    lines.append(f"生成 {datetime.now():%Y-%m-%d %H:%M}  "
                 f"／ 売った商品 = netkeirin_submissions + bet_detail")
    lines.append(f"前向き確認の起点 {REVIEW_EPOCH}（型ラボ全面移行日）"
                 f"— §3〜§5 の累積はここから数える。"
                 f"§2 の参照分布だけは20か月のペーパー行を相手にする。")
    lines.append("")

    lines.append("## §1 異常検知 — **単日で黒白がつく唯一の層。ここだけは今日直す**")
    alerts, n_ng = section_alerts(day, sold, n_skipped, subs, live)
    lines += alerts
    lines.append("")

    lines.append("## §2 当日成績（売った商品）— **単日では判断しない**")
    cars_of = {str(d["race_key"]): int(d.get("n_entries") or 7) for d in live}
    today, _ = section_today(sold, n_skipped, pool, n_boot,
                             seed=int(day.replace("-", "")), cars_of=cars_of)
    lines += today
    lines.append("")

    lines.append("## §3 外れの分解 — **台帳へ積む。反実仮想は出さない**")
    brk_lines, brk = section_breakdown(sold, live)
    lines += brk_lines
    lines.append("")

    lines.append("## §3.5 狙ったオッズ帯で決着したか — **外れを2種類に分ける**")
    # 🔴 タイトルは**実際に入稿した文言**を使う（客が見たものが正本）。
    #    コード側の生成規則を写すと、文言を変えた日から静かに食い違う。
    titles = {}
    for x in subs:
        if x["deleted_at"] is None and x.get("title"):
            titles.setdefault(str(x["rank_key"]),
                              str(x["title"]).split("｜")[0])
    lines += section_landing(sold, live, _band_baseline(), titles)
    lines.append("")

    lines.append("## §3.6 買った買い目は狙った帯にあったか — **こちらは制御できる量**")
    lines += section_bet_band(sold, live, titles)
    lines.append("")

    lines.append("## §4 軸信頼ゲートの答え合わせ — **累積で見る**")
    lines += section_gate(day, live)
    lines.append("")

    lines.append("## §5 「自信あり」フラグの精度 — **同日内の無作為対照と比べる**")
    lines += section_confident(day, n_boot, seed=int(day.replace("-", "")))
    lines.append("")

    if append:
        append_ledger(day, sold, brk, race_meta)

    lines.append(f"## §6 台帳（{LEDGER.name}）と発火条件")
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
