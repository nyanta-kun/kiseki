"""JRA 複勝残差診断 — Harville 変換のズレが馬タイプで系統的かを測る（Phase A / 学習なし）

事前登録: `docs/jra_winplace_structure_plan_2026_09_04.md` §4 Phase A

## 目的

ユーザー仮説（2026-09-04）:
  「1着を取りに行った結果3着内に残った馬」と「勝つのは難しいので1着は諦めて3着内に
  来た馬」がいる。つまり馬ごとに着順分布の散らばりが違う。

Harville / Plackett-Luce は「1頭につき強さが1つ」の Luce モデルなので、この現象を
原理的に表現できない。本スクリプトは **モデルを作らずに残差だけで** それが実在するかを
確かめる。

    r_i = 1[finish_position <= 3] - p_place_harville_i

を `p_win` の10分位で層別したうえで、脚質 / 過去5走の着順分散 / 過去5走の勝率複勝率比で
切り、層内残差の差に 95%CI を付ける。

## 🔴 検証の作法（CLAUDE.md「測る前に本番コードを読む」）に対する遵守事項

1. **DB の `keiba.calculated_indices.win_probability` は使わない。**
   出荷モデルは `jra_protocol.TRAIN_DATA_END` までで学習されており、探索窓 2025 は
   訓練内に入る（`composite.py:271-278`: 訓練内 0.43 / 訓練外 0.26）。
   本スクリプトは **各窓の開始日より前のデータだけ**で is_win binary LGB を学習し直す
   （walk-forward）。特徴量は `composite.OUT_PROB_FEATURE_NAMES` の34列、
   ハイパラは `scripts/train_jra_iswin_head.py::PARAMS` を import して使う。
   レース内正規化は本番と同じ `clip(1e-9,1.0) -> raw/total`（`composite.py:735-741`）。

2. **複勝確率は本番の関数をそのまま呼ぶ。** 独立実装をしない。
   `CompositeIndexCalculator._harville_place_probs` を import する。

3. **脚質は本番の分類器をそのまま呼ぶ。** 閾値をコピーしない。
   `PaceHandicapCalculator._determine_runner_type`（`pace_handicap.py:387`）を
   そのまま使う。過去走は本番 `_get_past_results_batch` と同じ条件
   （`abnormality_code = 0` / `date < 対象レース日` / 直近 `LOOKBACK_RACES` 走）。

4. **point-in-time 厳守。** 過去走系の特徴は対象レースの `date` より**厳密に前**の走だけ。
   このリポジトリは `pedigree.py` と `frame_bias.py` で PIT 違反の前科がある。

5. **`p_win` の10分位で層別する。** 省くと λ が既に扱っている本命-穴バイアスを
   再発見するだけになる（計画 §4.1 の 🔴）。

## 母集団（計画 §3）

- スキーマ `keiba` / JRA 10場（course 01〜10）
- 探索窓 2025-01-01〜2025-12-31 / 確認窓 2026-01-04〜2026-08-01
- レースの「フィールド」= `calculated_indices`(version>=26 の各馬最大版) に行があり
  `abnormality_code NOT IN (1,2)` の馬。**この集合の上で p_win を正規化し Harville を回す**
  （＝本番が推論時に見る集合に最も近い）。
- 残差の集計対象からは `finish_position` が NULL / 0 の馬を除く（中止・失格）。
  ただし**フィールドからは外さない**（実際に出走してレース展開に影響しているため、
  Harville の n を変えるべきではない）。
- 障害（`is_jump=1`）は含める。内訳を JSON に出す。
- 🔴 **`place_slots = 3`（フィールド 8頭以上）のレースに限定して本集計を行う。**
  Harville は n<8 で「2着以内」を返すため、同じ残差に混ぜられない。
  `place_slots = 2`（5〜7頭）は `place_slots_2_summary` に別掲する（判定には使わない）。
  `n < 5`（`place_slots = 0`）は除外。

## 切り口（層別変数・すべて PIT）

| 切り口 | 定義 | unknown 群 |
|---|---|---|
| `runner_type` | 本番 `_determine_runner_type`（直近10走の `passing_4/head_count` 平均） | 過去走 0 または `passing_4` 全欠損 → "unknown" |
| `running_style_pit` | 直近10走の `race_results.running_style`（1逃/2先/3差/4追）の最頻値 | 過去走 0 または全て不明(0) → "unknown" |
| `finish_var_tertile` | 直近5走の `finish_position` の標本分散の3分位 | 直近5走が揃わない馬 → "unknown" |
| `win_place_ratio_tertile` | 直近5走の（勝ち数 / 3着内数）の3分位 | 直近5走が揃わない → "unknown" / 3着内が0走 → "no_place" |

🔴 `running_style` は**対象レースの結果列**であり、そのレースの値を使うのは PIT 違反である
（そのレースで実際にどう走ったかはレース後にしか分からない）。**過去走の最頻値**を使う。
本番が使っているのは `passing_4` 由来の `runner_type` のほうなので、そちらが主・
`running_style_pit` は**頑健性の裏取り**という位置づけ（両者の一致表も JSON に出す）。

⚠️ `race_results.margin` は全行 NULL の死列、`passing_1` 44% / `passing_2` 50% しか
充足していない（2026-09-04 実測）。本スクリプトはいずれも使っていない。

`unknown` / `no_place` は別集計し、**判定には使わない**。
3分位・10分位のカット点は**探索窓で決めて凍結し、確認窓にもそのまま適用する**
（窓ごとに切り直すと同符号再現の判定が別物になるため）。

## 統計

- 層内比較のために `p_win` 10分位で中心化した調整残差 `r_adj = r - mean(r | 同じ decile)`
  を使う。各水準の `mean(r_adj)` と、水準間差 `mean(r_adj|A) - mean(r_adj|B)` を報告する。
- **95%CI はレース単位のクラスタ bootstrap**（`race_id` 単位で復元抽出・既定 1000反復）。
  同一レース内の残差は独立ではない（Σp_place ≒ 3 の制約がある）ため馬単位では不可。
  decile 平均も replicate ごとに再計算する。
- 効果量は pt（＝確率のパーセントポイント）で報告する。

## 判定基準（事前固定・動かさない）

- **仮説あり**: 探索窓でいずれかの切り口の層内残差差の 95%CI が 0 を跨がず、
  かつ確認窓で**同符号で再現**する（確認窓の点推定が同符号）
- **仮説なし**: 上記を満たす切り口が 0 件
- 有意でも効果量が 1pt 未満なら「実在するが小さい」と書く

## 使い方

    cd backend
    .venv/bin/python scripts/jra_place_residual_diag.py
    .venv/bin/python scripts/jra_place_residual_diag.py \
        --explore 20250101 20251231 --confirm 20260104 20260801 --bootstrap 1000

出力: `docs/model_verification/jra_place_residual_diag.json`（冪等・上書き）
"""

from __future__ import annotations

import argparse
import bisect
import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

_here = Path(__file__).resolve()
_root = _here.parents[1]  # backend/
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

# --- 本番コードをそのまま import する（独立実装をしない） -----------------------
from scripts.train_jra_iswin_head import PARAMS as ISWIN_PARAMS  # noqa: E402
from scripts.train_jra_out_rate import featurize as prod_featurize  # noqa: E402
from src.indices.composite import (  # noqa: E402
    OUT_PROB_FEATURE_NAMES,
    SUBINDEX_SOURCE_SQL,
    CompositeIndexCalculator,
)
from src.indices.pace_handicap import (  # noqa: E402
    LOOKBACK_RACES,
    PaceHandicapCalculator,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("place_residual_diag")

FEATURES = OUT_PROB_FEATURE_NAMES
OUT_PATH = _root.parent / "docs" / "model_verification" / "jra_place_residual_diag.json"
JRA_COURSES = ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10")

MAX_ROUND = 2000
SEEDS = (42, 123, 456)
PAST_N = 5  # 着順分散・勝率複勝率比のルックバック（計画 §4.1「過去5走」）

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

# 34列 + 目視確認と層別に要る列。train_jra_out_rate.FETCH_SQL と同じ骨格。
FETCH_SQL = f"""
WITH ci AS ({SUBINDEX_SOURCE_SQL})
SELECT
    r.date, ci.race_id, ci.horse_id,
    r.race_number, r.race_name, r.course_name,
    h.name AS horse_name, rr.horse_number,
    ci.speed_index, ci.last_3f_index, ci.course_aptitude, ci.position_advantage,
    ci.rotation_index, ci.jockey_index, ci.pace_index, ci.pedigree_index,
    ci.training_index, ci.anagusa_index, ci.paddock_index, ci.rebound_index,
    ci.rivals_growth_index, ci.career_phase_index, ci.distance_change_index,
    ci.jockey_trainer_combo_index, ci.going_pedigree_index,
    r.distance, r.head_count, r.surface, r.condition, r.grade,
    re.frame_number, re.horse_age, re.weight_carried, re.horse_weight,
    re.jvan_time_dm, re.jvan_battle_dm,
    rr.weight_change, rr.abnormality_code, rr.finish_position
FROM ci
JOIN keiba.races r         ON r.id = ci.race_id
JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
LEFT JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
LEFT JOIN keiba.horses h   ON h.id = ci.horse_id
WHERE r.date >= %(start)s AND r.date <= %(end)s
  AND r.course IN {JRA_COURSES}
"""

# 過去走（PIT 用）。本番 `_get_past_results_batch` と同じ条件:
#   abnormality_code = 0 / **course で絞らない**（本番も絞っておらず、地方・海外の走も
#   脚質判定に使っている）。対象レース側だけ JRA 10場に絞る（FETCH_SQL）。
PAST_SQL = """
SELECT rr.horse_id, r.date, rr.finish_position, rr.passing_4, rr.running_style,
       r.head_count
FROM keiba.race_results rr
JOIN keiba.races r ON r.id = rr.race_id
WHERE rr.abnormality_code = 0
  AND r.date <= %(end)s
ORDER BY rr.horse_id, r.date
"""

# race_results.running_style のコード → 脚質ラベル（JRA 実測で passing_4/head_count と単調対応）
RUNNING_STYLE_MAP = {"1": "rs_escape", "2": "rs_leader", "3": "rs_mid", "4": "rs_closer"}


def _dsn() -> str:
    return (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )


def _query(conn, sql: str, params: dict) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    return df


# ---------------------------------------------------------------------------
# PIT な過去走特徴
# ---------------------------------------------------------------------------


class PastRuns:
    """馬ごとの過去走を date 昇順で保持し、任意の日付より前だけを切り出す。

    🔴 point-in-time: 取り出しは常に `date < 対象レース日` の厳密不等号。
    """

    def __init__(self, df: pd.DataFrame) -> None:
        df = df.sort_values(["horse_id", "date"], kind="mergesort")
        self._by_horse: dict[int, tuple] = {}
        for hid, g in df.groupby("horse_id", sort=False):
            self._by_horse[int(hid)] = (
                g["date"].astype(str).tolist(),
                g["finish_position"].to_numpy(),
                g["passing_4"].to_numpy(),
                g["head_count"].to_numpy(),
                g["running_style"].to_numpy(),
            )

    def before(self, horse_id: int, date: str, limit: int):
        """`date` より前の直近 `limit` 走を **新しい順**で返す。"""
        rec = self._by_horse.get(int(horse_id))
        if rec is None:
            return [], [], [], []
        dates, fp, p4, hc, rs = rec
        cut = bisect.bisect_left(dates, str(date))  # dates[:cut] は全て date 未満
        if cut <= 0:
            return [], [], [], []
        lo = max(0, cut - limit)
        sl = slice(lo, cut)
        # 新しい順に反転（本番の order_by date DESC と揃える）
        return fp[sl][::-1], p4[sl][::-1], hc[sl][::-1], rs[sl][::-1]


def _runner_type(p4: np.ndarray, hc: np.ndarray) -> str:
    """本番 `PaceHandicapCalculator._determine_runner_type` をそのまま呼ぶ。

    閾値（RUNNER_TYPE_THRESHOLDS）をコピーしないため、行の形だけ合わせて渡す。
    """
    rows = [
        SimpleNamespace(
            RaceResult=SimpleNamespace(passing_4=None if pd.isna(a) else float(a)),
            Race=SimpleNamespace(head_count=None if pd.isna(b) else float(b)),
        )
        for a, b in zip(p4, hc)
    ]
    return PaceHandicapCalculator._determine_runner_type(None, rows)  # type: ignore[arg-type]


def build_pit_features(df: pd.DataFrame, past: PastRuns) -> pd.DataFrame:
    """対象行に PIT な脚質 / 着順分散 / 勝率複勝率比を付ける。"""
    rt: list[str] = []
    rs_pit: list[str] = []
    fvar: list[float] = []
    wpr: list[float] = []
    npast5: list[int] = []
    for hid, date in zip(df["horse_id"].to_numpy(), df["date"].astype(str).to_numpy()):
        # --- 脚質: 本番と同じ直近 LOOKBACK_RACES 走 ---
        _, p4, hc, rs = past.before(hid, date, LOOKBACK_RACES)
        rt.append(_runner_type(p4, hc) if len(p4) else "unknown")

        # --- running_style（過去走の最頻値・裏取り用）---
        codes = [RUNNING_STYLE_MAP[s] for s in
                 (str(v).strip() for v in rs if v is not None and not pd.isna(v))
                 if s in RUNNING_STYLE_MAP]
        rs_pit.append(max(set(codes), key=codes.count) if codes else "unknown")

        # --- 着順分散 / 勝率複勝率比: 直近 PAST_N 走 ---
        fp5, _, _, _ = past.before(hid, date, PAST_N)
        fp5 = np.asarray([v for v in fp5 if v is not None and not pd.isna(v) and v > 0], dtype=float)
        npast5.append(int(len(fp5)))
        if len(fp5) < PAST_N:
            fvar.append(np.nan)
            wpr.append(np.nan)
            continue
        fvar.append(float(np.var(fp5, ddof=1)))
        n_win = float((fp5 == 1).sum())
        n_place = float((fp5 <= 3).sum())
        wpr.append(n_win / n_place if n_place > 0 else -1.0)  # -1 = no_place 群の印
    out = df.copy()
    out["runner_type"] = rt
    out["running_style_pit"] = rs_pit
    out["finish_var5"] = fvar
    out["win_place_ratio5"] = wpr
    out["n_past5"] = npast5
    return out


# ---------------------------------------------------------------------------
# walk-forward の p_win
# ---------------------------------------------------------------------------


def fit_predict_pwin(train: pd.DataFrame, target: pd.DataFrame) -> np.ndarray:
    """`train`（＝窓より前のデータのみ）で is_win binary LGB を学習し `target` を予測する。

    train_jra_iswin_head と同じ手順:
      1. train を日付で 80/20 に割り、3seed で early stopping → best_iteration の中央値
      2. train 全体を seed[0] で固定ラウンド refit
    返すのは **正規化前の生確率**（正規化は呼び出し側でレース単位に行う）。
    """
    train = train.sort_values("date", kind="mergesort").reset_index(drop=True)
    dates = train["date"].astype(str)
    split = dates.iloc[int(len(dates) * 0.8)]
    tr = train[dates <= split]
    va = train[dates > split]
    if len(va) == 0 or len(tr) == 0:
        raise SystemExit(f"train/valid 分割に失敗（split={split} tr={len(tr)} va={len(va)}）")
    logger.info("  学習: train=%d行(%s〜%s) valid=%d行(〜%s)",
                len(tr), dates.min(), split, len(va), dates.max())

    Xtr, ytr = tr[FEATURES].values.astype(float), (tr["finish_position"] == 1).astype(int).values
    Xva, yva = va[FEATURES].values.astype(float), (va["finish_position"] == 1).astype(int).values
    best_iters = []
    for seed in SEEDS:
        ds = lgb.Dataset(Xtr, ytr, feature_name=FEATURES)
        dv = lgb.Dataset(Xva, yva, reference=ds)
        m = lgb.train(dict(ISWIN_PARAMS, seed=seed), ds, num_boost_round=MAX_ROUND,
                      valid_sets=[dv], callbacks=[lgb.early_stopping(100, verbose=False)])
        best_iters.append(int(m.best_iteration))
    n_rounds = int(np.median(best_iters))
    logger.info("  best_iters=%s -> refit rounds=%d", best_iters, n_rounds)

    Xall = train[FEATURES].values.astype(float)
    yall = (train["finish_position"] == 1).astype(int).values
    final = lgb.train(dict(ISWIN_PARAMS, seed=SEEDS[0]),
                      lgb.Dataset(Xall, yall, feature_name=FEATURES),
                      num_boost_round=n_rounds)
    return np.asarray(final.predict(target[FEATURES].values.astype(float)), dtype=float)


def attach_probs(df: pd.DataFrame, raw: np.ndarray) -> pd.DataFrame:
    """本番と同じ正規化 + 本番の Harville 関数で p_win / p_place を付ける。

    - 正規化: `np.clip(raw, 1e-9, 1.0)` -> `raw/total`（composite.py:735-741 と同一）
    - 複勝: `CompositeIndexCalculator._harville_place_probs`（独立実装をしない）
    - フィールド = df の各 race_id の全行（呼び出し側で abnormality 1,2 を除いてある）
    """
    d = df.copy()
    d["raw_win"] = np.clip(raw, 1e-9, 1.0)
    total = d.groupby("race_id")["raw_win"].transform("sum")
    d["p_win"] = d["raw_win"] / total

    p_place = np.empty(len(d), dtype=float)
    field_n = np.empty(len(d), dtype=int)
    for _, idx in d.groupby("race_id", sort=False).indices.items():
        wp = d["p_win"].to_numpy()[idx].tolist()
        pp = CompositeIndexCalculator._harville_place_probs(wp)
        p_place[idx] = pp
        field_n[idx] = len(wp)
    d["p_place_harville"] = p_place
    d["field_n"] = field_n
    return d


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------

CUTS = {
    "runner_type": ["escape", "leader", "mid", "closer"],
    "running_style_pit": ["rs_escape", "rs_leader", "rs_mid", "rs_closer"],
    "finish_var_tertile": ["T1_low", "T2_mid", "T3_high"],
    "win_place_ratio_tertile": ["T1_low", "T2_mid", "T3_high"],
}


def assign_levels(d: pd.DataFrame, cutoffs: dict) -> pd.DataFrame:
    d = d.copy()
    d["cut_runner_type"] = d["runner_type"].where(
        d["runner_type"].isin(CUTS["runner_type"]), "unknown"
    )
    d["cut_running_style_pit"] = d["running_style_pit"].where(
        d["running_style_pit"].isin(CUTS["running_style_pit"]), "unknown"
    )

    fv_c = cutoffs["finish_var_tertile"]
    fv = d["finish_var5"].to_numpy(dtype=float)
    lab = np.full(len(d), "unknown", dtype=object)
    ok = ~np.isnan(fv)
    lab[ok & (fv <= fv_c[0])] = "T1_low"
    lab[ok & (fv > fv_c[0]) & (fv <= fv_c[1])] = "T2_mid"
    lab[ok & (fv > fv_c[1])] = "T3_high"
    d["cut_finish_var_tertile"] = lab

    wr_c = cutoffs["win_place_ratio_tertile"]
    wr = d["win_place_ratio5"].to_numpy(dtype=float)
    lab = np.full(len(d), "unknown", dtype=object)
    ok = ~np.isnan(wr)
    lab[ok & (wr < 0)] = "no_place"
    pos = ok & (wr >= 0)
    lab[pos & (wr <= wr_c[0])] = "T1_low"
    lab[pos & (wr > wr_c[0]) & (wr <= wr_c[1])] = "T2_mid"
    lab[pos & (wr > wr_c[1])] = "T3_high"
    d["cut_win_place_ratio_tertile"] = lab

    dec_c = np.asarray(cutoffs["pwin_decile"], dtype=float)
    d["pwin_decile"] = np.searchsorted(dec_c, d["p_win"].to_numpy(dtype=float), side="right")
    return d


def _bootstrap_indices(race_codes: np.ndarray, n_rep: int, rng: np.random.Generator):
    """レース単位クラスタ bootstrap の行インデックスを yield する（完全ベクトル化）。"""
    order = np.argsort(race_codes, kind="mergesort")
    sorted_codes = race_codes[order]
    n_races = int(sorted_codes.max()) + 1
    counts = np.bincount(sorted_codes, minlength=n_races)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    for _ in range(n_rep):
        samp = rng.integers(0, n_races, size=n_races)
        c = counts[samp]
        tot = int(c.sum())
        base = np.repeat(starts[samp], c)
        off = np.arange(tot) - np.repeat(np.cumsum(c) - c, c)
        yield order[base + off]


def analyze(d: pd.DataFrame, n_boot: int, seed: int = 20260904) -> dict:
    """decile 中心化した調整残差の水準別平均と、水準間差の 95%CI を出す。

    主指標は複勝残差 `residual = 1[着<=3] - p_place_harville`（事前登録どおり）。

    ⚠️ **補助指標**として単勝残差 `win_residual = 1[着==1] - p_win` を同じ層・
    同じ bootstrap 標本で併記する。これは事前登録の判定には**使わない**が、解釈に必須:
    複勝残差だけが正なら Harville の散らばり仮定の破れだが、単勝残差も同符号・
    同程度なら**それは p_win の較正ずれ**であって仮説の証拠ではない。

    さらに補助指標 `excess_residual = residual - (p_place/p_win) * win_residual` を出す。
    Harville の式では
        p2_i = p_i * Σ_{j≠i} p_j/(1-p_j),  p3_i = p_i * ΣΣ ...
    であり、係数側は p_i にほとんど依存しない。よって一次近似で
        ∂p_place_i/∂p_win_i ≒ p_place_i / p_win_i
    が成り立つ。`(p_place/p_win) * win_residual` は「p_win の較正ずれが Harville を
    通して機械的に生む複勝残差」＝ *win-shadow* であり、`excess` はその影を差し引いた
    **複勝側固有のズレ**である。⚠️ 一次近似であり、レース内の Σp=1 制約による他馬への
    波及は無視している。事前登録の判定には使わない（解釈のためだけ）。
    """
    rng = np.random.default_rng(seed)
    r = d["residual"].to_numpy(dtype=float)
    rw = d["win_residual"].to_numpy(dtype=float)
    rx = d["excess_residual"].to_numpy(dtype=float)
    dec = d["pwin_decile"].to_numpy(dtype=int)
    race_codes = pd.factorize(d["race_id"].to_numpy())[0]
    n_dec = 10

    def _adj_level_means(vals: np.ndarray, idx: np.ndarray, codes: np.ndarray, k: int) -> np.ndarray:
        vv = vals[idx]
        dd = dec[idx]
        ds = np.bincount(dd, weights=vv, minlength=n_dec)
        dc = np.bincount(dd, minlength=n_dec).astype(float)
        dm = np.divide(ds, dc, out=np.zeros_like(ds), where=dc > 0)
        vadj = vv - dm[dd]
        cc = codes[idx]
        m = cc >= 0
        ls = np.bincount(cc[m], weights=vadj[m], minlength=k)
        lc = np.bincount(cc[m], minlength=k).astype(float)
        return np.divide(ls, lc, out=np.full(k, np.nan), where=lc > 0)

    def level_means(idx: np.ndarray, codes: np.ndarray, k: int) -> np.ndarray:
        return _adj_level_means(r, idx, codes, k)

    all_idx = np.arange(len(d))
    out: dict = {"cuts": {}}

    for cut, levels in CUTS.items():
        col = f"cut_{cut}"
        extra = ["unknown"] + (["no_place"] if cut == "win_place_ratio_tertile" else [])
        judge_levels = list(levels)
        all_levels = judge_levels + extra
        code_map = {lv: i for i, lv in enumerate(all_levels)}
        codes = d[col].map(lambda v: code_map.get(v, -1)).to_numpy(dtype=int)
        k = len(all_levels)

        point = level_means(all_idx, codes, k)
        point_w = _adj_level_means(rw, all_idx, codes, k)
        point_x = _adj_level_means(rx, all_idx, codes, k)
        boots = np.empty((n_boot, k), dtype=float)
        boots_w = np.empty((n_boot, k), dtype=float)
        boots_x = np.empty((n_boot, k), dtype=float)
        for b, idx in enumerate(_bootstrap_indices(race_codes, n_boot, rng)):
            boots[b] = _adj_level_means(r, idx, codes, k)
            boots_w[b] = _adj_level_means(rw, idx, codes, k)
            boots_x[b] = _adj_level_means(rx, idx, codes, k)

        counts = {lv: int((codes == i).sum()) for lv, i in code_map.items()}
        raw_means = {
            lv: (round(float(r[codes == i].mean()), 6) if counts[lv] else None)
            for lv, i in code_map.items()
        }
        lo = np.nanpercentile(boots, 2.5, axis=0)
        hi = np.nanpercentile(boots, 97.5, axis=0)
        lo_w = np.nanpercentile(boots_w, 2.5, axis=0)
        hi_w = np.nanpercentile(boots_w, 97.5, axis=0)
        lo_x = np.nanpercentile(boots_x, 2.5, axis=0)
        hi_x = np.nanpercentile(boots_x, 97.5, axis=0)

        levels_out = {}
        for lv, i in code_map.items():
            levels_out[lv] = {
                "n": counts[lv],
                "mean_residual_pt": None if counts[lv] == 0 else round(float(r[codes == i].mean()) * 100, 4),
                "mean_adj_residual_pt": None if np.isnan(point[i]) else round(float(point[i]) * 100, 4),
                "ci95_adj_pt": None if counts[lv] == 0 else [round(float(lo[i]) * 100, 4), round(float(hi[i]) * 100, 4)],
                # 補助（判定外）: 単勝側にも同じズレがあるか
                "aux_mean_adj_win_residual_pt": None if np.isnan(point_w[i]) else round(float(point_w[i]) * 100, 4),
                "aux_ci95_adj_win_pt": None if counts[lv] == 0 else [round(float(lo_w[i]) * 100, 4), round(float(hi_w[i]) * 100, 4)],
                # 補助（判定外）: win-shadow を差し引いた複勝側固有のズレ
                "aux_mean_adj_excess_pt": None if np.isnan(point_x[i]) else round(float(point_x[i]) * 100, 4),
                "aux_ci95_adj_excess_pt": None if counts[lv] == 0 else [round(float(lo_x[i]) * 100, 4), round(float(hi_x[i]) * 100, 4)],
                "judgeable": lv in judge_levels,
            }

        contrasts = []
        for a_i in range(len(judge_levels)):
            for b_i in range(a_i + 1, len(judge_levels)):
                la, lb = judge_levels[a_i], judge_levels[b_i]
                ia, ib = code_map[la], code_map[lb]
                if counts[la] == 0 or counts[lb] == 0:
                    continue
                diff = point[ia] - point[ib]
                bd = boots[:, ia] - boots[:, ib]
                clo, chi = np.nanpercentile(bd, [2.5, 97.5])
                bdw = boots_w[:, ia] - boots_w[:, ib]
                wlo, whi = np.nanpercentile(bdw, [2.5, 97.5])
                contrasts.append({
                    "pair": f"{la} - {lb}",
                    "diff_pt": round(float(diff) * 100, 4),
                    "ci95_pt": [round(float(clo) * 100, 4), round(float(chi) * 100, 4)],
                    "significant": bool(clo > 0 or chi < 0),
                    "n_a": counts[la], "n_b": counts[lb],
                    "aux_win_diff_pt": round(float(point_w[ia] - point_w[ib]) * 100, 4),
                    "aux_win_ci95_pt": [round(float(wlo) * 100, 4), round(float(whi) * 100, 4)],
                    "aux_win_significant": bool(wlo > 0 or whi < 0),
                    "aux_excess_diff_pt": round(float(point_x[ia] - point_x[ib]) * 100, 4),
                    "aux_excess_ci95_pt": [round(float(v) * 100, 4) for v in
                                           np.nanpercentile(boots_x[:, ia] - boots_x[:, ib], [2.5, 97.5])],
                    "aux_excess_significant": bool(
                        np.nanpercentile(boots_x[:, ia] - boots_x[:, ib], 2.5) > 0
                        or np.nanpercentile(boots_x[:, ia] - boots_x[:, ib], 97.5) < 0),
                })

        # decile × level のクロス表（生の残差平均・透明性のため）
        table = []
        for dv in range(n_dec):
            row = {"decile": dv, "n": int((dec == dv).sum()),
                   "mean_pwin": round(float(d["p_win"].to_numpy()[dec == dv].mean()), 5),
                   "mean_residual_pt": round(float(r[dec == dv].mean()) * 100, 3)}
            for lv, i in code_map.items():
                m = (dec == dv) & (codes == i)
                row[lv] = {"n": int(m.sum()),
                           "mean_residual_pt": round(float(r[m].mean()) * 100, 3) if m.any() else None}
            table.append(row)

        out["cuts"][cut] = {
            "levels": levels_out,
            "contrasts": contrasts,
            "decile_table": table,
        }

    out["overall"] = {
        "n_horses": int(len(d)),
        "n_races": int(d["race_id"].nunique()),
        "actual_place_rate": round(float((d["finish_position"] <= 3).mean()), 5),
        "mean_p_place_harville": round(float(d["p_place_harville"].mean()), 5),
        "mean_residual_pt": round(float(r.mean()) * 100, 4),
        "mean_p_win": round(float(d["p_win"].mean()), 5),
        "actual_win_rate": round(float((d["finish_position"] == 1).mean()), 5),
        "aux_mean_win_residual_pt": round(float(rw.mean()) * 100, 4),
        "aux_mean_excess_residual_pt": round(float(rx.mean()) * 100, 4),
    }
    # passing_4 由来の runner_type と running_style 由来の一致表（頑健性の裏取り）
    ct = pd.crosstab(d["cut_runner_type"], d["cut_running_style_pit"])
    order = ["escape", "leader", "mid", "closer"]
    rs_order = ["rs_escape", "rs_leader", "rs_mid", "rs_closer"]
    both = d[d["cut_runner_type"].isin(order) & d["cut_running_style_pit"].isin(rs_order)]
    agree = float((both["cut_runner_type"].map(lambda v: "rs_" + v)
                   == both["cut_running_style_pit"]).mean()) if len(both) else float("nan")
    out["runner_type_vs_running_style"] = {
        "crosstab": {str(i): {str(c): int(ct.loc[i, c]) for c in ct.columns} for i in ct.index},
        "n_both_known": int(len(both)),
        "exact_agreement": round(agree, 4),
        "note": "本番が使うのは passing_4 由来の runner_type。running_style は裏取り用",
    }

    jump = d["is_jump"] == 1
    out["jump_breakdown"] = {
        "flat": {"n": int((~jump).sum()),
                 "mean_residual_pt": round(float(r[~jump.to_numpy()].mean()) * 100, 4)},
        "jump": {"n": int(jump.sum()),
                 "mean_residual_pt": round(float(r[jump.to_numpy()].mean()) * 100, 4) if jump.any() else None},
    }
    return out


# ---------------------------------------------------------------------------
# パイプライン
# ---------------------------------------------------------------------------


def prepare_window(conn, start: str, end: str, train_end: str):
    """窓のフィールドを読み、窓より前だけで学習した is_win ヘッドの生予測を返す。

    Returns: (field_df, raw_win_pred, train_df) — `raw` は `field_df` と同じ行順。
    """
    logger.info("窓 %s〜%s を読み出し", start, end)
    df = prod_featurize(_query(conn, FETCH_SQL, {"start": start, "end": end}))
    logger.info("  生: %d行 / %dレース", len(df), df["race_id"].nunique())

    # フィールド = 実際に出走した馬（取消・除外を除く）。中止・失格は**残す**
    df["abnormality_code"] = pd.to_numeric(df["abnormality_code"], errors="coerce")
    field = df[~df["abnormality_code"].isin([1, 2]) & df["abnormality_code"].notna()].copy()
    logger.info("  フィールド: %d行 / %dレース", len(field), field["race_id"].nunique())

    # 学習データ（窓の開始日より前だけ）
    logger.info("  学習データ読み出し（〜%s）", train_end)
    tr = prod_featurize(_query(conn, FETCH_SQL, {"start": "19000101", "end": train_end}))
    tr["abnormality_code"] = pd.to_numeric(tr["abnormality_code"], errors="coerce")
    tr = tr[tr["finish_position"].notna() & (tr["finish_position"] > 0)].reset_index(drop=True)
    logger.info("  学習: %d行 / %dレース (%s〜%s)",
                len(tr), tr["race_id"].nunique(), tr["date"].min(), tr["date"].max())
    if str(tr["date"].max()) >= str(start):
        raise SystemExit(f"🔴 学習窓が評価窓に食い込んでいる: {tr['date'].max()} >= {start}")

    field = field.reset_index(drop=True)
    raw = fit_predict_pwin(tr, field)  # field の行順を保つ（並べ替えない）
    return field, raw, tr


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--explore", nargs=2, default=["20250101", "20251231"],
                    metavar=("START", "END"))
    ap.add_argument("--confirm", nargs=2, default=["20260104", "20260801"],
                    metavar=("START", "END"))
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    conn = psycopg2.connect(_dsn())

    logger.info("過去走テーブル読み出し（PIT 用・全場・abnormality_code=0）")
    past_df = _query(conn, PAST_SQL, {"end": max(args.explore[1], args.confirm[1])})
    past_df["date"] = past_df["date"].astype(str)
    logger.info("  過去走: %d行 / %d頭", len(past_df), past_df["horse_id"].nunique())
    past = PastRuns(past_df)

    windows: dict[str, pd.DataFrame] = {}
    meta: dict[str, dict] = {}
    for name, (start, end) in (("explore", args.explore), ("confirm", args.confirm)):
        train_end = (pd.Timestamp(start) - pd.Timedelta(days=1)).strftime("%Y%m%d")
        field, raw, tr = prepare_window(conn, start, end, train_end)
        d = attach_probs(field, raw)
        d = build_pit_features(d, past)
        d["is_jump"] = d["is_jump"].astype(int)
        meta[name] = {
            "window": [start, end],
            "train_end": train_end,
            "train_rows": int(len(tr)),
            "train_races": int(tr["race_id"].nunique()),
            "train_period": [str(tr["date"].min()), str(tr["date"].max())],
            "field_rows": int(len(d)),
            "field_races": int(d["race_id"].nunique()),
        }
        windows[name] = d
    conn.close()

    # ---- 🔴 目視確認: 1レースをそのまま表示する（検証の作法） ----
    sample = windows["explore"]
    sr = sample[sample["field_n"] >= 8]["race_id"].iloc[0]
    one = sample[sample["race_id"] == sr].sort_values("p_win", ascending=False)
    print("\n" + "=" * 96)
    print(f"目視確認 1レース: race_id={sr} {one['date'].iloc[0]} "
          f"{one['course_name'].iloc[0]}{one['race_number'].iloc[0]}R {one['race_name'].iloc[0]} "
          f"(field_n={one['field_n'].iloc[0]})")
    print("=" * 96)
    print(f"{'馬番':>4} {'馬名':<20} {'p_win':>8} {'p_place':>8} {'着順':>5} {'脚質':>8} "
          f"{'脚質(rs)':>10} {'過去5走':>6}")
    for _, row in one.iterrows():
        name = str(row["horse_name"])[:18]
        pad = " " * max(0, 20 - sum(2 if ord(ch) > 0x2E80 else 1 for ch in name))
        print(f"{int(row['horse_number']) if pd.notna(row['horse_number']) else 0:>4} "
              f"{name}{pad} {row['p_win']:>8.4f} {row['p_place_harville']:>8.4f} "
              f"{int(row['finish_position']) if pd.notna(row['finish_position']) else 0:>5} "
              f"{row['runner_type']:>8} {row['running_style_pit']:>10} {int(row['n_past5']):>6}")
    print("-" * 96)
    print(f"Σp_win = {one['p_win'].sum():.6f}   (期待 1.000000)")
    print(f"Σp_place = {one['p_place_harville'].sum():.6f}   (期待 ≒ 3.0 / field_n>=8)")
    print(f"脚質内訳 = {one['runner_type'].value_counts().to_dict()}")
    print("=" * 96 + "\n")
    visual_check = {
        "race_id": int(sr),
        "date": str(one["date"].iloc[0]),
        "race": f"{one['course_name'].iloc[0]}{one['race_number'].iloc[0]}R {one['race_name'].iloc[0]}",
        "field_n": int(one["field_n"].iloc[0]),
        "sum_p_win": round(float(one["p_win"].sum()), 6),
        "sum_p_place": round(float(one["p_place_harville"].sum()), 6),
        "rows": [
            {"horse_number": int(r["horse_number"]) if pd.notna(r["horse_number"]) else None,
             "horse_name": r["horse_name"], "p_win": round(float(r["p_win"]), 4),
             "p_place_harville": round(float(r["p_place_harville"]), 4),
             "finish_position": int(r["finish_position"]) if pd.notna(r["finish_position"]) else None,
             "runner_type": r["runner_type"], "running_style_pit": r["running_style_pit"],
             "n_past5": int(r["n_past5"])}
            for _, r in one.iterrows()
        ],
    }

    # ---- 母集団の確定 ----
    results: dict = {}
    slots2: dict = {}
    cutoffs: dict | None = None
    for name in ("explore", "confirm"):
        d = windows[name]
        d = d[d["finish_position"].notna() & (d["finish_position"] > 0)].copy()
        d["finish_position"] = d["finish_position"].astype(int)

        s3 = d[d["field_n"] >= 8].copy()
        s2 = d[(d["field_n"] >= 5) & (d["field_n"] <= 7)].copy()
        meta[name]["place_slots_3_rows"] = int(len(s3))
        meta[name]["place_slots_3_races"] = int(s3["race_id"].nunique())
        meta[name]["place_slots_2_rows"] = int(len(s2))
        meta[name]["place_slots_2_races"] = int(s2["race_id"].nunique())
        meta[name]["place_slots_0_rows"] = int((d["field_n"] < 5).sum())

        s3["residual"] = (s3["finish_position"] <= 3).astype(float) - s3["p_place_harville"]
        s3["win_residual"] = (s3["finish_position"] == 1).astype(float) - s3["p_win"]
        # win-shadow: p_win 較正ずれが Harville 経由で機械的に生む複勝残差（一次近似）
        s3["excess_residual"] = s3["residual"] - (
            s3["p_place_harville"] / s3["p_win"]) * s3["win_residual"]
        if len(s2):
            s2["residual"] = (s2["finish_position"] <= 2).astype(float) - s2["p_place_harville"]
            slots2[name] = {
                "n": int(len(s2)), "n_races": int(s2["race_id"].nunique()),
                "mean_residual_pt": round(float(s2["residual"].mean()) * 100, 4),
                "note": "Harville は n<8 で『2着以内』を返すため本集計とは別物。判定に使わない",
            }

        if cutoffs is None:  # 🔴 カット点は探索窓で凍結し確認窓に流用する
            fv = s3["finish_var5"].dropna().to_numpy()
            wr = s3["win_place_ratio5"]
            wr = wr[(wr.notna()) & (wr >= 0)].to_numpy()
            cutoffs = {
                "finish_var_tertile": [float(np.quantile(fv, 1 / 3)), float(np.quantile(fv, 2 / 3))],
                "win_place_ratio_tertile": [float(np.quantile(wr, 1 / 3)), float(np.quantile(wr, 2 / 3))],
                "pwin_decile": [float(v) for v in np.quantile(s3["p_win"], np.arange(1, 10) / 10)],
                "frozen_from": "explore",
            }
            logger.info("カット点を探索窓で凍結: %s", json.dumps(
                {k: v for k, v in cutoffs.items() if k != "pwin_decile"}, ensure_ascii=False))

        s3 = assign_levels(s3, cutoffs)
        logger.info("[%s] 集計 n=%d races=%d bootstrap=%d",
                    name, len(s3), s3["race_id"].nunique(), args.bootstrap)
        results[name] = analyze(s3, args.bootstrap)

    # ---- 判定 ----
    verdict_hits = []
    for cut in CUTS:
        ex = {c["pair"]: c for c in results["explore"]["cuts"][cut]["contrasts"]}
        cf = {c["pair"]: c for c in results["confirm"]["cuts"][cut]["contrasts"]}
        for pair, c in ex.items():
            if not c["significant"]:
                continue
            c2 = cf.get(pair)
            if c2 is None:
                continue
            same_sign = (c["diff_pt"] > 0) == (c2["diff_pt"] > 0)
            verdict_hits.append({
                "cut": cut, "pair": pair,
                "explore_diff_pt": c["diff_pt"], "explore_ci95_pt": c["ci95_pt"],
                "confirm_diff_pt": c2["diff_pt"], "confirm_ci95_pt": c2["ci95_pt"],
                "confirm_significant": c2["significant"],
                "reproduced_same_sign": bool(same_sign),
                "magnitude_class": ("<1pt（実在するが小さい）" if abs(c["diff_pt"]) < 1.0
                                    else ">=1pt"),
                # 補助（判定外）: 単勝側にも同じ差があるか＝ p_win 較正ずれの疑い
                "aux_explore_win_diff_pt": c["aux_win_diff_pt"],
                "aux_explore_win_ci95_pt": c["aux_win_ci95_pt"],
                "aux_confirm_win_diff_pt": c2["aux_win_diff_pt"],
                "aux_explore_excess_diff_pt": c["aux_excess_diff_pt"],
                "aux_explore_excess_ci95_pt": c["aux_excess_ci95_pt"],
                "aux_explore_excess_significant": c["aux_excess_significant"],
                "aux_confirm_excess_diff_pt": c2["aux_excess_diff_pt"],
                "aux_interpretation": (
                    "win-shadow を引いても有意（両窓で同符号）→ 複勝側固有の構造"
                    if c["aux_excess_significant"]
                    and (c["aux_excess_diff_pt"] > 0) == (c2["aux_excess_diff_pt"] > 0)
                    else "win-shadow を引くと有意でない/再現しない → p_win 較正ずれで説明できる"
                ),
            })
    reproduced = [h for h in verdict_hits if h["reproduced_same_sign"]]
    verdict = "仮説あり" if reproduced else "仮説なし"

    payload = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "script": str(_here.relative_to(_root.parent)),
        "preregistration": "docs/jra_winplace_structure_plan_2026_09_04.md §4 Phase A",
        "design": {
            "p_win": "walk-forward is_win binary LGB（各窓の開始日より前のみで学習）"
                     "／レース内 clip(1e-9,1)→raw/total（composite.py:735-741 と同一）",
            "p_place": "src.indices.composite.CompositeIndexCalculator._harville_place_probs"
                       "（本番関数をそのまま import）",
            "runner_type": "src.indices.pace_handicap.PaceHandicapCalculator._determine_runner_type"
                           "（本番関数をそのまま import・直近10走・PIT）",
            "running_style_pit": "race_results.running_style の過去10走最頻値（PIT・裏取り用）。"
                                 "🔴 対象レース自身の running_style は結果列なので使わない",
            "target_course_filter": "r.course IN ('01'..'10')（JRA 10場のみ・FETCH_SQL）",
            "past_run_course_filter": "なし（本番 _get_past_results_batch が絞っていないため。"
                                      "地方・海外の走も脚質判定に使う）",
            "unused_dead_columns": "margin（全行 NULL）/ passing_1（44%）/ passing_2（50%）は不使用",
            "population": "place_slots=3（field_n>=8）に限定。place_slots=2 は別掲",
            "field": "abnormality_code NOT IN (1,2) の行で p_win 正規化と Harville を行い、"
                     "残差集計からのみ finish_position NULL/0 を落とす",
            "ci": f"race_id 単位クラスタ bootstrap {args.bootstrap} 反復",
            "stratification": "p_win 10分位で中心化した調整残差で水準比較",
            "cutoffs_frozen_from": "explore",
        },
        "cutoffs": cutoffs,
        "windows": meta,
        "visual_check": visual_check,
        "results": results,
        "place_slots_2_summary": slots2,
        "verdict": {
            "decision": verdict,
            "n_significant_in_explore": len(verdict_hits),
            "n_reproduced_in_confirm": len(reproduced),
            "hits": verdict_hits,
            "criteria": "探索窓で95%CIが0を跨がず、かつ確認窓で同符号 → 仮説あり",
        },
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    logger.info("保存: %s", out)
    print(f"\n判定: {verdict}（探索窓で有意 {len(verdict_hits)} 件 / 確認窓で同符号再現 {len(reproduced)} 件）")


if __name__ == "__main__":
    main()
