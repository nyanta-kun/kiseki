"""netkeirin 予想家比較（2026-10-13 新設）— `/api/keirin/netkeirin-rivals` の計算本体。

元データは `keirin.netkeirin_rival_goods`（1商品=1行・keirin `src/netkeirin_rivals.py`
が取り込む）。SQL から取れた素の行を受け取り、表示に必要な集計だけを返す
**純関数の集まり**（DB にも FastAPI にも依存しない）。

## 数え方

- **予想数** … 商品数。同じ予想家が1レースに2商品出していれば2と数える
  （netkeirin の成績表示と同じ。レース数は `n_races` として別に出す）
- **的中(ガミ含む)** … 払戻 > 0。netkeirin の一覧で「的中」アイコンが付くもの
- **的中(ガミ除く)** … 払戻 > 購入金額。netkeirin のプロフィールの「的中率」はこちら
  （`keirin_sales_analysis` と同じ定義。`>` であって `>=` ではない）
- **回収率** … 払戻合計 / 購入金額合計

🔴 **未確定（payout が NULL）の商品は数えない。** 0 円として足すと、取り込みの
   直後だけ全員の回収率が下がって見える。呼び出し側の SQL で落とすこと。

## 順位

🔴 **予想数が `min_goods` 未満の予想家は順位を付けない。** 付けると、1レースだけ
   出して当てた予想家が回収率 2,000% で常に1位になり、順位が意味を失う。
   表には出す（`ranked=False`）が順位は None。
   同値は同順位（1, 2, 2, 4 …）。

## 推移グラフの比較相手（既定・2026-10-16）

🔴 **表示期間の成績で比較相手を選ばない。** 以前は「表示期間の指標上位5人」を
   既定にしていたが、それは*その期間に運が良かった人*を選ぶことになり、線が
   実力以上に高く見える（2026-09-14〜10-12 の実測: 前半の上位5人は後半に平均
   69.5% まで落ち、全員の平均 71.8% を下回った）。一方で 1 年単位では順位に
   持続性がある（2025→2026 の順位相関 +0.40）。

→ 既定は `steady_compare_candidates`: **表示期間の前日までの1年**で
   予想数 `STEADY_MIN_GOODS` 以上・**数えられる四半期が `STEADY_MIN_QUARTERS` 以上あり、
   その過半で全体の回収率を上回った**
   予想家を、その1年の回収率順に並べたもの。表示期間に予想が無い人は飛ばして
   次の人を採る（候補は人数で切らずに全員渡すこと。先に5人へ切ると線が減る）。
   該当者がいなければ従来どおり表示期間の上位へ落とし、`compare_basis` で知らせる。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date, timedelta
from statistics import median
from typing import Any

#: netkeirin 上の自分（二軸探偵）。keirin `src/netkeirin_rivals.SELF_YOSOKA_ID` と同値。
#: プロフィールの URL `/yoso/profile/?id=732` から（コードの他の場所には出てこない）。
SELF_YOSOKA_ID = 732

#: 順位を付ける指標（キー → 大きいほど上位）
RANK_METRICS = ("roi", "net_hit_rate", "hit_rate", "profit", "n_goods")

#: 期間内の予想数がこれ未満の予想家は順位の対象外（既定）
DEFAULT_MIN_GOODS = 20
#: 推移グラフの1区間で順位を付ける最低予想数
DEFAULT_MIN_GOODS_PER_BUCKET = 5

#: 継続上位の選定窓（表示期間の前日から遡る日数）
STEADY_LOOKBACK_DAYS = 365
#: 選定窓での最低予想数
STEADY_MIN_GOODS = 200
#: 「全体を上回ったか」を数える四半期の最低予想数（これ未満の四半期は数えない）
STEADY_MIN_GOODS_PER_QUARTER = 20
#: 数えられる四半期の最低数。1〜2 四半期だけでは「継続」と言えない
#: （データ開始 2025-04 の直後は該当者がいなくなり、表示期間の上位へ落ちる）
STEADY_MIN_QUARTERS = 3


def _blank() -> dict[str, Any]:
    return {"n_goods": 0, "races": set(), "n_hits": 0, "n_net_hits": 0, "bet": 0, "payout": 0}


def _add(acc: dict[str, Any], race_key: str, bet: int, payout: int) -> None:
    acc["n_goods"] += 1
    acc["races"].add(race_key)
    acc["bet"] += bet
    acc["payout"] += payout
    if payout > 0:
        acc["n_hits"] += 1
    if payout > bet:
        acc["n_net_hits"] += 1


def _finish(acc: dict[str, Any]) -> dict[str, Any]:
    n, bet = acc["n_goods"], acc["bet"]
    return {
        "n_goods": n,
        "n_races": len(acc["races"]),
        "n_hits": acc["n_hits"],
        "n_net_hits": acc["n_net_hits"],
        "bet": bet,
        "payout": acc["payout"],
        "profit": acc["payout"] - bet,
        "roi": (acc["payout"] / bet) if bet > 0 else None,
        "hit_rate": (acc["n_hits"] / n) if n else None,
        "net_hit_rate": (acc["n_net_hits"] / n) if n else None,
    }


def _assign_ranks(rows: list[dict[str, Any]], metric: str, min_goods: int, key: str = "rank") -> int:
    """`rows` に `key`（順位）を書き込み、順位の付いた人数を返す。同値は同順位。"""
    eligible = [r for r in rows if r["n_goods"] >= min_goods and r.get(metric) is not None]
    eligible.sort(key=lambda r: r[metric], reverse=True)
    prev, prev_rank = None, 0
    for i, r in enumerate(eligible, start=1):
        if r[metric] != prev:
            prev, prev_rank = r[metric], i
        r[key] = prev_rank
    ids = {id(r) for r in eligible}
    for r in rows:
        if id(r) not in ids:
            r[key] = None
    return len(eligible)


def bucket_of(d: date, granularity: str) -> str:
    """日付 → 区間の代表日（'YYYY-MM-DD'）。週は月曜始まり。"""
    if granularity == "week":
        return (d - timedelta(days=d.weekday())).isoformat()
    if granularity == "month":
        return d.replace(day=1).isoformat()
    return d.isoformat()


def steady_compare_candidates(
    agg_rows: Iterable[dict[str, Any]],
    *,
    self_id: int,
    min_goods: int = STEADY_MIN_GOODS,
    min_goods_per_quarter: int = STEADY_MIN_GOODS_PER_QUARTER,
    min_quarters: int = STEADY_MIN_QUARTERS,
) -> list[dict[str, Any]]:
    """選定窓の四半期集計 → 継続して回収率が高い予想家（選定窓の回収率が高い順）。

    agg_rows: 予想家×四半期の集計。`yosoka_id` / `yosoka_name` / `quarter` / `n_goods` /
              `bet` / `payout`（確定済みのみ）。全体の四半期回収率も同じ行から作る。

    条件: 予想数 `min_goods` 以上 かつ、予想数 `min_goods_per_quarter` 以上の四半期が
    `min_quarters` 以上あり、その**過半**で全体の回収率を上回ったこと。自分は含めない。
    """
    per: dict[int, dict[Any, list[int]]] = defaultdict(dict)
    names: dict[int, str] = {}
    field_q: dict[Any, list[int]] = defaultdict(lambda: [0, 0])
    for r in agg_rows:
        yid, q = int(r["yosoka_id"]), r["quarter"]
        n, bet, pay = int(r["n_goods"]), int(r["bet"] or 0), int(r["payout"] or 0)
        if r.get("yosoka_name"):
            names[yid] = str(r["yosoka_name"])
        acc = per[yid].setdefault(q, [0, 0, 0])
        acc[0] += n
        acc[1] += bet
        acc[2] += pay
        field_q[q][0] += bet
        field_q[q][1] += pay
    field_roi = {q: v[1] / v[0] for q, v in field_q.items() if v[0] > 0}

    out: list[dict[str, Any]] = []
    for yid, qs in per.items():
        if yid == self_id:
            continue
        n = sum(v[0] for v in qs.values())
        bet = sum(v[1] for v in qs.values())
        pay = sum(v[2] for v in qs.values())
        if n < min_goods or bet <= 0:
            continue
        counted = [q for q, v in qs.items() if v[0] >= min_goods_per_quarter and v[1] > 0 and q in field_roi]
        above = sum(1 for q in counted if qs[q][2] / qs[q][1] > field_roi[q])
        if len(counted) < min_quarters or above * 2 <= len(counted):  # 短すぎる / 過半に届かない
            continue
        out.append(
            {
                "yosoka_id": yid,
                "yosoka_name": names.get(yid),
                "lookback_n_goods": n,
                "lookback_roi": pay / bet,
                "quarters_above": above,
                "quarters_counted": len(counted),
            }
        )
    out.sort(key=lambda c: (-c["lookback_roi"], -c["lookback_n_goods"]))
    return out


def analyze(
    rows: Iterable[dict[str, Any]],
    *,
    self_id: int,
    metric: str = "roi",
    min_goods: int = DEFAULT_MIN_GOODS,
    granularity: str = "week",
    min_goods_per_bucket: int = DEFAULT_MIN_GOODS_PER_BUCKET,
    compare_ids: list[int] | None = None,
    n_compare_default: int = 5,
    steady_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """確定済みの商品行 → 順位表・自分の位置・推移。

    rows: `race_date`(date) / `race_key` / `yosoka_id` / `yosoka_name` / `bet` / `payout`。
          payout が None の行は渡さないこと（渡されても数えない）。
    steady_candidates: `steady_compare_candidates` の戻り値（並び順どおり）。`compare_ids` が
          無いときの既定の比較相手で、表示期間に予想が無い人は飛ばす。空なら表示期間の上位。
    """
    if metric not in RANK_METRICS:
        metric = "roi"
    per: dict[int, dict[str, Any]] = defaultdict(_blank)
    names: dict[int, str] = {}
    per_bucket: dict[str, dict[int, dict[str, Any]]] = defaultdict(lambda: defaultdict(_blank))
    field_bucket: dict[str, dict[str, Any]] = defaultdict(_blank)
    for r in rows:
        if r.get("payout") is None or r.get("bet") is None:
            continue
        yid = int(r["yosoka_id"])
        bet, pay = int(r["bet"]), int(r["payout"])
        if r.get("yosoka_name"):
            names[yid] = str(r["yosoka_name"])
        _add(per[yid], str(r["race_key"]), bet, pay)
        b = bucket_of(r["race_date"], granularity)
        _add(per_bucket[b][yid], str(r["race_key"]), bet, pay)
        _add(field_bucket[b], str(r["race_key"]), bet, pay)

    # ── 順位表 ──
    table = []
    for yid, acc in per.items():
        row = {"yosoka_id": yid, "yosoka_name": names.get(yid), "is_self": yid == self_id, **_finish(acc)}
        table.append(row)
    n_ranked = {}
    for m in RANK_METRICS:
        n_ranked[m] = _assign_ranks(table, m, min_goods, key=f"rank_{m}")
    for row in table:
        row["ranked"] = row["n_goods"] >= min_goods
        row["rank"] = row[f"rank_{metric}"]
    table.sort(key=lambda r: (r["rank"] is None, r["rank"] or 0, -r["n_goods"]))

    self_row = next((r for r in table if r["is_self"]), None)
    ranked_rows = [r for r in table if r["ranked"]]
    field_total = _blank()
    for acc in per.values():
        field_total["n_goods"] += acc["n_goods"]
        field_total["races"] |= acc["races"]
        for k in ("n_hits", "n_net_hits", "bet", "payout"):
            field_total[k] += acc[k]
    field = {
        **_finish(field_total),
        "n_yosoka": len(table),
        "n_ranked": len(ranked_rows),
        "median_roi": median([r["roi"] for r in ranked_rows if r["roi"] is not None]) if ranked_rows else None,
        "median_net_hit_rate": median([r["net_hit_rate"] for r in ranked_rows]) if ranked_rows else None,
    }

    # ── 推移で並べる相手（指定 > 継続上位 > 表示期間の上位） ──
    steady_by_id = {int(c["yosoka_id"]): c for c in steady_candidates or []}
    if compare_ids:
        compare_basis = "manual"
        cmp_ids = [i for i in compare_ids if i in per and i != self_id]
    else:
        compare_basis = "steady"
        cmp_ids = [i for i in steady_by_id if i in per and i != self_id][:n_compare_default]
        if not cmp_ids:
            compare_basis = "period_top"
            cmp_ids = [r["yosoka_id"] for r in table if r["rank"] is not None and not r["is_self"]][:n_compare_default]

    # ── 推移（区間ごと＋累積） ──
    series = []
    cum: dict[int, dict[str, int]] = defaultdict(lambda: {"bet": 0, "payout": 0})
    cum_field = {"bet": 0, "payout": 0}
    for b in sorted(per_bucket):
        brows = []
        for yid, acc in per_bucket[b].items():
            brows.append({"yosoka_id": yid, **_finish(acc)})
            cum[yid]["bet"] += acc["bet"]
            cum[yid]["payout"] += acc["payout"]
        n_b = _assign_ranks(brows, metric, min_goods_per_bucket, key="rank")
        by_id = {x["yosoka_id"]: x for x in brows}
        fb = field_bucket[b]
        cum_field["bet"] += fb["bet"]
        cum_field["payout"] += fb["payout"]
        ranked_b = [x for x in brows if x["rank"] is not None and x["roi"] is not None]
        me = by_id.get(self_id)
        point: dict[str, Any] = {
            "bucket": b,
            "n_ranked": n_b,
            "self": None
            if me is None
            else {
                "n_goods": me["n_goods"],
                "n_net_hits": me["n_net_hits"],
                "bet": me["bet"],
                "payout": me["payout"],
                "roi": me["roi"],
                "net_hit_rate": me["net_hit_rate"],
                "rank": me["rank"],
            },
            "self_cum_roi": (cum[self_id]["payout"] / cum[self_id]["bet"]) if cum[self_id]["bet"] else None,
            "field_roi": (fb["payout"] / fb["bet"]) if fb["bet"] else None,
            "field_cum_roi": (cum_field["payout"] / cum_field["bet"]) if cum_field["bet"] else None,
            "median_roi": median([x["roi"] for x in ranked_b]) if ranked_b else None,
            "compare_cum_roi": {str(i): (cum[i]["payout"] / cum[i]["bet"]) if cum[i]["bet"] else None for i in cmp_ids},
        }
        series.append(point)

    return {
        "metric": metric,
        "min_goods": min_goods,
        "granularity": granularity,
        "min_goods_per_bucket": min_goods_per_bucket,
        "self_id": self_id,
        "self": self_row,
        "n_ranked": n_ranked,
        "field": field,
        "ranking": table,
        "compare_basis": compare_basis,
        "compare": [
            {
                "yosoka_id": i,
                "yosoka_name": names.get(i),
                **{k: v for k, v in steady_by_id.get(i, {}).items() if k.startswith(("lookback_", "quarters_"))},
            }
            for i in cmp_ids
        ],
        "series": series,
    }
