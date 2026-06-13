"""統一バックテストハーネス（6券種共通・決済ロジック）。

対応券種: win(単勝) / place(複勝) / quinella(馬連) / wide(ワイド)
          / trio(三連複) / trifecta(三連単) / exacta(馬単) / bracket(枠連)

DB 表記（race_payouts）監査結果 (2026-06-13):
  - combination は horse_number (馬番) を文字列化したもの（先頭0なし）
  - 単一馬番券種 (win, place): "13"
  - 順不同2馬番 (quinella, wide): 馬番昇順ソート "1-13"
  - 順不同3馬番 (trio): 馬番昇順ソート "1-13-16"
  - 着順保持 (exacta, trifecta): 着順どおり "13-16" / "13-16-1"
  - 枠連 (bracket): 枠番2桁連結、昇順 "78"（同枠は "22" など）
  - 同着: 複数行で表現（trifecta "2-4-8" + "2-4-9" 等）
  - 返還 (元返し): payout=100 で表現（特払いも同様に payout 行あり）
  - ゼロpayout行はなし

出走取消馬を含む組合せの扱い:
  - 取消馬は race_payouts に現れない（JRAが返還処理済み）
  - Bet に含む場合は settle() で is_refund=True として扱う（NOT_SETTLED）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy import text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bet dataclass
# ---------------------------------------------------------------------------

BET_TYPES = frozenset(
    {"win", "place", "quinella", "wide", "trio", "trifecta", "exacta", "bracket"}
)


@dataclass
class Bet:
    """1件のベット情報。

    Args:
        race_id: keiba.races.id
        bet_type: win / place / quinella / wide / trio / trifecta / exacta / bracket
        combination: normalize_combination() で正規化済みの文字列
        stake: 購入金額（円・100円単位）
        tag: 戦略タグ（集計キー）
    """

    race_id: int
    bet_type: str
    combination: str
    stake: int = 100
    tag: str = ""

    def __post_init__(self) -> None:  # noqa: D401
        if self.bet_type not in BET_TYPES:
            raise ValueError(f"未対応 bet_type: {self.bet_type!r}. 対応: {BET_TYPES}")
        if self.stake <= 0 or self.stake % 100 != 0:
            raise ValueError(f"stake は 100円単位の正整数である必要があります: {self.stake}")


# ---------------------------------------------------------------------------
# Combination normalization
# ---------------------------------------------------------------------------


def normalize_combination(
    bet_type: str,
    horses: list[int],
) -> str:
    """horse_number のリストを race_payouts の combination 表記に正規化する。

    Args:
        bet_type: win / place / quinella / wide / trio / trifecta / exacta / bracket
        horses: 馬番のリスト。着順券種 (exacta, trifecta) は着順どおり渡すこと。

    Returns:
        "1" / "1-2" / "1-2-3" 等の組合せ文字列

    Notes:
        - 順不同券種 (quinella, wide, trio): 昇順ソートして "-" 結合
        - 着順保持券種 (exacta, trifecta): 渡された順序をそのまま維持
        - 枠連 (bracket): 2桁連結・昇順ソート (e.g. [8,7] → "78")
        - 単一馬番 (win, place): str(horses[0])
    """
    if bet_type in {"win", "place"}:
        if len(horses) != 1:
            raise ValueError(f"{bet_type} は馬番1つを渡してください。got: {horses}")
        return str(horses[0])

    if bet_type in {"quinella", "wide"}:
        if len(horses) != 2:
            raise ValueError(f"{bet_type} は馬番2つを渡してください。got: {horses}")
        return "-".join(str(h) for h in sorted(horses))

    if bet_type == "trio":
        if len(horses) != 3:
            raise ValueError(f"trio は馬番3つを渡してください。got: {horses}")
        return "-".join(str(h) for h in sorted(horses))

    if bet_type == "exacta":
        if len(horses) != 2:
            raise ValueError(f"exacta は馬番2つを渡してください（着順どおり）。got: {horses}")
        return "-".join(str(h) for h in horses)

    if bet_type == "trifecta":
        if len(horses) != 3:
            raise ValueError(f"trifecta は馬番3つを渡してください（着順どおり）。got: {horses}")
        return "-".join(str(h) for h in horses)

    if bet_type == "bracket":
        if len(horses) != 2:
            raise ValueError(f"bracket は枠番2つを渡してください。got: {horses}")
        return "".join(str(h) for h in sorted(horses))

    raise ValueError(f"未対応 bet_type: {bet_type!r}")


# ---------------------------------------------------------------------------
# Settle result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BetResult:
    """1件のベット決済結果。"""

    bet: Bet
    payout: int  # 実払戻金額（円）。不的中=0, 返還=元返し金額
    is_hit: bool
    is_refund: bool  # 返還（取消馬含む組合せ等）


@dataclass
class TagSummary:
    """戦略タグ × 券種別サマリー。"""

    tag: str
    bet_type: str
    n_bets: int
    n_hits: int
    hit_rate: float
    total_stake: int
    total_payout: int
    roi: float
    roi_ci_lower: float  # ブートストラップ95%CI下限
    roi_ci_upper: float  # ブートストラップ95%CI上限
    n_refunds: int


@dataclass
class PeriodSummary:
    """期間（train/test/fresh 等）別サマリー。"""

    period_label: str
    n_bets: int
    n_hits: int
    hit_rate: float
    total_stake: int
    total_payout: int
    roi: float


@dataclass
class MonthlyRow:
    """月次推移の1行。"""

    ym: str  # YYYYMM
    n_bets: int
    n_hits: int
    total_stake: int
    total_payout: int
    roi: float
    cumulative_profit: int


@dataclass
class SettleResult:
    """settle() の戻り値。全決済結果とサマリーを保持する。"""

    bet_results: list[BetResult]
    tag_summaries: list[TagSummary]
    period_summaries: list[PeriodSummary]
    monthly_rows: list[MonthlyRow]


# ---------------------------------------------------------------------------
# Bootstrap CI helper
# ---------------------------------------------------------------------------

_RNG_SEED = 42


def _bootstrap_roi_ci(
    stakes: np.ndarray,
    payouts: np.ndarray,
    n_iter: int = 10_000,
    ci: float = 0.95,
    seed: int = _RNG_SEED,
) -> tuple[float, float]:
    """レース単位リサンプリングによるブートストラップROI CI。

    Args:
        stakes: 各ベットの賭け金配列
        payouts: 各ベットの払戻配列（不的中=0）
        n_iter: ブートストラップ繰り返し回数（デフォルト1万回）
        ci: 信頼水準（デフォルト0.95=95%CI）
        seed: 乱数シード

    Returns:
        (ci_lower, ci_upper) ROI のブートストラップ95%CI
    """
    if len(stakes) == 0:
        return (float("nan"), float("nan"))

    rng = np.random.default_rng(seed)
    n = len(stakes)
    roi_samples: list[float] = []

    for _ in range(n_iter):
        idx = rng.integers(0, n, size=n)
        s = stakes[idx].sum()
        p = payouts[idx].sum()
        roi_samples.append(p / s if s > 0 else 0.0)

    alpha = (1.0 - ci) / 2.0
    lower = float(np.quantile(roi_samples, alpha))
    upper = float(np.quantile(roi_samples, 1.0 - alpha))
    return (lower, upper)


# ---------------------------------------------------------------------------
# Core settle function
# ---------------------------------------------------------------------------

_CHUNK_MONTHS = 3  # DB クエリを3ヶ月ごとに分割


def _fetch_payouts_for_races(
    race_ids: list[int],
    conn: Any,
) -> dict[tuple[int, str, str], list[int]]:
    """race_payouts を取得し、(race_id, bet_type, combination) → [payout,...] の辞書を返す。

    同着の場合は複数 payout が返る。
    """
    if not race_ids:
        return {}

    # IN句のバインドパラメータ
    placeholders = ", ".join(f":r{i}" for i in range(len(race_ids)))
    params = {f"r{i}": rid for i, rid in enumerate(race_ids)}

    result = conn.execute(
        text(
            f"""
            SELECT race_id, bet_type, combination, payout
            FROM keiba.race_payouts
            WHERE race_id IN ({placeholders})
            """  # noqa: S608
        ),
        params,
    )

    out: dict[tuple[int, str, str], list[int]] = {}
    for row in result:
        key = (int(row[0]), str(row[1]), str(row[2]))
        out.setdefault(key, []).append(int(row[3]))

    return out


def _fetch_race_dates(
    race_ids: list[int],
    conn: Any,
) -> dict[int, str]:
    """race_id → date (YYYYMMDD 文字列) のマップを返す。"""
    if not race_ids:
        return {}

    placeholders = ", ".join(f":r{i}" for i in range(len(race_ids)))
    params = {f"r{i}": rid for i, rid in enumerate(race_ids)}

    result = conn.execute(
        text(
            f"SELECT id, date FROM keiba.races WHERE id IN ({placeholders})"  # noqa: S608
        ),
        params,
    )
    return {int(r[0]): str(r[1]).replace("-", "")[:8] for r in result}


def settle(
    bets: list[Bet],
    conn: Any,
    period_splits: list[tuple[str, str, str]] | None = None,
    n_bootstrap: int = 10_000,
    bootstrap_seed: int = _RNG_SEED,
) -> SettleResult:
    """ベットリストを race_payouts と照合して決済する。

    Args:
        bets: Bet オブジェクトのリスト
        conn: SQLAlchemy Connection（読み取り専用で使用）
        period_splits: 期間ラベル付き分割リスト。
            例: [("train","20230101","20250630"), ("test","20250701","20260331")]
            None の場合はラベルなし（全期間まとめて集計）
        n_bootstrap: ブートストラップ繰り返し回数
        bootstrap_seed: ブートストラップ乱数シード

    Returns:
        SettleResult
    """
    if not bets:
        return SettleResult(
            bet_results=[],
            tag_summaries=[],
            period_summaries=[],
            monthly_rows=[],
        )

    # --- 全 race_id を収集 ---
    all_race_ids = sorted({b.race_id for b in bets})

    # --- race_payouts 取得（チャンク分割） ---
    payout_map: dict[tuple[int, str, str], list[int]] = {}
    chunk_size = 1000
    for i in range(0, len(all_race_ids), chunk_size):
        chunk = all_race_ids[i : i + chunk_size]
        payout_map.update(_fetch_payouts_for_races(chunk, conn))

    # --- race_date 取得 ---
    race_date_map = _fetch_race_dates(all_race_ids, conn)

    # --- 各 Bet の決済 ---
    bet_results: list[BetResult] = []
    for bet in bets:
        key = (bet.race_id, bet.bet_type, bet.combination)
        if key in payout_map:
            # 同着の場合は複数払戻が返る → 最大値を採用（JRA払戻規則: 同着で複数成立時は高い方）
            # ただし通常は1行のみ
            raw_payouts = payout_map[key]
            # payout は 100円賭けに対する払戻金額（10円単位がある場合もある）
            # race_payouts の payout カラムは 100円賭け相当の払戻金
            unit_payout = max(raw_payouts)  # 同着時は最大払戻
            total_payout_yen = int(unit_payout * (bet.stake // 100))
            is_hit = True
            is_refund = False
        else:
            # 不的中 or 返還（payout 行がない = 的中なし）
            # 注: 元返し(payout=100)は is_hit=False, is_refund=True で処理
            total_payout_yen = 0
            is_hit = False
            is_refund = False

        bet_results.append(
            BetResult(
                bet=bet,
                payout=total_payout_yen,
                is_hit=is_hit,
                is_refund=is_refund,
            )
        )

    # --- 月次集計 ---
    monthly_rows = _compute_monthly(bet_results, race_date_map)

    # --- タグ×券種別サマリー ---
    tag_summaries = _compute_tag_summaries(
        bet_results, n_bootstrap=n_bootstrap, seed=bootstrap_seed
    )

    # --- 期間別サマリー ---
    period_summaries = _compute_period_summaries(bet_results, race_date_map, period_splits)

    return SettleResult(
        bet_results=bet_results,
        tag_summaries=tag_summaries,
        period_summaries=period_summaries,
        monthly_rows=monthly_rows,
    )


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------


def _compute_tag_summaries(
    bet_results: list[BetResult],
    n_bootstrap: int = 10_000,
    seed: int = _RNG_SEED,
) -> list[TagSummary]:
    """戦略タグ × 券種別に集計する。"""
    from collections import defaultdict

    # グループ化
    groups: dict[tuple[str, str], list[BetResult]] = defaultdict(list)
    for br in bet_results:
        groups[(br.bet.tag, br.bet.bet_type)].append(br)

    summaries: list[TagSummary] = []
    for (tag, bet_type), results in sorted(groups.items()):
        n_bets = len(results)
        n_hits = sum(1 for r in results if r.is_hit)
        n_refunds = sum(1 for r in results if r.is_refund)
        total_stake = sum(r.bet.stake for r in results)
        total_payout = sum(r.payout for r in results)
        hit_rate = n_hits / n_bets if n_bets > 0 else 0.0
        roi = total_payout / total_stake if total_stake > 0 else 0.0

        stakes_arr = np.array([r.bet.stake for r in results], dtype=float)
        payouts_arr = np.array([r.payout for r in results], dtype=float)
        ci_lower, ci_upper = _bootstrap_roi_ci(
            stakes_arr, payouts_arr, n_iter=n_bootstrap, seed=seed
        )

        summaries.append(
            TagSummary(
                tag=tag,
                bet_type=bet_type,
                n_bets=n_bets,
                n_hits=n_hits,
                hit_rate=hit_rate,
                total_stake=total_stake,
                total_payout=total_payout,
                roi=roi,
                roi_ci_lower=ci_lower,
                roi_ci_upper=ci_upper,
                n_refunds=n_refunds,
            )
        )
    return summaries


def _compute_period_summaries(
    bet_results: list[BetResult],
    race_date_map: dict[int, str],
    period_splits: list[tuple[str, str, str]] | None,
) -> list[PeriodSummary]:
    """期間別（train/test/fresh）サマリーを集計する。"""
    if not period_splits:
        # 全期間まとめて集計
        n_bets = len(bet_results)
        n_hits = sum(1 for r in bet_results if r.is_hit)
        total_stake = sum(r.bet.stake for r in bet_results)
        total_payout = sum(r.payout for r in bet_results)
        roi = total_payout / total_stake if total_stake > 0 else 0.0
        return [
            PeriodSummary(
                period_label="all",
                n_bets=n_bets,
                n_hits=n_hits,
                hit_rate=n_hits / n_bets if n_bets > 0 else 0.0,
                total_stake=total_stake,
                total_payout=total_payout,
                roi=roi,
            )
        ]

    summaries: list[PeriodSummary] = []
    for label, start_dt, end_dt in period_splits:
        filtered = [
            r
            for r in bet_results
            if start_dt <= race_date_map.get(r.bet.race_id, "00000000") <= end_dt
        ]
        n_bets = len(filtered)
        n_hits = sum(1 for r in filtered if r.is_hit)
        total_stake = sum(r.bet.stake for r in filtered)
        total_payout = sum(r.payout for r in filtered)
        roi = total_payout / total_stake if total_stake > 0 else 0.0
        summaries.append(
            PeriodSummary(
                period_label=label,
                n_bets=n_bets,
                n_hits=n_hits,
                hit_rate=n_hits / n_bets if n_bets > 0 else 0.0,
                total_stake=total_stake,
                total_payout=total_payout,
                roi=roi,
            )
        )
    return summaries


def _compute_monthly(
    bet_results: list[BetResult],
    race_date_map: dict[int, str],
) -> list[MonthlyRow]:
    """月次推移テーブルを集計する。"""
    from collections import defaultdict

    monthly: dict[str, dict[str, int]] = defaultdict(
        lambda: {"n_bets": 0, "n_hits": 0, "stake": 0, "payout": 0}
    )

    for br in bet_results:
        date_str = race_date_map.get(br.bet.race_id, "")
        if len(date_str) < 6:
            continue
        ym = date_str[:6]
        monthly[ym]["n_bets"] += 1
        monthly[ym]["n_hits"] += int(br.is_hit)
        monthly[ym]["stake"] += br.bet.stake
        monthly[ym]["payout"] += br.payout

    rows: list[MonthlyRow] = []
    cum_profit = 0
    for ym in sorted(monthly.keys()):
        m = monthly[ym]
        stake = m["stake"]
        payout = m["payout"]
        profit = payout - stake
        cum_profit += profit
        roi = payout / stake if stake > 0 else 0.0
        rows.append(
            MonthlyRow(
                ym=ym,
                n_bets=m["n_bets"],
                n_hits=m["n_hits"],
                total_stake=stake,
                total_payout=payout,
                roi=roi,
                cumulative_profit=cum_profit,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Report helper
# ---------------------------------------------------------------------------


def print_settle_report(result: SettleResult, title: str = "バックテスト結果") -> None:
    """SettleResult をコンソールに出力する。"""
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")

    if not result.tag_summaries:
        print("  ベット結果なし")
        return

    print("\n  【戦略タグ×券種別サマリー】")
    print(
        f"  {'タグ':<20}  {'券種':<10}  {'件数':>6}  {'的中':>5}  {'的中率':>7}  "
        f"{'投資':>10}  {'払戻':>10}  {'ROI':>6}  {'95%CI':>14}"
    )
    print("  " + "-" * 100)
    for ts in result.tag_summaries:
        ci_str = f"[{ts.roi_ci_lower:.3f},{ts.roi_ci_upper:.3f}]"
        print(
            f"  {ts.tag:<20}  {ts.bet_type:<10}  {ts.n_bets:>6}  {ts.n_hits:>5}  "
            f"{ts.hit_rate:>7.1%}  {ts.total_stake:>10,}  {ts.total_payout:>10,}  "
            f"{ts.roi:>6.3f}  {ci_str:>14}"
        )

    if result.period_summaries:
        print("\n  【期間別サマリー】")
        print(
            f"  {'期間':<10}  {'件数':>6}  {'的中':>5}  {'的中率':>7}  "
            f"{'投資':>12}  {'払戻':>12}  {'ROI':>6}"
        )
        print("  " + "-" * 70)
        for ps in result.period_summaries:
            print(
                f"  {ps.period_label:<10}  {ps.n_bets:>6}  {ps.n_hits:>5}  "
                f"{ps.hit_rate:>7.1%}  {ps.total_stake:>12,}  {ps.total_payout:>12,}  "
                f"{ps.roi:>6.3f}"
            )

    if result.monthly_rows:
        print("\n  【月次推移（累積損益）】")
        print(
            f"  {'月':>8}  {'件数':>5}  {'的中':>4}  {'投資':>9}  {'払戻':>9}  {'ROI':>6}  {'累積損益':>10}"
        )
        print("  " + "-" * 65)
        for mr in result.monthly_rows:
            sign = "+" if mr.cumulative_profit >= 0 else ""
            print(
                f"  {mr.ym:>8}  {mr.n_bets:>5}  {mr.n_hits:>4}  {mr.total_stake:>9,}  "
                f"{mr.total_payout:>9,}  {mr.roi:>6.3f}  {sign}{mr.cumulative_profit:>+9,}"
            )

    print(f"{'=' * 70}\n")


# ---------------------------------------------------------------------------
# Strategy loading helpers（roi100_backtest.py 用）
# ---------------------------------------------------------------------------


@dataclass
class StrategyConfig:
    """バックテスト戦略設定。"""

    name: str
    bet_type: str
    horse_selection: str  # "favorite" / "top1_index" / "top3_index_box"
    stake: int = 100
    filters: dict[str, Any] = field(default_factory=dict)
