"""穴ぐさ × 総合指数上位 の walk-forward honest 検証（JRA）。

## なぜ必要か

DB の `keiba.calculated_indices` version=27 は**全期間 refit した本番モデルを
過去へ遡及適用したもの**（`scripts/inference_v27.py` の警告参照）。
2026-08 に作ったモデルが 2024 年のレースを採点しているため、
その composite_index で組んだ順位を使って過去の的中率・ROI を測ると
**model-vintage look-ahead** が入る。地方で同型の失敗をしている
（memory: chihou_survivor_bias_audit_2026_07_23）。

本スクリプトは四半期ごとに「その四半期の開始日より前のデータだけ」で
2ヘッド（順位回帰 / 着外率）を学習し直し、その vintage のモデルで
当該四半期を予測する。**評価対象のレースは一度も学習に入らない。**

## 何を測るか

  1. 穴ぐさ印 × 指数3位以内 の 3着内率 / 複勝ROI / 単勝ROI
  2. 対照群（穴ぐさ × 指数4位以下 / 印なし × 指数3位以内）との差
  3. 同一母集団での in-sample（DB v27）との乖離

順位は本番と同じ z 合成 `z(-reg_rank) - V27_OUT_WEIGHT * z(out_prob)` で決める。
`blend_v27` のスケーリングはレース内単調変換なので順位には影響しない。

使い方:
    cd backend
    .venv/bin/python scripts/anagusa_top3_walkforward.py
    .venv/bin/python scripts/anagusa_top3_walkforward.py --min-train-days 400
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

from dotenv import load_dotenv

# worktree には .env が無いことがあるので本体リポジトリへフォールバックする
for _cand in (_root.parent / ".env", Path.home() / "GitHub" / "kiseki" / ".env"):
    if _cand.exists():
        load_dotenv(_cand)
        break

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

from scripts.train_jra_out_rate import featurize  # noqa: E402
from src.indices.composite import (  # noqa: E402
    OUT_PROB_FEATURE_NAMES,
    SUBINDEX_SOURCE_SQL,
    V27_OUT_WEIGHT,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("anagusa_wf")

FEATURES = OUT_PROB_FEATURE_NAMES

# sekito 独自の場コード → JRA 場コード
COURSE_MAP_SQL = """
  CASE a.course_code
    WHEN 'JSPK' THEN '01' WHEN 'JHKD' THEN '02' WHEN 'JFKS' THEN '03'
    WHEN 'JNGT' THEN '04' WHEN 'JTOK' THEN '05' WHEN 'JNKY' THEN '06'
    WHEN 'JCKO' THEN '07' WHEN 'JKYO' THEN '08' WHEN 'JHSN' THEN '09'
    WHEN 'JKKR' THEN '10'
  END
"""

FETCH_SQL = f"""
WITH ci AS ({SUBINDEX_SOURCE_SQL})
SELECT
    r.date, ci.race_id, ci.horse_id,
    ci.speed_index, ci.last_3f_index, ci.course_aptitude, ci.position_advantage,
    ci.rotation_index, ci.jockey_index, ci.pace_index, ci.pedigree_index,
    ci.training_index, ci.anagusa_index, ci.paddock_index, ci.rebound_index,
    ci.rivals_growth_index, ci.career_phase_index, ci.distance_change_index,
    ci.jockey_trainer_combo_index, ci.going_pedigree_index,
    r.distance, r.head_count, r.surface, r.condition, r.grade,
    re.frame_number, re.horse_number, re.horse_age, re.weight_carried, re.horse_weight,
    re.jvan_time_dm, re.jvan_battle_dm,
    rr.weight_change, rr.abnormality_code, rr.finish_position,
    rr.win_odds, rr.place_odds, rr.win_popularity,
    v27.composite_index AS db_composite,
    a.rank AS anagusa_rank
FROM ci
JOIN keiba.races r         ON r.id = ci.race_id
JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
LEFT JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
LEFT JOIN keiba.calculated_indices v27
       ON v27.race_id = ci.race_id AND v27.horse_id = ci.horse_id AND v27.version = 27
LEFT JOIN sekito.anagusa a ON a.date = r.date::date
                          AND {COURSE_MAP_SQL} = r.course
                          AND a.race_no  = r.race_number
                          AND a.horse_no = re.horse_number
WHERE r.date >= %(start)s AND r.date <= %(end)s
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
"""


def _params(seed: int, objective: str) -> dict:
    """本番（train_jra_reg_rank / train_jra_out_rate）と同一のハイパーパラメータ。"""
    metric = "l2" if objective == "regression" else "binary_logloss"
    return dict(
        objective=objective, metric=metric,
        learning_rate=0.05, num_leaves=63, min_data_in_leaf=100,
        feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
        lambda_l2=1.0, verbose=-1, seed=seed, deterministic=True,
        num_threads=os.cpu_count() or 4,
    )


def load_all(start: str, end: str) -> pd.DataFrame:
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(FETCH_SQL, {"start": start, "end": end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    conn.close()
    return df


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = featurize(df)
    for c in ["win_odds", "place_odds", "win_popularity", "db_composite", "head_count"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    ab = df["abnormality_code"].fillna(0)
    # 取消・除外は出走していないので順位の母集団から外す（本番 dm_signals と同じ扱い）
    df = df[~ab.isin([1, 2])].copy()
    df = df.sort_values(["race_id", "horse_id"]).reset_index(drop=True)

    fp = df["finish_position"]
    hc = df["head_count"]
    df["is_finisher"] = fp.notna() & (fp > 0)
    # 複勝の払戻対象着順: 8頭以上=3着 / 5〜7頭=2着 / 4頭以下は複勝発売なし
    df["place_slots"] = np.where(hc >= 8, 3, np.where(hc >= 5, 2, 0))
    df["place_hit"] = ((df["is_finisher"]) & (fp <= df["place_slots"])).astype(int)
    df["win_hit"] = ((df["is_finisher"]) & (fp == 1)).astype(int)
    df["is_ana"] = df["anagusa_rank"].notna()
    payout_ok = df.groupby("race_id")["place_odds"].apply(lambda s: s.notna().any())
    df["payout_ok"] = df["race_id"].map(payout_ok)
    return df


def normalized_rank(df: pd.DataFrame) -> np.ndarray:
    r = df.groupby("race_id")["finish_position"].rank(method="min")
    n = df.groupby("race_id")["finish_position"].transform("size")
    return ((r - 1) / (n - 1).clip(lower=1)).values


def _race_z(df: pd.DataFrame, col: str) -> pd.Series:
    """レース内 z スコア。sd=0（全馬同値）のレースは 0 に潰す。"""
    g = df.groupby("race_id")[col]
    sd = g.transform("std")
    return ((df[col] - g.transform("mean")) / sd).where(sd > 1e-12, 0.0)


def quarters(start: str, end: str) -> list[tuple[str, str, str]]:
    """(ラベル, 開始日, 終了日) の四半期リスト。"""
    out = []
    y, q = int(start[:4]), (int(start[4:6]) - 1) // 3
    while True:
        qs = pd.Timestamp(year=y, month=q * 3 + 1, day=1)
        qe = qs + pd.offsets.QuarterEnd(0)
        s, e = qs.strftime("%Y%m%d"), qe.strftime("%Y%m%d")
        if s > end:
            break
        out.append((f"{y}Q{q+1}", max(s, start), min(e, end)))
        q += 1
        if q == 4:
            q, y = 0, y + 1
    return out


def fit_vintage(train: pd.DataFrame, seed: int, valid_days: int,
                features: list[str] | None = None) -> tuple[lgb.Booster, lgb.Booster]:
    """cutoff より前のデータだけで 2ヘッドを学習する。

    本番と同じく「末尾を valid にして early stopping → 全 train で固定ラウンド refit」。
    seed 平均は取らない（四半期 × 2ヘッドで学習回数が増えすぎるため）。

    `features` を渡すと特徴セットを差し替えられる（特徴量 A/B 用。
    `jra_chokyo_walkforward` が使う）。既定は本番と同じ 34 列。
    """
    feats = FEATURES if features is None else features
    cut = (pd.to_datetime(train["date"].max()) - pd.Timedelta(days=valid_days)).strftime("%Y%m%d")
    tr, va = train[train["date"] <= cut], train[train["date"] > cut]
    if len(va) < 2000:  # valid が薄すぎるときは 8:2 で切る
        idx = int(len(train) * 0.8)
        tr, va = train.iloc[:idx], train.iloc[idx:]

    models = []
    for objective, ycol in (("regression", "y_rank"), ("binary", "y_out")):
        d = lgb.Dataset(tr[feats].values, label=tr[ycol].values, feature_name=feats)
        dv = lgb.Dataset(va[feats].values, label=va[ycol].values, reference=d)
        m = lgb.train(_params(seed, objective), d, num_boost_round=2000, valid_sets=[dv],
                      callbacks=[lgb.early_stopping(100, verbose=False)])
        rounds = max(int(m.best_iteration), 50)
        dall = lgb.Dataset(train[feats].values, label=train[ycol].values,
                           feature_name=feats)
        models.append(lgb.train(_params(seed, objective), dall, num_boost_round=rounds))
    return models[0], models[1]


def add_rank(df: pd.DataFrame, score_col: str, rank_col: str, ascending: bool) -> None:
    """レース内順位（1 が最上位）を付ける。"""
    df[rank_col] = df.groupby("race_id")[score_col].rank(method="min", ascending=ascending)


def stat(sub: pd.DataFrame) -> dict:
    fin = sub[sub["is_finisher"]]
    n = len(fin)
    if n == 0:
        return {"n": 0}
    pl = fin[fin["place_slots"] > 0]
    po = pl[pl["payout_ok"]]
    ret = po["place_odds"].fillna(0.0).where(po["place_hit"] == 1, 0.0).sum()
    wo = fin[fin["win_odds"].notna()]
    win_ret = wo["win_odds"].where(wo["win_hit"] == 1, 0.0).sum()
    return {
        "n": n,
        "3着内率": round(pl["place_hit"].mean() * 100, 2) if len(pl) else np.nan,
        "複勝ROI": round(ret / len(po), 3) if len(po) else np.nan,
        "勝率": round(fin["win_hit"].mean() * 100, 2),
        "単勝ROI": round(win_ret / len(wo), 3) if len(wo) else np.nan,
        "平均人気": round(fin["win_popularity"].mean(), 2),
    }


def show(rows: list[tuple[str, pd.DataFrame]], title: str) -> pd.DataFrame:
    recs = []
    for label, sub in rows:
        d = stat(sub)
        d["label"] = label
        recs.append(d)
    out = pd.DataFrame(recs)
    cols = ["label", "n", "3着内率", "複勝ROI", "勝率", "単勝ROI", "平均人気"]
    out = out[[c for c in cols if c in out.columns]]
    print("\n" + "=" * 92)
    print(f"  {title}")
    print("=" * 92)
    print(out.to_string(index=False))
    return out


def _race_agg(sub: pd.DataFrame, col: str, races: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    g = sub.groupby("race_id")[col].agg(["sum", "count"]).reindex(races).fillna(0.0)
    return g["sum"].to_numpy(float), g["count"].to_numpy(float)


def boot_mean(sub: pd.DataFrame, col: str, n_boot: int = 4000,
              seed: int = 0) -> tuple[float, float, float]:
    """レース単位クラスタブートストラップで平均の95%CI。"""
    if sub.empty:
        return (np.nan, np.nan, np.nan)
    rng = np.random.default_rng(seed)
    races = sub["race_id"].unique()
    s, c = _race_agg(sub, col, races)
    idx = rng.integers(0, len(races), size=(n_boot, len(races)))
    vals = s[idx].sum(1) / np.maximum(c[idx].sum(1), 1)
    return (float(sub[col].mean()),
            float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))


def strat_diff(df: pd.DataFrame, a_mask: pd.Series, b_mask: pd.Series, strata: pd.Series,
               value: str, n_boot: int = 4000, seed: int = 0) -> tuple[float, float, float]:
    """層(=単勝オッズ帯)をそろえた a-b の加重平均差。レース単位ブートストラップ。"""
    sub = df[(a_mask | b_mask) & strata.notna()].copy()
    if sub.empty:
        return (np.nan, np.nan, np.nan)
    rng = np.random.default_rng(seed)
    sub["_a"] = a_mask.reindex(sub.index).fillna(False).astype(int)
    sub["_s"] = strata.reindex(sub.index).astype(str)
    races = sub["race_id"].unique()
    ri = sub["race_id"].map({r: i for i, r in enumerate(races)}).to_numpy()
    packs = {}
    for s in sorted(sub["_s"].unique()):
        for g in (0, 1):
            m = ((sub["_s"] == s) & (sub["_a"] == g)).to_numpy()
            sm, ct = np.zeros(len(races)), np.zeros(len(races))
            if m.any():
                np.add.at(sm, ri[m], sub.loc[m, value].to_numpy(float))
                np.add.at(ct, ri[m], 1.0)
            packs[(s, g)] = (sm, ct)
    keys = sorted({k[0] for k in packs})

    def compute(idx: np.ndarray) -> float:
        num = den = 0.0
        for s in keys:
            sa, na = packs[(s, 1)]
            sb, nb = packs[(s, 0)]
            na_s, nb_s = na[idx].sum(), nb[idx].sum()
            if na_s < 5 or nb_s < 5:
                continue
            num += (sa[idx].sum() / na_s - sb[idx].sum() / nb_s) * na_s
            den += na_s
        return num / den if den else np.nan

    point = compute(np.arange(len(races)))
    boots = np.array([compute(rng.integers(0, len(races), len(races))) for _ in range(n_boot)])
    boots = boots[~np.isnan(boots)]
    return point, float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-start", default="20230506", help="学習に使えるデータの最初")
    p.add_argument("--eval-start", default="20240106", help="評価開始（穴ぐさの蓄積開始）")
    p.add_argument("--eval-end", default="20260815")
    p.add_argument("--min-train-days", type=int, default=200,
                   help="この日数未満の学習期間しかない四半期は評価しない")
    p.add_argument("--valid-days", type=int, default=90, help="early stopping 用の末尾期間")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--out", default=None, help="結果 JSON の書き出し先")
    args = p.parse_args()

    df = prepare(load_all(args.data_start, args.eval_end))
    logger.info(f"読込: {len(df):,}行 / {df['race_id'].nunique():,}レース "
                f"({df['date'].min()}〜{df['date'].max()})")

    # 学習ラベル（完走馬のみ。本番の学習と同じ絞り込み）
    fin = df[df["is_finisher"]].copy()
    fin["y_rank"] = normalized_rank(fin)
    fin["y_out"] = (fin["finish_position"] >= 6).astype(int)

    qs = [q for q in quarters(args.eval_start, args.eval_end)]
    results = []
    for label, qstart, qend in qs:
        train = fin[fin["date"] < qstart]
        if train.empty:
            continue
        span = (pd.to_datetime(qstart) - pd.to_datetime(train["date"].min())).days
        if span < args.min_train_days:
            logger.info(f"{label}: 学習期間 {span}日 < {args.min_train_days} のためスキップ")
            continue
        target = df[(df["date"] >= qstart) & (df["date"] <= qend)]
        if target.empty:
            continue
        logger.info(f"{label}: train {len(train):,}行 ({train['date'].min()}〜{train['date'].max()}"
                    f" / {span}日) → predict {len(target):,}行 ({qstart}〜{qend})")
        reg_m, out_m = fit_vintage(train, args.seed, args.valid_days)
        X = target[FEATURES].values
        res = target.copy()
        res["_reg"] = reg_m.predict(X)
        res["_out"] = np.clip(out_m.predict(X), 0.0, 1.0)
        # 本番と同じ z 合成。blend_v27 のスケーリングはレース内単調なので順位は不変。
        # 両ヘッドを同じ ddof で標準化する限り、合成後のレース内順位は ddof に依らない
        res["wf_score"] = (_race_z(res, "_reg") * -1.0
                           - V27_OUT_WEIGHT * _race_z(res, "_out"))
        res["quarter"] = label
        res["train_days"] = span
        results.append(res)

    if not results:
        raise SystemExit("評価対象の四半期がありません")

    ev = pd.concat(results, ignore_index=True)
    add_rank(ev, "wf_score", "wf_rank", ascending=False)
    add_rank(ev, "db_composite", "db_rank", ascending=False)
    ev["ret_place"] = ev["place_odds"].fillna(0.0).where(ev["place_hit"] == 1, 0.0)
    ev["odds_bin"] = pd.cut(ev["win_odds"], [0, 5, 10, 20, 50, 10000],
                            labels=["<5", "5-10", "10-20", "20-50", "50+"])

    k = args.top_k
    ana = ev["is_ana"]
    wf_top = ev["wf_rank"] <= k
    db_top = ev["db_rank"] <= k

    print(f"\n評価対象: {ev['race_id'].nunique():,}レース / {len(ev):,}頭 "
          f"({ev['date'].min()}〜{ev['date'].max()})")
    print(f"四半期: {', '.join(sorted(ev['quarter'].unique()))}")
    print(f"walk-forward と DB(v27) で指数{k}位以内の一致率: "
          f"{(wf_top == db_top).mean()*100:.1f}%")

    show([
        (f"★穴ぐさ × 指数{k}位以内 [WF honest]", ev[ana & wf_top]),
        (f"　同上 [in-sample DB v27]", ev[ana & db_top]),
        (f"穴ぐさ × 指数{k+1}位以下 [WF]", ev[ana & ~wf_top]),
        (f"印なし × 指数{k}位以内 [WF]", ev[~ana & wf_top]),
        ("穴ぐさ全体", ev[ana]),
        ("全馬", ev),
    ], f"【1】walk-forward vs in-sample（同一母集団・上位{k}位）")

    hi = ev["win_odds"] >= 10
    show([
        (f"単勝10倍以上 × 穴ぐさ × 指数{k}位以内 [WF]", ev[hi & ana & wf_top]),
        (f"　同上 [in-sample DB v27]", ev[hi & ana & db_top]),
        (f"単勝10倍以上 × 印なし × 指数{k}位以内 [WF]", ev[hi & ~ana & wf_top]),
        (f"単勝10倍以上 × 穴ぐさ × 指数{k+1}位以下 [WF]", ev[hi & ana & ~wf_top]),
    ], "【2】人気薄帯（既存シグナル「特穴」相当）")

    show([
        (f"穴ぐさ × 指数{j}位以内 [WF]", ev[ana & (ev["wf_rank"] <= j)])
        for j in [1, 2, 3, 4, 5]
    ], "【3】上位何位まで取るか [WF]")

    ev["ret_win"] = ev["win_odds"].fillna(0.0).where(ev["win_hit"] == 1, 0.0)
    fin_ev = ev[ev["is_finisher"] & (ev["place_slots"] > 0)].copy()
    print("\n" + "=" * 92)
    print("  【4】信頼区間（レース単位クラスタブートストラップ・すべて walk-forward）")
    print("=" * 92)
    a = fin_ev[fin_ev["is_ana"] & (fin_ev["wf_rank"] <= k)]
    for col, unit, sc in [("place_hit", "3着内率", 100), ("ret_place", "複勝ROI", 1),
                          ("ret_win", "単勝ROI", 1)]:
        src = a[a["payout_ok"]] if col == "ret_place" else a[a["win_odds"].notna()]
        pt, lo, hi_ = boot_mean(src, col)
        suf = "%" if sc == 100 else ""
        print(f"  穴ぐさ×top{k} {unit}: {pt*sc:.3f}{suf} [{lo*sc:.3f}, {hi_*sc:.3f}]")
    a10 = a[a["win_odds"] >= 10]
    for col, unit in [("ret_place", "複勝ROI"), ("ret_win", "単勝ROI")]:
        src = a10[a10["payout_ok"]] if col == "ret_place" else a10
        pt, lo, hi_ = boot_mean(src, col)
        print(f"  単勝10倍以上 × 穴ぐさ×top{k} {unit}: {pt:.3f} [{lo:.3f}, {hi_:.3f}]")

    print("\n  オッズ帯をそろえた差（穴ぐさ×top{0} − 印なし×top{0}）:".format(k))
    for col, unit, sc in [("place_hit", "3着内率", 100), ("ret_place", "複勝ROI", 1)]:
        base = fin_ev[fin_ev["payout_ok"]] if col == "ret_place" else fin_ev
        pt, lo, hi_ = strat_diff(base, base["is_ana"] & (base["wf_rank"] <= k),
                                 ~base["is_ana"] & (base["wf_rank"] <= k),
                                 base["odds_bin"], col)
        suf = "pt" if sc == 100 else ""
        print(f"    {unit}: {pt*sc:+.3f}{suf} [{lo*sc:+.3f}, {hi_*sc:+.3f}]")

    print("\n  オッズ帯をそろえた差（穴ぐさ×top{0} − 穴ぐさ×{1}位以下）:".format(k, k + 1))
    for col, unit, sc in [("place_hit", "3着内率", 100), ("ret_place", "複勝ROI", 1)]:
        base = fin_ev[fin_ev["payout_ok"]] if col == "ret_place" else fin_ev
        pt, lo, hi_ = strat_diff(base, base["is_ana"] & (base["wf_rank"] <= k),
                                 base["is_ana"] & (base["wf_rank"] > k),
                                 base["odds_bin"], col)
        suf = "pt" if sc == 100 else ""
        print(f"    {unit}: {pt*sc:+.3f}{suf} [{lo*sc:+.3f}, {hi_*sc:+.3f}]")

    show([(f"{q} 穴ぐさ×top{k} [WF]", ev[ana & wf_top & (ev["quarter"] == q)])
          for q in sorted(ev["quarter"].unique())], "【5】四半期別（安定性・すべて honest）")

    # 学習期間が薄い初期四半期に結論が引きずられていないかを確認する
    thick = ev["train_days"] >= 365
    show([
        (f"学習1年以上の四半期のみ / 穴ぐさ × 指数{k}位以内 [WF]", ev[thick & ana & wf_top]),
        (f"　同上 [in-sample DB v27]", ev[thick & ana & db_top]),
        (f"学習1年以上 / 印なし × 指数{k}位以内 [WF]", ev[thick & ~ana & wf_top]),
    ], "【6】学習期間 365日以上の四半期に限定（2024Q3 以降）")

    if args.out:
        payload = {
            "eval_period": [ev["date"].min(), ev["date"].max()],
            "quarters": sorted(ev["quarter"].unique()),
            "top_k": k,
            "n_races": int(ev["race_id"].nunique()),
            "wf_anagusa_top": stat(ev[ana & wf_top]),
            "db_anagusa_top": stat(ev[ana & db_top]),
            "wf_anagusa_rest": stat(ev[ana & ~wf_top]),
            "wf_noana_top": stat(ev[~ana & wf_top]),
        }
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        logger.info(f"保存: {args.out}")


if __name__ == "__main__":
    main()
