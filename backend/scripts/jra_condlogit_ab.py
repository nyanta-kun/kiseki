"""JRA 単勝ヘッド 目的関数 3腕 walk-forward A/B（事前登録 §12.4 Phase D-2 の実装）

事前登録: `docs/jra_winplace_structure_plan_2026_09_04.md` §12.2〜12.4。
**本スクリプトは事前登録の仕様を実装するだけで、判定基準を動かさない。**

## 目的

現行の単勝確率は `objective="binary"` の is_win を学習し、**推論時にレース内で
L1 正規化するだけ**（`composite.py:737-741` / `train_jra_iswin_head.py`）。
つまり「同じレースの他馬より強いか」が**目的関数に一つも入っていない**。
Benter が市場を超えたときの本体は多項ロジット（conditional logit）で、kiseki は
一度も通っていない。§12.4 はこれを同一 walk-forward の対応比較で測る。

⚠️ `docs/jra_new_index_results.md` の Phase 2 は「Listwise は LambdaRank で実施済み」
として多項ロジットを検討対象から外しているが、**LambdaRank は NDCG のサロゲートで
確率を出さない**。別物であることを同一台で示すために参考腕として置く（採否には使わない）。

## 腕（§12.4・特徴量は3腕とも `feat` で固定。変えるのは目的関数だけ）

| 腕 | 目的関数 | 確率への変換 |
|---|---|---|
| `binary` | `objective="binary"`（現行 `PARAMS`） | レース内 L1 正規化（本番と同じ） |
| `condlogit` | **レース内 softmax の多項対数損失をカスタム目的関数で実装** | レース内 softmax |
| `lambdarank` | `objective="lambdarank"`（参考・採否に使わない） | レース内 softmax |

- `condlogit` の grad/hess: `grad_i = p_i − y_i` / `hess_i = max(p_i(1−p_i), 1e-6)`、
  group は race_id。softmax は max を引いてから exp（数値安定性）。
  レース境界は事前に配列で持ち、`np.maximum.reduceat` / `np.add.reduceat` で
  ベクトル化する（レースごとの Python ループは 2000 ラウンド×13万行で非現実的）
- `lambdarank` の relevance と `label_gain` は既存の
  `scripts/jra_rank_quality_review.train_lambdarank`（1着=4/2着=3/3着=2/4-5着=1/他=0、
  `label_gain=[0,1,3,7,15]`）を**そのまま踏襲**する（独自定義をしない）
- ハイパラは3腕とも `train_jra_iswin_head.PARAMS` を import して共有。
  変えるのは `objective` / `metric` /（lambdarank のみ）`label_gain` だけ

### 🔴 早期終了は3腕とも同じ「レース単位の多項対数損失」で行う（逸脱の明示）

事前登録は「3腕とも同一 early stopping の枠組み」かつ「`binary_logloss` で止めると
目的関数と評価が食い違う」と指定している。両方を満たす唯一の形は
**主指標そのもの（レース単位 多項対数損失）をカスタム評価関数にして3腕に共通で使う**
こと。よって `binary` 腕も Phase C（`binary_logloss` で早期終了）とは
**early stopping だけが異なる**。母集団・特徴・seed・ラウンド上限は同一。
`--binary-es binary_logloss` で Phase C と同じ止め方の感度も取れる。
**この決定は結果を見る前に行った。**

⚠️ LightGBM のカスタム評価関数が受け取る `preds` は、**組込み目的関数では変換後**
（binary ならシグモイド済みの確率）、**カスタム目的関数では raw score**。
腕ごとに変換を切り替える必要がある（`lightgbm/engine.py` の docstring・実装で確認）。

## 特徴量

Phase C の採用候補 `feat`（34特徴 + 脚質 + 着順分散 + 勝率複勝率比 + `pace_handicap_pit`）。
データセット生成・PIT 特徴・母集団は `scripts/jra_winplace_feature_ab.py` を
**import して共有する**（独立実装をしない）。多項対数損失・Harville・レース内正規化・
複勝指標は `scripts/jra_prob_scoring.py` を import して共有する。

## 窓（§12.2）

- **探索 2024Q1〜2025Q2（6四半期）** / **確認 2025Q3〜2026Q2（4四半期）**
- 🔴 **2026Q3（20260701以降）は絶対に使わない。** `TEST_START` ガードを入れてある

## 判定（§12.4・事前に固定・動かさない）

- **採用候補**: 探索6四半期の対応差（四半期等重み平均）の 95%CI が 0 を跨がず改善側、
  かつ改善四半期が **4/6 以上**、**かつ確認4四半期の平均差が同符号（改善側）**
- それ以外は不採用。`lambdarank` は参考として同じ数字を出すが**採否には使わない**

**副指標（報告必須・採否には使わない）**: top1勝率 / 較正の信頼性テーブル /
§9.2 の市場との差（0.16363 nat）を何%埋めたか / 複勝側（`_harville_place_probs`
経由の `place_ll`・`coverage@3`・交差件数。交差は Harville 経路なので 0 のはず）

## 使い方

    cd backend
    .venv/bin/python scripts/jra_condlogit_ab.py --self-test      # 合成データ検算のみ
    .venv/bin/python scripts/jra_condlogit_ab.py \
        --quarters 2024Q1..2026Q2 --explore-end 2025Q2 \
        --seeds 42,123,456 --bootstrap 2000 \
        --out ../docs/model_verification/jra_condlogit_ab.json

冪等（同じ引数なら同じ JSON を上書き）。`--cache` / `--pred-cache` で再実行を省略できる。
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

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# --- 既存の測定基盤をそのまま import する（独立実装をしない） -----------------
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
    parse_quarters,
    quarter_paired,
)
from scripts.train_jra_iswin_head import MAX_ROUND, PARAMS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("condlogit_ab")

OUT_PATH = _root.parent / "docs" / "model_verification" / "jra_condlogit_ab.json"

# 🔴 §9.2 で固定したモデル−市場の差。副指標「何%埋めたか」の分母
MARKET_GAP_NATS = 0.16363
PHASE_C_FEAT_DELTA = -0.00750      # §11.1 `feat` の四半期等重み Δ（= 市場差の 4.6%）

# 特徴量は Phase C の `feat` 腕で固定（§12.4）
FEAT_NAMES: list[str] = list(FEATURE_ARMS["feat"]["names"])
FEAT_COLS: list[str] = list(FEATURE_ARMS["feat"]["cols"])

# `lambdarank` の relevance / label_gain は jra_rank_quality_review.train_lambdarank を踏襲
LAMBDARANK_LABEL_GAIN = [0, 1, 3, 7, 15]

ARMS = ("binary", "condlogit", "lambdarank")
BASE_ARM = "binary"
HESS_FLOOR = 1e-6


# ---------------------------------------------------------------------------
# レース境界つきのベクトル化ユーティリティ
#   行が race_id ごとに連続していることを前提にする（呼び出し側で sort する）
# ---------------------------------------------------------------------------

def race_blocks(race_id: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """連続ブロックの開始位置 `starts` と各ブロックの長さ `counts` を返す。"""
    v = race_id.to_numpy()
    if len(v) == 0:
        return np.zeros(0, dtype=int), np.zeros(0, dtype=int)
    starts = np.flatnonzero(np.r_[True, v[1:] != v[:-1]])
    counts = np.diff(np.r_[starts, len(v)])
    if int(counts.sum()) != len(v) or len(np.unique(v)) != len(starts):
        raise SystemExit("🔴 行が race_id ごとに連続していない。sort_values の漏れ")
    return starts, counts


def softmax_by_race(f: np.ndarray, starts: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """レース内 softmax。max を引いてから exp を取る（数値安定性）。"""
    mx = np.maximum.reduceat(f, starts)
    e = np.exp(f - np.repeat(mx, counts))
    s = np.add.reduceat(e, starts)
    return e / np.repeat(s, counts)


def l1_by_race(p: np.ndarray, starts: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """レース内 L1 正規化（本番 `composite.py:737-741` と同じ形）。"""
    q = np.clip(p, 1e-9, 1.0)
    s = np.add.reduceat(q, starts)
    return q / np.repeat(s, counts)


# ---------------------------------------------------------------------------
# 🔴 condlogit のカスタム目的関数
# ---------------------------------------------------------------------------

def make_condlogit_objective(starts: np.ndarray, counts: np.ndarray, y: np.ndarray):
    """レース内 softmax の多項対数損失。

    L = − Σ_race Σ_i y_i log p_i,  p = softmax_race(raw score)
      grad_i = p_i − y_i
      hess_i = max(p_i (1 − p_i), HESS_FLOOR)

    LightGBM 4.x は `params["objective"]` に callable を置くと
    `basic.Booster.update` から `fobj(preds, train_set)` の形で呼ぶ（raw score）。
    `starts` / `counts` / `y` はこのクロージャに閉じ込める。
    """
    def fobj(preds: np.ndarray, dataset) -> tuple[np.ndarray, np.ndarray]:
        f = np.asarray(preds, dtype=float).ravel()
        if len(f) != len(y):
            raise SystemExit(f"🔴 objective に想定外の長さ: {len(f)} != {len(y)}")
        p = softmax_by_race(f, starts, counts)
        grad = p - y
        hess = np.maximum(p * (1.0 - p), HESS_FLOOR)
        return grad, hess
    return fobj


def make_race_mnl_eval(starts: np.ndarray, counts: np.ndarray,
                       win_pos: np.ndarray, transform: str, n_rows: int):
    """早期終了用のカスタム評価関数: レース単位の多項対数損失。

    `win_pos` は「勝ち馬がちょうど1頭のレース」の勝ち馬の行位置。
    `jra_prob_scoring.win_scores` と同じ定義（同着1着のレースは落とす）。

    ⚠️ `preds` は組込み目的関数（binary / lambdarank）では変換後、カスタム目的関数
    では raw score。`transform` で切り替える。
    """
    def feval(preds: np.ndarray, dataset) -> tuple[str, float, bool]:
        v = np.asarray(preds, dtype=float).ravel()
        if len(v) != n_rows:
            raise SystemExit(f"🔴 feval に想定外の長さ: {len(v)} != {n_rows}")
        p = softmax_by_race(v, starts, counts) if transform == "softmax" \
            else l1_by_race(v, starts, counts)
        ll = float(-np.log(np.maximum(p[win_pos], 1e-12)).mean())
        return "race_mnl_logloss", ll, False       # 低いほど良い
    return feval


def _win_positions(df: pd.DataFrame, starts: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """勝ち馬がちょうど1頭のレースについて、その勝ち馬の行位置を返す。"""
    y = (df["finish_position"] == 1).to_numpy().astype(float)
    n_win = np.add.reduceat(y, starts)
    idx = np.arange(len(y))
    # 各レースの最初の勝ち馬の位置（n_win==1 のレースだけ使うので「最初」で一意）
    first = np.add.reduceat(np.where(y > 0, idx, 0), starts)
    ok = n_win == 1
    return first[ok].astype(int)


# ---------------------------------------------------------------------------
# 学習（腕ごとに目的関数だけ差し替える。seed / 特徴 / ラウンド上限は共通）
# ---------------------------------------------------------------------------

def _sorted(d: pd.DataFrame) -> pd.DataFrame:
    """race_id ごとに行が連続するよう並べ替える（reduceat の前提）。"""
    return d.sort_values(["race_id", "horse_number"], kind="stable").reset_index(drop=True)


def fit_predict(tr: pd.DataFrame, va: pd.DataFrame, te: pd.DataFrame, arm: str,
                seeds: list[int], binary_es: str) -> tuple[np.ndarray, list[int]]:
    """1腕を seed 平均で学習し、te の**レース内確率**（Σ=1）を返す。

    3腕とも: 同一データセット / 同一特徴（`feat`）/ 同一 seed 群 / 同一ラウンド上限
    （`MAX_ROUND`）/ 同一 early stopping ラウンド数（100）。
    """
    s_tr, s_va, s_te = race_blocks(tr["race_id"]), race_blocks(va["race_id"]), \
        race_blocks(te["race_id"])
    Xtr = tr[FEAT_COLS].to_numpy(dtype=float)
    Xva = va[FEAT_COLS].to_numpy(dtype=float)
    Xte = te[FEAT_COLS].to_numpy(dtype=float)
    ytr = (tr["finish_position"] == 1).to_numpy().astype(float)
    yva = (va["finish_position"] == 1).to_numpy().astype(float)
    va_win = _win_positions(va, *s_va)

    probs, iters = [], []
    for s in seeds:
        if arm == "binary":
            params = dict(PARAMS, seed=s, objective="binary")
            d = lgb.Dataset(Xtr, ytr, feature_name=FEAT_NAMES)
            dv = lgb.Dataset(Xva, yva, reference=d)
            if binary_es == "binary_logloss":
                params["metric"] = "binary_logloss"
                feval = None
            else:
                params["metric"] = "None"
                feval = make_race_mnl_eval(*s_va, va_win, "l1", len(va))
        elif arm == "condlogit":
            fobj = make_condlogit_objective(*s_tr, ytr)
            params = dict(PARAMS, seed=s, objective=fobj, metric="None")
            d = lgb.Dataset(Xtr, ytr, feature_name=FEAT_NAMES)
            dv = lgb.Dataset(Xva, yva, reference=d)
            feval = make_race_mnl_eval(*s_va, va_win, "softmax", len(va))
        elif arm == "lambdarank":
            params = dict(PARAMS, seed=s, objective="lambdarank", metric="None",
                          label_gain=LAMBDARANK_LABEL_GAIN)
            d = lgb.Dataset(Xtr, _relevance(tr), group=s_tr[1], feature_name=FEAT_NAMES)
            dv = lgb.Dataset(Xva, _relevance(va), group=s_va[1], reference=d)
            feval = make_race_mnl_eval(*s_va, va_win, "softmax", len(va))
        else:
            raise SystemExit(f"未知の腕: {arm}")

        m = lgb.train(params, d, num_boost_round=MAX_ROUND, valid_sets=[dv],
                      feval=feval, callbacks=[lgb.early_stopping(100, verbose=False)])
        iters.append(int(m.best_iteration))
        raw = m.predict(Xte, num_iteration=m.best_iteration)
        # 腕ごとの確率化。3腕とも「seed ごとにレース内確率へ落としてから平均」
        probs.append(l1_by_race(raw, *s_te) if arm == "binary"
                     else softmax_by_race(raw, *s_te))
    p = np.mean(probs, axis=0)
    return l1_by_race(p, *s_te), iters      # 平均で Σ=1 が僅かにずれる分を戻す


def _relevance(d: pd.DataFrame) -> np.ndarray:
    """`jra_rank_quality_review.train_lambdarank` と同じ段階的 relevance。"""
    v = d["finish_position"].to_numpy()
    r = np.zeros(len(v), dtype=int)
    r[v == 1] = 4
    r[v == 2] = 3
    r[v == 3] = 2
    r[(v >= 4) & (v <= 5)] = 1
    return r


def run_arm(df: pd.DataFrame, arm: str, qs: list[tuple[str, str, str]],
            seeds: list[int], valid_days: int, binary_es: str) -> pd.DataFrame:
    """1腕を walk-forward で回し、評価行に p_win / p_place を付けて返す。

    窓の切り方は `jra_winplace_feature_ab.run_arm` と同一（train は四半期開始より前・
    末尾 `valid_days` 日を early stopping 用に取る）。
    """
    out = []
    for label, qstart, qend in qs:
        train = df[df["date"] < qstart]
        te = df[(df["date"] >= qstart) & (df["date"] <= qend)]
        if train.empty or te.empty:
            raise SystemExit(f"{label}: train か test が空（train={len(train)} test={len(te)}）")
        cut = (pd.to_datetime(qstart) - pd.Timedelta(days=valid_days)).strftime("%Y%m%d")
        tr, va = train[train["date"] <= cut], train[train["date"] > cut]
        if len(va) < 2000:
            i = int(len(train) * 0.8)
            tr, va = train.iloc[:i], train.iloc[i:]
        tr, va, te = _sorted(tr), _sorted(va), _sorted(te)
        t0 = time.time()
        p_win, iters = fit_predict(tr, va, te, arm, seeds, binary_es)
        te = te.copy()
        te["p_win"] = p_win
        te["p_place"] = harville_place(te, "p_win")
        te["quarter"] = label
        te["best_iters"] = str(iters)
        out.append(te)
        logger.info("  [%s] %s tr=%d va=%d te=%d/%dR iters=%s (%.1fs)",
                    arm, label, len(tr), len(va), len(te), te["race_id"].nunique(),
                    iters, time.time() - t0)
    return pd.concat(out, ignore_index=True)


# ---------------------------------------------------------------------------
# 🔴 合成データでの検算（学習前に必ず通す・§12.4 の指示）
# ---------------------------------------------------------------------------

def self_test(verbose: bool = True) -> dict:
    """condlogit の実装が正しいことを、DB を使わずに2段階で検算する。

    (1) grad / hess が損失の数値微分と一致するか
        L(f) = − Σ_race log p_winner に対して中心差分を取り、`grad_i = p_i − y_i` と
        `hess_i = p_i(1−p_i)` に一致することを確認する（HESS_FLOOR は掛からない大きさ）。

    (2) 収束後の予測が多項ロジットの解析解（最尤解）に一致するか
        全レースが同じ設計（特徴 x ∈ {0,1,2} が1頭ずつ）の合成データを作る。
        この設計では条件付きロジットのスコア方程式が
            Σ_race (y_i − p_i) = 0  （水準ごと）
        となり、**最尤解は「水準ごとの実測勝率」**に一致する（解析解）。
        木は x の3値で完全に表現できるので、LightGBM が解析解へ収束するはず。
    """
    L: list[str] = ["", "=" * 96, "🔴 condlogit 実装の検算（合成データ・DB 不使用）", "=" * 96]
    res: dict = {}

    # ---- (1) 数値微分との一致 ----
    rng = np.random.default_rng(20260904)
    counts = rng.integers(6, 19, size=40)
    starts = np.r_[0, np.cumsum(counts)[:-1]]
    n = int(counts.sum())
    f = rng.normal(0.0, 1.2, size=n)
    y = np.zeros(n)
    for s, c in zip(starts, counts):
        y[s + rng.integers(0, c)] = 1.0

    def loss(fv: np.ndarray) -> float:
        p = softmax_by_race(fv, starts, counts)
        return float(-np.log(np.maximum(p[y > 0], 1e-300)).sum())

    p = softmax_by_race(f, starts, counts)
    grad_a, hess_a = p - y, p * (1.0 - p)
    eps = 1e-5
    grad_n = np.empty(n)
    hess_n = np.empty(n)
    l0 = loss(f)
    for i in range(n):
        fp, fm = f.copy(), f.copy()
        fp[i] += eps
        fm[i] -= eps
        lp, lm = loss(fp), loss(fm)
        grad_n[i] = (lp - lm) / (2 * eps)
        hess_n[i] = (lp - 2 * l0 + lm) / (eps ** 2)
    g_err = float(np.abs(grad_a - grad_n).max())
    h_err = float(np.abs(hess_a - hess_n).max())
    res["fd_check"] = {"n_rows": n, "n_races": int(len(counts)),
                       "max_abs_grad_err": g_err, "max_abs_hess_err": h_err,
                       "grad_tol": 1e-6, "hess_tol": 1e-3,
                       "passed": bool(g_err < 1e-6 and h_err < 1e-3)}
    L.append(f"(1) 数値微分との一致  n={n}行 / {len(counts)}レース")
    L.append(f"    grad 最大絶対誤差 = {g_err:.3e} (許容 1e-6) → "
             f"{'OK' if g_err < 1e-6 else 'NG'}")
    L.append(f"    hess 最大絶対誤差 = {h_err:.3e} (許容 1e-3・中心差分の丸め込み) → "
             f"{'OK' if h_err < 1e-3 else 'NG'}")

    # ---- (2) 解析解（多項ロジット最尤解）への収束 ----
    G = 40000
    util = np.array([0.0, 1.0, 1.8])          # 真の効用
    true_p = np.exp(util) / np.exp(util).sum()
    rng2 = np.random.default_rng(7)
    x = np.tile(np.array([0.0, 1.0, 2.0]), G)
    win = rng2.choice(3, size=G, p=true_p)
    ys = np.zeros(3 * G)
    ys[np.arange(G) * 3 + win] = 1.0
    st = np.arange(G) * 3
    ct = np.full(G, 3)
    # 🔴 解析解: この設計では MLE は水準ごとの実測勝率に一致する
    analytic = np.bincount(win, minlength=3).astype(float) / G

    fobj = make_condlogit_objective(st, ct, ys)
    ds = lgb.Dataset(x.reshape(-1, 1), ys, feature_name=["x"])
    params = dict(objective=fobj, metric="None", num_leaves=3, max_depth=3,
                  min_data_in_leaf=50, learning_rate=0.2, lambda_l1=0.0, lambda_l2=0.0,
                  feature_fraction=1.0, bagging_fraction=1.0, verbose=-1, seed=0)
    m = lgb.train(params, ds, num_boost_round=400)
    raw = m.predict(x.reshape(-1, 1))
    fit = softmax_by_race(raw, st, ct)[:3]     # 全レース同一設計なので先頭1レースで足りる
    err = float(np.abs(fit - analytic).max())
    # raw score の差が log オッズ比に一致するか（加法定数を除く）
    dr = np.array([raw[1] - raw[0], raw[2] - raw[0]])
    da = np.log(analytic[1:] / analytic[0])
    res["analytic_mle_check"] = {
        "n_groups": G, "true_utilities": util.tolist(),
        "true_p": [round(float(v), 5) for v in true_p],
        "analytic_mle_p": [round(float(v), 5) for v in analytic],
        "condlogit_fitted_p": [round(float(v), 5) for v in fit],
        "max_abs_err": err, "tol": 5e-3, "passed": bool(err < 5e-3),
        "raw_score_diff": [round(float(v), 5) for v in dr],
        "analytic_log_odds_diff": [round(float(v), 5) for v in da],
        "max_abs_log_odds_err": float(np.abs(dr - da).max()),
        "sum_p": round(float(softmax_by_race(raw, st, ct)[:3].sum()), 12),
    }
    L.append("")
    L.append(f"(2) 解析解への収束  {G}レース × 3頭 / 特徴は x∈{{0,1,2}} の1列")
    L.append(f"    {'水準':<6}{'真の確率':>12}{'解析解(MLE)':>14}{'condlogit':>12}{'差':>10}")
    for i in range(3):
        L.append(f"    x={i:<4}{true_p[i]:>12.5f}{analytic[i]:>14.5f}"
                 f"{fit[i]:>12.5f}{fit[i] - analytic[i]:>+10.5f}")
    L.append(f"    Σp = {fit.sum():.10f}（1.0 であること）")
    L.append(f"    最大絶対誤差 = {err:.5f} (許容 5e-3) → {'OK' if err < 5e-3 else 'NG'}")
    L.append(f"    raw score 差 {dr.round(5).tolist()} vs 解析 log オッズ差 "
             f"{da.round(5).tolist()}（加法定数を除いて一致）")

    ok = res["fd_check"]["passed"] and res["analytic_mle_check"]["passed"]
    res["passed"] = bool(ok)
    L.append("")
    L.append(f"検算 総合判定: {'PASS' if ok else '🔴 FAIL'}")
    L.append("=" * 96)
    res["lines"] = L
    if verbose:
        print("\n".join(L))
    return res


# ---------------------------------------------------------------------------
# 🔴 目視確認: 実データを1レース表示する
# ---------------------------------------------------------------------------

def visual_check(df: pd.DataFrame, evs: dict[str, pd.DataFrame], arms: list[str]) -> list[str]:
    """全体の集計より先に、実データを1レース出して目で見る（CLAUDE.md の作法）。

    確認するのは3点:
      1. 3腕とも `Σp_win = 1.0`
      2. `condlogit` の raw score（= log p + 定数）の分布が妥当か
      3. `feat` 特徴（脚質・着順分散・勝率複勝率比・pace_handicap_pit）が入っているか
    """
    base = evs[arms[0]]
    cand = base[(base["place_slots"] == 3) & (base["n_runners"] >= 12)]
    rid = int(cand["race_id"].iloc[0]) if len(cand) else int(base["race_id"].iloc[0])
    src = df[df["race_id"] == rid].sort_values("horse_number")
    g0 = src.iloc[0]
    L = ["", "=" * 128,
         f"🔴 目視確認 race_id={rid} {g0['date']} {g0['course_name']}"
         f"{int(g0['race_number'])}R {g0['race_name']} n={len(src)} "
         f"place_slots={int(g0['place_slots'])}", "=" * 128]
    hdr = (f"{'馬番':>4}{'着':>4}{'馬名':<20}{'脚質':>9}{'着順分散':>10}"
           f"{'勝/複':>8}{'paceH':>8}")
    for a in arms:
        hdr += f"{'p_win:' + a:>16}"
    hdr += f"{'condlogit raw':>15}"
    L.append(hdr)

    def _n(v, w, d=2):
        return f"{'NaN':>{w}}" if v is None or (isinstance(v, float) and np.isnan(v)) \
            else f"{float(v):>{w}.{d}f}"

    pw = {a: evs[a].set_index(["race_id", "horse_number"])["p_win"] for a in arms}
    # condlogit の raw score は softmax の逆算（log p を平均0に中心化した形で示す）
    if "condlogit" in evs:
        cl = evs["condlogit"]
        cl_r = cl[cl["race_id"] == rid]
        lp = np.log(np.clip(cl_r["p_win"].to_numpy(dtype=float), 1e-12, None))
        raw = pd.Series(lp - lp.mean(), index=cl_r["horse_number"].to_numpy())
    else:
        raw = pd.Series(dtype=float)

    for _, r in src.iterrows():
        hn = int(r["horse_number"])
        row = (f"{hn:>4}{int(r['finish_position']):>4}{str(r['horse_name'])[:19]:<20}"
               f"{str(r['runner_type']):>9}{_n(r['finish_var5'], 10, 2)}"
               f"{_n(r['win_place_ratio5'], 8, 2)}{_n(r['pace_handicap_pit'], 8, 1)}")
        for a in arms:
            row += f"{_n(pw[a].get((rid, hn)), 16, 5)}"
        row += f"{_n(raw.get(hn), 15, 3)}"
        L.append(row)
    tot = f"{'Σ':>4}{'':>4}{'':<20}{'':>9}{'':>10}{'':>8}{'':>8}"
    for a in arms:
        tot += f"{evs[a][evs[a]['race_id'] == rid]['p_win'].sum():>16.5f}"
    L.append(tot + f"{'':>15}")
    L.append("（Σ行が3腕とも 1.00000 であること）")
    L.append("（condlogit raw = レース内で平均0に中心化した log p。softmax の入力そのもの。"
             "±3 程度に収まっていれば妥当・±20 のような値が出ていたら発散を疑う）")

    if "condlogit" in evs:
        r_all = evs["condlogit"]
        lg = np.log(np.clip(r_all["p_win"].to_numpy(dtype=float), 1e-12, None))
        cent = lg - pd.Series(lg).groupby(r_all["race_id"].to_numpy()).transform("mean").to_numpy()
        L.append(f"（全評価行の condlogit raw 分布: min={cent.min():.2f} "
                 f"p1={np.percentile(cent, 1):.2f} p50={np.percentile(cent, 50):.2f} "
                 f"p99={np.percentile(cent, 99):.2f} max={cent.max():.2f} "
                 f"sd={cent.std():.2f}）")
    return L


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------

def judge(arm_ev: pd.DataFrame, base_ev: pd.DataFrame, explore: list[str],
          confirm: list[str], n_boot: int) -> dict:
    """§12.4 の判定。探索6四半期で有意かつ 4/6 以上、確認4四半期で同符号。

    四半期別の対応差と bootstrap は `jra_winplace_feature_ab.quarter_paired` を
    そのまま使う（🔴 再実装しない）。`verdict` / 過半条件はそちらが 10四半期・6本
    前提なので、ここで窓ごとの基準に合わせて計算し直す。
    """
    ex = quarter_paired(arm_ev[arm_ev["quarter"].isin(explore)],
                        base_ev[base_ev["quarter"].isin(explore)], n_boot)
    cf = quarter_paired(arm_ev[arm_ev["quarter"].isin(confirm)],
                        base_ev[base_ev["quarter"].isin(confirm)], n_boot)
    al = quarter_paired(arm_ev, base_ev, n_boot)
    for d in (ex, cf, al):
        d.pop("verdict", None)
        d.pop("criterion_majority_quarters", None)

    need = (len(explore) + 1) // 2 + 1        # 6四半期 → 4
    ci_ok = ex["quarter_equal_weight"]["ci95"][1] < 0
    maj_ok = ex["n_improved_quarters"] >= need
    sign_ok = cf["quarter_equal_weight"]["delta"] < 0
    verdict = "採用候補" if (ci_ok and maj_ok and sign_ok) else "不採用"
    return {
        "explore": ex, "confirm": cf, "all_quarters": al,
        "criteria": {
            "explore_ci_excludes_zero_improving": bool(ci_ok),
            "explore_improved_quarters": ex["n_improved_quarters"],
            "explore_improved_required": need,
            "explore_majority_ok": bool(maj_ok),
            "confirm_same_sign_improving": bool(sign_ok),
        },
        "verdict": verdict,
        "market_gap_filled_pct_explore": round(
            -ex["quarter_equal_weight"]["delta"] / MARKET_GAP_NATS * 100, 3),
        "market_gap_filled_pct_all": round(
            -al["quarter_equal_weight"]["delta"] / MARKET_GAP_NATS * 100, 3),
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--quarters", default="2024Q1..2026Q2",
                   help="評価する四半期の範囲（既定 = §12.2 の10四半期）")
    p.add_argument("--explore-end", default="2025Q2",
                   help="探索窓の最終四半期（既定 = §12.2）。以降が確認窓")
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--bootstrap", type=int, default=2000)
    p.add_argument("--out", default=str(OUT_PATH))
    p.add_argument("--data-start", default="20230101")
    p.add_argument("--valid-days", type=int, default=90)
    p.add_argument("--cache", default=None, help="データセット pickle（冪等・再利用可）")
    p.add_argument("--pred-cache", default=None, help="腕ごとの評価行 pickle")
    p.add_argument("--arms", default=",".join(ARMS))
    p.add_argument("--binary-es", default="mnl", choices=["mnl", "binary_logloss"],
                   help="binary 腕の early stopping 指標（既定 mnl = 3腕共通・事前登録）")
    p.add_argument("--no-sensitivity", action="store_true",
                   help="binary 腕を Phase C と同じ binary_logloss で止めた感度を取らない")
    p.add_argument("--sens-cache", default=None, help="感度用ベースラインの pickle")
    p.add_argument("--self-test", action="store_true",
                   help="合成データでの検算だけ行って終了する（DB 不使用）")
    args = p.parse_args()

    # --- 🔴 学習の前に必ず検算する ---
    st = self_test()
    if not st["passed"]:
        raise SystemExit("🔴 condlogit の検算に失敗した。学習へ進まない")
    if args.self_test:
        return

    seeds = [int(s) for s in args.seeds.split(",")]
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    for a in arms:
        if a not in ARMS:
            raise SystemExit(f"未知の腕: {a}")
    if BASE_ARM not in arms:
        raise SystemExit(f"{BASE_ARM} は対応比較の基準なので必ず含める")

    qs = parse_quarters(args.quarters)
    qlabels = [q[0] for q in qs]
    eval_end = qs[-1][2]
    # 🔴 §12.2: TEST 窓（2026Q3 以降）を絶対に使わない
    if eval_end >= TEST_START:
        raise SystemExit(
            f"🔴 評価終端 {eval_end} が TEST_START={TEST_START} 以降。§12.2 により "
            f"2026Q3 は Phase D では使わない（一度きり評価の温存）")
    if args.explore_end not in qlabels:
        raise SystemExit(f"--explore-end {args.explore_end} が --quarters の範囲に無い")
    k = qlabels.index(args.explore_end) + 1
    explore, confirm = qlabels[:k], qlabels[k:]
    if not confirm:
        raise SystemExit("確認窓が空。--explore-end を見直すこと")
    logger.info("四半期 %d本 — 探索: %s ／ 確認: %s", len(qs),
                " ".join(explore), " ".join(confirm))

    cache = Path(args.cache) if args.cache else None
    if cache and cache.exists():
        df = pd.read_pickle(cache)
        logger.info("データセットをキャッシュから読込: %s", cache)
    else:
        df = build_dataset(args.data_start, eval_end)   # 🔴 Phase C と同一の実装を共有
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            df.to_pickle(cache)
            logger.info("データセットを保存: %s", cache)

    n_before = len(df)
    df = df[df["date"] < TEST_START]
    if len(df) != n_before:
        logger.info("TEST 窓(%s 以降) を %d行 除外", TEST_START, n_before - len(df))
    df = _sorted(df)
    logger.info("母集団: %d行 / %dレース (%s〜%s)", len(df), df["race_id"].nunique(),
                df["date"].min(), df["date"].max())

    # 同着1着（= 多項対数損失のレース単位1事象にならない）の実測
    nw = df.groupby("race_id")["finish_position"].apply(lambda s: int((s == 1).sum()))
    logger.info("勝ち馬が1頭でないレース: %d / %d (%.3f%%)",
                int((nw != 1).sum()), len(nw), float((nw != 1).mean() * 100))

    pred_cache = Path(args.pred_cache) if args.pred_cache else None
    if pred_cache and pred_cache.exists():
        evs = pd.read_pickle(pred_cache)
        logger.info("予測をキャッシュから読込: %s", pred_cache)
    else:
        evs = {}
        for arm in arms:
            logger.info("=== arm=%s (%d特徴・目的関数のみ差し替え) ===", arm, len(FEAT_NAMES))
            evs[arm] = run_arm(df, arm, qs, seeds, args.valid_days, args.binary_es)
        if pred_cache:
            pd.to_pickle(evs, pred_cache)

    # --- 🔴 目視確認（集計より先） ---
    vis = visual_check(df, evs, arms)
    print("\n".join(vis))

    base_ev = evs[BASE_ARM]
    print(f"\n評価対象: {base_ev['race_id'].nunique():,}レース / {len(base_ev):,}頭 "
          f"({base_ev['date'].min()}〜{base_ev['date'].max()})")
    print(f"腕: {' / '.join(arms)}（特徴は3腕とも feat {len(FEAT_NAMES)}列で固定）")
    print(f"探索: {' '.join(explore)} ／ 確認: {' '.join(confirm)}")

    results: dict = {}

    # --- 主指標 ---
    print("\n" + "=" * 120)
    print("  【主指標】レース単位 多項対数損失の binary との対応差（負＝改善）")
    print("=" * 120)
    others = [a for a in arms if a != BASE_ARM]
    jud = {a: judge(evs[a], base_ev, explore, confirm, args.bootstrap) for a in others}
    hdr = f"{'四半期':<9}{'窓':<5}{'nR':>7}{'binary_ll':>11}"
    for a in others:
        hdr += f"{a + '_ll':>13}{'Δ':>11}"
    print(hdr)
    print("-" * 120)
    for q in qlabels:
        win = "探索" if q in explore else "確認"
        src = jud[others[0]]["all_quarters"]["by_quarter"]
        row = next(r for r in src if r["quarter"] == q)
        line = f"{q:<9}{win:<5}{row['n_races']:>7}{row['base_logloss']:>11.4f}"
        for a in others:
            r = next(x for x in jud[a]["all_quarters"]["by_quarter"] if x["quarter"] == q)
            line += f"{r['arm_logloss']:>13.4f}{r['delta']:>+11.4f}"
        print(line)
    print("-" * 120)
    for a in others:
        v = jud[a]
        for tag, key in (("探索6Q", "explore"), ("確認4Q", "confirm"), ("全10Q", "all_quarters")):
            e = v[key]["quarter_equal_weight"]
            po = v[key]["race_pooled"]
            print(f"  [{a}] {tag} 等重みΔ={e['delta']:+.5f} "
                  f"95%CI=[{e['ci95'][0]:+.5f}, {e['ci95'][1]:+.5f}]  "
                  f"レース重みΔ={po['delta']:+.5f} [{po['ci95'][0]:+.5f}, {po['ci95'][1]:+.5f}] "
                  f"({po['n_races']}R) 改善{v[key]['n_improved_quarters']}/"
                  f"{v[key]['n_quarters']}")
        c = v["criteria"]
        print(f"  [{a}] 判定 → CI条件 {'○' if c['explore_ci_excludes_zero_improving'] else '×'}"
              f" / 改善{c['explore_improved_quarters']}/{len(explore)}"
              f"(必要{c['explore_improved_required']}) "
              f"{'○' if c['explore_majority_ok'] else '×'}"
              f" / 確認同符号 {'○' if c['confirm_same_sign_improving'] else '×'}"
              f" → **{v['verdict']}**"
              + ("   ※ lambdarank は参考。採否に使わない" if a == "lambdarank" else ""))
        print(f"  [{a}] 🔴 §9.2 の市場との差 {MARKET_GAP_NATS} nat のうち "
              f"探索 {v['market_gap_filled_pct_explore']:+.2f}% / "
              f"全10Q {v['market_gap_filled_pct_all']:+.2f}% を追加で埋めた"
              f"（Phase C の feat は {-PHASE_C_FEAT_DELTA / MARKET_GAP_NATS * 100:.1f}%）")
        print()
    results["primary"] = jud

    # --- 副指標 ---
    print("=" * 120)
    print("  【副指標】全期間プール（採否には使わない）")
    print("=" * 120)
    sec: dict = {}
    print(f"{'腕':<12}{'nR':>7}{'MNL logloss':>14}{'(SE)':>9}{'info gain':>12}{'gain%':>8}"
          f"{'top1勝率':>10}{'top1複勝率':>12}")
    for a in arms:
        w = win_scores(evs[a], "p_win")
        sec.setdefault(a, {})["win"] = {k2: v2 for k2, v2 in w.items()
                                        if not k2.startswith("_")}
        print(f"{a:<12}{w['n_races']:>7}{w['mnl_logloss']:>14.5f}{w['mnl_logloss_se']:>9.5f}"
              f"{w['info_gain_nats']:>12.5f}{w['info_gain_pct']:>8.2f}"
              f"{w['top1_win_rate']:>10.4f}{w['top1_place_rate']:>12.4f}")

    print(f"\n{'腕':<12}{'slots':>6}{'nR':>7}{'coverage@k':>13}{'place_ll':>11}"
          f"{'spearman':>11}{'交差R':>8}{'交差ペア':>10}{'同値ペア':>10}")
    for a in arms:
        for slots in (3, 2):
            s = evs[a][evs[a]["place_slots"] == slots]
            if not len(s):
                continue
            m = place_scores(s, "p_place", "p_win")
            sec.setdefault(a, {}).setdefault("place", {})[f"slots_{slots}"] = m
            sp = m["spearman_in_race"] if m["spearman_in_race"] is not None else float("nan")
            print(f"{a:<12}{slots:>6}{m['n_races']:>7}{m['coverage_at_k']:>13.4f}"
                  f"{m['place_logloss']:>11.5f}{sp:>11.4f}{m['cross_races']:>8}"
                  f"{m['cross_pairs']:>10}{m['tied_pairs']:>10}")
    print("（交差は Harville 経路のままなので全腕 0 のはず。0 でなければ実装バグを疑う）")

    # --- 較正の信頼性テーブル ---
    print("\n" + "=" * 120)
    print("  【較正】予測確率10分位 × 実測勝率（gap = 実測 − 予測。正＝過小評価）")
    print("=" * 120)
    print(f"{'分位':<5}" + "".join(f"{a + ' 予測/実測/差':>30}" for a in arms))
    for i in range(10):
        line = f"{i + 1:<5}"
        for a in arms:
            r = sec[a]["win"]["reliability"][i]
            line += f"{r['pred_pct']:>10.2f}%{r['actual_pct']:>9.2f}%{r['gap_pct']:>+9.2f}pt"
        print(line)
    results["secondary"] = sec

    # --- 🔴 感度: baseline を Phase C と同じ binary_logloss で止めた場合 ---
    if not args.no_sensitivity and args.binary_es == "mnl":
        print("\n" + "=" * 120)
        print("  【感度】binary 腕を Phase C と同じ binary_logloss で早期終了した場合")
        print("  （事前登録の主指標は上の mnl 早期終了のほう。ここは頑健性の確認）")
        print("=" * 120)
        sc = Path(args.sens_cache) if args.sens_cache else None
        if sc and sc.exists():
            base2 = pd.read_pickle(sc)
        else:
            base2 = run_arm(df, BASE_ARM, qs, seeds, args.valid_days, "binary_logloss")
            if sc:
                pd.to_pickle(base2, sc)
        sens = {a: judge(evs[a], base2, explore, confirm, args.bootstrap) for a in others}
        w2 = win_scores(base2, "p_win")
        print(f"  binary(binary_logloss 早期終了) 全期間 MNL logloss = {w2['mnl_logloss']:.5f} "
              f"／ mnl 早期終了 = {sec[BASE_ARM]['win']['mnl_logloss']:.5f} "
              f"（差 {w2['mnl_logloss'] - sec[BASE_ARM]['win']['mnl_logloss']:+.5f}）")
        for a in others:
            v = sens[a]
            for tag, key in (("探索6Q", "explore"), ("確認4Q", "confirm"),
                             ("全10Q", "all_quarters")):
                e = v[key]["quarter_equal_weight"]
                print(f"  [{a}] {tag} 等重みΔ={e['delta']:+.5f} "
                      f"95%CI=[{e['ci95'][0]:+.5f}, {e['ci95'][1]:+.5f}] "
                      f"改善{v[key]['n_improved_quarters']}/{v[key]['n_quarters']}")
            print(f"  [{a}] 判定（感度・参考）→ **{v['verdict']}**\n")
        results["sensitivity_binary_logloss_es"] = {
            "note": ("baseline の early stopping を Phase C と同じ binary_logloss にした場合。"
                     "🔴 事前登録の主指標は mnl 早期終了のほうであり、こちらは採否に使わない"),
            "binary_win": {k2: v2 for k2, v2 in w2.items() if not k2.startswith("_")},
            "judgement": sens,
        }

    # --- best_iteration ---
    iters = {a: sorted(set(evs[a]["best_iters"])) for a in arms}
    print("\n腕ごとの best_iteration（四半期×seed）:")
    for a in arms:
        print(f"  {a:<12} {iters[a]}")

    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "preregistration": "docs/jra_winplace_structure_plan_2026_09_04.md §12.2-12.4 (Phase D-2)",
        "quarters": [{"label": q[0], "start": q[1], "end": q[2]} for q in qs],
        "explore_quarters": explore,
        "confirm_quarters": confirm,
        "test_window_excluded": {"test_start": TEST_START,
                                 "note": "🔴 2026Q3 以降は Phase D では使わない（§12.2）"},
        "arms": {
            "binary": "objective=binary（現行）→ レース内 L1 正規化。ベースライン",
            "condlogit": ("レース内 softmax の多項対数損失をカスタム目的関数で実装。"
                          "grad=p−y / hess=max(p(1−p),1e-6) / group=race_id → レース内 softmax"),
            "lambdarank": ("objective=lambdarank（relevance と label_gain は "
                           "jra_rank_quality_review.train_lambdarank を踏襲）→ レース内 softmax。"
                           "🔴 参考のみ・採否には使わない"),
        },
        "features": {"set": "feat (Phase C の採用候補)", "n": len(FEAT_NAMES),
                     "names": FEAT_NAMES},
        "seeds": seeds,
        "bootstrap": args.bootstrap,
        "valid_days": args.valid_days,
        "max_round": MAX_ROUND,
        "params_shared": {k2: v2 for k2, v2 in PARAMS.items()
                          if k2 not in ("objective", "metric")},
        "early_stopping": {
            "mode": args.binary_es,
            "note": ("🔴 3腕とも early stopping はカスタム評価関数（レース単位の多項対数損失）"
                     "で行う。Phase C の feat 腕は binary_logloss で止めていたので、"
                     "binary 腕は Phase C と early stopping だけが異なる。結果を見る前に決めた"),
        },
        "n_rows": int(len(df)), "n_races": int(df["race_id"].nunique()),
        "data_period": [df["date"].min(), df["date"].max()],
        "eval_period": [base_ev["date"].min(), base_ev["date"].max()],
        "population": ("JRA のみ (races.course IN 01..10) / abnormality_code in (1,2) 除外 / "
                       "finish_position NULL・0 除外 / 障害含む。"
                       "3腕は完全に同一の行集合（jra_winplace_feature_ab.build_dataset を共有）"),
        "criteria": ("採用候補 = 探索6四半期の対応差（四半期等重み）の 95%CI が 0 を跨がず "
                     "改善側、かつ改善四半期 4/6 以上、かつ確認4四半期の平均差が同符号。"
                     "副指標は採否に使わない。lambdarank は参考のみ"),
        "market_gap_nats": MARKET_GAP_NATS,
        "phase_c_feat_delta": PHASE_C_FEAT_DELTA,
        "races_without_single_winner_pct": round(float((nw != 1).mean() * 100), 4),
        "self_test": {k2: v2 for k2, v2 in st.items() if k2 != "lines"},
        "self_test_lines": st["lines"],
        "visual_check": vis,
        "best_iterations": iters,
        "results": results,
    }
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print(f"\n保存: {outp}")


if __name__ == "__main__":
    main()
