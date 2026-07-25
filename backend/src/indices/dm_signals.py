"""DM (JV-Next タイム型・対戦型) シグナルタグ算出モジュール

合成ウェイトに混ぜず、特定条件を満たす馬に「軸」「穴」「警戒」タグを付与する。

軸シグナル (信頼度、バックテスト実証済み・3年8,618レース):
  TRIPLE_MATCH (🔥 三冠一致):
    base_rank=1 ∧ time_rank=1 ∧ battle_rank=1
    勝率 39.1% / 複勝 71.6% / ROI 84.9% / n=1,622
    → 軸固定で複勝・三連複に厚く

  TOP_PREMIUM (⭐ 高得点鉄板):
    composite_index ≥60 ∧ jvan_battle_dm ≥65 ∧ composite順位 ≤2
    勝率 46.5% / 複勝 74.4% / ROI 101.2% / n=86 (順位条件追加前のバックテスト値)
    → 単勝フラット買いでもプラス収支
    ※ 絶対しきい値のみだと強メンバー戦で多数該当し鉄板印が乱発するため
      (実測2026: 2頭以上66.6%/最大6頭)、composite上位2頭に限定 (2026-06-07)

穴シグナル (⚠️ 2026-07-25 再設計 [[jra_upset_badge_redesign]]):
  旧: ANAGUSA_DM/DM_BIG_DARK/DM_HIGH_ODDS/ANAGUSA_DM_TIME の4タグ（各々が
  anagusa/netkeiba/kichiuma/DM battleの一部を狭いAND条件で組み合わせ、
  場・距離帯ごとにdeny filterを個別に貼り付けていた）は、OOS検証
  (jra_verify_signals.py)でn=10〜184と小標本になりがちで有意性が不安定
  だったため廃止。3年+完全OOSのセグメント異質性分析で、4情報源
  (穴ぐさ/netkeiba/kichiuma/DM battle)の**一致数(badge_cnt)**が単純な
  カウントのまま両窓で頑健・単調な複勝的中率の分離を示した
  (badge0=7-10%→badge1=14-16%→badge2+=17-21%、train/testとも大サンプルで一貫)
  ため、これに一本化する:
  MULTI_SOURCE_MATCH (🔍複数指数一致穴、badge_cnt≥2):
    複勝的中 17-29%（指数順位1-3位で特に高い）
  SINGLE_SOURCE_MATCH (指数一致穴、badge_cnt=1):
    複勝的中 12-24%
  対象母集団は単勝オッズ≥10の人気薄馬のみ（is_sweet_spot/upset_reranker等と
  同じ閾値）。badge_cntの4情報源のうちDM battle≤2は、その馬のいるレースで
  DM対戦型指数が2頭以上揃っている場合のみカウントする(全頭DM必須の
  TRIPLE_MATCH/TOP_PREMIUM と異なりカバレッジを落とさない設計)。
  複勝ROIはこの母集団では全セグメントで<1(控除率の壁)であり、
  「回収率」でなく「的中率の頑健な分離」を目的としたタグである点に注意。

警戒シグナル:
  POPULAR_DOWNSIDE (❌ 人気下振れ):
    win_popularity≤3 ∧ base_rank≥4 ∧ battle_rank≥4
    勝率 15.3% / ROI 73.9% / n=3,563
    → 人気だが両指数で評価低い人気馬。軸から外す対象

API レスポンスにタグを付与し、フロントエンドでバッジ表示する想定。
TRIPLE_MATCH/TOP_PREMIUM/POPULAR_DOWNSIDEは全馬DM値必須(中途半端なシグナルを
避けるため)。MULTI_SOURCE_MATCH/SINGLE_SOURCE_MATCHはDM値が部分欠損でも計算する。
"""

from __future__ import annotations

from typing import Any, Protocol

# シグナル文字列定数 (UI 表示用にラベル付き、API では key を返す)
SIGNAL_TRIPLE_MATCH = "三冠一致"
SIGNAL_TOP_PREMIUM = "高得点鉄板"
SIGNAL_MULTI_SOURCE_MATCH = "複数指数一致穴"
SIGNAL_SINGLE_SOURCE_MATCH = "指数一致穴"
SIGNAL_POPULAR_DOWNSIDE = "人気下振れ"

# 高得点鉄板しきい値 (バックテスト確定)
TOP_PREMIUM_BASE_MIN = 60.0
TOP_PREMIUM_BATTLE_MIN = 65.0
# 高得点鉄板の発動頭数上限 (レース内 composite 順位の上限)。
# 絶対しきい値だけだと強メンバー戦で多数該当し「鉄板印」が乱発する
# (実測 2026 DM揃い1,421R: 2頭以上 66.6% / 3頭以上 30.5% / 最大6頭)。
# composite 上位 N 頭に限定して「抜けた本命」の意味を保つ。
# 2 にすると鉄板印(高得点鉄板 ∪ 三冠一致)を持つ頭数は 99.9% のレースで ≤2 になる。
TOP_PREMIUM_RANK_MAX = 2

# 穴badge対象の単勝オッズ下限（is_sweet_spot/upset_reranker と同一閾値）
UPSET_BADGE_MIN_ODDS = 10.0
# badge_cnt→タグの閾値（[[jra_upset_badge_redesign]] セグメント分析で確定）
UPSET_BADGE_MULTI_MIN = 2

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
    nb_ave_rank: int | None
    km_rank: int | None
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
    exclude_horse_numbers: set[int] | None = None,
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
        exclude_horse_numbers: 出走取消・発走除外馬の馬番セット。
                               シグナル判定・順位計算の母集団から除外する
                               (取消馬の DM 欠損でレース全体が全消しになるのを防ぐ)。

    DM 値 (time/battle) のいずれかが NULL のレースではシグナルは付与されない
    (中途半端なシグナルを避けるため)。除外馬はこの判定にも含めない。

    条件 (course/surface/distance) が渡されない場合は条件絞り込みなし
    (旧挙動互換)。
    """
    if not horses:
        return

    # 全馬の dm_signals を [] に初期化 (None だと未計算と区別できない)
    for h in horses:
        h.dm_signals = []

    # 取消・除外馬を母集団から外す（dm_signals は [] のまま）
    excluded = exclude_horse_numbers or set()
    horses = [h for h in horses if h.horse_number not in excluded]
    if not horses:
        return

    pop = popularity_map or {}
    odds = win_odds_map or {}

    # --- 穴badge (MULTI/SINGLE_SOURCE_MATCH): DM完全揃い不要・部分カバレッジでも計算 ---
    # DM battle は「そのレースで2頭以上値がある」場合のみランクを算出し、badge_cnt に含める。
    # 全馬DM必須の軸/警戒シグナルと異なりカバレッジを落とさない設計([[jra_upset_badge_redesign]])。
    partial_battle_ranks = _ranks_descending([h.jvan_battle_dm for h in horses])
    for i, h in enumerate(horses):
        win_odds = odds.get(h.horse_number)
        if win_odds is None or win_odds < UPSET_BADGE_MIN_ODDS:
            continue
        # nb_ave_rank/km_rank は getattr で防御的に読む（Protocol非準拠の
        # 呼び出し元(旧集計スクリプト等)でも badge_cnt 計算が落ちないように）
        nb_ave_rank = getattr(h, "nb_ave_rank", None)
        km_rank = getattr(h, "km_rank", None)
        badge_cnt = 0
        if h.anagusa_rank in ("A", "B", "C"):
            badge_cnt += 1
        if nb_ave_rank is not None and nb_ave_rank <= 3:
            badge_cnt += 1
        if km_rank is not None and km_rank <= 3:
            badge_cnt += 1
        if partial_battle_ranks[i] is not None and partial_battle_ranks[i] <= 2:
            badge_cnt += 1

        if badge_cnt >= UPSET_BADGE_MULTI_MIN:
            h.dm_signals.append(SIGNAL_MULTI_SOURCE_MATCH)
        elif badge_cnt == 1:
            h.dm_signals.append(SIGNAL_SINGLE_SOURCE_MATCH)

    # DM データがレース内で揃っているか確認 (1頭でも NULL なら軸/警戒シグナルはスキップ)
    if any(h.jvan_time_dm is None or h.jvan_battle_dm is None for h in horses):
        return

    # ランク算出
    base_ranks = _ranks_descending([h.composite_index for h in horses])
    time_ranks = _ranks_descending([h.jvan_time_dm for h in horses])
    battle_ranks = _ranks_descending([h.jvan_battle_dm for h in horses])

    # レース条件 (信頼度フィルタ用)
    surf_cat = _surface_cat(surface)
    dist_cat = _dist_cat(distance)
    seg = (surf_cat, dist_cat) if surf_cat and dist_cat else None

    # フィルタフラグ (False ならそのシグナル発動可)
    deny_triple = (
        (course_name in TRIPLE_MATCH_DENY_COURSES)
        or (seg in TRIPLE_MATCH_DENY_SEGMENTS)
    )
    deny_popular_downside = course_name in POPULAR_DOWNSIDE_DENY_COURSES

    for i, h in enumerate(horses):
        br = base_ranks[i]
        tr = time_ranks[i]
        ar = battle_ranks[i]
        if br is None or tr is None or ar is None:
            continue

        battle_dm = h.jvan_battle_dm or 0.0
        popularity = pop.get(h.horse_number)

        # 🔥 三冠一致: base=1 ∧ time=1 ∧ battle=1
        # 信頼度フィルタ: 福島/阪神/京都, 芝マイル, ダート中距離は除外
        if br == 1 and tr == 1 and ar == 1 and not deny_triple:
            h.dm_signals.append(SIGNAL_TRIPLE_MATCH)

        # ⭐ 高得点鉄板: composite≥60 ∧ battle≥65 ∧ composite順位≤2
        # 絶対しきい値だけだと強メンバー戦で多数該当し鉄板印が乱発するため、
        # レース内 composite 上位 TOP_PREMIUM_RANK_MAX 頭に限定する。
        if (
            h.composite_index >= TOP_PREMIUM_BASE_MIN
            and battle_dm >= TOP_PREMIUM_BATTLE_MIN
            and br <= TOP_PREMIUM_RANK_MAX
        ):
            h.dm_signals.append(SIGNAL_TOP_PREMIUM)

        # ❌ 人気下振れ: 人気≤3 ∧ base≥4位 ∧ battle≥4位
        # 信頼度フィルタ: 福島/小倉/阪神/京都 では「警戒対象が実は来やすい」ため非発動
        if (
            popularity is not None
            and popularity <= POPULAR_DOWNSIDE_POP_MAX
            and br >= POPULAR_DOWNSIDE_RANK_MIN
            and ar >= POPULAR_DOWNSIDE_RANK_MIN
            and not deny_popular_downside
        ):
            h.dm_signals.append(SIGNAL_POPULAR_DOWNSIDE)


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
