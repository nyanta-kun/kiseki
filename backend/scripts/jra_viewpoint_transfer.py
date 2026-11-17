"""メカウチダの「観点」を kiseki 自身に移植して推奨精度が上がるかの検証（JRA・2026-11-14）。

事前登録: ``docs/jra_viewpoint_transfer_plan_2026_11_14.md``。
**本スクリプトは事前登録を実装するだけで、腕・閾値・合格線を動かさない。**

サイト（メカウチダ）の数字は一切使わない。サイトが判断に使っている観点を
kiseki の DB から作り、kiseki の推奨が良くなるかを測る。

サブコマンド:

| コマンド | 内容 | DB |
|---|---|---|
| ``features`` | 観点の列（変わり目5列・好走条件・前走レベル）を作る | 読む |
| ``predict``  | Q2・Q3 の walk-forward で 4 腕の単勝・複勝確率を作る | 読まない |
| ``ab``       | §5 観点の特徴量 A/B（単勝 多項対数損失・複勝圏対数損失） | 読まない |
| ``market``   | 10分前オッズ・払戻・複勝EV軸の入力を取る | 読む |
| ``rule``     | §4 買い方の移植（8 腕）と比較相手 | 読まない |

🔴 すべて 2026-09-30 で止める（2026Q4 は TEST・事前登録 §3）。
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

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

import scripts.jra_lap_feature_ab as lab  # noqa: E402
import scripts.jra_place_head_ab as PH  # noqa: E402
import scripts.jra_winplace_feature_ab as ab  # noqa: E402
from scripts.jra_lap_v29_q3_confirm import assert_no_test_rows, split_train_valid  # noqa: E402
from scripts.jra_prob_scoring import harville_place, race_normalize  # noqa: E402
from scripts.jra_winplace_final_confirm import normalize_to_slots, relabel_is_placed  # noqa: E402

logger = logging.getLogger("viewpoint_transfer")

HARD_STOP = "20261001"
LEGS = (("2026Q2", "20260401", "20260630"), ("2026Q3", "20260701", "20260930"))
CHG_COLS = ["jockey_change", "surface_switch", "first_dirt", "first_course", "blinker_change"]
FORM_COLS = ["good_cond_match", "last_race_level"]
ARMS = {
    "v28": lab.V28_COLS,
    "v29": lab.LAP_COLS,
    "v29_chg": lab.LAP_COLS + CHG_COLS,
    "v29_form": lab.LAP_COLS + FORM_COLS,
}
BOOT_SEED = 20261114
EPS = 1e-6

# ---------------------------------------------------------------------------
# features
# ---------------------------------------------------------------------------

PAST_RUNS_SQL = """
SELECT r.date, r.id AS race_id, e.horse_id, e.jockey_id, e.blinker,
       r.course_name, r.surface, r.distance,
       rr.finish_position, rr.abnormality_code
FROM keiba.race_entries e
JOIN keiba.races r ON r.id = e.race_id
LEFT JOIN keiba.race_results rr ON rr.race_id = e.race_id AND rr.horse_id = e.horse_id
WHERE r.date BETWEEN %(start)s AND %(end)s
"""


def build_viewpoint_features(runs: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """対象行 (race_id, horse_id) ごとに観点の列を作る。

    すべて**対象レースより前の日付の走**だけから作る（同日の別レースも使わない）。
    `runs` は対象レース自身の行を含んでよい（そこから今回の騎手・ブリンカー・場を取る）。
    """
    runs = runs.copy()
    runs["date"] = runs["date"].astype(str)
    runs = runs.sort_values(["horse_id", "date", "race_id"]).reset_index(drop=True)
    # 走った（取消・除外でない）行だけを「過去走」とする
    ran = runs["abnormality_code"].fillna(0).isin([0]) & runs["finish_position"].notna()
    runs["ran"] = ran
    g = runs.groupby("horse_id", sort=False)

    # 直前の「走った」行の値を引き継ぐ（取消行は過去走に数えない）
    def prev_of(col: str) -> pd.Series:
        v = runs[col].where(runs["ran"])
        return v.groupby(runs["horse_id"]).transform(lambda s: s.shift(1).ffill())

    for c in ("jockey_id", "blinker", "surface", "date", "race_id"):
        runs["prev_" + c] = prev_of(c)
    has_prev = runs["prev_date"].notna()
    runs["jockey_change"] = np.where(has_prev, (runs["jockey_id"] != runs["prev_jockey_id"]).astype(float), np.nan)
    runs["surface_switch"] = np.where(has_prev, (runs["surface"] != runs["prev_surface"]).astype(float), np.nan)
    runs["blinker_change"] = np.where(
        has_prev & runs["blinker"].notna() & runs["prev_blinker"].notna(),
        (runs["blinker"].astype(float) != runs["prev_blinker"].astype(float)).astype(float),
        np.nan,
    )
    dirt_ran = (runs["surface"] == "ダ") & runs["ran"]
    prior_dirt = dirt_ran.astype(int).groupby(runs["horse_id"]).transform(lambda s: s.shift(1, fill_value=0).cumsum())
    runs["first_dirt"] = np.where(
        runs["surface"] == "ダ", np.where(has_prev, (prior_dirt == 0).astype(float), np.nan), 0.0
    )
    # 初コース: その場での過去走の数
    runs["_one"] = runs["ran"].astype(int)
    prior_course = runs.groupby(["horse_id", "course_name"], sort=False)["_one"].transform(
        lambda s: s.shift(1, fill_value=0).cumsum()
    )
    runs["first_course"] = np.where(has_prev, (prior_course == 0).astype(float), np.nan)

    # 好走条件一致: 過去の3着内で 同じ芝ダ ∧ 距離±200 ∧ 同じ場 の回数（同日以前は除く）
    gcm = np.full(len(runs), np.nan)
    for _, idx in g.indices.items():
        d = runs.loc[idx]
        dates = d["date"].to_numpy()
        top3 = (d["ran"] & (d["finish_position"] <= 3)).to_numpy()
        surf = d["surface"].to_numpy()
        dist = d["distance"].to_numpy(dtype=float)
        course = d["course_name"].to_numpy()
        for j in range(len(idx)):
            prior = dates < dates[j]
            if not prior.any():
                continue
            m = prior & top3 & (surf == surf[j]) & (np.abs(dist - dist[j]) <= 200) & (course == course[j])
            gcm[idx[j]] = float(m.sum())
    runs["good_cond_match"] = gcm

    # 前走レベル: 前走の相手のうち、前走の翌日〜今回の前日に走った馬の「1回でも3着内」率
    ranr = runs[runs["ran"]]
    by_horse = {
        h: (d["date"].to_numpy(), (d["finish_position"] <= 3).to_numpy().astype(int))
        for h, d in ranr.groupby("horse_id", sort=False)
    }
    field = ranr.groupby("race_id")["horse_id"].apply(list).to_dict()
    lrl = np.full(len(runs), np.nan)
    tgt_keys = set(zip(targets["race_id"], targets["horse_id"], strict=True))
    for i, (rid, hid, d0, prid, dt) in enumerate(
        zip(runs["race_id"], runs["horse_id"], runs["prev_date"], runs["prev_race_id"], runs["date"], strict=True)
    ):
        if (rid, hid) not in tgt_keys or pd.isna(prid):
            continue
        n_ran = n_good = 0
        for r in field.get(int(prid), []):
            if r == hid:
                continue
            ds, t3 = by_horse[r]
            lo = np.searchsorted(ds, d0, side="right")
            hi = np.searchsorted(ds, dt, side="left")
            if hi > lo:
                n_ran += 1
                n_good += int(t3[lo:hi].any())
        if n_ran >= 3:
            lrl[i] = n_good / n_ran
    runs["last_race_level"] = lrl
    keep = ["race_id", "horse_id", *CHG_COLS, *FORM_COLS]
    return targets[["race_id", "horse_id"]].merge(runs[keep], on=["race_id", "horse_id"], how="left")


def cmd_features(args: argparse.Namespace) -> None:
    df = pd.read_pickle(args.dataset)
    assert_no_test_rows(df, HARD_STOP)
    conn = psycopg2.connect(ab._dsn())
    t0 = time.time()
    runs = ab._query(conn, PAST_RUNS_SQL, {"start": "20150101", "end": "20260930"})
    conn.close()
    logger.info("過去走 %d行 (%.1fs)", len(runs), time.time() - t0)
    t0 = time.time()
    feat = build_viewpoint_features(runs, df[["race_id", "horse_id"]].drop_duplicates())
    logger.info("観点の列を作成 (%.1fs)", time.time() - t0)
    cov = {c: round(float(feat[c].notna().mean()) * 100, 1) for c in CHG_COLS + FORM_COLS}
    means = {c: round(float(feat[c].mean()), 3) for c in CHG_COLS + FORM_COLS}
    logger.info("充足(%%) %s / 平均 %s", cov, means)
    feat.to_pickle(args.out)


# ---------------------------------------------------------------------------
# predict
# ---------------------------------------------------------------------------


def cmd_predict(args: argparse.Namespace) -> None:
    df = pd.read_pickle(args.dataset)
    assert_no_test_rows(df, HARD_STOP)
    feat = pd.read_pickle(args.features)
    df = df.merge(feat, on=["race_id", "horse_id"], how="left").reset_index(drop=True)
    seeds = [int(s) for s in args.seeds.split(",")]
    out = []
    for quarter, start, end in LEGS:
        train = df[df["date"] < start]
        te0 = df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)
        tr, va, cut = split_train_valid(train, args.valid_days, start)
        tr_p, va_p = relabel_is_placed(tr), relabel_is_placed(va)
        logger.info("=== %s 学習 < %s（valid > %s）評価 %dR ===", quarter, start, cut, te0["race_id"].nunique())
        for arm, cols in ARMS.items():
            te = te0.copy()
            t0 = time.time()
            raw_w, it_w = ab.fit_predict(tr, va, te, cols, cols, seeds)
            raw_p, it_p = ab.fit_predict(tr_p, va_p, te, cols, cols, seeds)
            te["p_win"] = race_normalize(raw_w, te["race_id"])
            te["p_placed_raw"] = np.clip(raw_p, PH.EPS, 1.0 - PH.EPS)
            te["p_place"], _ = normalize_to_slots(te, "p_placed_raw")
            te["arm"], te["quarter"] = arm, quarter
            logger.info("  [%s] %d列 iters win=%s place=%s (%.1fs)", arm, len(cols), it_w, it_p, time.time() - t0)
            out.append(
                te[
                    [
                        "date",
                        "race_id",
                        "horse_id",
                        "horse_number",
                        "course_name",
                        "race_number",
                        "surface",
                        "grade",
                        "distance",
                        "place_slots",
                        "finish_position",
                        "abnormality_code",
                        "arm",
                        "quarter",
                        "p_win",
                        "p_place",
                    ]
                ]
            )
    pd.concat(out, ignore_index=True).to_pickle(args.out)


# ---------------------------------------------------------------------------
# ab
# ---------------------------------------------------------------------------


def _race_losses(ev: pd.DataFrame) -> pd.DataFrame:
    """レースごとの 単勝 多項対数損失 と 複勝圏 対数損失（レース合計）。"""
    ev = ev[ev["place_slots"] > 0]
    win = ev[ev["finish_position"] == 1].groupby("race_id")["p_win"].first()
    win_ll = -np.log(np.clip(win, EPS, 1.0))
    y = (ev["finish_position"] <= ev["place_slots"]).astype(float)
    p = np.clip(ev["p_place"], EPS, 1 - EPS)
    pl = -(y * np.log(p) + (1 - y) * np.log(1 - p))
    pl_ll = pl.groupby(ev["race_id"]).sum()
    return pd.DataFrame({"win": win_ll, "place": pl_ll}).dropna()


def _boot(d: np.ndarray, n: int, rng: np.random.Generator) -> tuple[float, float, float]:
    idx = rng.integers(0, len(d), size=(n, len(d)))
    s = d[idx].mean(axis=1)
    return float(d.mean()), float(np.percentile(s, 2.5)), float(np.percentile(s, 97.5))


def cmd_ab(args: argparse.Namespace) -> None:
    pr = pd.read_pickle(args.preds)
    rng = np.random.default_rng(BOOT_SEED)
    L = {a: _race_losses(pr[pr["arm"] == a]) for a in ARMS}
    res: dict = {"n_races": int(len(L["v29"]))}
    print(f"\n対象 {len(L['v29'])}R（Q2+Q3・walk-forward）")
    for a in ARMS:
        print(f"  {a:9s} 単勝 {L[a]['win'].mean():.5f} / 複勝圏 {L[a]['place'].mean():.5f}（レース平均）")
    for arm, base in (("v29_chg", "v29"), ("v29_form", "v29"), ("v29", "v28")):
        j = L[arm].join(L[base], lsuffix="_a", rsuffix="_b", how="inner")
        r = {}
        for m in ("win", "place"):
            mu, lo, hi = _boot((j[f"{m}_a"] - j[f"{m}_b"]).to_numpy(), args.bootstrap, rng)
            r[m] = {"delta": mu, "ci": [lo, hi]}
        ok = r["win"]["ci"][1] < 0 and r["place"]["ci"][1] < 0
        r["pass"] = bool(ok) if arm != "v29" else None
        res[f"{arm}-{base}"] = r
        flag = "" if arm == "v29" else ("  → 合格" if ok else "  → 不合格")
        print(
            f"  {arm} − {base}: 単勝 Δ={r['win']['delta']:+.5f} [{r['win']['ci'][0]:+.5f}, {r['win']['ci'][1]:+.5f}]"
            f" / 複勝圏 Δ={r['place']['delta']:+.5f} [{r['place']['ci'][0]:+.5f}, {r['place']['ci'][1]:+.5f}]{flag}"
        )
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# market
# ---------------------------------------------------------------------------

ODDS_SQL = """
WITH rc AS (
  -- 🔴 fetched_at は naive UTC。to_timestamp は（DB セッションの）JST で解釈されるので、
  --    AT TIME ZONE 'UTC' で naive UTC に直して比べる。二重に変換すると締切が 9 時間ずれ、
  --    発走直前のオッズを拾う（2026-11-14 に一度踏んだ）
  SELECT id AS race_id,
         to_timestamp(date || post_time, 'YYYYMMDDHH24MI') AT TIME ZONE 'UTC'
           - interval '10 minutes' AS cutoff
  FROM keiba.races
  WHERE date BETWEEN %(start)s AND %(end)s AND post_time ~ '^[0-9]{4}$'
)
SELECT DISTINCT ON (o.race_id, o.bet_type, o.combination)
       o.race_id, o.bet_type, o.combination, o.odds, o.fetched_at
FROM keiba.odds_history o JOIN rc ON rc.race_id = o.race_id
WHERE o.bet_type IN ('win', 'place') AND o.fetched_at <= rc.cutoff
ORDER BY o.race_id, o.bet_type, o.combination, o.fetched_at DESC
"""

PAYOUT_SQL = """
SELECT p.race_id, p.bet_type, p.combination, p.payout
FROM keiba.race_payouts p JOIN keiba.races r ON r.id = p.race_id
WHERE r.date BETWEEN %(start)s AND %(end)s AND p.bet_type IN ('win', 'place')
"""

SUBIDX_SQL = """
SELECT ci.race_id, e.horse_number, ci.composite_index, e.jvan_time_dm, e.jvan_battle_dm,
       ci.speed_index, ci.last_3f_index, ci.course_aptitude, ci.position_advantage,
       ci.jockey_index, ci.pace_index, ci.rotation_index, ci.rebound_index,
       ci.career_phase_index, ci.distance_change_index, ci.anagusa_index
FROM keiba.calculated_indices ci
JOIN keiba.race_entries e ON e.race_id = ci.race_id AND e.horse_id = ci.horse_id
JOIN keiba.races r ON r.id = ci.race_id
WHERE ci.version = 28 AND r.date BETWEEN %(start)s AND %(end)s
"""


def cmd_market(args: argparse.Namespace) -> None:
    conn = psycopg2.connect(ab._dsn())
    p = {"start": "20260401", "end": "20260930"}
    for name, sql in (("odds", ODDS_SQL), ("payouts", PAYOUT_SQL), ("subidx", SUBIDX_SQL)):
        t0 = time.time()
        d = ab._query(conn, sql, p)
        d.to_pickle(f"{args.outdir}/{name}.pkl")
        logger.info("%s %d行 (%.1fs)", name, len(d), time.time() - t0)
    conn.close()


# ---------------------------------------------------------------------------
# rule
# ---------------------------------------------------------------------------


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def prepare_rule_frame(pr: pd.DataFrame, odds: pd.DataFrame, pay: pd.DataFrame) -> pd.DataFrame:
    """1 腕の予測に 10分前オッズ・人気・市場確率・払戻を付ける。全馬にオッズのあるレースだけ残す。"""
    d = pr[pr["abnormality_code"].fillna(0).isin([0]) & pr["finish_position"].notna()].copy()
    d = d[(d["surface"] != "障") & (d["place_slots"] > 0)]
    o = odds.copy()
    o["horse_number"] = pd.to_numeric(o["combination"], errors="coerce")
    o = o.dropna(subset=["horse_number"])
    o["odds"] = o["odds"].astype(float)
    ow = o[o["bet_type"] == "win"][["race_id", "horse_number", "odds"]].rename(columns={"odds": "win_odds"})
    op = o[o["bet_type"] == "place"][["race_id", "horse_number", "odds"]].rename(columns={"odds": "place_odds"})
    d = d.merge(ow, on=["race_id", "horse_number"], how="left").merge(op, on=["race_id", "horse_number"], how="left")
    full = d.groupby("race_id")[["win_odds", "place_odds"]].apply(
        lambda x: x.notna().all().all() and (x > 0).all().all()
    )
    d = d[d["race_id"].isin(full[full].index)].copy()
    inv = 1 / d["win_odds"]
    d["mk_win"] = inv / inv.groupby(d["race_id"]).transform("sum")
    d["mk_place"] = harville_place(d, "mk_win")
    d["pop"] = d.groupby("race_id")["win_odds"].rank(method="first")
    py = pay.copy()
    py["horse_number"] = pd.to_numeric(py["combination"], errors="coerce")
    pw = py[py["bet_type"] == "win"][["race_id", "horse_number", "payout"]].rename(columns={"payout": "pay_win"})
    pp = py[py["bet_type"] == "place"][["race_id", "horse_number", "payout"]].rename(columns={"payout": "pay_place"})
    d = d.merge(pw, on=["race_id", "horse_number"], how="left").merge(pp, on=["race_id", "horse_number"], how="left")
    d["pay_win"] = d["pay_win"].fillna(0) / 100.0
    d["pay_place"] = d["pay_place"].fillna(0) / 100.0
    d["hit_place"] = (d["pay_place"] > 0).astype(int)
    return d.reset_index(drop=True)


def upset_level(d: pd.DataFrame) -> pd.Series:
    """kiseki 版の荒れやすさ: 1・2番人気の Σ p_place − Σ 市場複勝圏。三分位で 低/中/高（上位1/3 = 低）。"""
    top2 = d[d["pop"] <= 2]
    diff = top2.groupby("race_id")["p_place"].sum() - top2.groupby("race_id")["mk_place"].sum()
    q1, q2 = diff.quantile([1 / 3, 2 / 3])
    lv = pd.Series(np.where(diff >= q2, "低", np.where(diff < q1, "高", "中")), index=diff.index)
    return d["race_id"].map(lv)


def apply_site_rule(d: pd.DataFrame, a: float, t: float, gate: bool) -> pd.DataFrame:
    """§4.2 のルールで 1 レース 1 頭を選ぶ。"""
    z = a * _logit(d["p_place"].to_numpy()) + (1 - a) * _logit(d["mk_place"].to_numpy())
    q = 1 / (1 + np.exp(-z))
    qs = pd.Series(q, index=d.index)
    q = qs * d["place_slots"] / qs.groupby(d["race_id"]).transform("sum")
    ev = q.clip(upper=1 - 1e-6) * d["place_odds"]
    cand = (ev >= t) & (d["pop"] != 1) & (d["grade"].fillna("").isin(["G1", "G2", "G3", "重賞"]) == False)  # noqa: E712
    if gate:
        cand &= ~((d["upset"] == "低") & (d["pop"] >= 3))
    c = d[cand].copy()
    c["ev"] = ev[cand]
    return c.sort_values(["race_id", "pop"]).groupby("race_id").head(1)


def wp_ratio_rule(d: pd.DataFrame) -> pd.DataFrame:
    """単複比ルール（memory jra_place_wp_ratio_rule・候補 A）。"""
    r = d.copy()
    r["pp_rank"] = r.groupby("race_id")["p_place"].rank(ascending=False, method="first")
    c = r[
        (r["place_odds"] >= 3.0)
        & (r["place_odds"] < 4.0)
        & (r["pp_rank"] <= 5)
        & (r["win_odds"] / r["place_odds"] <= 3.5)
    ]
    return c.sort_values(["race_id", "p_place"], ascending=[True, False]).groupby("race_id").head(1)


def place_ev_axis(d: pd.DataFrame, sub: pd.DataFrame) -> pd.DataFrame:
    """本番の複勝EV軸（`betting.place_ev`）を同じ入力で呼ぶ。外部バッジ（netkeiba・吉馬）は欠損のまま（近似）。"""
    from src.betting.place_ev import SUB_INDEX_COLUMNS, get_place_ev_model

    model = get_place_ev_model()
    s = sub.copy()
    s["horse_number"] = s["horse_number"].astype(int)
    x = d.merge(s, on=["race_id", "horse_number"], how="left")
    picks = []
    for rid, g in x.groupby("race_id"):
        hs = []
        for r in g.itertuples():
            h = {
                "horse_number": int(r.horse_number),
                "win_odds": r.win_odds,
                "place_odds": r.place_odds,
                "composite_index": r.composite_index,
                "win_probability": r.p_win,
                "place_probability": r.p_place,
                "surface": r.surface,
                "distance": r.distance,
                "anagusa_rank": None,
                "nb_ave_rank": None,
                "km_rank": None,
                "jvan_time_dm": r.jvan_time_dm,
                "jvan_battle_dm": r.jvan_battle_dm,
            }
            for c in SUB_INDEX_COLUMNS:
                v = getattr(r, c, None)
                h[c] = None if v is None or pd.isna(v) else float(v)
            hs.append(h)
        pk = model.pick_race(hs, len(g))
        if pk is not None:
            picks.append((rid, pk["horse_number"]))
    k = pd.DataFrame(picks, columns=["race_id", "horse_number"])
    return d.merge(k, on=["race_id", "horse_number"], how="inner")


def summarize(sel: pd.DataFrame, base: pd.DataFrame, n_races: int, n_boot: int, rng: np.random.Generator) -> dict:
    """回収率（開催日クラスタ・ブートストラップ）・的中率・オッズ帯リフト・選択率。"""
    if len(sel) == 0:
        return {"n": 0}
    sel = sel.copy()
    sel["mix"] = 0.3 * sel["pay_win"] + 0.7 * sel["pay_place"]
    days = sel["date"].unique()
    by_day = sel.groupby("date").agg(n=("pay_place", "size"), pl=("pay_place", "sum"), mix=("mix", "sum"))
    idx = rng.integers(0, len(days), size=(n_boot, len(days)))
    arr = by_day.loc[days].to_numpy()
    tot = arr[idx].sum(axis=1)
    roi_pl, roi_mix = tot[:, 1] / tot[:, 0], tot[:, 2] / tot[:, 0]
    # 同じ複勝オッズ帯（0.5 刻み）の全馬に対する的中率のリフト
    bands = np.floor(base["place_odds"] * 2) / 2
    band_rate = base.groupby(bands)["hit_place"].mean()
    exp_hit = band_rate.reindex(np.floor(sel["place_odds"] * 2) / 2).to_numpy()
    return {
        "n": int(len(sel)),
        "select_rate": round(len(sel) / n_races, 4),
        "hit_place": round(float(sel["hit_place"].mean()), 4),
        "lift_vs_odds_band": round(float(sel["hit_place"].mean() - np.nanmean(exp_hit)), 4),
        "roi_place": round(float(sel["pay_place"].mean()), 4),
        "roi_place_ci": [round(float(np.percentile(roi_pl, 2.5)), 4), round(float(np.percentile(roi_pl, 97.5)), 4)],
        "roi_mix": round(float(sel["mix"].mean()), 4),
        "roi_mix_ci": [round(float(np.percentile(roi_mix, 2.5)), 4), round(float(np.percentile(roi_mix, 97.5)), 4)],
        "mean_place_odds": round(float(sel["place_odds"].mean()), 2),
        "by_quarter_roi_place": {q: round(float(g["pay_place"].mean()), 4) for q, g in sel.groupby("quarter")},
    }


def cmd_rule(args: argparse.Namespace) -> None:
    pr = pd.read_pickle(args.preds)
    odds = pd.read_pickle(f"{args.mdir}/odds.pkl")
    pay = pd.read_pickle(f"{args.mdir}/payouts.pkl")
    sub = pd.read_pickle(f"{args.mdir}/subidx.pkl")
    rng = np.random.default_rng(BOOT_SEED)
    res: dict = {}
    for arm in ("v29", "v28"):
        d = prepare_rule_frame(pr[pr["arm"] == arm], odds, pay)
        d["upset"] = upset_level(d)
        n_races = d["race_id"].nunique()
        out: dict = {"n_races": int(n_races), "n_days": int(d["date"].nunique())}
        print(
            f"\n===== 腕 {arm}: 母集団 {n_races}R / {d['date'].nunique()}日（Q2+Q3・障害除く・全馬に10分前オッズ） ====="
        )
        print(
            f"{'ルール':28s} {'点':>5s} {'選択率':>6s} {'複的中':>6s} {'リフト':>7s} {'複回収':>6s} {'95%CI':>17s} {'単3複7':>6s} {'95%CI':>17s} {'平均複':>5s}"
        )

        def show(name: str, sel: pd.DataFrame) -> None:
            s = summarize(sel, d, n_races, args.bootstrap, rng)
            out[name] = s
            if s["n"] == 0:
                print(f"{name:28s} 0")
                return
            print(
                f"{name:28s} {s['n']:5d} {s['select_rate']:6.3f} {s['hit_place']:6.3f} {s['lift_vs_odds_band']:+7.3f}"
                f" {s['roi_place']:6.3f} [{s['roi_place_ci'][0]:.3f},{s['roi_place_ci'][1]:.3f}]"
                f" {s['roi_mix']:6.3f} [{s['roi_mix_ci'][0]:.3f},{s['roi_mix_ci'][1]:.3f}] {s['mean_place_odds']:5.2f}"
            )

        for a in (0.5, 1.0):
            for t in (1.0, 1.1):
                for gate in (True, False):
                    show(f"site a={a} t={t} gate={'on' if gate else 'off'}", apply_site_rule(d, a, t, gate))
        show("比較: 単複比ルール", wp_ratio_rule(d))
        show("比較: 複勝EV軸（近似）", place_ev_axis(d, sub))
        # 比較③: 1番人気を除いた人気順（2番人気）を毎レース
        show("比較: 毎R 2番人気", d[d["pop"] == 2])
        res[arm] = out
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=2))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sp = p.add_subparsers(dest="cmd", required=True)
    f = sp.add_parser("features")
    f.add_argument("--dataset", required=True)
    f.add_argument("--out", required=True)
    pr = sp.add_parser("predict")
    pr.add_argument("--dataset", required=True)
    pr.add_argument("--features", required=True)
    pr.add_argument("--out", required=True)
    pr.add_argument("--seeds", default="42,123,456")
    pr.add_argument("--valid-days", type=int, default=90)
    a = sp.add_parser("ab")
    a.add_argument("--preds", required=True)
    a.add_argument("--bootstrap", type=int, default=4000)
    a.add_argument("--json")
    m = sp.add_parser("market")
    m.add_argument("--outdir", required=True)
    r = sp.add_parser("rule")
    r.add_argument("--preds", required=True)
    r.add_argument("--mdir", required=True)
    r.add_argument("--bootstrap", type=int, default=4000)
    r.add_argument("--json")
    args = p.parse_args()
    {"features": cmd_features, "predict": cmd_predict, "ab": cmd_ab, "market": cmd_market, "rule": cmd_rule}[args.cmd](
        args
    )


if __name__ == "__main__":
    main()
