#!/usr/bin/env python3
"""未使用の朝入力に価値があるか① — 選手コメント（`wt_entries.comment`）（2026-09-10）。

## 棚卸しの結論（先に本番を読む）

朝（7:15 の推奨バッチ）に使えて **FEATURE_COLS_WT に入っていない**列:

  🟢 `comment`（選手コメント・自由テキスト）… **完全に未使用**。本稿の対象
  🔴 `pred_win_pct` / `pred_top3_pct` / `pred_top2_pct` … **自社モデルの出力**
     （`backfill_index_pct_wt.py` が書き戻す）。外部情報ではない
  🔴 `ex_spurt_pct` / `ex_thrust_pct` / `ex_left_behind_pct` / `ex_split_line_pct`
     / `ex_snatch_pct` … **開催期間中に値が更新される**（1.5〜24.9% のペアで変化・
     `feature_wt.py` の実測）。朝の値と学習値がずれる train/serve skew。採用不可
  🔴 天候・風（`wt_race_conditions.fc_*` / `wt_weather`）… **G06 で検証済み・
     Phase1 不通過（無情報）**。再検証しない
  🟡 `second_rate` / `front_runner`〜`marker` / `cup_name` … 既存列と重複が濃い

## 測り方

ベースラインは `wt_entries.pred_top3_pct`（**月次凍結 vintage モデルの backfill＝
honest OOS**）。その上にコメント由来の特徴を足して、確認窓で AUC が動くかを見る。
学習は探索窓のみ。
"""
from __future__ import annotations

import csv
import re
import sys

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

CSV = sys.argv[1] if len(sys.argv) > 1 else "/tmp/comments.csv"
EX = ("2024-07-01", "2025-12-31")
CF = ("2026-01-01", "2026-08-26")


def load():
    rows = []
    with open(CSV) as f:
        for d in csv.DictReader(f):
            fo = d["finish_order"]
            p3 = d["pred_top3_pct"]
            if not fo or not p3:
                continue
            try:
                fo = int(float(fo))
            except ValueError:
                continue
            if fo < 1:
                continue
            rows.append(dict(date=d["race_date"], key=d["race_key"],
                             frame=int(d["frame_no"]), com=d["comment"] or "",
                             p3=float(p3) / 100.0,
                             mark=int(float(d["prediction_mark"] or 0)),
                             y=1 if fo <= 3 else 0))
    return rows


def main() -> None:
    rows = load()
    ex = [r for r in rows if EX[0] <= r["date"] <= EX[1]]
    cf = [r for r in rows if CF[0] <= r["date"] <= CF[1]]
    print(f"探索 {len(ex):,} / 確認 {len(cf):,}")
    for nm, s in (("探索", ex), ("確認", cf)):
        has = [r for r in s if r["com"].strip()]
        L = [len(r["com"]) for r in has]
        print(f"  {nm}: コメントあり {len(has)/len(s)*100:5.2f}%  "
              f"平均 {np.mean(L):.1f}文字  中央 {np.median(L):.0f}  "
              f"ユニーク率 {len({r['com'] for r in has})/len(has)*100:.1f}%")

    yx = np.array([r["y"] for r in ex]); yc = np.array([r["y"] for r in cf])
    b_ex = np.array([r["p3"] for r in ex]); b_cf = np.array([r["p3"] for r in cf])
    print(f"\n■ ベースライン（pred_top3_pct・月次vintage backfill）"
          f"  AUC 探索 {roc_auc_score(yx, b_ex):.4f} / 確認 {roc_auc_score(yc, b_cf):.4f}")

    # ── コメント単体の情報量 ────────────────────────────────────────────
    tx = [r["com"] for r in ex]; tc = [r["com"] for r in cf]
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3),
                          min_df=30, max_features=30000, sublinear_tf=True)
    Xx = vec.fit_transform(tx); Xc = vec.transform(tc)
    print(f"  語彙 {Xx.shape[1]:,}")
    m = LogisticRegression(C=1.0, max_iter=1000)
    m.fit(Xx, yx)
    sx = m.decision_function(Xx); sc = m.decision_function(Xc)
    print(f"\n■ コメント単体（char 2-3gram TF-IDF + ロジスティック）"
          f"  AUC 探索 {roc_auc_score(yx, sx):.4f} / **確認 {roc_auc_score(yc, sc):.4f}**")

    # ── 増分（ベースライン + コメント）──────────────────────────────────
    def logit(p):
        p = np.clip(p, 1e-4, 1 - 1e-4)
        return np.log(p / (1 - p))
    Zx = np.c_[logit(b_ex), sx]; Zc = np.c_[logit(b_cf), sc]
    m2 = LogisticRegression(max_iter=1000).fit(Zx, yx)
    px = m2.predict_proba(Zx)[:, 1]; pc = m2.predict_proba(Zc)[:, 1]
    a0, a1 = roc_auc_score(yc, b_cf), roc_auc_score(yc, pc)
    print(f"■ ベースライン + コメント  AUC 確認 {a1:.4f}  （ベース {a0:.4f} / "
          f"**Δ {a1-a0:+.4f}**）  係数 {m2.coef_[0]}")

    # ブートストラップCI（レース単位）
    keys = np.array([r["key"] for r in cf])
    uk = np.unique(keys)
    idx = {k: np.flatnonzero(keys == k) for k in uk}
    rng = np.random.default_rng(0)
    ds = []
    for _ in range(300):
        pick = rng.choice(len(uk), len(uk))
        sel = np.concatenate([idx[uk[j]] for j in pick])
        ds.append(roc_auc_score(yc[sel], pc[sel]) - roc_auc_score(yc[sel], b_cf[sel]))
    print(f"   ΔAUC 95%CI [{np.percentile(ds,2.5):+.4f}, {np.percentile(ds,97.5):+.4f}]")

    # ── レース内相対でも見る（順位を動かすか）──────────────────────────
    print("\n■ レース内順位（3着内の1位馬相当）で見る")
    for nm, sc_arr, base in (("ベースのみ", b_cf, None), ("ベース+コメント", pc, b_cf)):
        top = 0; n = 0
        for k, ii in ((k, np.flatnonzero(keys == k)) for k in uk):
            if len(ii) < 3:
                continue
            j = ii[np.argmax(sc_arr[ii])]
            top += yc[j]; n += 1
        print(f"    {nm:14s} 1位に置いた選手の3着内率 {top/n*100:5.2f}%  （{n:,}レース）")

    # ── どんな語が効いているか（解釈）────────────────────────────────
    names = np.array(vec.get_feature_names_out())
    co = m.coef_[0]
    o = np.argsort(co)
    print("\n■ 係数の大きい n-gram（プラス＝3着内寄り）")
    print("    +: " + " / ".join(f"{names[j]}({co[j]:+.2f})" for j in o[-15:][::-1]))
    print("    -: " + " / ".join(f"{names[j]}({co[j]:+.2f})" for j in o[:15]))


if __name__ == "__main__":
    main()
