"""中央競馬（JRA） Phase 0 — 席数ラベルの実態集計と、学習不要ベースラインの測定。

`docs/upset_seat_decomposition_plan_2026_09_02.md` の Phase 0 に対応する。

**この段階では何も学習しない。** 目的は次の3つだけ。

1. 席数 S / 空席数 E の分布を頭数ビン別に出し、人気薄の定義（人気基準 A /
   オッズ基準 B）を計画 §2.3 の手順で機械的に決める
2. 学習不要ベースラインを3本測る
   - `base_a` 期待空席数  = place_slots − Σ_{上位人気} 市場複勝確率（Henery）
   - `base_b` 2変数ルール = head_count × odds_top1
   - `base_c` 現行ゲート  = top3_share < 0.63 ∧ head_count >= 8
3. 3本の `(選択率, R1, R2, lift)` 曲線を出す。以後の比較対象はこれ

🔴 **オッズは必ず発走前スナップショット**（既定 T−6分・前向き記録と同じリード）。
確定オッズで一度でも測ると、過去2回と同じ崩壊を繰り返す（計画 §7.1）。
`--lead-minutes` を 0 にはできないようにしてある。

🔴 **base_c は地方の現行ゲート（`top3_share < 0.63` ∧ 8頭以上）を JRA へ
そのまま当てた参考値**。JRA には対応する運用ゲートが無く（hit_tier は tier=C で
レースを落とすが、これは指数と市場一致度で決まり席の空きを見ていない）、
**同じ物差しで両柱を並べるための対照**として置いている。

使い方:
    cd backend
    .venv/bin/python scripts/chihou_upset_seat_label.py \
        --start 20260328 --end 20260831 --out ../docs/model_verification/jra_seat_phase0.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

from src.betting.finish_order import _place_prob_single  # noqa: E402

# 地方の現行ゲート定数（src/indices/buy_signal.py と揃える・JRA へは参考として適用）
CLOSED_RACE_TOP3_SHARE = 0.63
MIN_HEAD_COUNT = 8

# 人気薄の定義（計画 §2.3 の両論。Phase 0 の出力で主定義を決める）
POP_RANK_MIN = 6      # 案A: 発走前オッズ順位がこれ以上
UNPOP_ODDS_MIN = 10.0  # 案B: 発走前単勝オッズがこれ以上

HEAD_BINS = [(5, 7), (8, 9), (10, 11), (12, 13), (14, 16), (17, 18)]


# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------

# ⚠️ `chihou_odds_query.latest_odds_sql()` は「発走時刻以前の最新」で、リード時間を
#    取れない。バックテストでは T−lead を明示したいので、同じ LATERAL + LIMIT 1 の
#    形（インデックス ix_chihou_odds_history_race_type_combo_time に乗せる形）を
#    保ったまま lead を足したクエリをここに置く。DISTINCT ON にすると 850ms かかる。
RACE_SQL = """
SELECT r.id            AS race_id,
       r.date          AS date,
       r.course        AS course,
       r.course_name   AS course_name,
       r.race_number   AS race_number,
       r.head_count    AS head_count,
       r.distance      AS distance,
       r.grade         AS grade
FROM keiba.races r
WHERE r.date BETWEEN %(start)s AND %(end)s
  AND r.post_time ~ '^[0-9]{4}$'
ORDER BY r.date, r.id
"""

ENTRY_SQL = """
SELECT e.race_id,
       e.horse_number,
       o.odds        AS pre_win_odds,
       rr.finish_position
FROM keiba.race_entries e
JOIN keiba.races r ON r.id = e.race_id
LEFT JOIN keiba.race_results rr
       ON rr.race_id = e.race_id AND rr.horse_number = e.horse_number
CROSS JOIN LATERAL (
    SELECT oh.odds
    FROM keiba.odds_history oh
    WHERE oh.race_id = e.race_id
      AND oh.bet_type = 'win'
      AND oh.combination = e.horse_number::text
      AND oh.fetched_at <= (
            to_timestamp(r.date || r.post_time, 'YYYYMMDDHH24MI')
            - interval '9 hours'
            - (%(lead)s || ' minutes')::interval
          )
    ORDER BY oh.fetched_at DESC
    LIMIT 1
) o
WHERE e.race_id = ANY(%(race_ids)s)
  AND e.horse_number IS NOT NULL
"""


def _connect():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def place_slots(head_count: int) -> int:
    """複勝の払戻対象席数。7頭以下は2着まで、4頭以下は複勝が売られない。

    `finish_order._place_prob_single` の `place_within`（n>=8 で3）と整合させる。
    """
    if head_count >= 8:
        return 3
    if head_count >= 5:
        return 2
    return 0


def fetch(conn, start: str, end: str, lead: int) -> list[dict[str, Any]]:
    """レースごとに {メタ, 出走馬[発走前オッズ・着順]} を組み立てる。"""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(RACE_SQL, {"start": start, "end": end})
        races = {r["race_id"]: dict(r, horses=[]) for r in cur.fetchall()}
        if not races:
            return []
        cur.execute(ENTRY_SQL, {"race_ids": list(races), "lead": lead})
        for row in cur.fetchall():
            races[row["race_id"]]["horses"].append(
                {
                    "horse_number": row["horse_number"],
                    "pre_win_odds": float(row["pre_win_odds"]),
                    "finish_position": row["finish_position"],
                }
            )
    return list(races.values())


# ---------------------------------------------------------------------------
# ラベルと市場確率
# ---------------------------------------------------------------------------


def market_win_probs(horses: list[dict]) -> dict[int, float]:
    """発走前単勝オッズ → 控除率を均して Σ=1 に正規化した勝率。"""
    raw = {h["horse_number"]: 1.0 / h["pre_win_odds"] for h in horses if h["pre_win_odds"] > 0}
    total = sum(raw.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in raw.items()}


def annotate(race: dict) -> dict | None:
    """1レースに pop_rank / 市場複勝確率 / 席数ラベルを付ける。

    発走前オッズが全馬そろっていないレースは捨てる（部分的な人気順は意味が無い）。
    """
    # 発走前オッズが付いている馬だけを「市場が見ている出走馬」として扱う。
    # 取消馬はオッズが止まるため自然に落ちる。`races.head_count` は確定値で
    # 発走前には入らないので使わない（計画 §10 / head_count の train-serve skew）。
    horses = [h for h in race["horses"] if h["pre_win_odds"] and h["pre_win_odds"] > 0]
    race["horses"] = horses
    n = len(horses)
    slots = place_slots(n)
    if slots == 0 or n < 5:
        return None

    wp = market_win_probs(horses)
    if not wp:
        return None

    for rank, h in enumerate(sorted(horses, key=lambda x: x["pre_win_odds"]), start=1):
        h["pop_rank"] = rank
    for h in horses:
        h["p_place_market"] = _place_prob_single(wp, h["horse_number"], "henery", None, None)
        fp = h["finish_position"]
        h["in_money"] = fp is not None and 0 < fp <= slots

    ordered = sorted(horses, key=lambda x: x["pop_rank"])
    race["n"] = n
    race["slots"] = slots
    race["odds_top1"] = ordered[0]["pre_win_odds"]
    race["top3_share"] = sum(wp[h["horse_number"]] for h in ordered[:3])
    race["entropy_norm"] = (
        -sum(p * math.log(p) for p in wp.values() if p > 0) / math.log(n) if n > 1 else None
    )
    # 着順が1件も無いレース（未確定）は集計対象外
    if not any(h["finish_position"] is not None for h in horses):
        return None

    for defn, is_unpop in (("A", lambda h: h["pop_rank"] >= POP_RANK_MIN),
                           ("B", lambda h: h["pre_win_odds"] >= UNPOP_ODDS_MIN)):
        unpop = [h for h in horses if is_unpop(h)]
        fav = [h for h in horses if not is_unpop(h)]
        race[f"S_{defn}"] = sum(1 for h in fav if h["in_money"])
        race[f"E_{defn}"] = slots - race[f"S_{defn}"]
        race[f"n_unpop_{defn}"] = len(unpop)
        race[f"hit_unpop_{defn}"] = sum(1 for h in unpop if h["in_money"])
        # base_a: 期待空席数（上位人気の市場複勝確率の和を席数から引く）
        race[f"E_hat_{defn}"] = slots - sum(h["p_place_market"] for h in fav)
    return race


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------


def head_bin(n: int) -> str:
    for lo, hi in HEAD_BINS:
        if lo <= n <= hi:
            return f"{lo}-{hi}"
    return "19+"


def label_distribution(races: list[dict], defn: str) -> dict[str, Any]:
    """頭数ビン別の E 分布と base rate。計画 §2.3 の定義選定の材料。"""
    out: dict[str, Any] = {}
    for r in races:
        b = head_bin(r["n"])
        d = out.setdefault(b, {"races": 0, "E_dist": defaultdict(int), "e_ge1": 0,
                               "unpop_horses": 0, "unpop_hits": 0})
        d["races"] += 1
        d["E_dist"][r[f"E_{defn}"]] += 1
        d["e_ge1"] += 1 if r[f"hit_unpop_{defn}"] >= 1 else 0
        d["unpop_horses"] += r[f"n_unpop_{defn}"]
        d["unpop_hits"] += r[f"hit_unpop_{defn}"]
    for b, d in out.items():
        d["E_dist"] = dict(sorted(d["E_dist"].items()))
        d["base_rate_e_ge1"] = d["e_ge1"] / d["races"] if d["races"] else None
        d["unpop_hit_rate"] = d["unpop_hits"] / d["unpop_horses"] if d["unpop_horses"] else None
    return out


def selection_curve(races: list[dict], defn: str, score_key: str,
                    rates: tuple[float, ...] = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30,
                                                0.40, 0.50)) -> list[dict]:
    """レースをスコア降順に選び、選択率ごとの R2 と lift を出す。

    R1（馬単位捕捉率）はレース選別だけでは決まらないため、ここでは
    「選ばれたレースに含まれる人気薄の的中頭数 ÷ 全体の的中頭数」を出す。
    馬を絞る段は Phase 2。
    """
    scored = [r for r in races if r.get(score_key) is not None]
    if not scored:
        return []
    scored.sort(key=lambda r: r[score_key], reverse=True)
    total_hit_races = sum(1 for r in races if r[f"hit_unpop_{defn}"] >= 1)
    total_hit_horses = sum(r[f"hit_unpop_{defn}"] for r in races)
    base = total_hit_races / len(races) if races else 0.0

    out = []
    for rate in rates:
        k = max(1, int(len(scored) * rate))
        sel = scored[:k]
        hit_races = sum(1 for r in sel if r[f"hit_unpop_{defn}"] >= 1)
        out.append({
            "selection_rate": round(k / len(races), 4),
            "n_races": k,
            "R2_capture": round(hit_races / total_hit_races, 4) if total_hit_races else None,
            "R1_capture": round(sum(r[f"hit_unpop_{defn}"] for r in sel) / total_hit_horses, 4)
            if total_hit_horses else None,
            "precision_e_ge1": round(hit_races / k, 4),
            "lift": round((hit_races / k) / base, 3) if base > 0 else None,
        })
    return out


def stratified_lift(races: list[dict], defn: str, score_key: str,
                    top_frac: float = 0.30) -> dict[str, Any]:
    """頭数ビンを固定した中での score の lift。

    🔴 Phase 0 stop rule #2 の直接検定。地方では E>=1 の base rate が頭数で
    9.7%〜75.1% と激しく動き（VAL 5,286R 実測）、頭数をまたいだ lift は
    「頭数の言い換え」でも簡単に 1.4 程度が出てしまった。JRA は頭数分布が
    違うので同じとは限らないが、検定の必要性は変わらない。**頭数を固定しても
    残る分だけが、レース側モデルに乗せられる情報**である。

    ビン内でスコア上位 `top_frac` を選び、その的中率をビンの base rate と比べる。
    `_pooled` はビンごとの結果をレース数で加重平均したもの。
    """
    out: dict[str, Any] = {}
    by_bin: dict[str, list[dict]] = defaultdict(list)
    for r in races:
        if r.get(score_key) is not None:
            by_bin[head_bin(r["n"])].append(r)

    tot_sel = tot_hit = tot_races = 0
    weighted_base = 0.0
    for b, rs in by_bin.items():
        base_hits = sum(1 for r in rs if r[f"hit_unpop_{defn}"] >= 1)
        base = base_hits / len(rs)
        rs_sorted = sorted(rs, key=lambda r: r[score_key], reverse=True)
        k = max(1, int(len(rs) * top_frac))
        sel = rs_sorted[:k]
        hit = sum(1 for r in sel if r[f"hit_unpop_{defn}"] >= 1)
        out[b] = {
            "n_races": len(rs),
            "base_rate": round(base, 4),
            "top30_precision": round(hit / k, 4),
            "lift": round((hit / k) / base, 3) if base > 0 else None,
        }
        tot_sel += k
        tot_hit += hit
        tot_races += len(rs)
        weighted_base += base * k

    if tot_sel:
        pooled_base = weighted_base / tot_sel
        out["_pooled"] = {
            "n_races": tot_races,
            "base_rate": round(pooled_base, 4),
            "top30_precision": round(tot_hit / tot_sel, 4),
            "lift": round((tot_hit / tot_sel) / pooled_base, 3) if pooled_base > 0 else None,
        }
    return out


def base_c_gate(races: list[dict], defn: str) -> dict[str, Any]:
    """地方の現行ゲート（closed_race / small_field）を JRA へ当てた参考点。

    JRA 側に対応する運用ゲートは無い。両柱を同じ物差しで並べるための対照。
    閾値を持たない単一点なので曲線ではなく1点として出す。
    """
    sel = [r for r in races if r["n"] >= MIN_HEAD_COUNT
           and r["top3_share"] < CLOSED_RACE_TOP3_SHARE]
    total_hit_races = sum(1 for r in races if r[f"hit_unpop_{defn}"] >= 1)
    base = total_hit_races / len(races) if races else 0.0
    hit = sum(1 for r in sel if r[f"hit_unpop_{defn}"] >= 1)
    return {
        "selection_rate": round(len(sel) / len(races), 4) if races else None,
        "n_races": len(sel),
        "R2_capture": round(hit / total_hit_races, 4) if total_hit_races else None,
        "precision_e_ge1": round(hit / len(sel), 4) if sel else None,
        "lift": round((hit / len(sel)) / base, 3) if sel and base > 0 else None,
    }


def main() -> None:
    # 🔴 人気薄の閾値は柱ごとに違ってよい（頭数分布が違うため）。Phase 0 の仕事は
    #    「ヘッドルームのある帯に base rate が入る閾値を機械的に見つける」こと。
    #    2026-09-02 実測: 地方は pop_rank>=6 で base rate 51.7%（OK）だが、
    #    JRA は同じ閾値で 64.6% と飽和する（14-16頭が最頻ビンのため）。
    global POP_RANK_MIN, UNPOP_ODDS_MIN

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True, help="開始日 YYYYMMDD")
    ap.add_argument("--end", required=True, help="終了日 YYYYMMDD")
    ap.add_argument("--lead-minutes", type=int, default=10,
                    help="発走何分前のオッズを使うか（既定10＝hit_tier 前向き記録と同じ）")
    ap.add_argument("--out", help="集計結果の JSON 出力先")
    ap.add_argument("--cache", help="DB 取得結果のキャッシュ先。あれば読み、無ければ書く")
    ap.add_argument("--pop-rank-min", type=int, default=POP_RANK_MIN,
                    help="定義A: 人気薄とみなす発走前オッズ順位の下限")
    ap.add_argument("--odds-min", type=float, default=UNPOP_ODDS_MIN,
                    help="定義B: 人気薄とみなす発走前単勝オッズの下限")
    args = ap.parse_args()

    # 🔴 確定オッズでの測定を機械的に禁止する（計画 §7.1・過去2回の崩壊）
    if args.lead_minutes < 1:
        ap.error("--lead-minutes は 1 以上。確定オッズでこの集計をしてはいけない（計画 §7.1）")
    if args.start < "20260328":
        ap.error("JRA の odds_history は 2026-03-28 開始。それ以前は発走前オッズが無い")

    POP_RANK_MIN = args.pop_rank_min
    UNPOP_ODDS_MIN = args.odds_min

    cache = Path(args.cache) if args.cache else None
    if cache and cache.exists():
        print(f"キャッシュから読み込み: {cache}")
        with open(cache) as f:
            raw = json.load(f)
    else:
        with _connect() as conn:
            raw = fetch(conn, args.start, args.end, args.lead_minutes)
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            with open(cache, "w") as f:
                json.dump(raw, f, default=str)
            print(f"キャッシュへ書き出し: {cache}")
    races = [r for r in (annotate(x) for x in raw) if r is not None]

    print(f"=== JRA 席数ラベル Phase 0  {args.start}〜{args.end}"
          f"  (T−{args.lead_minutes}分オッズ) ===")
    print(f"取得 {len(raw)} レース → 発走前オッズ全馬そろい & 着順あり {len(races)} レース")
    if not races:
        print("対象レースがありません")
        return

    result: dict[str, Any] = {
        "window": {"start": args.start, "end": args.end, "lead_minutes": args.lead_minutes},
        "n_races_fetched": len(raw),
        "n_races_used": len(races),
        "definitions": {"A_pop_rank_min": POP_RANK_MIN, "B_odds_min": UNPOP_ODDS_MIN},
    }

    for defn, label in (("A", f"人気基準 pop_rank>={POP_RANK_MIN}"),
                        ("B", f"オッズ基準 {UNPOP_ODDS_MIN}倍以上")):
        dist = label_distribution(races, defn)
        rates = [d["base_rate_e_ge1"] for d in dist.values() if d["races"] >= 30]
        spread = (max(rates) - min(rates)) if len(rates) >= 2 else None
        # 🔴 生の差（max−min）は閾値を厳しくするほど機械的に縮む。base rate が 0 に
        #    近づけば差も 0 に近づくため、「ヘッドルーム下限ぎりぎりを選ぶ」だけに
        #    退化する（2026-09-02 の掃引で地方・JRA とも実際にそうなった）。
        #    ロジット空間の幅はスケール不変なので、この退化が起きない。
        logits = [math.log(p / (1 - p)) for p in rates if 0.0 < p < 1.0]
        spread_logit = (max(logits) - min(logits)) if len(logits) >= 2 else None
        overall = sum(1 for r in races if r[f"hit_unpop_{defn}"] >= 1) / len(races)

        print(f"\n--- 定義{defn}: {label} ---")
        print(f"  全体 base rate  P(人気薄が1頭以上複勝圏) = {overall:6.1%}")
        if spread is not None:
            print(f"  頭数ビン別 base rate のレンジ = {spread:.3f}"
                  f" / ロジット幅 = {spread_logit:.3f}")
        else:
            print("  (ビンが足りずレンジ算出不可)")
        print(f"  {'頭数':>7} {'R数':>6} {'base':>7} {'人気薄的中率':>10}")
        for b, d in sorted(dist.items()):
            print(f"  {b:>7} {d['races']:>6} {d['base_rate_e_ge1']:>7.1%} "
                  f"{(d['unpop_hit_rate'] or 0):>10.1%}")

        curves = {
            "base_a_expected_vacancy": selection_curve(races, defn, f"E_hat_{defn}"),
            "base_b0_head_count": selection_curve(races, defn, "n"),
            "base_b_odds_top1": selection_curve(races, defn, "odds_top1"),
            "base_c_current_gate": base_c_gate(races, defn),
        }
        strat = stratified_lift(races, defn, f"E_hat_{defn}")
        print("\n  [ベースライン] 選択率ごとの lift / レース単位捕捉率 R2")
        print(f"  {'腕':<26} {'選択率':>7} {'R2':>7} {'lift':>6}")
        for name in ("base_a_expected_vacancy", "base_b0_head_count", "base_b_odds_top1"):
            for pt in curves[name]:
                if abs(pt["selection_rate"] - 0.20) < 0.02:
                    print(f"  {name:<26} {pt['selection_rate']:>7.1%} "
                          f"{(pt['R2_capture'] or 0):>7.1%} {pt['lift']:>6}")
        c = curves["base_c_current_gate"]
        print(f"  {'base_c_current_gate':<26} {(c['selection_rate'] or 0):>7.1%} "
              f"{(c['R2_capture'] or 0):>7.1%} {c['lift']:>6}")

        # 🔴 Phase 0 stop rule #2 の直接検定。
        #    E が頭数だけで決まるなら、頭数を固定した中で base_a は効かないはず。
        print("\n  [頭数を固定したときの base_a の lift]（上積み余地の有無）")
        print(f"  {'頭数':>7} {'R数':>6} {'base':>7} {'上位30%の的中':>12} {'lift':>6}")
        for b, d in sorted(strat.items()):
            if d["n_races"] < 100:
                continue
            # base rate 0 のビン（人気薄が一度も来ていない）は lift が定義できない
            lift_s = f"{d['lift']:.3f}" if d["lift"] is not None else "  n/a"
            print(f"  {b:>7} {d['n_races']:>6} {d['base_rate']:>7.1%} "
                  f"{d['top30_precision']:>12.1%} {lift_s:>6}")
        pooled = strat.get("_pooled")
        if pooled:
            pooled_s = f"{pooled['lift']:.3f}" if pooled["lift"] is not None else "  n/a"
            print(f"  {'加重平均':>7} {pooled['n_races']:>6} {pooled['base_rate']:>7.1%} "
                  f"{pooled['top30_precision']:>12.1%} {pooled_s:>6}"
                  f"   ← これが 1.0 付近なら頭数の言い換えにすぎない")

        result[f"definition_{defn}"] = {
            "overall_base_rate": round(overall, 4),
            "head_bin_spread": round(spread, 4) if spread is not None else None,
            "head_bin_spread_logit": round(spread_logit, 4) if spread_logit is not None else None,
            "distribution": dist,
            "baselines": curves,
            "base_a_lift_stratified_by_head_count": strat,
        }

    # 計画 §2.3 の決定手順 + ヘッドルーム条件。
    #
    # 🔴 §2.3 は「頭数を条件付けた後の base rate のばらつきが小さい方」だけを
    #    基準にしていたが、それだけでは**飽和した定義が勝ってしまう**。
    #    base rate が 1.0 に近い定義はそもそも動く余地が無いのでばらつきも小さく、
    #    しかも lift の上限が 1/base rate まで潰れる。
    #    2026-09-02 のスモークテスト（20260501-07・313R）で実際にこれが起きた:
    #      定義B は spread 0.030 で「勝った」が base rate 73.5% で lift 上限 1.36、
    #      base_b / base_c の lift は 0.88 / 0.94 と **1 を割った**。
    #    そこで「識別の余地がある帯に base rate があること」を先に要求する。
    HEADROOM = (0.20, 0.60)
    cands = []
    for defn in ("A", "B"):
        d = result[f"definition_{defn}"]
        br, sp = d["overall_base_rate"], d["head_bin_spread_logit"]
        ok = br is not None and HEADROOM[0] <= br <= HEADROOM[1]
        d["headroom_ok"] = ok
        d["lift_ceiling"] = round(1.0 / br, 3) if br else None
        if ok and sp is not None:
            cands.append((sp, defn))
    print("\n=== 主定義の機械的決定（計画 §2.3 + ヘッドルーム条件）===")
    for defn in ("A", "B"):
        d = result[f"definition_{defn}"]
        print(f"  定義{defn}: base rate {d['overall_base_rate']:.1%} / "
              f"lift 上限 {d['lift_ceiling']} / 頭数ビンのロジット幅 "
              f"{d['head_bin_spread_logit']} / ヘッドルーム "
              f"{'OK' if d['headroom_ok'] else 'NG'}")
    if cands:
        primary = min(cands)[1]
        result["primary_definition"] = primary
        print(f"  → 主定義 = 定義{primary}"
              f"（ヘッドルーム {HEADROOM[0]:.0%}〜{HEADROOM[1]:.0%} を満たす中で"
              f"頭数依存（ロジット幅）が最小）")
    else:
        result["primary_definition"] = None
        print(f"  → 🔴 どちらも base rate が {HEADROOM[0]:.0%}〜{HEADROOM[1]:.0%} の外。"
              "閾値（POP_RANK_MIN / UNPOP_ODDS_MIN）を振り直すこと。"
              "定義を決めずに Phase 1 へ進んではいけない")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n書き出し: {args.out}")


if __name__ == "__main__":
    main()
