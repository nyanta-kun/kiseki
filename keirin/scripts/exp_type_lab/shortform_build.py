#!/usr/bin/env python3
"""短期（直近N走）の脚質・調子を point-in-time で作る台（2026-09-14・担当: 短期脚質）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/shortform_build.py

出力 /tmp/shortform/feat.pkl = dict(names=[...], by_key={race_key: (7,F) float32})
車番 = frame_no（1..7）。値が取れない選手は NaN。

## 先に本番を読んだ結果（CLAUDE.md「測る前に本番コードを読む」）

`FEATURE_COLS_WT`（`src/preprocessing/feature_wt.py`）には **短期の量が既に大量に
入っている**:
    win_3m / top3_3m / quin_3m / win_6m / top3_6m / quin_6m / venue_wr / days_since
    / wr_trend                      … 90日・180日窓のローリング（ks流）
    rp_prev_delta / rp_delta_90 / rp_delta_180 / rp_trend  … 競走得点トレンド
    b_rate_90 / s_rate_90 / fh_rel_90 / fh_best_rate_90    … S/B・上がりの90日窓
    cup_n_so_far / cup_top3_rate / cup_win_rate / cup_mean_order_n … 節内成績
つまり **p3 モデルは既に「直近の調子」を見ている**。ここで作るのは
モデルに入っていない形のものに絞る:

  ① **走数ベース**の直近N走（N=3,5,10）。既存は全て**日数窓**で、出走間隔が
     空いた選手では中身が別物になる
  ② **決まり手（`factor`）由来の短期脚質**。`factor` は1・2着の決まり手
     （逃/捲/差/マ）で、モデルは一度も使っていない（`ex_*` は汚染列として除外済み）
  ③ **長期 style ラベルと直近実行のズレ**（脚質が変わっている選手）
  ④ **直近の落車・欠車**（`finish_order = 0`）。モデルは finish_order>=1 しか見ない

## リーク対策

- 履歴は `shift(1)` ＋ (race_date, race_no) 昇順。当日の自レースは必ず窓の外
- `finish_order` が NULL / 0 の行は成績系の値を NaN 化（`add_rp_trend_features_wt`
  と同じ方針。NULL 行は wave-picks の AIスコア上書きが恒久残存しうる）
- 使うのは **過去レースの結果列**（finish_order / res_back / res_standing /
  final_half / factor / race_point）だけ。`ex_*` は一切触らない
"""
from __future__ import annotations

import os
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("/tmp/shortform/feat.pkl")
NS = (3, 5, 10)


def load_history() -> pd.DataFrame:
    from sqlalchemy import create_engine, text as sa_text
    eng = create_engine(os.environ["KEIRIN_DB_URL"])
    sql = sa_text(
        "SELECT e.race_key, e.frame_no, e.player_id, e.finish_order, e.factor, "
        "e.res_standing, e.res_back, e.final_half, e.race_point, e.style, "
        "r.race_date, r.race_no, r.n_entries, r.cup_id, r.day_index "
        "FROM keirin.wt_entries e JOIN keirin.wt_races r ON e.race_key=r.race_key")
    with eng.connect() as c:
        H = pd.read_sql_query(sql, c)
    eng.dispose()
    return H


def build(H: pd.DataFrame) -> tuple[list[str], pd.DataFrame]:
    H = H.copy()
    H["_dt"] = pd.to_datetime(H["race_date"])
    fo = pd.to_numeric(H["finish_order"], errors="coerce")
    ok = fo >= 1                                   # 完走（DNF/欠車/未確定を除く）
    ne = pd.to_numeric(H["n_entries"], errors="coerce").fillna(7.0).clip(lower=2)

    H["_ord"] = (fo / ne).where(ok)                # 正規化着順（小=良）
    H["_top3"] = (fo <= 3).astype(float).where(ok)
    H["_win"] = (fo == 1).astype(float).where(ok)
    H["_dnf"] = (~ok).astype(float)                # 欠車・失格・未確定
    H["_b"] = pd.to_numeric(H["res_back"], errors="coerce").where(ok)
    H["_s"] = pd.to_numeric(H["res_standing"], errors="coerce").where(ok)
    fh = pd.to_numeric(H["final_half"], errors="coerce")
    fh = fh.where(fh > 0).where(ok)
    H["_fh"] = fh
    med = H.groupby("race_key")["_fh"].transform("median")
    H["_fhrel"] = H["_fh"] - med                   # 負=速い
    f = H["factor"].fillna("")
    # 決まり手（1・2着のみ記録）。「その走で何をしたか」の短期脚質。
    H["_nige"] = (f == "逃").astype(float)
    H["_maku"] = (f == "捲").astype(float)
    H["_sashi"] = (f == "差").astype(float)
    H["_mark"] = (f == "マ").astype(float)
    H["_front"] = ((f == "逃") | (f == "捲")).astype(float)   # 自力
    H["_rp"] = pd.to_numeric(H["race_point"], errors="coerce").where(ok)

    H = H.sort_values(["player_id", "_dt", "race_no"]).reset_index(drop=True)
    pid = H["player_id"].to_numpy()
    gs = np.zeros(len(H), np.int64)          # 各行の属する選手ブロックの開始 index
    starts = np.flatnonzero(np.r_[True, pid[1:] != pid[:-1]])
    gs[starts] = starts
    gs = np.maximum.accumulate(gs)
    idx = np.arange(len(H), dtype=np.int64)

    def roll_prev(v: np.ndarray, N: int) -> np.ndarray:
        """直前 N 行（自分を含まない・選手境界を跨がない）の平均。NaN は窓から除外。"""
        ok = np.isfinite(v)
        S = np.r_[0.0, np.cumsum(np.where(ok, v, 0.0))]
        C = np.r_[0, np.cumsum(ok.astype(np.int64))]
        lo = np.maximum(gs, idx - N)
        cnt = C[idx] - C[lo]
        tot = S[idx] - S[lo]
        out = np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan)
        return out

    names: list[str] = []
    cols = {"ord": "_ord", "top3": "_top3", "win": "_win", "dnf": "_dnf",
            "b": "_b", "s": "_s", "fhrel": "_fhrel",
            "nige": "_nige", "maku": "_maku", "sashi": "_sashi",
            "mark": "_mark", "front": "_front", "rp": "_rp"}
    vals = {nm: H[src].to_numpy(dtype=np.float64) for nm, src in cols.items()}
    for N in NS:
        for nm in cols:
            c = f"{nm}{N}"
            H[c] = roll_prev(vals[nm], N)
            names.append(c)
    # 直近走数（履歴の厚み）と出走間隔
    okord = np.isfinite(vals["ord"])
    Cc = np.r_[0, np.cumsum(okord.astype(np.int64))]
    H["nhist"] = (Cc[idx] - Cc[gs]).astype(np.float64)
    dt = H["_dt"].to_numpy("datetime64[D]").astype(np.int64)
    prev = np.where(idx > gs, dt[np.maximum(idx - 1, 0)], np.nan)
    H["dsince"] = dt - prev
    names += ["nhist", "dsince"]
    # 競走得点の直近差（当日発表値 − 直近5走平均）
    H["rp_now"] = pd.to_numeric(H["race_point"], errors="coerce")
    H["rp_d5"] = H["rp_now"] - H["rp5"]
    H["rp_d10"] = H["rp_now"] - H["rp10"]
    names += ["rp_d5", "rp_d10"]
    # 長期 style ラベル（当日の出走表の脚質）と直近実行のズレ
    st = H["style"].fillna("")
    H["st_nige"] = (st == "逃").astype(float)
    H["st_oi"] = (st == "追").astype(float)
    # 「逃」なのに直近10走で自力（逃/捲）が出ていない → 脚質が落ちている
    H["drift_front"] = H["st_nige"] - H["front10"]
    # 「追」なのに直近で自力を出している → 脚質が変わっている
    H["drift_mark"] = H["st_oi"] - H["mark10"]
    names += ["st_nige", "st_oi", "drift_front", "drift_mark"]
    return names, H


def main() -> None:
    import pickle as pk
    rows = pk.load(open("/tmp/ratebranch/rows.pkl", "rb"))
    keys = sorted({r["race_key"] for r in rows})
    print(f"対象レース {len(keys):,}")
    H = load_history()
    print(f"履歴 {len(H):,} 行  {H['race_date'].min()} 〜 {H['race_date'].max()}")
    names, H = build(H)
    print(f"特徴 {len(names)} 本")

    want = set(keys)
    sub = H[H["race_key"].isin(want)]
    by: dict[str, np.ndarray] = {}
    arr = sub[["race_key", "frame_no"] + names].to_numpy(dtype=object)
    for row in arr:
        rk, fn = str(row[0]), int(row[1])
        if not (1 <= fn <= 7):
            continue
        a = by.setdefault(rk, np.full((7, len(names)), np.nan, np.float32))
        a[fn - 1] = np.asarray(row[2:], dtype=np.float64)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("wb") as f:
        pickle.dump({"names": names, "by_key": by}, f)
    full = sum(1 for k in keys if k in by and np.isfinite(by[k]).all())
    print(f"保存 {OUT}  レース {len(by):,}  全車全特徴が有限 {full:,}")


if __name__ == "__main__":
    main()
