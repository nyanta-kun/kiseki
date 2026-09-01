#!/usr/bin/env python3
"""7H1 のゲート再設計を検証するための vintage キャッシュ生成。

## なぜ専用スクリプトか

`build_7h1_candidates.build()` は選別後の候補しか返さず、`fav_ppw`（本命の
モデル1着率）などの素性を落としてしまう。ゲートに「1着率の上限」を足せるかを
測るには **選別前の全母集団と本命の強さ**が要る。

本スクリプトは build() と**同じ本番関数**（`build_favbust_row` / `roles_of` /
`rank_7h1_build_legs` / `rank_7h1_daily_select`）を同じ順序で呼び、候補へ
診断用の列を足しただけのもの。選別結果が build() と一致することは
`--verify` で突き合わせる。

⚠️ 月次凍結 vintage モデルのみを使う（本番モデルは全期間学習なので
   過去に当てると in-sample）。favbust の vintage は 2024-04 以降のみ。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_7h1_gate/build_cache.py \
        --from 2024-04 --to 2026-08 --out data/exp/7h1_gate_cache.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.backfill_7h1_rank_wt import (  # noqa: E402
    _combo_key, _load_boards, _load_finishes,
)
from src.database import get_connection  # noqa: E402
from src.evaluation.backtest_wt import _load_payouts_wt  # noqa: E402
from src.models.trainer import load_model  # noqa: E402
from src.preprocessing.favbust_features import (  # noqa: E402
    build_favbust_row, feature_vector, roles_of,
)
from src.preprocessing.feature_wt import (  # noqa: E402
    build_features_wt, load_raw_data_wt, prepare_X,
)
from src.race_shape import _normalized  # noqa: E402
from src.result_top3 import hit_trifecta, winning_trifectas  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_7H1_NE, rank_7h1_build_legs, rank_7h1_daily_select, rank_7h1_stakes,
)
from src.wt_vintage_config import bad_model_name, favbust_model_name, monthly_windows  # noqa: E402
from scripts.build_7h1_candidates import load_context  # noqa: E402


def build_rows_for_window(date_from: str, date_to: str, eval_model: str,
                          win_model: str, bad_model: str, favbust_model: str):
    """選別**前**の全母集団を診断列つきで返す（build() と同じ手順）。"""
    meta_all, ents_all = load_context(date_from, date_to)
    if not meta_all:
        return []
    df = build_features_wt(load_raw_data_wt(min_date=date_from, max_date=date_to))
    df = df[df["race_key"].isin(set(meta_all))].copy()
    if df.empty:
        return []
    ev, wi, ba = load_model(eval_model), load_model(win_model), load_model(bad_model)
    fb = load_model(favbust_model)
    X = prepare_X(df)
    pp3 = ev.predict_proba(X)[:, 1]
    ppw = wi.predict_proba(X)[:, 1]
    pbd = ba.predict_proba(X)[:, 1]
    preds: dict[str, dict[int, tuple]] = defaultdict(dict)
    for rk, fno, a, b, c in zip(df["race_key"], df["frame_no"], pp3, ppw, pbd):
        preds[rk][int(fno)] = (float(a), float(b), float(c))

    rows, feats = [], []
    for rk, pr in preds.items():
        ents = ents_all.get(rk)
        if not ents or len(ents) != RANK_7H1_NE:
            continue
        row = build_favbust_row(meta_all[rk], ents, pr)
        if row is None:
            continue
        fav = row.pop("_fav")
        roles = roles_of(ents, fav)
        others = sorted((f for f in pr if f != fav), key=lambda f: -pr[f][0])
        tf = rank_7h1_build_legs(others, roles)
        if not tf:
            continue
        u_tf, total = rank_7h1_stakes(len(tf))
        m = meta_all[rk]
        # 表示用の1着率（サイトと同じ正規化）。**vintage の生確率**から作る。
        frames = sorted(pr)
        norm = _normalized([pr[f][1] for f in frames], 1.0) or [0.0] * len(frames)
        nmap = dict(zip(frames, norm))
        nsorted = sorted(norm, reverse=True)
        rows.append({
            # 🔴 判定器の学習に使う 67 特徴。本番 `feature_vector()` の並び
            #    （FAVBUST_FEATURE_COLS）そのままなので、列を変えたら学習側も直す。
            "feat": [float(x) for x in feature_vector(row)],
            "race_key": rk, "race_date": m["race_date"],
            "venue_name": m.get("venue_name"), "race_no": m.get("race_no"),
            "race_type": m.get("race_type"), "grade": m.get("grade"),
            "n_entries": RANK_7H1_NE,
            "fav": fav,
            "gap12": round(row["fav_ppw_gap12"], 6),
            "fav_ppw": round(row["fav_ppw"], 6),
            "fav_pp3": round(row["fav_pp3"], 6),
            "fav_ppw_norm": round(nmap.get(fav, 0.0), 6),
            "gap12_norm": round(nsorted[0] - nsorted[1], 6),
            "others": others,
            "legs": tf, "legs_tf": tf,
            "stake_tf": u_tf, "bet_amount": total,
        })
        feats.append(feature_vector(row))

    if not rows:
        return []
    probs = fb.predict(np.array(feats, dtype=float))
    for r, p in zip(rows, probs):
        r["bust_prob"] = float(p)
    return rows


def score(rows: list[dict]) -> list[dict]:
    """backfill_7h1_rank_wt.build_rows と同一の採点を行い、行に足して返す。"""
    race_keys = [r["race_key"] for r in rows]
    _trio_bd, tf_bd = _load_boards(race_keys)
    fins = _load_finishes(race_keys)
    pm = _load_payouts_wt(race_keys)
    # 本命の着順（バスト判定用）。3着までしか _load_finishes は返さないので別引き。
    fav_fin: dict[str, dict[int, int]] = defaultdict(dict)
    with get_connection() as c:
        for i in range(0, len(race_keys), 900):
            ch = race_keys[i:i + 900]
            q = ("SELECT race_key, frame_no, finish_order FROM wt_entries "
                 "WHERE race_key IN (%s)" % ",".join("?" * len(ch)))
            for rk, fno, fo in c.execute(q, ch):
                fav_fin[rk][int(fno)] = int(fo) if fo is not None else -1

    out = []
    for c in rows:
        rk = c["race_key"]
        tf_lookup = tf_bd.get(rk)
        order = fins.get(rk)
        r = dict(c)
        r["scored"] = False
        if tf_lookup and order:
            legs_tf_all = list(c.get("legs_tf") or [])
            head = int(legs_tf_all[0].split("-")[0])
            if any(k[0] == head for k in tf_lookup):
                legs_tf = [t for t in legs_tf_all if _combo_key(t, True) in tf_lookup]
                if legs_tf:
                    u_tf, bet = rank_7h1_stakes(len(legs_tf))
                    wins_tf = winning_trifectas(order)
                    tf_win = hit_trifecta(
                        [tuple(int(x) for x in t.split("-")) for t in legs_tf
                         if t.count("-") == 2], wins_tf)
                    hit_tf = tf_win is not None
                    tf_odds = pm.get(rk, {}).get(("trifecta", tf_win or wins_tf[0]), 0)
                    r.update({
                        "scored": True,
                        "n_combos": len(legs_tf),
                        "hit": int(hit_tf),
                        "payout": int(tf_odds * u_tf // 100 if hit_tf else 0),
                        "trifecta_payout": int(tf_odds),
                        "bet_amount": int(bet),
                    })
        fo = fav_fin.get(rk, {}).get(c["fav"], -1)
        r["fav_finish"] = fo
        r["fav_bust"] = int(fo <= 0 or fo >= 4)   # 4着以下 or 失格/欠場
        r["fav_win"] = int(fo == 1)
        out.append(r)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="mfrom", default="2024-04")
    ap.add_argument("--to", dest="mto", default=None)
    ap.add_argument("--out", default="data/exp/7h1_gate_cache.jsonl")
    ap.add_argument("--verify", action="store_true",
                    help="build_7h1_candidates.build() と選別結果を突き合わせる")
    args = ap.parse_args()

    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["race_date"][:7])

    fh = out.open("a", encoding="utf-8")
    for (d_from, d_to, ev_m, wi_m) in monthly_windows():
        tag = d_from[:7]
        if tag < args.mfrom or (args.mto and tag > args.mto):
            continue
        if tag in done:
            print(f"[skip] {tag} 済み")
            continue
        ba_m, fb_m = bad_model_name(ev_m), favbust_model_name(ev_m)
        if not (REPO / "data" / "models" / f"{fb_m}.pkl").exists():
            print(f"[skip] {tag} favbust vintage なし ({fb_m})")
            continue
        t0 = time.time()
        rows = build_rows_for_window(d_from, d_to, ev_m, wi_m, ba_m, fb_m)
        if not rows:
            print(f"[{tag}] 母集団0件")
            continue
        # 選別（本番関数をそのまま）
        by_day: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_day[r["race_date"]].append(r)
        sel = set()
        for day in sorted(by_day):
            for r in rank_7h1_daily_select(by_day[day]):
                sel.add(r["race_key"])
        for r in rows:
            r["selected"] = int(r["race_key"] in sel)

        if args.verify:
            from scripts.build_7h1_candidates import build as _build
            ref = {c["race_key"] for c in _build(d_from, d_to, ev_m, wi_m, ba_m, fb_m)}
            assert ref == sel, f"{tag}: build() と不一致 {len(ref)} vs {len(sel)}"
            print(f"[{tag}] verify OK ({len(ref)}件)")

        for r in score(rows):
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        fh.flush()
        print(f"[{tag}] 母集団{len(rows)}件 / 選別{len(sel)}件 "
              f"({time.time() - t0:.0f}秒)")
    fh.close()


if __name__ == "__main__":
    main()
