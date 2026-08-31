"""直接対決(H2H)・ライン位置別/レース種別別/時間帯別 実績の A/B 検証（2026-08-31 新設）。

ユーザー要望:
  「自前の履歴から作れる新しい特徴量を足して A/B 検証してほしい。
    ① 直接対決（同一レースに出た2選手の過去の着順比較）
    ② ライン位置別の実績（単騎/先頭/番手/3番手で走ったときの過去成績）
    ③ レース種別別の実績（予選/準決勝/決勝/特選 等）
    ④ 時間帯別の実績（昼/ナイター/ミッドナイト）」

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔴 先行研究（測る前に読むこと）— `docs/prediction-factors.md`
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
**① は既に一度実装され、本番採用が撤回されている**（2026-07-28）。
`add_h2h_features_wt()` は `src/preprocessing/feature_wt.py` に**現存する**が
`FEATURE_COLS_WT` には入っていない。当時の結末:

  - AUC・的中率は S1/S7/S9 の3戦略とも**一貫して改善**していた
  - しかし honest walk-forward の **ROI** が S1 −79.5pt / S9 −126.0pt と割れ、
    共有モデルのため部分採用ができずユーザー判断で全面撤回

つまり①は「モデル指標では効くが商品 ROI では割れた」という決着で、
**本スクリプトが測る指標（AUC / 指数1位3着内率）では改善が既知**。
したがって①の結果は「再発見」であって新情報ではない。新情報は②③④、
および①に**相手ごとの勝率の平均/最小**（ユーザー提案・当時は未実装）を
足した拡張版が上積みを持つかどうか。

関連する既存の否定結果:
  - 決まり手の自前 point-in-time 集計（`exp_basic_elements_ab.py`・2026-08-04）
    → ΔAUC ほぼ 0・3着内率は両窓とも負。`b_rate_90`/`s_rate_90`/`style_enc` と冗長
  - レース単位の集約（rp_mean 等）→ AUC は上がるが 1位3着内率が窓で符号反転
    ＝**AUC だけを見て採否を決めてはいけない**
  - 節内成績4本（`exp_form_features_ab.py`）→ 使われたのは `cup_mean_order_n` 1本だけ

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔴🔴 point-in-time の保証
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
全特徴は `wt_entries` 全履歴（2022-12〜）を **(race_date, start_at, race_key) 昇順**に
1パス走査し、「そのレースより**前**に確定した結果」だけで作る。

  - ②③④ は「同じ (player_id, バケット) の過去行の累積和」から**自分自身の行を
    差し引いた**排他累積（`cumsum() - 自分の値`）で作る。自分の結果は構造的に入らない
  - ① は各レースの**特徴を先に確定させてから**そのレースの結果で対戦表を更新する
    （既存 `add_h2h_features_wt` と同じ順序）
  - 未確定・DNF（`finish_order < 1`）は分子にも分母にも入れない
  - `--selftest` に4本の検証を入れてある（下記 `selftest()` の docstring 参照）

⚠️ **同日の自分より前のレースは「過去」として数える**（既存 H2H 実装と同じ）。
   `add_rolling_features_wt` 系の `rolling("90D", closed="left")` は同日を丸ごと
   捨てるが、ここでは start_at で厳密に順序づけできるため使う。
   発走前ライブ予測でも同じ情報しか使わないので train/serve skew は生じない。

⚠️ **履歴は 2022-12-01 始まり**。それ以前のキャリアは存在しないので、
   ベテランでも「通算」は最大 4 年弱。これは全アームに共通の制約。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ 母数の薄い率をそのまま使わない（ユーザー指摘・競艇モーター2連率と同型）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
②③④ は**2段の階層平滑化**を掛ける。生の率は一切特徴にしない。

    P0        = 定数の事前確率（3着内 0.43 ≒ 3/7 / 1着 0.143 ≒ 1/7）
                ← 学習データから作らないので、ここから漏れる情報はゼロ
    overall_p = (通算3着内数 + K0 * P0) / (通算出走数 + K0)      K0 = 10
    rate_B    = (バケットB内3着内数 + K * overall_p) / (B内出走数 + K)   K = 10
    lift_B    = rate_B − overall_p

`lift_B` を明示的に持たせるのは、**決定木は特徴どうしの引き算を作れない**ため。
「その位置/種別/時間帯が本人の平均と比べて得意か」は差でしか表現できない。
母数 `n_B` も特徴として渡す（モデルが自分で信頼度を測れるように）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
測り方（`exp_form_features_ab.py` と同一・勝手に変えない）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  4窓 × 5seed・deterministic=True・baseline は現行 `FEATURE_COLS_WT`（66特徴）
  w1/w2 = 掃引窓 / **w3/w4 = 確認窓**（採否は w3/w4 で決める）
  記録は ΔAUC / Δ1位勝率 / Δ1位3着内 / Δ二軸 の4指標すべて

採用ライン（先に固定・事後に動かさない。`exp_form_features_ab.py` と同一）:
  **確認窓 w3/w4 の両方で符号一致** かつ **平均 Δ1位3着内 ≥ +0.30pt**

Usage:
    export KEIRIN_DB_URL=...
    PYTHONPATH=. .venv/bin/python scripts/exp_h2h_line_records_ab.py --selftest
    PYTHONPATH=. .venv/bin/python scripts/exp_h2h_line_records_ab.py --build-cache
    PYTHONPATH=. .venv/bin/python scripts/exp_h2h_line_records_ab.py --windows w3
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.database import get_connection  # noqa: E402
from src.preprocessing.feature_wt import (  # noqa: E402
    FEATURE_COLS_WT,
    TARGET_COL_WT,
    build_features_wt,
    load_raw_data_wt,
)

TRAIN_FROM = "2024-04-01"
HISTORY_FROM = "2022-12-01"        # wt_entries の実データ開始
WINDOWS = {
    "w1": ("2026-04-13", "2026-07-15"),   # 掃引窓
    "w2": ("2026-01-01", "2026-04-12"),   # 掃引窓
    "w3": ("2025-10-01", "2025-12-31"),   # 確認窓
    "w4": ("2025-07-01", "2025-09-30"),   # 確認窓
}
SEEDS = [42, 101, 202, 303, 404]

CACHE = Path(os.environ.get(
    "EXP_H2H_CACHE",
    "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/"
    "19c049e5-ea85-4b67-af6f-4efddbeea937/scratchpad/exp_h2h_feat.pkl"))

# --- 平滑化定数（事前に決めて動かさない） -----------------------------------
P0_TOP3 = 3.0 / 7.0       # 7車立てが 85% を占めるので 3/7
P0_WIN = 1.0 / 7.0
K0_CAREER = 10.0          # 通算 → 定数事前確率への縮小
K_BUCKET = 10.0           # バケット → 通算への縮小

# --- 特徴列 -----------------------------------------------------------------
H2H_COLS = ["h2h_win_rate", "h2h_n_total", "h2h_net_norm",
            "h2h_opp_mean_rate", "h2h_opp_min_rate"]
LP_COLS = ["lp_n", "lp_top3_rate", "lp_win_rate", "lp_top3_lift", "lp_win_lift"]
RT_COLS = ["rtc_n", "rtc_top3_rate", "rtc_win_rate", "rtc_top3_lift", "rtc_win_lift"]
TOD_COLS = ["tod_n", "tod_top3_rate", "tod_win_rate", "tod_top3_lift", "tod_win_lift"]

ARMS: dict[str, list[str]] = {
    "base": [],
    "+h2h": H2H_COLS,
    "+linepos": LP_COLS,
    "+racetype": RT_COLS,
    "+tod": TOD_COLS,
    "+all": H2H_COLS + LP_COLS + RT_COLS + TOD_COLS,
}


# ---------------------------------------------------------------------------
# 履歴の読み込み
# ---------------------------------------------------------------------------
def _engine():
    db_url = os.environ.get("KEIRIN_DB_URL")
    if not db_url:
        raise RuntimeError("KEIRIN_DB_URL が未設定です")
    from sqlalchemy import create_engine
    return create_engine(db_url)


def _read_sql(sql: str) -> pd.DataFrame:
    """⚠️ get_connection() を pandas に直接渡すと RealDictCursor のせいで
    全行が「列名の文字列」になる（PG環境で無言で壊れる）。必ず engine 経由。"""
    eng = _engine()
    df = pd.read_sql_query(sql, eng)
    eng.dispose()
    if len(df) and str(df.iloc[0, 0]) == df.columns[0]:
        raise RuntimeError("SQL取得が壊れています（列名が値として入っている）")
    return df


def load_history() -> pd.DataFrame:
    """特徴生成の材料になる全履歴（学習窓より前も含む）。"""
    return _read_sql(
        "SELECT e.race_key, e.player_id, e.finish_order, e.line_pos, e.line_size, "
        "       r.race_date, r.start_at, r.race_type "
        "FROM keirin.wt_entries e JOIN keirin.wt_races r ON e.race_key=r.race_key "
        f"WHERE r.race_date >= '{HISTORY_FROM}'")


# ---------------------------------------------------------------------------
# バケット定義（履歴側と対象側で必ず同じ関数を通す）
# ---------------------------------------------------------------------------
def line_pos_bucket(line_pos: pd.Series, line_size: pd.Series) -> pd.Series:
    """単騎 / ライン先頭 / 番手 / 3番手以降。

    ⚠️ 単騎（line_size <= 1）は先頭に数えない。`add_line_leader_features_wt` と
       同じ扱いにしてある（そちらの docstring 参照）。line_size が欠損なら単騎。
    """
    lp = pd.to_numeric(line_pos, errors="coerce")
    ls = pd.to_numeric(line_size, errors="coerce")
    out = pd.Series("third", index=lp.index, dtype=object)
    out[lp <= 2] = "deputy"
    out[lp <= 1] = "leader"
    out[(ls <= 1) | ls.isna() | lp.isna()] = "solo"
    return out


def race_type_bucket(race_type: pd.Series) -> pd.Series:
    """レース種別を7バケットへ。`add_race_type_features_wt` のキーワードに揃える。

    ⚠️ 判定順に意味がある。「初特選」は特選・「チャレンジ予選」は予選・
       「準決勝」は決勝ではなく準決勝に落ちること。
    """
    s = race_type.fillna("").astype(str)
    out = pd.Series("other", index=s.index, dtype=object)
    out[s.str.contains("一般")] = "ippan"
    out[s.str.contains("選抜")] = "senbatsu"
    out[s.str.contains("特選")] = "tokusen"
    out[s.str.contains("予選")] = "heat"
    out[s.str.contains("決勝")] = "final"
    out[s.str.contains("準決")] = "semi"      # 「準決勝」は決勝を部分一致で拾うので最後
    return out


def tod_bucket(start_at: pd.Series) -> pd.Series:
    """発走時刻(JST)から モーニング / デイ / ナイター / ミッドナイト。

    `wt_races.start_at` は unix epoch 秒の**文字列**。JST = UTC+9。
    実測分布（102,916R）: 8時 2,252 / 9時 5,107 / …… / 21時 8,486 / 23時 5,389。
    """
    ep = pd.to_numeric(start_at, errors="coerce")
    hour = ((ep + 9 * 3600) // 3600) % 24
    out = pd.Series("unknown", index=ep.index, dtype=object)
    out[hour >= 0] = "midnight"
    out[hour < 21] = "night"
    out[hour < 15] = "day"
    out[hour < 10] = "morning"
    out[ep.isna()] = "unknown"
    return out


# ---------------------------------------------------------------------------
# ② ③ ④ バケット別実績（排他累積 = point-in-time）
# ---------------------------------------------------------------------------
def _bucket_records(hist: pd.DataFrame, bucket: pd.Series, prefix: str,
                    cols: list[str]) -> pd.DataFrame:
    """(player_id, bucket) 別の「そのレースより前」の実績を階層平滑化して返す。

    🔴 自分の行を含めない仕組み: `cumsum() - 自分の値` の排他累積。
       shift(1) と違い、同一グループ内の並び替えの影響も受けない
       （累積は時刻昇順で作る）。
    """
    h = hist.copy()
    fin = pd.to_numeric(h["finish_order"], errors="coerce")
    ok = (fin >= 1) & fin.notna()
    h["_n"] = ok.astype(float)                       # 確定した1走
    h["_t3"] = (ok & (fin <= 3)).astype(float)
    h["_w1"] = (ok & (fin == 1)).astype(float)
    h["_b"] = bucket.values

    # --- 通算（player 単位） ---
    g = h.groupby("player_id", sort=False)
    n_all = g["_n"].cumsum() - h["_n"]
    t3_all = g["_t3"].cumsum() - h["_t3"]
    w1_all = g["_w1"].cumsum() - h["_w1"]

    ov_t3 = (t3_all + K0_CAREER * P0_TOP3) / (n_all + K0_CAREER)
    ov_w1 = (w1_all + K0_CAREER * P0_WIN) / (n_all + K0_CAREER)

    # --- バケット単位 ---
    gb = h.groupby(["player_id", "_b"], sort=False)
    n_b = gb["_n"].cumsum() - h["_n"]
    t3_b = gb["_t3"].cumsum() - h["_t3"]
    w1_b = gb["_w1"].cumsum() - h["_w1"]

    r_t3 = (t3_b + K_BUCKET * ov_t3) / (n_b + K_BUCKET)
    r_w1 = (w1_b + K_BUCKET * ov_w1) / (n_b + K_BUCKET)

    out = pd.DataFrame({
        "race_key": h["race_key"].values,
        "player_id": h["player_id"].values,
        cols[0]: n_b.values,
        cols[1]: r_t3.values,
        cols[2]: r_w1.values,
        cols[3]: (r_t3 - ov_t3).values,
        cols[4]: (r_w1 - ov_w1).values,
    })
    out.attrs["prefix"] = prefix
    return out


# ---------------------------------------------------------------------------
# ① 直接対決（H2H）
# ---------------------------------------------------------------------------
def _h2h_records(hist: pd.DataFrame) -> pd.DataFrame:
    """レース単位1パスの H2H。既存 `add_h2h_features_wt` の3列に2列を足した拡張版。

    - h2h_win_rate      : 対戦履歴がある相手への通算勝率（プール・履歴無しは 0.5）
    - h2h_n_total       : 対戦回数の合計（カバレッジ）
    - h2h_net_norm      : (先着−後着) 合計 / 出走頭数
    - h2h_opp_mean_rate : **相手ごと**の勝率（Laplace k=2・0.5 へ縮小）の平均
    - h2h_opp_min_rate  : 同・最小（n>=3 の相手のみ。該当なしは 0.5）
                          ← 「1人だけ大の苦手が居る」をプール勝率は消してしまう

    🔴 特徴を先に全員分確定 → そのあとで当該レースの結果を対戦表へ反映する順序。
       逆にすると自分のレースの結果が自分の特徴に入る。
    """
    h = hist.copy()
    h["_fin"] = pd.to_numeric(h["finish_order"], errors="coerce")
    order = (h.groupby("race_key")
             .agg(_d=("race_date", "first"), _s=("_sort", "first"))
             .sort_values(["_d", "_s"]).index.tolist())

    win: dict = defaultdict(int)   # (a,b) a<b → a が先着した回数
    cnt: dict = defaultdict(int)
    groups = {rk: g for rk, g in h.groupby("race_key", sort=False)}
    rows = []

    for rk in order:
        g = groups[rk]
        pids = g["player_id"].tolist()
        fins = g["_fin"].tolist()
        ne = float(len(pids)) or 1.0

        for p in pids:
            wins = matches = net = 0
            rates = []
            rates3 = []
            for q in pids:
                if q == p:
                    continue
                key = (p, q) if p < q else (q, p)
                n = cnt[key]
                if n == 0:
                    continue
                w_ab = win[key]
                w_p = w_ab if p < q else (n - w_ab)
                matches += n
                wins += w_p
                net += w_p - (n - w_p)
                r = (w_p + 2 * 0.5) / (n + 2)      # Laplace k=2 → 0.5
                rates.append(r)
                if n >= 3:
                    rates3.append(r)
            rows.append((
                rk, p,
                (wins / matches) if matches > 0 else 0.5,
                float(matches),
                float(net) / ne,
                float(np.mean(rates)) if rates else 0.5,
                float(min(rates3)) if rates3 else 0.5,
            ))

        # --- 更新（この時点より後の特徴にだけ効く） ---
        fin_ok = [(p, f) for p, f in zip(pids, fins)
                  if f is not None and f == f and 1 <= f <= 99]
        for i in range(len(fin_ok)):
            pa, fa = fin_ok[i]
            for j in range(i + 1, len(fin_ok)):
                pb, fb = fin_ok[j]
                if fa == fb:
                    continue
                key = (pa, pb) if pa < pb else (pb, pa)
                cnt[key] += 1
                if (fa < fb) == (pa < pb):
                    win[key] += 1

    return pd.DataFrame(rows, columns=["race_key", "player_id"] + H2H_COLS)


# ---------------------------------------------------------------------------
# 組み立て
# ---------------------------------------------------------------------------
def build_extra_features(df: pd.DataFrame, hist: pd.DataFrame | None = None,
                         verbose: bool = True) -> pd.DataFrame:
    """df（build_features_wt 済み）に H2H / ライン位置 / 種別 / 時間帯の実績を付ける。"""
    if hist is None:
        if verbose:
            print("履歴読み込み ...", flush=True)
        hist = load_history()
    hist = hist.copy()
    # 🔴 時刻順の唯一の正本。start_at 欠損は当日の末尾へ回す（未来を先に見ない）。
    hist["_sort"] = pd.to_numeric(hist["start_at"], errors="coerce").fillna(1 << 62)
    hist = hist.sort_values(["race_date", "_sort", "race_key", "player_id"],
                            kind="mergesort").reset_index(drop=True)

    if verbose:
        print(f"  履歴 {len(hist):,}行 "
              f"({hist['race_date'].min()}〜{hist['race_date'].max()})", flush=True)

    parts = []
    for bucket, prefix, cols in (
        (line_pos_bucket(hist["line_pos"], hist["line_size"]), "lp", LP_COLS),
        (race_type_bucket(hist["race_type"]), "rtc", RT_COLS),
        (tod_bucket(hist["start_at"]), "tod", TOD_COLS),
    ):
        if verbose:
            vc = bucket.value_counts()
            print(f"  {prefix}: " + " / ".join(f"{k}={v:,}" for k, v in vc.items()),
                  flush=True)
        parts.append(_bucket_records(hist, bucket, prefix, cols))

    if verbose:
        print("  h2h 1パス走査 ...", flush=True)
    parts.append(_h2h_records(hist))

    out = df.copy()
    for p in parts:
        out = out.merge(p, on=["race_key", "player_id"], how="left")

    # 履歴に無い行（理論上は無い）は中立値で埋める
    for c in H2H_COLS:
        out[c] = out[c].fillna(0.5 if c.endswith("rate") else 0.0)
    for c in LP_COLS + RT_COLS + TOD_COLS:
        if c.endswith("_n"):
            out[c] = out[c].fillna(0.0)
        elif c.endswith("lift"):
            out[c] = out[c].fillna(0.0)
        else:
            out[c] = out[c].fillna(P0_TOP3 if "top3" in c else P0_WIN)
    return out


# ---------------------------------------------------------------------------
# 評価（exp_form_features_ab.py と同一）
# ---------------------------------------------------------------------------
def race_metrics(test: pd.DataFrame, prob: np.ndarray, ne_map: dict) -> dict:
    """7車レースの 指数1位の勝率・3着内率、および二軸（上位2車とも3着内）。"""
    t = test.copy()
    t["p"] = prob
    win = top3 = two = n = 0
    for rk, g in t.groupby("race_key"):
        if ne_map.get(rk) != 7 or len(g) != 7:
            continue
        fo = pd.to_numeric(g["finish_order"], errors="coerce")
        if (fo >= 1).sum() < 3:
            continue
        order = g["p"].values.argsort()[::-1]
        f1 = fo.iloc[order[0]]
        f2 = fo.iloc[order[1]]
        f1 = 99 if pd.isna(f1) or f1 < 1 else f1
        f2 = 99 if pd.isna(f2) or f2 < 1 else f2
        n += 1
        win += 1 if f1 == 1 else 0
        top3 += 1 if f1 <= 3 else 0
        two += 1 if (f1 <= 3 and f2 <= 3) else 0
    if not n:
        return {"win": 0.0, "top3": 0.0, "two": 0.0, "n": 0}
    return {"win": win / n, "top3": top3 / n, "two": two / n, "n": n}


def run_window(df: pd.DataFrame, name: str, test_from: str, test_to: str,
               arms: dict) -> dict:
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score

    with get_connection() as conn:
        ne_map = dict(conn.execute(
            "SELECT race_key, n_entries FROM wt_races WHERE race_date BETWEEN ? AND ?",
            (test_from, test_to)))
    train = df[(df["race_date"] >= TRAIN_FROM) & (df["race_date"] < test_from)]
    test = df[(df["race_date"] >= test_from) & (df["race_date"] <= test_to)]
    kind = "確認窓" if name in ("w3", "w4") else "掃引窓"
    print(f"\n######## {name}({kind}) test={test_from}〜{test_to}  "
          f"train {len(train):,}行 / test {len(test):,}行 ########", flush=True)

    res: dict = {}
    for arm, extra in arms.items():
        cols = FEATURE_COLS_WT + extra
        acc = {"auc": [], "win": [], "top3": [], "two": []}
        n = 0
        m = None
        for seed in SEEDS:
            m = lgb.LGBMClassifier(
                objective="binary", n_estimators=500, learning_rate=0.05,
                num_leaves=31, min_child_samples=20, subsample=0.8,
                colsample_bytree=0.8, random_state=seed,
                deterministic=True, force_row_wise=True, verbose=-1)
            m.fit(train[cols], train[TARGET_COL_WT])
            p = m.predict_proba(test[cols])[:, 1]
            acc["auc"].append(roc_auc_score(test[TARGET_COL_WT], p))
            r = race_metrics(test, p, ne_map)
            for k in ("win", "top3", "two"):
                acc[k].append(r[k])
            n = r["n"]
        res[arm] = {k: float(np.mean(v)) for k, v in acc.items()}
        res[arm]["auc_sd"] = float(np.std(acc["auc"]))
        print(f"== {arm} ({len(cols)}特徴) ==  n={n}")
        print(f"   AUC {res[arm]['auc']:.5f} ±{res[arm]['auc_sd']:.5f} / "
              f"1位勝率 {res[arm]['win']*100:.2f}% / "
              f"1位3着内 {res[arm]['top3']*100:.2f}% / 二軸 {res[arm]['two']*100:.2f}%",
              flush=True)
        if extra and m is not None:
            imp = pd.Series(m.feature_importances_, index=cols)
            rk = imp.rank(ascending=False, method="min").astype(int)
            for c in extra:
                print(f"     {c:<20} imp={imp[c]:5d}  順位 {rk[c]}/{len(cols)}")
            res[arm]["imp_rank"] = {c: int(rk[c]) for c in extra}
    return res


# ---------------------------------------------------------------------------
# point-in-time セルフテスト
# ---------------------------------------------------------------------------
def selftest() -> int:
    """point-in-time であることの検査（4本）。

    T1 未来非依存 : 対象レースより**後**の全レースの着順を書き換えても、対象
                    レースの特徴が1ビットも変わらないこと
    T2 自己排他   : 対象行**自身**の着順を書き換えても自分の特徴が変わらないこと
    T3 過去依存   : 対象レースより**前**のレースの着順を書き換えたら特徴が変わること
                    （＝T1が「そもそも何も見ていない」ことによる自明な合格でない）
    T4 既存実装一致: h2h の共有3列が `add_h2h_features_wt()` と一致すること
    T5 同日順序   : 同日の**前**のレースは数え、同日の**後**のレースは数えないこと
    """
    from src.preprocessing.feature_wt import add_h2h_features_wt

    def mk(fins: dict[str, list[int]]) -> pd.DataFrame:
        """race_key -> finish_order のリスト（選手は p0..p3 固定・4車）。"""
        rows = []
        meta = {
            "R1": ("2025-01-01", 1000, "予選"),
            "R2": ("2025-01-01", 2000, "予選"),      # 同日・R1 の後
            "R3": ("2025-01-02", 1000, "決勝"),      # 対象レース
            "R4": ("2025-01-03", 1000, "予選"),      # 未来
            "R5": ("2025-01-04", 1000, "予選"),      # 未来
        }
        for rk, fo in fins.items():
            d, s, rt = meta[rk]
            for i, f in enumerate(fo):
                rows.append({
                    "race_key": rk, "player_id": f"p{i}", "finish_order": f,
                    "line_pos": i + 1, "line_size": 4,
                    "race_date": d, "start_at": str(s), "race_type": rt,
                })
        return pd.DataFrame(rows)

    base = {"R1": [1, 2, 3, 4], "R2": [4, 3, 2, 1], "R3": [1, 2, 3, 4],
            "R4": [2, 1, 4, 3], "R5": [3, 4, 1, 2]}
    feat_cols = H2H_COLS + LP_COLS + RT_COLS + TOD_COLS

    def run(fins) -> pd.DataFrame:
        h = mk(fins)
        tgt = h[["race_key", "player_id"]].copy()
        out = build_extra_features(tgt, hist=h, verbose=False)
        return out.set_index(["race_key", "player_id"])[feat_cols].sort_index()

    ok = True
    ref = run(base)
    r3 = ref.loc["R3"]

    # T1: 未来を書き換える
    fut = dict(base, R4=[4, 3, 2, 1], R5=[1, 2, 3, 4])
    d1 = run(fut).loc["R3"]
    if not np.allclose(r3.values.astype(float), d1.values.astype(float)):
        diff = (r3 - d1).abs().max()
        print(f"❌ T1 未来非依存: 対象レースの特徴が変化した\n{diff[diff > 0]}")
        ok = False
    else:
        print("✅ T1 未来非依存: R4/R5 を書き換えても R3 の特徴は不変")

    # T2: 対象行自身を書き換える
    slf = dict(base, R3=[4, 3, 2, 1])
    d2 = run(slf).loc["R3"]
    if not np.allclose(r3.values.astype(float), d2.values.astype(float)):
        diff = (r3 - d2).abs().max()
        print(f"❌ T2 自己排他: 自分の着順で特徴が変化した\n{diff[diff > 0]}")
        ok = False
    else:
        print("✅ T2 自己排他: R3 自身の着順を変えても R3 の特徴は不変")

    # T3: 過去を書き換える（変わらなければ「何も見ていない」＝検査が無意味）
    past = dict(base, R1=[4, 3, 2, 1])
    d3 = run(past).loc["R3"]
    changed = [c for c in feat_cols
               if not np.allclose(r3[c].astype(float).values,
                                  d3[c].astype(float).values)]
    if len(changed) < 8:
        print(f"❌ T3 過去依存: 変化した列が {len(changed)} 本しかない {changed}")
        ok = False
    else:
        print(f"✅ T3 過去依存: R1 を書き換えると R3 の特徴 {len(changed)}/"
              f"{len(feat_cols)} 本が変化（＝過去は確かに見ている）")

    # T4: 既存 add_h2h_features_wt との一致
    h = mk(base)
    tgt = h[["race_key", "player_id", "race_date"]].copy()
    exist = add_h2h_features_wt(tgt, history=h).set_index(
        ["race_key", "player_id"]).sort_index()
    mine = run(base)
    bad = []
    for c in ["h2h_win_rate", "h2h_n_total", "h2h_net_norm"]:
        # 既存は「対戦履歴なし」を NaN→0.5 補完、本実装は直接 0.5。同値になる。
        if not np.allclose(exist[c].astype(float).values,
                           mine[c].astype(float).values):
            bad.append(c)
    if bad:
        print(f"❌ T4 既存実装一致: {bad} が不一致")
        for c in bad:
            print(pd.DataFrame({"exist": exist[c], "mine": mine[c]}).to_string())
        ok = False
    else:
        print("✅ T4 既存実装一致: h2h 共有3列が add_h2h_features_wt と一致")

    # T5: 同日順序（R2 を書き換えると R3 が変わる / R2 は R1 を見て R3 は R1+R2 を見る）
    sameday = dict(base, R2=[1, 2, 3, 4])
    sd = run(sameday)
    d5 = sd.loc["R3"]
    ch5 = [c for c in feat_cols
           if not np.allclose(r3[c].astype(float).values,
                              d5[c].astype(float).values)]
    r2_ref = ref.loc["R2"]
    d5_r2 = sd.loc["R2"]
    r2_same = np.allclose(r2_ref[H2H_COLS].values.astype(float),
                          d5_r2[H2H_COLS].values.astype(float))
    if not ch5:
        print("❌ T5 同日順序: 同日の前のレース(R2)が翌日(R3)に反映されていない")
        ok = False
    elif not r2_same:
        print("❌ T5 同日順序: R2 自身の着順が R2 の特徴へ漏れている")
        ok = False
    else:
        print(f"✅ T5 同日順序: 同日の前のレースは数え({len(ch5)}列が変化)、"
              "自分自身は数えない")

    # T6: 実データでのサニティ（初出走の n は 0 か）
    print("\n-- 実データ サニティ --")
    hist = load_history()
    hist["_sort"] = pd.to_numeric(hist["start_at"], errors="coerce").fillna(1 << 62)
    hist = hist.sort_values(["race_date", "_sort", "race_key", "player_id"],
                            kind="mergesort").reset_index(drop=True)
    first = hist.groupby("player_id").head(1)[["race_key", "player_id"]]
    sub = build_extra_features(first, hist=hist, verbose=False)
    bad_n = {c: float((sub[c] > 0).mean()) for c in
             ["lp_n", "rtc_n", "tod_n", "h2h_n_total"]}
    if any(v > 0 for v in bad_n.values()):
        print(f"❌ T6 初出走の母数が 0 でない: {bad_n}")
        ok = False
    else:
        print(f"✅ T6 初出走 {len(first):,}人の母数はすべて 0 "
              "（自分の初戦を数えていない）")

    print("\n" + ("✅ すべて合格" if ok else "❌ 失敗あり"))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
def build_cache() -> pd.DataFrame:
    print("データ読み込み ...", flush=True)
    max_to = max(t for _, t in WINDOWS.values())
    raw = load_raw_data_wt(min_date="2023-06-01", max_date=max_to)
    df = build_features_wt(raw)
    print(f"  特徴量構築 {len(df):,}行 / baseline {len(FEATURE_COLS_WT)}特徴",
          flush=True)
    df = build_extra_features(df)
    keep = (["race_key", "player_id", "race_date", "finish_order", TARGET_COL_WT]
            + FEATURE_COLS_WT + ARMS["+all"])
    df = df[[c for c in dict.fromkeys(keep) if c in df.columns]]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_pickle(CACHE)
    print(f"  キャッシュ保存 {CACHE} ({len(df):,}行 {len(df.columns)}列)", flush=True)
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default="w1,w2,w3,w4")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--build-cache", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())

    if args.build_cache or not CACHE.exists():
        df = build_cache()
        if args.build_cache:
            return
    else:
        print(f"キャッシュ読み込み {CACHE}", flush=True)
        df = pd.read_pickle(CACHE)

    arms = {k: v for k, v in ARMS.items() if k in args.arms.split(",")}
    if "base" not in arms:
        arms = {"base": [], **arms}

    out = {}
    for w in args.windows.split(","):
        out[w] = run_window(df, w, *WINDOWS[w], arms=arms)

    print("\n" + "=" * 78)
    print("== 差分（各アーム − base）==")
    for arm in arms:
        if arm == "base":
            continue
        for w, r in out.items():
            d_auc = r[arm]["auc"] - r["base"]["auc"]
            d_t3 = (r[arm]["top3"] - r["base"]["top3"]) * 100
            d_w = (r[arm]["win"] - r["base"]["win"]) * 100
            d_2 = (r[arm]["two"] - r["base"]["two"]) * 100
            kind = "確認" if w in ("w3", "w4") else "掃引"
            print(f"{arm:<10} {w}({kind})  ΔAUC{d_auc:+.5f}  Δ勝{d_w:+.2f}pt  "
                  f"Δ3着内{d_t3:+.2f}pt  Δ二軸{d_2:+.2f}pt")
    print("\n採用ライン（事前固定）: 確認窓 w3/w4 の両方で符号一致 かつ "
          "その平均 Δ1位3着内 ≥ +0.30pt")


if __name__ == "__main__":
    main()
