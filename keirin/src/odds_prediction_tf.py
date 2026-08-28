"""朝の構造情報だけで最終**三連単**オッズを予測する（2026-08-12 新設）。

`src/odds_prediction.py`（三連複）の三連単版。設計思想はあちらの docstring を
そのまま引き継ぐので、**まずあちらを読むこと**。ここには三連単固有の話だけを書く。

## なぜ要るのか

三連単の高配当枠は「想定オッズが 100〜140倍の目を10点買う」という帯の狙い撃ちで
成立する。現状その想定オッズは **Plackett-Luce の確率から `0.749/p` で逆算した
だけ**で粗く、実測（13,785R）では**的中時の中央払戻が 69,200円**と、狙った
10万円を下回っていた。`P(10万円超) <= ROI/10` の上限 9.4% に対して実績 2.90%
＝約1/3しか取れておらず、**差の主因はオッズ推定の粗さ**。

## 三連複版との違い

- **組み合わせが順列**（7車なら 210通り・三連複は35通り）。したがって
  特徴量は「集合の性質」ではなく**着順ごとの性質**を持つ必要がある。
  同じ3車でも 1-2-3 と 3-2-1 では10倍以上オッズが違う
- したがって `lp_pl` は**順列そのものの Plackett-Luce 確率**（三連複のように
  6順列を足し上げない）。三連複版の `_pl_trio` をそのまま使ってはいけない
- ライン構造は「1着と2着が同ラインか」「ライン順（前→後）に決まる形か」など
  **向きを持つ特徴**が効く。集合としての `same_line_max` だけでは足りない

## 🔴 板として整合させること（三連複版と同じ）

三連単の板も `Σ(1/オッズ) = 1/払戻率` という硬い制約を持つ。7車の実測は
**1.3360（変動係数 0.003・398レースで確認）**でほぼ定数。各目を独立に回帰すると
この制約を満たさないので、レース内で一律に再スケールする。
**logMAE はこの欠陥に盲目**なので、対称指標だけを見て良し悪しを決めないこと。

## 🔴 学習の p3/pw は walk-forward 予測を使う

`wt_entries.pred_{top3,win}_pct` は過去分が backfill されており**学習に使うと
look-ahead**。三連複版と同じく `data/exp_cache/axis_detail_7car.pkl` を第一ソースに
する。推論時は live の `wt_entries.pred_*` を使う。

⚠️ 本モジュールを設計した検証では、**軸積のような「絶対閾値のゲート」が
   確率の出どころ次第で母集団を1.4倍に変えた**前例がある
   （[[keirin_7h3_recalibration_closed_2026_08_12]]）。帯（100〜140倍）で切るのも
   同じ性質なので、**本番の推論経路で件数を突き合わせてから閾値を決めること**。
"""
from __future__ import annotations

import itertools
import json
import math
from collections.abc import Mapping, Sequence
import os
from pathlib import Path
from typing import Any

import numpy as np

# 🔴 **`KEIRIN_ODDS_TF_MODEL_DIR` で差し替えられるようにしてある**（2026-08-27）。
#    三連単の予測オッズは月次 vintage を持たず、本番モデルの学習終端は 2025-12-31。
#    それより前の窓を評価するには「もっと古い終端で学習したモデル」が要るが、
#    学習スクリプトの出力先が固定だと**本番モデルを上書きしてしまう**
#    （三連複側には `KEIRIN_ODDS_MODEL_DIR` があるのに、こちらだけ無かった）。
#    過去を評価するときは vintage ディレクトリを指すこと。
MODEL_DIR = Path(os.environ.get(
    "KEIRIN_ODDS_TF_MODEL_DIR",
    str(Path(__file__).resolve().parent.parent / "data" / "models")))
META_PATH = MODEL_DIR / "odds_tf_meta.json"
#: 三連単の予測オッズを作れる車数。**モデルは車数ごとに別ファイル**
#: （`odds_tf_n7.txt` / `odds_tf_n9.txt`）で、特徴量の作り方は共通。
#: 9車は 2026-08-27 に追加（型ラボを 9車へ広げるため。それまでは 7車のみだった）。
SUPPORTED_N_CAR = (7, 9)

NO_MARK = 9
CLASS_CODE = {"S1": 0, "S2": 1, "S3": 2, "A1": 3, "A2": 4, "A3": 5, "L1": 6}
CLASS_DEFAULT = 7
STYLE_CODE = {"逃": 0, "捲": 1, "追": 2, "両": 3}
STYLE_DEFAULT = 4

#: 着順ごとの性質を持たせる。接尾辞 1/2/3 は**着順**（p3 の順位ではない）。
FEATURE_NAMES: tuple[str, ...] = (
    "lp_pl", "lp_prod",
    "p3_1", "p3_2", "p3_3", "p3sum",
    "pw_1", "pw_2", "pw_3", "pwsum",
    "rk_1", "rk_2", "rk_3", "rksum", "rk_spread",
    "rw_1", "rw_2", "rw_3",
    "mk_1", "mk_2", "mk_3", "n_marked",
    "rp_1", "rp_2", "rp_3", "rp_sum", "rp_rel",
    # 向きを持つライン特徴（三連単固有）
    "same_line_12", "same_line_23", "same_line_13", "n_line_in",
    "line_order_12", "line_order_23", "lead_at_1", "solo_at_1", "lpos_1",
    "has_top_line", "solo_in", "lead_in",
    "frame_1", "frame_sum", "cls_sum", "sty_1", "sty_lead",
    "fr_1", "fr_sum", "sr_sum", "tr_sum",
    # レース水準（全目で共通）
    "rp_mean", "rp_std", "rp_max", "rp_gap12", "rp_gap23", "rp_range",
    "n_lines", "n_solo", "max_line",
    "ent_p3", "ent_pw", "pw_max", "pw_gap12", "p3_max", "p3_sum2",
)


class OddsPredictionUnavailable(RuntimeError):
    """モデル・メタ・入力のいずれかが揃わない。呼び出し側は従来経路へ落とすこと。"""


def _entropy(values: Sequence[float]) -> float:
    v = np.asarray(list(values), dtype=float)
    total = v.sum()
    if total <= 0:
        return 0.0
    v = v / total
    return float(-(v * np.log(v + 1e-12)).sum())


def pl_ordered(pw: Mapping[int, float], cars: Sequence[int]) -> dict[tuple, float]:
    """Plackett-Luce の**順列**確率 P(a→b→c)。三連複版と違い足し上げない。"""
    s = {c: max(float(pw[c]), 1e-9) for c in cars}
    tot = sum(s.values())
    out: dict[tuple, float] = {}
    for a, b, c in itertools.permutations(cars, 3):
        d1 = tot - s[a]
        d2 = tot - s[a] - s[b]
        if d1 <= 0 or d2 <= 0:
            continue
        out[(a, b, c)] = (s[a] / tot) * (s[b] / d1) * (s[c] / d2)
    return out


def build_race_features(
    cars: Sequence[int],
    p3: Mapping[int, float],
    pw: Mapping[int, float],
    meta: Mapping[int, Mapping[str, Any]],
) -> tuple[list[tuple], np.ndarray]:
    """1レース分の全**三連単**組み合わせについて特徴量行列を作る。

    returns (着順つきタプルの並び, 行列[len(combos) x len(FEATURE_NAMES)])
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

    pl = pl_ordered(pw, cars)
    z_pl = sum(pl.values()) or 1.0
    prod_raw = {t: p3[t[0]] * p3[t[1]] * p3[t[2]] for t in pl}
    z_prod = sum(prod_raw.values()) or 1.0
    rank_p3 = {c: i for i, c in enumerate(sorted(cars, key=lambda x: -p3[x]))}
    rank_pw = {c: i for i, c in enumerate(sorted(cars, key=lambda x: -pw[x]))}
    pws = np.sort([pw[c] for c in cars])[::-1]

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
        "pw_max": float(pws[0]), "pw_gap12": float(pws[0] - pws[1]),
        "p3_max": float(max(p3[c] for c in cars)),
        "p3_sum2": float(np.sort([p3[c] for c in cars])[::-1][:2].sum()),
    }

    combos: list[tuple] = []
    rows: list[list[float]] = []
    for t in sorted(pl):
        a, b, c3 = t
        ks = [rank_p3[a], rank_p3[b], rank_p3[c3]]
        f = {
            "lp_pl": math.log10(max(pl[t] / z_pl, 1e-12)),
            "lp_prod": math.log10(max(prod_raw[t] / z_prod, 1e-12)),
            "p3_1": p3[a], "p3_2": p3[b], "p3_3": p3[c3],
            "p3sum": p3[a] + p3[b] + p3[c3],
            "pw_1": pw[a], "pw_2": pw[b], "pw_3": pw[c3],
            "pwsum": pw[a] + pw[b] + pw[c3],
            "rk_1": ks[0], "rk_2": ks[1], "rk_3": ks[2], "rksum": sum(ks),
            "rk_spread": max(ks) - min(ks),
            "rw_1": rank_pw[a], "rw_2": rank_pw[b], "rw_3": rank_pw[c3],
            "mk_1": mk[a], "mk_2": mk[b], "mk_3": mk[c3],
            "n_marked": sum(1 for x in t if mk[x] < NO_MARK),
            "rp_1": rp[a], "rp_2": rp[b], "rp_3": rp[c3],
            "rp_sum": rp[a] + rp[b] + rp[c3],
            "rp_rel": (rp[a] + rp[b] + rp[c3]) / 3 - rp_mean,
            # 向きを持つライン特徴
            "same_line_12": int(lg[a] == lg[b]),
            "same_line_23": int(lg[b] == lg[c3]),
            "same_line_13": int(lg[a] == lg[c3]),
            "n_line_in": len({lg[a], lg[b], lg[c3]}),
            # ライン順（前→後）に決まる形か。1 = 順走・0 = それ以外
            "line_order_12": int(lg[a] == lg[b] and lpos[a] < lpos[b]),
            "line_order_23": int(lg[b] == lg[c3] and lpos[b] < lpos[c3]),
            "lead_at_1": lead[a], "solo_at_1": int(lsz[a] == 1), "lpos_1": lpos[a],
            "has_top_line": int(top_line in (lg[a], lg[b], lg[c3])),
            "solo_in": sum(1 for x in t if lsz[x] == 1),
            "lead_in": sum(lead[x] for x in t),
            "frame_1": a, "frame_sum": a + b + c3,
            "cls_sum": cls_[a] + cls_[b] + cls_[c3],
            "sty_1": sty[a], "sty_lead": sum(1 for x in t if sty[x] == 0),
            "fr_1": fr[a], "fr_sum": fr[a] + fr[b] + fr[c3],
            "sr_sum": sr[a] + sr[b] + sr[c3],
            "tr_sum": tr[a] + tr[b] + tr[c3],
            **race_level,
        }
        combos.append(t)
        rows.append([float(f[name]) for name in FEATURE_NAMES])
    return combos, np.asarray(rows, dtype=np.float64)


# ---------------------------------------------------------------------------
_MODEL_CACHE: dict[int, Any] = {}
_META_CACHE: dict[str, Any] | None = None


def load_meta() -> dict[str, Any]:
    """`odds_tf_meta.json` を読み、**特徴量名の一覧を照合する**。

    train/serve skew を起動時に落とすため。順序違いも検知する。
    """
    global _META_CACHE
    if _META_CACHE is None:
        if not META_PATH.exists():
            raise OddsPredictionUnavailable(f"メタが見つかりません: {META_PATH}")
        meta = json.loads(META_PATH.read_text(encoding="utf-8"))
        names = tuple(meta.get("feature_names") or ())
        if names != FEATURE_NAMES:
            raise OddsPredictionUnavailable(
                "学習時と推論時で特徴量が食い違っています。モデルを学習し直すこと。")
        _META_CACHE = meta
    return _META_CACHE


def load_model(n_car: int):
    if n_car not in _MODEL_CACHE:
        path = MODEL_DIR / f"odds_tf_n{n_car}.txt"
        if not path.exists():
            raise OddsPredictionUnavailable(f"モデルが見つかりません: {path}")
        import lightgbm as lgb
        _MODEL_CACHE[n_car] = lgb.Booster(model_file=str(path))
    return _MODEL_CACHE[n_car]


def model_train_end(n_car: int | None = None) -> str | None:
    """学習終端。`n_car` を指定すればその車数のもの。

    🔴 **車数を指定しないときは「最も新しい終端」を返す**。honest 判定を
       甘くしないため（古い方を返すと、まだ in-sample な期間を通してしまう）。
    ⚠️ 旧形式のメタ（`per_n_car` が無い）は最上位の `train_end` へ落ちる。
    """
    meta = load_meta()
    per = meta.get("per_n_car") or {}
    if n_car is not None:
        v = per.get(str(n_car))
        if isinstance(v, dict) and v.get("train_end"):
            return str(v["train_end"])
        return str(meta.get("train_end")) if meta.get("train_end") else None
    ends = [str(v.get("train_end")) for v in per.values()
            if isinstance(v, dict) and v.get("train_end")]
    if meta.get("train_end"):
        ends.append(str(meta["train_end"]))
    return max(ends) if ends else None


def target_sum(n_car: int) -> float:
    """板の Σ(1/オッズ) の目標値（学習窓の実測平均）。"""
    v = load_meta().get("target_sum", {}).get(str(n_car))
    if not v:
        raise OddsPredictionUnavailable(f"target_sum({n_car}) がメタにありません")
    return float(v)


def conservative_quantiles(final_odds, coherent_pred) -> dict[str, float]:
    """`実際 / 整合板` の下側分位（p05 / p10 / p25）。

    三連複側 `scripts/train_odds_prediction.py` と**同じ定義**にしてある
    （学習窓で較正し、`odds_tf_meta.json` の `conservative` へ入れる）。
    学習スクリプトと後追い較正スクリプトの両方がここを呼ぶ——
    2箇所に書くと「表示に使う倍率」が静かに食い違う。
    """
    import numpy as _np
    ratio = _np.asarray(final_odds, dtype=float) / _np.asarray(coherent_pred, dtype=float)
    ratio = ratio[_np.isfinite(ratio) & (ratio > 0)]
    if ratio.size == 0:
        raise OddsPredictionUnavailable("保守倍率を計算できる行がありません")
    return {f"p{int(q * 100):02d}": float(_np.quantile(ratio, q)) for q in (0.05, 0.10, 0.25)}


def conservative_multiplier(n_car: int, quantile: str = "p25") -> float:
    """整合板に掛けて「下振れしてもこの倍率は割らない」水準を作る倍率。

    🔴 **三連複の値を流用しないこと**（2026-08-29 まで実際に流用していた）。
       `netkeirin_submit_wt._conservative_board` が三連単の盤面へ
       `src.odds_prediction.conservative_multiplier`（三連複の 0.8428）を
       掛けており、券種が違うのに同じ倍率という状態だった。三連単のほうが
       ばらつきが大きい（honest 2026 の ±2倍以内 80.6% ↔ 三連複 91.6%）。

    ⚠️ 配分には使わない（レース内で一律なので比率が変わらない）。
    ⚠️ **k点の最小値**（＝商品としての「最低払戻」）へこれを流用してはいけない。
       それは `backend/src/services/keirin_payout_floor.py` の
       `floor_ratio(点数, 券種)` の役目（1点あたりの分位を最小値の約束に
       使うと、点数が増えるほど甘くなる）。
    """
    m = (load_meta().get("per_n_car") or {}).get(str(n_car), {})
    c = (m.get("conservative") or {}).get(quantile)
    if not c:
        raise OddsPredictionUnavailable(
            f"三連単 {n_car}車の保守倍率 {quantile} がメタにありません"
            "（scripts/calibrate_odds_tf_conservative.py で入れること）")
    return float(c)


def predict_board(cars, p3, pw, meta) -> dict[tuple, float]:
    """全210点の予測オッズを返す。**レース内で再スケールして板として整合させる**。

    🔴 再スケールを省くと Σ(1/オッズ) が制約から外れ、
       「配当を多めに配る」向きに壊れる。帯（100〜140倍）で切る用途では
       そのずれがそのまま狙いのずれになる。
    """
    n_car = len(list(cars))
    booster = load_model(n_car)
    combos, X = build_race_features(cars, p3, pw, meta)
    raw = np.power(10.0, booster.predict(X))
    raw = np.clip(raw, 1.0, None)
    scale = float((1.0 / raw).sum()) / target_sum(n_car)
    return {c: float(o * scale) for c, o in zip(combos, raw)}


# ---------------------------------------------------------------------------
# DB から入力を集める（入稿経路が使う・2026-08-26 追加）
# ---------------------------------------------------------------------------
def predicted_trifecta_board(race_key: str) -> dict[tuple, float]:
    """整合化済みの三連単予測オッズ盤面 {(1着,2着,3着): オッズ}。

    入力（`wt_entries` の p3/pw・出走表）は三連複版と**同じもの**を使うので、
    `src.odds_prediction.load_race_inputs` をそのまま借りる。
    作れないときは `OddsPredictionUnavailable`。

    ⚠️ 対応車数は `SUPPORTED_N_CAR`。未対応の車数では例外を投げる（黙って0を返さない）。
    """
    from src.odds_prediction import load_race_inputs

    cars, p3, pw, meta = load_race_inputs(race_key)
    if len(cars) not in SUPPORTED_N_CAR:
        raise OddsPredictionUnavailable(
            f"{race_key}: 三連単の予測オッズは {SUPPORTED_N_CAR} 車にしか作れません"
            f"（このレースは {len(cars)}車）")
    return predict_board(cars, p3, pw, meta)


def try_predicted_trifecta_board(race_key: str) -> dict[tuple, float] | None:
    """例外を出さない版。使えないときは None を返し、**理由を必ず残す**。

    🔴 無言のフォールバックにしない。予測オッズが使えていなくても入稿は成功して
       しまうので、ログが無いと劣化に気づけない（三連複版と同じ思想）。
    """
    try:
        return predicted_trifecta_board(race_key)
    except Exception as e:  # noqa: BLE001 — 入稿を止めない
        print(f"[odds-pred-tf] {race_key}: 三連単の予測オッズを作れません: {e}",
              flush=True)
        return None
