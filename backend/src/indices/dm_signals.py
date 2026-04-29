"""DM (JV-Next タイム型・対戦型) シグナルタグ算出モジュール

合成ウェイトに混ぜず、特定条件を満たす馬に「軸」「穴」「警戒」タグを付与する。
バックテスト (scripts/backtest_dm.py / backtest_dm_signal.py / backtest_combined_signals.py) で
99.0%カバレッジ・8,618レース・3年実績で実証された7種類のシグナル:

軸シグナル (信頼度):
  TRIPLE_MATCH (🔥 三冠一致):
    base_rank=1 ∧ time_rank=1 ∧ battle_rank=1
    勝率 39.1% / 複勝 71.6% / ROI 84.9% / n=1,622
    → 軸固定で複勝・三連複に厚く

  TOP_PREMIUM (⭐ 高得点鉄板):
    composite_index ≥60 ∧ jvan_battle_dm ≥65
    勝率 46.5% / 複勝 74.4% / ROI 101.2% / n=86
    → 単勝フラット買いでもプラス収支

穴シグナル (妙味):
  ANAGUSA_DM (🏆 穴ぐさDM・最強):
    anagusa∈{A,B} ∧ battle_rank=1 ∧ 人気≥5
    勝率 10.2% / 複勝 20.4% / ROI 188.8% / n=49
    → 3独立情報源 (穴ぐさ人手, DM AI, 既存指数) 一致の穴推奨

  DM_BIG_DARK (⚡ DM大穴):
    battle_rank=1 ∧ 人気≥7 ∧ jvan_battle_dm≥65
    勝率 7.6% / 複勝 20.1% / ROI 154.0% / n=184
    → 大穴単勝 (オッズ高め)

  DM_HIGH_ODDS (⚡ DM高オッズ):
    battle_rank=1 ∧ win_odds≥10 ∧ time_rank≤2
    勝率 9.0% / 複勝 25.0% / ROI 130.0% / n=156
    → オッズベースの中穴

  ANAGUSA_DM_TIME (💎 穴ぐさ+DMtime):
    anagusa=A ∧ time_rank=1
    勝率 9.4% / 複勝 24.5% / ROI 103.5% / n=106
    → サンプル多めの穴シグナル

警戒シグナル:
  POPULAR_DOWNSIDE (❌ 人気下振れ):
    win_popularity≤3 ∧ base_rank≥4 ∧ battle_rank≥4
    勝率 15.3% / ROI 73.9% / n=3,563
    → 人気だが両指数で評価低い人気馬。軸から外す対象

API レスポンスにタグを付与し、フロントエンドでバッジ表示する想定。
DM 値が NULL の馬・レースではタグは空のまま (運用範囲は DM 揃いレースのみ)。
"""

from __future__ import annotations

from typing import Any, Protocol

# シグナル文字列定数 (UI 表示用にラベル付き、API では key を返す)
SIGNAL_TRIPLE_MATCH = "三冠一致"
SIGNAL_TOP_PREMIUM = "高得点鉄板"
SIGNAL_ANAGUSA_DM = "穴ぐさDM"
SIGNAL_DM_BIG_DARK = "DM大穴"
SIGNAL_DM_HIGH_ODDS = "DM高オッズ"
SIGNAL_ANAGUSA_DM_TIME = "穴ぐさ+DMtime"
SIGNAL_POPULAR_DOWNSIDE = "人気下振れ"

# 高得点鉄板しきい値 (バックテスト確定)
TOP_PREMIUM_BASE_MIN = 60.0
TOP_PREMIUM_BATTLE_MIN = 65.0

# 穴ぐさDM 人気しきい値
ANAGUSA_DM_POP_MIN = 5

# DM大穴 人気しきい値・battle値しきい値
DM_BIG_DARK_POP_MIN = 7
DM_BIG_DARK_BATTLE_MIN = 65.0

# DM高オッズ オッズしきい値・time順位上限
DM_HIGH_ODDS_MIN = 10.0
DM_HIGH_ODDS_TIME_RANK_MAX = 2

# 人気下振れ 人気上限・指数下限
POPULAR_DOWNSIDE_POP_MAX = 3
POPULAR_DOWNSIDE_RANK_MIN = 4

# =============================================================================
# 条件別 信頼度フィルタ (バックテスト 2023-2026 / 8,362 レース実証)
#
# シグナルは条件によって ROI が大きくブレる。「信頼できる条件のみ発動」
# させることで誤シグナル発信を防ぐ。
# - course は「中山/東京/京都/阪神/...」のレース場名を使う
# - surface は "芝" / "ダート" / "障害" の prefix
# - distance は m
# 詳細: scripts/backtest_dm_signal_segments.py 出力
# =============================================================================

# 三冠一致: 福島 (49%) / 阪神 (68%) / 京都 (70%) は ROI<80% で誤発信
TRIPLE_MATCH_DENY_COURSES = {"福島", "阪神", "京都"}
# 三冠一致: 芝マイル (69%) / ダート中距離 (70%) は ROI<80%
TRIPLE_MATCH_DENY_SEGMENTS: set[tuple[str, str]] = {
    ("芝", "マイル"),
    ("ダート", "中距離"),
}

# 穴ぐさDM: 東京 (21%!) / 小倉 (58%) / 札幌 (60%) / 阪神 (79%) で逆効果
ANAGUSA_DM_DENY_COURSES = {"東京", "小倉", "札幌", "阪神"}
# 穴ぐさDM: 障害 / ダート×中距離 / 芝×中距離 は ROI<80%
ANAGUSA_DM_DENY_SEGMENTS: set[tuple[str, str]] = {
    ("障害", "長距離"),
    ("障害", "中距離"),
    ("障害", "マイル"),
    ("障害", "スプリント"),
    ("ダート", "中距離"),
    ("芝", "中距離"),
    ("ダート", "スプリント"),
}

# DM高オッズ: 芝×マイル (n=29) で ROI 0% — サンプル小だが極端なので除外
DM_HIGH_ODDS_DENY_SEGMENTS: set[tuple[str, str]] = {
    ("芝", "マイル"),
}

# 人気下振れ (警戒): 福島 (95%) / 小倉 (92%) / 阪神 (86%) / 京都 (85%) では
# 警戒対象が実は来やすい (機械的消しは逆効果)。これらの場では警戒タグ非発動。
POPULAR_DOWNSIDE_DENY_COURSES = {"福島", "小倉", "阪神", "京都"}


def _dist_cat(distance: float | int | None) -> str | None:
    """距離 → カテゴリ (スプリント/マイル/中距離/長距離)"""
    if distance is None:
        return None
    d = float(distance)
    if d <= 1400:
        return "スプリント"
    if d <= 1800:
        return "マイル"
    if d <= 2400:
        return "中距離"
    return "長距離"


def _surface_cat(surface: str | None) -> str | None:
    """サーフェイス文字列 → カテゴリ (芝/ダート/障害)"""
    if not isinstance(surface, str):
        return None
    if surface.startswith("芝"):
        return "芝"
    if surface.startswith("ダ"):
        return "ダート"
    if surface.startswith("障"):
        return "障害"
    return None


class _Horse(Protocol):
    """compute_dm_signals が必要とする最小インターフェース。

    HorseIndexOut (api/races.py) を想定するが、テストや他用途で
    同じプロパティを持つオブジェクトなら何でも渡せる。
    """

    horse_number: int
    composite_index: float
    jvan_time_dm: float | None
    jvan_battle_dm: float | None
    anagusa_rank: str | None
    dm_signals: list[str] | None


def _ranks_descending(values: list[float | None]) -> list[int | None]:
    """降順ランクを付ける (最大=1)。NULL は None を返す。同値は同一ランク。

    例: [50, 80, 80, 30] → [3, 1, 1, 4]
    """
    n = len(values)
    indexed = [(i, v) for i, v in enumerate(values) if v is not None]
    indexed.sort(key=lambda x: x[1], reverse=True)
    out: list[int | None] = [None] * n
    rank = 0
    last_v: float | None = None
    seen = 0
    for i, v in indexed:
        seen += 1
        if last_v is None or v != last_v:
            rank = seen
            last_v = v
        out[i] = rank
    return out


def compute_dm_signals(
    horses: list[Any],
    popularity_map: dict[int, int] | None = None,
    win_odds_map: dict[int, float] | None = None,
    course_name: str | None = None,
    surface: str | None = None,
    distance: float | int | None = None,
) -> None:
    """各馬に DM シグナルタグを付与する (in-place)。

    Args:
        horses: HorseIndexOut のリスト (composite_index, jvan_time_dm,
                jvan_battle_dm, anagusa_rank を持つこと)
        popularity_map: {horse_number: 人気} のマップ。
                        渡されない場合は人気依存シグナルは付かない。
                        人気は 1 = 最人気 ... N = 最不人気。
        win_odds_map: {horse_number: 単勝オッズ} のマップ。
                      渡されない場合は DM_HIGH_ODDS は付かない。
        course_name: レース場名 ("中山","東京",...)。条件別フィルタに使用。
        surface: 馬場 ("芝","ダート","障害")。条件別フィルタに使用。
        distance: 距離 (m)。条件別フィルタに使用。

    DM 値 (time/battle) のいずれかが NULL のレースではシグナルは付与されない
    (中途半端なシグナルを避けるため)。

    条件 (course/surface/distance) が渡されない場合は条件絞り込みなし
    (旧挙動互換)。
    """
    if not horses:
        return

    # 全馬の dm_signals を [] に初期化 (None だと未計算と区別できない)
    for h in horses:
        h.dm_signals = []

    # DM データがレース内で揃っているか確認 (1頭でも NULL ならスキップ)
    if any(h.jvan_time_dm is None or h.jvan_battle_dm is None for h in horses):
        return

    # ランク算出
    base_ranks = _ranks_descending([h.composite_index for h in horses])
    time_ranks = _ranks_descending([h.jvan_time_dm for h in horses])
    battle_ranks = _ranks_descending([h.jvan_battle_dm for h in horses])

    pop = popularity_map or {}
    odds = win_odds_map or {}

    # レース条件 (信頼度フィルタ用)
    surf_cat = _surface_cat(surface)
    dist_cat = _dist_cat(distance)
    seg = (surf_cat, dist_cat) if surf_cat and dist_cat else None

    # フィルタフラグ (False ならそのシグナル発動可)
    deny_triple = (
        (course_name in TRIPLE_MATCH_DENY_COURSES)
        or (seg in TRIPLE_MATCH_DENY_SEGMENTS)
    )
    deny_anagusa_dm = (
        (course_name in ANAGUSA_DM_DENY_COURSES)
        or (seg in ANAGUSA_DM_DENY_SEGMENTS)
    )
    deny_high_odds = seg in DM_HIGH_ODDS_DENY_SEGMENTS
    deny_popular_downside = course_name in POPULAR_DOWNSIDE_DENY_COURSES

    for i, h in enumerate(horses):
        br = base_ranks[i]
        tr = time_ranks[i]
        ar = battle_ranks[i]
        if br is None or tr is None or ar is None:
            continue

        battle_dm = h.jvan_battle_dm or 0.0
        anagusa = h.anagusa_rank
        popularity = pop.get(h.horse_number)
        win_odds = odds.get(h.horse_number)

        tags: list[str] = []

        # 🔥 三冠一致: base=1 ∧ time=1 ∧ battle=1
        # 信頼度フィルタ: 福島/阪神/京都, 芝マイル, ダート中距離は除外
        if br == 1 and tr == 1 and ar == 1 and not deny_triple:
            tags.append(SIGNAL_TRIPLE_MATCH)

        # ⭐ 高得点鉄板: composite≥60 ∧ battle≥65
        # (segment 内一貫して ROI≥85% でフィルタ不要)
        if h.composite_index >= TOP_PREMIUM_BASE_MIN and battle_dm >= TOP_PREMIUM_BATTLE_MIN:
            tags.append(SIGNAL_TOP_PREMIUM)

        # 🏆 穴ぐさDM: anagusa∈{A,B} ∧ battle=1 ∧ 人気≥5
        # 信頼度フィルタ: 東京 (ROI 21%!) など除外
        if (
            anagusa in ("A", "B")
            and ar == 1
            and popularity is not None
            and popularity >= ANAGUSA_DM_POP_MIN
            and not deny_anagusa_dm
        ):
            tags.append(SIGNAL_ANAGUSA_DM)

        # ⚡ DM大穴: battle=1 ∧ 人気≥7 ∧ battle値≥65
        # (全 segment で ROI≥125% で安定。フィルタなし)
        if (
            ar == 1
            and popularity is not None
            and popularity >= DM_BIG_DARK_POP_MIN
            and battle_dm >= DM_BIG_DARK_BATTLE_MIN
        ):
            tags.append(SIGNAL_DM_BIG_DARK)

        # ⚡ DM高オッズ: battle=1 ∧ オッズ≥10 ∧ time≤2
        # 信頼度フィルタ: 芝×マイル (ROI 0%) は除外
        if (
            ar == 1
            and win_odds is not None
            and win_odds >= DM_HIGH_ODDS_MIN
            and tr is not None
            and tr <= DM_HIGH_ODDS_TIME_RANK_MAX
            and not deny_high_odds
        ):
            tags.append(SIGNAL_DM_HIGH_ODDS)

        # 💎 穴ぐさ+DMtime: anagusa=A ∧ time=1 (フィルタなし)
        if anagusa == "A" and tr == 1:
            tags.append(SIGNAL_ANAGUSA_DM_TIME)

        # ❌ 人気下振れ: 人気≤3 ∧ base≥4位 ∧ battle≥4位
        # 信頼度フィルタ: 福島/小倉/阪神/京都 では「警戒対象が実は来やすい」ため非発動
        if (
            popularity is not None
            and popularity <= POPULAR_DOWNSIDE_POP_MAX
            and br >= POPULAR_DOWNSIDE_RANK_MIN
            and ar >= POPULAR_DOWNSIDE_RANK_MIN
            and not deny_popular_downside
        ):
            tags.append(SIGNAL_POPULAR_DOWNSIDE)

        h.dm_signals = tags


def popularity_from_odds(
    horse_numbers: list[int], win_odds_map: dict[int, float | None]
) -> dict[int, int]:
    """単勝オッズから人気を導出する。

    オッズが低い馬 = 人気上位 (1 が最人気)。同オッズは同人気。
    オッズが NULL の馬は最下位扱いせず、マップから除外する (シグナル発動回避)。
    """
    valid = [(hn, win_odds_map.get(hn)) for hn in horse_numbers]
    sortable = [(hn, o) for hn, o in valid if o is not None and o > 0]
    sortable.sort(key=lambda x: x[1])
    out: dict[int, int] = {}
    rank = 0
    last_o: float | None = None
    seen = 0
    for hn, o in sortable:
        seen += 1
        if last_o is None or o != last_o:
            rank = seen
            last_o = o
        out[hn] = rank
    return out
