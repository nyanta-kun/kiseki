"""POG（ペーパーオーナーゲーム）の読み取り API。

統合 Phase 5 の 5d-3。sekito `backend/routes/pog.js`（2,668行・44 endpoint）の
うち、**実際に使われている 5 本**を移植する。

## なぜ 5 本なのか

nginx のアクセスログ 14 日ぶん（2026-08-25〜09-08）の実測で、`/api/pog/*` に
来たのは **44 本中 7 本だけ**。うち 4 本がほぼ全量だった:

    GET /owners-history              143
    GET /group/:id/recent-races      143
    GET /owners                      142
    GET /group/:id/user/:uid/horses  111
    GET /group/:id/user/all/horses     9
    GET /group/:id/sire-count          1
    GET /graded-wins                   1

🔴 **「残り 37 本は死んでいる」とは読めない。** ドラフト系（`/suggest`
`/roll` `/confirm`）は**年1回・春だけ**使われ、この 14 日はオフシーズン。
次のドラフトは 2027年春。判断材料にしてよいのは**移植の順序**であって、
削除の可否ではない（docs/pog_migration_plan_2026_09_08.md §5.1）。

## 移設元との違い

- 成績は `sekito.horse` の保存列ではなく `keiba.horse_runs` から**毎回数える**。
  保存列は誰も更新しておらず、2024年産は全頭 0 だった（services/pog_standings.py）
- 馬 ID の解決に `sekito.entries` を使わない。あちらは **2026-05-03 で凍結**
  しており、`v_entries.netkeiba_horse_id` は 2026-06 以降 **0%**
- 認証は付けない。移設元も POG の 44 本中 `requireAuth` は **0 本**で、
  読み取りは公開だった（匿名 curl で順位表が取れることを確認済み）。
  ⚠️ 画面側は Auth.js のログインが要る（proxy.ts）。ここを変えるなら
  「読み取りを公開のままにするか」の判断（Phase 4 の案 B）が要る
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db
from ..services.pog_horse_search import search_horses
from ..services.pog_horse_search import suggest as suggest_horse_values
from ..services.pog_rankings import METRICS as RANKING_METRICS
from ..services.pog_rankings import fetch_ranking
from ..services.pog_recent_races import fetch_recent_races
from ..services.pog_records import fetch_graded_wins, fetch_score_summary
from ..services.pog_siblings import fetch_siblings
from ..services.pog_standings import fetch_horses, fetch_owners

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pog", tags=["pog"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


class GroupOut(BaseModel):
    """POG のグループ（1年 = 1グループ）。"""

    id: int
    year: int
    name: str | None


class OwnerOut(BaseModel):
    """馬主別の成績。"""

    rank: int
    user_id: int
    name: str | None
    win: int
    place: int
    show: int
    out: int
    prize: int
    prize_jra: int
    prize_other: int
    top_horse: str | None


class OwnerRankOut(BaseModel):
    """順位だけ（変動矢印の計算に使う）。"""

    rank: int
    user_id: int


async def _group_id_for_year(db: AsyncSession, year: int) -> int:
    """年度からグループ id を引く。

    🔴 **外向きの指定は year にする。** 移設元の `group_id` は keiba へ写した
    時点で別体系になり（2026 = sekito 27 / keiba 22）、しかも sekito 側は
    年順ですらなかった（2025=3・2024=6）。ID を跨いで使うと必ず取り違える。
    year は一意（`uq_pog_groups_year`）で人が読んで分かる。
    """
    row = await db.execute(
        text("SELECT id FROM keiba.pog_groups WHERE year = :year"), {"year": year}
    )
    found = row.scalar_one_or_none()
    if found is None:
        raise HTTPException(status_code=404, detail=f"{year} 年度のグループがありません")
    return found


@router.get("/groups", response_model=list[GroupOut])
async def list_groups(db: DbDep) -> list[GroupOut]:
    """グループを新しい年度から返す。"""
    rows = await db.execute(
        text("SELECT id, year, name FROM keiba.pog_groups ORDER BY year DESC")
    )
    return [GroupOut(id=r.id, year=r.year, name=r.name) for r in rows]


@router.get("/owners", response_model=list[OwnerOut])
async def get_owners(
    db: DbDep, year: int = Query(..., description="POG の年度（例 2026）")
) -> list[OwnerOut]:
    """馬主別の成績を賞金の降順で返す。"""
    group_id = await _group_id_for_year(db, year)
    return [OwnerOut(**vars(o)) for o in await fetch_owners(db, group_id)]


@router.get("/owners-history", response_model=list[OwnerRankOut])
async def get_owners_history(
    db: DbDep,
    year: int = Query(..., description="POG の年度（例 2026）"),
    asof: date = Query(..., description="この日までの成績で順位を出す"),
) -> list[OwnerRankOut]:
    """指定日時点の順位を返す（順位変動の矢印に使う）。

    🔴 `/owners` と**同じ集計**に日付フィルタを足しただけにする。
    別のクエリで出すと、変動していないのに矢印が出る（移設元のコメントに
    その修正履歴が残っている）。
    """
    group_id = await _group_id_for_year(db, year)
    owners = await fetch_owners(db, group_id, asof=asof)
    return [OwnerRankOut(rank=o.rank, user_id=o.user_id) for o in owners]


@router.get("/horses")
async def get_group_horses(
    db: DbDep,
    year: int = Query(..., description="POG の年度（例 2026）"),
    user_id: int | None = Query(None, description="省略するとグループ全馬"),
) -> list[dict]:
    """指名馬の一覧を返す。

    移設元の `/group/:id/user/:uid/horses` と `/group/:id/user/all/horses` を
    1 本にまとめた（違いは絞り込みだけだった）。
    """
    group_id = await _group_id_for_year(db, year)
    return await fetch_horses(db, group_id, user_id=user_id)


@router.get("/recent-races")
async def get_recent_races(
    db: DbDep, year: int = Query(..., description="POG の年度（例 2026）")
) -> list[dict]:
    """指名馬の今週の出走（結果が出ていれば着順も）。

    14日のアクセスログで **143 回**と POG で最も叩かれていた画面の中身。
    移設元は地方を**凍結した `sekito.entries`** から引いていたので、
    2026-06 以降の地方の出走が 1 件も出ていなかった（services 側の docstring 参照）。
    """
    group_id = await _group_id_for_year(db, year)
    return await fetch_recent_races(db, group_id)


class MembershipOut(BaseModel):
    """その利用者が POG に参加しているか。ナビの出し分けに使う。"""

    is_member: bool
    latest_year: int | None


@router.get("/membership", response_model=MembershipOut)
async def get_membership(
    db: DbDep, user_id: int = Query(..., description="keiba.users.id")
) -> MembershipOut:
    """POG の参加者かどうかを返す。

    `latest_year` は最も新しい参加年度。**ナビの POG のリンク先を
    その年にするために**使う。

    🔴 **可視性の判定には使わなくなった**（2026-09-10）。POG を出すかは
    管理者が付ける `keiba.users.menu_pog` で決まる（判定の正本は
    `services/menu_access.py`）。参加実績で判定していた頃は、
    **その年度に指名しなかった人からリンクが消える**（＝過去の順位表も
    見られなくなる）という取り違えがあった。

    ⚠️ そのため `is_member=False` でもナビに POG が出ることがある。
    そのときのリンク先は `/pog`（最新年度へ転送される）。
    """
    row = await db.execute(
        text(
            "SELECT max(g.year) FROM keiba.pog_group_members m "
            "  JOIN keiba.pog_groups g ON g.id = m.group_id "
            " WHERE m.user_id = :user_id"
        ),
        {"user_id": user_id},
    )
    latest = row.scalar_one_or_none()
    return MembershipOut(is_member=latest is not None, latest_year=latest)


class HorseSearchOut(BaseModel):
    """ドラフトの候補馬 1 頭。"""

    netkeiba_horse_id: str
    # 🔴 未命名馬（＝ドラフトの主対象）は名前が NULL でありうる。
    #    2023年産は 7,765頭中 2,975頭が NULL だった。`str` にすると 500 になる。
    name: str | None
    sire: str
    broodmare: str
    broodmare_sire: str
    sex: str | None
    stable: str
    birth_year: int | None


class HorseSearchResponse(BaseModel):
    """候補馬の検索結果。`total` は絞り込み後の総数（ページ送りに使う）。"""

    items: list[HorseSearchOut]
    total: int


@router.get("/horse-search", response_model=HorseSearchResponse)
async def search_draft_horses(
    db: DbDep,
    birth_year: int | None = Query(None, description="産年。ドラフトでは必ず指定する"),
    name: str = Query("", description="馬名の部分一致"),
    sire: str = Query("", description="父名の部分一致"),
    broodmare: str = Query("", description="母名の部分一致"),
    page: int = Query(1, ge=1, description="1 起点のページ番号"),
) -> HorseSearchResponse:
    """ドラフトの候補馬を検索する（移設元 `/aobon`）。

    🔴 移設元は `keiba.provisional_horses` を UNION しており、そのテーブルを
    Phase 5a で消したため**本番で 500 を返していた**（sekito 側は #42 で修復）。
    こちらは最初から `keiba.pog_horses` だけを見る。
    """
    items, total = await search_horses(
        db,
        birth_year=birth_year,
        name=name,
        sire=sire,
        broodmare=broodmare,
        page=page,
    )
    return HorseSearchResponse(
        items=[HorseSearchOut(**i) for i in items], total=total
    )


@router.get("/horse-suggest", response_model=list[str])
async def suggest_draft_horses(
    db: DbDep,
    field: str = Query(..., description="name / sire / broodmare"),
    q: str = Query("", description="部分一致させる文字列"),
    birth_year: int | None = Query(None, description="産年で絞る"),
) -> list[str]:
    """検索欄の入力補完（移設元 `/suggest`）。"""
    try:
        return await suggest_horse_values(db, field=field, q=q, birth_year=birth_year)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class GradedWinOut(BaseModel):
    """POG 指名馬の重賞勝ち 1 件。"""

    date: str
    source: str
    race_id: int
    course_code: str | None
    course_name: str | None
    race_no: int
    race_name: str | None
    grade: str | None
    netkeiba_horse_id: str | None
    horse_name: str | None
    owner_name: str | None
    user_id: int
    pog_year: int


@router.get("/graded-wins", response_model=list[GradedWinOut])
async def list_graded_wins(
    db: DbDep,
    year: int | None = Query(None, description="POG の年度。省略すると全年度"),
    user_id: int | None = Query(None, description="馬主。省略すると全員"),
) -> list[GradedWinOut]:
    """POG 指名馬の重賞勝ちを新しい順に返す（移設元 `/graded-wins`・記録室）。

    🔴 移設元は `sekito.entries`（2026-05-03 で凍結）に依存した
    `sekito.mv_graded_wins` を見ており、地方は 2026-04-15 で止まり、
    中央も馬 ID が 2026年 89件中 51件しか付いていなかった。
    """
    return [GradedWinOut(**r) for r in await fetch_graded_wins(db, year=year, user_id=user_id)]


class WinsOut(BaseModel):
    """重賞勝ちの内訳。"""

    derby: int
    g1: int
    g2: int
    g3: int
    nar: int
    overseas_derby: int
    overseas_other: int


class ScoreOut(BaseModel):
    """スコア集計 1 人ぶん。"""

    rank: int
    prize_rank: int
    user_id: int
    name: str | None
    total_prize: int
    basic_points: int
    rank_prize: int
    special_prize: int
    total_points: int
    win: int
    place: int
    show: int
    out: int
    horse_count: int
    horses_raced: int
    horses_won: int
    all_raced: bool
    all_won: bool
    wins: WinsOut


@router.get("/score-summary", response_model=list[ScoreOut])
async def get_score_summary(
    db: DbDep, year: int = Query(..., description="POG の年度（例 2026）")
) -> list[ScoreOut]:
    """スコア集計（精算表）を合計pt の降順で返す（移設元 `/score-summary`）。

    🔴 **実際の精算に使う数字。** 計算は `services/pog_score.py` の純関数に
    切り出してあり、`tests/test_pog_score.py` が固定している。

    🔴 移設元との違い: **中央交流競走を地方重賞（500pt）として数える**。
    JV-Link は中央交流を `G1/G2/G3` として持つため、格だけで判定すると
    中央 G1（1,000pt）になってしまう。詳細は pog_score.py の docstring。
    """
    group_id = await _group_id_for_year(db, year)
    scores = await fetch_score_summary(db, group_id=group_id, year=year)
    return [
        ScoreOut(**{**s.__dict__, "wins": WinsOut(**s.wins.__dict__)}) for s in scores
    ]


class RankingRow(BaseModel):
    """ランキング 1 行。指標によって使う欄が変わる。"""

    #: 束ねた単位（種牡馬名 / 母父名 / 厩舎名 / 馬名 / 馬主名）。
    key: str
    #: その束に属する指名頭数。
    count: int
    #: 主指標。指標により 指名数 / 平均賞金(万) / 勝率(%) など。
    value: float
    #: 補助の整数（勝ち数・出走した頭数など）。使わない指標では None。
    sub: int | None
    #: 母数（出走数など）。使わない指標では None。
    total: int | None
    g1: int
    g2: int
    g3: int


class RankingOut(BaseModel):
    """ランキング 1 本。"""

    metric: str
    label: str
    rows: list[RankingRow]


@router.get("/rankings", response_model=RankingOut)
async def get_ranking(
    db: DbDep,
    metric: str = Query(..., description="指標。/api/pog/ranking-metrics で一覧"),
    year: int | None = Query(None, description="POG の年度。省略すると全年度（通算）"),
    limit: int = Query(50, ge=1, le=200),
) -> RankingOut:
    """POG のランキングを返す（移設元の 18 エンドポイントを 1 本にまとめたもの）。

    移設元は 9 指標 × 2 スコープ（`/group/:id/*` と `/all-groups/*`）に
    分かれていたが、どれも「何かで束ねて数える」だけなので統合した。
    `year` の有無がスコープに対応する。
    """
    try:
        rows = await fetch_ranking(db, metric=metric, year=year, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RankingOut(
        metric=metric,
        label=RANKING_METRICS[metric].label,
        rows=[RankingRow(**r) for r in rows],
    )


@router.get("/ranking-metrics", response_model=dict[str, str])
async def list_ranking_metrics() -> dict[str, str]:
    """使える指標と表示名の対応を返す。"""
    return {k: m.label for k, m in RANKING_METRICS.items()}


class SiblingHorseOut(BaseModel):
    """兄弟馬 1 頭。"""

    year: int
    netkeiba_horse_id: str
    horse_name: str
    sex: str | None
    sire: str | None
    stable: str | None
    owner_name: str | None
    pick_order: int
    win: int
    place: int
    show: int
    out: int
    prize: int


class SiblingGroupOut(BaseModel):
    """同じ母から指名された馬のまとまり。"""

    broodmare: str
    nomination_count: int
    horses: list[SiblingHorseOut]


@router.get("/siblings", response_model=list[SiblingGroupOut])
async def list_siblings(
    db: DbDep,
    min_nominations: int = Query(2, ge=2, le=20, description="何回以上指名された母を出すか"),
) -> list[SiblingGroupOut]:
    """同じ母から複数回指名されている馬をまとめて返す（移設元 `/sibling-horses`）。

    ドラフトの下調べ（この母の上の子はどうだったか）に使う。

    🔴 移設元は `sekito.horse` の保存列から戦績を作っていたが、その列は
    更新されておらず新しい世代ほど空だった。ここは `keiba.horse_runs` から数える。

    ⚠️ **2017年度以前の指名馬は成績が出ない。** `keiba.horses` の生年別
    カバレッジが 2013年産以前で極端に薄いため（2009年産は 229頭のみ）。
    """
    rows = await fetch_siblings(db, min_nominations=min_nominations)
    return [
        SiblingGroupOut(
            broodmare=g["broodmare"],
            nomination_count=g["nomination_count"],
            horses=[SiblingHorseOut(**h) for h in g["horses"]],
        )
        for g in rows
    ]
