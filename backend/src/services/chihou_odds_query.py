"""地方競馬「発走時刻以前の最新オッズ」を引く SQL。

API（表示）と前向き記録（`chihou_place_pick_log`）で**同じオッズ**を見る必要が
あるため、両方から import できる場所に置いている。片方だけ別のクエリを使うと
「画面に出ていた顔ぶれ」と「記録した顔ぶれ」がずれ、記録の意味が無くなる。
"""

from __future__ import annotations

# ⚠️ 素直に `DISTINCT ON (race_id, combination) ... ORDER BY fetched_at DESC` と書くと、
#    当日ぶん 12 万行を読んでディスクソート（external merge）が走り 850ms かかる。
#    odds_history は 9.4GB / 3200万行あり、1レースあたり約 700 スナップショットあるため。
#
#    `ix_chihou_odds_history_race_type_combo_time (race_id, bet_type, combination,
#    fetched_at DESC)` に完全に乗せるため、出走馬ごとの LATERAL + LIMIT 1 にしている。
#    これならインデックスの先頭 1 件を引くだけで済む。
#
# `{bet_types}` に "'win'" もしくは "'win', 'place'" を format で入れて使う
# （bet_type はインデックスの第2キーなので、種別ごとに引く方が速い）。
LATEST_ODDS_SQL = """
    SELECT r.id AS race_id, bt.bet_type, e.horse_number::text AS combination, o.odds
    FROM chihou.races r
    JOIN chihou.race_entries e ON e.race_id = r.id AND e.horse_number IS NOT NULL
    CROSS JOIN (VALUES ({bet_types})) AS bt(bet_type)
    CROSS JOIN LATERAL (
        SELECT oh.odds
        FROM chihou.odds_history oh
        WHERE oh.race_id = r.id
          AND oh.bet_type = bt.bet_type
          AND oh.combination = e.horse_number::text
          AND (
            r.post_time !~ '^[0-9]{{4}}$'
            OR oh.fetched_at <= (
                to_timestamp(r.date || r.post_time, 'YYYYMMDDHH24MI') - interval '9 hours'
            )
          )
        ORDER BY oh.fetched_at DESC
        LIMIT 1
    ) o
    WHERE r.id = ANY(:race_ids)
"""
