"""調教（坂路）を「期待水準との交互作用」として組み込む walk-forward A/B 検証。

## 仮説（2026-08-16 ユーザー提示）

一般的な予想は成績を中心に見る。調教はその上で **2 方向の修正**に使われる:

  - **arm A 下振れ検知**: 強いと見られている馬が「力通り走れるか」
  - **arm B 上振れ検知**: 弱い（成績が悪い・調子が悪い）と見られている馬が
    「調教により上り調子で今回走れそうか」

つまり調教は**絶対水準ではなく、その馬への期待水準との交互作用**で効く。

## 事前の実測（モデル不使用・2,713R / 33,045頭 / 2025-11〜2026-08）

自己比 = 本追いのトレセン z − 自己ベースライン（35〜180日前）中央値。負 = 上り調子。

| 母集団 | 自己比 上昇 | 自己比 悪化 | 差 |
|---|---|---|---|
| 1番人気（arm A） | 68.20% | **61.97%** | **−6.2pt** |
| 1-4番人気（arm A） | 51.14%(やや上昇) | 46.85% | −4.3pt |
| 5番人気以下（arm B） | 11.67%(やや上昇) | 10.17% | +1.2pt |

**arm A の方が強い。** そして自己比と人気順位のレース内 Spearman は **+0.042**
＝市場は自己比の調教変化をほぼ織り込んでいない（絶対水準の +0.103 よりさらに直交）。

## 何を測るか

腕（feature arm）を 3 つ比べる:

  - `base` : 現行 34 特徴
  - `ck`   : + 坂路 8 特徴（`train_v26_chokyo` と同一実装を import）
  - `ck_x` : + 期待水準 `expect_level` と、仮説を明示した交互作用 2 本
             `ck_upside`（弱い馬の上昇）/ `ck_downside`（強い馬の悪化）

主指標は [[jra_darkhorse_discrimination_limit_2026_08_16]] と同じ
**人気を完全に揃えた層化リフト**。超えるべき水準は 5-8番人気 +1.95pt / 9-12番人気 +0.81pt。

⚠️ **評価窓は 4 四半期しかない。** `slope_training` は 2025-05-27 開始で、
それ以前のレースは調教特徴が全欠損になる（LightGBM は NaN をそのまま扱えるので
**学習は全期間を使い**、評価だけ 2025Q4 以降に限る）。n が小さいので
点推定ではなく必ず CI と四半期分解で判断すること。

使い方:
    cd backend
    .venv/bin/python scripts/jra_chokyo_walkforward.py
    .venv/bin/python scripts/jra_chokyo_walkforward.py --cache /tmp/ck_ev.pkl
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
    boot_mean,
    fit_vintage,
    load_all,
    normalized_rank,
    prepare,
    quarters,
    strat_diff,
)
from scripts.jra_darkhorse_walkforward import POP_BANDS, band_rank, show  # noqa: E402

# 坂路特徴の生成は既存実装をそのまま使う（z の取り方を二重管理しない）
from scripts.train_v26_chokyo import (  # noqa: E402
    CHOKYO_FEATURES_FULL,
    attach_chokyo,
    load_slope,
)
from src.indices.composite import V27_OUT_WEIGHT  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chokyo_wf")

# 仮説を明示した特徴。`expect_level` は 0=強い(期待高) 〜 1=弱い(期待低)。
INTERACTION_FEATURES = ["expect_level", "ck_upside", "ck_downside"]

ARMS: dict[str, list[str]] = {
    "base": list(FEATURES),
    "ck": list(FEATURES) + list(CHOKYO_FEATURES_FULL),
    "ck_x": list(FEATURES) + list(CHOKYO_FEATURES_FULL) + INTERACTION_FEATURES,
}

# 調教データの開始（`keiba.slope_training` の最小 training_date）。
# これ以前のレースは調教特徴が全欠損なので評価対象にしない。
CHOKYO_DATA_START = "20250527"


def _dsn() -> str:
    return (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )


def add_expect_level(df: pd.DataFrame) -> pd.DataFrame:
    """odds-free の「期待水準」を直近3走のレース内正規化着順の平均で作る。

    0 = 常に上位（強いと思われている）… 1 = 常に下位（弱いと思われている）。
    **その馬自身の過去走のみ**を使い、当該レースは必ず除く（`shift(1)`）ので
    point-in-time。走歴が無い馬（新馬）は NaN のままにする——中央値で埋めると
    「平均的な期待の馬」に化けてしまい、仮説の交互作用が薄まるため。

    ⚠️ 人気（オッズ）を使わないのは、composite が odds-free だから。
    「弱いと見られている」の市場側の定義は評価の層化（人気帯）が担う。
    """
    df = df.copy()
    fin = df["finish_position"]
    hc = df["head_count"].astype(float)
    nrank = (fin - 1.0) / (hc - 1.0).clip(lower=1.0)
    df["_nrank"] = nrank.where(fin.notna() & (fin > 0))

    df = df.sort_values(["horse_id", "date"]).reset_index(drop=True)
    g = df.groupby("horse_id")["_nrank"]
    df["expect_level"] = g.shift(1).rolling(3, min_periods=1).mean().reset_index(level=0, drop=True)
    return df.drop(columns=["_nrank"])


def add_interactions(df: pd.DataFrame) -> pd.DataFrame:
    """仮説（上振れ／下振れ）を明示した交互作用 2 本を作る。

    `chokyo_self_4f_dev` は「本追い 4F − 自己ベースライン中央値」で、
    **負 = 自分の平常より速い = 上り調子**（`train_v26_chokyo` の定義）。

    - `ck_upside`   = 上昇度 × 期待の低さ  → 弱いと思われている馬の上り調子
    - `ck_downside` = 悪化度 × 期待の高さ  → 強いと思われている馬の下降

    調教または走歴が欠けている馬は NaN（0 で埋めない。「効果ゼロ」と
    「情報が無い」を LightGBM に区別させるため）。
    """
    df = df.copy()
    dev = pd.to_numeric(df["chokyo_self_4f_dev"], errors="coerce")
    exp = pd.to_numeric(df["expect_level"], errors="coerce")
    up = (-dev).clip(lower=0.0)   # 自己比で速くなった分
    down = dev.clip(lower=0.0)    # 自己比で遅くなった分
    df["ck_upside"] = up * exp
    df["ck_downside"] = down * (1.0 - exp)
    return df


def build_dataset(args: argparse.Namespace) -> pd.DataFrame:
    """34特徴 + 坂路8特徴 + 交互作用3特徴 を持つ 1 枚のテーブルを作る。"""
    df = prepare(load_all(args.data_start, args.eval_end))
    df["win_popularity"] = pd.to_numeric(df["win_popularity"], errors="coerce")
    df = add_expect_level(df)

    conn = psycopg2.connect(_dsn())
    cstats, works = load_slope(conn, end=args.eval_end)
    conn.close()

    # attach_chokyo は race_date / horse_id 列を見る
    df["race_date"] = df["date"]
    df = attach_chokyo(df, works, cstats)
    df = df.drop(columns=["race_date"])
    df = add_interactions(df)

    cov = df[df["date"] >= CHOKYO_DATA_START]["chokyo_4f_z"].notna().mean()
    logger.info(
        f"読込: {len(df):,}行 / {df['race_id'].nunique():,}レース。"
        f"調教充足率（{CHOKYO_DATA_START}以降）= {cov * 100:.1f}%"
    )
    return df


def run_arm(df: pd.DataFrame, feats: list[str], args: argparse.Namespace,
            eval_start: str) -> pd.DataFrame:
    """1 つの feature arm について四半期 vintage を回し、予測を連結する。"""
    fin = df[df["is_finisher"]].copy()
    fin["y_rank"] = normalized_rank(fin)
    fin["y_out"] = (fin["finish_position"] >= 6).astype(int)

    out = []
    for label, qstart, qend in quarters(eval_start, args.eval_end):
        train = fin[fin["date"] < qstart]
        if train.empty:
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
    if not out:
        raise SystemExit("評価対象の四半期がありません")
    ev = pd.concat(out, ignore_index=True)
    add_rank(ev, "wf_score", "wf_rank", ascending=False)
    return ev


def paired_selection_diff(ev_a: pd.DataFrame, ev_b: pd.DataFrame, lo: int, hi: int,
                          n_boot: int = 4000, seed: int = 0) -> tuple[float, float, float, float]:
    """腕 a と腕 b の「帯内 指数1位馬」の 3着内率差を**対応のある**形で検定する。

    🔴 腕ごとの CI を並べて重なりを見るのは誤り。2 腕は**同じレース**を予測しており
    選出馬もほとんど同じなので、差の分散は周辺分布から想像するより遥かに小さい。
    レース単位のクラスタブートストラップで**差そのもの**を再標本化する。

    Returns: (差pt, CI下限, CI上限, 選出が一致したレースの割合%)
    """
    def picked(ev: pd.DataFrame) -> pd.Series:
        fin = ev[ev["is_finisher"] & (ev["place_slots"] > 0)]
        m = fin["win_popularity"].between(lo, hi)
        br = band_rank(fin, m, "wf_score")
        sel = fin[m & (br == 1)]
        # 同着（同スコア）が出たレースは先頭 1 頭に寄せる
        return sel.groupby("race_id").agg(hit=("place_hit", "first"), horse=("horse_id", "first"))

    a, b = picked(ev_a), picked(ev_b)
    races = a.index.intersection(b.index)
    if len(races) == 0:
        return (np.nan, np.nan, np.nan, np.nan)
    ha = a.loc[races, "hit"].to_numpy(float)
    hb = b.loc[races, "hit"].to_numpy(float)
    agree = float((a.loc[races, "horse"].to_numpy() == b.loc[races, "horse"].to_numpy()).mean() * 100)

    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(races), size=(n_boot, len(races)))
    diffs = (ha[idx].mean(1) - hb[idx].mean(1)) * 100
    return (float((ha.mean() - hb.mean()) * 100),
            float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5)), agree)


def stratified_lift(ev: pd.DataFrame, lo: int, hi: int) -> tuple[float, float, float, int]:
    """人気を完全に揃えた層化リフト（帯内 指数1位 − 帯内その他）の 3着内率差。"""
    fin = ev[ev["is_finisher"] & (ev["place_slots"] > 0)].copy()
    m = fin["win_popularity"].between(lo, hi)
    if m.sum() == 0:
        return (np.nan, np.nan, np.nan, 0)
    br = band_rank(fin, m, "wf_score")
    strata = fin["win_popularity"].astype("Int64").astype(str)
    pt, lo_ci, hi_ci = strat_diff(fin, m & (br == 1), m & (br > 1), strata, "place_hit")
    return (pt * 100, lo_ci * 100, hi_ci * 100, int((m & (br == 1)).sum()))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-start", default="20230506")
    p.add_argument("--eval-start", default="20251001", help="調教データが乗る最初の四半期")
    p.add_argument("--eval-end", default="20260815")
    p.add_argument("--valid-days", type=int, default=90)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default=None)
    p.add_argument("--cache", default=None, help="特徴付きデータセットの pickle キャッシュ")
    p.add_argument("--pred-cache", default=None,
                   help="腕ごとの walk-forward 予測の pickle キャッシュ。集計だけ変えるときに再学習を避ける")
    args = p.parse_args()

    cache = Path(args.cache) if args.cache else None
    if cache and cache.exists():
        df = pd.read_pickle(cache)
        logger.info(f"キャッシュから読込: {cache}")
    else:
        df = build_dataset(args)
        if cache:
            df.to_pickle(cache)
            logger.info(f"保存: {cache}")

    pred_cache = Path(args.pred_cache) if args.pred_cache else None
    if pred_cache and pred_cache.exists():
        evs: dict[str, pd.DataFrame] = pd.read_pickle(pred_cache)
        logger.info(f"腕ごとの予測をキャッシュから読込: {pred_cache}（再学習なし）")
    else:
        evs = {}
        for arm, feats in ARMS.items():
            logger.info(f"--- arm={arm} ({len(feats)}特徴) ---")
            evs[arm] = run_arm(df, feats, args, args.eval_start)
        if pred_cache:
            pd.to_pickle(evs, pred_cache)
            logger.info(f"保存: {pred_cache}")

    ref = evs["base"]
    print(
        f"\n評価対象: {ref['race_id'].nunique():,}レース / {len(ref):,}頭 "
        f"({ref['date'].min()}〜{ref['date'].max()}) / "
        f"四半期: {', '.join(sorted(ref['quarter'].unique()))}"
    )

    # --- 主指標: 人気を揃えた層化リフト -------------------------------------------
    print("\n" + "=" * 100)
    print("  【主指標】人気を完全に揃えた層化リフト（帯内 指数1位 − 帯内その他・3着内率pt）")
    print("  ※ 全期間ベースライン: 5-8番人気 +1.95pt / 9-12番人気 +0.81pt")
    print("=" * 100)
    lift: dict[str, dict] = {}
    for name, lo, hi in POP_BANDS:
        print(f"\n  [{name}]")
        for arm in ARMS:
            pt, l, h, n = stratified_lift(evs[arm], lo, hi)
            print(f"    {arm:6s} {pt:+6.2f}pt [{l:+6.2f}, {h:+6.2f}]  (選出 n={n:,})")
            lift.setdefault(name, {})[arm] = [round(pt, 2), round(l, 2), round(h, 2), n]

    # --- 腕どうしの差（対応のある検定・これが採否の根拠になる） ----------------------
    print("\n" + "=" * 100)
    print("  【腕の比較】帯内 指数1位馬の3着内率差（同一レースでの対応比較・レース単位CI）")
    print("=" * 100)
    pair: dict[str, dict] = {}
    for name, lo, hi in POP_BANDS:
        print(f"\n  [{name}]")
        for arm in ("ck", "ck_x"):
            pt, l, h, agree = paired_selection_diff(evs[arm], evs["base"], lo, hi)
            sig = "有意" if (l > 0 or h < 0) else "  — "
            print(f"    {arm:5s} − base: {pt:+6.2f}pt [{l:+6.2f}, {h:+6.2f}] {sig}"
                  f"   選出一致 {agree:.1f}%")
            pair.setdefault(name, {})[arm] = [round(pt, 2), round(l, 2), round(h, 2), round(agree, 1)]
        pt, l, h, agree = paired_selection_diff(evs["ck_x"], evs["ck"], lo, hi)
        print(f"    ck_x  − ck  : {pt:+6.2f}pt [{l:+6.2f}, {h:+6.2f}]"
              f"        選出一致 {agree:.1f}%")

    # --- arm A / arm B を仮説どおりの形で直接見る ----------------------------------
    # 「調教が自己比で動いた馬を、指数が帯内で上げ下げできているか」を見る。
    # 指数が調教を使えていれば、悪化馬は帯内下位へ、上昇馬は帯内上位へ寄るはず。
    def diagnose(arm: str, lo: int, hi: int, worsening: bool, title: str) -> None:
        ev = evs[arm]
        m_band = ev["win_popularity"].between(lo, hi) & ev["is_finisher"]
        dev = pd.to_numeric(ev["chokyo_self_4f_dev"], errors="coerce")
        # ⚠️ `dev > 0` を「悪化」としてはいけない。dev は「直近35日の**最速**追い −
        # ベースライン**中央値**」なので構造的に負へ偏り、正になるのは全体の 2% 程度しかない。
        # 帯内の四分位で切る（事前の記述統計と同じ切り方）。
        q = dev[m_band].quantile([0.25, 0.75])
        m_dev = (dev >= q.iloc[1]) if worsening else (dev <= q.iloc[0])
        br = band_rank(ev, m_band, "wf_score")
        label = "悪化" if worsening else "上昇"
        show([
            ("帯 全体", ev[m_band]),
            (f"　自己比 {label}", ev[m_band & m_dev]),
            (f"　　{label} × 帯内 指数1位", ev[m_band & m_dev & (br == 1)]),
            (f"　　{label} × 帯内 指数下位", ev[m_band & m_dev & (br > 1)]),
        ], title)

    print("\n" + "=" * 100)
    print("  【arm A 下振れ検知】1-4番人気 × 自己比 悪化 を指数が下げられているか")
    print("=" * 100)
    for arm in ARMS:
        diagnose(arm, 1, 4, True, f"arm A / {arm}")

    print("\n" + "=" * 100)
    print("  【arm B 上振れ検知】5番人気以下 × 自己比 上昇 を指数が拾えているか")
    print("=" * 100)
    for arm in ARMS:
        diagnose(arm, 5, 99, False, f"arm B / {arm}")

    # --- 四半期別の安定性 ------------------------------------------------------------
    print("\n" + "=" * 100)
    print("  【安定性】四半期別・5-8番人気 帯内指数1位の3着内率")
    print("=" * 100)
    for arm in ARMS:
        ev = evs[arm]
        m = ev["win_popularity"].between(5, 8)
        br = band_rank(ev, m, "wf_score")
        sub = ev[m & (br == 1) & ev["is_finisher"]]
        s = sub.groupby("quarter")["place_hit"].agg(["size", "mean"])
        print(f"  {arm:6s} " + " / ".join(
            f"{q} {int(r['size'])}頭 {r['mean'] * 100:.1f}%" for q, r in s.iterrows()))

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"arms": {k: len(v) for k, v in ARMS.items()},
             "quarters": sorted(ref["quarter"].unique()),
             "n_races": int(ref["race_id"].nunique()),
             "stratified_lift": lift,
             "paired_vs_base": pair},
            ensure_ascii=False, indent=2, default=str))
        logger.info(f"保存: {args.out}")


if __name__ == "__main__":
    main()
