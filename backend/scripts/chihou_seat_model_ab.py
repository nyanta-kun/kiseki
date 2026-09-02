"""地方競馬 Phase 1 — レース側モデル vs 学習不要ベースライン。

事前登録 `docs/upset_seat_preregistration_2026_09_02.md` に従う。
**判定基準・母集団・特徴量・選択率・stop rule はすべてそこで凍結済み。
このスクリプトはそれを実行するだけで、閾値を探索しない。**

腕（事前登録 §5）:
  base_a   期待空席数（学習なし）      ← 実質のベースライン。Phase 0 で lift 1.890
  base_b0  頭数のみ（学習なし）
  base_b   odds_top1 のみ（学習なし）
  base_c   現行ゲート（単一点・参考）
  m_noodds LightGBM・オッズ列なし
  m_odds2  LightGBM・odds_top1 と entropy_norm を追加

主判定（§11）: m_* が選択率 10/20/30% の**全3点**で base_a / base_b0 / base_b の
R2 をすべて上回り、レース単位ブートストラップの差の 95%CI が 0 を跨がないこと。
**1点でも落ちたら不採用。**

使い方:
    cd backend
    .venv/bin/python scripts/chihou_seat_model_ab.py \
        --cache /tmp/chihou_seat_cache.json --pop-rank-min 8 \
        --train-end 20260630 --out ../docs/model_verification/chihou_seat_phase1.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import lightgbm as lgb  # noqa: E402

from scripts.chihou_upset_seat_label import (  # noqa: E402
    CLOSED_RACE_TOP3_SHARE,
    MIN_HEAD_COUNT,
    annotate,
    head_bin,
)
import scripts.chihou_upset_seat_label as seat  # noqa: E402

SELECTION_POINTS = (0.10, 0.20, 0.30)   # 事前登録 §10。ここ以外は採否に使わない
N_BOOT = 4000
SEEDS = (0, 1, 2, 3, 4)

# 事前登録 §5.2。列を足さない
FEATURES_NOODDS = ["n", "slots", "distance", "is_turf", "course_id", "grade_id",
                   "race_number", "race_type_id", "weight_type_id", "month"]
FEATURES_ODDS2 = FEATURES_NOODDS + ["odds_top1", "entropy_norm"]

# 地方はほぼ全てダートなので is_turf が定数になるのは想定内。
# それ以外の列が定数なら取得漏れ（2026-09-02 に surface / race_type_code /
# weight_type_code の3列が SELECT に無く、定数のまま Phase 1 を回してしまった）。
EXPECTED_CONSTANT = {"is_turf"}

LGB_PARAMS = {
    "objective": "binary",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 50,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbose": -1,
}
NUM_ROUNDS = 400


def build_features(races: list[dict]) -> None:
    """モデル入力の列をレース dict へ足す。カテゴリは出現順の整数化。"""
    cat_maps: dict[str, dict[Any, int]] = {"course": {}, "grade": {}, "rt": {}, "wt": {}}

    def cid(key: str, v: Any) -> int:
        m = cat_maps[key]
        if v not in m:
            m[v] = len(m)
        return m[v]

    for r in races:
        r["is_turf"] = 0  # 地方はほぼ全てダート。列としては残すが定数になる
        r["course_id"] = cid("course", r.get("course"))
        r["grade_id"] = cid("grade", r.get("grade"))
        r["race_type_id"] = cid("rt", r.get("race_type_code"))
        r["weight_type_id"] = cid("wt", r.get("weight_type_code"))
        r["month"] = int(str(r["date"])[4:6])
        r["distance"] = float(r.get("distance") or 0)
        r["race_number"] = int(r.get("race_number") or 0)


def matrix(races: list[dict], feats: list[str]) -> np.ndarray:
    return np.array([[float(r.get(f) or 0.0) for f in feats] for r in races], dtype=float)


def check_constant_features(races: list[dict], feats: list[str]) -> list[str]:
    """事前登録した特徴が実際に値を持っているか確かめる。

    🔴 **取得漏れは「エラーにならず、ただ列が定数になる」形で壊れる。**
    2026-09-02 に実際に踏んだ: Phase 0 の RACE_SQL が surface /
    race_type_code / weight_type_code を SELECT しておらず、
    `r.get(...)` が None → 0.0 に潰れ、10列のうち3列が定数のまま
    Phase 1 を1回まるごと回してしまった。想定外の定数は必ず落とす。
    """
    x = matrix(races, feats)
    const = [f for i, f in enumerate(feats) if len(np.unique(x[:, i])) <= 1]
    unexpected = [f for f in const if f not in EXPECTED_CONSTANT]
    if unexpected:
        raise SystemExit(
            f"🔴 事前登録した特徴が定数になっている: {unexpected}\n"
            "   取得漏れの可能性が高い。Phase 0 の RACE_SQL を確認し、"
            "キャッシュを作り直すこと。"
        )
    return const


def labels(races: list[dict], defn: str) -> np.ndarray:
    return np.array([1.0 if r[f"hit_unpop_{defn}"] >= 1 else 0.0 for r in races])


def fit_predict(tr: list[dict], ev: list[dict], feats: list[str], defn: str) -> np.ndarray:
    """seed 平均。単一 seed で結論が変わる事故を避ける（競輪の教訓）。"""
    xtr, ytr = matrix(tr, feats), labels(tr, defn)
    xev = matrix(ev, feats)
    preds = []
    for seed in SEEDS:
        params = dict(LGB_PARAMS, seed=seed, bagging_seed=seed, feature_fraction_seed=seed)
        booster = lgb.train(params, lgb.Dataset(xtr, label=ytr), num_boost_round=NUM_ROUNDS)
        preds.append(booster.predict(xev))
    return np.mean(preds, axis=0)


def r2_at(scores: np.ndarray, y: np.ndarray, rate: float) -> tuple[float, np.ndarray]:
    """選択率 rate で選んだときの R2 と、選択マスク。

    R2 = 人気薄が複勝圏に来たレースのうち、選ばれたレースが占める割合。
    """
    k = max(1, int(len(scores) * rate))
    idx = np.argsort(-scores)[:k]
    mask = np.zeros(len(scores), dtype=bool)
    mask[idx] = True
    total = y.sum()
    return (float(y[mask].sum() / total) if total else float("nan"), mask)


def boot_diff(y: np.ndarray, mask_a: np.ndarray, mask_b: np.ndarray,
              seed: int = 0) -> tuple[float, float, float]:
    """R2 の差のレース単位ブートストラップ CI。

    レースが観測単位なので、レースを復元抽出する（1レース1行なので行の復元抽出と
    同じ）。分母（全陽性レース数）も再抽出ごとに取り直す。
    """
    rng = np.random.default_rng(seed)
    n = len(y)
    idx = rng.integers(0, n, size=(N_BOOT, n))
    ya, aa, bb = y[idx], mask_a[idx], mask_b[idx]
    tot = ya.sum(1)
    ok = tot > 0
    diffs = np.where(ok, ((ya * aa).sum(1) - (ya * bb).sum(1)) / np.maximum(tot, 1), np.nan)
    point = (y * mask_a).sum() / y.sum() - (y * mask_b).sum() / y.sum()
    return (float(point), float(np.nanpercentile(diffs, 2.5)),
            float(np.nanpercentile(diffs, 97.5)))


def stratified_lift_scores(races: list[dict], scores: np.ndarray, y: np.ndarray,
                           top_frac: float = 0.30) -> float | None:
    """頭数ビンを固定した lift の加重平均（事前登録 §12）。"""
    bins: dict[str, list[int]] = {}
    for i, r in enumerate(races):
        bins.setdefault(head_bin(r["n"]), []).append(i)
    tot_sel = tot_hit = 0
    weighted_base = 0.0
    for _b, ix in bins.items():
        ix_arr = np.array(ix)
        base = y[ix_arr].mean()
        if base <= 0:
            continue
        k = max(1, int(len(ix_arr) * top_frac))
        sel = ix_arr[np.argsort(-scores[ix_arr])[:k]]
        tot_sel += k
        tot_hit += int(y[sel].sum())
        weighted_base += base * k
    if not tot_sel or weighted_base <= 0:
        return None
    return float((tot_hit / tot_sel) / (weighted_base / tot_sel))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", required=True, help="Phase 0 が書いた取得キャッシュ")
    ap.add_argument("--pop-rank-min", type=int, default=8)
    ap.add_argument("--train-end", default="20260630", help="学習窓の終端（含む）")
    ap.add_argument("--out")
    args = ap.parse_args()

    seat.POP_RANK_MIN = args.pop_rank_min
    defn = "A"

    raw = json.load(open(args.cache))
    races = [r for r in (annotate(x) for x in raw) if r is not None]
    build_features(races)
    tr = [r for r in races if str(r["date"]) <= args.train_end]
    ev = [r for r in races if str(r["date"]) > args.train_end]
    y = labels(ev, defn)

    const = check_constant_features(tr, FEATURES_ODDS2)

    print(f"=== 地方 Phase 1  レース側モデル vs ベースライン（pop_rank>={args.pop_rank_min}）===")
    if const:
        print(f"  （想定内の定数列: {const}）")
    print(f"学習 {len(tr)}R（〜{args.train_end}） / 評価 {len(ev)}R"
          f" / 評価窓の base rate {y.mean():.1%}")

    arms: dict[str, np.ndarray] = {
        "base_a_expected_vacancy": np.array([r[f"E_hat_{defn}"] for r in ev]),
        "base_b0_head_count": np.array([float(r["n"]) for r in ev]),
        "base_b_odds_top1": np.array([float(r["odds_top1"]) for r in ev]),
        "m_noodds": fit_predict(tr, ev, FEATURES_NOODDS, defn),
        "m_odds2": fit_predict(tr, ev, FEATURES_ODDS2, defn),
    }

    result: dict[str, Any] = {
        "preregistration": "docs/upset_seat_preregistration_2026_09_02.md",
        "pop_rank_min": args.pop_rank_min,
        "n_train": len(tr), "n_eval": len(ev), "eval_base_rate": round(float(y.mean()), 4),
        "selection_points": list(SELECTION_POINTS),
        "constant_features": const,
        "arms": {},
    }

    print(f"\n--- R2（選択率ごと）---\n  {'腕':<26}" + "".join(f"{p:>9.0%}" for p in SELECTION_POINTS))
    masks: dict[str, dict[float, np.ndarray]] = {}
    for name, sc in arms.items():
        row, masks[name] = [], {}
        for p in SELECTION_POINTS:
            v, m = r2_at(sc, y, p)
            row.append(v)
            masks[name][p] = m
        result["arms"][name] = {"R2": {str(p): round(v, 4) for p, v in zip(SELECTION_POINTS, row)}}
        print(f"  {name:<26}" + "".join(f"{v:>9.1%}" for v in row))

    # base_c は単一点（閾値を持たない）
    sel_c = np.array([r["n"] >= MIN_HEAD_COUNT and r["top3_share"] < CLOSED_RACE_TOP3_SHARE
                      for r in ev])
    r2_c = float(y[sel_c].sum() / y.sum()) if y.sum() else float("nan")
    print(f"  {'base_c_current_gate':<26}{sel_c.mean():>9.1%} → R2 {r2_c:.1%}（単一点・参考）")
    result["arms"]["base_c_current_gate"] = {"selection_rate": round(float(sel_c.mean()), 4),
                                             "R2": round(r2_c, 4)}

    print("\n--- 頭数を固定した lift（事前登録 §12・base_a を超えること）---")
    for name, sc in arms.items():
        lift = stratified_lift_scores(ev, sc, y)
        result["arms"][name]["stratified_lift"] = round(lift, 3) if lift else None
        print(f"  {name:<26} {lift:.3f}" if lift else f"  {name:<26} n/a")

    # ---- 主判定（事前登録 §11）----
    print("\n=== 主判定（事前登録 §11）===")
    print("  m_* が選択率 10/20/30% の全3点で base_a / base_b0 / base_b すべてを上回り、")
    print("  差の 95%CI が 0 を跨がないこと。1点でも落ちたら不採用。\n")
    verdict: dict[str, Any] = {}
    for m in ("m_noodds", "m_odds2"):
        passed = True
        details = []
        for p in SELECTION_POINTS:
            for b in ("base_a_expected_vacancy", "base_b0_head_count", "base_b_odds_top1"):
                pt, lo, hi = boot_diff(y, masks[m][p], masks[b][p])
                ok = lo > 0
                passed &= ok
                details.append({"rate": p, "vs": b, "diff": round(pt, 4),
                                "ci": [round(lo, 4), round(hi, 4)], "pass": bool(ok)})
                print(f"  {m} vs {b:<26} @{p:.0%}  "
                      f"Δ{pt:+.3f} [{lo:+.3f}, {hi:+.3f}] {'OK' if ok else 'NG'}")
        verdict[m] = {"pass": bool(passed), "details": details}
        print(f"  → {m}: {'採用可' if passed else '🔴 不採用'}\n")
    result["verdict"] = verdict

    if not any(v["pass"] for v in verdict.values()):
        print("=== stop rule #1 に該当 ===")
        print("  学習モデルはベースラインを超えなかった。事前登録 §15-1 のとおり中止し、")
        print("  base_a 単体（学習不要・オッズだけで動く）を軽い商品として残す案に縮退する。")
    else:
        d_no = result["arms"]["m_noodds"]["R2"][str(SELECTION_POINTS[1])]
        d_o2 = result["arms"]["m_odds2"]["R2"][str(SELECTION_POINTS[1])]
        d_a = result["arms"]["base_a_expected_vacancy"]["R2"][str(SELECTION_POINTS[1])]
        if (d_o2 - d_a) > 0 and (d_no - d_a) <= 0.5 * (d_o2 - d_a):
            print("=== ⚠️ stop rule #2 の疑い ===")
            print("  改善の過半がオッズ2列から来ている。二重使用の退化として中止を検討する。")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n書き出し: {args.out}")


if __name__ == "__main__":
    main()
