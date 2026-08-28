"""honest な残差キャッシュを作る（2026・7車・三連複と三連単）。

    KEIRIN_ODDS_MODEL_DIR=data/backup/odds_model_20260816 \
      PYTHONPATH=. .venv/bin/python scripts/exp_oddspred_gap/03b_build_resid.py [trio|tf]

出力: data/exp_cache/oddspred_resid_{trio,tf}_2026.pkl
      列 = rk, date, venue, race_type, c1..c3(車番), p1..p3(選手ID), pred, final, resid

🔴 三連複は **必ず vintage（train_end 2025-12-31）** を指すこと。本番モデルは
   2026-08-04 まで学習しているので 2026 を採点すると in-sample になる。
   三連単の本番モデルは train_end 2025-12-31 なので 2026 はそのまま honest。
   入力の p3/pw は本番と同じ `wt_entries.pred_*`（入稿経路と同じ数字で測るため）。
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

from _common import CACHE, q  # noqa: E402

KIND = (sys.argv[1] if len(sys.argv) > 1 else "trio").lower()
N_CAR = int(sys.argv[2]) if len(sys.argv) > 2 else 7      # 9 で9車（三連複のみ）
if KIND == "trio":
    from src.odds_prediction import load_meta, predict_board  # noqa: E402
    BET, KEYF = "trio", frozenset
    OUT = CACHE / f"oddspred_resid_trio{'' if N_CAR == 7 else N_CAR}_2026.pkl"
    if load_meta()["per_n_car"][str(N_CAR)]["train_end"] >= "2026-01-01":
        raise SystemExit("本番モデルでは in-sample です。KEIRIN_ODDS_MODEL_DIR に vintage を指すこと")
else:
    from src.odds_prediction_tf import load_meta, predict_board  # noqa: E402
    BET, KEYF = "trifecta", tuple
    OUT = CACHE / "oddspred_resid_tf_2026.pkl"
    if load_meta()["per_n_car"][str(N_CAR)]["train_end"] >= "2026-01-01":
        raise SystemExit("学習終端が 2026 以降です（in-sample）")
print(f"{KIND} {N_CAR}車: train_end {load_meta()['per_n_car'][str(N_CAR)]['train_end']}", flush=True)

ENT_COLS = ("race_point", "prediction_mark", "player_class", "style", "line_group",
            "line_size", "line_pos", "is_line_leader", "first_rate", "second_rate", "third_rate")
races = {r["race_key"]: (r["venue_id"], r["race_type"]) for r in q(
    "SELECT race_key, venue_id, race_type FROM keirin.wt_races "
    "WHERE race_date BETWEEN '20260101' AND '20260828' AND n_entries=%s", (N_CAR,))}
keys = sorted(races)
print(f"{N_CAR}車レース", len(keys), flush=True)

rows, CH = [], 300
for i in range(0, len(keys), CH):
    ch = keys[i:i + CH]
    ent = defaultdict(list)
    for r in q(f"SELECT race_key, frame_no, player_id, pred_win_pct, pred_top3_pct, "
               f"{', '.join(ENT_COLS)} FROM keirin.wt_entries WHERE race_key = ANY(%s)", (ch,)):
        ent[r["race_key"]].append(dict(r))
    bd = defaultdict(dict)
    for r in q("SELECT race_key, combination, odds_value FROM keirin.wt_odds "
               "WHERE bet_type=%s AND race_key = ANY(%s)", (BET, ch)):
        if r["odds_value"]:
            bd[r["race_key"]][KEYF(int(v) for v in re.split(r"[-=>]", r["combination"]))] = \
                float(r["odds_value"])
    # 三連複は全点そろうことを求める（7車35点 / 9車84点）。三連単は打ち切り点が落ちる
    need = (35 if N_CAR == 7 else 84) if BET == "trio" else 200
    for rk in ch:
        es, board = ent.get(rk), bd.get(rk)
        if not es or len(es) != N_CAR or not board or len(board) < need:
            continue
        cars, p3, pw, meta, pid, ok = [], {}, {}, {}, {}, True
        for d in es:
            car = int(d["frame_no"])
            if d["pred_top3_pct"] is None or d["pred_win_pct"] is None or d["race_point"] is None:
                ok = False
                break
            cars.append(car)
            p3[car], pw[car] = float(d["pred_top3_pct"]) / 100, float(d["pred_win_pct"]) / 100
            pid[car] = d["player_id"]
            meta[car] = {k: d[k] for k in ENT_COLS}
            meta[car]["mark"] = d["prediction_mark"]
        if not ok:
            continue
        # p3 の高い順に 0,1,2,… （軸2車総流しなどプラン形状の再現に使う）
        rank3 = {c: i for i, c in enumerate(sorted(cars, key=lambda c: -p3[c]))}
        try:
            pb = predict_board(cars, p3, pw, meta)
        except Exception:
            continue
        venue, rtype = races[rk]
        for cb, po in pb.items():
            fo = board.get(cb if BET == "trio" else tuple(cb))
            if not fo or fo >= 9000:     # 三連単の表示上限（右側打ち切り）は落とす
                continue
            cs = sorted(cb)
            rows.append((rk, rk[:8], venue, rtype, cs[0], cs[1], cs[2],
                         pid[cs[0]], pid[cs[1]], pid[cs[2]],
                         rank3[cs[0]], rank3[cs[1]], rank3[cs[2]],
                         float(po), float(fo), float(np.log10(fo / po))))
    if (i // CH) % 10 == 0:
        print(f"  {i+len(ch)}/{len(keys)} rows={len(rows)}", flush=True)

df = pd.DataFrame(rows, columns=["rk", "date", "venue", "race_type", "c1", "c2", "c3",
                                 "p1", "p2", "p3", "k1", "k2", "k3", "pred", "final", "resid"])
CACHE.mkdir(parents=True, exist_ok=True)
df.to_pickle(OUT)
print(f"保存 {OUT} {df.shape} レース {df.rk.nunique()}")
print(f"  logMAE {df.resid.abs().mean():.4f}  ±2倍 {100*(df.resid.abs()<np.log10(2)).mean():.2f}%"
      f"  中央 確定/予測 {10**df.resid.median():.4f}")
