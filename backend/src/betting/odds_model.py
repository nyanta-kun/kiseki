"""払戻ベース 組合せオッズ近似モデル。

確定単勝オッズ（=払戻/100）から Harville 確率を計算し、
log 回帰（券種別傾き・切片）で任意組合せのオッズを近似する。

モデルパラメータは fit_odds_approximation.py で学習し
models/odds_approx_v1.json に保存。本モジュールはその JSON を読み込んで
推論のみを行う。

使い方（推論）::

    from src.betting.odds_model import OddsApproximator
    approx = OddsApproximator.from_json("models/odds_approx_v1.json")
    est_odds = approx.estimate("quinella", [2, 5], win_probs)

使い方（Harville 確率計算のみ）::

    from src.betting.odds_model import harville_combo_prob
    p = harville_combo_prob([0.4, 0.3, 0.2, 0.1], horse_numbers=[1, 2], bet_type="quinella", n_horses=4)
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

# JRA 公表控除率（bet_type 文字列 → 控除率）
TAKEOUT_RATE: dict[str, float] = {
    "win": 0.20,
    "place": 0.20,
    "bracket": 0.225,      # 枠連
    "quinella": 0.225,     # 馬連
    "wide": 0.225,         # ワイド
    "exacta": 0.25,        # 馬単（trio と同じ控除で代用）
    "trio": 0.25,          # 三連複
    "trifecta": 0.275,     # 三連単
}


# ---------------------------------------------------------------------------
# Harville 確率計算（スタンドアロン関数）
# ---------------------------------------------------------------------------


def harville_win_probs_from_odds(win_odds: list[float]) -> list[float]:
    """確定単勝オッズ（払戻/100）から Harville 入力確率を計算する。

    1/オッズ のオーバーラウンドを正規化して合計 1.0 にする。

    Args:
        win_odds: 各馬の単勝オッズ（払戻/100）。0 以下は対象外（取消等）。

    Returns:
        正規化された勝率ベクトル（合計 1.0）。
    """
    raw: list[float] = []
    for o in win_odds:
        if o is None or o <= 0:
            raw.append(0.0)
        else:
            raw.append(1.0 / float(o))
    total = sum(raw)
    if total <= 0:
        n = len(raw)
        return [1.0 / n] * n
    return [r / total for r in raw]


def harville_combo_prob(
    win_probs: list[float],
    horse_indices: list[int],
    bet_type: str,
    n_horses: int | None = None,
) -> float:
    """Harville 公式で任意組合せの確率を算出する。

    Args:
        win_probs: 正規化済み勝率ベクトル（0-indexed、全馬分）。
        horse_indices: 組合せの馬インデックス（0-indexed）。
        bet_type: "win" / "place" / "quinella" / "wide" / "exacta" / "trio" / "trifecta"。
        n_horses: 出走頭数（None なら len(win_probs)）。

    Returns:
        組合せ確率 (0, 1]。
    """
    n = n_horses if n_horses is not None else len(win_probs)
    ps = list(win_probs)

    if bet_type == "win":
        # 1着: P(i)
        return ps[horse_indices[0]]

    if bet_type == "place":
        # 複勝: 3着以内（8頭以上）or 2着以内（8頭未満）
        place_probs = _harville_place_probs(ps, n)
        return place_probs[horse_indices[0]]

    if bet_type in ("quinella", "wide"):
        # 馬連/ワイド: P(i,j) = P(i)P(j|i除外) + P(j)P(i|j除外)
        i, j = horse_indices[0], horse_indices[1]
        pi, pj = ps[i], ps[j]
        denom_i = 1.0 - pi
        denom_j = 1.0 - pj
        p = 0.0
        if denom_i > 1e-9:
            p += pi * (pj / denom_i)
        if denom_j > 1e-9:
            p += pj * (pi / denom_j)
        return min(p, 1.0)

    if bet_type == "exacta":
        # 馬単: P(1着=i) × P(2着=j | 1着=i)
        i, j = horse_indices[0], horse_indices[1]
        pi = ps[i]
        denom_i = 1.0 - pi
        if denom_i <= 1e-9:
            return 0.0
        return pi * (ps[j] / denom_i)

    if bet_type == "trio":
        # 三連複: 3頭の着順不問組合せ
        i, j, k = horse_indices[0], horse_indices[1], horse_indices[2]
        return _trio_prob(ps, i, j, k)

    if bet_type == "trifecta":
        # 三連単: 1着=i, 2着=j, 3着=k
        i, j, k = horse_indices[0], horse_indices[1], horse_indices[2]
        pi = ps[i]
        denom_i = 1.0 - pi
        if denom_i <= 1e-9:
            return 0.0
        pj_given_i = ps[j] / denom_i
        denom_ij = 1.0 - pi - ps[j]
        if denom_ij <= 1e-9:
            return 0.0
        pk_given_ij = ps[k] / denom_ij
        return pi * pj_given_i * pk_given_ij

    if bet_type == "bracket":
        # 枠連: 枠番は呼び出し元で馬番→枠番変換済み想定。quinella と同型
        i, j = horse_indices[0], horse_indices[1]
        pi, pj = ps[i], ps[j]
        denom_i = 1.0 - pi
        denom_j = 1.0 - pj
        p = 0.0
        if denom_i > 1e-9:
            p += pi * (pj / denom_i)
        if denom_j > 1e-9:
            p += pj * (pi / denom_j)
        return min(p, 1.0)

    raise ValueError(f"Unknown bet_type: {bet_type}")


def _harville_place_probs(win_probs: list[float], n: int | None = None) -> list[float]:
    """Harville 公式による複勝確率（コンポジット実装から切り出した関数）。

    JRA ルール:
      - 8頭以上: 3着以内
      - 8頭未満: 2着以内

    Args:
        win_probs: 正規化済み勝率ベクトル。
        n: 出走頭数（None なら len(win_probs)）。

    Returns:
        各馬の複勝確率リスト（合計は 8頭以上で約3, 8頭未満で約2）。
    """
    num = n if n is not None else len(win_probs)
    place_within = 3 if num >= 8 else 2
    if num <= place_within:
        return [1.0] * len(win_probs)

    place_probs = []
    for i in range(len(win_probs)):
        pi = win_probs[i]

        # 2着確率
        p2 = 0.0
        for j in range(len(win_probs)):
            if j == i:
                continue
            denom_j = 1.0 - win_probs[j]
            if denom_j <= 1e-9:
                continue
            p2 += win_probs[j] * (pi / denom_j)

        # 3着確率（8頭以上のみ）
        p3 = 0.0
        if num >= 8:
            for j in range(len(win_probs)):
                if j == i:
                    continue
                denom_j = 1.0 - win_probs[j]
                if denom_j <= 1e-9:
                    continue
                for k in range(len(win_probs)):
                    if k == i or k == j:
                        continue
                    p_k_given_j = win_probs[k] / denom_j
                    denom_jk = 1.0 - win_probs[j] - win_probs[k]
                    if denom_jk <= 1e-9:
                        continue
                    p3 += win_probs[j] * p_k_given_j * (pi / denom_jk)

        place_probs.append(min(pi + p2 + p3, 1.0))

    return place_probs


def _trio_prob(ps: list[float], i: int, j: int, k: int) -> float:
    """三連複確率: 3頭の着順不問（6パターンの合計）。"""
    total = 0.0
    indices = [i, j, k]
    # 6通りの順列
    perms = [
        (indices[0], indices[1], indices[2]),
        (indices[0], indices[2], indices[1]),
        (indices[1], indices[0], indices[2]),
        (indices[1], indices[2], indices[0]),
        (indices[2], indices[0], indices[1]),
        (indices[2], indices[1], indices[0]),
    ]
    for a, b, c in perms:
        pa = ps[a]
        denom_a = 1.0 - pa
        if denom_a <= 1e-9:
            continue
        pb_given_a = ps[b] / denom_a
        denom_ab = 1.0 - pa - ps[b]
        if denom_ab <= 1e-9:
            continue
        pc_given_ab = ps[c] / denom_ab
        total += pa * pb_given_a * pc_given_ab
    return min(total, 1.0)


# ---------------------------------------------------------------------------
# パラメータ JSON の構造
# ---------------------------------------------------------------------------
# {
#   "version": 1,
#   "fit_date": "2025-...",
#   "params": {
#     "<bet_type>": {"a": float, "b": float, "mae": float, "n": int, "bias": float}
#   }
# }
#
# 推定式: log10(est_odds) = a + b * log10(1 / p_harville)


class OddsApproximator:
    """Harville 確率 → 市場オッズ 近似モデル（ログ線形回帰）。

    Attributes:
        params: 券種ごとの回帰パラメータ {"a": float, "b": float}。
        version: モデルバージョン。
    """

    def __init__(
        self,
        params: dict[str, dict[str, Any]],
        version: int = 1,
        fit_date: str = "",
    ) -> None:
        """初期化。

        Args:
            params: 券種 → {"a": float, "b": float, ...} の辞書。
            version: モデルバージョン番号。
            fit_date: 学習日時文字列（記録用）。
        """
        self.params = params
        self.version = version
        self.fit_date = fit_date

    @classmethod
    def from_json(cls, path: str | Path) -> OddsApproximator:
        """JSON ファイルからモデルを読み込む。

        Args:
            path: JSON ファイルパス（models/odds_approx_v1.json）。

        Returns:
            OddsApproximator インスタンス。

        Raises:
            FileNotFoundError: ファイルが存在しない場合。
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"モデルファイルが見つかりません: {p}")
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            params=data["params"],
            version=data.get("version", 1),
            fit_date=data.get("fit_date", ""),
        )

    def to_json(self, path: str | Path) -> None:
        """モデルを JSON ファイルに保存する。

        Args:
            path: 保存先パス。
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": self.version,
            "fit_date": self.fit_date,
            "params": self.params,
        }
        with p.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def estimate(
        self,
        bet_type: str,
        horse_indices: list[int],
        win_probs: list[float],
        n_horses: int | None = None,
    ) -> float:
        """任意組合せの近似オッズを返す。

        Args:
            bet_type: 券種文字列（"win" / "quinella" / "trio" など）。
            horse_indices: 組合せの馬インデックス（0-indexed）。
            win_probs: 正規化済み勝率ベクトル（全馬分）。
            n_horses: 出走頭数（None なら len(win_probs)）。

        Returns:
            推定オッズ（払戻/100 相当）。最低 1.0 で clip。

        Raises:
            KeyError: bet_type が未学習の場合。
        """
        p = harville_combo_prob(win_probs, horse_indices, bet_type, n_horses)
        if p <= 0:
            return float("inf")

        log_inv_p = math.log10(1.0 / p)

        if bet_type not in self.params:
            # フォールバック: 控除率ベースのナイーブ推定
            takeout = TAKEOUT_RATE.get(bet_type, 0.25)
            return max(1.0, (1.0 - takeout) / p)

        a = self.params[bet_type]["a"]
        b = self.params[bet_type]["b"]
        log10_odds = a + b * log_inv_p
        return max(1.0, 10.0 ** log10_odds)

    def estimate_naive(
        self,
        bet_type: str,
        horse_indices: list[int],
        win_probs: list[float],
        n_horses: int | None = None,
    ) -> float:
        """控除率ベースのナイーブ推定（パラメータ不要・ベースライン）。

        Args:
            bet_type: 券種文字列。
            horse_indices: 馬インデックス（0-indexed）。
            win_probs: 正規化済み勝率ベクトル。
            n_horses: 出走頭数。

        Returns:
            (1 - 控除率) / Harville 確率。
        """
        p = harville_combo_prob(win_probs, horse_indices, bet_type, n_horses)
        if p <= 0:
            return float("inf")
        takeout = TAKEOUT_RATE.get(bet_type, 0.25)
        return max(1.0, (1.0 - takeout) / p)

    def coverage(self) -> list[str]:
        """学習済み券種リストを返す。"""
        return list(self.params.keys())
