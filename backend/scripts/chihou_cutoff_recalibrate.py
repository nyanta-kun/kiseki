"""地方 足切り閾値の再較正（walk-forward・軸=1着取りこぼしの上限）

## 背景

足切りルール `gap >= 30 または (gap >= 24 かつ 指数順位 >= 7)` は **v13 の
composite スケール**で較正した値（除外率 31% を狙って選んだ）。市場特徴 5 本を
外した v14（2026-08-14 デプロイ）は gap 分布が縮み、**実測の除外率は 14.5%** まで
落ちている。詳細は `docs/chihou_cutoff_recalibration_2026_09_02.md`。

## 何を揃えるか（2026-09-06 ユーザー判断）

**1着取りこぼしの上限**を制約に置き、その下で除外率を最大化する。

  - 除外率を揃える案は、同じ除外率で v14 の質が v13 より低いため
    「見た目を保つ代わりに劣化を黙って輸入する」ことになる
  - 除外馬の着外率を揃える案は、切る量を減らすほど良くなる量なので
    単独の目標にすると「何も切らない」が最適解になり、目標として成立しない
  - 1着取りこぼしだけが「足切りの約束が破れたときの損害」を直接測っており、
    許容値を言葉で置ける（既定 3% = v13 運用点の 3.3% 以下）

## なぜ walk-forward なのか

gap は**絶対量**なので、モデルの学習量が変わるとスケールが動く。本番は
`chihou_monthly_rollover.py` が毎月「先月末まで学習 → 当月を採点」を繰り返す。
単一 vintage（train ≤ TRAIN_END）で較正すると、**本番より 1 年ぶん学習の少ない
モデル**に対して閾値を決めることになり、これは `ci.version = 13` 直書きや
`ALL_FEATURES` 学習と同じ「本番に無いモデルへ較正する」型の誤り。

そこで評価月ごとに **その前月末までで学習し直した vintage モデル**で採点する。
学習レシピ（`train_binary_control` / `PROD_FEATURES` / seed 0 / 固定 round）は
本番と同一にし、**学習窓だけ**を月次で動かす。

## 探索窓と確認窓

  W1 / W2 の 2 窓に割って**両窓で制約を満たす**ものだけを候補にする。
  1 窓だけで選ぶと good period に乗っただけの候補が通る（JRA 平八バッジで
  「2窓とも複勝ROI>1」が候補の 47% を通してしまった前例がある）。

  確認は本スクリプトではやらない。`chihou_protocol.TEST_START` 以降は
  月次ローリング（`chihou_monthly_rollover.py`）の一度きり評価に任せる。

使い方:
    cd backend
    .venv/bin/python scripts/chihou_cutoff_recalibrate.py
    .venv/bin/python scripts/chihou_cutoff_recalibrate.py --max-winner-cut 0.03
    .venv/bin/python scripts/chihou_cutoff_recalibrate.py --cache /tmp/wf.pkl  # 2回目以降
"""
from __future__ import annotations

import argparse
import calendar
import datetime
import json
import logging
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts.chihou_cutoff_venue_review import summarize, wilson  # noqa: E402
from scripts.chihou_rank_quality_review import DATA_START, connect  # noqa: E402
from scripts.inference_chihou_v14 import fetch_all_entrants  # noqa: E402
from scripts.train_chihou_market_lgb import (  # noqa: E402
    PROD_FEATURES,
    fetch,
    prep,
    train_binary_control,
)
from scripts.train_chihou_v11_lightgbm import fetch_hist  # noqa: E402
from src.chihou_protocol import TEST_START, VAL_START  # noqa: E402
from src.indices.chihou_calculator import _scale_to_index_local  # noqa: E402
from src.indices.chihou_cutoff import cut_flags  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_cutoff_recal")

OUT_DIR = _root.parent / "docs" / "model_verification"


def month_range(start: str, end: str) -> list[str]:
    """YYYYMM の並びを返す（両端含む）。"""
    y, m = int(start[:4]), int(start[4:6])
    ey, em = int(end[:4]), int(end[4:6])
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def month_bounds(ym: str) -> tuple[str, str]:
    y, m = int(ym[:4]), int(ym[4:6])
    last = calendar.monthrange(y, m)[1]
    return f"{ym}01", f"{ym}{last:02d}"


def prev_day(date: str) -> str:
    d = datetime.datetime.strptime(date, "%Y%m%d").date() - datetime.timedelta(days=1)
    return d.strftime("%Y%m%d")


def build_walkforward(conn, months: list[str]) -> pd.DataFrame:
    """評価月ごとに「前月末まで学習した vintage モデル」で composite を作る。"""
    logger.info("学習用データ取得 %s〜%s", DATA_START, month_bounds(months[-1])[1])
    df_hist = fetch_hist(conn)
    tr_all = prep(conn, fetch(conn, DATA_START, month_bounds(months[-1])[1]), df_hist)
    tr_all["finish_position"] = pd.to_numeric(tr_all["finish_position"], errors="coerce")
    tr_all = tr_all[tr_all["finish_position"].notna() & (tr_all["finish_position"] > 0)]

    logger.info("評価用データ取得（出走取消・失格を含む出走馬全体）")
    ev_all = prep(
        conn,
        fetch_all_entrants(conn, month_bounds(months[0])[0], month_bounds(months[-1])[1]),
        df_hist,
    )
    ev_all["finish_position"] = pd.to_numeric(ev_all["finish_position"], errors="coerce")

    feats = list(PROD_FEATURES)
    parts = []
    for ym in months:
        m_start, m_end = month_bounds(ym)
        train_end = prev_day(m_start)
        tr = tr_all[tr_all["date"] <= train_end]
        ev = ev_all[(ev_all["date"] >= m_start) & (ev_all["date"] <= m_end)]
        if ev.empty:
            logger.warning("%s: 評価データなし。スキップ", ym)
            continue
        x_tr = tr[feats].fillna(0.0).to_numpy(dtype=np.float64)
        y_tr = (tr["finish_position"] <= 3).astype(int).to_numpy()
        model = train_binary_control(x_tr, y_tr, seed=0, feature_names=feats)
        p = model.predict(ev[feats].fillna(0.0).to_numpy(dtype=np.float64))
        e = ev.copy()
        e["_p"] = p
        # 本番と同じスケーリング（レース内 _scale_to_index_local）
        e["composite_index"] = e.groupby("race_id")["_p"].transform(
            lambda s: pd.Series(_scale_to_index_local(s.tolist()), index=s.index)
        )
        e["ym"] = ym
        parts.append(e[["date", "ym", "course_name", "race_id", "horse_id",
                        "composite_index", "head_count", "finish_position"]])
        logger.info("%s: train ≤%s %d行 → 評価 %d頭 / %dR",
                    ym, train_end, len(tr), len(ev), ev["race_id"].nunique())
    return pd.concat(parts, ignore_index=True)


def mark(df: pd.DataFrame, hard: float, soft: float, rank_min: int) -> np.ndarray:
    """レース単位で足切りフラグを立てる（判定の正本は chihou_cutoff.cut_flags）。

    位置はこの DataFrame 内で数え直す。呼び出し側が窓で切ったスライスを渡すため、
    元 df の行番号を使うとずれる。
    """
    d = df.reset_index(drop=True)
    cut = np.zeros(len(d), dtype=bool)
    for _, g in d.groupby("race_id", sort=False):
        flags = cut_flags([float(x) for x in g["composite_index"].to_numpy(dtype=float)],
                          gap_hard=hard, gap_soft=soft, rank_min=rank_min)
        cut[g.index.to_numpy()] = flags
    return cut


def evaluate(df: pd.DataFrame, hard: float, soft: float, rank_min: int) -> dict:
    d = df.reset_index(drop=True)
    d["cut"] = mark(d, hard, soft, rank_min)
    return summarize(d)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--val-start", default=VAL_START[:6], help="YYYYMM")
    ap.add_argument("--val-end", default=None, help="YYYYMM（既定=TEST_START の前月）")
    ap.add_argument("--split", default=None, help="W2 の開始月 YYYYMM（既定=窓の中央）")
    ap.add_argument("--max-winner-cut", type=float, default=0.03,
                    help="1着取りこぼしの上限（既定 3%% = v13 運用点 3.3%% 以下）")
    ap.add_argument("--cache", default="/tmp/chihou_cutoff_wf.pkl")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--json-out", default=str(OUT_DIR / "chihou_cutoff_recalibrate.json"))
    args = ap.parse_args()

    val_end = args.val_end or month_range("200001", TEST_START[:6])[-2]
    months = month_range(args.val_start, val_end)
    logger.info("VAL %s〜%s（%d か月）", months[0], months[-1], len(months))

    cache = Path(args.cache)
    if cache.exists() and not args.rebuild:
        logger.info("キャッシュを使う: %s", cache)
        df = pd.read_pickle(cache)
    else:
        conn = connect()
        try:
            df = build_walkforward(conn, months)
        finally:
            conn.close()
        df.to_pickle(cache)
        logger.info("キャッシュ保存: %s", cache)

    df = df.reset_index(drop=True)

    split = args.split or months[len(months) // 2]
    w1 = df[df["ym"] < split]
    w2 = df[df["ym"] >= split]
    logger.info("W1 %s〜%s / W2 %s〜%s", months[0], prev_month_label(split), split, months[-1])

    grid = []
    for hard in range(14, 33):
        for offset in (4, 6, 8):
            soft = hard - offset
            if soft < 5:
                continue
            for rank_min in (5, 7):
                grid.append((float(hard), float(soft), rank_min))

    rows = []
    for hard, soft, rank_min in grid:
        r1 = evaluate(w1, hard, soft, rank_min)
        r2 = evaluate(w2, hard, soft, rank_min)
        rows.append({
            "hard": hard, "soft": soft, "rank_min": rank_min,
            "w1": r1, "w2": r2,
            "ok": (r1["winner_cut_rate"] <= args.max_winner_cut
                   and r2["winner_cut_rate"] <= args.max_winner_cut),
            "min_cut_rate": min(r1["cut_rate"], r2["cut_rate"]),
        })

    ok = [r for r in rows if r["ok"]]
    ok.sort(key=lambda r: -r["min_cut_rate"])

    print()
    print(f"■ 制約: 1着取りこぼし <= {args.max_winner_cut:.1%} を W1・W2 の両方で満たす")
    print(f"  W1 {months[0]}〜{prev_month_label(split)} / W2 {split}〜{months[-1]}")
    print(f"  候補 {len(ok)} / {len(rows)} 通り")
    print()
    hdr = (f"{'hard/soft/順位':>16} | {'除外率':>14} | {'着外率':>14} | "
           f"{'1着落ち':>14} | {'3着内落ち':>14}")
    print(hdr)
    print("-" * len(hdr))
    for r in ok[:12]:
        lab = f"{r['hard']:g}/{r['soft']:g}/{r['rank_min']}"
        print(f"{lab:>16} | "
              f"{r['w1']['cut_rate']:6.1%} {r['w2']['cut_rate']:6.1%} | "
              f"{r['w1']['cut_out_rate']:6.1%} {r['w2']['cut_out_rate']:6.1%} | "
              f"{r['w1']['winner_cut_rate']:6.1%} {r['w2']['winner_cut_rate']:6.1%} | "
              f"{r['w1']['placer_cut_rate']:6.1%} {r['w2']['placer_cut_rate']:6.1%}")

    print()
    print("■ 参考: 現行 30/24/7 と v13 の運用点付近")
    for hard, soft, rank_min in [(30.0, 24.0, 7), (24.0, 18.0, 7)]:
        r1 = evaluate(w1, hard, soft, rank_min)
        r2 = evaluate(w2, hard, soft, rank_min)
        lab = f"{hard:g}/{soft:g}/{rank_min}"
        print(f"{lab:>16} | "
              f"{r1['cut_rate']:6.1%} {r2['cut_rate']:6.1%} | "
              f"{r1['cut_out_rate']:6.1%} {r2['cut_out_rate']:6.1%} | "
              f"{r1['winner_cut_rate']:6.1%} {r2['winner_cut_rate']:6.1%} | "
              f"{r1['placer_cut_rate']:6.1%} {r2['placer_cut_rate']:6.1%}")

    print()
    print("■ gap 分位（walk-forward・本番と同じレシピ）")
    g = df.groupby("race_id")["composite_index"]
    gap = (g.transform("max") - df["composite_index"]).to_numpy()
    for q in (50, 75, 90, 95):
        print(f"  p{q}: {np.percentile(gap, q):.1f}")

    if ok:
        best = ok[0]
        print()
        print(f"★ 制約下で除外率が最大なのは {best['hard']:g}/{best['soft']:g}/"
              f"{best['rank_min']}（除外率 W1 {best['w1']['cut_rate']:.1%} / "
              f"W2 {best['w2']['cut_rate']:.1%}）")
        k1 = int(round(best["w1"]["winner_cut_rate"] * best["w1"]["n_win"]))
        lo, hi = wilson(k1, best["w1"]["n_win"])
        print(f"  W1 の1着取りこぼし 95%CI: [{lo:.1%}, {hi:.1%}]")

    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_out).write_text(json.dumps({
        "generated": datetime.date.today().isoformat(),
        "val_months": months,
        "split": split,
        "max_winner_cut": args.max_winner_cut,
        "recipe": "walk-forward monthly / train_binary_control / PROD_FEATURES / seed 0",
        "rows": rows,
    }, ensure_ascii=False, indent=2, default=float))
    logger.info("JSON: %s", args.json_out)


def prev_month_label(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[4:6])
    m -= 1
    if m == 0:
        y, m = y - 1, 12
    return f"{y:04d}{m:02d}"


if __name__ == "__main__":
    main()
