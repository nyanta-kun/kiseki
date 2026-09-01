#!/usr/bin/env python3
"""7C 軸2差し替え（◎◯一致時に印から離す）を vintage で再検証（2026-08-26・ユーザー依頼）。

## 問い

> 二軸を取る際に WT◎◯ と一致した場合、印置き換えをするとしていた。しかし結果 ◎△
> となってもそこまでオッズが変わらない様に感じる。該当商品のレース条件において、
> **指数1位2位とした場合、的中率・ROI が変わるか**、**WT◎◯と一致するのはどれくらいの率か**。

対象は `strategy_wt.rank_7c_reselect_axis2_off_marks`（2026-08-19 採用・2026-08-21 に
落差 0.30 の停止条件を追加）。

## 腕（軸1は全腕で不変・p3 1位）

| 腕 | 軸2 |
|---|---|
| `TOP2` | **指数（p3）2位のまま**＝差し替えなし |
| `PROD` | 現行本番（◎◯一致 ∧ 落差 < 0.30 のとき ◎◯以外の p3 1位へ） |
| `SWAP_ALL` | 落差の停止条件なし（2026-08-19〜21 の挙動） |

## 作法

- 🔴 予測は **vintage walk-forward**（`data/exp_cache/wf_preds_*.pkl`）。
  `wt_entries.pred_top3_pct` は backfill 値で model-vintage look-ahead。
- 🔴 買い方・ゲートは**本番関数をそのまま通す**（`rank_7c_select_legs` /
  `rank_7c_buy_plan` / `rank_7c_accepts`）。相手も点数も軸2に連動して変わる。
- 🔴 配分は本番と同じ**傾斜配分**（`rebuild_stakes.stakes_for_combos`・p3 単独）。
  均等割りで測ると表示的中（ガミ除き）が別物になる。
- 探索窓 2024-07〜2025-12 / 確認窓 2026-01〜（`--swap` で逆向き）。年をまたぐ独立窓。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_7c_marks/axis2_swap.py
    PYTHONPATH=. .venv/bin/python scripts/exp_7c_marks/axis2_swap.py --swap
"""
from __future__ import annotations

import argparse
import glob
import os
import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.database import get_connection                      # noqa: E402
from src.p3_calibration import calibrated_p3_sum_top2        # noqa: E402
from src.rebuild_stakes import stakes_for_combos             # noqa: E402
from src.result_top3 import winning_trios                    # noqa: E402
from src.odds_prediction import (                             # noqa: E402
    OddsPredictionUnavailable, conservative_multiplier, predict_board,
)
from src.stake_allocation import (                            # noqa: E402
    MIN_EXPECTED_PAYOUT_BY_RANK, MIN_MEAN_PAYOUT, MIN_POINT_ODDS,
    cheap_point_odds, expected_payout_floor, mean_payout_of_lines,
    tilted_stakes,
)
from src.strategy_wt import (                                # noqa: E402
    RACE_BUDGET, rank_7c_accepts, rank_7c_buy_plan, rank_7c_is_lowpay_pattern,
    rank_7c_reselect_axis2_off_marks, rank_7c_select_axis, rank_7c_select_legs,
)

ARMS = ("TOP2", "TOP2-案E無", "PROD", "SWAP_ALL")
WF_GLOB = "data/exp_cache/wf_preds_*.pkl"
SPLIT = "2026-01-01"
# 🔴 WT の印は 1..4。**3 は ▲ で、最弱は 4（△）**。
#    `strategy_wt` の `wt_ana` は名前に反して **mark 3（▲）**を指す。
MARK_NAME = {0: "無印", 1: "◎", 2: "◯", 3: "▲", 4: "△"}


# ── 読み込み ───────────────────────────────────────────────────────────────
def load_entries_p3(keys: list[str]) -> dict[str, dict[str, dict[int, float]]]:
    """backfill された `wt_entries.pred_top3_pct / pred_win_pct`（look-ahead）。

    🔴 比較用にしか使わない。2026-08-19 の採用時の実測がこの出所だったので、
       出所だけを差し替えたときに結論が変わるかを見るために持っている。
    """
    out: dict[str, dict[str, dict[int, float]]] = defaultdict(
        lambda: {"p3": {}, "pw": {}})
    with get_connection() as c:
        for ch in _chunks(keys):
            q = ("SELECT race_key, frame_no, pred_top3_pct, pred_win_pct "
                 "FROM wt_entries WHERE pred_top3_pct IS NOT NULL "
                 "  AND pred_win_pct IS NOT NULL AND race_key IN (%s)"
                 % ",".join("?" * len(ch)))
            for r in c.execute(q, ch).fetchall():
                out[r["race_key"]]["p3"][int(r["frame_no"])] = \
                    float(r["pred_top3_pct"]) / 100.0
                out[r["race_key"]]["pw"][int(r["frame_no"])] = \
                    float(r["pred_win_pct"]) / 100.0
    return dict(out)


def load_vintage() -> dict[str, dict[str, dict[int, float]]]:
    """{race_key: {"p3": {車番: 確率}, "pw": {...}}}（vintage walk-forward）。"""
    out: dict[str, dict[str, dict[int, float]]] = defaultdict(
        lambda: {"p3": {}, "pw": {}})
    files = sorted(glob.glob(WF_GLOB))
    if not files:
        raise SystemExit(f"[error] {WF_GLOB} が無い")
    for f in files:
        d = pickle.load(open(f, "rb"))
        for rk, fn, p3, pw in zip(d["race_key"], d["frame_no"], d["pp3"], d["ppw"]):
            out[rk]["p3"][int(fn)] = float(p3)
            out[rk]["pw"][int(fn)] = float(pw)
    return dict(out)


def _chunks(keys, n=900):
    for i in range(0, len(keys), n):
        yield keys[i:i + n]


def load_races(keys: list[str]) -> dict[str, dict]:
    out = {}
    with get_connection() as c:
        for ch in _chunks(keys):
            q = ("SELECT race_key, race_date, race_type, cup_grade FROM wt_races "
                 "WHERE n_entries = 7 AND race_key IN (%s)" % ",".join("?" * len(ch)))
            for r in c.execute(q, ch).fetchall():
                out[r["race_key"]] = dict(date=str(r["race_date"]),
                                          race_type=r["race_type"],
                                          cup_grade=r["cup_grade"])
    return out


def load_entries(keys: list[str]) -> dict[str, dict[int, dict]]:
    out: dict[str, dict[int, dict]] = defaultdict(dict)
    with get_connection() as c:
        for ch in _chunks(keys):
            q = ("SELECT race_key, frame_no, prediction_mark, line_group, "
                 "       finish_order, race_point, player_class, style, "
                 "       line_size, line_pos, is_line_leader, "
                 "       first_rate, second_rate, third_rate "
                 "FROM wt_entries WHERE race_key IN (%s)"
                 % ",".join("?" * len(ch)))
            for r in c.execute(q, ch).fetchall():
                d = dict(r)
                out[r["race_key"]][int(r["frame_no"])] = dict(
                    mark=(int(d["prediction_mark"])
                          if d["prediction_mark"] is not None else None),
                    lg=d["line_group"],
                    fo=(int(d["finish_order"])
                        if d["finish_order"] is not None else None),
                    # ↓ 予測オッズモデルの入力（`odds_prediction.load_race_inputs` と同じ列）
                    meta={"race_point": d["race_point"],
                          "mark": d["prediction_mark"],
                          "player_class": d["player_class"],
                          "style": d["style"],
                          "line_group": d["line_group"],
                          "line_size": d["line_size"],
                          "line_pos": d["line_pos"],
                          "is_line_leader": d["is_line_leader"],
                          "first_rate": d["first_rate"],
                          "second_rate": d["second_rate"],
                          "third_rate": d["third_rate"]})
    return dict(out)


def load_boards(keys: list[str]) -> dict[str, dict[frozenset[int], float]]:
    """確定三連複オッズ（採点用）。"""
    bd: dict[str, dict[frozenset[int], float]] = defaultdict(dict)
    with get_connection() as c:
        for ch in _chunks(keys):
            q = ("SELECT race_key, combination, odds_value FROM wt_odds "
                 "WHERE bet_type = 'trio' AND race_key IN (%s)"
                 % ",".join("?" * len(ch)))
            for r in c.execute(q, ch).fetchall():
                try:
                    s = frozenset(int(x) for x in str(r["combination"])
                                  .replace("=", "-").split("-"))
                    v = float(r["odds_value"])
                except (TypeError, ValueError):
                    continue
                if len(s) == 3 and v > 0:
                    bd[r["race_key"]][s] = v
    return dict(bd)


# ── 1レース分の腕を組む ───────────────────────────────────────────────────
def build_arm(p3, pw, axis1, axis2, lg, wt_ana, race_type, cup_grade, p3_sum):
    """本番と同じ順序で相手・買い方・受理を決める。"""
    others = sorted(set(p3) - {axis1, axis2})
    legs = rank_7c_select_legs(others, p3)
    plan = rank_7c_buy_plan(p3, pw, axis1, legs, wt_ana=wt_ana)
    cand = {
        "p3_sum_top2": p3_sum,
        "p3_sum_top2_cal": calibrated_p3_sum_top2(p3, race_type, cup_grade),
        "legs_7c": legs,
        "legs_7c_buy": list(plan[1]) if plan else [],
        "lowpay_pattern": rank_7c_is_lowpay_pattern(p3, lg),
        "axis1_p3": p3[axis1],
    }
    return dict(axis2=axis2, plan=plan, accepted=rank_7c_accepts(cand))


def submit_gates(axis1, axis2, legs_buy, p3, pred_board, cons):
    """本番の入稿ゲートを同じ順序で通す。

    returns (stakes {相手:賭け金}, skip_code or None)。

    🔴 **判定できないときは通す**（本番の思想「分からないことを理由に商品を
       落とさない」）。予測オッズが1点でも欠ければ3ゲートとも素通し。
    """
    pred = None
    if pred_board:
        got = {t: pred_board.get(frozenset({axis1, axis2, t})) for t in legs_buy}
        if all(v and v > 0 for v in got.values()):
            pred = got
    stakes, _src = tilted_stakes(list(legs_buy), None, p3, budget=RACE_BUDGET,
                                 predicted_odds=pred)
    if pred is None:
        return stakes, None
    # ① 1点でも安すぎる目（生の予測オッズ < 2.0倍）
    if cheap_point_odds(pred) is not None:
        return stakes, "point_odds"
    # ② 平均払戻（入稿する買い目そのものから・小数第1位で丸めた値）
    lines = [{"stake": stakes[t], "odds": round(float(pred[t]), 1)}
             for t in legs_buy]
    mean = mean_payout_of_lines(lines)
    if mean is not None and mean <= MIN_MEAN_PAYOUT:
        return stakes, "mean_payout"
    # ③ 想定払戻の下限（保守倍率 c(p25) を掛けてから）
    floor = expected_payout_floor(stakes, {t: pred[t] * cons for t in legs_buy},
                                  RACE_BUDGET)
    if floor is not None and floor < MIN_EXPECTED_PAYOUT_BY_RANK["7C"]:
        return stakes, "expected_floor"
    return stakes, None


def score(axis1, axis2, plan, p3, board, wins, stakes=None):
    """(bet, payout, hit, n_legs, 買った点の確定オッズ列) を返す。買えないなら None。"""
    kind, legs_buy = plan
    if kind != "trio" or not legs_buy:
        return None
    combos = [frozenset({axis1, axis2, t}) for t in legs_buy]
    if any(c not in board for c in combos):
        return None
    if stakes is None:
        stakes = stakes_for_combos(axis1, axis2, combos, p3, board=None,
                                   budget=RACE_BUDGET)
    else:
        stakes = {frozenset({axis1, axis2, t}): v for t, v in stakes.items()}
    bet = sum(stakes.values())
    pay = sum(int(board[c] * 100) * stakes[c] // 100 for c in combos if c in wins)
    hit_odds = [board[c] for c in combos if c in wins]
    return (bet, pay, any(c in wins for c in combos), len(combos),
            [board[c] for c in combos], hit_odds, legs_buy, stakes)


# ── 集計 ─────────────────────────────────────────────────────────────────
def summarize(rows):
    """rows=[dict(date,bet,pay,hit,two,legs,odds)] → 指標。"""
    if not rows:
        return None
    n = len(rows)
    bet = sum(r["bet"] for r in rows)
    pay = sum(r["pay"] for r in rows)
    pl = sorted(r["pay"] for r in rows if r["hit"])
    days = len({r["date"] for r in rows})
    return dict(
        n=n, days=days, per_race=bet / n,
        legs=float(np.mean([r["legs"] for r in rows])),
        two=sum(r["two"] for r in rows) / n,
        hit=sum(r["hit"] for r in rows) / n,
        disp=sum(1 for r in rows if r["pay"] > r["bet"]) / n,
        gami=(sum(1 for r in rows if r["hit"] and r["pay"] <= r["bet"])
              / max(sum(r["hit"] for r in rows), 1)),
        roi=pay / max(bet, 1),
        med=(float(np.median(pl)) if pl else 0.0),
        odds_med=float(np.median([o for r in rows for o in r["odds"]])),
        hit_odds_med=(float(np.median([o for r in rows for o in r["hit_odds"]]))
                      if any(r["hit_odds"] for r in rows) else 0.0),
        taikou=sum(r["has_taikou"] for r in rows) / n,
        ho=sum(r["ho_bought"] for r in rows) / n,
        ho_share=float(np.mean([r["ho_share"] for r in rows])),
        big2=sum(1 for r in rows if r["pay"] >= 20000) / max(days, 1),
        per_day=n / max(days, 1),
    )


def paired_ci(pairs, B=4000, seed=41):
    """日ブロック bootstrap。pairs=[(date, bet_a, pay_a, hit_a, disp_a, bet_b, ...)]。

    返り値: dict(roi=(lo,hi), hit=(lo,hi), disp=(lo,hi))  … すべて alt − ref。
    """
    byd = defaultdict(lambda: np.zeros(9))
    for d, ba, pa, ha, da, bb, pb, hb, db in pairs:
        v = byd[d]
        v += np.array([ba, pa, ha, da, bb, pb, hb, db, 1.0])
    a = np.array(list(byd.values()))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(B, len(a)))
    s = a[idx].sum(1)
    out = {}
    d_roi = np.sort(s[:, 5] / s[:, 4] - s[:, 1] / s[:, 0])
    d_hit = np.sort((s[:, 6] - s[:, 2]) / s[:, 8])
    d_dis = np.sort((s[:, 7] - s[:, 3]) / s[:, 8])
    for k, v in (("roi", d_roi), ("hit", d_hit), ("disp", d_dis)):
        out[k] = (v[int(B * .025)], v[int(B * .975)])
    return out


def day_compare(ref, alt, B=4000, seed=41):
    """日ブロック bootstrap（**母集団が違ってよい**版）。

    入稿ゲート後は腕ごとに残るレースが違うので、レース対応では比べられない。
    日を単位に「その日その腕が出した商品」を丸ごと集計し、同じ日インデックスを
    両腕へ当てて差を取る。返すのは Δ(alt − ref) の 95%CI。
    """
    days = sorted({r["date"] for r in ref} | {r["date"] for r in alt})
    idx = {d: i for i, d in enumerate(days)}
    A = np.zeros((len(days), 8))
    for j, rows in ((0, ref), (4, alt)):
        for r in rows:
            i = idx[r["date"]]
            A[i, j] += r["bet"]
            A[i, j + 1] += r["pay"]
            A[i, j + 2] += 1
            A[i, j + 3] += int(r["pay"] > r["bet"])
    rng = np.random.default_rng(seed)
    sel = rng.integers(0, len(days), size=(B, len(days)))
    t = A[sel].sum(1)
    out = {}
    for k, v in (("roi", t[:, 5] / np.maximum(t[:, 4], 1) - t[:, 1] / np.maximum(t[:, 0], 1)),
                 ("disp", t[:, 7] / np.maximum(t[:, 6], 1) - t[:, 3] / np.maximum(t[:, 2], 1)),
                 ("n", (t[:, 6] - t[:, 2]) / len(days))):
        v = np.sort(v)
        out[k] = (v[int(B * .025)], v[int(B * .975)])
    return out


def fmt(tag, s):
    if not s:
        return f"{tag:>9} —"
    return (f"{tag:>9}{s['n']:>7,}{s['per_day']:>8.2f}{s['legs']:>6.2f}"
            f"{s['two']:>9.2%}{s['hit']:>9.2%}{s['disp']:>10.2%}"
            f"{s['gami']:>8.1%}{s['roi']:>8.1%}{s['med']:>10,.0f}"
            f"{s['odds_med']:>9.2f}{s['hit_odds_med']:>9.2f}"
            f"{s['taikou']:>8.0%}{s['ho']:>8.0%}{s['ho_share']:>8.0%}"
            f"{s['big2']:>9.2f}")


HDR = (f"{'腕':>9}{'R':>7}{'件/日':>8}{'点':>6}{'二軸':>9}{'的中':>9}"
       f"{'表示的中':>10}{'ガミ':>8}{'ROI':>8}{'払戻中央':>10}"
       f"{'買目倍率':>9}{'的中倍率':>9}{'◯を買':>8}{'◎◯▲買':>8}{'その額':>8}{'2万+/日':>9}")


def run(window_name, keys, vin, races, ents, boards, gates=False, detail=False):
    cons = conservative_multiplier(7, "p25") if gates else 1.0
    skips = defaultdict(lambda: defaultdict(int))   # arm -> code -> n
    det = []                                        # 1レース1行の明細
    rows = defaultdict(list)          # arm -> [row]
    common = defaultdict(list)        # arm -> [row]（全腕が買えたレースだけ）
    fired = defaultdict(list)         # arm -> [row]（差し替えが発火したレース）
    stat = defaultdict(int)
    alt_marks = defaultdict(int)
    cliffs = []
    n_pop = 0

    for rk in keys:
        v, meta, e = vin.get(rk), races.get(rk), ents.get(rk)
        board = boards.get(rk)
        if not (v and meta and e and board):
            continue
        p3, pw = v["p3"], v["pw"]
        if len(p3) != 7 or len(e) < 7:
            continue
        order = sorted(((x["fo"], f) for f, x in e.items() if x["fo"]),
                       key=lambda t: t[0])
        wins = set(winning_trios(order))
        if not wins:
            continue
        sel = rank_7c_select_axis(p3)
        if not sel:
            continue
        a1, a2_top2, p3_sum = sel
        marks = {f: e[f]["mark"] for f in e}
        honmei = next((f for f, m in marks.items() if m == 1), None)
        taikou = next((f for f, m in marks.items() if m == 2), None)
        ana = next((f for f, m in marks.items() if m == 3), None)
        lg = {f: e[f]["lg"] for f in e}

        n_pop += 1
        agree = (honmei is not None and taikou is not None
                 and {a1, a2_top2} == {honmei, taikou})
        stat["agree"] += int(agree)
        stat["a1_honmei"] += int(honmei is not None and a1 == honmei)
        stat["marks_ok"] += int(honmei is not None and taikou is not None)

        a2_prod = rank_7c_reselect_axis2_off_marks(p3, a1, a2_top2, honmei, taikou)
        a2_all = rank_7c_reselect_axis2_off_marks(p3, a1, a2_top2, honmei, taikou,
                                                  cliff_max=10.0)
        if agree:
            cliffs.append(p3[a2_top2] - p3.get(a2_all, 0.0))
            stat["fired_prod"] += int(a2_prod != a2_top2)
            if a2_all != a2_top2:
                alt_marks[marks.get(a2_all)] += 1

        arms = {}
        for tag, a2, ana_arg in (("TOP2", a2_top2, ana),
                                 ("TOP2-案E無", a2_top2, None),
                                 ("PROD", a2_prod, ana),
                                 ("SWAP_ALL", a2_all, ana)):
            arms[tag] = build_arm(p3, pw, a1, a2, lg, ana_arg,
                                  meta["race_type"], meta["cup_grade"], p3_sum)

        top3 = {c for w in wins for c in w}
        pred_board = None
        if gates:
            try:
                pred_board = predict_board(
                    sorted(p3), p3, pw, {f: e[f]["meta"] for f in e})
            except (OddsPredictionUnavailable, Exception):   # noqa: BLE001
                pred_board = None
            if pred_board is None:
                skips["_race"]["no_pred_board"] += 1
        scored = {}
        for tag, arm in arms.items():
            if not (arm["accepted"] and arm["plan"]):
                continue
            stakes = None
            if gates:
                kind, legs_buy = arm["plan"]
                if kind != "trio" or not legs_buy:
                    continue
                stakes, code = submit_gates(a1, arm["axis2"], legs_buy, p3,
                                            pred_board, cons)
                if code:
                    skips[tag][code] += 1
                    if detail and tag in ("TOP2", "PROD"):
                        det.append((rk, tag, a1, arm["axis2"], list(legs_buy),
                                    code, None, None, marks))
                    continue
            sc = score(a1, arm["axis2"], arm["plan"], p3, board, wins,
                       stakes=stakes)
            if not sc:
                continue
            bet, pay, hit, nlegs, odds, hit_odds, legs_buy, stk = sc
            _tri = (frozenset({honmei, taikou, a2_all})
                    if (honmei and taikou and a2_all) else None)
            if detail and tag in ("TOP2", "PROD"):
                det.append((rk, tag, a1, arm["axis2"], list(legs_buy),
                            "", pay, bet, marks))
            scored[tag] = dict(date=meta["date"], bet=bet, pay=pay, hit=hit,
                               two=int(a1 in top3 and arm["axis2"] in top3),
                               legs=nlegs, odds=odds, hit_odds=hit_odds,
                               has_taikou=int(taikou is not None
                                              and (taikou in (a1, arm["axis2"])
                                                   or taikou in legs_buy)),
                               ho_bought=int(_tri is not None and _tri in stk),
                               ho_share=(stk.get(_tri, 0) / max(bet, 1)
                                         if _tri is not None else 0.0))
            rows[tag].append(scored[tag])
        if gates:
            pass
        if len(scored) == 4:
            for tag in scored:
                common[tag].append(scored[tag])
            if agree and a2_prod != a2_top2:
                for tag in scored:
                    fired[tag].append(scored[tag])

    print(f"\n{'='*100}\n【{window_name}】7車 {n_pop:,}R（vintage 予測・確定オッズ採点"
          f"{'・入稿ゲートあり' if gates else ''}）")
    ok = max(stat["marks_ok"], 1)
    print(f"  ◎◯が取れるレース          : {stat['marks_ok']:,} "
          f"({stat['marks_ok']/max(n_pop,1):.1%})")
    print(f"  指数1位 == ◎              : {stat['a1_honmei']/ok:.1%}")
    print(f"  🔴 指数1位2位 == {{◎,◯}}    : {stat['agree']/ok:.1%}"
          f"  （{stat['agree']:,}R）")
    if cliffs:
        c = np.array(cliffs)
        print(f"  うち実際に差し替え（落差<0.30）: "
              f"{stat['fired_prod']/max(stat['agree'],1):.1%}"
              f"  落差中央 {np.median(c):.3f}")
        tot = sum(alt_marks.values())
        s = " / ".join(f"{MARK_NAME.get(k, '無印' if k is None else str(k))} "
                       f"{v/max(tot,1):.1%}"
                       for k, v in sorted(alt_marks.items(),
                                          key=lambda kv: (kv[0] is None, kv[0])))
        print(f"  差し替え先の印              : {s}")

    if gates:
        print(f"\n〈入稿ゲートで落ちた件数〉（予測盤面が作れず判定不能 "
              f"{skips['_race']['no_pred_board']:,}R）")
        print(f"{'腕':>11}{'1点2倍未満':>12}{'平均払戻2万以下':>16}"
              f"{'下限1.5倍未満':>14}{'計':>7}")
        for tag in ARMS:
            d = skips[tag]
            tot = sum(d.values())
            print(f"{tag:>11}{d['point_odds']:>12,}{d['mean_payout']:>16,}"
                  f"{d['expected_floor']:>14,}{tot:>7,}")

    if detail:
        print("\n〈1レースごとの明細〉  ✅=入稿  ❌=ゲートで見送り")
        print(f"{'レース':>16}{'腕':>7}  {'印(◎◯▲)':<10}{'軸':<8}{'相手':<14}"
              f"{'結果':<28}{'払戻':>9}")
        for rk_, tag, a1_, a2_, legs_, code, pay_, bet_, mk in sorted(det):
            h_ = next((f for f, m in mk.items() if m == 1), None)
            t_ = next((f for f, m in mk.items() if m == 2), None)
            an_ = next((f for f, m in mk.items() if m == 3), None)
            res = f"❌ {code}" if code else ("✅ 的中" if pay_ else "✅ 外れ")
            print(f"{rk_:>16}{tag:>7}  {f'{h_}/{t_}/{an_}':<10}"
                  f"{f'{a1_}-{a2_}':<8}{','.join(map(str, legs_)):<14}"
                  f"{res:<28}{(pay_ or 0):>9,}")

    if gates:
        print(f"\n〈入稿ゲート通過後（腕ごとに母集団が違う＝これが商品の姿）〉")
        print(HDR)
        for tag in ARMS:
            print(fmt(tag, summarize(rows[tag])))
        for tag in ARMS[1:]:
            if not rows[tag] or not rows["TOP2"]:
                continue
            ci = day_compare(rows["TOP2"], rows[tag])
            print(f"   Δ({tag} − TOP2)  件/日 [{ci['n'][0]:+.2f},{ci['n'][1]:+.2f}]"
                  f"  表示的中 [{ci['disp'][0]:+.2%},{ci['disp'][1]:+.2%}]"
                  f"  ROI [{ci['roi'][0]:+.1%},{ci['roi'][1]:+.1%}]")
        # 🔴 1日サンプルがどれだけ揺れるかを併記する（日次の勝ち負けと分布）
        byd = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
        for tag in ARMS:
            for r in rows[tag]:
                byd[r["date"]][tag][0] += r["bet"]
                byd[r["date"]][tag][1] += r["pay"]
        both = [(d, v["TOP2"], v["PROD"]) for d, v in byd.items()
                if v["TOP2"][0] and v["PROD"][0]]
        if both:
            wins = sum(1 for _, a, b in both if a[1] / a[0] > b[1] / b[0])
            z0 = sum(1 for _, a, _ in both if a[1] == 0)
            z1 = sum(1 for _, _, b in both if b[1] == 0)
            r0 = np.array([a[1] / a[0] for _, a, _ in both])
            r1 = np.array([b[1] / b[0] for _, _, b in both])
            print(f"\n   〈1日を単位にすると〉両腕とも商品が出た {len(both)} 日")
            print(f"     日次ROI が TOP2 > PROD の日: {wins}/{len(both)} "
                  f"({wins/len(both):.0%})")
            print(f"     日次ROI 中央 TOP2 {np.median(r0):.0%} / PROD {np.median(r1):.0%}"
                  f"   四分位 TOP2 [{np.percentile(r0,25):.0%},{np.percentile(r0,75):.0%}]"
                  f" / PROD [{np.percentile(r1,25):.0%},{np.percentile(r1,75):.0%}]")
            print(f"     払戻0円の日: TOP2 {z0/len(both):.0%} / PROD {z1/len(both):.0%}"
                  f"   🔴 1日では符号が決まらない")
        return

    for title, src in (("腕ごとの実母集団（それぞれが買えたレース）", rows),
                       ("共通母集団（全腕とも買えたレースだけ）", common),
                       ("🔴 差し替えが発火したレースだけ", fired)):
        print(f"\n〈{title}〉")
        print(HDR)
        for tag in ARMS:
            print(fmt(tag, summarize(src[tag])))
        if src is not rows and src["TOP2"]:
            for tag in ARMS[1:]:
                pairs = [(a["date"], a["bet"], a["pay"], a["hit"], int(a["pay"] > a["bet"]),
                          b["bet"], b["pay"], b["hit"], int(b["pay"] > b["bet"]))
                         for a, b in zip(src["TOP2"], src[tag])]
                ci = paired_ci(pairs)
                print(f"   Δ({tag} − TOP2)  的中 [{ci['hit'][0]:+.2%},{ci['hit'][1]:+.2%}]"
                      f"  表示的中 [{ci['disp'][0]:+.2%},{ci['disp'][1]:+.2%}]"
                      f"  ROI [{ci['roi'][0]:+.1%},{ci['roi'][1]:+.1%}]")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default=SPLIT)
    ap.add_argument("--swap", action="store_true", help="窓を入れ替える（形式のみ）")
    ap.add_argument("--p3", default="vintage", choices=["vintage", "entries"],
                    help="予測の出所。entries は backfill 値＝look-ahead（比較用）")
    ap.add_argument("--detail", action="store_true",
                    help="1レースごとの明細を出す（1日サンプルの確認用）")
    ap.add_argument("--gates", action="store_true",
                    help="入稿ゲート（1点2倍/平均払戻2万/下限1.5倍）まで通す。"
                         "🔴 KEIRIN_ODDS_MODEL_DIR で vintage オッズモデルを指すこと")
    ap.add_argument("--from", dest="d1", default="")
    ap.add_argument("--to", dest="d2", default="")
    args = ap.parse_args()

    if args.p3 == "entries":
        with get_connection() as c:
            q = "SELECT race_key FROM wt_races WHERE n_entries = 7"
            if args.d1:
                q += f" AND race_date >= '{args.d1}'"
            if args.d2:
                q += f" AND race_date <= '{args.d2}'"
            all_keys = [r["race_key"] for r in c.execute(q).fetchall()]
        vin = load_entries_p3(all_keys)
    else:
        vin = load_vintage()
    keys = list(vin)
    races = load_races(keys)
    keys = [k for k in keys if k in races]
    ents = load_entries(keys)
    boards = load_boards(keys)
    print(f"[load] vintage {len(vin):,}R / 7車 {len(keys):,}R / "
          f"盤面 {len(boards):,}R")

    if args.d1:
        keys = [k for k in keys if races[k]["date"] >= args.d1]
    if args.d2:
        keys = [k for k in keys if races[k]["date"] <= args.d2]
    w1 = sorted(k for k in keys if races[k]["date"] < args.split)
    w2 = sorted(k for k in keys if races[k]["date"] >= args.split)
    if args.swap:
        w1, w2 = w2, w1
    if args.gates:
        print(f"[gates] オッズモデル: {os.environ.get('KEIRIN_ODDS_MODEL_DIR') or '本番'}"
              f"  保守倍率 c(p25)={conservative_multiplier(7, 'p25'):.4f}")
    run("探索窓", w1, vin, races, ents, boards, gates=args.gates,
        detail=args.detail)
    run("確認窓", w2, vin, races, ents, boards, gates=args.gates,
        detail=args.detail)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
