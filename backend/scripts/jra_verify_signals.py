"""JRA 表示シグナル(バッジ/信頼度/期待度)の妥当性検証

地方の scripts/chihou_verify_signals.py / chihou_model_compare.py と同じ方法論で、
JRA の各表示シグナルが OOS で主張通りに機能しているか(tier別 勝率/複勝/ROI が単調か、
較正が合っているか)を検証する。

検証作法:
  - OOS 時系列ホールドアウト(train: 〜2025-06 / test: 2025-07〜)
  - 単勝ROI は drop1(最高配当の的中を1つ除外) + ブートストラップ95%CI を併記
  - 「主張ROI」と「実測ROI(test)」を並べ、過学習/陳腐化を可視化
  - test だけでなく full(全期間) も出して n を確保しつつ test での再現性を見る

検証対象シグナル:
  ① jra_buy_signal           (buy/caution/pass)            レース1位馬の単ROI
  ② jra_horse_purchase_signal(super_buy/buy/watch)         馬個別の単ROI
  ③ is_sweet_spot (JRA)      (odds≥10 ∧ EV∈[1.2,5.0] ∧ バッジ ∧ k≤2)
  ④ dm_signals 7タグ         各タグの 勝率/複勝/単ROI
  ⑤ anagusa_rank A/B/C       外部ピックの 単/複ROI
  ⑥ 外部指数穴馬 (nb/km)     external_dark_horse の的中/ROI
  ⑦ confidence_rank / recommend_rank (地方と共有・JRA未検証の疑い)  1位馬の勝率単調性
  ⑧ v26 win_probability 較正 (softmax・未較正の疑い)  decile別 予測vs実測 + ECE

使い方:
  cd backend
  .venv/bin/python scripts/jra_verify_signals.py
  .venv/bin/python scripts/jra_verify_signals.py --start 20230501 --train-end 20250630 --test-start 20250701
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

from src.indices.buy_signal import (  # noqa: E402
    SWEET_SPOT_MAX_EV,
    SWEET_SPOT_MIN_EV,
    is_sweet_spot,
    jra_buy_signal,
    jra_horse_purchase_signal,
)
from src.indices.confidence import (  # noqa: E402
    calculate_race_confidence,
    calculate_recommend_rank,
)
from src.indices.dm_signals import compute_dm_signals  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jra_verify")

V26_VERSION = 26

# JRA 2桁コード → sekito course_code (recommender._JRA_TO_SEKITO と同一)
_JRA_TO_SEKITO: dict[str, str] = {
    "01": "JSPK", "02": "JHKD", "03": "JFKS", "04": "JNGT", "05": "JTOK",
    "06": "JNKY", "07": "JCKO", "08": "JKYO", "09": "JHSN", "10": "JKKR",
}

BASE_QUERY = """
SELECT
    ci.race_id,
    ci.horse_id,
    r.date,
    r.course,
    r.course_name,
    r.race_number,
    r.surface,
    r.distance,
    r.head_count,
    re.horse_number,
    re.jvan_time_dm,
    re.jvan_battle_dm,
    ci.composite_index,
    ci.win_probability,
    ci.place_probability,
    rr.finish_position,
    rr.win_odds,
    rr.place_odds,
    rr.win_popularity
FROM keiba.calculated_indices ci
JOIN keiba.races r ON r.id = ci.race_id
JOIN keiba.race_entries re
    ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN keiba.race_results rr
    ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
WHERE ci.version = %(ver)s
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
  AND r.head_count >= 5
  AND r.date BETWEEN %(start)s AND %(end)s
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND rr.finish_position IS NOT NULL
  AND rr.win_odds IS NOT NULL AND rr.win_odds > 0
ORDER BY r.date, ci.race_id, re.horse_number
"""


def _pnum(raw) -> float | None:
    if raw is None:
        return None
    import re
    s = str(raw).strip()
    if s in ("-", "", "0"):
        return None
    m = re.search(r"\d+", s)
    return float(m.group()) if m else None


def fetch_base(conn, start: str, end: str) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(BASE_QUERY, {"ver": V26_VERSION, "start": start, "end": end})
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    for c in ("composite_index", "win_probability", "place_probability",
              "finish_position", "win_odds", "place_odds", "win_popularity",
              "jvan_time_dm", "jvan_battle_dm", "distance", "head_count",
              "horse_number", "race_number"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    logger.info("base取得: %d行 %dレース (%s〜%s)", len(df), df["race_id"].nunique(), start, end)
    return df


def fetch_external(conn, start: str, end: str) -> dict[tuple, dict[int, dict]]:
    """sekito.netkeiba/kichiuma/anagusa → (date_str, sekito_code, race_no)→{horse_no:{...}}。"""
    s = f"{start[:4]}-{start[4:6]}-{start[6:]}"
    e = f"{end[:4]}-{end[4:6]}-{end[6:]}"
    cur = conn.cursor()

    cur.execute(
        "SELECT date, course_code, race_no, horse_no, idx_course, idx_ave "
        "FROM sekito.netkeiba WHERE date BETWEEN %s AND %s", (s, e))
    nb: dict[tuple, dict[int, tuple]] = {}
    for d, cc, rn, hn, ic, ia in cur.fetchall():
        nb.setdefault((str(d), cc, int(rn)), {})[int(hn)] = (_pnum(ic), _pnum(ia))

    cur.execute(
        "SELECT date, course_code, race_no, horse_no, sp_score "
        "FROM sekito.kichiuma WHERE date BETWEEN %s AND %s", (s, e))
    km: dict[tuple, dict[int, float]] = {}
    for d, cc, rn, hn, sp in cur.fetchall():
        if sp is not None:
            km.setdefault((str(d), cc, int(rn)), {})[int(hn)] = float(sp)

    cur.execute(
        "SELECT date, course_code, race_no, horse_no, rank "
        "FROM sekito.anagusa WHERE date BETWEEN %s AND %s", (s, e))
    ag: dict[tuple, dict[int, str]] = {}
    for d, cc, rn, hn, rk in cur.fetchall():
        if rk in ("A", "B", "C"):
            ag.setdefault((str(d), cc, int(rn)), {})[int(hn)] = rk
    cur.close()

    def _rank(m: dict[int, float]) -> dict[int, int]:
        return {hn: i + 1 for i, hn in enumerate(sorted(m, key=lambda h: m[h], reverse=True))}

    out: dict[tuple, dict[int, dict]] = {}
    for key in set(nb) | set(km) | set(ag):
        nbk = nb.get(key, {})
        nb_course = {hn: v[0] for hn, v in nbk.items() if v[0] is not None}
        nb_ave = {hn: v[1] for hn, v in nbk.items() if v[1] is not None}
        nb_cr, nb_ar, km_r = _rank(nb_course), _rank(nb_ave), _rank(km.get(key, {}))
        agk = ag.get(key, {})
        allh = set(nb_course) | set(nb_ave) | set(km.get(key, {})) | set(agk)
        out[key] = {hn: {
            "nb_course_rank": nb_cr.get(hn),
            "nb_ave_rank": nb_ar.get(hn),
            "km_rank": km_r.get(hn),
            "anagusa_rank": agk.get(hn),
        } for hn in allh}
    logger.info("外部指数: %dレース分", len(out))
    return out


def annotate(df: pd.DataFrame, ext: dict) -> pd.DataFrame:
    """全馬にシグナル(順位/EV/購入signal/dm_signals/sweet_spot/buy_signal/recommend_rank)を付与。"""
    parts = []
    for rid, g in df.groupby("race_id", sort=False):
        g = g.sort_values("composite_index", ascending=False).reset_index(drop=True).copy()
        n = len(g)
        date = str(g["date"].iloc[0])
        course = g["course"].iloc[0]
        course_name = g["course_name"].iloc[0]
        surface = g["surface"].iloc[0]
        distance = g["distance"].iloc[0]
        head_count = int(g["head_count"].iloc[0]) if pd.notna(g["head_count"].iloc[0]) else n
        race_no = int(g["race_number"].iloc[0]) if pd.notna(g["race_number"].iloc[0]) else None
        ext_key = (f"{date[:4]}-{date[4:6]}-{date[6:]}", _JRA_TO_SEKITO.get(course), race_no)
        ext_race = ext.get(ext_key, {})

        # composite 順位
        g["composite_rank"] = np.arange(1, n + 1)
        # 単勝オッズ人気順 (昇順=1人気)
        g["odds_rank"] = g["win_odds"].rank(method="first").astype(int)

        # 外部指数 / anagusa を馬番で付与
        for col in ("nb_course_rank", "nb_ave_rank", "km_rank", "anagusa_rank"):
            g[col] = g["horse_number"].map(
                lambda hn: ext_race.get(int(hn), {}).get(col) if pd.notna(hn) else None
            )

        # gap 指標
        comp = g["composite_index"].tolist()
        gap_1_2 = (comp[0] - comp[1]) if n >= 2 else None
        top2_t3_gap = (comp[1] - comp[2]) if n >= 3 else None

        # DM シグナル (compute_dm_signals は SimpleNamespace を in-place 更新)
        objs = [SimpleNamespace(
            horse_number=int(r.horse_number) if pd.notna(r.horse_number) else -1,
            composite_index=float(r.composite_index) if pd.notna(r.composite_index) else 0.0,
            jvan_time_dm=float(r.jvan_time_dm) if pd.notna(r.jvan_time_dm) else None,
            jvan_battle_dm=float(r.jvan_battle_dm) if pd.notna(r.jvan_battle_dm) else None,
            anagusa_rank=r.anagusa_rank,
            dm_signals=None,
        ) for r in g.itertuples()]
        odds_map = {int(r.horse_number): float(r.win_odds)
                    for r in g.itertuples() if pd.notna(r.horse_number) and pd.notna(r.win_odds)}
        pop_map = {int(r.horse_number): int(r.odds_rank)
                   for r in g.itertuples() if pd.notna(r.horse_number)}
        compute_dm_signals(objs, popularity_map=pop_map, win_odds_map=odds_map,
                           course_name=course_name, surface=surface, distance=distance)
        dm_by_hn = {o.horse_number: (o.dm_signals or []) for o in objs}
        g["dm_signals"] = g["horse_number"].map(lambda hn: dm_by_hn.get(int(hn), []) if pd.notna(hn) else [])

        # 購入シグナル (馬個別)
        psig, sweet, evs = [], [], []
        for r in g.itertuples():
            rank = int(r.composite_rank)
            ps = jra_horse_purchase_signal(
                rank=rank,
                top2_t3_gap=top2_t3_gap if rank <= 2 else None,
                win_odds=float(r.win_odds) if pd.notna(r.win_odds) else None,
            )
            psig.append(ps)
            ev = (float(r.win_probability) * float(r.win_odds)
                  if pd.notna(r.win_probability) and pd.notna(r.win_odds) else None)
            evs.append(ev)
            ss = is_sweet_spot(
                win_odds=float(r.win_odds) if pd.notna(r.win_odds) else None,
                win_probability=float(r.win_probability) if pd.notna(r.win_probability) else None,
                composite_rank=rank,
                dm_signals=r.dm_signals,
                purchase_signal=ps,
                anagusa_rank=r.anagusa_rank,
                nb_course_rank=r.nb_course_rank,
                nb_ave_rank=r.nb_ave_rank,
                km_rank=r.km_rank,
            )
            sweet.append(ss)
        g["purchase_signal"] = psig
        g["ev_win"] = evs
        g["is_sweet_spot"] = sweet
        # k≤2 取消 (本番 races.py と同じ)
        if sum(sweet) >= 3:
            g["is_sweet_spot"] = False

        # レースレベル: buy_signal / confidence / recommend_rank
        top_odds = float(g["win_odds"].iloc[0]) if pd.notna(g["win_odds"].iloc[0]) else None
        g["buy_signal"] = jra_buy_signal(int(distance) if pd.notna(distance) else 0, top_odds)
        wp_list = [float(x) for x in g["win_probability"].tolist() if pd.notna(x)]
        conf = calculate_race_confidence(comp, head_count, wp_list or None)
        g["confidence_rank"] = conf["rank"]
        g["confidence_label"] = conf["label"]
        g["recommend_rank"] = calculate_recommend_rank(conf["score"], conf.get("win_prob_top"), top_odds)
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


# ---------------------------------------------------------------------------
# 集計ヘルパ
# ---------------------------------------------------------------------------
def _roi_ci(sub: pd.DataFrame, rng: np.random.Generator, n_boot: int = 2000) -> dict:
    n = len(sub)
    if n == 0:
        return {"n": 0, "win": 0.0, "plc": 0.0, "roi": 0.0, "drop1": 0.0, "lo": 0.0, "hi": 0.0}
    fp = sub["finish_position"].values
    odds = sub["win_odds"].values
    win = (fp == 1)
    payout = np.where(win, odds, 0.0)
    roi = payout.sum() / n
    drop1 = (payout.sum() - payout.max()) / max(n - 1, 1) if payout.max() > 0 else roi
    boot = [rng.choice(payout, size=n, replace=True).mean() for _ in range(n_boot)]
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return {"n": n, "win": win.mean() * 100, "plc": (fp <= 3).mean() * 100,
            "roi": roi, "drop1": drop1, "lo": lo, "hi": hi}


def tier_table(df: pd.DataFrame, key: str, order: list, title: str,
               rng: np.random.Generator, claims: dict | None = None) -> None:
    # 注: keiba.race_results.place_odds は 1-3着馬のみ記録(選択バイアス)のため
    #     複勝ROI は算出不可。複勝率(finish<=3)のみ妥当。
    print(f"\n--- {title} ---")
    hdr = f"  {'tier':<14}{'n':>6}{'勝率':>7}{'複勝率':>7}{'単ROI':>7}{'drop1':>7}{'95%CI':>15}"
    if claims:
        hdr += f"{'主張単ROI':>9}"
    print(hdr)
    for t in order:
        s = df[df[key] == t]
        st = _roi_ci(s, rng)
        star = " ★" if st["lo"] > 1.0 else (" ◯" if st["roi"] > 1.0 else "")
        line = (f"  {str(t):<14}{st['n']:>6}{st['win']:>6.1f}%{st['plc']:>6.1f}%"
                f"{st['roi']:>7.3f}{st['drop1']:>7.3f}  [{st['lo']:.2f},{st['hi']:.2f}]")
        if claims:
            cv = claims.get(t)
            line += f"{cv:>9.3f}" if cv is not None else f"{'-':>9}"
        line += star
        print(line)


# ---------------------------------------------------------------------------
def run_block(df: pd.DataFrame, label: str, rng: np.random.Generator) -> None:
    print("\n" + "=" * 92)
    print(f"  期間: {label}   {df['race_id'].nunique()}レース / {len(df)}馬")
    print("=" * 92)
    top1 = df[df["composite_rank"] == 1]

    # ① jra_buy_signal (レース1位馬の単ROI)
    tier_table(top1, "buy_signal", ["buy", "caution", "pass"],
               "① jra_buy_signal: 指数1位馬の成績 (claim: buy 単ROI 1.237)",
               rng, claims={"buy": 1.237, "caution": 1.0, "pass": 0.87})

    # ② jra_horse_purchase_signal (馬個別)
    tier_table(df, "purchase_signal", ["super_buy", "buy", "watch"],
               "② jra_horse_purchase_signal: 馬個別 (claim: 1.48/1.29/1.04)",
               rng, claims={"super_buy": 1.48, "buy": 1.29, "watch": 1.04})

    # ③ is_sweet_spot
    print("\n--- ③ is_sweet_spot (JRA): sweet=True 馬の成績 (claim: 単ROI 1.188) ---")
    tier_table(df, "is_sweet_spot", [True, False],
               "   sweet_spot True/False", rng)
    # EV帯別 (sweet候補=odds≥10 のうち) 較正の谷を見る
    cand = df[(df["win_odds"] >= 10) & df["ev_win"].notna()].copy()
    if len(cand):
        cand["ev_bin"] = pd.cut(cand["ev_win"], [0, 1.2, 1.5, 2.0, 3.0, 5.0, 1e9],
                                labels=["<1.2", "1.2-1.5", "1.5-2.0", "2.0-3.0", "3.0-5.0", "≥5.0"])
        print("\n   EV帯別(odds≥10馬) 単ROI — sweet_spotのEVゲート[1.2,5.0]が単調か:")
        print(f"     {'EV帯':<10}{'n':>6}{'勝率':>7}{'単ROI':>8}{'drop1':>8}")
        for b, gg in cand.groupby("ev_bin", observed=True):
            st = _roi_ci(gg, rng)
            print(f"     {str(b):<10}{st['n']:>6}{st['win']:>6.1f}%{st['roi']:>8.3f}{st['drop1']:>8.3f}")

    # ④ DM signals 7タグ (各タグ該当馬)
    print("\n--- ④ dm_signals 7タグ: 各タグ該当馬の成績 (claim はコメント値) ---")
    dm_tags = ["三冠一致", "高得点鉄板", "穴ぐさDM", "DM大穴", "DM高オッズ", "穴ぐさ+DMtime", "人気下振れ"]
    dm_claims = {"三冠一致": 0.849, "高得点鉄板": 1.012, "穴ぐさDM": 1.888, "DM大穴": 1.540,
                 "DM高オッズ": 1.300, "穴ぐさ+DMtime": 1.035, "人気下振れ": 0.739}
    print(f"  {'タグ':<14}{'n':>6}{'勝率':>7}{'複勝':>7}{'単ROI':>7}{'drop1':>7}{'95%CI':>15}{'主張':>8}")
    for tag in dm_tags:
        s = df[df["dm_signals"].apply(lambda xs: tag in xs)]
        st = _roi_ci(s, rng)
        star = " ★" if st["lo"] > 1.0 else (" ◯" if st["roi"] > 1.0 else "")
        print(f"  {tag:<14}{st['n']:>6}{st['win']:>6.1f}%{st['plc']:>6.1f}%{st['roi']:>7.3f}"
              f"{st['drop1']:>7.3f}  [{st['lo']:.2f},{st['hi']:.2f}]{dm_claims[tag]:>8.3f}{star}")

    # ⑤ anagusa_rank A/B/C (1位以外も含む全馬・外部ピック)
    print("\n--- ⑤ anagusa_rank A/B/C: 外部ピック馬の 単/複ROI (claim: A>B>C 単調・全<1) ---")
    ag = df[df["anagusa_rank"].notna()]
    tier_table(ag, "anagusa_rank", ["A", "B", "C"], "   anagusa rank別", rng)

    # ⑥ external_dark_horse (CI4位以下 ∧ (nb_course=1 or (nb_ave≤2 ∧ km=1)))
    edh = df[(df["composite_rank"] >= 4) & (
        (df["nb_course_rank"] == 1) |
        ((df["nb_ave_rank"] <= 2) & (df["km_rank"] == 1))
    )]
    st = _roi_ci(edh, rng)
    print("\n--- ⑥ external_dark_horse (指数4位以下×外部上位): 単ROI ---")
    print(f"  n={st['n']} 勝率{st['win']:.1f}% 複勝率{st['plc']:.1f}% 単ROI{st['roi']:.3f} "
          f"drop1={st['drop1']:.3f} CI[{st['lo']:.2f},{st['hi']:.2f}]")

    # ⑦ recommend_rank / confidence_rank (1位馬の勝率単調性 — 地方と共有・JRA未検証)
    tier_table(top1, "recommend_rank", ["S", "A", "B", "C"],
               "⑦a recommend_rank: 指数1位馬の成績 (地方再定義・JRA未検証の疑い)", rng)
    tier_table(top1, "confidence_rank", ["S", "A", "B", "C"],
               "⑦b confidence_rank: 指数1位馬の成績", rng)

    # ⑧ win_probability 較正 (softmax・未較正の疑い)
    print("\n--- ⑧ v26 win_probability 較正 (decile別 予測勝率 vs 実測勝率 / ECE) ---")
    d = df[df["win_probability"].notna()].copy()
    d["bin"] = pd.qcut(d["win_probability"].rank(method="first"), 10, labels=False)
    print(f"  {'decile':<8}{'n':>7}{'予測勝率':>10}{'実測勝率':>10}{'乖離':>9}")
    ece = 0.0
    tot = len(d)
    for b, gg in d.groupby("bin"):
        pred = gg["win_probability"].mean() * 100
        act = (gg["finish_position"] == 1).mean() * 100
        ece += abs(pred - act) / 100 * len(gg) / tot
        print(f"  {int(b)+1:<8}{len(gg):>7}{pred:>9.2f}%{act:>9.2f}%{act-pred:>+8.2f}%")
    print(f"  → ECE(加重平均絶対誤差) = {ece:.4f}  (0に近いほど較正良好)")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230501")
    p.add_argument("--train-end", default="20250630")
    p.add_argument("--test-start", default="20250701")
    p.add_argument("--end", default="20260605")
    args = p.parse_args()

    rng = np.random.default_rng(12345)
    dsn = (f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
           f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
           f"password={os.getenv('DB_PASSWORD')}")
    conn = psycopg2.connect(dsn)
    df = fetch_base(conn, args.start, args.end)
    ext = fetch_external(conn, args.start, args.end)
    conn.close()

    logger.info("シグナル付与中...")
    df = annotate(df, ext)
    df["date"] = df["date"].astype(str)

    full = df
    test = df[df["date"] >= args.test_start]

    print("\n" + "#" * 92)
    print("# JRA 表示シグナル妥当性検証")
    print("# ★=95%CI下限>1(黒字確証) / ◯=点推定>1 / 較正は ECE が0に近いほど良好")
    print("# 主張列との乖離・tier非単調・CI跨ぎ・ECE悪化 が『破綻/陳腐化』のサイン")
    print("#" * 92)
    run_block(full, f"FULL {args.start}-{args.end}", rng)
    run_block(test, f"OOS test {args.test_start}-{args.end}", rng)


if __name__ == "__main__":
    main()
