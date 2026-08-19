"""最終三連複オッズの予測モデルを学習する（7車 / 9車・2026-08-11 新設）。

    python scripts/train_odds_prediction.py --n-car 7 --train-end 2025-12-31
    python scripts/train_odds_prediction.py --n-car 9 --train-end 2025-12-31
    python scripts/train_odds_prediction.py --n-car 7 --eval-only   # 学習せず評価だけ

出力（`data/models/`・git 管理外）:
    odds_trio_n7.txt / odds_trio_n9.txt   LightGBM モデル
    odds_trio_meta.json                   目標総和・保守倍率・特徴量名・学習窓

## 🔴 特徴量は `src.odds_prediction.build_race_features` を呼ぶ

学習側で作り直してはいけない。本番と別実装にすると train/serve skew が静かに入り、
**入稿は成功するので気づけない**。順序違いも検知しにくいので、特徴量名の一覧を
meta へ記録し、推論側の `load_meta()` が起動時に照合する。

## p3 / pw は honest walk-forward の予測を使う

`wt_entries.pred_{top3,win}_pct` は**過去分が backfill されており look-ahead**
（[[keirin_gami_race_gate_rejected_2026_08_08]]）。学習に使うと、実際より賢い p3 を
前提とした関係を学んでしまう。そこで学習は `data/exp_cache/` の walk-forward 予測を
第一ソースにする（[[keirin_7a_line_priority_rejected_2026_08_09]] と同じ方針）。

推論時は live の `wt_entries.pred_*` を使うが、両者は相関 0.975/0.980・平均差ほぼ0・
同スケールで系統差が無く、推論経路の特徴量で測り直しても
logMAE 0.1368 → 0.1416 / ±2倍以内 91.5% → 90.6% と実用上保たれることを確認済み。

## 目標総和（整合化の定数）と保守倍率は**学習窓から決める**

検証窓の数字をそのまま定数化してはいけない
（[[keirin_step3_dutch_7a_gate_impl_2026_08_09]] / [[keirin_7h2_production_wiring_2026_08_10]]）。

⚠️ walk-forward 予測キャッシュの再生成は
   7車 `scripts/exp_7car_gap_fresh.py` / 9車 `scripts/gen_wf_preds_9car.py`。
"""
from __future__ import annotations

import argparse
import glob
import itertools
import json
import logging
import os
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.odds_prediction import (  # noqa: E402
    FEATURE_NAMES,
    META_PATH,
    MODEL_DIR,
    SUPPORTED_N_CAR,
    build_race_features,
)

log = logging.getLogger("train_odds")
EXP = REPO / "data" / "exp_cache"
SEP_TRANS = str.maketrans({"=": "-"})
ODDS_SENTINEL = 9000.0     # これ以上は「板が立っていない」扱い（朝の板は 9999.9）


# ---------------------------------------------------------------------------
def _connect():
    import psycopg2

    url = os.environ.get("KEIRIN_DB_URL")
    if not url:
        raise SystemExit("KEIRIN_DB_URL が未設定です")
    return psycopg2.connect(url, connect_timeout=60)


def _load_entries(race_keys: list[str]) -> dict[str, dict[int, dict]]:
    """wt_entries から推論時と同じ列を引く（meta の作り方を揃えるため）。"""
    sql = """
    SELECT race_key, frame_no, race_point, prediction_mark, player_class, style,
           line_group, line_size, line_pos, is_line_leader,
           first_rate, second_rate, third_rate
    FROM keirin.wt_entries WHERE race_key = ANY(%s)
    """
    with _connect() as conn:
        df = pd.read_sql(sql, conn, params=(race_keys,))
    df["race_point"] = pd.to_numeric(df.race_point, errors="coerce")
    out: dict[str, dict[int, dict]] = {}
    for rk, g in df.groupby("race_key"):
        out[rk] = {
            int(r.frame_no): {
                "race_point": r.race_point, "mark": r.prediction_mark,
                "player_class": r.player_class, "style": r.style,
                "line_group": r.line_group, "line_size": r.line_size,
                "line_pos": r.line_pos, "is_line_leader": r.is_line_leader,
                "first_rate": r.first_rate, "second_rate": r.second_rate,
                "third_rate": r.third_rate,
            }
            for r in g.itertuples(index=False)
        }
    return out


def _load_final_boards(race_keys: list[str]) -> dict[str, dict[frozenset, float]]:
    """確定三連複オッズ。

    🔴 `wt_odds.combination` は `1=2=3`、`wt_odds_snapshot` は `1-2-3` と
       **区切り文字が違う**。正規化しないと大半のレースが静かに落ちる
       （9車で 4,591R → 230R になり「最近のデータしか無い」と誤診した）。
    """
    sql = """
    SELECT DISTINCT ON (race_key, combination) race_key, combination, odds_value
    FROM keirin.wt_odds
    WHERE bet_type = 'trio' AND race_key = ANY(%s)
    ORDER BY race_key, combination, collected_at DESC
    """
    with _connect() as conn:
        df = pd.read_sql(sql, conn, params=(race_keys,))
    df["odds_value"] = pd.to_numeric(df.odds_value, errors="coerce")
    df = df[(df.odds_value > 0) & (df.odds_value < ODDS_SENTINEL)]
    out: dict[str, dict[frozenset, float]] = {}
    for rk, g in df.groupby("race_key"):
        board = {}
        for comb, od in zip(g.combination, g.odds_value):
            try:
                key = frozenset(int(x) for x in str(comb).translate(SEP_TRANS).split("-"))
            except ValueError:
                continue
            if len(key) == 3:
                board[key] = float(od)
        out[rk] = board
    return out


def _load_wf_preds(n_car: int) -> dict[str, tuple[dict, dict, str]]:
    """{race_key: (p3, pw, date)} を walk-forward キャッシュから読む。"""
    preds: dict[str, tuple[dict, dict, str]] = {}
    if n_car == 7:
        path = EXP / "axis_detail_7car.pkl"
        if not path.exists():
            raise SystemExit(f"{path} がありません（scripts/exp_7car_gap_fresh.py で生成）")
        for r in pickle.load(open(path, "rb")):
            if len(r.get("p3") or {}) == 7:
                preds[r["rk"]] = (r["p3"], r["pw"], r["date"])
    else:
        files = sorted(glob.glob(str(EXP / "wf_preds9_*.pkl")))
        if not files:
            raise SystemExit("wf_preds9_*.pkl がありません（scripts/gen_wf_preds_9car.py で生成）")
        df = pd.concat([pd.read_pickle(f) for f in files], ignore_index=True)
        df = df.drop_duplicates(subset=["race_key", "frame_no"])
        for rk, g in df.groupby("race_key"):
            if len(g) != 9:
                continue
            d8 = rk[:8]
            preds[rk] = (dict(zip(g.frame_no, g.pp3)), dict(zip(g.frame_no, g.ppw)),
                         f"{d8[:4]}-{d8[4:6]}-{d8[6:8]}")
    return preds


# ---------------------------------------------------------------------------
def build_dataset(n_car: int) -> pd.DataFrame:
    preds = _load_wf_preds(n_car)
    keys = sorted(preds)
    log.info("walk-forward 予測: %s車 %d レース", n_car, len(keys))
    entries = _load_entries(keys)
    boards = _load_final_boards(keys)
    n_combo = len(list(itertools.combinations(range(n_car), 3)))

    frames, skipped = [], {"entries": 0, "board": 0, "features": 0}
    for rk in keys:
        p3, pw, date = preds[rk]
        meta = entries.get(rk)
        board = boards.get(rk)
        if not meta or len(meta) != n_car:
            skipped["entries"] += 1
            continue
        if not board or len(board) != n_combo:
            skipped["board"] += 1
            continue
        try:
            combos, X = build_race_features(sorted(p3), p3, pw, meta)
        except Exception:
            skipped["features"] += 1
            continue
        y = np.array([board[c] for c in combos], dtype=float)
        df = pd.DataFrame(X, columns=list(FEATURE_NAMES))
        df["rk"] = rk
        df["date"] = date
        df["odds"] = y
        frames.append(df)
    log.info("採用 %d レース / 除外 %s", len(frames), skipped)
    if not frames:
        raise SystemExit("学習データが空です")
    return pd.concat(frames, ignore_index=True)


def _report(tag: str, y: np.ndarray, pred: np.ndarray) -> dict:
    err = np.log10(pred) - np.log10(y)
    ratio = 10 ** err
    down = y / pred
    m = dict(
        n=int(len(y)),
        logmae=float(np.abs(err).mean()),
        within15=float(((ratio >= 1 / 1.5) & (ratio <= 1.5)).mean() * 100),
        within2=float(((ratio >= 0.5) & (ratio <= 2.0)).mean() * 100),
        median_ratio=float(np.median(ratio)),
        down_08=float((down < 0.8).mean() * 100),
        down_05=float((down < 0.5).mean() * 100),
    )
    print(f"  [{tag}] n={m['n']:,}  logMAE {m['logmae']:.4f}  "
          f"±2倍 {m['within2']:.1f}%  中央比 {m['median_ratio']:.3f}  "
          f"下振れ<0.8倍 {m['down_08']:.2f}%  <0.5倍 {m['down_05']:.2f}%")
    return m


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-car", type=int, required=True, choices=list(SUPPORTED_N_CAR))
    ap.add_argument("--train-end", default="2025-12-31",
                    help="この日以前を学習に使う（以降は評価）")
    ap.add_argument("--rounds", type=int, default=700)
    ap.add_argument("--eval-only", action="store_true")
    # 🔴 **配分に効くのはレース内の相対値だけ**（`landing_weights` は 1/オッズ に
    #    比例した重みを正規化して使う）。しかも推論の整合化が Σ(1/o) を定数へ
    #    再スケールするので、**モデルが当てた「水準」は捨てられて再付与される**。
    #    つまり level 学習は水準にモデル容量を使っており、その分だけ相対値が甘い。
    #    centered は目的関数をレース内中心化した log10(オッズ) に替えて、
    #    最初から相対値だけを学習させる。**推論側の変更は不要**（整合化が水準を
    #    決めるため）。2026-08-19 検証。
    ap.add_argument("--target-mode", choices=("level", "centered"), default="level")
    # 本番モデルを上書きせずに比較するための退避名。
    ap.add_argument("--save-suffix", default="",
                    help="モデル名の接尾辞（例: _centered2512）。空なら本番名を上書き")
    args = ap.parse_args()

    import lightgbm as lgb

    cache = EXP / f"odds_trio_dataset_n{args.n_car}.pkl"
    if cache.exists():
        d = pd.read_pickle(cache)
        log.info("データセットをキャッシュから読みました: %s", cache)
    else:
        d = build_dataset(args.n_car)
        cache.parent.mkdir(parents=True, exist_ok=True)
        d.to_pickle(cache)
    d["y"] = np.log10(d.odds)
    if args.target_mode == "centered":
        # レース内で中心化する。水準は推論の整合化が決めるので学習しない。
        d["y"] = d["y"] - d.groupby("rk")["y"].transform("mean")
    tr = d[d.date <= args.train_end]
    te = d[d.date > args.train_end]
    print(f"\n{args.n_car}車  学習 {tr.rk.nunique():,}R / {len(tr):,}目"
          f"  評価 {te.rk.nunique():,}R / {len(te):,}目"
          f"  目的={args.target_mode}  保存先={args.n_car}{args.save_suffix}")
    if tr.empty:
        raise SystemExit("学習窓が空です")

    model_path = MODEL_DIR / f"odds_trio_n{args.n_car}{args.save_suffix}.txt"
    if args.eval_only:
        booster = lgb.Booster(model_file=str(model_path))
    else:
        params = dict(objective="regression", metric="l1", learning_rate=0.05,
                      num_leaves=127 if args.n_car == 7 else 63,
                      min_data_in_leaf=200 if args.n_car == 7 else 100,
                      feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
                      verbose=-1, num_threads=8)
        booster = lgb.train(params, lgb.Dataset(tr[list(FEATURE_NAMES)], tr.y),
                            num_boost_round=args.rounds)
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        tmp = model_path.with_suffix(".txt.tmp")
        booster.save_model(str(tmp))
        tmp.replace(model_path)          # アトミックに差し替える
        print(f"  モデルを保存: {model_path}")

    # ── 目標総和と保守倍率は【学習窓】から決める ──────────────────
    target_sum = float(tr.groupby("rk").odds.apply(lambda s: (1 / s).sum()).mean())
    tr_pred = 10 ** booster.predict(tr[list(FEATURE_NAMES)])
    tr_scale = (pd.Series(1 / tr_pred).groupby(tr.rk.values).transform("sum")
                / target_sum).to_numpy()
    tr_coh = tr_pred * tr_scale
    ratio = tr.odds.to_numpy() / tr_coh
    conservative = {f"p{int(q*100):02d}": float(np.quantile(ratio, q))
                    for q in (0.05, 0.10, 0.25)}
    print(f"\n  目標総和（学習窓の Σ1/o 平均）= {target_sum:.4f}"
          f"  → 含意払戻率 {1/target_sum:.4f}")
    print("  保守倍率（学習窓の 実際/整合板 の下側分位）= "
          + "  ".join(f"{k}:{v:.4f}" for k, v in conservative.items()))

    print("\n=== 精度 ===")
    stats = {}
    for tag, part in (("学習窓", tr), ("評価窓", te)):
        if part.empty:
            continue
        p = 10 ** booster.predict(part[list(FEATURE_NAMES)])
        scale = (pd.Series(1 / p).groupby(part.rk.values).transform("sum")
                 / target_sum).to_numpy()
        stats[tag] = {"raw": _report(f"{tag}・素の点予測", part.odds.to_numpy(), p),
                      "coherent": _report(f"{tag}・整合板", part.odds.to_numpy(), p * scale)}

    if args.save_suffix:
        print("\n  ※ --save-suffix 指定のため meta は更新しません（本番と混ぜない）")
    if not args.eval_only and not args.save_suffix:
        meta = {}
        if META_PATH.exists():
            meta = json.loads(META_PATH.read_text(encoding="utf-8"))
        meta["feature_names"] = list(FEATURE_NAMES)
        meta.setdefault("per_n_car", {})[str(args.n_car)] = {
            "target_sum": target_sum,
            "conservative": conservative,
            "train_end": args.train_end,
            "n_train_races": int(tr.rk.nunique()),
            "rounds": args.rounds,
            "stats": stats,
        }
        META_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = META_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(META_PATH)
        print(f"\n  メタを保存: {META_PATH}")


if __name__ == "__main__":
    main()
