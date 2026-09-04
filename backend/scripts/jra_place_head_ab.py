"""JRA 複勝確率の作り方 4腕 walk-forward A/B（事前登録 §12.3 Phase D-1 の実装）

事前登録: `docs/jra_winplace_structure_plan_2026_09_04.md` §12.3。
**本スクリプトは事前登録の仕様を実装するだけで、判定基準を動かさない。**

## 目的（§12.1）

Phase C（§11.2）の実測は「**単勝ヘッドに特徴を足しても複勝側の構造は取れない**」だった。
`feat` は単勝側の較正を直した（逃げ馬の単勝残差 −62%）が、複勝側固有の excess は
着順分散・勝率複勝率比の層でほぼ動かず（1〜7%減）、交差件数は全腕 0件、
`place_ll` は −0.0013 でほぼ不変。

⇒ 構造そのものを変えるしかない。**特徴量は4腕とも `feat` で固定し、
変えるのは複勝確率の作り方だけ**にして、複勝の対数損失で対応比較する。

## 腕（§12.3・4腕とも `p_win` は同一のもの）

| 腕 | 複勝確率の作り方 |
|---|---|
| `harville`   | `CompositeIndexCalculator._harville_place_probs(p_win)`（本番関数を import・ベースライン） |
| `top3_raw`   | 独立 `is_top3` binary LGB の**生出力**（レース内正規化なし） |
| `top3_norm`  | 同じヘッド → レース内で **Σp = place_slots** に正規化（地方 `chihou_calculator.py:802` と同じ形） |
| `henery_fit` | `p_win^λ` 再正規化 → Harville。**λ は探索窓で当てはめて確認窓へ流用** |

🔴 `henery_fit` の λ に既存の `models/finish_order_lambda.json`（0.4345）は**使わない**。
§訂正1 のとおり、あれは v26 の softmax 確率に対して 2023-01〜2025-06 で当てたもので、
現行 v27 系の `p_win` に当てると NLL が悪化する。本スクリプトは探索窓で当て直す。

⚠️ λ は探索窓に対しては in-sample（スカラー1個・約5,000レース）。よって
**`henery_fit` の探索窓の数字は本質的に上振れする**。確認窓が正味の値。

## 主指標と判定（§12.3・事前に固定・動かさない）

**複勝の対数損失 `place_ll`**（`is_top3` に対する二値対数損失・proper scoring rule）。
`place_slots=3` と `=2` を分けて集計し、**判定は `place_slots=3` で行う**（§9.1 罠3）。
`harville` との対応差をレースクラスタ bootstrap で評価する。

| 判定 | 条件 |
|---|---|
| **採用候補** | 探索6四半期の対応差の 95%CI が 0 を跨がず改善側、かつ改善 4/6 以上。**さらに確認4四半期で同符号** |
| 不採用 | 上記を満たさない |

## 副指標（報告必須・採否には使わない）

- 🔴 **交差件数**（単勝順位 vs 複勝順位）。§9.2 で現行は全窓 0件。`top3_*` 腕で 0 より
  増えることが「Harville で表現できなかった構造を表現できた」直接の証拠。
  `henery_fit` は理論上 0（λ>0 の冪は単調変換なのでレース内順位を保ち、Harville は
  p_win について単調）。0 でなければ実装バグを疑う。
  🔴 数え方は `jra_prob_scoring.place_scores` の実装（`rank()` ではなく生値の差を
  `CROSS_TOL=1e-9` 許容で比較）を **import してそのまま使う**。`rank(method="first")` で
  数えると同値オッズで偽陽性が出る（§9.2 罠3・実測13件）
- `coverage@3` / 複勝側 Spearman
- 🔴 **§9.1 の層別の複勝残差**（`p_win` 10分位で調整済み）。
  **`excess` は Harville 前提の一次近似なので独立ヘッドには使えない**
  （`∂p_place/∂p_win ≒ p_place/p_win` は Harville でしか成り立たない）。
  よって **残差そのものが 0 に近づいたか**で見る。特に着順分散 T1/T3 と
  勝率複勝率比 T1 — ここが縮まなければ構造を変えても仮説は回収できていない（§12.5）

## 窓（§12.2）

- **探索 2024Q1〜2025Q2（6四半期）** / **確認 2025Q3〜2026Q2（4四半期）**
- 🔴 **2026Q3（`TEST_START=20260701` 以降）は絶対に使わない。** ガードを2重に入れてある
  （四半期終端の検査 + DataFrame からの行除去）

## 実装上の共有（🔴 再実装をしない）

- `feat` 特徴の作り方 … `jra_winplace_feature_ab` から `build_dataset` / `ARMS["feat"]` /
  `fit_predict` / `parse_quarters` を import
- 複勝側の指標・交差件数 … `jra_prob_scoring.place_scores` を import
- Harville … `jra_prob_scoring.harville_place`（＝本番 `_harville_place_probs`）を import
- 層別のカット点・水準割当・レースクラスタ bootstrap …
  `jra_place_residual_diag` の `CUTS` / `assign_levels` / `_bootstrap_indices` を import

## 使い方

    cd backend
    .venv/bin/python scripts/jra_place_head_ab.py \
        --explore-quarters 2024Q1..2025Q2 --confirm-quarters 2025Q3..2026Q2 \
        --seeds 42,123,456 --bootstrap 2000 \
        --out ../docs/model_verification/jra_place_head_ab.json

冪等。`--cache` / `--pred-cache` に pickle を指定すると再実行が速い。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# --- 既存の測定基盤／本番コードを import する（独立実装をしない） -----------------
from scripts.jra_place_residual_diag import (  # noqa: E402
    CUTS,
    _bootstrap_indices,
    assign_levels,
)
from scripts.jra_prob_scoring import (  # noqa: E402
    harville_place,
    place_scores,
    race_normalize,
    win_scores,
)
from scripts.jra_winplace_feature_ab import (  # noqa: E402
    ARMS as FEATURE_ARMS,
)
from scripts.jra_winplace_feature_ab import (  # noqa: E402
    TEST_START,
    build_dataset,
    fit_predict,
    parse_quarters,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("place_head_ab")

OUT_PATH = _root.parent / "docs" / "model_verification" / "jra_place_head_ab.json"

# 🔴 特徴量は4腕とも `feat`（Phase C の採用候補）で固定する。変えるのは複勝の作り方だけ。
FEAT_NAMES: list[str] = FEATURE_ARMS["feat"]["names"]
FEAT_COLS: list[str] = FEATURE_ARMS["feat"]["cols"]

ARMS = ("harville", "top3_raw", "top3_norm", "henery_fit")
BASE_ARM = "harville"

EPS = 1e-9          # 確率のクリップ（place_scores の内部と同じ水準）
PLACE_COL = {a: f"p_place__{a}" for a in ARMS}


# ---------------------------------------------------------------------------
# 学習（p_win は4腕で同一。is_top3 ヘッドだけを別に建てる）
# ---------------------------------------------------------------------------

def _relabel_top3(d: pd.DataFrame) -> pd.DataFrame:
    """独立 `is_top3` ヘッドを **is_win ヘッドと同一の学習手順**で学習するための写像。

    `jra_winplace_feature_ab.fit_predict` は label を `finish_position == 1` で作る
    （is_win 用に固定）。同じ関数を書き写さずに再利用するため、`finish_position` を
    次のように写像した**コピー**を渡す:

        finish_position <= 3 → 1（label=1）/ それ以外 → 99（label=0）

    こうすると `fit_predict` の中で `(finish_position == 1)` が `is_top3` になる。
    PARAMS / MAX_ROUND / seeds / early stopping(100) は完全に共有される。

    ⚠️ ラベルは `place_slots` ではなく **一律 3着以内**（事前登録 §12.3 の
    「独立 `is_top3` binary LGB」の字義どおり）。`place_slots=2` のレース
    （5〜7頭立て・全体の 0.5% 未満）は「3着以内ヘッドを2着以内で採点する」
    ことになるので、**判定は `place_slots=3` で行う**（§12.3）。
    """
    fp = pd.to_numeric(d["finish_position"], errors="coerce")
    out = d.copy()
    out["finish_position"] = np.where(fp <= 3, 1, 99)
    return out


def run_walk_forward(df: pd.DataFrame, qs: list[tuple[str, str, str]],
                     seeds: list[int], valid_days: int) -> pd.DataFrame:
    """四半期ごとの walk-forward。評価行に `p_win` と `p_top3_raw` を付けて返す。

    🔴 `p_win` は全腕で同一のもの（`feat` 特徴の is_win ヘッド）。腕の違いは
    **複勝確率の作り方だけ**であること。学習/検証の分割も2ヘッドで完全に共通。
    """
    out = []
    for label, qstart, qend in qs:
        train = df[df["date"] < qstart]
        te = df[(df["date"] >= qstart) & (df["date"] <= qend)].copy()
        if train.empty or te.empty:
            raise SystemExit(f"{label}: train か test が空（train={len(train)} test={len(te)}）")
        cut = (pd.to_datetime(qstart) - pd.Timedelta(days=valid_days)).strftime("%Y%m%d")
        tr, va = train[train["date"] <= cut], train[train["date"] > cut]
        if len(va) < 2000:
            i = int(len(train) * 0.8)
            tr, va = train.iloc[:i], train.iloc[i:]
        te = te.reset_index(drop=True)

        t0 = time.time()
        raw_w, it_w = fit_predict(tr, va, te, FEAT_NAMES, FEAT_COLS, seeds)
        te["p_win"] = race_normalize(raw_w, te["race_id"])

        raw_t3, it_t3 = fit_predict(_relabel_top3(tr), _relabel_top3(va), te,
                                    FEAT_NAMES, FEAT_COLS, seeds)
        te["p_top3_raw"] = np.clip(raw_t3, EPS, 1.0 - EPS)

        te["quarter"] = label
        te["best_iters_win"] = str(it_w)
        te["best_iters_top3"] = str(it_t3)
        out.append(te)
        logger.info("  [%s] tr=%d va=%d te=%d/%dR iters_win=%s iters_top3=%s (%.1fs)",
                    label, len(tr), len(va), len(te), te["race_id"].nunique(),
                    it_w, it_t3, time.time() - t0)
    return pd.concat(out, ignore_index=True)


# ---------------------------------------------------------------------------
# 腕ごとの複勝確率
# ---------------------------------------------------------------------------

def _henery_place(d: pd.DataFrame, lam: float) -> np.ndarray:
    """`p_win^λ` をレース内で再正規化してから本番 Harville に渡す。

    λ=1 で `harville` 腕と完全一致する（実装の自己検査に使える）。
    """
    q = np.power(np.clip(d["p_win"].to_numpy(dtype=float), EPS, 1.0), float(lam))
    s = pd.Series(q, index=d.index)
    tot = s.groupby(d["race_id"]).transform("sum")
    tmp = d.copy()
    tmp["_p_lam"] = (s / tot).to_numpy()
    return harville_place(tmp, "_p_lam")


def _top3_norm(d: pd.DataFrame) -> tuple[np.ndarray, int]:
    """独立ヘッドの生出力をレース内で `Σp = place_slots` に正規化する。

    地方 `chihou_calculator.py` は生出力をそのまま使う（`min(1.0, ...)` のみ）が、
    §12.3 はレース内 Σ を払戻対象着順に合わせる腕を別に置いている。

    ⚠️ 正規化後に 1 を超える馬はクリップする（確率として 1 を超えられない）。
    クリップが効いた頭数を返して報告する（Σ=place_slots はその分だけ崩れる）。
    """
    raw = d["p_top3_raw"].to_numpy(dtype=float)
    s = pd.Series(raw, index=d.index)
    tot = s.groupby(d["race_id"]).transform("sum").to_numpy()
    slots = d["place_slots"].to_numpy(dtype=float)
    scaled = np.where(tot > 0, raw * slots / np.maximum(tot, EPS), raw)
    n_clip = int((scaled > 1.0 - EPS).sum())
    return np.clip(scaled, EPS, 1.0 - EPS), n_clip


def fit_lambda(ev: pd.DataFrame, coarse: np.ndarray, fine_step: float = 0.01) -> dict:
    """探索窓の `place_slots=3` で `place_ll` を最小化する λ を当てる。

    🔴 目的関数は**主指標そのもの**（複勝の二値対数損失）。粗いグリッド →
    最良点の周りを細かいグリッドで詰める（logloss(λ) は単峰）。
    """
    d = ev[ev["place_slots"] == 3].reset_index(drop=True)
    y = (pd.to_numeric(d["finish_position"], errors="coerce") <= 3).to_numpy(dtype=float)

    def _ll(lam: float) -> float:
        p = np.clip(_henery_place(d, lam), EPS, 1.0 - EPS)
        return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())

    curve = [(round(float(l), 4), round(_ll(float(l)), 6)) for l in coarse]
    best = min(curve, key=lambda t: t[1])[0]
    step = float(coarse[1] - coarse[0])
    fine = np.round(np.arange(best - step, best + step + 1e-12, fine_step), 4)
    fine = fine[fine > 0.0]
    curve_f = [(round(float(l), 4), round(_ll(float(l)), 6)) for l in fine]
    best_f, best_ll = min(curve_f, key=lambda t: t[1])
    return {
        "lambda": float(best_f),
        "explore_place_ll_at_lambda": best_ll,
        "explore_place_ll_at_1": round(_ll(1.0), 6),
        "coarse_curve": curve,
        "fine_curve": curve_f,
        "n_races": int(d["race_id"].nunique()),
        "note": "探索窓の place_slots=3 で place_ll を最小化。確認窓へはこの値をそのまま流用する",
    }


def attach_place_probs(ev: pd.DataFrame, lam: float) -> tuple[pd.DataFrame, dict]:
    """4腕の複勝確率を1枚の DataFrame に付ける（母集団は4腕で完全に同一）。"""
    d = ev.reset_index(drop=True).copy()
    d[PLACE_COL["harville"]] = harville_place(d, "p_win")
    d[PLACE_COL["top3_raw"]] = np.clip(d["p_top3_raw"].to_numpy(dtype=float), EPS, 1.0 - EPS)
    norm, n_clip = _top3_norm(d)
    d[PLACE_COL["top3_norm"]] = norm
    d[PLACE_COL["henery_fit"]] = np.clip(_henery_place(d, lam), EPS, 1.0 - EPS)
    return d, {"top3_norm_clipped_horses": n_clip}


# ---------------------------------------------------------------------------
# 主指標: 複勝対数損失の対応差
# ---------------------------------------------------------------------------

def _place_ll_per_race(d: pd.DataFrame, col: str) -> pd.Series:
    """レースごとの `place_ll`（そのレースの出走馬の二値対数損失の平均）。

    `place_scores` の `place_logloss` は**頭数重み**のプール平均。対応差の
    レースクラスタ bootstrap にはレース単位の値が要るのでここで分解する
    （プールし直すと `place_scores` と一致することを `main` で検算している）。
    """
    y = (pd.to_numeric(d["finish_position"], errors="coerce")
         <= d["place_slots"]).to_numpy(dtype=float)
    p = np.clip(d[col].to_numpy(dtype=float), EPS, 1.0 - EPS)
    ll = -(y * np.log(p) + (1 - y) * np.log(1 - p))
    return pd.Series(ll).groupby(d["race_id"].to_numpy()).mean()


def quarter_paired_place(ev: pd.DataFrame, arm: str, n_boot: int,
                         min_improved: int, seed: int = 20260904) -> dict:
    """四半期別の `place_ll` 対応差（arm − harville）と、その等重み平均の 95%CI。

    - 母集団は `place_slots == 3`（§12.3「判定は place_slots=3 で行う」）
    - bootstrap は**レースクラスタ**（四半期ごとに再標本 → 四半期平均を等重みで平均）
    """
    d = ev[ev["place_slots"] == 3]
    quarters = sorted(d["quarter"].unique())
    per_q: dict[str, np.ndarray] = {}
    rows = []
    for q in quarters:
        g = d[d["quarter"] == q]
        a = _place_ll_per_race(g, PLACE_COL[arm])
        b = _place_ll_per_race(g, PLACE_COL[BASE_ARM])
        common = a.index.intersection(b.index)
        diff = (a.loc[common] - b.loc[common]).to_numpy()
        per_q[q] = diff
        rows.append({
            "quarter": q, "n_races": int(len(diff)),
            "arm_place_ll": round(float(a.loc[common].mean()), 5),
            "base_place_ll": round(float(b.loc[common].mean()), 5),
            "delta": round(float(diff.mean()), 5),
            "improved": bool(diff.mean() < 0),
        })

    rng = np.random.default_rng(seed)
    qkeys = list(per_q)
    eq_boot = np.empty(n_boot, dtype=float)
    all_d = np.concatenate([per_q[q] for q in qkeys])
    pooled_boot = np.empty(n_boot, dtype=float)
    for b in range(n_boot):
        eq_boot[b] = float(np.mean(
            [per_q[q][rng.integers(0, len(per_q[q]), len(per_q[q]))].mean() for q in qkeys]))
        pooled_boot[b] = all_d[rng.integers(0, len(all_d), len(all_d))].mean()

    def _ci(x):
        return [round(float(np.percentile(x, 2.5)), 5), round(float(np.percentile(x, 97.5)), 5)]

    eq_point = float(np.mean([per_q[q].mean() for q in qkeys]))
    n_improved = int(sum(1 for q in qkeys if per_q[q].mean() < 0))
    eq_ci = _ci(eq_boot)
    return {
        "by_quarter": rows,
        "n_quarters": len(qkeys),
        "n_improved_quarters": n_improved,
        "quarter_equal_weight": {"delta": round(eq_point, 5), "ci95": eq_ci},
        "race_pooled": {"delta": round(float(all_d.mean()), 5), "ci95": _ci(pooled_boot),
                        "n_races": int(len(all_d))},
        "criterion_ci_excludes_zero_improving": bool(eq_ci[1] < 0),
        "criterion_min_improved_quarters": bool(n_improved >= min_improved),
        "min_improved_required": int(min_improved),
    }


# ---------------------------------------------------------------------------
# 副指標: §9.1 の層別「複勝残差」（🔴 excess ではない）
# ---------------------------------------------------------------------------

CUT_NAMES = ("runner_type", "finish_var_tertile", "win_place_ratio_tertile")


def residual_by_level(ev: pd.DataFrame, arm: str, cutoffs: dict, n_boot: int,
                      seed: int = 20260904) -> dict:
    """`p_win` 10分位で調整した**複勝残差** `1[3着以内] − p_place` を層別に出す。

    🔴 §12.3: `excess = residual − (p_place/p_win)·win_residual` は
    `∂p_place/∂p_win ≒ p_place/p_win` という **Harville 前提の一次近似**なので、
    独立 `is_top3` ヘッドには当てはまらない。よって
    **残差そのものが 0 に近づいたか**で見る。

    - 母集団は `place_slots == 3`（§9.1 と同じ）
    - `p_win` 10分位で中心化してから水準平均（§9.1 罠6: 層別しないと最上位 decile が支配する）
    - CI はレースクラスタ bootstrap（`_bootstrap_indices` を共有）
    - 🔴 カット点は `harville` 腕の**探索窓**で1回決めて全腕・全窓に流用（§9.1 罠4）
    """
    d = ev[ev["place_slots"] == 3].reset_index(drop=True)
    if not len(d):
        return {}
    d = assign_levels(d, cutoffs)
    y = (pd.to_numeric(d["finish_position"], errors="coerce") <= 3).to_numpy(dtype=float)
    res = y - d[PLACE_COL[arm]].to_numpy(dtype=float)
    dec = d["pwin_decile"].to_numpy(dtype=int)
    n_dec = int(dec.max()) + 1
    race_codes = pd.factorize(d["race_id"].to_numpy())[0]
    rng = np.random.default_rng(seed)

    def _adj(vals: np.ndarray, idx: np.ndarray, codes: np.ndarray, k: int) -> np.ndarray:
        vv, dd = vals[idx], dec[idx]
        ds = np.bincount(dd, weights=vv, minlength=n_dec)
        dc = np.bincount(dd, minlength=n_dec).astype(float)
        dm = np.divide(ds, dc, out=np.zeros_like(ds), where=dc > 0)
        vadj = vv - dm[dd]
        cc = codes[idx]
        m = cc >= 0
        ls = np.bincount(cc[m], weights=vadj[m], minlength=k)
        lc = np.bincount(cc[m], minlength=k).astype(float)
        return np.divide(ls, lc, out=np.full(k, np.nan), where=lc > 0)

    spec = {}
    for cut in CUT_NAMES:
        levels = list(CUTS[cut]) + ["unknown"] + (
            ["no_place"] if cut == "win_place_ratio_tertile" else [])
        code_map = {lv: i for i, lv in enumerate(levels)}
        codes = d[f"cut_{cut}"].map(lambda v: code_map.get(v, -1)).to_numpy(dtype=int)
        spec[cut] = (code_map, codes, len(levels), np.empty((n_boot, len(levels)), dtype=float))

    # bootstrap の行インデックスは1回だけ作って3つの切り口で使い回す
    for b, idx in enumerate(_bootstrap_indices(race_codes, n_boot, rng)):
        for cut in CUT_NAMES:
            _, codes, k, bx = spec[cut]
            bx[b] = _adj(res, idx, codes, k)

    all_idx = np.arange(len(d))
    out: dict = {
        "n_horses": int(len(d)), "n_races": int(d["race_id"].nunique()),
        "overall_place_residual_pt": round(float(res.mean() * 100), 4),
        "cuts": {},
    }
    for cut in CUT_NAMES:
        code_map, codes, k, bx = spec[cut]
        pt = _adj(res, all_idx, codes, k)
        lo = np.nanpercentile(bx, 2.5, axis=0)
        hi = np.nanpercentile(bx, 97.5, axis=0)
        out["cuts"][cut] = {
            lv: {
                "n": int((codes == i).sum()),
                "place_residual_pt": round(float(pt[i] * 100), 4),
                "ci95_pt": [round(float(lo[i] * 100), 4), round(float(hi[i] * 100), 4)],
            }
            for lv, i in code_map.items() if (codes == i).sum() > 0
        }
    return out


def freeze_cutoffs(ev: pd.DataFrame) -> dict:
    """3分位・10分位のカット点を **`harville` 腕の探索窓**で1回だけ決める（§9.1 罠4）。

    `p_win` は4腕で同一なので `pwin_decile` は腕によらないが、事前登録の
    「`harville` 腕で1回決めて4腕に流用」をそのまま実装する。
    """
    d = ev[ev["place_slots"] == 3]
    fv = d["finish_var5"].dropna().to_numpy(dtype=float)
    wr = d["win_place_ratio5"]
    wr = wr[wr >= 0].dropna().to_numpy(dtype=float)
    return {
        "finish_var_tertile": [float(np.quantile(fv, 1 / 3)), float(np.quantile(fv, 2 / 3))],
        "win_place_ratio_tertile": [float(np.quantile(wr, 1 / 3)), float(np.quantile(wr, 2 / 3))],
        "pwin_decile": [float(x) for x in
                        np.quantile(d["p_win"].to_numpy(dtype=float), np.arange(1, 10) / 10)],
    }


# ---------------------------------------------------------------------------
# 🔴 目視確認: 実データを1レース表示する（CLAUDE.md「測る前に本番コードを読む」）
# ---------------------------------------------------------------------------

def visual_check(ev: pd.DataFrame, lam: float) -> list[str]:
    """全体を回す前に必ず目で見る。確認するのは4点（§実施内容）:

      1. `Σp_win = 1.0`
      2. `harville` の `Σp_place = place_slots`
      3. `top3_norm` の `Σp = place_slots`
      4. `top3_raw` は合計が `place_slots` からずれてよい
    """
    d = ev[ev["place_slots"] == 3]
    # 中身が見えるレースを選ぶ: レース名があり、過去5走が揃う馬が過半のもの
    ok = d.groupby("race_id").agg(
        named=("race_name", lambda s: s.notna().all()),
        frac5=("finish_var5", lambda s: float(s.notna().mean())),
    )
    cand = ok[ok["named"] & (ok["frac5"] >= 0.5)]
    rid = int(cand.index[0]) if len(cand) else int(d["race_id"].iloc[0])
    g = ev[ev["race_id"] == rid].sort_values("horse_number")
    r0 = g.iloc[0]
    L = ["", "=" * 126]
    L.append(f"🔴 目視確認 race_id={rid} {r0['date']} {r0['course_name']}"
             f"{int(r0['race_number'])}R {r0['race_name']} "
             f"n={len(g)} place_slots={int(r0['place_slots'])} quarter={r0['quarter']} "
             f"（λ={lam:.4f}）")
    L.append("=" * 126)
    hdr = (f"{'馬番':>4}{'着':>4}{'馬名':<20}{'脚質':>9}{'着順分散':>10}{'勝/複':>8}"
           f"{'p_win':>10}{'raw_top3':>11}")
    for a in ARMS:
        hdr += f"{a:>13}"
    L.append(hdr)

    def _n(v, w, dg=4):
        return f"{'NaN':>{w}}" if v is None or (isinstance(v, float) and np.isnan(v)) \
            else f"{float(v):>{w}.{dg}f}"

    for _, r in g.iterrows():
        row = (f"{int(r['horse_number']):>4}{int(r['finish_position']):>4}"
               f"{str(r['horse_name'])[:19]:<20}{str(r['runner_type']):>9}"
               f"{_n(r['finish_var5'], 10, 2)}{_n(r['win_place_ratio5'], 8, 2)}"
               f"{_n(r['p_win'], 10, 5)}{_n(r['p_top3_raw'], 11, 5)}")
        for a in ARMS:
            row += _n(r[PLACE_COL[a]], 13, 5)
        L.append(row)
    tot = f"{'Σ':>4}{'':>4}{'':<20}{'':>9}{'':>10}{'':>8}{g['p_win'].sum():>10.5f}" \
          f"{g['p_top3_raw'].sum():>11.5f}"
    for a in ARMS:
        tot += f"{g[PLACE_COL[a]].sum():>13.5f}"
    L.append(tot)
    L.append(f"（期待: Σp_win=1.00000 / harville と top3_norm と henery_fit の Σ="
             f"{int(r0['place_slots'])}.00000 / top3_raw だけはずれてよい）")
    L.append("（p_win は4腕で同一。腕の違いは複勝確率の作り方だけ）")
    return L


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--explore-quarters", default="2024Q1..2025Q2",
                   help="探索窓（既定 = 事前登録 §12.2 の6四半期）")
    p.add_argument("--confirm-quarters", default="2025Q3..2026Q2",
                   help="確認窓（既定 = 事前登録 §12.2 の4四半期）")
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--bootstrap", type=int, default=2000)
    p.add_argument("--out", default=str(OUT_PATH))
    p.add_argument("--data-start", default="20230101", help="学習データの開始日")
    p.add_argument("--valid-days", type=int, default=90,
                   help="early stopping 用に train の末尾から取る日数")
    p.add_argument("--min-improved-explore", type=int, default=4,
                   help="探索窓の採用条件（§12.3: 改善 4/6 以上）")
    p.add_argument("--cache", default=None, help="データセット pickle（冪等・再利用可）")
    p.add_argument("--pred-cache", default=None, help="walk-forward 予測 pickle")
    args = p.parse_args()

    seeds = [int(s) for s in args.seeds.split(",")]
    q_exp = parse_quarters(args.explore_quarters)
    q_con = parse_quarters(args.confirm_quarters)
    qs = q_exp + q_con
    if len({q[0] for q in qs}) != len(qs):
        raise SystemExit("探索窓と確認窓に同じ四半期が入っている")

    # 🔴 §12.2: TEST 窓（2026Q3 以降）を絶対に使わない
    eval_end = max(q[2] for q in qs)
    if eval_end >= TEST_START:
        raise SystemExit(
            f"🔴 評価終端 {eval_end} が TEST_START={TEST_START} 以降。事前登録 §12.2 により "
            f"2026Q3 は Phase D では使わない（一度きり評価の温存）")
    logger.info("探索 %d本: %s", len(q_exp), " / ".join(q[0] for q in q_exp))
    logger.info("確認 %d本: %s", len(q_con), " / ".join(q[0] for q in q_con))

    cache = Path(args.cache) if args.cache else None
    if cache and cache.exists():
        df = pd.read_pickle(cache)
        logger.info("データセットをキャッシュから読込: %s", cache)
    else:
        df = build_dataset(args.data_start, eval_end)
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            df.to_pickle(cache)

    missing = [c for c in FEAT_COLS if c not in df.columns]
    if missing:
        raise SystemExit(f"データセットに `feat` 特徴が足りない: {missing}")

    # 🔴 2重ガード: TEST 窓の行を DataFrame からも落とす
    n_before = len(df)
    df = df[df["date"] < TEST_START].reset_index(drop=True)
    if len(df) != n_before:
        logger.info("TEST 窓(%s 以降) を %d行 除外", TEST_START, n_before - len(df))
    logger.info("母集団: %d行 / %dレース (%s〜%s)", len(df), df["race_id"].nunique(),
                df["date"].min(), df["date"].max())

    pred_cache = Path(args.pred_cache) if args.pred_cache else None
    if pred_cache and pred_cache.exists():
        ev = pd.read_pickle(pred_cache)
        logger.info("予測をキャッシュから読込: %s", pred_cache)
    else:
        logger.info("=== walk-forward（is_win ヘッド + 独立 is_top3 ヘッド・特徴は feat 固定）===")
        ev = run_walk_forward(df, qs, seeds, args.valid_days)
        if pred_cache:
            pd.to_pickle(ev, pred_cache)

    ev = ev.reset_index(drop=True)
    exp_labels = {q[0] for q in q_exp}
    con_labels = {q[0] for q in q_con}
    ev_exp_pre = ev[ev["quarter"].isin(exp_labels)].reset_index(drop=True)

    # --- λ を探索窓で当てる（🔴 既存の 0.4345 は使わない・§訂正1）---
    t0 = time.time()
    lam_info = fit_lambda(ev_exp_pre, np.round(np.arange(0.20, 2.01, 0.10), 4))
    lam = lam_info["lambda"]
    logger.info("λ フィット（探索窓・place_slots=3・%dR）: λ=%.4f "
                "place_ll %.6f → %.6f (%.1fs)",
                lam_info["n_races"], lam, lam_info["explore_place_ll_at_1"],
                lam_info["explore_place_ll_at_lambda"], time.time() - t0)

    ev, norm_info = attach_place_probs(ev, lam)
    ev_exp = ev[ev["quarter"].isin(exp_labels)].reset_index(drop=True)
    ev_con = ev[ev["quarter"].isin(con_labels)].reset_index(drop=True)

    # --- 🔴 目視確認（集計より先に出す）---
    vis = visual_check(ev, lam)
    print("\n".join(vis))

    # --- 実装の自己検査 ---
    checks: dict = {}
    d3 = ev[ev["place_slots"] == 3]
    sums = d3.groupby("race_id")[[PLACE_COL[a] for a in ARMS] + ["p_win"]].sum()
    checks["max_abs_dev_p_win_sum_from_1"] = round(float((sums["p_win"] - 1.0).abs().max()), 9)
    for a in ARMS:
        checks[f"max_abs_dev_sum_from_3__{a}"] = round(
            float((sums[PLACE_COL[a]] - 3.0).abs().max()), 6)
    lam1 = np.clip(_henery_place(ev, 1.0), EPS, 1.0 - EPS)
    checks["henery_lambda1_equals_harville_maxdiff"] = round(
        float(np.abs(lam1 - ev[PLACE_COL["harville"]].to_numpy()).max()), 12)
    checks["top3_norm_clipped_horses"] = norm_info["top3_norm_clipped_horses"]
    # `_place_ll_per_race` を頭数重みでプールし直すと place_scores と一致するか
    for a in ARMS:
        ps = place_scores(d3, PLACE_COL[a], "p_win")
        yy = (pd.to_numeric(d3["finish_position"], errors="coerce") <= 3).to_numpy(dtype=float)
        pp = np.clip(d3[PLACE_COL[a]].to_numpy(dtype=float), EPS, 1.0 - EPS)
        pooled = float(-(yy * np.log(pp) + (1 - yy) * np.log(1 - pp)).mean())
        checks[f"place_ll_matches_place_scores__{a}"] = bool(
            abs(pooled - ps["place_logloss"]) < 5e-6)
    print("\n【実装の自己検査】")
    for k, v in checks.items():
        print(f"  {k}: {v}")

    print(f"\n探索窓: {ev_exp['race_id'].nunique():,}R / {len(ev_exp):,}頭 "
          f"({ev_exp['date'].min()}〜{ev_exp['date'].max()}) 四半期{len(q_exp)}本")
    print(f"確認窓: {ev_con['race_id'].nunique():,}R / {len(ev_con):,}頭 "
          f"({ev_con['date'].min()}〜{ev_con['date'].max()}) 四半期{len(q_con)}本")
    print(f"特徴量: feat 固定（{len(FEAT_NAMES)}列）／ p_win は4腕で同一")

    results: dict = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "preregistration": "docs/jra_winplace_structure_plan_2026_09_04.md §12.3 (Phase D-1)",
        "config": {
            "explore_quarters": [q[0] for q in q_exp],
            "confirm_quarters": [q[0] for q in q_con],
            "seeds": seeds, "bootstrap": args.bootstrap,
            "valid_days": args.valid_days, "data_start": args.data_start,
            "test_start_guard": TEST_START,
            "feature_arm": "feat", "n_features": len(FEAT_NAMES),
            "arms": list(ARMS), "baseline": BASE_ARM,
            "primary_metric": "place_ll (is_top3 binary logloss, place_slots=3)",
            "criterion": "探索: 95%CI が0を跨がず改善側 かつ 改善 "
                         f"{args.min_improved_explore}/{len(q_exp)} 以上、"
                         "かつ 確認窓の等重み平均が同符号",
        },
        "population": {
            "explore": {"n_races": int(ev_exp["race_id"].nunique()), "n_horses": int(len(ev_exp)),
                        "date_min": str(ev_exp["date"].min()), "date_max": str(ev_exp["date"].max())},
            "confirm": {"n_races": int(ev_con["race_id"].nunique()), "n_horses": int(len(ev_con)),
                        "date_min": str(ev_con["date"].min()), "date_max": str(ev_con["date"].max())},
        },
        "lambda_fit": lam_info,
        "self_checks": checks,
        "visual_check": vis,
    }

    # ------------------------------------------------------------------
    # 主指標
    # ------------------------------------------------------------------
    print("\n" + "=" * 126)
    print("  【主指標】複勝対数損失 place_ll の harville との対応差（place_slots=3・負＝改善）")
    print("=" * 126)
    primary: dict = {}
    for a in ARMS:
        if a == BASE_ARM:
            continue
        pe = quarter_paired_place(ev_exp, a, args.bootstrap, args.min_improved_explore)
        pc = quarter_paired_place(ev_con, a, args.bootstrap, 1)
        exp_ok = (pe["criterion_ci_excludes_zero_improving"]
                  and pe["criterion_min_improved_quarters"])
        same_sign = bool(np.sign(pc["quarter_equal_weight"]["delta"])
                         == np.sign(pe["quarter_equal_weight"]["delta"])
                         and pe["quarter_equal_weight"]["delta"] != 0)
        primary[a] = {
            "explore": pe, "confirm": pc,
            "criterion_confirm_same_sign": same_sign,
            "verdict": "採用候補" if (exp_ok and same_sign) else "不採用",
        }

    for a in ARMS:
        if a == BASE_ARM:
            continue
        v = primary[a]
        print(f"\n  [{a}]")
        print(f"    {'窓':<6}{'四半期':<9}{'nR':>7}{'harville':>11}{'arm':>11}{'Δ':>11}{'':>4}")
        for wname, key in (("探索", "explore"), ("確認", "confirm")):
            for r in v[key]["by_quarter"]:
                print(f"    {wname:<6}{r['quarter']:<9}{r['n_races']:>7}"
                      f"{r['base_place_ll']:>11.5f}{r['arm_place_ll']:>11.5f}"
                      f"{r['delta']:>+11.5f}{'  改善' if r['improved'] else '  悪化':>4}")
        for wname, key in (("探索", "explore"), ("確認", "confirm")):
            eq = v[key]["quarter_equal_weight"]
            po = v[key]["race_pooled"]
            print(f"    {wname} 等重み平均 Δ={eq['delta']:+.5f} "
                  f"95%CI=[{eq['ci95'][0]:+.5f}, {eq['ci95'][1]:+.5f}] ｜"
                  f" レース重み Δ={po['delta']:+.5f} "
                  f"[{po['ci95'][0]:+.5f}, {po['ci95'][1]:+.5f}] (n={po['n_races']}R) ｜"
                  f" 改善 {v[key]['n_improved_quarters']}/{v[key]['n_quarters']}")
        pe = v["explore"]
        print(f"    判定: CI条件 {'○' if pe['criterion_ci_excludes_zero_improving'] else '×'}"
              f" / 改善{pe['min_improved_required']}本以上 "
              f"{'○' if pe['criterion_min_improved_quarters'] else '×'}"
              f" / 確認同符号 {'○' if v['criterion_confirm_same_sign'] else '×'}"
              f" → **{v['verdict']}**")
    results["primary"] = primary

    # ------------------------------------------------------------------
    # 副指標
    # ------------------------------------------------------------------
    print("\n" + "=" * 126)
    print("  【副指標】複勝側の指標と交差件数（採否には使わない）")
    print("=" * 126)
    sec: dict = {}
    print(f"{'窓':<6}{'腕':<12}{'slots':>6}{'nR':>7}{'place_ll':>11}{'coverage@k':>12}"
          f"{'spearman':>11}{'交差R':>8}{'交差ペア':>10}{'同値ペア':>10}")
    for wname, evw in (("explore", ev_exp), ("confirm", ev_con)):
        for a in ARMS:
            for slots in (3, 2):
                s = evw[evw["place_slots"] == slots]
                if not len(s):
                    continue
                m = place_scores(s, PLACE_COL[a], "p_win")
                sec.setdefault(wname, {}).setdefault(a, {})[f"slots_{slots}"] = m
                sp = m["spearman_in_race"]
                print(f"{wname:<8}{a:<12}{slots:>6}{m['n_races']:>7}{m['place_logloss']:>11.5f}"
                      f"{m['coverage_at_k']:>12.4f}"
                      f"{(f'{sp:.4f}' if sp is not None else '-'):>11}"
                      f"{m['cross_races']:>8}{m['cross_pairs']:>10}{m['tied_pairs']:>10}")
    results["secondary_place"] = sec

    # 単勝側は4腕で同一（p_win が同じ）。1回だけ出して同一性の証拠にする
    ws = {w: {k: v for k, v in win_scores(e, "p_win").items() if not k.startswith("_")}
          for w, e in (("explore", ev_exp), ("confirm", ev_con))}
    results["win_side_shared"] = ws
    print(f"\n（参考・4腕共通の単勝側）探索 MNL={ws['explore']['mnl_logloss']:.5f} "
          f"top1勝率={ws['explore']['top1_win_rate']:.4f} / "
          f"確認 MNL={ws['confirm']['mnl_logloss']:.5f} "
          f"top1勝率={ws['confirm']['top1_win_rate']:.4f}")

    # ------------------------------------------------------------------
    # 層別の複勝残差（🔴 excess ではない）
    # ------------------------------------------------------------------
    cutoffs = freeze_cutoffs(ev_exp[ev_exp["quarter"].isin(exp_labels)])
    results["cutoffs"] = cutoffs
    print("\n" + "=" * 126)
    print("  【副指標】層別の複勝残差 1[3着以内] − p_place（p_win 10分位で調整・pt）")
    print("  🔴 excess は Harville 前提の一次近似なので独立ヘッドには使わない。残差が 0 に近いほど良い")
    print("=" * 126)
    resid: dict = {}
    for wname, evw in (("explore", ev_exp), ("confirm", ev_con)):
        for a in ARMS:
            resid.setdefault(wname, {})[a] = residual_by_level(
                evw, a, cutoffs, args.bootstrap)
    results["residual_by_level"] = resid

    for cut in CUT_NAMES:
        print(f"\n  ── {cut} ──")
        levels = [lv for lv in resid["explore"][BASE_ARM]["cuts"][cut]]
        head = f"{'水準':<12}{'n':>8}"
        for a in ARMS:
            head += f"{a + '(探索)':>22}"
        head += f"{'':>2}" + "".join(f"{a + '(確認)':>12}" for a in ARMS)
        print(head)
        for lv in levels:
            line = f"{lv:<12}{resid['explore'][BASE_ARM]['cuts'][cut][lv]['n']:>8}"
            for a in ARMS:
                r = resid["explore"][a]["cuts"][cut].get(lv)
                line += (f"{r['place_residual_pt']:>+8.2f}"
                         f"[{r['ci95_pt'][0]:+6.2f},{r['ci95_pt'][1]:+6.2f}]") if r else f"{'-':>22}"
            line += "  "
            for a in ARMS:
                r = resid["confirm"][a]["cuts"][cut].get(lv)
                line += f"{r['place_residual_pt']:>+12.2f}" if r else f"{'-':>12}"
            print(line)

    # 🔴 §12.5 の分岐材料: 着順分散 T1/T3 と 勝率複勝率比 T1 の |残差| が縮んだか
    key_levels = [("finish_var_tertile", "T1_low"), ("finish_var_tertile", "T3_high"),
                  ("win_place_ratio_tertile", "T1_low")]
    shrink: dict = {}
    for a in ARMS:
        if a == BASE_ARM:
            continue
        rows = []
        for cut, lv in key_levels:
            b_e = resid["explore"][BASE_ARM]["cuts"][cut][lv]["place_residual_pt"]
            a_e = resid["explore"][a]["cuts"][cut][lv]["place_residual_pt"]
            b_c = resid["confirm"][BASE_ARM]["cuts"][cut][lv]["place_residual_pt"]
            a_c = resid["confirm"][a]["cuts"][cut][lv]["place_residual_pt"]
            rows.append({
                "cut": cut, "level": lv,
                "explore_base_pt": b_e, "explore_arm_pt": a_e,
                "explore_abs_shrink_pct": round((abs(b_e) - abs(a_e)) / abs(b_e) * 100, 1)
                if abs(b_e) > 1e-9 else None,
                "confirm_base_pt": b_c, "confirm_arm_pt": a_c,
                "confirm_abs_shrink_pct": round((abs(b_c) - abs(a_c)) / abs(b_c) * 100, 1)
                if abs(b_c) > 1e-9 else None,
            })
        shrink[a] = rows
    results["key_level_shrink"] = shrink

    print("\n" + "=" * 126)
    print("  【§12.5 stop rule の分岐材料】着順分散 T1/T3・勝率複勝率比 T1 の |残差| が縮んだか")
    print("=" * 126)
    print(f"{'腕':<12}{'層':<28}{'探索 base→arm':>26}{'縮小%':>9}{'確認 base→arm':>26}{'縮小%':>9}")
    for a, rows in shrink.items():
        for r in rows:
            print(f"{a:<12}{r['cut'] + '/' + r['level']:<28}"
                  f"{r['explore_base_pt']:>+12.2f} →{r['explore_arm_pt']:>+11.2f}"
                  f"{(r['explore_abs_shrink_pct'] if r['explore_abs_shrink_pct'] is not None else float('nan')):>9.1f}"
                  f"{r['confirm_base_pt']:>+12.2f} →{r['confirm_arm_pt']:>+11.2f}"
                  f"{(r['confirm_abs_shrink_pct'] if r['confirm_abs_shrink_pct'] is not None else float('nan')):>9.1f}")

    # ------------------------------------------------------------------
    # まとめ
    # ------------------------------------------------------------------
    adopted = [a for a in primary if primary[a]["verdict"] == "採用候補"]
    results["verdicts"] = {a: primary[a]["verdict"] for a in primary}
    results["adopted_candidates"] = adopted
    print("\n" + "=" * 126)
    print("  【判定】" + " / ".join(f"{a}: {primary[a]['verdict']}" for a in primary))
    print("=" * 126)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    def _clean(o):
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items() if not str(k).startswith("_")}
        if isinstance(o, list):
            return [_clean(v) for v in o]
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.bool_,)):
            return bool(o)
        return o

    out.write_text(json.dumps(_clean(results), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n保存: {out}")


if __name__ == "__main__":
    main()
