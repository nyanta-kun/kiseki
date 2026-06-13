"""バンクロール シミュレーション。

T05 の SettleResult を入力に、配分方式（均等/Kelly 0.1/0.25/0.5）別の
資金曲線・最大ドローダウン・破産確率（モンテカルロ 1万系列）を比較する。

## 使い方

```bash
# SettleResult JSON を入力してシミュレーション
cd backend
.venv/bin/python scripts/simulate_bankroll.py \\
    --input /tmp/settle_results.json \\
    --initial-bankroll 100000 \\
    --n-simulations 10000 \\
    --output /tmp/bankroll_sim.json

# パラメータのみでシンプル比較（SettleResult なしで直接指定）
.venv/bin/python scripts/simulate_bankroll.py \\
    --hit-rate 0.073 \\
    --avg-return 15.0 \\
    --n-bets 100 \\
    --initial-bankroll 100000
```

## 入力形式（--input JSON）

settle_results.json は以下の形式を想定:
```json
{
  "bets": [
    {"stake": 1000, "payout": 15000, "hit": true},
    {"stake": 1000, "payout": 0, "hit": false}
  ]
}
```
または T05 の SettleResult オブジェクトの JSON シリアライズ形式。

## 出力

各配分方式について:
- 最終バンクロールの平均・中央値・5/25/75/95 パーセンタイル
- 最大ドローダウン（平均・最大）
- 破産確率（バンクロール < 初期の 10% になった割合）
- 月次推移グラフ用データ（オプション）
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class BetRecord:
    """単一ベット記録。"""

    stake_fraction: float  # bankroll に対する投資比率
    odds: float            # 払戻倍率（的中時）
    hit: bool              # 的中フラグ


@dataclass
class SimulationResult:
    """シミュレーション結果。"""

    method: str
    n_simulations: int
    n_bets: int
    initial_bankroll: float

    # 最終バンクロール統計
    final_mean: float
    final_median: float
    final_p05: float
    final_p25: float
    final_p75: float
    final_p95: float

    # ドローダウン
    max_drawdown_mean: float
    max_drawdown_max: float

    # 破産確率（< 10% of initial）
    ruin_probability: float

    # ROI
    expected_roi: float


def _max_drawdown(curve: list[float]) -> float:
    """資金曲線の最大ドローダウン（peak-to-trough 割合）を計算する。

    Args:
        curve: バンクロール推移リスト

    Returns:
        最大ドローダウン（0.0〜1.0。0.20 = 20% 下落）
    """
    peak = curve[0]
    max_dd = 0.0
    for v in curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _simulate_one_path(
    records: list[BetRecord],
    initial: float,
    method: str,
    kelly_fraction: float = 0.25,
    ruin_threshold: float = 0.10,
) -> tuple[list[float], bool]:
    """1 シミュレーションパスを実行する。

    Args:
        records: ベット記録列（順序はランダムにシャッフル済みの想定）
        initial: 初期バンクロール
        method: "flat" / "kelly_{fraction}" の文字列
        kelly_fraction: Kelly 係数（method="kelly_*" の場合に使用）
        ruin_threshold: 破産とみなすバンクロール割合

    Returns:
        (資金曲線, 破産フラグ)
    """
    bankroll = initial
    curve = [bankroll]
    ruined = False

    for rec in records:
        if bankroll <= 0:
            ruined = True
            break

        if method == "flat":
            stake = rec.stake_fraction * initial  # 固定金額
        else:
            # Kelly: stake = kelly_fraction * f* * bankroll
            # ここでは stake_fraction を Kelly 比率として扱う
            net_odds = rec.odds - 1.0
            if net_odds <= 0:
                continue
            # rec.stake_fraction はあらかじめ計算済みの full kelly ratio
            f_full = rec.stake_fraction
            f_frac = kelly_fraction * f_full
            stake = max(0.0, f_frac * bankroll)
            stake = min(stake, bankroll)  # 全財産超えない

        stake = min(stake, bankroll)
        if rec.hit:
            bankroll += stake * (rec.odds - 1.0)
        else:
            bankroll -= stake

        curve.append(bankroll)

        if bankroll < initial * ruin_threshold:
            ruined = True
            break

    return curve, ruined


def run_monte_carlo(
    records: list[BetRecord],
    initial_bankroll: float,
    methods: list[tuple[str, float]],
    n_simulations: int = 10_000,
    seed: int = 42,
) -> list[SimulationResult]:
    """モンテカルロ シミュレーションを実行する。

    Args:
        records: ベット記録列（順序がランダム化される）
        initial_bankroll: 初期バンクロール
        methods: (方式名, Kelly係数) のリスト。
            例: [("flat", 0), ("kelly_0.1", 0.1), ("kelly_0.25", 0.25), ("kelly_0.5", 0.5)]
        n_simulations: シミュレーション回数
        seed: 乱数シード

    Returns:
        各方式の SimulationResult リスト
    """
    rng = random.Random(seed)
    results = []

    for method_name, kf in methods:
        finals: list[float] = []
        drawdowns: list[float] = []
        ruin_count = 0

        for _ in range(n_simulations):
            shuffled = records[:]
            rng.shuffle(shuffled)
            curve, ruined = _simulate_one_path(
                shuffled,
                initial_bankroll,
                method=method_name,
                kelly_fraction=kf,
            )
            finals.append(curve[-1])
            drawdowns.append(_max_drawdown(curve))
            if ruined:
                ruin_count += 1

        finals.sort()
        n = len(finals)
        p = lambda pct: finals[max(0, min(n - 1, int(n * pct / 100)))]  # noqa: E731

        results.append(
            SimulationResult(
                method=method_name,
                n_simulations=n_simulations,
                n_bets=len(records),
                initial_bankroll=initial_bankroll,
                final_mean=sum(finals) / n,
                final_median=p(50),
                final_p05=p(5),
                final_p25=p(25),
                final_p75=p(75),
                final_p95=p(95),
                max_drawdown_mean=sum(drawdowns) / len(drawdowns),
                max_drawdown_max=max(drawdowns),
                ruin_probability=ruin_count / n_simulations,
                expected_roi=sum(finals) / n / initial_bankroll,
            )
        )

    return results


def records_from_settle_json(data: dict[str, Any]) -> list[BetRecord]:
    """settle_results JSON からベット記録を作成する。

    Args:
        data: JSON データ（{"bets": [{"stake": int, "payout": int, "hit": bool}]}）

    Returns:
        BetRecord リスト
    """
    bets = data.get("bets", [])
    if not bets:
        raise ValueError("bets が空です")

    # 最大 stake を基準に比率化（flat simulation 用）
    max_stake = max(b["stake"] for b in bets)

    records = []
    for b in bets:
        stake = b["stake"]
        payout = b.get("payout", 0)
        hit = b.get("hit", payout > 0)
        odds = (payout / stake) if (hit and stake > 0) else 2.0  # fallback

        # Kelly 比率の近似: p * (b-1) を使う。
        # ここでは stake / max_stake を「full Kelly ratio」として扱う（近似）
        # 実際の Kelly 計算には est_prob と odds が必要だが、
        # settle_results には確率が含まれないため hit_rate で近似する。
        fraction = stake / max_stake if max_stake > 0 else 0.01

        records.append(BetRecord(stake_fraction=fraction, odds=odds, hit=hit))

    return records


def records_from_params(
    hit_rate: float,
    avg_return: float,
    n_bets: int,
    seed: int = 42,
) -> list[BetRecord]:
    """パラメータからシミュレーション用レコードを生成する。

    Args:
        hit_rate: 的中率（例: 0.073 = 7.3%）
        avg_return: 平均払戻倍率（的中時）
        n_bets: ベット数
        seed: 乱数シード

    Returns:
        BetRecord リスト
    """
    rng = random.Random(seed)
    records = []
    # Kelly ratio ≈ (p * (b-1) - (1-p)) / (b-1)
    net_odds = avg_return - 1.0
    if net_odds > 0:
        full_kelly = max(0.0, (hit_rate * net_odds - (1.0 - hit_rate)) / net_odds)
    else:
        full_kelly = 0.01

    for _ in range(n_bets):
        hit = rng.random() < hit_rate
        records.append(
            BetRecord(
                stake_fraction=full_kelly,
                odds=avg_return,
                hit=hit,
            )
        )
    return records


def print_report(results: list[SimulationResult]) -> None:
    """シミュレーション結果をテーブル形式で出力する。"""
    header = (
        f"{'方式':<15} {'最終平均':>12} {'最終中央':>12} "
        f"{'P05':>10} {'P95':>10} "
        f"{'最大DD平均':>10} {'最大DD最大':>10} "
        f"{'破産確率':>8} {'期待ROI':>8}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r.method:<15} "
            f"{r.final_mean:>12,.0f} "
            f"{r.final_median:>12,.0f} "
            f"{r.final_p05:>10,.0f} "
            f"{r.final_p95:>10,.0f} "
            f"{r.max_drawdown_mean:>10.1%} "
            f"{r.max_drawdown_max:>10.1%} "
            f"{r.ruin_probability:>8.2%} "
            f"{r.expected_roi:>8.3f}"
        )


def main() -> None:
    """CLI エントリポイント。"""
    parser = argparse.ArgumentParser(
        description="バンクロール モンテカルロ シミュレーション"
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        help="SettleResult JSON ファイルパス（--hit-rate 等と排他）",
    )
    parser.add_argument("--hit-rate", type=float, help="的中率（例: 0.073）")
    parser.add_argument("--avg-return", type=float, help="平均払戻倍率（例: 15.0）")
    parser.add_argument("--n-bets", type=int, default=200, help="ベット数")
    parser.add_argument(
        "--initial-bankroll", type=float, default=100_000.0,
        help="初期バンクロール（円）",
    )
    parser.add_argument(
        "--n-simulations", type=int, default=10_000,
        help="モンテカルロ試行回数",
    )
    parser.add_argument("--seed", type=int, default=42, help="乱数シード")
    parser.add_argument(
        "--output", "-o",
        type=Path,
        help="結果を JSON ファイルに保存するパス",
    )
    args = parser.parse_args()

    # データ準備
    if args.input:
        with args.input.open() as f:
            data = json.load(f)
        records = records_from_settle_json(data)
    elif args.hit_rate and args.avg_return:
        records = records_from_params(
            hit_rate=args.hit_rate,
            avg_return=args.avg_return,
            n_bets=args.n_bets,
            seed=args.seed,
        )
    else:
        print("--input または --hit-rate + --avg-return を指定してください", file=sys.stderr)
        sys.exit(1)

    # 配分方式
    methods = [
        ("flat", 0.0),
        ("kelly_0.10", 0.10),
        ("kelly_0.25", 0.25),
        ("kelly_0.50", 0.50),
    ]

    print(f"\n初期バンクロール: {args.initial_bankroll:,.0f}円  ベット数: {len(records)}  試行: {args.n_simulations}\n")

    results = run_monte_carlo(
        records=records,
        initial_bankroll=args.initial_bankroll,
        methods=methods,
        n_simulations=args.n_simulations,
        seed=args.seed,
    )

    print_report(results)

    if args.output:
        out_data = [asdict(r) for r in results]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w") as f:
            json.dump(out_data, f, ensure_ascii=False, indent=2)
        print(f"\n結果を保存: {args.output}")


if __name__ == "__main__":
    main()
