"""地方 足切り（Web グレーアウト）精度の競馬場別検証

v13 で採用した足切りルール
  `gap >= 30 || (gap >= 24 && composite順位 >= 7)`
  （gap = レース内最高 composite − その馬の composite）
が、**競馬場ごとに同じように効いているか**を測る。全体では
除外31.2% / 除外馬の着外率93.6% / 1着取りこぼし2.9% だが、
頭数・堅さ・時計水準が場ごとに違うため偏りがあり得る。

## 2つのモードを併記する（片方だけ見ると誤る）

`--source db`（既定）: **本番 DB の v13 composite をそのまま使う**。
  実際に Web に出ている表示の再現。ただし v13 の過去分は全期間学習の単一モデルを
  遡及適用した **in-sample** 値（model-vintage look-ahead）なので、
  **数字は楽観側に出る**。運用実態の確認用であって汎化性能ではない。

`--source honest`: train ≤20250630 / valid 〜20251231 で **学習し直したモデル**で
  対象期間を予測し、同じスケール・同じルールを当てる。汎化性能の確認用。

## 母集団（生存者バイアス対策）

順位と gap は **出走取消・失格を含む出走馬全体**で確定させてから、
確定結果のある馬だけに絞って的中/取りこぼしを数える。本番
`chihou_recommender.rank_by_hn` と母集団を揃えるため
（memory: chihou_survivor_bias_audit_2026_07_23）。

## 指標

  除外率          : 足切りされる馬の割合
  除外馬の着外率  : 足切りされた馬のうち実際に4着以下だった割合 ← 高いほど良い
  1着取りこぼし  : 1着馬を足切りしてしまった割合               ← 低いほど良い
  3着内取りこぼし: 3着内馬を足切りしてしまった割合             ← 低いほど良い

⚠️ `--from` が `chihou_protocol.TEST_START` 以降のとき、
   `record_test_usage()` で使用履歴を台帳に残す（一度きり評価の追跡）。

使い方:
    cd backend
    .venv/bin/python scripts/chihou_cutoff_venue_review.py --from 20260701 --to 20260731
    .venv/bin/python scripts/chihou_cutoff_venue_review.py --from 20260701 --to 20260731 --source honest
"""

from __future__ import annotations

import argparse
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

from scripts.chihou_rank_quality_review import (  # noqa: E402
    DATA_START,
    VALID_END,
    connect,
    train_binary,
)
from scripts.inference_chihou_v14 import fetch_all_entrants  # noqa: E402
from scripts.train_chihou_market_lgb import ALL_FEATURES, fetch, prep  # noqa: E402
from scripts.train_chihou_v11_lightgbm import fetch_hist  # noqa: E402
from src.chihou_protocol import TEST_START, TRAIN_END, record_test_usage  # noqa: E402
from src.indices.chihou_calculator import _scale_to_index_local  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chihou_cutoff_venue")

MODELS_DIR = _root / "models"

# frontend/src/components/ChihouRaceDetailClient.tsx と一致させること
CUT_GAP_HARD = 30.0
CUT_GAP_SOFT = 24.0
CUT_RANK_MIN = 7

DB_SQL = """
SELECT r.date, r.course_name, ci.race_id, ci.horse_id,
       ci.composite_index, r.head_count,
       rr.finish_position, rr.abnormality_code
FROM chihou.calculated_indices ci
JOIN chihou.races r ON r.id = ci.race_id
LEFT JOIN chihou.race_results rr
       ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE ci.version = 13
  AND r.course != '83'
  AND r.date BETWEEN %(s)s AND %(e)s
"""


def mark_cut(df: pd.DataFrame, comp_col: str) -> pd.DataFrame:
    """出走馬全体で gap と順位を確定させ、足切りフラグを立てる。"""
    d = df.copy()
    g = d.groupby("race_id")[comp_col]
    d["gap"] = g.transform("max") - d[comp_col]
    d["rank"] = g.rank(ascending=False, method="first")
    d["cut"] = (d["gap"] >= CUT_GAP_HARD) | (
        (d["gap"] >= CUT_GAP_SOFT) & (d["rank"] >= CUT_RANK_MIN)
    )
    return d


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """二項比率の Wilson 95%信頼区間。

    場ごとの n は 300〜1,700 頭とばらつくため、率の差が標本サイズ由来かを
    見分けられるようにする。12場×複数指標＝多重比較になるので、
    CI が重なる差を「傾向」と読まないこと。
    """
    if n <= 0:
        return (float("nan"), float("nan"))
    p_hat = k / n
    denom = 1 + z * z / n
    centre = (p_hat + z * z / (2 * n)) / denom
    half = z * ((p_hat * (1 - p_hat) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def summarize(d: pd.DataFrame) -> dict:
    """確定結果のある馬だけで指標を出す。"""
    fin = d[d["finish_position"].notna() & (d["finish_position"] > 0)]
    if fin.empty:
        return {}
    cut = fin["cut"].to_numpy()
    fp = fin["finish_position"].to_numpy()
    n_win = int((fp == 1).sum())
    n_place = int((fp <= 3).sum())
    n_cut = int(cut.sum())
    return {
        "races": int(fin["race_id"].nunique()),
        "horses": int(len(fin)),
        "n_cut": n_cut,
        "n_win": n_win,
        "n_place": n_place,
        "cut_rate": float(cut.mean()),
        "cut_out_rate": float((fp[cut] >= 4).mean()) if cut.any() else float("nan"),
        "cut_out_ci": wilson(int((fp[cut] >= 4).sum()), n_cut),
        "winner_cut_rate": float(cut[fp == 1].sum() / max(1, n_win)),
        "winner_cut_ci": wilson(int(cut[fp == 1].sum()), n_win),
        "placer_cut_rate": float(cut[fp <= 3].sum() / max(1, n_place)),
        "placer_cut_ci": wilson(int(cut[fp <= 3].sum()), n_place),
        "avg_head_count": float(fin.groupby("race_id")["head_count"].first().mean()),
    }


def load_db(conn, start: str, end: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(DB_SQL, {"s": start, "e": end})
    df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    cur.close()
    for c in ["composite_index", "finish_position", "abnormality_code", "head_count"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # 出走取消・発走除外は「結果なし」として扱う（順位確定の母集団には残す）
    df.loc[df["abnormality_code"].isin([1, 2]), "finish_position"] = np.nan
    return df


def build_honest(conn, start: str, end: str, seeds: list[int]) -> pd.DataFrame:
    """train ≤TRAIN_END で学習し直したモデルで対象期間を予測する。"""
    logger.info(f"学習データ取得 {DATA_START}〜{VALID_END}")
    df_tr_raw = fetch(conn, DATA_START, VALID_END)
    df_hist = fetch_hist(conn)
    df_tv = prep(conn, df_tr_raw, df_hist)
    df_tv["finish_position"] = pd.to_numeric(df_tv["finish_position"], errors="coerce")
    df_tv = df_tv[df_tv["finish_position"].notna() & (df_tv["finish_position"] > 0)]
    tr = df_tv[df_tv["date"] <= TRAIN_END]
    va = df_tv[df_tv["date"] > TRAIN_END]
    logger.info(f"train {len(tr):,} / valid {len(va):,}")

    logger.info(f"対象期間の出走馬全体を取得 {start}〜{end}")
    te_raw = fetch_all_entrants(conn, start, end)
    te = prep(conn, te_raw, df_hist)
    te["finish_position"] = pd.to_numeric(te["finish_position"], errors="coerce")

    logger.info("is_top3 学習・予測")
    p = train_binary(tr, va, te,
                     (tr["finish_position"] <= 3).astype(int).values,
                     (va["finish_position"] <= 3).astype(int).values,
                     list(ALL_FEATURES), seeds)
    te = te.copy()
    te["_p"] = p
    te["composite_index"] = te.groupby("race_id")["_p"].transform(
        lambda s: pd.Series(_scale_to_index_local(s.tolist()), index=s.index))
    return te[["date", "course_name", "race_id", "horse_id", "composite_index",
               "head_count", "finish_position"]]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="start", default="20260701")
    p.add_argument("--to", dest="end", default="20260731")
    p.add_argument("--source", choices=["db", "honest"], default="db")
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--json-out", default=None)
    args = p.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]

    if args.start >= TEST_START:
        record_test_usage(
            f"足切り精度の競馬場別検証（{args.start}〜{args.end} / source={args.source}）",
            "chihou_cutoff_venue_review.py",
            f"ルール gap>={CUT_GAP_HARD:g} or (gap>={CUT_GAP_SOFT:g} and rank>={CUT_RANK_MIN})",
        )

    conn = connect()
    try:
        if args.source == "db":
            df = load_db(conn, args.start, args.end)
        else:
            df = build_honest(conn, args.start, args.end, seeds)
    finally:
        conn.close()

    if df.empty:
        logger.error("対象行がありません")
        sys.exit(1)

    d = mark_cut(df, "composite_index")

    rows = []
    overall = summarize(d)
    for venue, sub in d.groupby("course_name"):
        s = summarize(sub)
        if s:
            s["course_name"] = venue
            rows.append(s)
    res = pd.DataFrame(rows).sort_values("cut_out_rate", ascending=False)

    label = ("本番DB v13（in-sample・運用実態の再現）" if args.source == "db"
             else f"honest 再学習（train ≤{TRAIN_END}）")
    print("\n" + "=" * 104)
    print(f"地方 足切り精度 競馬場別  {args.start}〜{args.end}  source={label}")
    print(f"ルール: gap>={CUT_GAP_HARD:g} または (gap>={CUT_GAP_SOFT:g} かつ 指数順位>={CUT_RANK_MIN})")
    print("=" * 104)
    hdr = (f"{'競馬場':<8}{'R数':>6}{'頭数':>7}{'平均頭数':>9}{'除外率':>9}"
           f"{'除外馬の着外率(95%CI)':>26}{'1着取りこぼし':>13}{'3着内取りこぼし':>16}")
    print(hdr)
    print("-" * 104)
    for _, r in res.iterrows():
        lo, hi = r["cut_out_ci"]
        print(f"{r['course_name']:<8}{int(r['races']):>6}{int(r['horses']):>7,}"
              f"{r['avg_head_count']:>9.1f}{r['cut_rate']:>9.1%}"
              f"{r['cut_out_rate']:>9.1%} [{lo:.1%}-{hi:.1%}]"
              f"{r['winner_cut_rate']:>13.1%}{r['placer_cut_rate']:>16.1%}")
    print("-" * 104)
    print(f"{'全体':<8}{int(overall['races']):>6}{int(overall['horses']):>7,}"
          f"{overall['avg_head_count']:>9.1f}{overall['cut_rate']:>9.1%}"
          f"{overall['cut_out_rate']:>15.1%}{overall['winner_cut_rate']:>15.1%}"
          f"{overall['placer_cut_rate']:>16.1%}")
    print("\n除外馬の着外率は高いほど良い / 1着・3着内の取りこぼしは低いほど良い")
    if args.source == "db":
        print("※ DB の v13 は全期間学習モデルの遡及適用＝in-sample。汎化性能は --source honest で見ること")

    out = args.json_out or str(
        MODELS_DIR / f"chihou_cutoff_venue_{args.source}_{args.start}_{args.end}.json")
    Path(out).write_text(json.dumps(
        {"start": args.start, "end": args.end, "source": args.source,
         "rule": {"gap_hard": CUT_GAP_HARD, "gap_soft": CUT_GAP_SOFT,
                  "rank_min": CUT_RANK_MIN},
         "overall": overall, "by_venue": rows}, ensure_ascii=False, indent=2))
    logger.info(f"保存: {out}")


if __name__ == "__main__":
    main()
