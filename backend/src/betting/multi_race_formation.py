"""複数レース重勝式（WIN5 / 地方重勝式）の「足の幅」配分アルゴリズム。

WIN5（中央・5レースの1着を当てる）や地方の重勝式（3レースの馬単など）では、
「どのレースを何頭に広げるか」= (k_1, ..., k_n) の決定がそのまま点数と的中率を決める。
本モジュールはこの配分を予算制約つき最適化として解く純ロジックを提供する。

## 定式化

各レース i の出走馬に勝率 p_{i,1} >= p_{i,2} >= ... が与えられているとき、
上位 k_i 頭を買う買い目について:

    カバレッジ  cov_i(k_i) = Σ_{j<=k_i} p_{i,j}   （そのレースで勝ち馬を含む確率）
    総点数      T          = Π_i k_i
    的中確率    P          = Π_i cov_i(k_i)       （レース間独立を仮定）

予算 B 点（= 金額 ÷ 1点単価）以下で P を最大化する。対数を取ると

    maximize  Σ_i log cov_i(k_i)   subject to   Σ_i log k_i <= log B

となり、log 空間のナップサック問題になる。本モジュールは 2 通りの解法を持つ:

  - **厳密 DP** (`_solve_exact`): 点数を状態とする動的計画法。到達可能な点数の集合は
    「k_i の積」に限られて疎なので、WIN5 規模（5レース × 最大18頭・B <= 数十万点）では実用的。
  - **限界効用による貪欲** (`_solve_greedy`): Δlog cov / Δlog k が最大のレースから順に広げ、
    そのあと 1〜2 レースを別の頭数に置き換えて埋め直す局所探索をかける。
    点数が積で効くため増分貪欲だけでは予算に端数が残るが、局所探索がそれを回収する。

両者が一致することは tests/test_multi_race_formation.py で確認している（実務レンジの分布・
予算では一致する）。ただし整数制約由来のギャップは原理的には消えず、極端な分布を乱数生成した
400 ケースでは一致率 99.3%・最悪でも厳密解の 96.3% の的中確率だった。**本番の推奨値には
`method="exact"`（既定）を使うこと。** 貪欲は「なぜこの配分なのか」を限界効用の順序で
説明するための補助であり、WIN5 規模では速度上の必要性はない。

## 入力の前提（重要）

**カバレッジは「勝率の和」なので、入力の勝率が較正 (calibrated) されていないと意味を持たない。**
本リポジトリの JRA `win_probability` は is_win LGB ヘッド + レース内正規化で ECE 0.0027 と
較正済みであり、そのまま投入できる。地方など較正が甘い確率を渡した場合、返される
`hit_probability` はその較正誤差をそのまま引き継ぐ。
**入力確率の較正は本モジュールの責務ではない**（純関数であり、DB・モデルに一切依存しない）。

期待する入力は「レース内で降順・合計 1.0 に正規化された勝率」。降順でない場合は降順に
並べ替え、合計が 1.0 から外れている場合は安全弁として再正規化する（p <= 0 の馬は除外）。

## 利用例

    races = [
        RaceCandidates(race_id="1R", win_probs=[0.42, 0.18, 0.12, ...]),
        ...
    ]
    plan = optimize_formation(races, budget_yen=100_000)   # WIN5 は 1点100円
    plan.total_tickets      # 総点数
    plan.hit_probability    # 的中確率
    plan.next_expansion     # 予算をあと少し増やすならどのレースを広げるか

    # 「レース3は必ず2頭にする」といったユーザー指定は fixed_picks で表現する
    races[2] = replace(races[2], fixed_picks=2)
    plan = optimize_formation(races, budget_yen=100_000)
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

# 1 点あたりの単価（円）。WIN5 は 100 円固定、地方重勝式も 100 円が基本。
DEFAULT_UNIT_PRICE = 100

# 合計 1.0 からのずれがこの値を超えたら安全弁として再正規化する。
_NORMALIZE_TOLERANCE = 1e-9

# 勝率がこの値以下の馬は log(0) を踏まないよう買い目候補から除外する。
_MIN_PROBABILITY = 1e-12

# 貪欲法の局所探索で「改善した」と見なす log スコア差の下限（浮動小数のゆらぎ対策）。
_SCORE_EPS = 1e-15


@dataclass(frozen=True)
class RaceCandidates:
    """重勝式の 1 レース分の入力。

    Attributes:
        race_id: レース識別子（表示・検証用。一意である必要はあるが形式は問わない）
        win_probs: レース内で正規化された勝率列（降順推奨。降順でなければ内部で並べ替える）
        horses: win_probs に対応する馬番列（省略可。指定時は買い目の馬番を出力に含める）
        max_picks: このレースで買える最大頭数（省略時は出走頭数 = 有効な勝率の数）
        fixed_picks: ユーザーが固定した頭数（指定時、最適化はこのレースを動かさない）
    """

    race_id: str
    win_probs: Sequence[float]
    horses: Sequence[int] | None = None
    max_picks: int | None = None
    fixed_picks: int | None = None


@dataclass(frozen=True)
class RaceAllocation:
    """1 レースへの配分結果。

    Attributes:
        race_id: レース識別子
        picks: 買う頭数 k_i
        coverage: cov_i(k_i)（このレースで勝ち馬を含む確率）
        selected_horses: 買い目の馬番（入力で horses を渡した場合のみ。未指定なら空タプル）
        available: 買い目候補にできる頭数（p > 0 かつ max_picks 以内）
        fixed: ユーザー固定制約によって決まった値か
        next_picks: あと 1 頭広げたときの頭数（上限に達していれば None）
        next_coverage: あと 1 頭広げたときのカバレッジ（上限に達していれば None）
        marginal_utility: あと 1 頭広げたときの限界効用 Δlog cov / Δlog k（上限なら None）
    """

    race_id: str
    picks: int
    coverage: float
    selected_horses: tuple[int, ...] = ()
    available: int = 0
    fixed: bool = False
    next_picks: int | None = None
    next_coverage: float | None = None
    marginal_utility: float | None = None


@dataclass(frozen=True)
class FormationExpansion:
    """「予算をあと増やすならどこを広げるか」の推奨 1 手。

    Attributes:
        race_id: 広げる対象レース
        from_picks: 現在の頭数
        to_picks: 広げた後の頭数（+1 頭）
        coverage_gain: カバレッジの増分
        marginal_utility: 限界効用 Δlog cov / Δlog k（大きいほど「点数あたりの効き」が良い）
        additional_tickets: 増える点数
        additional_cost: 増える金額（円）
        hit_probability_after: 広げた後の的中確率
    """

    race_id: str
    from_picks: int
    to_picks: int
    coverage_gain: float
    marginal_utility: float
    additional_tickets: int
    additional_cost: int
    hit_probability_after: float


@dataclass(frozen=True)
class FormationPlan:
    """配分結果と検証用メトリクス一式。

    Attributes:
        allocations: レースごとの配分（入力順）
        total_tickets: 総点数 Π k_i
        total_cost: 総額（円）= total_tickets * unit_price
        hit_probability: 全レース的中確率 Π cov_i(k_i)
        max_tickets: 制約に使った点数上限
        unit_price: 1 点あたりの単価（円）
        budget_yen: 予算（円）。max_tickets 指定で呼ばれた場合は換算値
        within_budget: 総点数が予算内に収まっているか（固定制約だけで超過する場合 False）
        method: 使用した解法（"exact" / "greedy" / "fixed"）
        next_expansion: 予算を増やす場合の推奨拡張（これ以上広げられなければ None）
    """

    allocations: list[RaceAllocation] = field(default_factory=list)
    total_tickets: int = 1
    total_cost: int = 0
    hit_probability: float = 0.0
    max_tickets: int = 1
    unit_price: int = DEFAULT_UNIT_PRICE
    budget_yen: int = 0
    within_budget: bool = True
    method: str = "exact"
    next_expansion: FormationExpansion | None = None


# ---------------------------------------------------------------------------
# 予算換算
# ---------------------------------------------------------------------------


def budget_to_max_tickets(budget_yen: int, unit_price: int = DEFAULT_UNIT_PRICE) -> int:
    """金額の予算を点数上限に変換する。

    WIN5 は 1 点 100 円なので 100,000 円 -> 1,000 点。端数は切り捨てる。

    Args:
        budget_yen: 予算（円・0 以上）
        unit_price: 1 点あたりの単価（円・1 以上）

    Returns:
        買える最大点数（0 以上）

    Raises:
        ValueError: budget_yen が負、または unit_price が 1 未満のとき
    """
    if budget_yen < 0:
        raise ValueError(f"budget_yen は 0 以上である必要があります: {budget_yen}")
    if unit_price < 1:
        raise ValueError(f"unit_price は 1 以上である必要があります: {unit_price}")
    return int(budget_yen // unit_price)


# ---------------------------------------------------------------------------
# 入力の前処理
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PreparedRace:
    """内部用: 正規化・整列済みのレース情報。

    Attributes:
        race_id: レース識別子
        coverage: coverage[k-1] = 上位 k 頭のカバレッジ（k = 1..available）
        horses: coverage と同じ並びの馬番（未指定なら空タプル）
        fixed_picks: 固定制約（None なら最適化対象）
    """

    race_id: str
    coverage: tuple[float, ...]
    horses: tuple[int, ...]
    fixed_picks: int | None


def _prepare_race(race: RaceCandidates) -> _PreparedRace:
    """1 レース分の入力を検証し、累積カバレッジ列に変換する。

    - p <= 0（および NaN）の馬を除外する（log(0) 回避）
    - 勝率降順に並べ替える（horses を渡していれば対応関係を保つ）
    - max_picks / 出走頭数で候補を打ち切る
    - 合計が 1.0 から外れていれば安全弁として再正規化する

    Args:
        race: 入力レース

    Returns:
        _PreparedRace（coverage は狭義単調増加）

    Raises:
        ValueError: 有効な勝率が 1 つもない、horses の長さが不一致、
            fixed_picks が候補頭数の範囲外のとき
    """
    probs = list(race.win_probs)
    horses = list(race.horses) if race.horses is not None else []
    if horses and len(horses) != len(probs):
        raise ValueError(
            f"{race.race_id}: horses の長さ({len(horses)})が win_probs({len(probs)})と一致しません"
        )

    pairs: list[tuple[float, int]] = []
    for idx, prob in enumerate(probs):
        if not math.isfinite(prob) or prob <= _MIN_PROBABILITY:
            continue  # p=0 の馬は log(0) を踏むため候補から外す
        pairs.append((float(prob), horses[idx] if horses else 0))

    if not pairs:
        raise ValueError(f"{race.race_id}: 有効な勝率（p > 0）が 1 頭もありません")

    # 勝率降順。同率は馬番昇順で決定的にする。
    pairs.sort(key=lambda x: (-x[0], x[1]))

    total = sum(p for p, _ in pairs)
    if total <= 0.0:
        raise ValueError(f"{race.race_id}: 勝率の合計が 0 以下です")
    if abs(total - 1.0) > _NORMALIZE_TOLERANCE:
        # 入力は正規化済みが前提だが、除外・丸めのずれを吸収する安全弁。
        pairs = [(p / total, h) for p, h in pairs]

    limit = len(pairs)
    if race.max_picks is not None:
        if race.max_picks < 1:
            raise ValueError(f"{race.race_id}: max_picks は 1 以上である必要があります: {race.max_picks}")
        limit = min(limit, race.max_picks)

    coverage: list[float] = []
    acc = 0.0
    for prob, _ in pairs[:limit]:
        acc += prob
        coverage.append(min(acc, 1.0))

    fixed = race.fixed_picks
    if fixed is not None and not 1 <= fixed <= limit:
        raise ValueError(
            f"{race.race_id}: fixed_picks={fixed} が候補頭数 1..{limit} の範囲外です"
        )

    return _PreparedRace(
        race_id=race.race_id,
        coverage=tuple(coverage),
        horses=tuple(h for _, h in pairs[:limit]) if horses else (),
        fixed_picks=fixed,
    )


def _marginal_utility(coverage: Sequence[float], current: int, target: int) -> float:
    """k=current から k=target へ広げるときの限界効用を返す。

    限界効用 = Δlog cov / Δlog k
        = (log cov(target) - log cov(current)) / (log target - log current)

    「1 点あたりどれだけカバレッジの対数が伸びるか」であり、log 空間ナップサックにおける
    価値／重量比にあたる。

    Args:
        coverage: 累積カバレッジ列（coverage[k-1]）
        current: 現在の頭数（1 以上）
        target: 広げた後の頭数（current より大きいこと）

    Returns:
        限界効用（点数が増えないケースは 0.0 を返す）
    """
    if target <= current:
        return 0.0
    denom = math.log(target) - math.log(current)
    if denom <= 0.0:
        return 0.0
    return (math.log(coverage[target - 1]) - math.log(coverage[current - 1])) / denom


# ---------------------------------------------------------------------------
# ソルバ
# ---------------------------------------------------------------------------


def _solve_exact(prepared: Sequence[_PreparedRace], max_tickets: int) -> list[int]:
    """厳密 DP で最適な (k_1, ..., k_n) を求める。

    点数（k_i の積）を状態とする DP。到達可能な点数は「小さい整数の積」に限られるため
    状態数は max_tickets よりはるかに少なく、WIN5 規模では十分高速。
    固定制約のあるレースは選択肢を 1 つに絞ることで同じ DP に載せる。

    同スコアのときは点数が少ない解を優先する。

    Args:
        prepared: 前処理済みレース列
        max_tickets: 点数上限（1 以上。固定制約だけで超える場合は呼び出し側で緩めておく）

    Returns:
        レース入力順の頭数リスト
    """
    # states: 点数 -> (log 的中確率, これまでの頭数列)
    states: dict[int, tuple[float, tuple[int, ...]]] = {1: (0.0, ())}

    for race in prepared:
        options = (
            [race.fixed_picks] if race.fixed_picks is not None else list(range(1, len(race.coverage) + 1))
        )
        nxt: dict[int, tuple[float, tuple[int, ...]]] = {}
        for tickets, (score, picks) in states.items():
            for k in options:
                new_tickets = tickets * k
                if new_tickets > max_tickets:
                    break  # k 昇順なのでこれ以降も超過する
                new_score = score + math.log(race.coverage[k - 1])
                current = nxt.get(new_tickets)
                if current is None or new_score > current[0]:
                    nxt[new_tickets] = (new_score, (*picks, k))
        if not nxt:
            # 固定制約だけで上限を超えるケース（呼び出し側で緩和済みなので通常は起きない）
            return [r.fixed_picks or 1 for r in prepared]
        states = nxt

    # 同スコアなら点数の少ない解を選ぶ（-tickets を第2キーにする）
    best = max(states.items(), key=lambda item: (item[1][0], -item[0]))
    return list(best[1][1])


def _score(prepared: Sequence[_PreparedRace], picks: Sequence[int]) -> float:
    """頭数ベクトルの log 的中確率 Σ log cov_i(k_i) を返す（内部評価用）。"""
    return sum(math.log(race.coverage[k - 1]) for race, k in zip(prepared, picks, strict=True))


def _greedy_fill(
    prepared: Sequence[_PreparedRace],
    picks: Sequence[int],
    max_tickets: int,
    *,
    frozen: frozenset[int] = frozenset(),
) -> list[int]:
    """限界効用が最大の拡張を、予算に収まらなくなるまで繰り返し採用する（増分貪欲）。

    k -> k+1 だけでなく k -> k'(> k) の飛び越しも候補にするため、
    カバレッジの log-log 凹性が崩れている分布でも取りこぼしにくい。

    Args:
        prepared: 前処理済みレース列
        picks: 開始点となる頭数ベクトル（破壊せずコピーして使う）
        max_tickets: 点数上限
        frozen: 動かさないレースの index 集合（局所探索から使う。固定制約とは別）

    Returns:
        拡張後の頭数リスト
    """
    picks = list(picks)
    tickets = math.prod(picks)

    while True:
        best_ratio = 0.0
        best_move: tuple[int, int, int] | None = None  # (レース index, 新しい k, 新しい総点数)
        for i, race in enumerate(prepared):
            if race.fixed_picks is not None or i in frozen:
                continue
            base = tickets // picks[i]
            for k in range(picks[i] + 1, len(race.coverage) + 1):
                new_tickets = base * k
                if new_tickets > max_tickets:
                    break  # k 昇順なのでこれ以降も超過する
                ratio = _marginal_utility(race.coverage, picks[i], k)
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_move = (i, k, new_tickets)
        if best_move is None:
            return picks
        idx, new_k, new_tickets = best_move
        picks[idx] = new_k
        tickets = new_tickets


def _solve_greedy(prepared: Sequence[_PreparedRace], max_tickets: int) -> list[int]:
    """限界効用による貪欲法 + 局所探索で (k_1, ..., k_n) を求める。

    判断材料は常に限界効用 Δlog cov / Δlog k だけで、厳密 DP のような全状態展開はしない。

      1. **増分貪欲**: 全レース 1 点（固定制約があればその値）から始め、予算に収まる拡張のうち
         限界効用が最大のものを繰り返し採用する（`_greedy_fill`）。
      2. **局所探索**: 1 レース（および 2 レース同時）の頭数を別の値に置き換え、
         そのレースを固定したまま残りを 1. で埋め直す。改善する限り繰り返す。

    2. が必要なのは、点数が積で効くため増分貪欲だけでは予算に端数が残るから。
    例えば 1,000 点で (14, 2, 14) まで広げると 784 点で行き止まりになるが、
    1 レース削って埋め直すと (13, 3, 12) のようなより良い配分に届く。

    整数制約由来のギャップは完全には消えず、極端な分布では厳密解にわずかに劣ることがある
    （実測: ランダム生成 400 ケースで一致率 99.3%、最悪でも厳密解の 96.3% の的中確率）。
    厳密解が必要なら `method="exact"` を使うこと。

    Args:
        prepared: 前処理済みレース列
        max_tickets: 点数上限（1 以上）

    Returns:
        レース入力順の頭数リスト
    """
    best = _greedy_fill(
        prepared,
        [race.fixed_picks if race.fixed_picks is not None else 1 for race in prepared],
        max_tickets,
    )
    best_score = _score(prepared, best)

    n = len(prepared)
    free = [i for i in range(n) if prepared[i].fixed_picks is None]

    improved = True
    while improved:
        improved = False

        # 1 レースだけ別の頭数に置き換えて埋め直す
        for i in free:
            for k in range(1, len(prepared[i].coverage) + 1):
                candidate = list(best)
                candidate[i] = k
                if math.prod(candidate) > max_tickets:
                    break  # k 昇順なのでこれ以降も超過する
                candidate = _greedy_fill(prepared, candidate, max_tickets, frozen=frozenset({i}))
                score = _score(prepared, candidate)
                if score > best_score + _SCORE_EPS:
                    best, best_score, improved = candidate, score, True

        # 2 レース同時に置き換えて埋め直す（片方を削って他方を広げる手を拾う）
        for a_idx, i in enumerate(free):
            for j in free[a_idx + 1 :]:
                for ki in range(1, len(prepared[i].coverage) + 1):
                    outer = list(best)
                    outer[i] = ki
                    if math.prod(outer) // outer[j] > max_tickets:
                        break
                    for kj in range(1, len(prepared[j].coverage) + 1):
                        candidate = list(outer)
                        candidate[j] = kj
                        if math.prod(candidate) > max_tickets:
                            break
                        candidate = _greedy_fill(
                            prepared, candidate, max_tickets, frozen=frozenset({i, j})
                        )
                        score = _score(prepared, candidate)
                        if score > best_score + _SCORE_EPS:
                            best, best_score, improved = candidate, score, True

    return best


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def _build_plan(
    prepared: Sequence[_PreparedRace],
    picks: Sequence[int],
    *,
    max_tickets: int,
    unit_price: int,
    budget_yen: int,
    method: str,
) -> FormationPlan:
    """頭数ベクトルから検証用メトリクス込みの FormationPlan を組み立てる。"""
    allocations: list[RaceAllocation] = []
    total_tickets = 1
    log_prob = 0.0

    for race, k in zip(prepared, picks, strict=True):
        total_tickets *= k
        coverage = race.coverage[k - 1]
        log_prob += math.log(coverage)
        has_next = k < len(race.coverage)
        allocations.append(
            RaceAllocation(
                race_id=race.race_id,
                picks=k,
                coverage=coverage,
                selected_horses=race.horses[:k],
                available=len(race.coverage),
                fixed=race.fixed_picks is not None,
                next_picks=k + 1 if has_next else None,
                next_coverage=race.coverage[k] if has_next else None,
                marginal_utility=_marginal_utility(race.coverage, k, k + 1) if has_next else None,
            )
        )

    hit_probability = math.exp(log_prob)

    # 予算をあと増やすなら、限界効用が最大のレースを 1 頭広げる（固定レースは対象外）。
    best: FormationExpansion | None = None
    for allocation in allocations:
        next_picks = allocation.next_picks
        next_coverage = allocation.next_coverage
        utility = allocation.marginal_utility
        if allocation.fixed or next_picks is None or next_coverage is None or utility is None:
            continue
        if best is not None and utility <= best.marginal_utility:
            continue
        new_tickets = total_tickets // allocation.picks * next_picks
        best = FormationExpansion(
            race_id=allocation.race_id,
            from_picks=allocation.picks,
            to_picks=next_picks,
            coverage_gain=next_coverage - allocation.coverage,
            marginal_utility=utility,
            additional_tickets=new_tickets - total_tickets,
            additional_cost=(new_tickets - total_tickets) * unit_price,
            hit_probability_after=hit_probability * (next_coverage / allocation.coverage),
        )

    return FormationPlan(
        allocations=allocations,
        total_tickets=total_tickets,
        total_cost=total_tickets * unit_price,
        hit_probability=hit_probability,
        max_tickets=max_tickets,
        unit_price=unit_price,
        budget_yen=budget_yen,
        within_budget=total_tickets <= max_tickets,
        method=method,
        next_expansion=best,
    )


def evaluate_formation(
    races: Sequence[RaceCandidates],
    picks: Sequence[int],
    *,
    unit_price: int = DEFAULT_UNIT_PRICE,
    budget_yen: int | None = None,
) -> FormationPlan:
    """与えられた頭数ベクトルを評価する（検証・比較用）。

    「一律 5 頭」など任意の買い方を optimize_formation の結果と同じ指標で比較できる。

    Args:
        races: レース列
        picks: 各レースの頭数（races と同じ長さ・順序）
        unit_price: 1 点あたりの単価（円）
        budget_yen: 予算（円）。指定時は within_budget の判定に使う

    Returns:
        FormationPlan

    Raises:
        ValueError: 長さ不一致、または picks が候補頭数の範囲外のとき
    """
    if len(races) != len(picks):
        raise ValueError(f"races({len(races)}) と picks({len(picks)}) の長さが一致しません")
    prepared = [_prepare_race(race) for race in races]
    for race, k in zip(prepared, picks, strict=True):
        if not 1 <= k <= len(race.coverage):
            raise ValueError(f"{race.race_id}: picks={k} が候補頭数 1..{len(race.coverage)} の範囲外です")

    total_tickets = math.prod(picks)
    max_tickets = (
        budget_to_max_tickets(budget_yen, unit_price) if budget_yen is not None else total_tickets
    )
    return _build_plan(
        prepared,
        picks,
        max_tickets=max_tickets,
        unit_price=unit_price,
        budget_yen=budget_yen if budget_yen is not None else total_tickets * unit_price,
        method="fixed",
    )


def optimize_formation(
    races: Sequence[RaceCandidates],
    *,
    budget_yen: int | None = None,
    max_tickets: int | None = None,
    unit_price: int = DEFAULT_UNIT_PRICE,
    method: str = "exact",
) -> FormationPlan:
    """予算制約下で的中確率を最大化する頭数配分を求める。

    budget_yen（金額）か max_tickets（点数）のいずれかを指定する。両方指定した場合は
    厳しい方（点数の小さい方）を採用する。どちらも省略した場合は制約なし（全頭買い）になる。

    `RaceCandidates.fixed_picks` を指定したレースは動かさず、残り予算で他レースを再最適化する
    （「レース3は必ず2頭にする」といったユーザー操作をこれで表現する）。
    固定制約だけで予算を超える場合も解は返すが `within_budget=False` になる。

    **入力の勝率は較正済みかつレース内で正規化されていること**（モジュール docstring 参照）。

    Args:
        races: レース列（1 レース以上）
        budget_yen: 予算（円）
        max_tickets: 点数上限
        unit_price: 1 点あたりの単価（円。WIN5 は 100）
        method: "exact"（厳密 DP）または "greedy"（限界効用による貪欲）

    Returns:
        FormationPlan（各レースの頭数・カバレッジ・総点数・総額・的中確率・次の推奨拡張）

    Raises:
        ValueError: races が空、method が不正、予算指定が不正のとき
    """
    if not races:
        raise ValueError("races が空です")
    if method not in {"exact", "greedy"}:
        raise ValueError(f"method は 'exact' か 'greedy' のいずれかです: {method}")

    prepared = [_prepare_race(race) for race in races]

    limits: list[int] = []
    if budget_yen is not None:
        limits.append(budget_to_max_tickets(budget_yen, unit_price))
    if max_tickets is not None:
        if max_tickets < 0:
            raise ValueError(f"max_tickets は 0 以上である必要があります: {max_tickets}")
        limits.append(max_tickets)
    if limits:
        effective_max = min(limits)
    else:
        effective_max = math.prod(len(race.coverage) for race in prepared)

    # 予算が 1 点未満でも「全レース 1 点 = 1 点」は必ず買えるので、最低 1 点は確保する。
    effective_max = max(1, effective_max)

    # 固定制約だけで上限を超える場合、その積までは緩めて解を返す（within_budget=False で報告）。
    minimum_tickets = math.prod(race.fixed_picks or 1 for race in prepared)
    solver_max = max(effective_max, minimum_tickets)

    solver = _solve_exact if method == "exact" else _solve_greedy
    picks = solver(prepared, solver_max)

    return _build_plan(
        prepared,
        picks,
        max_tickets=effective_max,
        unit_price=unit_price,
        budget_yen=budget_yen if budget_yen is not None else effective_max * unit_price,
        method=method,
    )
