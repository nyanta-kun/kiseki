"""朝の構造情報だけで最終三連複オッズを予測する（2026-08-11 新設）。

## なぜ要るのか

入稿は朝 7:00 だが、winticket の板は**発走までの近さ**で埋まるので朝の時点では
薄い（[[keirin_odds_availability_by_posttime_2026_08_07]]）。実測（検証窓13,749R）で
**朝の板が買う点すべてに揃うレースは 8.9%**、目の単位でも 29.6% が `9999.9`（＝未確定）。
残りは `landing_weights` が p3 単独へ落ちるため、配分が「倍率の水準」を見ないまま決まる。

そこで**競走得点・ライン構成・WT公式印・枠番・級班・脚質・直近成績率**と
既存モデルの p3/pw から、最終オッズを直接回帰する。

    honest 時間前向き（学習 <=2025-12 / 検証 2026-01〜08）
    7車 logMAE 0.137 / ±2倍以内 91.5% / Spearman 0.942
    9車 logMAE 0.144 / ±2倍以内 89.9% / Spearman 0.934
    （比較）朝の板 logMAE 0.331 / ±2倍以内 59.3% ・ p3含意 0.334 / 50.3%

## 🔴 板として整合させること（Σ(1/オッズ) を定数に合わせる）

三連複の板は `Σ(1/オッズ) = 1/払戻率` という硬い制約を持ち、**実測はほぼ定数**
（7車 1.3449・変動係数 0.0065 / 9車 1.3387・0.0028）。各目を独立に回帰すると
この制約を満たさず、実測では **1.2598（含意払戻率79.4%）＝「配当を多めに配る」向き**
に壊れていた。これは購入計画を壊す方向そのもの。

レース内で一律に再スケールすると下振れが **<0.8倍 29.96→24.24% / <0.5倍 5.37→3.85%**
へ改善する。**logMAE は 0.1367→0.1364 でほとんど動かない＝対称指標はこの欠陥に盲目**
だった。目標総和は学習窓の実測から決め `odds_trio_meta.json` に記録する。

## 🔴 配分には整合化・保守化の効果が無い（比率が変わらないため）

`landing_weights` の重みは `1/オッズ` に比例するので、**レース内一律のスケールでは
重みが1ミリも動かない**。整合化と保守倍率が効くのは「金額の水準」を使う用途
（想定払戻の表示・ガミ判定・ダッチング可否）だけ。配分目的なら素の点予測でよいが、
**同じ関数から両方を出すほうが取り違えない**ので整合板を正本とする。

## 🔴 予測オッズは p3 と blend しない

`landing_weights(lam=0.5)` は朝オッズと p3 の相乗平均を取るが、**予測オッズに対して
同じことをしてはいけない**。予測オッズは重要度の約7割が p3 由来（`lp_pl` 44% +
`lp_prod` 26%）なので、blend すると p3 を二重計上して薄まる。実測（7Cゲート内4,670R）:

| 配分 | 実質的中%（掃引/確認） |
|---|---|
| 現行 blend(朝, p3) | 30.49 / 30.64 |
| blend(予測, p3) | 34.26 / 34.22 |
| **予測オッズ単独** | **39.64 / 37.99** |

## 特徴量は学習と推論で同じ関数から作る

`build_race_features()` を学習スクリプト（`scripts/train_odds_prediction.py`）と
本番の両方が呼ぶ。**別々に書くと train/serve skew が静かに入る。**
`odds_trio_meta.json` に特徴量名の一覧を記録し、読み込み時に照合する。

⚠️ `wt_entries.pred_bad` は存在しない（3ヘッドのうち大敗ヘッドは永続化されていない）。
   学習側でも使わない。重要度0.6%なので落としても精度は変わらなかった
   （logMAE 0.1367 → 0.1368）。

⚠️ 学習は honest walk-forward の p3/pw、推論は `wt_entries.pred_{top3,win}_pct`。
   両者は相関 0.975/0.980・平均差ほぼ0・同スケールで系統差は無い。
   推論経路の特徴量で測り直しても logMAE 0.1416 / ±2倍 90.6% と保たれる。
"""
from __future__ import annotations

import itertools
import json
import logging
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

log = logging.getLogger(__name__)

# 予測オッズのモデル置き場。既定は本番の配布先。
#
# 🔴 **`KEIRIN_ODDS_MODEL_DIR` で差し替えられる**（2026-08-21 新設）。過去の窓を
#    評価するときは学習終端の古い（honest な）モデルを使う必要があるが、本番の
#    配布物を上書きして戻し忘れると**入稿の配分と足切りが静かに古いモデルになる**。
#    環境変数で向き先だけを変えれば、その回のプロセスにしか影響しない。
#    例: `KEIRIN_ODDS_MODEL_DIR=data/backup/odds_model_20260816`
MODEL_DIR = Path(os.environ.get("KEIRIN_ODDS_MODEL_DIR")
                 or Path(__file__).resolve().parent.parent / "data" / "models")
META_PATH = MODEL_DIR / "odds_trio_meta.json"

SUPPORTED_N_CAR = (7, 9)

# 級班・脚質の数値化。未知は中央値相当へ寄せる（学習時と同じ規則であること）
CLASS_CODE = {"SS": 5, "S1": 4, "S2": 3, "A1": 2, "A2": 1, "A3": 0}
CLASS_DEFAULT = 2
STYLE_CODE = {"逃": 0, "追": 1, "両": 2}
STYLE_DEFAULT = 2

# WT公式印。印なしは 0 で入ってくるので「最弱」を意味する 5 へ写す。
# ⚠️ NaN にしてはいけない（[[keirin_highpay_axis_design_2026_08_10]] と同じ罠）
NO_MARK = 5

FEATURE_NAMES: tuple[str, ...] = (
    "lp_prod", "lp_pl",
    "p3a", "p3b", "p3c", "p3sum", "pwa", "pwsum",
    "rk1", "rk2", "rk3_", "rksum", "rw1", "rw2", "rw3_",
    "mk1", "mk2", "mk3", "n_marked",
    "rp1", "rp2", "rp3", "rp_sum", "rp_rel",
    "n_line_in", "same_line_max", "has_top_line", "solo_in", "lead_in", "lpos_sum",
    "frame_sum", "frame_min", "cls_sum", "sty_lead",
    "fr_sum", "sr_sum", "tr_sum",
    "rp_mean", "rp_std", "rp_max", "rp_gap12", "rp_gap23", "rp_range",
    "n_lines", "n_solo", "max_line",
    "ent_p3", "ent_pw", "pw_max", "pw_gap12", "p3_max", "p3_sum2",
)


class OddsPredictionUnavailable(RuntimeError):
    """モデル・メタ・入力のいずれかが揃わない。呼び出し側は従来経路へ落とすこと。"""


# ---------------------------------------------------------------------------
# 特徴量（学習と推論で共有する唯一の実装）
# ---------------------------------------------------------------------------
def _entropy(values: Sequence[float]) -> float:
    v = np.asarray(list(values), dtype=float)
    total = v.sum()
    if total <= 0:
        return 0.0
    v = v / total
    return float(-(v * np.log(v + 1e-12)).sum())


def _pl_trio(pw: Mapping[int, float], cars: Sequence[int]) -> dict[frozenset, float]:
    """Plackett-Luce で「3人とも3着以内」を順列6通りの和として厳密に出す。"""
    s = {c: max(float(pw[c]), 1e-9) for c in cars}
    tot = sum(s.values())
    out: dict[frozenset, float] = {}
    for combo in itertools.combinations(cars, 3):
        p = 0.0
        for a, b, c in itertools.permutations(combo):
            d1 = tot - s[a]
            d2 = tot - s[a] - s[b]
            if d1 <= 0 or d2 <= 0:
                continue
            p += (s[a] / tot) * (s[b] / d1) * (s[c] / d2)
        out[frozenset(combo)] = p
    return out


def build_race_features(
    cars: Sequence[int],
    p3: Mapping[int, float],
    pw: Mapping[int, float],
    meta: Mapping[int, Mapping[str, Any]],
) -> tuple[list[frozenset], np.ndarray]:
    """1レース分の全三連複組み合わせについて特徴量行列を作る。

    cars: 車番（7 or 9）
    p3:   {車番: 3着内率 0-1}
    pw:   {車番: 1着率 0-1}
    meta: {車番: {race_point, mark, line_group, line_size, line_pos,
                  is_line_leader, player_class, style,
                  first_rate, second_rate, third_rate}}

    returns (組み合わせの並び, 行列[len(combos) x len(FEATURE_NAMES)])
    """
    cars = sorted(cars)
    if len(cars) not in SUPPORTED_N_CAR:
        raise OddsPredictionUnavailable(f"未対応の車数: {len(cars)}")
    for c in cars:
        if float(p3.get(c, 0)) <= 0 or float(pw.get(c, 0)) <= 0:
            raise OddsPredictionUnavailable(f"p3/pw が欠けています: 車番 {c}")
        if c not in meta:
            raise OddsPredictionUnavailable(f"出走表の情報が欠けています: 車番 {c}")

    rp = {c: float(meta[c]["race_point"]) for c in cars}
    if any(math.isnan(v) for v in rp.values()):
        raise OddsPredictionUnavailable("競走得点に欠損があります")
    lg = {c: meta[c].get("line_group") for c in cars}
    if any(v is None for v in lg.values()):
        raise OddsPredictionUnavailable("ライン情報に欠損があります")
    lsz = {c: int(meta[c].get("line_size") or 1) for c in cars}
    lpos = {c: int(meta[c].get("line_pos") or 1) for c in cars}
    lead = {c: int(meta[c].get("is_line_leader") or 0) for c in cars}
    mk = {c: (int(meta[c].get("mark") or 0) or NO_MARK) for c in cars}
    cls_ = {c: CLASS_CODE.get(meta[c].get("player_class"), CLASS_DEFAULT) for c in cars}
    sty = {c: STYLE_CODE.get(meta[c].get("style"), STYLE_DEFAULT) for c in cars}
    fr = {c: float(meta[c].get("first_rate") or 0) for c in cars}
    sr = {c: float(meta[c].get("second_rate") or 0) for c in cars}
    tr = {c: float(meta[c].get("third_rate") or 0) for c in cars}

    rpv = np.array([rp[c] for c in cars], dtype=float)
    rp_mean = float(rpv.mean())
    rp_sorted = np.sort(rpv)[::-1]
    top_line = max(set(lg.values()),
                   key=lambda L: sum(p3[c] for c in cars if lg[c] == L))

    prod_raw = {frozenset(cb): p3[cb[0]] * p3[cb[1]] * p3[cb[2]]
                for cb in itertools.combinations(cars, 3)}
    z_prod = sum(prod_raw.values()) or 1.0
    pl = _pl_trio(pw, cars)
    z_pl = sum(pl.values()) or 1.0
    rank_p3 = {c: i for i, c in enumerate(sorted(cars, key=lambda x: -p3[x]))}
    rank_pw = {c: i for i, c in enumerate(sorted(cars, key=lambda x: -pw[x]))}

    race_level = {
        "rp_mean": rp_mean, "rp_std": float(rpv.std()), "rp_max": float(rp_sorted[0]),
        "rp_gap12": float(rp_sorted[0] - rp_sorted[1]),
        "rp_gap23": float(rp_sorted[1] - rp_sorted[2]),
        "rp_range": float(rp_sorted[0] - rp_sorted[-1]),
        "n_lines": float(len(set(lg.values()))),
        "n_solo": float(sum(1 for c in cars if lsz[c] == 1)),
        "max_line": float(max(lsz.values())),
        "ent_p3": _entropy(p3[c] for c in cars),
        "ent_pw": _entropy(pw[c] for c in cars),
        "pw_max": float(max(pw[c] for c in cars)),
        "pw_gap12": float(np.sort([pw[c] for c in cars])[::-1][0]
                          - np.sort([pw[c] for c in cars])[::-1][1]),
        "p3_max": float(max(p3[c] for c in cars)),
        "p3_sum2": float(np.sort([p3[c] for c in cars])[::-1][:2].sum()),
    }

    combos: list[frozenset] = []
    rows: list[list[float]] = []
    for cb in itertools.combinations(cars, 3):
        key = frozenset(cb)
        cc = sorted(cb, key=lambda x: -p3[x])
        a, b, c3 = cc
        mks = sorted(mk[x] for x in cc)
        rps = sorted((rp[x] for x in cc), reverse=True)
        lgs = [lg[x] for x in cc]
        k3 = sorted(rank_p3[x] for x in cc)
        w3 = sorted(rank_pw[x] for x in cc)
        f = {
            "lp_prod": math.log10(max(prod_raw[key] / z_prod, 1e-12)),
            "lp_pl": math.log10(max(pl[key] / z_pl, 1e-12)),
            "p3a": p3[a], "p3b": p3[b], "p3c": p3[c3],
            "p3sum": p3[a] + p3[b] + p3[c3],
            "pwa": pw[a], "pwsum": pw[a] + pw[b] + pw[c3],
            "rk1": k3[0], "rk2": k3[1], "rk3_": k3[2], "rksum": sum(k3),
            "rw1": w3[0], "rw2": w3[1], "rw3_": w3[2],
            "mk1": mks[0], "mk2": mks[1], "mk3": mks[2],
            "n_marked": sum(1 for x in mks if x < NO_MARK),
            "rp1": rps[0], "rp2": rps[1], "rp3": rps[2], "rp_sum": sum(rps),
            "rp_rel": sum(rps) / 3 - rp_mean,
            "n_line_in": len(set(lgs)),
            "same_line_max": max(lgs.count(x) for x in set(lgs)),
            "has_top_line": int(top_line in lgs),
            "solo_in": sum(1 for x in cc if lsz[x] == 1),
            "lead_in": sum(lead[x] for x in cc),
            "lpos_sum": sum(lpos[x] for x in cc),
            "frame_sum": sum(cc), "frame_min": min(cc),
            "cls_sum": sum(cls_[x] for x in cc),
            "sty_lead": sum(1 for x in cc if sty[x] == 0),
            "fr_sum": sum(fr[x] for x in cc),
            "sr_sum": sum(sr[x] for x in cc),
            "tr_sum": sum(tr[x] for x in cc),
            **race_level,
        }
        combos.append(key)
        rows.append([float(f[name]) for name in FEATURE_NAMES])
    return combos, np.asarray(rows, dtype=np.float64)


# ---------------------------------------------------------------------------
# モデルの読み込みと推論
# ---------------------------------------------------------------------------
_MODEL_CACHE: dict[int, Any] = {}
_META_CACHE: dict[str, Any] | None = None


def load_meta() -> dict[str, Any]:
    """`odds_trio_meta.json`（目標総和・保守倍率・特徴量名）を読む。

    ⚠️ 特徴量名は**学習時に記録したものと完全一致**していなければならない。
       食い違ったまま推論すると静かに壊れる（順序違いは特に検知しにくい）。
    """
    global _META_CACHE
    if _META_CACHE is not None:
        return _META_CACHE
    if not META_PATH.exists():
        raise OddsPredictionUnavailable(
            f"{META_PATH} がありません。scripts/train_odds_prediction.py を実行してください"
        )
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    recorded = tuple(meta.get("feature_names") or ())
    if recorded != FEATURE_NAMES:
        raise OddsPredictionUnavailable(
            "学習時と推論時で特徴量が一致しません（順序も含めて一致が必要）。"
            f"モデルを再学習してください。学習時 {len(recorded)}本 / 現在 {len(FEATURE_NAMES)}本"
        )
    _META_CACHE = meta
    return meta


def model_train_end() -> str | None:
    """このモデルの学習終端（`per_n_car` の最大値）。記録が無ければ None。"""
    per = load_meta().get("per_n_car", {}) or {}
    ends = [str(v.get("train_end")) for v in per.values() if v.get("train_end")]
    return max(ends) if ends else None


def assert_model_is_honest(date_from: str, *, who: str = "") -> None:
    """三連複オッズモデルの**学習終端より前**を対象にしていないか検査する。

    🔴 **予測オッズは月次 vintage を持たない。** 学習終端以前の期間を評価すると
       in-sample になり、「予測オッズで足切りしたら良くなった」のような結論が
       まるごと嘘になる。7T1 の三連単側には
       `backfill_7t1_rank_wt.assert_odds_model_is_honest` があるのに三連複側だけ
       無く、**2026-08-21 に実際に踏んだ**（本番の `train_end` は 2026-08-04 で、
       2026年の窓を評価しようとしていた）。

    回避策は「honest なモデルへ差し替える」こと。学習終端の古いモデルを
    `data/backup/odds_model_YYYYMMDD/` に置いてあるので `KEIRIN_ODDS_MODEL_DIR`
    でそちらを向ける（本番の配布物を上書きしない）。

    ⚠️ **live 予想では絶対に発火しない**（対象は常に学習終端より後）。
       発火するのは過去を評価するスクリプトだけ。
    """
    end = model_train_end()
    if end and str(date_from) <= end:
        tag = f"[{who}] " if who else ""
        raise SystemExit(
            f"{tag}{date_from} は三連複オッズモデルの学習終端（{end}）以前です。"
            "そのまま評価すると in-sample になります。honest なモデル "
            "（data/backup/odds_model_*/）を KEIRIN_ODDS_MODEL_DIR で指すか、"
            f"{end} より後の期間だけを対象にしてください。")


def load_model(n_car: int):
    if n_car in _MODEL_CACHE:
        return _MODEL_CACHE[n_car]
    path = MODEL_DIR / f"odds_trio_n{n_car}.txt"
    if not path.exists():
        raise OddsPredictionUnavailable(f"{path} がありません")
    try:
        import lightgbm as lgb
    except ImportError as e:  # pragma: no cover
        raise OddsPredictionUnavailable(f"lightgbm が読めません: {e}") from e
    booster = lgb.Booster(model_file=str(path))
    _MODEL_CACHE[n_car] = booster
    return booster


def target_sum(n_car: int) -> float:
    m = load_meta().get("per_n_car", {}).get(str(n_car))
    if not m or not m.get("target_sum"):
        raise OddsPredictionUnavailable(f"{n_car}車の目標総和が meta にありません")
    return float(m["target_sum"])


def conservative_multiplier(n_car: int, quantile: str = "p10") -> float:
    """整合板に掛けて「購入計画に使う数字」を作る倍率（下側分位・学習窓較正）。

    ⚠️ 配分には使わない（比率が変わらないので無意味）。
       想定払戻の表示・ガミ判定など**金額の水準**を使う用途のみ。
    """
    m = load_meta().get("per_n_car", {}).get(str(n_car), {})
    c = (m.get("conservative") or {}).get(quantile)
    if not c:
        raise OddsPredictionUnavailable(f"{n_car}車の保守倍率 {quantile} が meta にありません")
    return float(c)


def predict_board(
    cars: Sequence[int],
    p3: Mapping[int, float],
    pw: Mapping[int, float],
    meta: Mapping[int, Mapping[str, Any]],
) -> dict[frozenset, float]:
    """整合化済みの三連複予測オッズ盤面 {frozenset({a,b,c}): オッズ}。

    整合化 = レース内で Σ(1/オッズ) を学習窓実測の定数へ合わせる再スケール。
    """
    cars = sorted(cars)
    n_car = len(cars)
    booster = load_model(n_car)
    combos, X = build_race_features(cars, p3, pw, meta)
    raw = np.power(10.0, booster.predict(X))
    if not np.all(np.isfinite(raw)) or np.any(raw <= 0):
        raise OddsPredictionUnavailable("予測オッズに非有限・非正の値が出ました")
    s = float((1.0 / raw).sum())
    if s <= 0:
        raise OddsPredictionUnavailable("予測板の総和が0以下です")
    scale = s / target_sum(n_car)
    return {k: float(o * scale) for k, o in zip(combos, raw)}


# ---------------------------------------------------------------------------
# DB から入力を集める（本番の入稿経路が使う）
# ---------------------------------------------------------------------------
_ENTRY_SQL = """
SELECT frame_no, race_point, prediction_mark, player_class, style,
       line_group, line_size, line_pos, is_line_leader,
       first_rate, second_rate, third_rate,
       pred_win_pct, pred_top3_pct
FROM wt_entries WHERE race_key = ?
"""


def load_race_inputs(race_key: str):
    """入稿時点の DB から (cars, p3, pw, meta) を組み立てる。

    p3/pw は `wt_entries.pred_{top3,win}_pct`（日次バッチが候補生成直後＝入稿より前に
    書く）。パーセント表記なので 1/100 して 0-1 へ直す。
    """
    from src.database import get_connection

    with get_connection() as conn:
        rows = conn.execute(_ENTRY_SQL, (race_key,)).fetchall()
    if not rows:
        raise OddsPredictionUnavailable(f"{race_key}: wt_entries が空です")

    cars, p3, pw, meta = [], {}, {}, {}
    for r in rows:
        d = dict(r)
        car = int(d["frame_no"])
        p3v, pwv = d.get("pred_top3_pct"), d.get("pred_win_pct")
        rp = d.get("race_point")
        if p3v is None or pwv is None or rp is None:
            raise OddsPredictionUnavailable(
                f"{race_key}: 車番{car} の pred_top3_pct / pred_win_pct / race_point が未設定"
            )
        cars.append(car)
        p3[car] = float(p3v) / 100.0
        pw[car] = float(pwv) / 100.0
        meta[car] = {
            "race_point": float(rp),
            "mark": d.get("prediction_mark"),
            "player_class": d.get("player_class"),
            "style": d.get("style"),
            "line_group": d.get("line_group"),
            "line_size": d.get("line_size"),
            "line_pos": d.get("line_pos"),
            "is_line_leader": d.get("is_line_leader"),
            "first_rate": d.get("first_rate"),
            "second_rate": d.get("second_rate"),
            "third_rate": d.get("third_rate"),
        }
    if len(cars) not in SUPPORTED_N_CAR:
        raise OddsPredictionUnavailable(f"{race_key}: 未対応の車数 {len(cars)}")
    return sorted(cars), p3, pw, meta


def predicted_trio_board(race_key: str) -> dict[frozenset, float]:
    """整合化済みの予測オッズ盤面。揃わなければ OddsPredictionUnavailable。"""
    cars, p3, pw, meta = load_race_inputs(race_key)
    return predict_board(cars, p3, pw, meta)


def predicted_odds_for_legs(
    race_key: str, axis1: int, axis2: int, partners: Sequence[int],
) -> dict[int, float]:
    """軸2車固定の買い目について {相手車番: 予測オッズ} を返す。

    ⚠️ **買う点すべてが揃わなければ例外**。一部だけ返すと呼び出し側で
       別尺度と混ざり比率が壊れる（`landing_weights._usable_odds` と同じ思想）。
    """
    board = predicted_trio_board(race_key)
    out: dict[int, float] = {}
    for t in partners:
        o = board.get(frozenset({axis1, axis2, int(t)}))
        if not o or o <= 0:
            raise OddsPredictionUnavailable(
                f"{race_key}: 予測盤面に {axis1}-{axis2}-{t} がありません"
            )
        out[int(t)] = float(o)
    return out


def trio_ev_for_legs(
    race_key: str, axis1: int, axis2: int, partners: Sequence[int],
) -> dict[int, float] | None:
    """{相手車番: EV} を返す。作れなければ **None**（例外にしない）。

        EV_i = 予測オッズ({軸1,軸2,i}) × P({軸1,軸2,i} が3着以内)

    `P` は Plackett-Luce（`_pl_trio`）＝モデルの勝率から出す厳密値。
    予測オッズは「市場がいくら付けるか」の推定なので、この積は
    **市場に対する割安さ**になる（1 より大きいほど我々の見立てで割安）。

    🔴 **買う点すべてが揃わなければ None**。一部だけ返すと呼び出し側で
       EV 順と指数順が混ざり、並びの意味が壊れる
       （`predicted_odds_for_legs` と同じ思想）。
    ⚠️ 予測オッズは 7車・9車以外では作れない（実測 3.7%）。
       呼び出し側は None のときに従来規則へフォールバックすること。
    """
    try:
        cars, p3, pw, meta = load_race_inputs(race_key)
        board = predict_board(cars, p3, pw, meta)
        pl = _pl_trio(pw, cars)
    except Exception:
        return None
    out: dict[int, float] = {}
    for t in partners:
        k = frozenset({int(axis1), int(axis2), int(t)})
        o, p = board.get(k), pl.get(k)
        if not o or o <= 0 or p is None:
            return None
        out[int(t)] = float(o) * float(p)
    return out


def trio_hit_probability(
    race_key: str, axis1: int, axis2: int, partners: Sequence[int],
) -> float | None:
    """買う三連複のどれかが3着以内に入る確率（Plackett-Luce）。作れなければ None。

    「当たりやすい順に並べる」ためだけの量。**絶対値は信用しないこと**——
    2026-08-22 の実測（8/16〜8/21 の実売 228件）で、この値の四分位と実際の
    的中率は

        Q1 予測 0.17 / 実測 0.23   Q2 0.41 / 0.23
        Q3 0.57 / 0.44             Q4 0.73 / **0.51**

    と上側で**大きく上振れ**する。順位付けとしては単調（Q1 0.23 → Q4 0.51）で
    使えるので、`premium_pick` は順位だけに使っている。

    ⚠️ 三連複の盤面しか作れない（7車・9車以外は None）。三連単のランクでは使えない。
    """
    try:
        cars, p3, pw, meta = load_race_inputs(race_key)
        pl = _pl_trio(pw, cars)
    except Exception:
        return None
    total = 0.0
    for t in partners:
        p = pl.get(frozenset({int(axis1), int(axis2), int(t)}))
        if p is None:
            return None
        total += float(p)
    return total


def try_predicted_odds_for_legs(
    race_key: str, axis1: int, axis2: int, partners: Sequence[int],
) -> dict[int, float] | None:
    """例外を出さない版。使えないときは None を返し、**理由を必ずログに残す**。

    🔴 無言のフォールバックにしないこと。予測オッズが使えていないのに
       入稿は成功するので、ログが無いと劣化に気づけない
       （[[keirin_session_2026_08_07_handoff]]「無言のフォールバックは検知できない」）。
    """
    try:
        return predicted_odds_for_legs(race_key, axis1, axis2, partners)
    except OddsPredictionUnavailable as e:
        log.warning("[odds-pred] %s: 予測オッズを使えません（従来経路へ）: %s", race_key, e)
        return None
    except Exception as e:  # noqa: BLE001 — 入稿を止めないことを最優先する
        log.warning("[odds-pred] %s: 予測オッズで想定外の失敗（従来経路へ）: %r",
                    race_key, e)
        return None
