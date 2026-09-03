"""JRA 単勝・複勝構造の **2026Q3 一度きり最終確認**（事前登録 §15 の実装）

事前登録: `docs/jra_winplace_structure_plan_2026_09_04.md` §15。
**本スクリプトは事前登録の仕様を実装するだけで、腕を増やさず・閾値を動かさない。**

🔴 **これは `TEST_START=20260701` の一度きり評価を消費する。**
実行後に `jra_protocol.record_test_usage()` を呼び `scripts/JRA_TEST_USAGE_LEDGER.md`
へ1行追記する（同じ行が既にあれば追記しない＝冪等）。

## これは「選択」ではなく「確認」（§15.1）

採用候補の選択は Phase C（10四半期）と Phase D-1（探索6+確認4四半期）で終わっている。
2026Q3 で問うのは **「別の窓でも同じ方向に出るか」だけ**。

## 腕（§15.2・2腕のみ）

| 腕 | 内容 |
|---|---|
| `prod` | 現行34特徴・`fillna(50.0)` → is_win → L1正規化 → `_harville_place_probs`（本番の再現） |
| `new`  | `feat` 特徴 → is_win → L1正規化（単勝） / 独立ヘッド → `Σp = place_slots`（複勝） |

- 特徴・母集団は `jra_winplace_feature_ab.build_dataset` / `ARMS` を import して
  Phase C/D と完全に同一（🔴 再実装しない）
- 学習は **`jra_protocol.TRAIN_DATA_END`（2026-06-30）まで**で打ち切る
- 評価は **2026-07-01 〜 データのある最終日**

### 🔴 §13.1 罠1 の修正: `place_slots` ごとにラベルを変える

Phase D-1 は事前登録の字義どおり独立ヘッドのラベルを一律 `finish_position <= 3` に
していたため、5〜7頭立て（`place_slots=2`）で「3着以内ヘッドを2着以内で採点」していた。
本スクリプトは §15.2 のとおり **ラベルを `finish_position <= place_slots`** にする
（＝本番実装で採る形）。`place_slots=0`（5頭未満・払戻対象着順が無い）の行は
ラベルが定義できないので**独立ヘッドの学習からだけ外す**（件数を JSON に記録する）。
🔴 **判定は `place_slots=3` のみ**（§15.3）。

## 判定基準（§15.3・動かさない）

| 判定 | 条件 |
|---|---|
| **確認成功** | 単勝の多項対数損失・複勝の `place_ll` の**両方**で `new` の点推定が `prod` より改善側 |
| **確認失敗** | どちらかが**有意に悪化**（95%CI が 0 を跨がず悪化側） |
| **判断保留** | 上記のいずれでもない |

🔴 **95%CI は必ず併記するが、判定には使わない。** 2026Q3 は 500〜600R しかなく
検出力が無いため、有意性を要求すると効果が実在しても不採用になる（§15.3）。

## 副指標（報告必須・判定に使わない・§15.4）

- 🔴 **交差件数**（単勝順位 vs 複勝順位）。`prod` は 0 のはず。`new` が 0 なら実装バグを疑う。
  数え方は `jra_prob_scoring.place_scores`（生値を `CROSS_TOL=1e-9` 許容で比較）を import
- 🔴 **層別の複勝残差**（着順分散 T1/T3・勝率複勝率比 T1）。
  `jra_place_head_ab.residual_by_level` を import して共有
- top1勝率 / top1複勝率 / `coverage@3` / `place_slots` 別内訳
- **市場との差**（§9.2 の +0.16363 nat）を 2026Q3 で再計算し `new` が何%埋めたか。
  🔴 2026Q3 の**発走前オッズ充足率を実測して併記**する

## 分位のカット点（§15.5）

🔴 **Phase D-1 の JSON（`docs/model_verification/jra_place_head_ab.json` の `cutoffs`）
から読み込んでそのまま使う。2026Q3 で切り直さない。**

## 使い方

    cd backend
    .venv/bin/python scripts/jra_winplace_final_confirm.py \
        --out ../docs/model_verification/jra_winplace_final_confirm.json

冪等。`--cache` / `--pred-cache` に pickle を指定すると再実行が速い。
台帳追記は同じ行が既にあればスキップする。`--no-record-usage` で抑止できる（試走用）。
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

# --- 既存の測定基盤／本番コードを import する（🔴 独立実装をしない） -----------------
import scripts.jra_place_head_ab as PH  # noqa: E402
from scripts.jra_place_head_ab import (  # noqa: E402
    CUT_NAMES,
    EPS,
    quarter_paired_place,
    residual_by_level,
)
from scripts.jra_prob_scoring import (  # noqa: E402
    JRA_COURSES,
    PRERACE_ODDS_SQL,
    _connect,
    _query,
    attach_market,
    harville_place,
    paired_logloss_ci,
    place_scores,
    race_normalize,
    win_scores,
)
from scripts.jra_winplace_feature_ab import (  # noqa: E402
    ARMS as FEATURE_ARMS,
)
from scripts.jra_winplace_feature_ab import (  # noqa: E402
    build_dataset,
    fit_predict,
)
from src import jra_protocol  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("final_confirm")

OUT_PATH = _root.parent / "docs" / "model_verification" / "jra_winplace_final_confirm.json"
PHASE_D_JSON = _root.parent / "docs" / "model_verification" / "jra_place_head_ab.json"

ARMS = ("prod", "new")
BASE_ARM = "prod"
# 腕ごとの特徴（§15.2）: prod = Phase C の `base` 腕 / new = Phase C の `feat` 腕
ARM_FEATURES = {"prod": FEATURE_ARMS["base"], "new": FEATURE_ARMS["feat"]}

WIN_COL = {a: f"p_win__{a}" for a in ARMS}
PLACE_COL = {a: f"p_place__{a}" for a in ARMS}

# 🔴 `jra_place_head_ab` の共有関数（`residual_by_level` / `quarter_paired_place`）は
# モジュール大域の `PLACE_COL` / `BASE_ARM` を見る。再実装せず共有するために差し替える。
PH.PLACE_COL = PLACE_COL
PH.BASE_ARM = BASE_ARM

# §9.2（Phase B）で確認窓に測ったモデル−市場の差。2026Q3 の再計算値と並べて報告する
MARKET_GAP_PHASE_B_NATS = 0.16363

# §15.4 の「層別の複勝残差」で必ず見る層（§13.1 と同じ3層）
KEY_LEVELS = [
    ("finish_var_tertile", "T1_low"),
    ("finish_var_tertile", "T3_high"),
    ("win_place_ratio_tertile", "T1_low"),
]


# ---------------------------------------------------------------------------
# 独立ヘッドのラベル（🔴 §15.2 / §13.1 罠1: place_slots ごとに変える）
# ---------------------------------------------------------------------------

def relabel_is_placed(d: pd.DataFrame) -> pd.DataFrame:
    """独立ヘッドを **is_win ヘッドと同一の学習手順**で学習するための写像。

    `jra_winplace_feature_ab.fit_predict` は label を `finish_position == 1` で作る
    （is_win 用に固定）。同じ関数を書き写さずに再利用するため、`finish_position` を

        finish_position <= place_slots → 1（label=1）/ それ以外 → 99（label=0）

    と写像した**コピー**を渡す。PARAMS / MAX_ROUND / seeds / early stopping は共有される。

    🔴 Phase D-1 は一律 `<= 3` だった（§13.1 罠1）。ここは事前登録 §15.2 のとおり
    `place_slots` に合わせる。`place_slots == 0`（5頭未満）は払戻対象着順が無く
    ラベルが定義できないので**この写像では落とす**（呼び出し側で件数を記録する）。
    """
    fp = pd.to_numeric(d["finish_position"], errors="coerce")
    slots = pd.to_numeric(d["place_slots"], errors="coerce")
    out = d[slots > 0].copy()
    fp, slots = fp[slots > 0], slots[slots > 0]
    out["finish_position"] = np.where(fp <= slots, 1, 99)
    return out


def normalize_to_slots(d: pd.DataFrame, raw_col: str) -> tuple[np.ndarray, int]:
    """独立ヘッドの生出力をレース内で `Σp = place_slots` に正規化する。

    🔴 `jra_place_head_ab._top3_norm` と同一の式。あちらは列名 `p_top3_raw` に
    固定されているので、列名だけ差し替えられる形で持ってきた（式は変えていない）。
    正規化後に 1 を超える馬はクリップし、その頭数を返す（Σ はその分だけ崩れる）。
    """
    raw = d[raw_col].to_numpy(dtype=float)
    s = pd.Series(raw, index=d.index)
    tot = s.groupby(d["race_id"]).transform("sum").to_numpy()
    slots = d["place_slots"].to_numpy(dtype=float)
    scaled = np.where(tot > 0, raw * slots / np.maximum(tot, EPS), raw)
    n_clip = int((scaled > 1.0 - EPS).sum())
    return np.clip(scaled, EPS, 1.0 - EPS), n_clip


# ---------------------------------------------------------------------------
# 学習（1窓のみ・walk-forward の1ステップ）
# ---------------------------------------------------------------------------

def fit_arms(train: pd.DataFrame, te: pd.DataFrame, seeds: list[int],
             valid_days: int, eval_start: str) -> tuple[pd.DataFrame, dict]:
    """`prod` / `new` の2腕を同一の train/valid 分割で学習し、評価行に確率を付ける。"""
    cut = (pd.to_datetime(eval_start) - pd.Timedelta(days=valid_days)).strftime("%Y%m%d")
    tr, va = train[train["date"] <= cut], train[train["date"] > cut]
    if len(va) < 2000:
        i = int(len(train) * 0.8)
        tr, va = train.iloc[:i], train.iloc[i:]
    te = te.reset_index(drop=True).copy()
    info: dict = {"train_rows": int(len(tr)), "valid_rows": int(len(va)),
                  "valid_cut": cut,
                  "train_date_min": str(train["date"].min()),
                  "train_date_max": str(train["date"].max())}

    for arm in ARMS:
        names, cols = ARM_FEATURES[arm]["names"], ARM_FEATURES[arm]["cols"]
        t0 = time.time()
        raw_w, it_w = fit_predict(tr, va, te, names, cols, seeds)
        te[WIN_COL[arm]] = race_normalize(raw_w, te["race_id"])
        info[f"best_iters_win__{arm}"] = it_w
        logger.info("  [%s] is_win ヘッド: %d特徴 iters=%s (%.1fs)",
                    arm, len(names), it_w, time.time() - t0)

    # --- prod の複勝: 本番 `_harville_place_probs` ---
    te[PLACE_COL["prod"]] = harville_place(te, WIN_COL["prod"])

    # --- new の複勝: 独立ヘッド（🔴 ラベルは place_slots ごと）→ Σ=place_slots ---
    names, cols = ARM_FEATURES["new"]["names"], ARM_FEATURES["new"]["cols"]
    tr_p, va_p = relabel_is_placed(tr), relabel_is_placed(va)
    info["is_placed_head_dropped_rows"] = {
        "train": int(len(tr) - len(tr_p)), "valid": int(len(va) - len(va_p)),
        "reason": "place_slots=0（5頭未満）はラベルが定義できないため独立ヘッドの学習から除外",
    }
    t0 = time.time()
    raw_p, it_p = fit_predict(tr_p, va_p, te, names, cols, seeds)
    te["p_placed_raw"] = np.clip(raw_p, EPS, 1.0 - EPS)
    info["best_iters_place__new"] = it_p
    logger.info("  [new] 独立 is_placed ヘッド: iters=%s (%.1fs)", it_p, time.time() - t0)

    norm, n_clip = normalize_to_slots(te, "p_placed_raw")
    te[PLACE_COL["new"]] = norm
    info["new_place_clipped_horses"] = n_clip
    return te, info


# ---------------------------------------------------------------------------
# 🔴 目視確認: 実データを1レース表示する（CLAUDE.md「baseline は1件表示して目視」）
# ---------------------------------------------------------------------------

def visual_check(ev: pd.DataFrame) -> list[str]:
    """全体を回す前に必ず目で見る。確認するのは3点（事前登録の実施内容）:

      1. 両腕とも `Σp_win = 1.0`
      2. `prod` の `Σp_place = place_slots`
      3. `new`  の `Σp = place_slots`
    """
    d = ev[ev["place_slots"] == 3]
    ok = d.groupby("race_id").agg(
        named=("race_name", lambda s: s.notna().all()),
        frac5=("finish_var5", lambda s: float(s.notna().mean())),
    )
    cand = ok[ok["named"] & (ok["frac5"] >= 0.5)]
    rid = int(cand.index[0]) if len(cand) else int(d["race_id"].iloc[0])
    g = ev[ev["race_id"] == rid].sort_values("horse_number")
    r0 = g.iloc[0]
    L = ["", "=" * 130]
    L.append(f"🔴 目視確認 race_id={rid} {r0['date']} {r0['course_name']}"
             f"{int(r0['race_number'])}R {r0['race_name']} "
             f"n={len(g)} place_slots={int(r0['place_slots'])}")
    L.append("=" * 130)
    L.append(f"{'馬番':>4}{'着':>4}{'馬名':<20}{'脚質':>9}{'着順分散':>10}{'勝/複':>8}"
             f"{'p_win prod':>12}{'p_win new':>12}{'raw_placed':>12}"
             f"{'p_place prod':>14}{'p_place new':>13}")

    def _n(v, w, dg=5):
        return f"{'NaN':>{w}}" if v is None or (isinstance(v, float) and np.isnan(v)) \
            else f"{float(v):>{w}.{dg}f}"

    for _, r in g.iterrows():
        L.append(f"{int(r['horse_number']):>4}{int(r['finish_position']):>4}"
                 f"{str(r['horse_name'])[:19]:<20}{str(r['runner_type']):>9}"
                 f"{_n(r['finish_var5'], 10, 2)}{_n(r['win_place_ratio5'], 8, 2)}"
                 f"{_n(r[WIN_COL['prod']], 12)}{_n(r[WIN_COL['new']], 12)}"
                 f"{_n(r['p_placed_raw'], 12)}"
                 f"{_n(r[PLACE_COL['prod']], 14)}{_n(r[PLACE_COL['new']], 13)}")
    L.append(f"{'Σ':>4}{'':>4}{'':<20}{'':>9}{'':>10}{'':>8}"
             f"{g[WIN_COL['prod']].sum():>12.5f}{g[WIN_COL['new']].sum():>12.5f}"
             f"{g['p_placed_raw'].sum():>12.5f}"
             f"{g[PLACE_COL['prod']].sum():>14.5f}{g[PLACE_COL['new']].sum():>13.5f}")
    L.append(f"（期待: Σp_win=1.00000（両腕）/ p_place prod と p_place new の Σ="
             f"{int(r0['place_slots'])}.00000 / raw_placed だけはずれてよい）")
    return L


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--eval-start", default=jra_protocol.TEST_START,
                   help="評価窓の開始（既定 = jra_protocol.TEST_START）")
    p.add_argument("--eval-end", default=None,
                   help="評価窓の終了。既定はデータのある最終日を自動検出")
    p.add_argument("--train-end", default=jra_protocol.TRAIN_DATA_END,
                   help="学習の終端（既定 = jra_protocol.TRAIN_DATA_END）")
    p.add_argument("--data-start", default="20230101", help="学習データの開始日")
    p.add_argument("--seeds", default="42,123,456")
    p.add_argument("--bootstrap", type=int, default=2000)
    p.add_argument("--valid-days", type=int, default=90,
                   help="early stopping 用に train の末尾から取る日数")
    p.add_argument("--max-lead-min", type=float, default=60.0,
                   help="発走前オッズとして採用する最大リードタイム（分）")
    p.add_argument("--out", default=str(OUT_PATH))
    p.add_argument("--cache", default=None, help="データセット pickle（冪等・再利用可）")
    p.add_argument("--pred-cache", default=None, help="予測 pickle")
    p.add_argument("--no-record-usage", action="store_true",
                   help="🔴 台帳への追記を抑止する（試走用。本番実行では付けない）")
    args = p.parse_args()

    seeds = [int(s) for s in args.seeds.split(",")]

    # --- 🔴 プロトコルの整合を実行前に検査する ---
    #
    # 本番モード: `--eval-start` が TEST_START と一致（事前登録 §15 の窓）。台帳へ追記する。
    # 空回しモード: `--eval-start` が TEST_START より**前**（＝ VAL 窓）。実装の検算用に
    #   だけ使い、**台帳へ追記せず・正式な出力先へも書かない**。TEST を消費しない。
    # TEST_START より後ろの窓は事前登録に無いので受け付けない。
    smoke = args.eval_start < jra_protocol.TEST_START
    if not smoke and args.eval_start != jra_protocol.TEST_START:
        raise SystemExit(f"事前登録 §15 は TEST_START={jra_protocol.TEST_START} の窓を指定して"
                         f"いる（現在 {args.eval_start}）。窓を変えるなら事前登録から書き直すこと")
    if args.train_end >= args.eval_start:
        raise SystemExit(f"学習終端 {args.train_end} が評価開始 {args.eval_start} 以降。"
                         "評価窓を学習に混ぜてはいけない")
    logger.info("プロトコル: %s", jra_protocol.describe())
    if smoke:
        args.no_record_usage = True
        if Path(args.out).resolve() == OUT_PATH.resolve():
            raise SystemExit("空回しモード（VAL 窓）では正式な出力先へ書かない。--out を変えること")
        logger.warning("⚠️ 空回しモード: 評価窓 %s〜 は VAL。実装の検算専用で "
                       "TEST を消費せず、台帳にも追記しない", args.eval_start)
    else:
        logger.info("🔴 これは一度きり評価の消費: 学習 ≤%s / 評価 %s〜",
                    args.train_end, args.eval_start)

    # --- データのある最終日を自動検出 ---
    eval_end = args.eval_end
    if eval_end is None:
        conn = _connect()
        q = _query(conn, """
            SELECT max(r.date) AS d
            FROM keiba.races r
            JOIN keiba.race_results rr ON rr.race_id = r.id
            WHERE r.course IN %(courses)s AND rr.finish_position > 0
        """, {"courses": JRA_COURSES})
        conn.close()
        eval_end = str(q["d"].iloc[0])
        logger.info("データのある最終日を自動検出: %s", eval_end)

    cache = Path(args.cache) if args.cache else None
    if cache and cache.exists():
        df = pd.read_pickle(cache)
        logger.info("データセットをキャッシュから読込: %s", cache)
    else:
        df = build_dataset(args.data_start, eval_end)
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            df.to_pickle(cache)

    for arm in ARMS:
        miss = [c for c in ARM_FEATURES[arm]["cols"] if c not in df.columns]
        if miss:
            raise SystemExit(f"データセットに {arm} の特徴が足りない: {miss}")

    train = df[df["date"] <= args.train_end]
    te0 = df[(df["date"] >= args.eval_start) & (df["date"] <= eval_end)]
    if train.empty or te0.empty:
        raise SystemExit(f"train={len(train)} eval={len(te0)}: 窓の指定を確認すること")
    logger.info("母集団: 学習 %d行/%dR (%s〜%s) / 評価 %d行/%dR (%s〜%s)",
                len(train), train["race_id"].nunique(), train["date"].min(),
                train["date"].max(), len(te0), te0["race_id"].nunique(),
                te0["date"].min(), te0["date"].max())

    pred_cache = Path(args.pred_cache) if args.pred_cache else None
    if pred_cache and pred_cache.exists():
        ev = pd.read_pickle(pred_cache)
        fit_info = json.loads(Path(str(pred_cache) + ".info.json").read_text())
        logger.info("予測をキャッシュから読込: %s", pred_cache)
    else:
        logger.info("=== 2腕を学習（prod: 34特徴 / new: feat 特徴 + 独立ヘッド）===")
        ev, fit_info = fit_arms(train, te0, seeds, args.valid_days, args.eval_start)
        if pred_cache:
            pd.to_pickle(ev, pred_cache)
            Path(str(pred_cache) + ".info.json").write_text(
                json.dumps(fit_info, ensure_ascii=False, indent=2))
    ev = ev.reset_index(drop=True)
    ev["quarter"] = "2026Q3"

    # --- 🔴 目視確認（集計より先に出す）---
    vis = visual_check(ev)
    print("\n".join(vis))

    # --- 実装の自己検査 ---
    checks: dict = {}
    d3 = ev[ev["place_slots"] == 3]
    sums = ev.groupby("race_id")[[WIN_COL[a] for a in ARMS]
                                 + [PLACE_COL[a] for a in ARMS] + ["place_slots"]].agg(
        {**{WIN_COL[a]: "sum" for a in ARMS}, **{PLACE_COL[a]: "sum" for a in ARMS},
         "place_slots": "first"})
    for a in ARMS:
        checks[f"max_abs_dev_p_win_sum_from_1__{a}"] = round(
            float((sums[WIN_COL[a]] - 1.0).abs().max()), 9)
        checks[f"max_abs_dev_p_place_sum_from_slots__{a}"] = round(
            float((sums[PLACE_COL[a]] - sums["place_slots"]).abs().max()), 6)
    checks["new_place_clipped_horses"] = fit_info["new_place_clipped_horses"]
    checks["is_placed_head_dropped_rows"] = fit_info["is_placed_head_dropped_rows"]
    # 母集団が2腕で完全に同一であること（腕で行が変わってはいけない）
    checks["same_population_rows"] = int(len(ev))
    checks["same_population_races"] = int(ev["race_id"].nunique())
    print("\n【実装の自己検査】")
    for k, v in checks.items():
        print(f"  {k}: {v}")

    print(f"\n評価窓: {ev['race_id'].nunique():,}R / {len(ev):,}頭 "
          f"({ev['date'].min()}〜{ev['date'].max()}) ｜ 判定母集団 place_slots=3: "
          f"{d3['race_id'].nunique():,}R / {len(d3):,}頭")

    results: dict = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "preregistration": "docs/jra_winplace_structure_plan_2026_09_04.md §15",
        "protocol": jra_protocol.describe(),
        "config": {
            "arms": list(ARMS), "baseline": BASE_ARM,
            "arm_features": {a: len(ARM_FEATURES[a]["names"]) for a in ARMS},
            "train_end": args.train_end,
            "eval_start": args.eval_start, "eval_end": eval_end,
            "data_start": args.data_start, "seeds": seeds,
            "bootstrap": args.bootstrap, "valid_days": args.valid_days,
            "max_lead_min": args.max_lead_min,
            "primary_win": "レース単位 多項対数損失（jra_prob_scoring.win_scores）",
            "primary_place": "place_ll（is_placed 二値対数損失・place_slots=3）",
            "criterion": "確認成功=両方で new の点推定が改善側 / 確認失敗=どちらかが"
                         "有意に悪化（95%CI が 0 を跨がず悪化側）/ それ以外=判断保留",
            "ci_note": "🔴 95%CI は併記のみ。2026Q3 は検出力が無いため判定に使わない（§15.3）",
            "place_label": "finish_position <= place_slots（🔴 §13.1 罠1 の修正）",
        },
        "population": {
            "train": {"n_rows": int(len(train)), "n_races": int(train["race_id"].nunique()),
                      "date_min": str(train["date"].min()), "date_max": str(train["date"].max())},
            "eval": {"n_rows": int(len(ev)), "n_races": int(ev["race_id"].nunique()),
                     "date_min": str(ev["date"].min()), "date_max": str(ev["date"].max())},
            "eval_slots3": {"n_rows": int(len(d3)), "n_races": int(d3["race_id"].nunique())},
        },
        "fit_info": fit_info,
        "self_checks": checks,
        "visual_check": vis,
    }

    # ------------------------------------------------------------------
    # 主指標 (1): 単勝の多項対数損失
    # ------------------------------------------------------------------
    print("\n" + "=" * 120)
    print("  【主指標1】単勝 レース単位 多項対数損失（負の Δ ＝ new が改善）")
    print("=" * 120)
    ws = {a: win_scores(ev, WIN_COL[a]) for a in ARMS}
    win_delta = paired_logloss_ci(ws["new"], ws[BASE_ARM], args.bootstrap)
    print(f"{'腕':<8}{'nR':>7}{'MNL logloss':>14}{'(SE)':>9}{'uniform':>10}"
          f"{'info gain':>12}{'gain%':>9}{'top1勝率':>11}{'top1複勝率':>12}")
    for a in ARMS:
        m = ws[a]
        print(f"{a:<8}{m['n_races']:>7}{m['mnl_logloss']:>14.5f}{m['mnl_logloss_se']:>9.5f}"
              f"{m['uniform_logloss']:>10.5f}{m['info_gain_nats']:>12.5f}"
              f"{m['info_gain_pct']:>9.3f}{m['top1_win_rate']:>11.4f}"
              f"{m['top1_place_rate']:>12.4f}")
    print(f"\n  new − prod  Δ={win_delta['delta_logloss']:+.5f} "
          f"95%CI=[{win_delta['ci95'][0]:+.5f}, {win_delta['ci95'][1]:+.5f}] "
          f"(n={win_delta['n_races']}R)")
    results["primary_win"] = {
        "by_arm": {a: {k: v for k, v in ws[a].items() if not k.startswith("_")} for a in ARMS},
        "new_minus_prod": win_delta,
        "point_estimate_improves": bool(win_delta["delta_logloss"] < 0),
        "significantly_worse": bool(win_delta["ci95"][0] > 0),
    }

    # ------------------------------------------------------------------
    # 主指標 (2): 複勝の place_ll（place_slots=3）
    # ------------------------------------------------------------------
    print("\n" + "=" * 120)
    print("  【主指標2】複勝 place_ll（place_slots=3・負の Δ ＝ new が改善）")
    print("=" * 120)
    # 🔴 `jra_place_head_ab.quarter_paired_place` を import して共有（再実装しない）。
    #    四半期は 2026Q3 の1本だけなので等重み平均＝レース重み平均になる。
    pl = quarter_paired_place(ev, "new", args.bootstrap, min_improved=1)
    r0 = pl["by_quarter"][0]
    print(f"  prod place_ll={r0['base_place_ll']:.5f} / new place_ll={r0['arm_place_ll']:.5f} "
          f"(n={r0['n_races']}R)")
    po = pl["race_pooled"]
    print(f"  new − prod  Δ={po['delta']:+.5f} "
          f"95%CI=[{po['ci95'][0]:+.5f}, {po['ci95'][1]:+.5f}] (n={po['n_races']}R)")
    results["primary_place"] = {
        "detail": pl,
        "point_estimate_improves": bool(po["delta"] < 0),
        "significantly_worse": bool(po["ci95"][0] > 0),
    }

    # ------------------------------------------------------------------
    # 🔴 判定（§15.3・動かさない）
    # ------------------------------------------------------------------
    win_imp = results["primary_win"]["point_estimate_improves"]
    pl_imp = results["primary_place"]["point_estimate_improves"]
    win_bad = results["primary_win"]["significantly_worse"]
    pl_bad = results["primary_place"]["significantly_worse"]
    if win_bad or pl_bad:
        verdict = "確認失敗"
    elif win_imp and pl_imp:
        verdict = "確認成功"
    else:
        verdict = "判断保留"
    results["verdict"] = verdict
    results["verdict_inputs"] = {
        "win_point_improves": win_imp, "place_point_improves": pl_imp,
        "win_significantly_worse": win_bad, "place_significantly_worse": pl_bad,
    }
    print("\n" + "=" * 120)
    print(f"  【判定（§15.3）】**{verdict}**"
          f"  ｜ 単勝 点推定改善={'○' if win_imp else '×'}"
          f" / 複勝 点推定改善={'○' if pl_imp else '×'}"
          f" / 有意悪化={'あり' if (win_bad or pl_bad) else 'なし'}")
    print("=" * 120)

    # ------------------------------------------------------------------
    # 副指標 (a): 複勝側の指標と交差件数（place_slots 別）
    # ------------------------------------------------------------------
    print("\n" + "=" * 120)
    print("  【副指標】複勝側の指標と交差件数（採否には使わない）")
    print("  🔴 prod は 0 のはず。new が 0 なら独立ヘッドが効いていない＝実装バグを疑う")
    print("=" * 120)
    sec: dict = {}
    print(f"{'腕':<8}{'slots':>6}{'nR':>7}{'n頭':>7}{'place_ll':>11}{'coverage@k':>12}"
          f"{'spearman':>11}{'交差R':>8}{'交差ペア':>10}{'同値ペア':>10}")
    for a in ARMS:
        for slots in (3, 2):
            s = ev[ev["place_slots"] == slots]
            if not len(s):
                continue
            m = place_scores(s, PLACE_COL[a], WIN_COL[a])
            sec.setdefault(a, {})[f"slots_{slots}"] = m
            sp = m["spearman_in_race"]
            print(f"{a:<8}{slots:>6}{m['n_races']:>7}{m['n_horses']:>7}"
                  f"{m['place_logloss']:>11.5f}{m['coverage_at_k']:>12.4f}"
                  f"{(f'{sp:.4f}' if sp is not None else '-'):>11}"
                  f"{m['cross_races']:>8}{m['cross_pairs']:>10}{m['tied_pairs']:>10}")
    results["secondary_place"] = sec

    # ------------------------------------------------------------------
    # 副指標 (b): 層別の複勝残差（🔴 カット点は Phase D-1 から凍結して持ってくる）
    # ------------------------------------------------------------------
    if not PHASE_D_JSON.exists():
        raise SystemExit(f"Phase D-1 の JSON が無い（カット点を凍結できない）: {PHASE_D_JSON}")
    cutoffs = json.loads(PHASE_D_JSON.read_text())["cutoffs"]
    results["cutoffs"] = {"source": str(PHASE_D_JSON.relative_to(_root.parent)),
                          "note": "🔴 §15.5: Phase C/D の探索窓で凍結したものをそのまま使う"
                                  "（2026Q3 で切り直さない）",
                          "values": cutoffs}
    print("\n" + "=" * 120)
    print("  【副指標】層別の複勝残差 1[複勝圏] − p_place（p_win 10分位で調整・pt・0 に近いほど良い）")
    print(f"  🔴 カット点は Phase D-1 の探索窓から凍結（{PHASE_D_JSON.name}）。2026Q3 で切り直していない")
    print("=" * 120)
    resid: dict = {}
    for a in ARMS:
        # `residual_by_level` は `assign_levels` 経由で `p_win` 列を見る（10分位の調整）。
        # 腕ごとの p_win を使う（カット点そのものは凍結値で共通）。
        frame = ev.copy()
        frame["p_win"] = frame[WIN_COL[a]]
        resid[a] = residual_by_level(frame, a, cutoffs, args.bootstrap)
    results["residual_by_level"] = resid

    for cut in CUT_NAMES:
        print(f"\n  ── {cut} ──")
        print(f"{'水準':<12}{'n':>8}{'prod':>26}{'new':>26}")
        for lv in resid[BASE_ARM]["cuts"][cut]:
            line = f"{lv:<12}{resid[BASE_ARM]['cuts'][cut][lv]['n']:>8}"
            for a in ARMS:
                r = resid[a]["cuts"][cut].get(lv)
                line += (f"{r['place_residual_pt']:>+12.2f}"
                         f"[{r['ci95_pt'][0]:+6.2f},{r['ci95_pt'][1]:+6.2f}]") if r \
                    else f"{'-':>26}"
            print(line)
    for a in ARMS:
        print(f"  （{a} 全体の複勝残差: {resid[a]['overall_place_residual_pt']:+.4f} pt"
              f" / n={resid[a]['n_horses']}頭 {resid[a]['n_races']}R）")

    shrink = []
    for cut, lv in KEY_LEVELS:
        b = resid[BASE_ARM]["cuts"][cut][lv]["place_residual_pt"]
        n = resid["new"]["cuts"][cut][lv]["place_residual_pt"]
        shrink.append({
            "cut": cut, "level": lv, "prod_pt": b, "new_pt": n,
            "prod_ci95_pt": resid[BASE_ARM]["cuts"][cut][lv]["ci95_pt"],
            "new_ci95_pt": resid["new"]["cuts"][cut][lv]["ci95_pt"],
            "abs_shrink_pct": round((abs(b) - abs(n)) / abs(b) * 100, 1) if abs(b) > 1e-9 else None,
            "new_ci_crosses_zero": bool(resid["new"]["cuts"][cut][lv]["ci95_pt"][0] <= 0
                                        <= resid["new"]["cuts"][cut][lv]["ci95_pt"][1]),
        })
    results["key_level_shrink"] = shrink
    print("\n  【§15.4 の必須3層】着順分散 T1/T3・勝率複勝率比 T1")
    print(f"{'層':<32}{'prod':>12}{'new':>12}{'縮小%':>9}{'new CI が 0 を跨ぐ':>20}")
    for r in shrink:
        print(f"{r['cut'] + '/' + r['level']:<32}{r['prod_pt']:>+12.2f}{r['new_pt']:>+12.2f}"
              f"{(r['abs_shrink_pct'] if r['abs_shrink_pct'] is not None else float('nan')):>9.1f}"
              f"{('○' if r['new_ci_crosses_zero'] else '×'):>20}")

    # ------------------------------------------------------------------
    # 副指標 (c): 市場との差（🔴 発走前オッズ充足率を実測して併記）
    # ------------------------------------------------------------------
    print("\n" + "=" * 120)
    print("  【副指標】市場との差（§9.2 の +0.16363 nat を 2026Q3 で再計算）")
    print("=" * 120)
    conn = _connect()
    od = _query(conn, PRERACE_ODDS_SQL,
                {"courses": JRA_COURSES, "start": args.eval_start, "end": eval_end,
                 "max_lead": args.max_lead_min})
    conn.close()
    mk: dict = {"max_lead_min": args.max_lead_min}
    evm = ev.copy()
    evm["horse_number"] = pd.to_numeric(evm["horse_number"], errors="coerce")
    if len(od):
        od["horse_number"] = pd.to_numeric(od["combination"], errors="coerce")
        od["pre_odds"] = pd.to_numeric(od["odds"], errors="coerce")
        od = od.dropna(subset=["horse_number"])
        # 同じスナップショットに同一馬番が2行入っていると merge で行が増える。落としてから結合する
        od = od.drop_duplicates(subset=["race_id", "horse_number"], keep="first")
        lead = od.groupby("race_id")["lead_min"].first().astype(float)
        n0 = len(evm)
        evm = evm.merge(od[["race_id", "horse_number", "pre_odds"]],
                        on=["race_id", "horse_number"], how="left")
        if len(evm) != n0:
            raise SystemExit(f"オッズ結合で行数が変わった（{n0} → {len(evm)}）。母集団が壊れる")
    else:
        evm["pre_odds"] = np.nan
        lead = pd.Series(dtype=float)
    evm = attach_market(evm, "pre_odds", "p_mkt_pre")
    pre_races = set(evm.loc[evm["p_mkt_pre"].notna(), "race_id"].unique())
    all_races = set(evm["race_id"].unique())
    mk["n_races_eval"] = len(all_races)
    mk["n_races_with_prerace_odds"] = len(pre_races)
    mk["coverage_pct"] = round(100 * len(pre_races) / max(1, len(all_races)), 2)
    mk["lead_min_median"] = round(float(lead.median()), 2) if len(lead) else None
    mk["lead_min_p90"] = round(float(lead.quantile(0.9)), 2) if len(lead) else None
    # 月別の充足率（内訳）
    evm["_ym"] = evm["date"].str[:6]
    mk["monthly_coverage"] = [
        {"ym": ym, "n_races": int(g["race_id"].nunique()),
         "n_prerace_odds": int(g.loc[g["p_mkt_pre"].notna(), "race_id"].nunique()),
         "coverage_pct": round(100 * g.loc[g["p_mkt_pre"].notna(), "race_id"].nunique()
                               / max(1, g["race_id"].nunique()), 2)}
        for ym, g in evm.groupby("_ym")]
    print(f"  発走前オッズ充足率（max_lead={args.max_lead_min:.0f}分・全馬そろったレース）: "
          f"{len(pre_races)} / {len(all_races)}R = {mk['coverage_pct']:.2f}%"
          f"  ｜ リード中央値 {mk['lead_min_median']}分 / p90 {mk['lead_min_p90']}分")
    for m in mk["monthly_coverage"]:
        print(f"    {m['ym']}: {m['n_prerace_odds']}/{m['n_races']}R = {m['coverage_pct']:.2f}%")

    if pre_races:
        sub = evm[evm["race_id"].isin(pre_races)]
        ms = {a: win_scores(sub, WIN_COL[a]) for a in ARMS}
        ms["market_prerace"] = win_scores(sub, "p_mkt_pre")
        gap_prod = paired_logloss_ci(ms[BASE_ARM], ms["market_prerace"], args.bootstrap)
        gap_new = paired_logloss_ci(ms["new"], ms["market_prerace"], args.bootstrap)
        sub_delta = paired_logloss_ci(ms["new"], ms[BASE_ARM], args.bootstrap)
        filled = (round(-sub_delta["delta_logloss"] / gap_prod["delta_logloss"] * 100, 2)
                  if gap_prod.get("delta_logloss") not in (None, 0) else None)
        mk.update({
            "subset_note": "🔴 発走前オッズが全馬分そろったレースだけの部分集合",
            "by_series": {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
                          for k, v in ms.items()},
            "prod_minus_market": gap_prod,
            "new_minus_market": gap_new,
            "new_minus_prod_on_subset": sub_delta,
            "phase_b_gap_nats": MARKET_GAP_PHASE_B_NATS,
            "pct_of_2026Q3_gap_filled_by_new": filled,
        })
        print(f"\n{'系列':<16}{'nR':>7}{'MNL logloss':>14}{'info gain':>12}{'top1勝率':>11}")
        for k in (BASE_ARM, "new", "market_prerace"):
            m = ms[k]
            print(f"{k:<16}{m['n_races']:>7}{m['mnl_logloss']:>14.5f}"
                  f"{m['info_gain_nats']:>12.5f}{m['top1_win_rate']:>11.4f}")
        print(f"\n  prod − 市場 Δ={gap_prod['delta_logloss']:+.5f} "
              f"[{gap_prod['ci95'][0]:+.5f}, {gap_prod['ci95'][1]:+.5f}]"
              f"  （§9.2 の確認窓は +{MARKET_GAP_PHASE_B_NATS:.5f}）")
        print(f"  new  − 市場 Δ={gap_new['delta_logloss']:+.5f} "
              f"[{gap_new['ci95'][0]:+.5f}, {gap_new['ci95'][1]:+.5f}]")
        print(f"  new  − prod（市場subset）Δ={sub_delta['delta_logloss']:+.5f} "
              f"[{sub_delta['ci95'][0]:+.5f}, {sub_delta['ci95'][1]:+.5f}]")
        print(f"  🔴 new が 2026Q3 の市場差を埋めた割合: {filled}%")
    else:
        mk["subset_note"] = "発走前オッズが1件も取れなかった"
        print("  発走前オッズが1件も取れなかった。市場比較は出せない")
    results["market"] = mk

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 🔴 TEST 使用の記録（台帳へ1行追記・冪等）
    # ------------------------------------------------------------------
    if args.no_record_usage:
        print("\n⚠️ --no-record-usage 指定のため台帳へ追記しなかった（試走扱い）")
        return
    ledger = Path(jra_protocol._LEDGER_PATH)
    script = f"scripts/{_here.name}"
    if ledger.exists() and script in ledger.read_text():
        print(f"\n台帳に {script} の行が既にある。追記しない（冪等）")
        return
    decision = ("単勝確率の構造見直し（`feat` 特徴）と複勝の独立ヘッド化（Σ=place_slots）の "
                "2026Q3 最終確認（事前登録 §15）")
    note = (f"prod（現行34特徴→Harville）vs new（feat特徴 + 独立 is_placed ヘッド）を "
            f"{ev['race_id'].nunique()}R / {len(ev)}頭（{ev['date'].min()}〜{ev['date'].max()}）で比較。"
            f"単勝 多項対数損失 Δ={win_delta['delta_logloss']:+.5f} "
            f"[{win_delta['ci95'][0]:+.5f}, {win_delta['ci95'][1]:+.5f}] / "
            f"複勝 place_ll(slots=3) Δ={po['delta']:+.5f} "
            f"[{po['ci95'][0]:+.5f}, {po['ci95'][1]:+.5f}]。**{verdict}**")
    jra_protocol.record_test_usage(decision, script, note)
    print(f"\n🔴 台帳へ追記した: {ledger}")
    print(f"   - {pd.Timestamp.today().date()} `TEST_START={jra_protocol.TEST_START}` "
          f"**{script}**: {decision} — {note}")


if __name__ == "__main__":
    main()
