#!/usr/bin/env python3
"""同着で `hit=0` のまま残っている過去の `picks_history` 行を直す（2026-08-22 単発）。

## 背景

2026-08-22 まで採点は全経路が `ORDER BY finish_order` の**先頭3件だけ**を正解に
していたため、同着でもう一方の目を買っていた的中が `hit=0` で記録されていた
（`src/result_top3.py` の docstring 参照）。本体は修正済みだが、
**`picks_history` は当月しか再構築しない**ので過去月の行は誤ったまま残る。

## この修復のやり方（🔴 直接 UPDATE しない）

「同着だから的中のはず」と決め打ちで書き換えるのではなく、
**その月の vintage モデルで当該日を再計算し、記録と突き合わせてから**書く。

    1. 同着レースに掛かった picks を全走査し、`src/result_top3` で
       「記録 hit=0 だが実際は的中」の行だけを拾う
    2. その行の**ランクの `build_rows` を、その月の vintage モデルで
       当該日1日だけ**回す（本番の再構築とまったく同じ経路）
    3. 再計算行と記録行の `pred_combo` / `n_combos` / `bet_amount` を照合する。
       🔴 **一致しなければ書かない。** 一致しないのは「その行が書かれた当時と
       いまで規則が違う」（世代混在）ということで、同着とは別の話。
       ここで直すと、その1行だけ新しい規則で塗り替えることになる
    4. 一致した行だけ hit / payout / trio_payout / trifecta_payout を更新する

既定は **dry-run**。書き込むときだけ `--apply` を付ける。

⚠️ この規則で選ばれるランクは日次上限（`daily_cap`）を持たないので、
   1日だけを渡しても月まるごとを渡したときと選出結果は同じ
   （上限を持つのは 7T1 のみ・`rank_7t1_daily_select`）。
"""
from __future__ import annotations

import argparse
import importlib
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection  # noqa: E402
from src.evaluation.backtest_wt import _load_payouts_wt  # noqa: E402
from src.models.trainer import load_model  # noqa: E402
from src.preprocessing.feature_wt import (  # noqa: E402
    build_features_wt,
    load_raw_data_wt,
    prepare_X,
)
from src.rebuild_stakes import load_morning_boards, stakes_for_combos  # noqa: E402
from src.strategy_wt import unit_stake  # noqa: E402
from src.result_top3 import (  # noqa: E402
    hit_trifecta,
    hit_trio,
    winning_trifectas,
    winning_trios,
)

#: rank → (backfill モジュール, race_key の接尾辞, bad モデルを使うか)
RANKS = {
    "RANK_7S": ("backfill_7s_rank_wt", "#7S", True),
    "RANK_7B": ("backfill_7b_rank_wt", "#7B", True),
    "RANK_7C": ("backfill_7c_rank_wt", "#7C", True),
    "RANK_7M1": ("backfill_7m1_rank_wt", "#7M1", True),
    "RANK_9C": ("backfill_9c_rank_wt", "#9C", False),
}

_TRIO_RE = re.compile(r"^\s*(\d+)\s*=\s*(\d+)\s*-\s*([\d,]+)")


def _tag(race_date: str) -> str:
    """'2025-04-03' → 'm2504'（vintage モデルの月タグ）。"""
    y, m, _ = race_date.split("-")
    return f"m{y[2:]}{m}"


def _combos_of(pred_combo: str) -> tuple[str, list]:
    """`pred_combo` から (券種, 買い目) を取り出す。"""
    text = (pred_combo or "").strip()
    if text.startswith("三単:"):
        body = text.split(":", 1)[1]
        parts = [p.strip() for p in body.split(",")]
        head = parts[0].split("-")
        if len(head) == 3 and all(p.count("-") == 2 for p in parts):
            return "trifecta", [tuple(int(x) for x in p.split("-")) for p in parts]
        if len(head) == 3:      # 「軸1-軸2-相手,相手,…」
            a1, a2 = int(head[0]), int(head[1])
            return "trifecta", ([(a1, a2, int(head[2]))]
                                + [(a1, a2, int(p)) for p in parts[1:]])
        return "trifecta", []
    m = _TRIO_RE.match(text)
    if not m:
        return "trio", []
    a1, a2 = int(m.group(1)), int(m.group(2))
    return "trio", [frozenset((a1, a2, int(x))) for x in m.group(3).split(",")]


def find_broken() -> list[dict]:
    """同着で `hit=0` のまま残っている行を拾う（DBは読むだけ）。"""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT race_key FROM ("
            "  SELECT race_key, COUNT(*) c3, COUNT(DISTINCT finish_order) d3"
            "  FROM wt_entries WHERE finish_order BETWEEN 1 AND 3 GROUP BY race_key"
            ") t WHERE c3 > 3 OR (c3 = 3 AND d3 < 3)")
        keys = [r["race_key"] for r in cur.fetchall()]
        cur.execute("SELECT race_key, finish_order, frame_no FROM wt_entries "
                    "WHERE race_key = ANY(?) AND finish_order BETWEEN 1 AND 3", (keys,))
        fin: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for r in cur.fetchall():
            fin[r["race_key"]].append((int(r["finish_order"]), int(r["frame_no"])))
        cur.execute(
            "SELECT race_key, race_date, rank, pred_combo, n_combos, hit, payout, "
            "       bet_amount, trio_payout, trifecta_payout "
            "FROM picks_history WHERE split_part(race_key,'#',1) = ANY(?)", (keys,))
        rows = [dict(r) for r in cur.fetchall()]

    broken = []
    for r in rows:
        if r["hit"]:
            continue
        base = r["race_key"].split("#")[0]
        kind, combos = _combos_of(r["pred_combo"])
        if not combos:
            continue
        if kind == "trio":
            win = hit_trio(combos, winning_trios(fin[base]))
        else:
            win = hit_trifecta(combos, winning_trifectas(fin[base]))
        if win is not None:
            broken.append({**r, "base": base, "kind": kind, "win": win})
    return sorted(broken, key=lambda r: (r["race_date"], r["race_key"]))


def recompute(rank: str, race_date: str, race_key: str) -> dict | None:
    """その月の vintage モデルで当該日を再計算し、対象レースの行を返す。"""
    mod_name, suffix, use_bad = RANKS[rank]
    mod = importlib.import_module(f"scripts.{mod_name}")
    tag = _tag(race_date)
    rows = mod.build_rows(f"lgbm_wt_eval_{tag}", race_date, race_date,
                          f"lgbm_wt_win_{tag}",
                          f"lgbm_wt_bad_{tag}" if use_bad else None)
    want = race_key if race_key.endswith(suffix) else race_key + suffix
    for row in rows:
        if row["race_key"] == want:
            return row
    return None


def _vintage_top3_probs(race_date: str, race_key: str) -> dict[int, float]:
    """その月の vintage 評価モデルで当該レースの3着内率を出す。

    `backfill_*_rank_wt.build_rows` の冒頭とまったく同じ手順
    （`load_raw_data_wt` → `build_features_wt` → `prepare_X` → `predict_proba`）。
    """
    model = load_model(f"lgbm_wt_eval_{_tag(race_date)}")
    df = build_features_wt(load_raw_data_wt(min_date=race_date, max_date=race_date))
    df = df[df["race_key"] == race_key].copy()
    if df.empty:
        return {}
    df["pred_prob"] = model.predict_proba(prepare_X(df))[:, 1]
    return {int(r.frame_no): float(r.pred_prob) for r in df.itertuples(index=False)}


def repair_by_recorded_buy(b: dict) -> dict | None:
    """記録された買い目そのものに、当時の配分規則を当てて払戻を出す。

    選出ゲートや相手の絞り方が当時と変わっているレースは `build_rows` では
    再現できない（＝世代が違う）。しかし**買った目は記録に残っている**ので、
    「その目に当時の配分規則を当てる」ことはできる。

    🔴 **配分が当時と同じであることを `bet_amount` の一致で検証する。**
       賭け金は各目の配分を100円単位に丸めた合計なので、これが一致するなら
       配分そのものが一致しているとみなせる。合わなければ**書かない**。
    """
    kind, combos = _combos_of(b["pred_combo"])
    if not combos:
        return None
    pm = _load_payouts_wt([b["base"]]).get(b["base"], {})
    if kind == "trifecta":
        pay_key = ("trifecta", tuple(b["win"]))
        unit = unit_stake(len(combos))
        stake, total = unit, unit * len(combos)
    else:
        # 軸2車＝全ての目に共通して含まれる2車
        axis = set(combos[0])
        for c in combos[1:]:
            axis &= set(c)
        if len(axis) != 2:
            return {"skip": f"軸2車を特定できない（共通 {sorted(axis)}）"}
        a1, a2 = sorted(axis)
        probs = _vintage_top3_probs(b["race_date"], b["base"])
        if not probs:
            return None
        boards = load_morning_boards([b["base"]])
        stakes = stakes_for_combos(a1, a2, combos, probs, boards.get(b["base"]))
        stake, total = stakes.get(b["win"], 0), sum(stakes.values())
        pay_key = ("trio", b["win"])
    if total != int(b["bet_amount"] or 0):
        return {"skip": f"賭け金が合わない（記録 {b['bet_amount']} / 再現 {total}）"}
    pay100 = int(pm.get(pay_key, 0) or 0)
    if pay100 <= 0:
        return {"skip": "当たり目の確定配当が引けない"}
    return {"hit": 1, "payout": pay100 * stake // 100,
            "trio_payout": pay100 if kind == "trio" else 0,
            "trifecta_payout": pay100 if kind == "trifecta" else 0,
            "stake": stake}


def _update(b: dict, row: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE picks_history SET hit=?, payout=?, trio_payout=?, "
            "trifecta_payout=? WHERE race_key=? AND rank=?",
            (int(row["hit"]), int(row["payout"]),
             int(row.get("trio_payout") or 0),
             int(row.get("trifecta_payout") or 0),
             b["race_key"], b["rank"]))
        conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="実際に UPDATE する（既定は dry-run）")
    ap.add_argument("--recorded-buy", action="store_true",
                    help="vintage の再構築で再現できない行を、**記録された買い目**へ"
                         "当時の配分規則を当てて直す（賭け金一致で検証する）")
    args = ap.parse_args()

    broken = find_broken()
    print(f"同着で記録が hit=0 のまま残っている行: {len(broken)}件\n")
    if not broken:
        return

    applied, skipped = 0, 0
    for b in broken:
        head = f"{b['race_date']} {b['race_key']:22s} {b['rank']:9s}"
        if b["rank"] not in RANKS:
            print(f"{head} SKIP: 再計算経路が未定義")
            skipped += 1
            continue
        row = recompute(b["rank"], b["race_date"], b["base"])
        reason = None
        if row is None:
            reason = "再計算で選出されなかった（世代が違う）"
        else:
            # 🔴 同着以外の差が出ている行は build_rows の結果で上書きしない。
            #    買い目の**集合**が同じなら表記ゆれ（並び・axis_sum の丸め）なので許す。
            same_buy = (_combos_of(row.get("pred_combo", "")) [1]
                        and set(map(frozenset, _combos_of(row.get("pred_combo", ""))[1]))
                        == set(map(frozenset, _combos_of(b["pred_combo"])[1])))
            diff = [k for k in ("n_combos", "bet_amount")
                    if str(row.get(k)) != str(b[k])]
            if not same_buy or diff:
                reason = f"記録と再計算で買い目が違う {diff or ''}".strip()
                if row.get("pred_combo") != b["pred_combo"]:
                    print(f"      記録={b['pred_combo']!r}  再計算={row.get('pred_combo')!r}")
            elif not row.get("hit"):
                reason = "再計算でも的中にならない（要調査）"
        if reason:
            fb = repair_by_recorded_buy(b) if args.recorded_buy else None
            if not fb or "skip" in fb:
                extra = f" / 記録買い目でも不可: {fb['skip']}" if fb and "skip" in fb else ""
                print(f"{head} SKIP: {reason}{extra}")
                skipped += 1
                continue
            print(f"{head} hit 0→1  payout {b['payout']}→{fb['payout']}  "
                  f"（記録買い目＋当時の配分 {fb['stake']}円/点）  買={b['pred_combo'][:26]}")
            if args.apply:
                _update(b, fb)
            applied += 1
            continue
        print(f"{head} hit 0→1  payout {b['payout']}→{row['payout']}  "
              f"trio {b['trio_payout']}→{row.get('trio_payout', 0)}  "
              f"三単 {b['trifecta_payout']}→{row.get('trifecta_payout', 0)}  "
              f"買={b['pred_combo'][:24]}")
        if args.apply:
            _update(b, row)
        applied += 1

    print(f"\n{'更新' if args.apply else '更新予定'} {applied}件 / 見送り {skipped}件")
    if not args.apply:
        print("（dry-run。実際に書くには --apply）")


if __name__ == "__main__":
    main()
