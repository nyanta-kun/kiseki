"""キャリア0-2走に専念する残差補正ヘッドの walk-forward A/B（JRA）。

## なぜ

人気を揃えた層化リフトをキャリアで分解すると、モデルの識別力は
**自己戦績がある馬に完全に依存**していた（[[jra_career_thin_blind_spot_2026_08_16]]）:

| キャリア | 全体比 | 1-4番人気 | 5番人気以下 |
|---|---|---|---|
| 初出走 | 10.0% | −3.35pt | −1.24pt |
| 1-2走 | 19.3% | +0.47 | +0.24 |
| 3-10走 | 50.8% | **+5.46** | **+2.02** |

キャリア0-2走（**全体の 29.3%**）は識別力ゼロ。血統特徴を足しても
+0.2〜0.4pt で非有意だった（[[jra_pedigree_thin_career_2026_08_16]]）。

そこで**特徴ではなく学習の当て方**を変える。`reg_rank` は全馬で学習しており、
**7割を占めるキャリア3走以上に損失が支配されている**。血統を足したとき
キャリア3走以上で −0.36pt という副作用が出たのは、モデルがその特徴を
「キャリア馬の文脈」で使ってしまった証拠と読める。

## なぜ「別モデル」ではなく「残差補正」なのか

レース内でキャリアが混在する。実測（9,156R・2024-01〜）:

  混在レース(1〜99%) **55.2%** / ほぼ全馬が薄い(>=75%) 15.7% / ほぼ全馬が濃い(<=25%) 59.9%
  キャリア0-2走の馬のうち、薄いレースに居るのは **46.6%** だけ

つまり薄い馬の過半数は濃い馬と同じレースで競う。**別々のモデルで出したスコアは
同一レースで並べられない**（スケールが違う）。

残差補正なら base の予測に同じ単位の補正を足すだけなのでスケールが保たれる:

    corrected_reg = base_reg + resid_head(x)     ← 薄いキャリアの馬にだけ適用
    score = z(-corrected_reg) - V27_OUT_WEIGHT * z(out_prob)

`resid_head` の目的変数は `y_rank − base_reg`（レース内正規化着順の残差）で、
**キャリア0-2走の行だけ**で学習する。

## 腕

  - `base`     : 現行 34 特徴（対照）
  - `seg`      : base + 残差ヘッド（34特徴）
  - `seg_ped`  : base + 残差ヘッド（34 + 血統6特徴）
    ← セグメントを切れば血統が活きるか、という問いも同時に測る

使い方:
    cd backend
    .venv/bin/python scripts/jra_thin_career_head_walkforward.py --cache /tmp/rel_ds.pkl
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

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts.anagusa_top3_walkforward import (  # noqa: E402
    FEATURES,
    _params,
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
from scripts.jra_pedigree_thin_career_walkforward import (  # noqa: E402
    PED_FEATURES,
    add_pedigree_features,
)
from src.indices.composite import V27_OUT_WEIGHT  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("thin_head_wf")

THIN_MAX_CAREER = 2  # 「自己戦績が薄い」の定義（当該走より前の出走数）

CAREER_BANDS = [
    ("初出走", 0, 0),
    ("キャリア1-2走", 1, 2),
    ("キャリア0-2走(まとめ)", 0, 2),
    ("キャリア3走以上", 3, 999),
]


def fit_residual_head(train: pd.DataFrame, base_reg: np.ndarray, feats: list[str],
                      seed: int, valid_days: int) -> lgb.Booster | None:
    """キャリア0-2走の行だけで「base の残差」を学習する。

    残差 = y_rank − base_reg。base と同じ単位なのでそのまま足せる。
    """
    d = train.copy()
    d["_resid"] = d["y_rank"].to_numpy() - base_reg
    d = d[d["career"] <= THIN_MAX_CAREER]
    if len(d) < 3000:
        return None
    cut = (pd.to_datetime(d["date"].max()) - pd.Timedelta(days=valid_days)).strftime("%Y%m%d")
    tr, va = d[d["date"] <= cut], d[d["date"] > cut]
    if len(va) < 500:
        idx = int(len(d) * 0.8)
        tr, va = d.iloc[:idx], d.iloc[idx:]

    ds = lgb.Dataset(tr[feats].values, label=tr["_resid"].values, feature_name=feats)
    dv = lgb.Dataset(va[feats].values, label=va["_resid"].values, reference=ds)
    m = lgb.train(_params(seed, "regression"), ds, num_boost_round=2000, valid_sets=[dv],
                  callbacks=[lgb.early_stopping(100, verbose=False)])
    rounds = max(int(m.best_iteration), 50)
    dall = lgb.Dataset(d[feats].values, label=d["_resid"].values, feature_name=feats)
    return lgb.train(_params(seed, "regression"), dall, num_boost_round=rounds)


def run(df: pd.DataFrame, args: argparse.Namespace) -> dict[str, pd.DataFrame]:
    """四半期ごとに base を学習し、その残差ヘッドを 2 通り学習して 3 腕を作る。"""
    fin = df[df["is_finisher"]].copy()
    fin["y_rank"] = normalized_rank(fin)
    fin["y_out"] = (fin["finish_position"] >= 6).astype(int)

    outs: dict[str, list[pd.DataFrame]] = {"base": [], "seg": [], "seg_ped": []}
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
        logger.info(f"{label}: train {len(train):,} → predict {len(target):,}")

        reg_m, out_m = fit_vintage(train, args.seed, args.valid_days, features=FEATURES)
        base_reg_train = reg_m.predict(train[FEATURES].values)

        base_reg = reg_m.predict(target[FEATURES].values)
        out_p = np.clip(out_m.predict(target[FEATURES].values), 0.0, 1.0)
        thin = (target["career"] <= THIN_MAX_CAREER).to_numpy()

        for arm, feats in (("seg", list(FEATURES)),
                           ("seg_ped", list(FEATURES) + PED_FEATURES)):
            head = fit_residual_head(train, base_reg_train, feats, args.seed, args.valid_days)
            reg = base_reg.copy()
            if head is not None:
                # 薄いキャリアの馬にだけ補正を足す（濃い馬は base のまま）
                reg[thin] = base_reg[thin] + head.predict(target.loc[thin, feats].values)
            res = target.copy()
            res["_reg"], res["_out"], res["quarter"] = reg, out_p, label
            outs[arm].append(res)

        res = target.copy()
        res["_reg"], res["_out"], res["quarter"] = base_reg, out_p, label
        outs["base"].append(res)

    evs = {}
    for arm, parts in outs.items():
        ev = pd.concat(parts, ignore_index=True)
        ev["wf_score"] = _race_z(ev, "_reg") * -1.0 - V27_OUT_WEIGHT * _race_z(ev, "_out")
        add_rank(ev, "wf_score", "wf_rank", ascending=False)
        evs[arm] = ev
    return evs


def paired(a_ev: pd.DataFrame, b_ev: pd.DataFrame, mask_fn, n_boot: int = 4000,
           seed: int = 0):
    """同一レースでの対応比較（帯内 指数1位馬の3着内率差）。"""
    def picked(ev):
        f = ev[ev["is_finisher"] & (ev["place_slots"] > 0)]
        m = mask_fn(f)
        br = band_rank(f, m, "wf_score")
        sel = f[m & (br == 1)]
        return sel.groupby("race_id").agg(hit=("place_hit", "first"),
                                          horse=("horse_id", "first"))

    a, b = picked(a_ev), picked(b_ev)
    races = a.index.intersection(b.index)
    if len(races) < 50:
        return None
    ha = a.loc[races, "hit"].to_numpy(float)
    hb = b.loc[races, "hit"].to_numpy(float)
    agree = float((a.loc[races, "horse"].to_numpy() == b.loc[races, "horse"].to_numpy()).mean())
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(races), size=(n_boot, len(races)))
    d = (ha[idx].mean(1) - hb[idx].mean(1)) * 100
    return ((ha.mean() - hb.mean()) * 100, float(np.percentile(d, 2.5)),
            float(np.percentile(d, 97.5)), len(races), agree * 100)


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
    df = add_pedigree_features(df)  # career 列もここで付く
    logger.info(f"{len(df):,}行 / キャリア0-2走の割合 "
                f"{df['career'].le(THIN_MAX_CAREER).mean() * 100:.1f}%")

    pred_cache = Path(args.pred_cache) if args.pred_cache else None
    if pred_cache and pred_cache.exists():
        evs = pd.read_pickle(pred_cache)
        logger.info(f"予測をキャッシュから読込: {pred_cache}")
    else:
        evs = run(df, args)
        if pred_cache:
            pd.to_pickle(evs, pred_cache)

    ref = evs["base"]
    print(f"\n評価対象: {ref['race_id'].nunique():,}レース / {len(ref):,}頭 "
          f"({ref['date'].min()}〜{ref['date'].max()})")

    # career を全履歴から引き直したので、ベースラインの層化リフトも出し直す
    print("\n" + "=" * 100)
    print("  【ベースライン再掲】キャリア別・人気を揃えた層化リフト（base のみ・career は全履歴基準）")
    print("=" * 100)
    fin_b = evs["base"][evs["base"]["is_finisher"] & (evs["base"]["place_slots"] > 0)].copy()
    strata = fin_b["win_popularity"].astype("Int64").astype(str)
    for cnm, clo, chi in CAREER_BANDS:
        for pnm, plo, phi in [("1-4番人気", 1, 4), ("5番人気以下", 5, 99)]:
            m = fin_b["career"].between(clo, chi) & fin_b["win_popularity"].between(plo, phi)
            if m.sum() < 500:
                continue
            br = band_rank(fin_b, m, "wf_score")
            pt, lo, hi = strat_diff(fin_b, m & (br == 1), m & (br > 1), strata, "place_hit")
            print(f"  {cnm:20s} × {pnm:10s} n={int(m.sum()):6,}  "
                  f"{pt * 100:+6.2f}pt [{lo * 100:+6.2f}, {hi * 100:+6.2f}]")

    print("\n" + "=" * 100)
    print("  【主指標】base との対応比較（帯内 指数1位馬の3着内率差）")
    print("=" * 100)
    summary: dict[str, dict] = {}
    for cnm, clo, chi in CAREER_BANDS:
        print(f"\n  [{cnm}]")
        for pnm, plo, phi in [("1-4番人気", 1, 4), ("5番人気以下", 5, 99)]:
            def f(ev, clo=clo, chi=chi, plo=plo, phi=phi):
                return (ev["career"].between(clo, chi)
                        & ev["win_popularity"].between(plo, phi))
            for arm in ("seg", "seg_ped"):
                r = paired(evs[arm], evs["base"], f)
                if r is None:
                    continue
                pt, lo, hi, nr, ag = r
                sig = "有意" if (lo > 0 or hi < 0) else "  — "
                print(f"    {arm:8s} × {pnm:10s} {pt:+6.2f}pt [{lo:+6.2f}, {hi:+6.2f}] {sig}"
                      f"  対象{nr:5,}R 選出一致{ag:.1f}%")
                summary.setdefault(cnm, {})[f"{arm}|{pnm}"] = [
                    round(pt, 2), round(lo, 2), round(hi, 2)]

    print("\n" + "=" * 100)
    print("  【素の成績】キャリア0-2走に限定した帯内 指数1位")
    print("=" * 100)
    for pnm, plo, phi in [("1-4番人気", 1, 4), ("5番人気以下", 5, 99)]:
        rows = []
        for arm in ("base", "seg", "seg_ped"):
            ev = evs[arm]
            m = ev["career"].between(0, THIN_MAX_CAREER) & ev["win_popularity"].between(plo, phi)
            br = band_rank(ev, m, "wf_score")
            rows.append((f"{arm} 帯内1位", ev[m & (br == 1)]))
        show(rows, f"キャリア0-2走 × {pnm}")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"n_races": int(ref["race_id"].nunique()), "paired_vs_base": summary},
            ensure_ascii=False, indent=2, default=str))
        logger.info(f"保存: {args.out}")


if __name__ == "__main__":
    main()
