"""JRA「軸ロジック」再設計: 指数1位馬のセグメント別 的中率/ROI 異質性分析

keirin S1 の「軸級班denyフィルター」([[keirin_s1_axis_class_deny_filter_2026_07_22]])と
同じ方法論を JRA の指数1位馬（軸候補）に適用する。

keirinでの発見: 軸選手の級班が最上位（S1/A1）だと、的中率は同水準のまま配当が
低くなりやすい（＝市場も同じ判断をしており妙味が薄い）。同じ現象がJRAの
「グレード」「クラス」等のセグメントに存在するかを、train+valで探索し
testで一度きり検証する（多重比較を避ける規律）。

使い方:
  cd backend
  .venv/bin/python scripts/jra_axis_segment_deny_analysis.py
  .venv/bin/python scripts/jra_axis_segment_deny_analysis.py --train-end 20250630 --test-start 20250701
"""
from __future__ import annotations

import argparse
import logging
import os
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
import psycopg2  # noqa: E402

# 既存の妥当性検証スクリプトの annotate/fetch_external をそのまま再利用する
sys.path.insert(0, str(_here.parent))
from jra_verify_signals import annotate, fetch_external  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jra_axis_segment")

V26_VERSION = 26

# fetch_base 相当だが grade / race_condition_code / race_type_code / horse_age を追加取得
BASE_QUERY = """
SELECT
    ci.race_id,
    ci.horse_id,
    r.date,
    r.course,
    r.course_name,
    r.race_number,
    r.surface,
    r.distance,
    r.head_count,
    r.grade,
    r.race_condition_code,
    r.race_type_code,
    re.horse_number,
    re.horse_age,
    re.jvan_time_dm,
    re.jvan_battle_dm,
    ci.composite_index,
    ci.win_probability,
    ci.place_probability,
    rr.finish_position,
    rr.win_odds,
    rr.place_odds,
    rr.win_popularity
FROM keiba.calculated_indices ci
JOIN keiba.races r ON r.id = ci.race_id
JOIN keiba.race_entries re
    ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN keiba.race_results rr
    ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE ci.version = %(ver)s
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
  AND r.head_count >= 5
  AND r.date BETWEEN %(start)s AND %(end)s
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND rr.finish_position IS NOT NULL
  AND rr.win_odds IS NOT NULL AND rr.win_odds > 0
ORDER BY r.date, ci.race_id, re.horse_number
"""


def fetch_base(conn, start: str, end: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(BASE_QUERY, {"ver": V26_VERSION, "start": start, "end": end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    for c in ("composite_index", "win_probability", "place_probability",
              "finish_position", "win_odds", "place_odds", "win_popularity",
              "jvan_time_dm", "jvan_battle_dm", "distance", "head_count",
              "horse_number", "race_number", "horse_age"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    logger.info("base取得: %d行 %dレース (%s〜%s)", len(df), df["race_id"].nunique(), start, end)
    return df


# コード表2007 → 大まかなクラス帯（keirinの player_class 相当）
_CLASS_MAP: dict[str, str] = {
    "701": "新馬/未出走",
    "702": "新馬/未出走",
    "703": "未勝利",
    "005": "1勝クラス",
    "010": "2勝クラス",
    "016": "3勝クラス",
    "999": "オープン(非重賞)",
}


def _class_band(row) -> str:
    if row["grade"] in ("G1", "G2", "G3"):
        return "重賞(G1-G3)"
    code = row["race_condition_code"]
    if pd.notna(code) and str(code) in _CLASS_MAP:
        band = _CLASS_MAP[str(code)]
        if band == "オープン(非重賞)" and row["grade"]:
            return "オープン特別"
        return band
    return "不明"


def _odds_bucket(o: float) -> str:
    if o < 1.5:
        return "<1.5"
    if o < 2.0:
        return "1.5-2.0"
    if o < 3.0:
        return "2.0-3.0"
    if o < 5.0:
        return "3.0-5.0"
    return "5.0+"


def _head_bucket(n: int) -> str:
    if n <= 8:
        return "-8"
    if n <= 12:
        return "9-12"
    if n <= 16:
        return "13-16"
    return "17+"


def _roi_row(sub: pd.DataFrame, rng: np.random.Generator, n_boot: int = 2000) -> dict:
    n = len(sub)
    if n == 0:
        return {"n": 0, "win": 0.0, "plc": 0.0, "roi": 0.0, "lo": 0.0, "hi": 0.0}
    fp = sub["finish_position"].to_numpy()
    odds = sub["win_odds"].to_numpy()
    win = fp == 1
    payout = np.where(win, odds, 0.0)
    roi = payout.sum() / n
    boot = [rng.choice(payout, size=n, replace=True).mean() for _ in range(n_boot)]
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return {"n": n, "win": win.mean() * 100, "plc": (fp <= 3).mean() * 100,
            "roi": roi, "lo": lo, "hi": hi}


def segment_table(df: pd.DataFrame, col: str, title: str, rng: np.random.Generator,
                   order: list[str] | None = None) -> None:
    print(f"\n--- {title} ---")
    print(f"  {'segment':<16}{'n':>7}{'単勝的中':>10}{'複勝的中':>10}{'単ROI':>9}{'CI':>16}")
    keys = order if order is not None else sorted(df[col].dropna().unique())
    for k in keys:
        sub = df[df[col] == k]
        if len(sub) == 0:
            continue
        st = _roi_row(sub, rng)
        mark = "★" if st["lo"] > 1.0 else ("▼" if st["hi"] < 1.0 else " ")
        print(f"  {str(k):<16}{st['n']:>7}{st['win']:>9.1f}%{st['plc']:>9.1f}%"
              f"{st['roi']:>8.3f}{mark}  [{st['lo']:.2f},{st['hi']:.2f}]")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230501")
    p.add_argument("--train-end", default="20250630")
    p.add_argument("--test-start", default="20250701")
    p.add_argument("--end", default="20260725")
    args = p.parse_args()

    rng = np.random.default_rng(12345)
    dsn = (f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
           f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
           f"password={os.getenv('DB_PASSWORD')}")
    conn = psycopg2.connect(dsn)
    df = fetch_base(conn, args.start, args.end)
    ext = fetch_external(conn, args.start, args.end)
    conn.close()

    logger.info("シグナル付与中...")
    df = annotate(df, ext)
    df["date"] = df["date"].astype(str)

    # DM(1403)対戦型指数の1位馬 = 指数1位馬 か（第三者の予想ソースとの一致度）
    def _dm_rank(g: pd.DataFrame) -> pd.Series:
        d = g["jvan_battle_dm"]
        if d.notna().sum() < 2:
            return pd.Series(["DM無"] * len(g), index=g.index)
        r = d.rank(method="first", ascending=False)
        return pd.Series(np.where(r == 1, "DM1位一致", np.where(d.isna(), "DM無", "DM乖離")),
                          index=g.index)
    df["dm_agree"] = df.groupby("race_id", group_keys=False).apply(_dm_rank)

    def _age_bucket(a: float) -> str:
        if pd.isna(a):
            return "不明"
        a = int(a)
        if a <= 3:
            return "2-3歳"
        if a == 4:
            return "4歳"
        return "5歳+"
    df["age_bucket"] = df["horse_age"].apply(_age_bucket)

    def _gap_bucket(g: float) -> str:
        if pd.isna(g):
            return "不明"
        if g < 3:
            return "gap<3"
        if g < 6:
            return "gap3-6"
        if g < 10:
            return "gap6-10"
        return "gap10+"

    # レース単位の gap_1_2（指数1位-2位差）を算出
    gap_map = (
        df[df["composite_rank"].isin([1, 2])]
        .pivot_table(index="race_id", columns="composite_rank", values="composite_index")
    )
    gap_map = (gap_map[1] - gap_map[2]).rename("gap_1_2")

    # 指数1位馬（軸候補）のみに絞る
    top1 = df[df["composite_rank"] == 1].copy()
    top1 = top1.join(gap_map, on="race_id")
    top1["class_band"] = top1.apply(_class_band, axis=1)
    top1["top_odds_bucket"] = top1["win_odds"].apply(_odds_bucket)
    top1["head_bucket"] = top1["head_count"].apply(_head_bucket)
    # 指数1位と市場1番人気の一致/乖離
    top1["market_agree"] = np.where(top1["odds_rank"] == 1, "1位一致", "乖離")
    top1["gap_bucket"] = top1["gap_1_2"].apply(_gap_bucket)

    train = top1[top1["date"] < args.train_end]
    test = top1[top1["date"] >= args.test_start]

    print("\n" + "#" * 92)
    print("# JRA 軸ロジック セグメント異質性分析（指数1位馬 = composite_rank==1）")
    print("# ★=95%CI下限>1(黒字確証) / ▼=95%CI上限<1(赤字確証)")
    print(f"# train+val: {args.start}-{args.train_end} (n={len(train)}) / "
          f"test: {args.test_start}-{args.end} (n={len(test)})")
    print("#" * 92)

    class_order = ["新馬/未出走", "未勝利", "1勝クラス", "2勝クラス", "3勝クラス",
                    "オープン特別", "重賞(G1-G3)", "不明"]
    for label, d in (("train+val", train), ("test(OOS)", test)):
        print(f"\n{'='*30} {label} {'='*30}")
        segment_table(d, "class_band", "① クラス帯別 (keirinの軸級班相当)", rng, class_order)
        segment_table(d, "surface", "② 芝/ダート別", rng)
        segment_table(d, "head_bucket", "③ 頭数帯別", rng)
        segment_table(d, "top_odds_bucket", "④ 指数1位馬の単勝オッズ帯別", rng)
        segment_table(d, "market_agree", "⑤ 指数1位 vs 市場1番人気の一致/乖離", rng)
        segment_table(d, "confidence_rank", "⑥ confidence_rank別", rng, ["S", "A", "B", "C"])
        segment_table(d, "course_name", "⑦ 競馬場別", rng)
        segment_table(d, "dm_agree", "⑨ DM(1403)対戦型指数との一致/乖離", rng,
                      ["DM1位一致", "DM乖離", "DM無"])
        segment_table(d, "age_bucket", "⑩ 指数1位馬の馬齢帯別", rng, ["2-3歳", "4歳", "5歳+", "不明"])
        segment_table(d, "gap_bucket", "⑪ 指数1-2位差(gap_1_2)帯別", rng,
                      ["gap<3", "gap3-6", "gap6-10", "gap10+", "不明"])

        print(f"\n--- ⑧ confidence_rank × market_agree クロス集計 "
              "(market_agreeがtier内で追加分離を持つか) ---")
        print(f"  {'tier':<4}{'market':<8}{'n':>7}{'単勝的中':>10}{'複勝的中':>10}{'単ROI':>9}")
        for tier in ["S", "A", "B", "C"]:
            for ma in ["1位一致", "乖離"]:
                sub = d[(d["confidence_rank"] == tier) & (d["market_agree"] == ma)]
                if len(sub) == 0:
                    continue
                st = _roi_row(sub, rng, n_boot=500)
                print(f"  {tier:<4}{ma:<8}{st['n']:>7}{st['win']:>9.1f}%"
                      f"{st['plc']:>9.1f}%{st['roi']:>8.3f}")

        print(f"\n--- ⑫ confidence_rank × dm_agree クロス集計 ---")
        print(f"  {'tier':<4}{'dm':<10}{'n':>7}{'単勝的中':>10}{'複勝的中':>10}{'単ROI':>9}")
        for tier in ["S", "A", "B", "C"]:
            for da in ["DM1位一致", "DM乖離", "DM無"]:
                sub = d[(d["confidence_rank"] == tier) & (d["dm_agree"] == da)]
                if len(sub) == 0:
                    continue
                st = _roi_row(sub, rng, n_boot=500)
                print(f"  {tier:<4}{da:<10}{st['n']:>7}{st['win']:>9.1f}%"
                      f"{st['plc']:>9.1f}%{st['roi']:>8.3f}")


if __name__ == "__main__":
    main()
