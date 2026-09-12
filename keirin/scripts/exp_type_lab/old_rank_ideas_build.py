#!/usr/bin/env python3
"""旧ランクの3モデル（favbust / upset_screen / wt_bad）を honest に再スコアして
型ラボの既存量と並べられる台を作る（2026-09-11）。

出力: /tmp/old_rank_ideas_rows.pkl   1行 = 1レース（7車・2024-07〜2026-08）

🔴 モデルは**月次 vintage** を使う（本番モデルを過去へ当てると in-sample）。
   - lgbm_wt_eval_mYYMM / lgbm_wt_win_mYYMM / lgbm_wt_bad_mYYMM  … 選手単位
   - lgbm_wt_favbust_mYYMM                                       … 本命バスト（レース単位）
   - lgbm_upset_screen_n15v{2312,2412,2506}                      … 波乱（レース単位）
⚠️ 2025-12 以降の lgbm_wt_bad_m* は 66列で現在の FEATURE_COLS_WT(70) と一致せず
   `load_model` が落ちる。ここでは pickle を直接読み、モデル自身の列だけを渡す。
"""
from __future__ import annotations

import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.database import get_connection  # noqa: E402
from src.preprocessing.favbust_features import build_favbust_row, feature_vector  # noqa: E402
from src.preprocessing import upset_features as UF  # noqa: E402
from src.preprocessing.feature_wt import load_features_wt, prepare_X  # noqa: E402
from src.type_lab import race_shape  # noqa: E402

import os
DATE_FROM = os.environ.get("ORI_FROM", "2024-07-01")
DATE_TO = os.environ.get("ORI_TO", "2026-08-31")
OUT = os.environ.get("ORI_OUT", "/tmp/old_rank_ideas_rows.pkl")
MODEL_DIR = REPO / "data" / "models"


def _raw(name: str):
    with open(MODEL_DIR / f"{name}.pkl", "rb") as f:
        return pickle.load(f)


def _cols(m):
    c = getattr(m, "feature_name_", None)
    if c is None and hasattr(m, "feature_name"):
        try:
            c = m.feature_name()
        except Exception:
            c = None
    return list(c) if c else None


def _proba(m, X):
    if hasattr(m, "predict_proba"):
        return m.predict_proba(X)[:, 1]
    return m.predict(X)


def _upset_model(d: str):
    if d < "2025-01-01":
        return "lgbm_upset_screen_n15v2312"
    if d < "2025-07-01":
        return "lgbm_upset_screen_n15v2412"
    return "lgbm_upset_screen_n15v2506"


def load_db():
    with get_connection() as c:
        meta = {}
        for r in c.execute(
                "SELECT r.race_key, r.race_date, r.race_no, r.venue_id, r.grade, "
                "       r.race_type, r.day_index, r.start_at, r.distance, r.n_entries, "
                "       v.bank_length, v.is_indoor, v.name AS venue_name "
                "FROM wt_races r LEFT JOIN venue_info v ON r.venue_id = v.venue_code "
                "WHERE r.n_entries = 7 AND r.cancel = 0 "
                "  AND r.race_date BETWEEN ? AND ?", (DATE_FROM, DATE_TO)):
            meta[r["race_key"]] = dict(r)
        keys = sorted(meta)
        ents = defaultdict(list)
        for i in range(0, len(keys), 700):
            ch = keys[i:i + 700]
            q = ("SELECT race_key, frame_no, pred_win_pct, pred_top3_pct, "
                 "       prediction_mark, race_point, line_group, line_size, line_pos, "
                 "       is_line_leader, n_lines, style, prefecture, player_class, "
                 "       first_rate, third_rate, s_count, b_count, finish_order, "
                 "       ex_left_behind_pct "
                 "FROM wt_entries WHERE race_key IN (%s)" % ",".join("?" * len(ch)))
            for r in c.execute(q, ch):
                ents[r["race_key"]].append(dict(r))
    # 🔴 `wt_race_payouts` は database.py のスキーマ接頭辞リストに入っていないため
    #    get_connection() 経由では引けない。ここだけ psycopg2 で直接読む。
    import os as _os
    import psycopg2 as _pg
    pay = {}
    with _pg.connect(_os.environ["KEIRIN_DB_URL"]) as con:
        cur = con.cursor()
        for i in range(0, len(keys), 2000):
            ch = keys[i:i + 2000]
            cur.execute("SELECT race_key, bet_type, payout FROM keirin.wt_race_payouts "
                        "WHERE bet_type IN ('trifecta','trio') AND race_key = ANY(%s)", (ch,))
            for rk, bt, pv in cur.fetchall():
                pay.setdefault(rk, {})[bt] = int(pv)
    return meta, dict(ents), pay


def main() -> None:
    print("DB 読み込み ...", flush=True)
    meta, ents, pay = load_db()
    print(f"  7車レース {len(meta):,}", flush=True)

    print("特徴量 ...", flush=True)
    feat = load_features_wt(os.environ.get("ORI_FEAT_FROM","2022-12-01"), DATE_TO, use_cache=True)
    feat = feat[feat["race_key"].isin(set(meta))].copy()
    feat["_m"] = pd.to_datetime(feat["race_date"]).dt.strftime("%y%m")
    print(f"  対象行 {len(feat):,}", flush=True)

    # ── 選手単位の vintage スコア（月ごと）──
    preds: dict[str, dict[int, tuple]] = defaultdict(dict)
    for m in sorted(feat["_m"].unique()):
        sub = feat[feat["_m"] == m]
        try:
            ev, wi, ba = _raw(f"lgbm_wt_eval_m{m}"), _raw(f"lgbm_wt_win_m{m}"), _raw(f"lgbm_wt_bad_m{m}")
        except FileNotFoundError as e:
            print(f"  [skip] {m} {e}", flush=True)
            continue
        X = prepare_X(sub)
        out = []
        for mod in (ev, wi, ba):
            cs = _cols(mod)
            out.append(_proba(mod, X[cs] if cs else X))
        for rk, fn, a, b, c in zip(sub["race_key"], sub["frame_no"], *out):
            preds[rk][int(fn)] = (float(a), float(b), float(c))
        print(f"  {m} {len(sub):,}行", flush=True)

    # ── レース単位 ──
    fb_cache, us_cache = {}, {}
    rows = []
    for rk, mt in meta.items():
        es = ents.get(rk)
        pr = preds.get(rk)
        if not es or len(es) != 7 or not pr or len(pr) != 7:
            continue
        d = str(mt["race_date"])
        mtag = d[2:4] + d[5:7]
        fin = {int(e["frame_no"]): (int(e["finish_order"]) if e.get("finish_order") else None)
               for e in es}
        top3 = {f for f, o in fin.items() if o in (1, 2, 3)}
        if len(top3) < 3:
            continue

        p3 = {int(e["frame_no"]): float(e["pred_top3_pct"] or 0) / 100 for e in es}
        pw = {int(e["frame_no"]): float(e["pred_win_pct"] or 0) / 100 for e in es}
        if min(p3.values()) <= 0:
            continue
        sh = race_shape(
            p3,
            {int(e["frame_no"]): e.get("line_group") for e in es},
            {int(e["frame_no"]): e.get("line_pos") for e in es},
            {int(e["frame_no"]): e.get("style") for e in es},
            {int(e["frame_no"]): float(e.get("race_point") or 0) for e in es},
            {int(e["frame_no"]): float(e.get("ex_left_behind_pct") or 0) for e in es},
            int(mt.get("day_index") or 0), pw)
        if sh is None:
            continue

        # favbust（軸1 == WT◎ のレースだけ）
        bust_p = np.nan
        row = build_favbust_row(mt, es, pr)
        if row is not None:
            row.pop("_fav")
            nm = f"lgbm_wt_favbust_m{mtag}"
            if nm not in fb_cache:
                try:
                    fb_cache[nm] = _raw(nm)
                except FileNotFoundError:
                    fb_cache[nm] = None
            if fb_cache[nm] is not None:
                v = np.array([feature_vector(row)], dtype=float)
                bust_p = float(_proba(fb_cache[nm], v)[0])

        # upset_screen
        up_p = np.nan
        ur = UF.build_upset_row(es, mt)
        if ur is not None:
            nm = _upset_model(d)
            if nm not in us_cache:
                us_cache[nm] = _raw(nm)
            v = np.array([UF.feature_vector(ur)], dtype=float)
            up_p = float(_proba(us_cache[nm], v)[0])

        a1, a2 = sh.order[0], sh.order[1]
        # 3ヘッド軸（7S 式）: 軸1 = pw 最上位 / 軸2 = z(p3) - 0.5*z(bad)
        pwtop = max(pr, key=lambda f: pr[f][1])
        others = [f for f in pr if f != pwtop]
        p3v = np.array([pr[f][0] for f in others]); bdv = np.array([pr[f][2] for f in others])
        zs = ((p3v - p3v.mean()) / (p3v.std() or 1)) - 0.5 * ((bdv - bdv.mean()) / (bdv.std() or 1))
        a2_3h = others[int(np.argmax(zs))]

        rows.append(dict(
            race_key=rk, date=d, venue=mt.get("venue_name"), rtype=mt.get("race_type"),
            grade=mt.get("grade"), dayi=int(mt.get("day_index") or 0),
            type=sh.type_label, axis_sum=float(sh.axis_sum), arare=int(sh.arare),
            gap=float(sh.gap), pw_ent=float(sh.pw_ent),
            a1=a1, a2=a2, a1_3h=pwtop, a2_3h=a2_3h,
            a1_in3=a1 in top3, a2_in3=a2 in top3, both_in3=(a1 in top3 and a2 in top3),
            a1_1st=(fin.get(a1) == 1),
            both_in3_3h=(pwtop in top3 and a2_3h in top3),
            a1_in3_3h=(pwtop in top3),
            bust_p=bust_p, upset_p=up_p,
            fav_pbad=float(pr[a1][2]), pbad_min=float(min(pr[f][2] for f in pr)),
            a1_pbad=float(pr[a1][2]), a2_pbad=float(pr[a2][2]),
            p3_a1=p3[a1], p3_a2=p3[a2], pw_a1=pw[a1], pw_a2=pw[a2],
            pw_gap12=float(sorted(pw.values(), reverse=True)[0] - sorted(pw.values(), reverse=True)[1]),
            rp_sd=float(np.std([float(e.get("race_point") or 0) for e in es])),
            agree=(pr and max(pr, key=lambda f: pr[f][1]) ==
                   next((int(e["frame_no"]) for e in es if e.get("prediction_mark") == 1), -1)),
            tf_pay=pay.get(rk, {}).get("trifecta"), trio_pay=pay.get(rk, {}).get("trio"),
        ))
    print(f"行 {len(rows):,}", flush=True)
    with open(OUT, "wb") as f:
        pickle.dump(rows, f)
    print("保存", OUT)


if __name__ == "__main__":
    main()
