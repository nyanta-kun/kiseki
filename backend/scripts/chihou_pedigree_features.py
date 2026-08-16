"""地方競馬 血統特徴（point-in-time の産駒成績）。

## 🔴 結論: 不採用（2026-08-16 検証済み・台帳 17.7）

**walk-forward 2年（271,338頭 / 25,744レース）で改善ゼロ。**
人気薄×指数5位内の複勝圏率 18.39% → 18.45%（+0.06pt / +0.2σ）、
再現率 27.80% → 27.76%。機序から効くはずのキャリア0-2走（+0.13pt / 0.2σ）・
2〜3歳（+0.11pt / 0.2σ）でも動かない。

単変量では有意（父の過去実績 上位25% 11.48% vs 下位25% 10.04%・3.0σ）なのに
モデルに入れると消える ＝ **馬自身の戦績が既に持っている情報**だった。
`fetch_hist` 由来の特徴と共線で、増分がない。

⚠️ **単変量の有意性を「モデルへの増分」と読み替えないこと。** 今回はそれで
1本まるごと実装している。次に同じ判断をするときは、まず既存特徴に対する
残差で測ること。

このモジュールは**再検証を安くするために残してある**（`--pedigree` で A/B できる）。
血統を作り直すなら、産駒成績の集計ではなく **母系の深さ・インブリード**など
戦績と共線でない情報を入れること。

## なぜ血統か（着手時の見立て）

台帳 17.2 のとおり、詰まっているのは精度ではなく**再現率**である。
実際に複勝圏へ来た人気薄のうち、指数が5位以内に置けていたのは 27.8% しかない。
重み付け（17.3）でも閾値でも動かない以上、**モデルが見ていない情報を足す**しかない。

血統は地方のモデルが一度も見たことのない軸で、out-of-time で有意に効く
（台帳 17.5: 父の過去実績 上位25% 11.48% vs 下位25% 10.04%・+1.44pt / 3.0σ）。

## どこから取るか

🔴 **UmaConn は BLOD（血統）を配信していない**（2026-08-16 実機確認・台帳 17.5）。
`chihou.pedigrees` が 0 件なのは取り込み経路の不具合ではなくデータ源が空だったため。

代わりに **中央の血統マスタ `keiba.pedigrees` と突合する**:

    chihou.horses.umaconn_code  ↔  keiba.horses.jravan_code   （血統登録番号・同一体系）

出走馬ベースで **91.5%** をカバーする。取れない 8.5% に偏りは無い
（父あり/なしで 平均人気 5.90 vs 5.91・6番人気以下率 52.6% vs 52.4%）ので、
欠損は 0 埋めしてよい。

⚠️ `keiba.pedigrees.sire_line`（父系統ラベル）は地方馬の **23.3%** しか埋まらない。
系統ラベルではなく**父ごとの実績集計**を使うこと。

## point-in-time の担保

集計は必ず**そのレースの日付より前**の産駒成績だけで作る。
`merge_asof(..., allow_exact_matches=False)` が「同日を含まない直近」を引くので、
同じ日の他レースの結果も混入しない。

⚠️ **同日除外は緩めないこと。** 地方は同一開催日に同じ種牡馬の産駒が複数走る。
同日を含めると「その日の結果を見てその日を予測する」ことになる。
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# モデルに渡す血統特徴。
PEDIGREE_FEATURES: list[str] = [
    "sire_top3_rate_pit",        # 父の産駒 複勝率（縮約済み）
    "sire_upset_top3_rate_pit",  # 父の産駒が6番人気以下のときの複勝率（縮約済み）
    "sire_dist_top3_rate_pit",   # 父 × 距離帯 の複勝率（縮約済み）
    "bms_top3_rate_pit",         # 母父の産駒 複勝率（縮約済み）
    "sire_n_runs_log_pit",       # 父の産駒 出走数 log1p（信頼度）
]

# 縮約の強さ。この頭数ぶんだけ全体平均へ引き戻す。
# 地方は種牡馬の裾が長く（214頭が80走以上・残りは少数）、生の率をそのまま渡すと
# 少数産駒の父のノイズを学習してしまう。
SHRINK_K: float = 50.0
SHRINK_K_DIST: float = 30.0

# 「人気薄」の線。台帳の注目馬条件と揃える。
UPSET_POP_RANK: int = 6

# 距離帯。地方の番組は 1400 前後に山があるので3分割で足りる。
DIST_BINS: list[float] = [0, 1300, 1700, np.inf]
DIST_LABELS: list[int] = [0, 1, 2]

# 地方馬と中央の血統マスタは **血統登録番号で直接つながる**。
# `chihou.horses.umaconn_code` と `keiba.horses.jravan_code` は同一体系で、
# どちらも 100% 充足している（実測 2026-08-16: 20,489 頭が一致）。
#
# 🔴 **名前で突合してはいけない。** 2026-08-16 に一度やって誤った記述を残した:
#   - `horses.birthday` は **両テーブルとも 100% が空文字**。`name + birthday` で
#     結合しても `'' = ''` が常に真になり、**実質は名前だけの一致**になる
#   - 馬名は世代をまたいで再利用されるので、名前一致では候補が複数出る
#     （地方馬 20,692 頭のうち 520 頭で候補2頭以上）
#   - 当時は「父が一意に決まる馬だけ採用」で誤りを避けていた。結果として
#     登録番号版と父が **20,090 頭すべてで一致**したため A/B の結論は変わらなかったが、
#     たまたま助かっただけで、キーとしては壊れている
PEDIGREE_QUERY = """
    SELECT ch.id AS horse_id, p.sire, p.sire_of_dam AS bms
    FROM chihou.horses ch
    JOIN keiba.horses kh ON kh.jravan_code = ch.umaconn_code
    JOIN keiba.pedigrees p ON p.horse_id = kh.id
    WHERE p.sire IS NOT NULL
"""

# 産駒成績の履歴。集計の材料なので**全期間**を取る（対象期間で切らない）。
SIRE_HISTORY_QUERY = """
    SELECT r.date, s.horse_id, r.distance,
           s.finish_position, s.win_popularity
    FROM chihou.race_results s
    JOIN chihou.races r ON r.id = s.race_id
    WHERE r.course <> '83'
      AND s.finish_position IS NOT NULL
      AND COALESCE(s.abnormality_code, 0) = 0
"""


def _read(conn, sql: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(sql)
    df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    cur.close()
    return df


def fetch_pedigree(conn) -> pd.DataFrame:
    """chihou.horses.id → 父・母父 の対応表を返す。

    Returns:
        `horse_id` / `sire` / `bms` の DataFrame。
    """
    ped = _read(conn, PEDIGREE_QUERY)
    if ped["horse_id"].duplicated().any():
        raise ValueError("血統表に horse_id の重複がある（結合で行が増える）")
    logger.info("血統: %d 頭に父を対応付け", len(ped))
    return ped


def _dist_band(distance: pd.Series) -> pd.Series:
    return pd.cut(
        pd.to_numeric(distance, errors="coerce"),
        bins=DIST_BINS, labels=DIST_LABELS,
    ).astype("float")


def _cum_by(hist: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """キー単位で「その日まで（当日含む）」の累計を作る。

    `merge_asof(allow_exact_matches=False)` で引くと当日は除外されるので、
    結果として **その日より前** の累計になる。
    """
    g = (
        hist.groupby([*keys, "date"], observed=True)
        .agg(n=("top3", "size"), s=("top3", "sum"))
        .reset_index()
        .sort_values("date")
    )
    g[["cum_n", "cum_s"]] = g.groupby(keys, observed=True)[["n", "s"]].cumsum()
    return g[[*keys, "date", "cum_n", "cum_s"]]


def build_pit_tables(conn, ped: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """point-in-time 集計の元表を作る。

    Args:
        conn: DB 接続。
        ped: `fetch_pedigree()` の戻り値。

    Returns:
        `sire` / `sire_upset` / `sire_dist` / `bms` / `global` の集計表。
    """
    hist = _read(conn, SIRE_HISTORY_QUERY)
    hist["date"] = pd.to_datetime(hist["date"], format="%Y%m%d")
    hist["top3"] = (pd.to_numeric(hist["finish_position"], errors="coerce") <= 3).astype(int)
    hist["pop"] = pd.to_numeric(hist["win_popularity"], errors="coerce")
    hist["band"] = _dist_band(hist["distance"])
    hist = hist.merge(ped, on="horse_id", how="left")
    logger.info(
        "産駒成績 %d 行 / 父が付いた割合 %.1f%%",
        len(hist), 100.0 * hist["sire"].notna().mean(),
    )

    sire_h = hist[hist["sire"].notna()]
    bms_h = hist[hist["bms"].notna()]
    upset_h = sire_h[sire_h["pop"] >= UPSET_POP_RANK]
    dist_h = sire_h[sire_h["band"].notna()]

    # 全体平均も point-in-time で持つ（縮約の prior に使う）
    gl = (
        hist.groupby("date", observed=True)
        .agg(n=("top3", "size"), s=("top3", "sum"))
        .reset_index().sort_values("date")
    )
    gl[["cum_n", "cum_s"]] = gl[["n", "s"]].cumsum()
    gl_up = (
        hist[hist["pop"] >= UPSET_POP_RANK].groupby("date", observed=True)
        .agg(n=("top3", "size"), s=("top3", "sum"))
        .reset_index().sort_values("date")
    )
    gl_up[["cum_n", "cum_s"]] = gl_up[["n", "s"]].cumsum()

    return {
        "sire": _cum_by(sire_h, ["sire"]),
        "sire_upset": _cum_by(upset_h, ["sire"]),
        "sire_dist": _cum_by(dist_h, ["sire", "band"]),
        "bms": _cum_by(bms_h, ["bms"]),
        "global": gl[["date", "cum_n", "cum_s"]],
        "global_upset": gl_up[["date", "cum_n", "cum_s"]],
    }


def _asof(
    target: pd.DataFrame, table: pd.DataFrame, by: list[str] | None, suffix: str
) -> pd.DataFrame:
    """対象行に「その日より前」の累計を貼る。"""
    left = target.sort_values("date")
    right = table.sort_values("date")
    # merge_asof は on / by キーの dtype 完全一致を要求する。datetime の分解能も
    # 揃える必要がある（pandas 3 では us と s が混ざる）。
    left["date"] = left["date"].astype("datetime64[ns]")
    right["date"] = right["date"].astype("datetime64[ns]")
    # DB 由来は object、groupby を通った側は StringDtype になりうるので object へ揃える。
    for col in by or []:
        if not pd.api.types.is_numeric_dtype(left[col]) or not pd.api.types.is_numeric_dtype(
            right[col]
        ):
            left[col] = left[col].astype(object)
            right[col] = right[col].astype(object)
    out = pd.merge_asof(
        left, right, on="date", by=by,
        direction="backward", allow_exact_matches=False,   # ← 同日を含めない
    )
    return out.rename(columns={"cum_n": f"n_{suffix}", "cum_s": f"s_{suffix}"})


def add_pedigree_features(
    df: pd.DataFrame, ped: pd.DataFrame, pit: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    """特徴量済みの DataFrame へ血統特徴を足す。

    Args:
        df: `horse_id` / `date` / `distance` を持つ DataFrame。
        ped: `fetch_pedigree()` の戻り値。
        pit: `build_pit_tables()` の戻り値。

    Returns:
        `PEDIGREE_FEATURES` を追加した DataFrame（行順・行数は入力と同じ）。
    """
    if ped["horse_id"].duplicated().any():
        # ここを通すと merge で行が増え、下流の代入が黙ってずれる
        raise ValueError("ped に horse_id の重複がある")
    work = df.copy()
    work["_row"] = np.arange(len(work))
    work["date"] = pd.to_datetime(work["date"], format="%Y%m%d")
    work["band"] = _dist_band(work["distance"])
    work = work.merge(ped, on="horse_id", how="left")

    def _shrunk(num, den, prior, k: float) -> np.ndarray:
        n = np.nan_to_num(np.asarray(den, dtype=float), nan=0.0)
        s = np.nan_to_num(np.asarray(num, dtype=float), nan=0.0)
        return (s + k * np.asarray(prior, dtype=float)) / (n + k)

    g = _asof(work[["_row", "date"]], pit["global"], None, "gl")
    g_up = _asof(work[["_row", "date"]], pit["global_upset"], None, "glup")
    prior = (g["s_gl"] / g["n_gl"]).fillna(0.25)
    prior_up = (g_up["s_glup"] / g_up["n_glup"]).fillna(0.10)
    prior = prior.set_axis(g["_row"]).sort_index()
    prior_up = prior_up.set_axis(g_up["_row"]).sort_index()

    a = _asof(work[["_row", "date", "sire"]], pit["sire"], ["sire"], "sire").sort_values("_row")
    b = _asof(
        work[["_row", "date", "sire"]], pit["sire_upset"], ["sire"], "up"
    ).sort_values("_row")
    d = _asof(
        work[["_row", "date", "sire", "band"]], pit["sire_dist"], ["sire", "band"], "dist"
    ).sort_values("_row")
    m = _asof(work[["_row", "date", "bms"]], pit["bms"], ["bms"], "bms").sort_values("_row")

    out = df.copy()
    out["sire_top3_rate_pit"] = _shrunk(
        a["s_sire"].values, a["n_sire"].values, prior.values, SHRINK_K
    )
    out["sire_upset_top3_rate_pit"] = _shrunk(
        b["s_up"].values, b["n_up"].values, prior_up.values, SHRINK_K
    )
    out["sire_dist_top3_rate_pit"] = _shrunk(
        d["s_dist"].values, d["n_dist"].values, prior.values, SHRINK_K_DIST
    )
    out["bms_top3_rate_pit"] = _shrunk(
        m["s_bms"].values, m["n_bms"].values, prior.values, SHRINK_K
    )
    out["sire_n_runs_log_pit"] = np.log1p(
        np.nan_to_num(np.asarray(a["n_sire"].values, dtype=float), nan=0.0)
    )
    if len(out) != len(df):
        raise ValueError(f"行数が変わった: {len(df)} → {len(out)}")
    return out
