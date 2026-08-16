"""血統特徴を「自己戦績が薄い馬」に絞って A/B する walk-forward（JRA・血統 step1）。

## なぜこのセグメントか

人気を完全に揃えた層化リフトをキャリア（当該走より前の出走数）で分解すると、
モデルの識別力は **自己戦績がある馬に完全に依存**していた
（[[jra_career_thin_blind_spot_2026_08_16]]・walk-forward 11四半期）:

| キャリア | 全体比 | 1-4番人気 | 5番人気以下 |
|---|---|---|---|
| 初出走 | 10.0% | **−3.35pt** | **−1.24pt** |
| 1-2走 | 19.3% | +0.47 | +0.24 |
| 3-10走 | 50.8% | **+5.46** | **+2.02** |
| 11走+ | 19.8% | +0.98 | **+2.75** |

キャリア0-2走（**全体の 29.3%**）は識別力ゼロ。速度・上がり・ローテ等のサブ指数も
DM も過去成績由来なので、走歴が無い馬では入力そのものが無い。
**血統が効きうるとすればここしかない。**

## この検証の位置づけ（5代インブリードへ進むかの門番）

5代インブリードには取り込みの実装が要る（HN の親コード保存 / `pedigrees` への
繁殖登録番号列 / BLDN 再取り込み）。その前に、**今あるデータ（父・母の名前）**で
作れる血統特徴がこのセグメントで動くかを見る。

ここで増分ゼロなら「血統情報は馬自身の戦績とは別に効くのか」という前提自体が
怪しくなるので、5代化の期待値も下がる。

⚠️ ただし完全な門番ではない。ここで測るのは**祖先の質**であって、
インブリードは**配合の構造**という別の構成概念。ゼロでも 5代を完全には否定しない。

## 特徴（すべて point-in-time・当該レース日より前の結果だけで作る）

`pedigree_index` は既に「父・母父の条件別勝率」を持つので、**それと重ならないもの**を選ぶ:

  - `sire_debut_place` : 父の産駒の**キャリア0-2走時点**での複勝率（仕上がりの早さ）
  - `sire_debut_n`     : その母数（信頼度）
  - `sire_2yo_place`   : 父の産駒の 2歳戦での複勝率
  - `dam_prog_place`   : 母の産駒＝**兄弟姉妹**の複勝率
  - `dam_prog_n`       : その母数
  - `sire_all_place`   : 父の産駒全体の複勝率（対照。pedigree_index と重なる）

⚠️ 集計は**種牡馬名**で行う。同名別馬は混ざる（地方で実際に踏んだ・重複426行）。
`pedigrees` は horse_id で引くので馬→父名の対応は一意だが、父名→実体は保証されない。

使い方:
    cd backend
    .venv/bin/python scripts/jra_pedigree_thin_career_walkforward.py --cache /tmp/rel_ds.pkl
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

from scripts.anagusa_top3_walkforward import (  # noqa: E402
    FEATURES,
    _race_z,
    add_rank,
    fit_vintage,
    load_all,
    normalized_rank,
    prepare,
    quarters,
    strat_diff,
)
from scripts.jra_darkhorse_walkforward import band_rank, show  # noqa: E402
from src.indices.composite import V27_OUT_WEIGHT  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ped_thin_wf")

PED_FEATURES = [
    "sire_debut_place", "sire_debut_n",
    "sire_2yo_place",
    "dam_prog_place", "dam_prog_n",
    "sire_all_place",
]

# 事前確率へ縮小するときの重み（ベイズ縮小）。母数がこの頭数で半分効く。
SHRINK_K = 20.0

CAREER_BANDS = [
    ("初出走", 0, 0),
    ("キャリア1-2走", 1, 2),
    ("キャリア0-2走(まとめ)", 0, 2),
    ("キャリア3走以上", 3, 999),
]


def load_true_career() -> pd.Series:
    """(horse_id, date) → **その走より前の出走数**。全履歴から作る。

    🔴 読み込み窓の中で `cumcount()` してはいけない。データ開始（2023-05）より前に
    走っていた馬まで career=0 になる（左側打ち切り）。実測では評価窓の 2.0% が
    誤判定され、キャリア0-2走の割合が 27.7% → 29.3% に膨らんでいた。
    学習側は窓の先頭ほど歪みが大きいのでもっと悪い。
    `keiba.race_results` は 1954 年まで持っているので全履歴で数える。
    """
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    hist = pd.read_sql(
        "SELECT rr.horse_id, r.date FROM keiba.race_results rr "
        "JOIN keiba.races r ON r.id = rr.race_id "
        "WHERE rr.finish_position IS NOT NULL AND rr.finish_position > 0",
        conn,
    )
    conn.close()
    hist = hist.sort_values(["horse_id", "date"])
    hist["career"] = hist.groupby("horse_id").cumcount()
    s = hist.set_index(["horse_id", "date"])["career"]
    return s[~s.index.duplicated()]


def load_pedigree() -> pd.DataFrame:
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    df = pd.read_sql("SELECT horse_id, sire, dam FROM keiba.pedigrees", conn)
    conn.close()
    return df


def _pit_sums(df: pd.DataFrame, key: str, mask: pd.Series, prefix: str) -> pd.DataFrame:
    """`key`（父名 or 母名）別の「複勝数・出走数」を **point-in-time** で返す。

    同じ日のレース同士がお互いを参照しないよう、**日単位で集計してから
    1 日シフト**して累積する。当該レース当日の結果は必ず除かれる。
    """
    src = df[mask & df[key].notna()]
    daily = src.groupby([key, "date"], sort=True)["place_hit"].agg(["sum", "count"])
    cum = daily.groupby(level=0).cumsum().groupby(level=0).shift(1)
    cum.columns = [f"{prefix}_s", f"{prefix}_n"]
    return cum.reset_index()


def _self_cum(df: pd.DataFrame, mask: pd.Series) -> tuple[pd.Series, pd.Series]:
    """その馬自身の、当該走より前の「複勝数・出走数」。

    母の産駒（＝兄弟姉妹）の成績には**自馬の過去走も含まれてしまう**。
    母数は平均 14 走しかないので混入は無視できず、
    「血統の情報」ではなく「自分の戦績」を測ってしまう。ここで差し引く。
    """
    tmp = pd.DataFrame({
        "h": df["horse_id"].values,
        "s": df["place_hit"].where(mask, 0.0).values,
        "n": mask.astype(float).values,
    }, index=df.index)
    g = tmp.groupby("h")
    own_s = g["s"].cumsum() - tmp["s"]   # 当該走を含めない累積
    own_n = g["n"].cumsum() - tmp["n"]
    return own_s, own_n


def _shrunk(s: pd.Series, n: pd.Series, prior: float) -> pd.Series:
    """ベイズ縮小した率。母数が少ない血統は全体平均へ寄せる。"""
    return (s + prior * SHRINK_K) / (n + SHRINK_K)


def add_pedigree_features(df: pd.DataFrame) -> pd.DataFrame:
    """血統特徴を付ける。行数が変わらないことを必ず確認する。"""
    n_before = len(df)
    ped = load_pedigree()
    dup = ped["horse_id"].duplicated().sum()
    if dup:
        logger.warning(f"pedigrees に horse_id 重複 {dup} 件 → 先頭を採用")
        ped = ped.drop_duplicates("horse_id")
    df = df.merge(ped, on="horse_id", how="left")
    assert len(df) == n_before, f"結合で行数が変わった: {n_before} → {len(df)}"

    df["place_hit"] = pd.to_numeric(df["place_hit"], errors="coerce").fillna(0)
    df = df.sort_values(["horse_id", "date"]).copy()
    career = load_true_career()
    df["career"] = pd.MultiIndex.from_arrays([df["horse_id"], df["date"]]).map(career)
    miss = df["career"].isna()
    if miss.any():
        # 履歴に無い＝未出走（出走取消等）。窓内の順序で代用する
        df.loc[miss, "career"] = df[miss].groupby("horse_id").cumcount()
        logger.info(f"career を履歴から引けなかった行: {int(miss.sum()):,}（窓内順で代用）")
    df["career"] = pd.to_numeric(df["career"], errors="coerce")
    df["age"] = pd.to_numeric(df["horse_age"], errors="coerce")
    prior = float(df["place_hit"].mean())

    fin = df["is_finisher"]
    specs = [
        # (集計キー, 母集団, 接頭辞, 自馬分を差し引くか)
        ("sire", fin & (df["career"] <= 2), "sire_debut", False),
        ("sire", fin & (df["age"] <= 2), "sire_2yo", False),
        ("sire", fin, "sire_all", False),
        ("dam", fin, "dam_prog", True),
    ]
    for key, mask, prefix, drop_self in specs:
        tbl = _pit_sums(df, key, mask, prefix)
        n_before = len(df)
        df = df.merge(tbl, on=[key, "date"], how="left")
        assert len(df) == n_before, f"{prefix} の結合で行数が変わった"
        s = df[f"{prefix}_s"].fillna(0.0)
        n = df[f"{prefix}_n"].fillna(0.0)
        if drop_self:
            own_s, own_n = _self_cum(df, mask)
            s = (s - own_s).clip(lower=0.0)
            n = (n - own_n).clip(lower=0.0)
        df[f"{prefix}_place"] = _shrunk(s, n, prior)
        df[f"{prefix}_n"] = n

    # 父系は母数が数百あるので自馬の混入は無視できる（母系だけ差し引いている）
    return df.drop(columns=[c for c in df.columns if c.endswith("_s")], errors="ignore")


def run_arm(df: pd.DataFrame, feats: list[str], args: argparse.Namespace) -> pd.DataFrame:
    fin = df[df["is_finisher"]].copy()
    fin["y_rank"] = normalized_rank(fin)
    fin["y_out"] = (fin["finish_position"] >= 6).astype(int)
    out = []
    for label, qstart, qend in quarters(args.eval_start, args.eval_end):
        train = fin[fin["date"] < qstart]
        if train.empty:
            continue
        span = (pd.to_datetime(qstart) - pd.to_datetime(train["date"].min())).days
        if span < args.min_train_days:
            continue
        target = df[(df["date"] >= qstart) & (df["date"] <= qend)]
        if target.empty:
            continue
        reg_m, out_m = fit_vintage(train, args.seed, args.valid_days, features=feats)
        res = target.copy()
        res["_reg"] = reg_m.predict(target[feats].values)
        res["_out"] = np.clip(out_m.predict(target[feats].values), 0.0, 1.0)
        res["wf_score"] = _race_z(res, "_reg") * -1.0 - V27_OUT_WEIGHT * _race_z(res, "_out")
        res["quarter"] = label
        out.append(res)
    ev = pd.concat(out, ignore_index=True)
    add_rank(ev, "wf_score", "wf_rank", ascending=False)
    return ev


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-start", default="20230506")
    p.add_argument("--eval-start", default="20240101")
    p.add_argument("--eval-end", default="20260815")
    p.add_argument("--min-train-days", type=int, default=200)
    p.add_argument("--valid-days", type=int, default=90)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--cache", default=None)
    p.add_argument("--pred-cache", default=None)
    p.add_argument("--out", default=None)
    args = p.parse_args()

    cache = Path(args.cache) if args.cache else None
    if cache and cache.exists():
        df = pd.read_pickle(cache)
        logger.info(f"キャッシュから読込: {cache}")
    else:
        df = prepare(load_all(args.data_start, args.eval_end))
        df["win_popularity"] = pd.to_numeric(df["win_popularity"], errors="coerce")
        if cache:
            df.to_pickle(cache)
    df = add_pedigree_features(df)
    ev_win = df[df["date"] >= args.eval_start]
    logger.info(
        f"{len(df):,}行 / 父あり {df['sire'].notna().mean() * 100:.1f}% "
        f"母あり {df['dam'].notna().mean() * 100:.1f}% / "
        f"評価窓の sire_debut_place 充足 {ev_win['sire_debut_place'].notna().mean() * 100:.1f}%"
    )

    arms = {"base": list(FEATURES), "ped": list(FEATURES) + PED_FEATURES}
    pred_cache = Path(args.pred_cache) if args.pred_cache else None
    if pred_cache and pred_cache.exists():
        evs = pd.read_pickle(pred_cache)
        logger.info(f"予測をキャッシュから読込: {pred_cache}")
    else:
        evs = {}
        for arm, feats in arms.items():
            logger.info(f"--- arm={arm} ({len(feats)}特徴) ---")
            evs[arm] = run_arm(df, feats, args)
        if pred_cache:
            pd.to_pickle(evs, pred_cache)

    ref = evs["base"]
    print(f"\n評価対象: {ref['race_id'].nunique():,}レース / {len(ref):,}頭 "
          f"({ref['date'].min()}〜{ref['date'].max()})")

    print("\n" + "=" * 100)
    print("  【主指標】キャリア別・人気を揃えた層化リフト（帯内 指数1位 − 帯内その他）")
    print("=" * 100)
    summary: dict[str, dict] = {}
    for arm in arms:
        ev = evs[arm]
        fin = ev[ev["is_finisher"] & (ev["place_slots"] > 0)].copy()
        strata = fin["win_popularity"].astype("Int64").astype(str)
        print(f"\n  --- {arm} ---")
        for nm, lo, hi in CAREER_BANDS:
            for plo, phi, pnm in [(1, 4, "1-4番人気"), (5, 99, "5番人気以下")]:
                m = fin["career"].between(lo, hi) & fin["win_popularity"].between(plo, phi)
                if m.sum() < 500:
                    continue
                br = band_rank(fin, m, "wf_score")
                pt, lo_ci, hi_ci = strat_diff(fin, m & (br == 1), m & (br > 1),
                                              strata, "place_hit")
                print(f"    {nm:20s} × {pnm:10s} n={int(m.sum()):6,}  "
                      f"{pt * 100:+6.2f}pt [{lo_ci * 100:+6.2f}, {hi_ci * 100:+6.2f}]")
                summary.setdefault(arm, {})[f"{nm}|{pnm}"] = [
                    round(pt * 100, 2), round(lo_ci * 100, 2), round(hi_ci * 100, 2)]

    print("\n" + "=" * 100)
    print("  【素の成績】キャリア0-2走に限定した帯内 指数1位")
    print("=" * 100)
    for plo, phi, pnm in [(1, 4, "1-4番人気"), (5, 99, "5番人気以下")]:
        rows = []
        for arm in arms:
            ev = evs[arm]
            m = ev["career"].between(0, 2) & ev["win_popularity"].between(plo, phi)
            br = band_rank(ev, m, "wf_score")
            rows.append((f"{arm} 帯内1位", ev[m & (br == 1)]))
        show(rows, f"キャリア0-2走 × {pnm}")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"n_races": int(ref["race_id"].nunique()), "stratified_lift": summary},
            ensure_ascii=False, indent=2, default=str))
        logger.info(f"保存: {args.out}")


if __name__ == "__main__":
    main()
