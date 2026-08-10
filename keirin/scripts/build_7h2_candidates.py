#!/usr/bin/env python3
"""RANK_7H2（穴推奨・印なし2軸の高配当）の候補を生成する。

## 位置づけ

7H2 は既存の予想ベース6ランクと **軸の選び方が違う**。既存はモデル上位2車を
軸にするが、7H2 は **WT公式印の付いていない車**の中から軸2車を選ぶ
（有力だが人気薄＝配当帯を移す）。母集団はレース単位のエントロピー選別で、
7H1 のようなレース単位の学習モデルは使わない。

出力する候補JSONの形式と保存先は既存ランクに合わせてあるので、
`notify_prerace_wt.py` / `netkeirin_submit_wt.py` からは同じように読める。

## 処理

1. 対象日の7車レースと出走表を DB から取得
2. 本番モデル（`lgbm_wt_eval` / `_win`）で選手単位の予測を作る
   （**連帯ヘッド `ptop2` は不要**。軸2も三連複プールも 3着内率で決まる）
3. `strategy_wt.rank_7h2_entropy()` で3着内率の正規化エントロピー
4. `strategy_wt.rank_7h2_build_legs()` で買い目（三連単F 倍購入10点 + 三連複BOX）
5. `strategy_wt.rank_7h2_daily_select()` で絶対閾値により選別

## 使い方

    PYTHONPATH=. .venv/bin/python scripts/build_7h2_candidates.py \\
        --date 2026-08-10 [--out data/picks/wave_picks_wt_2026-08-10_s7h2_candidates.json]
    # 過去日を honest に作る場合は月次vintageを明示する
    #   --eval-model lgbm_wt_eval_m2608 --win-model lgbm_wt_win_m2608

⚠️ 過去分の再構築で本番モデル（全期間学習）を使うと in-sample になる。
   必ず vintage を指定すること（`assert_vintage_for_past` が落とす）。

⚠️ `RANK_7H2_ENTROPY_MIN` は本番モデルの較正に合わせた絶対閾値。
   vintage モデルで過去分を作ると該当率がずれうる（実測 20.0〜21.6%）。

DB は読み取りのみ。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection  # noqa: E402
from src.models.trainer import load_model  # noqa: E402
from src.preprocessing.feature_wt import (  # noqa: E402
    build_features_wt, load_raw_data_wt, prepare_X,
)
from src.strategy_wt import (  # noqa: E402
    RANK_7H2_NE, rank_7h2_build_legs, rank_7h2_daily_select, rank_7h2_entropy,
    rank_7h2_stakes,
)
from src.wt_vintage_config import assert_vintage_for_past  # noqa: E402


def load_context(date_from: str, date_to: str) -> tuple[dict, dict]:
    with get_connection() as c:
        meta = {}
        for r in c.execute(
                "SELECT r.race_key, r.race_date, r.race_no, r.venue_id, r.grade, "
                "       r.race_type, r.day_index, r.start_at, r.distance, "
                "       v.name AS venue_name "
                "FROM wt_races r LEFT JOIN venue_info v ON r.venue_id = v.venue_code "
                "WHERE r.n_entries = ? AND r.cancel = 0 "
                "  AND r.race_date BETWEEN ? AND ?",
                (RANK_7H2_NE, date_from, date_to)):
            meta[r["race_key"]] = dict(r)
        keys = sorted(meta)
        ents: dict[str, list[dict]] = defaultdict(list)
        for i in range(0, len(keys), 700):
            ch = keys[i:i + 700]
            q = ("SELECT race_key, frame_no, name, prediction_mark "
                 "FROM wt_entries WHERE race_key IN (%s)" % ",".join("?" * len(ch)))
            for r in c.execute(q, ch):
                ents[r["race_key"]].append(dict(r))
    return meta, dict(ents)


def build(date_from: str, date_to: str, eval_model: str, win_model: str) -> list[dict]:
    meta_all, ents_all = load_context(date_from, date_to)
    if not meta_all:
        return []
    df = build_features_wt(load_raw_data_wt(min_date=date_from, max_date=date_to))
    df = df[df["race_key"].isin(set(meta_all))].copy()
    if df.empty:
        return []
    ev, wi = load_model(eval_model), load_model(win_model)
    X = prepare_X(df)
    pp3 = ev.predict_proba(X)[:, 1]
    ppw = wi.predict_proba(X)[:, 1]
    preds: dict[str, dict[int, tuple[float, float]]] = defaultdict(dict)
    for rk, fno, a, b in zip(df["race_key"], df["frame_no"], pp3, ppw):
        preds[rk][int(fno)] = (float(a), float(b))

    rows: list[dict] = []
    for rk, pr in preds.items():
        ents = ents_all.get(rk)
        if not ents or len(ents) != RANK_7H2_NE or len(pr) != RANK_7H2_NE:
            continue
        top3 = {f: v[0] for f, v in pr.items()}
        win = {f: v[1] for f, v in pr.items()}
        # 🔴 印なしは 0 であって NaN ではない。None（列が無い/欠測）だけを弾く。
        marks = {int(e["frame_no"]): e.get("prediction_mark") for e in ents}
        marks = {f: (None if v is None else int(v)) for f, v in marks.items()}
        trio, tf = rank_7h2_build_legs(win, top3, marks)
        if not trio or not tf:
            continue
        u_trio, u_tf, total = rank_7h2_stakes(len(trio), len(tf))
        if not u_trio or not u_tf:
            continue
        ax = sorted((f for f in win if marks.get(f) == 0), key=lambda f: -win[f])
        m = meta_all[rk]
        name_of = {int(e["frame_no"]): e.get("name") for e in ents}
        a1 = int(tf[0].split("-")[0])
        a2 = int(tf[0].split("-")[1])
        rows.append({
            "race_key": rk, "race_date": m["race_date"],
            "venue_name": m.get("venue_name"), "race_no": m.get("race_no"),
            "start_time": m.get("start_at"), "race_type": m.get("race_type"),
            "n_entries": RANK_7H2_NE,
            "entropy": round(rank_7h2_entropy(top3), 6),
            "axis1": a1, "axis2": a2,
            "axis1_name": name_of.get(a1), "axis2_name": name_of.get(a2),
            "n_unmarked": len(ax),
            "legs_trio": ["=".join(str(x) for x in sorted(t)) for t in trio],
            "legs_tf": tf,
            "stake_trio": u_trio, "stake_tf": u_tf, "bet_amount": total,
        })

    out: list[dict] = []
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_day[r["race_date"]].append(r)
    for day in sorted(by_day):
        out.extend(rank_7h2_daily_select(by_day[day]))
    out.sort(key=lambda c: (c["race_date"], -c["entropy"]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="対象日（YYYY-MM-DD）")
    ap.add_argument("--to", dest="date_to", default=None, help="範囲指定の終端")
    ap.add_argument("--out", default=None)
    ap.add_argument("--eval-model", default="lgbm_wt_eval")
    ap.add_argument("--win-model", default="lgbm_wt_win")
    args = ap.parse_args()

    # 過去日に本番モデル（全期間学習）を当てると in-sample になるので落とす。
    # 既定値が本番モデル名なので、指定を忘れると**無言で**そうなる。
    d_to = args.date_to or args.date
    assert_vintage_for_past(d_to, {"eval": args.eval_model, "win": args.win_model})

    cands = build(args.date, d_to, args.eval_model, args.win_model)
    out = Path(args.out) if args.out else (
        REPO / "data" / "picks" / f"wave_picks_wt_{args.date}_s7h2_candidates.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cands, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out)
    print(f"[保存先] {out}  (7H2候補 {len(cands)}件)")
    for c in cands:
        print(f"  {c['venue_name']}{c['race_no']}R  "
              f"軸{c['axis1']}({c['axis1_name']})-{c['axis2']}({c['axis2_name']}) "
              f"エントロピー{c['entropy']:.4f}  "
              f"三連複{len(c['legs_trio'])}点×{c['stake_trio']}円 + "
              f"三連単{len(c['legs_tf'])}点×{c['stake_tf']}円 = {c['bet_amount']}円")


if __name__ == "__main__":
    main()
