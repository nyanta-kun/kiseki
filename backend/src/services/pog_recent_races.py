"""POG 指名馬の「今週の出走」（sekito `/group/:id/recent-races` の移設先）。

14 日のアクセスログで **143 回**と、POG の中で最も叩かれていた API。

## 🔴 地方を `sekito.entries` から `chihou.*` 直読みへ変えた

移設元は中央を既に `keiba.*` 直読みへ書き換えていた（v_entries が重かったため）
が、**地方だけ `sekito.entries` に残っていた**。そのテーブルは
**2026-05-03 で凍結**しており、それ以降の地方の出走は 1 件も出ない。

    sekito.entries の最終日          2026-05-03
    v_entries.netkeiba_horse_id      2026-06 以降 0%

ここでは `chihou.race_entries` / `race_results` / `jockeys` / `odds_history` を
直接見る。馬の対応は `chihou.horses.umaconn_code = 指名の netkeiba_horse_id`
（`sekito.mv_horse_runs` と同じキー）。

## 期間

移設元と同じ式を使う（変えると「今週」の見え方が変わる）:

    開始 = 今日 - ((曜日 + 1) % 7)          日曜起点で直前の土曜あたり
    終了 = 今日 + (((5 - 曜日 + 7) % 7) + 3)
    特別登録だけ 開始 + 21 日まで（出馬表確定前の先々情報のため）

## 中央のオッズ

`keiba.latest_odds`（発走前の最新スナップショット）を使う。移設元は
`odds_history` を LATERAL で引いていたが、`latest_odds` は同じ値を
1 行で持つ（13GB の履歴を毎回舐めない）。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SQL = text(
    """
    WITH base AS (
        SELECT CURRENT_DATE AS today, EXTRACT(DOW FROM CURRENT_DATE)::int AS dow
    ),
    range AS (
        SELECT today - INTERVAL '1 day' * ((dow + 1) % 7)                 AS start_date,
               today + INTERVAL '1 day' * (((5 - dow + 7) % 7) + 3)       AS end_date,
               today + INTERVAL '1 day' * ((dow + 1) % 7 * -1 + 21)       AS special_end
        FROM base
    ),
    picks AS (
        SELECT p.netkeiba_horse_id,
               COALESCE(m.nickname, u.name) AS owner_name
        FROM keiba.pog_picks p
        JOIN keiba.users u ON u.id = p.user_id
        LEFT JOIN keiba.pog_group_members m
               ON m.group_id = p.group_id AND m.user_id = p.user_id
        WHERE p.group_id = :group_id
          AND p.pick_order > 0
          AND p.netkeiba_horse_id IS NOT NULL
    ),
    -- ===== 中央（keiba を直読み）=====
    jra AS (
        SELECT
            keiba.to_date_imm(kr.date, 'YYYYMMDD')      AS date,
            rc.code                                      AS course_code,
            rc.name                                      AS course_name,
            kr.race_number                               AS race_no,
            kr.race_name,
            kr.surface                                   AS ground,
            kr.distance,
            kr.post_time,
            re.horse_number                              AS horse_no,
            kh.jravan_code                               AS netkeiba_horse_id,
            kh.name                                      AS horse_name,
            kj.name                                      AS jockey,
            rr.win_popularity::int                       AS ninki,
            -- 🔴 確定オッズを優先する。発走後は race_results.win_odds が正で、
            --    latest_odds は最終スナップショットなので通常一致するが、
            --    確定値がある行はそれを使う方が誤解が無い。
            --    ⚠️ 移設元は odds_history の最新を引いていたが、発走後の行を
            --       剪定した後は「発走前の途中値」を拾ってしまう
            --       （実測 2026-09-05: 移設元 3.1 / 確定 2.2）。
            COALESCE(rr.win_odds, lo.odds)::float        AS tan,
            CASE WHEN rr.finish_position IS NOT NULL
                 THEN rr.finish_position::text END       AS result,
            (COALESCE(rr.prize_money, 0) / 100)::int     AS prize
        FROM picks pk
        JOIN keiba.horses kh       ON kh.jravan_code = pk.netkeiba_horse_id
        JOIN keiba.race_entries re ON re.horse_id = kh.id
        JOIN keiba.races kr        ON kr.id = re.race_id
        JOIN keiba.racecourse_map rc ON rc.jra_code = kr.course
        LEFT JOIN keiba.jockeys kj ON kj.id = re.jockey_id
        LEFT JOIN keiba.race_results rr
               ON rr.race_id = re.race_id AND rr.horse_id = kh.id
        LEFT JOIN keiba.latest_odds lo
               ON lo.race_id = re.race_id
              AND lo.bet_type = 'win'
              AND lo.combination = re.horse_number::text
        WHERE keiba.to_date_imm(kr.date, 'YYYYMMDD')
              BETWEEN (SELECT start_date FROM range) AND (SELECT end_date FROM range)
    ),
    -- ===== 地方（chihou を直読み。移設元は凍結した sekito.entries を見ていた）=====
    chihou AS (
        SELECT
            keiba.to_date_imm(cr.date, 'YYYYMMDD')      AS date,
            rc.code                                      AS course_code,
            rc.name                                      AS course_name,
            cr.race_number                               AS race_no,
            cr.race_name,
            cr.surface                                   AS ground,
            cr.distance,
            cr.post_time,
            re.horse_number                              AS horse_no,
            ch.umaconn_code                              AS netkeiba_horse_id,
            ch.name                                      AS horse_name,
            cj.name                                      AS jockey,
            rr.win_popularity::int                       AS ninki,
            rr.win_odds::float                           AS tan,
            CASE WHEN rr.finish_position IS NOT NULL
                 THEN rr.finish_position::text END       AS result,
            (COALESCE(rr.prize_money, 0) / 100)::int     AS prize
        FROM picks pk
        JOIN chihou.horses ch       ON ch.umaconn_code = pk.netkeiba_horse_id
        JOIN chihou.race_entries re ON re.horse_id = ch.id
        JOIN chihou.races cr        ON cr.id = re.race_id
        JOIN keiba.racecourse_map rc ON rc.netkeiba_id = cr.course
        LEFT JOIN chihou.jockeys cj ON cj.id = re.jockey_id
        LEFT JOIN chihou.race_results rr
               ON rr.race_id = re.race_id AND rr.horse_id = ch.id
        WHERE keiba.to_date_imm(cr.date, 'YYYYMMDD')
              BETWEEN (SELECT start_date FROM range) AND (SELECT end_date FROM range)
          -- 中央との交流重賞は中央側で出す（mv_horse_runs と同じ重複排除）
          AND NOT EXISTS (
              SELECT 1 FROM keiba.races kr2
              WHERE kr2.date = cr.date
                AND kr2.course = cr.course
                AND kr2.race_number = cr.race_number
          )
    ),
    -- ===== 中央の特別登録（出馬表が確定する前の先々情報）=====
    special AS (
        SELECT
            keiba.to_date_imm(sr.race_date, 'YYYYMMDD') AS date,
            rc.code                                      AS course_code,
            rc.name                                      AS course_name,
            sr.race_number                               AS race_no,
            sr.race_name,
            NULL::varchar                                AS ground,
            sr.distance,
            NULL::varchar                                AS post_time,
            NULL::int                                    AS horse_no,
            sr.jravan_horse_code                         AS netkeiba_horse_id,
            sr.horse_name,
            sr.expected_jockey_name                      AS jockey,
            NULL::int                                    AS ninki,
            NULL::float                                  AS tan,
            NULL::text                                   AS result,
            0                                            AS prize
        FROM picks pk
        JOIN keiba.special_registrations sr
              ON sr.jravan_horse_code = pk.netkeiba_horse_id
        JOIN keiba.racecourse_map rc ON rc.jra_code = sr.course_code
        WHERE keiba.to_date_imm(sr.race_date, 'YYYYMMDD')
              BETWEEN (SELECT start_date FROM range) AND (SELECT special_end FROM range)
          -- 🔴 そのレースの**出馬表が公開されていたら出さない**。
          --    特別登録は「予定」なので、確定した枠に居ない＝結局出走しなかった
          --    馬を過去の日付で並べても意味が無い。
          --    移設元も同じ規則（`WHERE me.horse_no IS NOT NULL OR
          --    res.has_non_null_horse_no = false`）を最後の WHERE で当てている。
          --    ここで消す方が、無駄な行を作ってから捨てるより素直。
          AND NOT EXISTS (
              SELECT 1
              FROM keiba.races kr3
              JOIN keiba.race_entries re3 ON re3.race_id = kr3.id
              WHERE kr3.jravan_race_id = sr.jravan_race_id
                AND re3.horse_number IS NOT NULL
          )
    ),
    merged AS (
        SELECT * FROM jra
        UNION ALL SELECT * FROM chihou
        UNION ALL SELECT * FROM special
    )
    SELECT
        m.date, m.course_code, m.course_name, m.race_no, m.race_name,
        m.ground, m.distance, m.post_time, m.horse_no,
        m.netkeiba_horse_id, m.horse_name, m.jockey, m.ninki, m.tan,
        m.result, m.prize,
        h.sex, h.sire, h.broodmare, h.broodmare_sire,
        array_agg(DISTINCT pk.owner_name) FILTER (WHERE pk.owner_name IS NOT NULL)
            AS owners
    FROM merged m
    JOIN picks pk ON pk.netkeiba_horse_id = m.netkeiba_horse_id
    LEFT JOIN keiba.pog_horses h ON h.netkeiba_horse_id = m.netkeiba_horse_id
    GROUP BY m.date, m.course_code, m.course_name, m.race_no, m.race_name,
             m.ground, m.distance, m.post_time, m.horse_no,
             m.netkeiba_horse_id, m.horse_name, m.jockey, m.ninki, m.tan,
             m.result, m.prize, h.sex, h.sire, h.broodmare, h.broodmare_sire
    ORDER BY m.date, m.course_code, m.race_no, m.horse_no NULLS LAST
    """
)


async def fetch_recent_races(db: AsyncSession, group_id: int) -> list[dict]:
    """POG 指名馬の今週の出走を返す（結果が出ていれば着順も）。"""
    rows = await db.execute(_SQL, {"group_id": group_id})
    return [dict(r._mapping) for r in rows]
