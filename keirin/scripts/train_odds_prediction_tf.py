"""最終**三連単**オッズの予測モデルを学習する（7車・2026-08-12 新設）。

    PYTHONPATH=. .venv/bin/python scripts/train_odds_prediction_tf.py --train-end 2025-12-31

出力（`data/models/`・git 管理外）:
    odds_tf_n7.txt      LightGBM モデル
    odds_tf_meta.json   目標総和・特徴量名・学習窓

`scripts/train_odds_prediction.py`（三連複）の三連単版。方針はあちらと同じで、
**まずあちらの docstring を読むこと**。ここには三連単固有の点だけを書く。

## 🔴 特徴量は `src.odds_prediction_tf.build_race_features` を呼ぶ

学習側で作り直さない。特徴量名の一覧を meta へ記録し、推論側の `load_meta()` が
起動時に照合する（train/serve skew は入稿が成功するので気づけない）。

## 🔴 p3 / pw は walk-forward 予測を使う

`wt_entries.pred_*` は過去分が backfill されており学習に使うと look-ahead。
`data/exp_cache/axis_detail_7car.pkl` を使う。

## データ量に注意

1レース 210行（三連複は35行）。walk-forward の7車 48,541レースを全部使うと
約1,020万行になる。既定では `--max-races` で間引く（行数がメモリを食うだけで
精度には効かないため）。
"""
from __future__ import annotations

import argparse
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

from src.odds_prediction_tf import (  # noqa: E402
    FEATURE_NAMES, MODEL_DIR, META_PATH, build_race_features, conservative_quantiles,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("train_odds_tf")
EXP = REPO / "data" / "exp_cache"
# 🔴 **打ち切り（表示上限）の位置は車数で違う。** winticket の三連単オッズは
#    9999.9 が表示上限で、そこに張り付いた点は真の値が分からない（右側打ち切り）。
#    7車は中央値が低く 9000 以上がほとんど出ないので 9000 で切っても実害が無いが、
#    **9車は中央値 1,650倍・p90 8,891倍で、8.2% が上限に張り付く**。
#    9車に 9000 を当てると「504点そろっていない」で**レースごと落ちる**
#    （実測 5,698R 中 4,834R が除外され、学習が 339R まで痩せた）。
#    7車の既存の挙動は変えないため車数別に持つ。
ODDS_SENTINEL_BY_CAR = {7: 9000.0, 9: 9999.0}
ODDS_SENTINEL = 9000.0     # 後方互換（7車の既定）

#: 板の充足率がこれを下回るレースは捨てる。**完全一致を要求しない**
#: （打ち切り点を落とすと必ず 504 未満になるため）。
MIN_BOARD_RATIO = 0.85
#: 既定は 7車。`--n-car 9` で 9車モデル（`odds_tf_n9.txt`）を学習する。
#: 🔴 **モデルもメタも車数ごとに分かれている。** メタは丸ごと上書きせず**マージ**する
#:    （上書きすると `target_sum["7"]` が消えて本番の三連単予測オッズが全滅する）。
N_CAR = 7
N_COMBO = 210


def _connect():
    import psycopg2
    url = os.environ.get("KEIRIN_DB_URL")
    if not url:
        raise SystemExit("KEIRIN_DB_URL が未設定です")
    return psycopg2.connect(url, connect_timeout=60)


def _load_entries(race_keys: list[str]) -> dict[str, dict[int, dict]]:
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


def _load_final_boards(race_keys: list[str]) -> dict[str, dict[tuple, float]]:
    """確定三連単オッズ。三連単の `combination` は `1-2-3`（着順つき）。"""
    sql = """
    SELECT DISTINCT ON (race_key, combination) race_key, combination, odds_value
    FROM keirin.wt_odds
    WHERE bet_type = 'trifecta' AND race_key = ANY(%s)
    ORDER BY race_key, combination, collected_at DESC
    """
    with _connect() as conn:
        df = pd.read_sql(sql, conn, params=(race_keys,))
    df["odds_value"] = pd.to_numeric(df.odds_value, errors="coerce")
    cap = ODDS_SENTINEL_BY_CAR.get(N_CAR, ODDS_SENTINEL)
    df = df[(df.odds_value > 0) & (df.odds_value < cap)]
    out: dict[str, dict[tuple, float]] = {}
    for rk, g in df.groupby("race_key"):
        board = {}
        for comb, od in zip(g.combination, g.odds_value):
            try:
                key = tuple(int(x) for x in str(comb).split("-"))
            except ValueError:
                continue
            if len(key) == 3 and len(set(key)) == 3:
                board[key] = float(od)
        out[rk] = board
    return out


def _load_wf_preds() -> dict[str, tuple[dict, dict, str]]:
    """walk-forward の p3/pw。**本番モデルを過去へ当てない**ための第一ソース。

    7車: `axis_detail_7car.pkl`（レースごとの dict）
    9車: `wf_preds9_*.pkl`（race_key/frame_no/pp3/ppw の DataFrame・
         `scripts/exp_type_lab/carcount6.py` と同じ作り方で別途生成したもの）
    """
    if N_CAR == 7:
        path = EXP / "axis_detail_7car.pkl"
        if not path.exists():
            raise SystemExit(f"{path} がありません（scripts/exp_7car_gap_fresh.py で生成）")
        preds = {}
        for r in pickle.load(open(path, "rb")):
            if len(r.get("p3") or {}) == N_CAR and len(r.get("pw") or {}) == N_CAR:
                preds[r["rk"]] = (r["p3"], r["pw"], r["date"])
        return preds

    import glob as _g
    files = sorted(_g.glob(str(EXP / f"wf_preds{N_CAR}_*.pkl")))
    if not files:
        raise SystemExit(
            f"{EXP}/wf_preds{N_CAR}_*.pkl がありません。"
            f"{N_CAR}車の walk-forward 予測を先に作ってください")
    df = pd.concat([pd.read_pickle(f) for f in files], ignore_index=True)
    p3: dict[str, dict] = {}
    pw: dict[str, dict] = {}
    for rk, fn, a, b in zip(df["race_key"], df["frame_no"], df["pp3"], df["ppw"]):
        rk = str(rk)
        p3.setdefault(rk, {})[int(fn)] = float(a)
        pw.setdefault(rk, {})[int(fn)] = float(b)
    out = {}
    for rk in p3:
        if len(p3[rk]) == N_CAR and len(pw.get(rk) or {}) == N_CAR:
            d = rk[:8]
            out[rk] = (p3[rk], pw[rk], f"{d[:4]}-{d[4:6]}-{d[6:]}")
    return out


def build_dataset(max_races: int | None) -> pd.DataFrame:
    preds = _load_wf_preds()
    keys = sorted(preds)
    if max_races and len(keys) > max_races:
        # 期間を偏らせないよう等間隔で間引く（先頭から切ると古い年だけになる）
        idx = np.linspace(0, len(keys) - 1, max_races).astype(int)
        keys = [keys[i] for i in sorted(set(idx))]
    log.info("walk-forward 予測: %d レース（%d点/レース）", len(keys), N_COMBO)

    frames, skipped = [], {"entries": 0, "board": 0, "features": 0}
    CH = 800
    for i in range(0, len(keys), CH):
        ch = keys[i:i + CH]
        entries = _load_entries(ch)
        boards = _load_final_boards(ch)
        for rk in ch:
            p3, pw, date = preds[rk]
            meta, board = entries.get(rk), boards.get(rk)
            if not meta or len(meta) != N_CAR:
                skipped["entries"] += 1
                continue
            if not board or len(board) < N_COMBO * MIN_BOARD_RATIO:
                skipped["board"] += 1
                continue
            try:
                combos, X = build_race_features(sorted(p3), p3, pw, meta)
            except Exception:
                skipped["features"] += 1
                continue
            # 打ち切り点（表示上限に張り付いた点）は board に無い。**その点だけ落とす**
            # ——レースごと捨てると 9車の学習が痩せる。Σ1/o への影響は
            # 1/9999.9 ≈ 0.0001 × 約41点 = 0.004 で目標総和 1.33 に対し無視できる。
            keep = [i for i, c in enumerate(combos) if c in board]
            if len(keep) < N_COMBO * MIN_BOARD_RATIO:
                skipped["board"] += 1
                continue
            df = pd.DataFrame(X[keep], columns=list(FEATURE_NAMES))
            df["rk"] = rk
            df["date"] = date
            df["odds"] = [board[combos[i]] for i in keep]
            frames.append(df)
        log.info("  %d/%d レース 採用%d", min(i + CH, len(keys)), len(keys), len(frames))
    log.info("採用 %d レース / 除外 %s", len(frames), skipped)
    if not frames:
        raise SystemExit("学習データが空です")
    return pd.concat(frames, ignore_index=True)


def _report(tag: str, y: np.ndarray, pred: np.ndarray) -> dict:
    err = np.log10(pred) - np.log10(y)
    ratio = 10 ** err
    out = {
        "n": int(len(y)),
        "logMAE": float(np.abs(err).mean()),
        "within2x": float(((ratio >= 0.5) & (ratio <= 2.0)).mean()),
        "under08": float((ratio < 0.8).mean()),
        "under05": float((ratio < 0.5).mean()),
    }
    log.info("[%s] n=%d logMAE=%.4f ±2倍以内=%.1f%% <0.8倍=%.1f%% <0.5倍=%.1f%%",
             tag, out["n"], out["logMAE"], 100 * out["within2x"],
             100 * out["under08"], 100 * out["under05"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-end", default="2025-12-31",
                    help="この日までを学習に使う（以降は honest な検証窓）")
    ap.add_argument("--n-car", type=int, default=7, choices=(7, 9),
                    help="学習する車数（既定7）。9 は odds_tf_n9.txt を作る")
    ap.add_argument("--rounds", type=int, default=600)
    ap.add_argument("--max-races", type=int, default=12000)
    args = ap.parse_args()
    global N_CAR, N_COMBO
    N_CAR = int(args.n_car)
    N_COMBO = N_CAR * (N_CAR - 1) * (N_CAR - 2)
    log.info("車数 %d（組み合わせ %d通り）", N_CAR, N_COMBO)

    import lightgbm as lgb

    df = build_dataset(args.max_races)
    df["y"] = np.log10(df.odds)
    tr = df[df.date <= args.train_end]
    te = df[df.date > args.train_end]
    log.info("学習 %d行（%d R）/ 検証 %d行（%d R）",
             len(tr), tr.rk.nunique(), len(te), te.rk.nunique())
    if te.empty:
        raise SystemExit("検証窓が空です（--train-end を見直すこと）")

    params = dict(objective="regression", metric="l1", learning_rate=0.05,
                  num_leaves=63, min_data_in_leaf=200, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, verbose=-1)
    booster = lgb.train(params, lgb.Dataset(tr[list(FEATURE_NAMES)], tr.y),
                        num_boost_round=args.rounds)

    # 目標総和は**学習窓の実測**から決める（検証窓の数字を定数化しない）
    target_sum = float(tr.groupby("rk").odds.apply(lambda s: (1 / s).sum()).mean())
    log.info("目標総和（学習窓の Σ1/o 平均）= %.4f → 含意払戻率 %.4f",
             target_sum, 1 / target_sum)

    res = {}
    conservative = None
    for tag, part in (("train", tr), ("test", te)):
        raw = np.clip(np.power(10.0, booster.predict(part[list(FEATURE_NAMES)])), 1.0, None)
        p = pd.DataFrame({"rk": part.rk.to_numpy(), "raw": raw, "odds": part.odds.to_numpy()})
        res[tag + "_raw"] = _report(tag + "/素", p.odds.to_numpy(), p.raw.to_numpy())
        # レース内で再スケールして板として整合させる
        scale = p.groupby("rk").raw.transform(lambda s: (1 / s).sum() / target_sum)
        coherent = (p.raw * scale).to_numpy()
        res[tag] = _report(tag + "/整合", p.odds.to_numpy(), coherent)
        if tag == "train":
            # 保守倍率（下側分位）。三連複と同じ定義・同じ窓（学習窓）で作る。
            # 🔴 これが無いと `_conservative_board` が三連単へ**三連複の倍率**を
            #    掛ける状態へ戻る（2026-08-29 まで実際にそうなっていた）。
            conservative = conservative_quantiles(p.odds.to_numpy(), coherent)
            log.info("保守倍率（学習窓の 実際/整合板 の下側分位）= %s",
                     "  ".join(f"{k}:{v:.4f}" for k, v in conservative.items()))

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(MODEL_DIR / f"odds_tf_n{N_CAR}.txt"))
    # 🔴 **丸ごと上書きしない。** 上書きすると他の車数の `target_sum` が消え、
    #    本番の `target_sum(7)` が例外になって三連単の予測オッズが全滅する。
    prev = {}
    if META_PATH.exists():
        try:
            prev = json.loads(META_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:  # noqa: BLE001
            log.warning("既存メタを読めませんでした（新規で書きます）: %r", e)
    tgt = dict(prev.get("target_sum") or {})
    tgt[str(N_CAR)] = target_sum
    per = dict(prev.get("per_n_car") or {})
    # 旧形式（per_n_car を持たない）を車数別へ畳み直す
    if not per and prev.get("train_end"):
        for k in prev.get("target_sum") or {}:
            per[k] = {"train_end": prev.get("train_end"),
                      "n_train_races": prev.get("n_train_races"),
                      "metrics": prev.get("metrics")}
    per[str(N_CAR)] = {"train_end": args.train_end,
                       "n_train_races": int(tr.rk.nunique()),
                       "conservative": conservative, "metrics": res}
    ends = [str(v.get("train_end")) for v in per.values()
            if isinstance(v, dict) and v.get("train_end")]
    META_PATH.write_text(json.dumps({
        "feature_names": list(FEATURE_NAMES),
        "target_sum": tgt,
        "per_n_car": per,
        # 最上位は**最も新しい終端**。honest 判定を甘くしないため（古い方を残すと
        # まだ in-sample な期間を通してしまう）。車数別の正確な値は per_n_car。
        "train_end": max(ends) if ends else args.train_end,
        "n_train_races": int(tr.rk.nunique()),
        "metrics": res,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("保存: %s / %s", MODEL_DIR / f"odds_tf_n{N_CAR}.txt", META_PATH)

    imp = sorted(zip(FEATURE_NAMES, booster.feature_importance("gain")),
                 key=lambda kv: -kv[1])[:12]
    tot = sum(booster.feature_importance("gain")) or 1
    log.info("重要度上位: %s", ", ".join(f"{k} {100*v/tot:.1f}%" for k, v in imp))


if __name__ == "__main__":
    main()
