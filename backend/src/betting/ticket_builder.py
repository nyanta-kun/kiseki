"""買い目構築ヘルパー（軸流し・BOX・フォーメーション）。

JRA 公式の点数計算式に準拠した組合せ列挙を提供する純関数群。

## 券種と組合せ表記

本モジュールの combination 文字列は T05（backtest.py）の settle() に渡す形式に合わせる。
- 単勝 / 複勝: "01" （馬番 2 桁ゼロ埋め）
- 馬連 / ワイド: "01-02" （馬番昇順・ハイフン区切り）
- 馬単: "01-02" （1着-2着順）
- 三連複: "01-02-03" （馬番昇順）
- 三連単: "01-02-03" （着順固定）
- 枠連: "1-2" （枠番昇順・ハイフン区切り）

## 点数計算の参考（JRA 公式）

フォーメーション点数 = 1軸 × 2軸 × 3軸 の組合せ数から重複を引いたもの。
三連単フォーメーション 2×3×4 例:
  1 列目 2頭 × 2列目 3頭 × 3列目 4頭 = 24
  ただし同一馬が複数列に入る場合は除外 → 実際の点数はケースによる。
  本モジュールは「重複を除いた組合せ」を正確に列挙するため公式点数と一致する。
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import combinations, permutations


def _fmt(horse: int) -> str:
    """馬番を 2 桁ゼロ埋め文字列に変換する。"""
    return f"{horse:02d}"


def _fmt_frame(frame: int) -> str:
    """枠番を 1 桁文字列に変換する（枠連用）。"""
    return str(frame)


# ---------------------------------------------------------------------------
# 単勝 / 複勝
# ---------------------------------------------------------------------------


def build_win(horses: Sequence[int]) -> list[str]:
    """単勝 買い目列を構築する。

    Args:
        horses: 対象馬番リスト

    Returns:
        combination 文字列リスト（例: ["01", "03"]）
    """
    return [_fmt(h) for h in horses]


def build_place(horses: Sequence[int]) -> list[str]:
    """複勝 買い目列を構築する。

    Args:
        horses: 対象馬番リスト

    Returns:
        combination 文字列リスト（例: ["01", "03"]）
    """
    return [_fmt(h) for h in horses]


# ---------------------------------------------------------------------------
# 馬連 / ワイド（順不同・昇順）
# ---------------------------------------------------------------------------


def build_quinella_box(horses: Sequence[int]) -> list[str]:
    """馬連 BOX 買い目を構築する。

    n 頭 BOX = C(n,2) 点。

    Args:
        horses: 対象馬番リスト（2頭以上）

    Returns:
        combination 文字列リスト（例: ["01-02", "01-03", "02-03"]）
    """
    result = []
    for a, b in combinations(sorted(set(horses)), 2):
        result.append(f"{_fmt(a)}-{_fmt(b)}")
    return result


def build_quinella_axis(
    axis: Sequence[int],
    partners: Sequence[int],
) -> list[str]:
    """馬連 軸流し買い目を構築する（軸1頭以上）。

    軸1頭×相手N頭 = N 点。
    軸2頭×相手N頭 = 軸間組合せ + 各軸×相手（重複除外）。

    Args:
        axis: 軸馬番リスト（1〜2頭）
        partners: 相手馬番リスト

    Returns:
        combination 文字列リスト
    """
    all_horses = set(axis) | set(partners)
    axis_set = set(axis)

    result = set()
    for h in all_horses:
        if h in axis_set:
            for p in all_horses:
                if p != h:
                    pair = tuple(sorted([h, p]))
                    result.add(pair)
        else:
            for ax in axis_set:
                pair = tuple(sorted([ax, h]))
                result.add(pair)

    # 相手のみ同士は不要なので axis が含まれるもののみ残す
    filtered = {pair for pair in result if axis_set & set(pair)}
    return [f"{_fmt(a)}-{_fmt(b)}" for a, b in sorted(filtered)]


def build_wide_box(horses: Sequence[int]) -> list[str]:
    """ワイド BOX 買い目を構築する（組合せ形式は馬連と同じ）。

    Args:
        horses: 対象馬番リスト（2頭以上）

    Returns:
        combination 文字列リスト
    """
    return build_quinella_box(horses)


def build_wide_axis(
    axis: Sequence[int],
    partners: Sequence[int],
) -> list[str]:
    """ワイド 軸流し買い目を構築する。

    Args:
        axis: 軸馬番リスト
        partners: 相手馬番リスト

    Returns:
        combination 文字列リスト
    """
    return build_quinella_axis(axis, partners)


# ---------------------------------------------------------------------------
# 馬単（着順固定）
# ---------------------------------------------------------------------------


def build_exacta_box(horses: Sequence[int]) -> list[str]:
    """馬単 BOX 買い目を構築する。

    n 頭 BOX = P(n,2) = n*(n-1) 点。

    Args:
        horses: 対象馬番リスト（2頭以上）

    Returns:
        combination 文字列リスト（例: ["01-02", "02-01"]）
    """
    result = []
    for a, b in permutations(sorted(set(horses)), 2):
        result.append(f"{_fmt(a)}-{_fmt(b)}")
    return result


def build_exacta_axis(
    axis_first: Sequence[int],
    axis_second: Sequence[int],
) -> list[str]:
    """馬単 軸流し（1着軸 × 2着相手）買い目を構築する。

    Args:
        axis_first: 1着軸馬番リスト
        axis_second: 2着相手馬番リスト

    Returns:
        combination 文字列リスト（着順固定）
    """
    result = []
    for first in axis_first:
        for second in axis_second:
            if first != second:
                result.append(f"{_fmt(first)}-{_fmt(second)}")
    # 重複除外
    seen: set[str] = set()
    deduped = []
    for c in result:
        if c not in seen:
            seen.add(c)
            deduped.append(c)
    return deduped


# ---------------------------------------------------------------------------
# 三連複（順不同・昇順）
# ---------------------------------------------------------------------------


def build_trio_box(horses: Sequence[int]) -> list[str]:
    """三連複 BOX 買い目を構築する。

    n 頭 BOX = C(n,3) 点。

    Args:
        horses: 対象馬番リスト（3頭以上）

    Returns:
        combination 文字列リスト（例: ["01-02-03"]）
    """
    result = []
    for a, b, c in combinations(sorted(set(horses)), 3):
        result.append(f"{_fmt(a)}-{_fmt(b)}-{_fmt(c)}")
    return result


def build_trio_axis(
    axis: Sequence[int],
    partners: Sequence[int],
) -> list[str]:
    """三連複 軸1頭流し買い目を構築する。

    軸1頭 × 相手N頭 の中から2頭選ぶ = C(N, 2) 点。
    ただし軸馬自身が相手に含まれていても重複除外する。

    Args:
        axis: 軸馬番（1頭のみ想定。複数の場合は先頭を使用）
        partners: 相手馬番リスト（2頭以上）

    Returns:
        combination 文字列リスト
    """
    ax = axis[0]
    others = sorted(set(partners) - {ax})
    result = []
    for b, c in combinations(others, 2):
        triple = tuple(sorted([ax, b, c]))
        result.append(f"{_fmt(triple[0])}-{_fmt(triple[1])}-{_fmt(triple[2])}")
    return result


def build_trio_formation(
    col1: Sequence[int],
    col2: Sequence[int],
    col3: Sequence[int],
) -> list[str]:
    """三連複 フォーメーション買い目を構築する。

    3列に馬を指定し、各列から1頭ずつ選ぶ全組合せ（重複排除・昇順）を返す。

    例: col1=[1,2], col2=[1,2,3], col3=[1,2,3,4] → 実際の点数はケースによる。

    JRA 公式との一致検証（DoD要件）:
      col1=[1,2], col2=[1,2,3], col3=[1,2,3,4] の場合:
      全3者が異なる組合せを昇順で列挙。重複を除いた点数 = 本関数の len() で確認可能。

    Args:
        col1: 1列目馬番リスト
        col2: 2列目馬番リスト
        col3: 3列目馬番リスト

    Returns:
        combination 文字列リスト（重複なし・各組合せは3馬番昇順）
    """
    seen: set[tuple[int, int, int]] = set()
    for a in col1:
        for b in col2:
            for c in col3:
                triple = tuple(sorted({a, b, c}))
                if len(triple) == 3:  # 全て異なる馬番
                    seen.add(triple)  # type: ignore[arg-type]
    return [
        f"{_fmt(t[0])}-{_fmt(t[1])}-{_fmt(t[2])}"
        for t in sorted(seen)
    ]


# ---------------------------------------------------------------------------
# 三連単（着順固定）
# ---------------------------------------------------------------------------


def build_trifecta_box(horses: Sequence[int]) -> list[str]:
    """三連単 BOX 買い目を構築する。

    n 頭 BOX = P(n,3) = n*(n-1)*(n-2) 点。

    Args:
        horses: 対象馬番リスト（3頭以上）

    Returns:
        combination 文字列リスト（着順固定）
    """
    result = []
    for a, b, c in permutations(sorted(set(horses)), 3):
        result.append(f"{_fmt(a)}-{_fmt(b)}-{_fmt(c)}")
    return result


def build_trifecta_axis(
    axis_first: Sequence[int],
    axis_second: Sequence[int],
    axis_third: Sequence[int],
) -> list[str]:
    """三連単 フォーメーション（軸流し）買い目を構築する。

    3列に馬を指定し、各列から1頭ずつ選ぶ全組合せ（全て異なる）を返す。

    JRA 公式との一致検証（DoD要件）:
      axis_first=[1,2], axis_second=[1,2,3], axis_third=[1,2,3,4]
      の三連単フォーメーション = 本関数の len() と一致。

    Args:
        axis_first: 1着に来る馬の候補リスト
        axis_second: 2着に来る馬の候補リスト
        axis_third: 3着に来る馬の候補リスト

    Returns:
        combination 文字列リスト（重複なし・着順固定）
    """
    seen: set[tuple[int, int, int]] = set()
    for first in axis_first:
        for second in axis_second:
            if second == first:
                continue
            for third in axis_third:
                if third == first or third == second:
                    continue
                seen.add((first, second, third))
    return [
        f"{_fmt(t[0])}-{_fmt(t[1])}-{_fmt(t[2])}"
        for t in sorted(seen)
    ]


# ---------------------------------------------------------------------------
# 枠連
# ---------------------------------------------------------------------------


def build_frame_box(frames: Sequence[int]) -> list[str]:
    """枠連 BOX 買い目を構築する。

    Args:
        frames: 対象枠番リスト（1〜8）

    Returns:
        combination 文字列リスト（枠番昇順。ゾロ目は自枠連）
    """
    result = []
    sorted_frames = sorted(set(frames))
    # 枠連はゾロ目も存在（同枠内で決まる場合）
    for i, a in enumerate(sorted_frames):
        for b in sorted_frames[i:]:
            result.append(f"{_fmt_frame(a)}-{_fmt_frame(b)}")
    return result


# ---------------------------------------------------------------------------
# 確率上位 N 点フォーメーション自動構成（T03 との結合用）
# ---------------------------------------------------------------------------


def top_n_formation(
    combo_probs: dict[str, float],
    bet_type: str,
    n: int,
) -> list[str]:
    """確率上位 N 点の組合せを返す（T03 enumerate_combo_probs との結合用）。

    T03 の finish_order.enumerate_combo_probs() の出力（combination -> 確率）を
    受け取り、確率降順で上位 N 点を返す。

    使用例::

        from src.betting.finish_order import enumerate_combo_probs
        from src.betting.ticket_builder import top_n_formation

        win_probs = {1: 0.30, 2: 0.25, 3: 0.20, ...}
        combo_probs = enumerate_combo_probs(win_probs, bet_type="trio")
        top_combos = top_n_formation(combo_probs, bet_type="trio", n=10)

    Args:
        combo_probs: combination -> 確率 のマッピング
        bet_type: 券種（集計ログ用・フィルタはしない）
        n: 返す点数の上限

    Returns:
        確率降順上位 N 点の combination リスト
    """
    sorted_combos = sorted(combo_probs.items(), key=lambda x: x[1], reverse=True)
    return [combo for combo, _ in sorted_combos[:n]]


def count_formation_points(combinations_list: list[str]) -> int:
    """組合せリストの点数（重複除外）を返す。

    Args:
        combinations_list: combination 文字列リスト

    Returns:
        重複を除いた点数
    """
    return len(set(combinations_list))
