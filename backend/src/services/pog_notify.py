"""POG の Discord 通知（sekito の notify 3 ジョブの移設先）。

    result   id=93  `*/10 10-23 * * *`   着順が確定したら送る
    barrier  id=94  `0 13 * * *`         翌日の枠順が確定したら送る
    entry    id=95  `30 19 * * 3`        週末（土日月）の出走想定を送る

result は 1 レース 1 馬ごとに重複を判定し、barrier / entry は
**その日ぶんをまとめて 1 通**なので (group, 種別, 日付) で判定する。

## 移設元との違い

| | 移設元 | ここ |
|---|---|---|
| 出走・着順 | `sekito.v_entries` 経由（**馬名**で突合） | `keiba` / `chihou` の `race_results` を直読み（**netkeiba_horse_id** で突合） |
| 重複判定 | SELECT → 送信 → INSERT | 同じ順序だが**一意インデックスで DB も守る** |
| 送信先 | `sekito.pog_group` | `keiba.pog_groups` |

馬名での突合をやめたのは、`sekito.entries` が 2026-05-03 で凍結しており、
同名の別馬や表記ゆれに弱いため。指名は netkeiba_horse_id で持っている
（`keiba.pog_picks`）ので、そのまま結合できる。

## 🔴 送ってから記録する

記録を先にすると、送信に失敗したときに**二度と送られない**。
重複の方がまだましなので、移設元と同じ「送信 → 記録」を守る。
そのうえで一意インデックス（`uq_pog_notifications_dedup`）が
同時実行による二重記録を防ぐ。

## 通知の中身

移設元の Discord embed をそのまま踏襲する（見た目が変わると気づかれる）:

    タイトル  🏆 {グループ名} POG馬結果速報
    色        0xf39c12
    競馬場ごとに field を作り、着順順に並べる
    🥇🥈🥉 は 1〜3 着、それ以外は 📍
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DISCORD_TIMEOUT = 30
EMBED_COLOR = 0xF39C12
USERNAME = "赤兎 POG通知"

# その日に着順が確定した POG 指名馬。中央と地方の両方。
_RESULTS_SQL = text(
    """
    WITH picks AS (
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
    jra AS (
        SELECT rc.code AS course_code, rc.name AS course_name,
               kr.race_number AS race_no, kh.name AS horse_name,
               rr.finish_position, pk.owner_name
        FROM picks pk
        JOIN keiba.horses kh ON kh.jravan_code = pk.netkeiba_horse_id
        JOIN keiba.race_results rr ON rr.horse_id = kh.id
        JOIN keiba.races kr ON kr.id = rr.race_id
        JOIN keiba.racecourse_map rc ON rc.jra_code = kr.course
        WHERE kr.date = :ymd AND rr.finish_position IS NOT NULL
    ),
    chihou AS (
        SELECT rc.code AS course_code, rc.name AS course_name,
               cr.race_number AS race_no, ch.name AS horse_name,
               rr.finish_position, pk.owner_name
        FROM picks pk
        JOIN chihou.horses ch ON ch.umaconn_code = pk.netkeiba_horse_id
        JOIN chihou.race_results rr ON rr.horse_id = ch.id
        JOIN chihou.races cr ON cr.id = rr.race_id
        JOIN keiba.racecourse_map rc ON rc.netkeiba_id = cr.course
        WHERE cr.date = :ymd AND rr.finish_position IS NOT NULL
          -- 交流重賞は中央側で出す（重複を避ける）
          AND NOT EXISTS (
              SELECT 1 FROM keiba.races kr2
              WHERE kr2.date = cr.date AND kr2.course = cr.course
                AND kr2.race_number = cr.race_number
          )
    )
    SELECT * FROM jra
    UNION ALL SELECT * FROM chihou
    ORDER BY finish_position, course_code, race_no
    """
)

_SENT_SQL = text(
    """
    SELECT 1 FROM keiba.pog_notifications
    WHERE group_id = :group_id AND notification_type = 'result'
      AND race_date = :race_date AND course_code = :course_code
      AND race_no = :race_no AND horse_name = :horse_name
    """
)

# barrier / entry は「その日ぶんを 1 通」なので日付だけで判定する。
_SENT_DAY_SQL = text(
    """
    SELECT 1 FROM keiba.pog_notifications
    WHERE group_id = :group_id AND notification_type = :kind
      AND race_date = :race_date AND course_code IS NULL
    """
)

_RECORD_DAY_SQL = text(
    """
    INSERT INTO keiba.pog_notifications
        (group_id, notification_type, race_date, content)
    VALUES (:group_id, :kind, :race_date, :content)
    ON CONFLICT DO NOTHING
    """
)

# 出走想定（確定出馬表が出る前）。keiba.projected_entries は netkeiba_horse_id を
# 持つので、指名とそのまま結合できる（移設元も同じ）。
_PROJECTED_SQL = text(
    """
    SELECT pe.course_code, rm.name AS course_name, pe.race_number AS race_no,
           pe.horse_name, pe.expected_jockey_name AS jockey,
           NULL::int AS barrier, NULL::int AS horse_no, NULL::numeric AS weight,
           COALESCE(m.nickname, u.name) AS owner_name
    FROM keiba.projected_entries pe
    JOIN keiba.racecourse_map rm ON rm.jra_code = pe.course_code
    JOIN keiba.pog_picks p ON p.netkeiba_horse_id = pe.netkeiba_horse_id
                          AND p.group_id = :group_id
                          AND p.visible AND p.pick_order > 0
    JOIN keiba.users u ON u.id = p.user_id
    LEFT JOIN keiba.pog_group_members m
           ON m.group_id = p.group_id AND m.user_id = p.user_id
    WHERE pe.race_date = :ymd
    ORDER BY pe.course_code, pe.race_number, pe.horse_name
    """
)

# 枠順が確定した出走（中央・地方）。移設元は v_entries 経由で**馬名**突合だった。
_BARRIER_SQL = text(
    """
    WITH picks AS (
        SELECT p.netkeiba_horse_id, COALESCE(m.nickname, u.name) AS owner_name
        FROM keiba.pog_picks p
        JOIN keiba.users u ON u.id = p.user_id
        LEFT JOIN keiba.pog_group_members m
               ON m.group_id = p.group_id AND m.user_id = p.user_id
        WHERE p.group_id = :group_id AND p.visible AND p.pick_order > 0
          AND p.netkeiba_horse_id IS NOT NULL
    ),
    jra AS (
        SELECT rc.code AS course_code, rc.name AS course_name,
               kr.race_number AS race_no, kh.name AS horse_name,
               kj.name AS jockey, re.frame_number AS barrier,
               re.horse_number AS horse_no, re.weight_carried AS weight,
               pk.owner_name
        FROM picks pk
        JOIN keiba.horses kh ON kh.jravan_code = pk.netkeiba_horse_id
        JOIN keiba.race_entries re ON re.horse_id = kh.id
        JOIN keiba.races kr ON kr.id = re.race_id
        JOIN keiba.racecourse_map rc ON rc.jra_code = kr.course
        LEFT JOIN keiba.jockeys kj ON kj.id = re.jockey_id
        WHERE kr.date = :ymd AND re.frame_number IS NOT NULL
    ),
    chihou AS (
        SELECT rc.code AS course_code, rc.name AS course_name,
               cr.race_number AS race_no, ch.name AS horse_name,
               cj.name AS jockey, re.frame_number AS barrier,
               re.horse_number AS horse_no, re.weight_carried AS weight,
               pk.owner_name
        FROM picks pk
        JOIN chihou.horses ch ON ch.umaconn_code = pk.netkeiba_horse_id
        JOIN chihou.race_entries re ON re.horse_id = ch.id
        JOIN chihou.races cr ON cr.id = re.race_id
        JOIN keiba.racecourse_map rc ON rc.netkeiba_id = cr.course
        LEFT JOIN chihou.jockeys cj ON cj.id = re.jockey_id
        WHERE cr.date = :ymd AND re.frame_number IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM keiba.races kr2
              WHERE kr2.date = cr.date AND kr2.course = cr.course
                AND kr2.race_number = cr.race_number
          )
    )
    SELECT * FROM jra
    UNION ALL SELECT * FROM chihou
    ORDER BY course_code, race_no
    """
)

_RECORD_SQL = text(
    """
    INSERT INTO keiba.pog_notifications
        (group_id, notification_type, race_date, course_code, race_no,
         horse_name, content)
    VALUES (:group_id, 'result', :race_date, :course_code, :race_no,
            :horse_name, :content)
    ON CONFLICT DO NOTHING
    """
)

_GROUPS_SQL = text(
    """
    SELECT id, year, name, discord_webhook_url, notification_settings
    FROM keiba.pog_groups
    WHERE discord_enabled AND discord_webhook_url IS NOT NULL
      AND (notification_platform IS NULL OR notification_platform = 'discord')
    ORDER BY year DESC
    """
)


@dataclass
class NotifyResult:
    """1 実行ぶんの結果。"""

    groups: int = 0
    candidates: int = 0
    sent: int = 0
    already: int = 0
    failed: int = 0


def _position_emoji(position: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(position, "📍")


def build_embed(group_name: str, day: date, rows: list[dict]) -> dict:
    """移設元と同じ形の Discord embed を作る。"""
    by_course: dict[str, list[dict]] = {}
    for r in rows:
        by_course.setdefault(r["course_name"], []).append(r)

    fields = []
    for course_name, items in by_course.items():
        lines = []
        for r in items:
            owner = f" **({r['owner_name']})**" if r.get("owner_name") else ""
            lines.append(
                f"{_position_emoji(r['finish_position'])} **{r['race_no']}R** "
                f"{r['horse_name']}{owner} **{r['finish_position']}着**"
            )
        fields.append({"name": f"📍 {course_name}", "value": "\n".join(lines),
                       "inline": False})

    return {
        "title": f"🏆 {group_name} POG馬結果速報",
        "description": f"📅 **{day.strftime('%Y年%m月%d日')}**",
        "color": EMBED_COLOR,
        # ⚠️ offset 無しの ISO8601 は Discord に UTC と解釈され +9h ずれる。
        #    移設元が踏んで直したところなので、必ず tz 付きで送る。
        "timestamp": datetime.now(UTC).isoformat(),
        "fields": fields,
    }


def send_discord(webhook_url: str, embed: dict) -> bool:
    """Discord へ送る。成功したら True。"""
    try:
        res = requests.post(
            webhook_url,
            json={"username": USERNAME, "embeds": [embed]},
            headers={"Content-Type": "application/json"},
            timeout=DISCORD_TIMEOUT,
        )
    except requests.RequestException as e:  # noqa: BLE001
        logger.error("Discord 送信でエラー: %s", e)
        return False
    if res.status_code == 204:
        return True
    logger.error("Discord 送信失敗: %s %s", res.status_code, res.text[:200])
    return False


def notify_results(session: Session, day: date, *, dry_run: bool = False) -> NotifyResult:
    """指定日の確定した着順を、通知が有効なグループへ送る。"""
    result = NotifyResult()
    ymd = day.strftime("%Y%m%d")

    for group in session.execute(_GROUPS_SQL).mappings():
        settings = group["notification_settings"] or {}
        if settings.get("result_notification") is False:
            continue
        result.groups += 1

        rows = [dict(r) for r in session.execute(
            _RESULTS_SQL, {"group_id": group["id"], "ymd": ymd}
        ).mappings()]
        result.candidates += len(rows)
        if not rows:
            continue

        unsent = []
        for r in rows:
            sent = session.execute(_SENT_SQL, {
                "group_id": group["id"], "race_date": day,
                "course_code": r["course_code"], "race_no": r["race_no"],
                "horse_name": r["horse_name"],
            }).first()
            if sent:
                result.already += 1
            else:
                unsent.append(r)
        if not unsent:
            continue

        logger.info("%s(%d年): 未送信 %d 件", group["name"], group["year"], len(unsent))
        if dry_run:
            for r in unsent:
                logger.info("  [dry-run] %s %sR %s %s着", r["course_name"],
                            r["race_no"], r["horse_name"], r["finish_position"])
            continue

        if not send_discord(group["discord_webhook_url"],
                            build_embed(group["name"] or "POG", day, unsent)):
            result.failed += len(unsent)
            continue

        # 🔴 送信できたぶんだけ記録する。ここで落ちても二重送信にはならない
        #    （一意インデックスが弾く）。
        for r in unsent:
            session.execute(_RECORD_SQL, {
                "group_id": group["id"], "race_date": day,
                "course_code": r["course_code"], "race_no": r["race_no"],
                "horse_name": r["horse_name"],
                "content": f"{r['horse_name']} {r['finish_position']}着",
            })
        session.commit()
        result.sent += len(unsent)

    return result


def build_entry_embed(group_name: str, day: date, rows: list[dict]) -> dict:
    """出走想定（🏇 青）の embed。移設元 `notify_pog_group_entries` を踏襲。"""
    by_course: dict[str, list[dict]] = {}
    for r in rows:
        by_course.setdefault(r["course_name"], []).append(r)

    fields = []
    for course_name, items in by_course.items():
        lines = []
        for r in items:
            barrier = f" ({r['barrier']}枠)" if r.get("barrier") else ""
            jockey = f" {r['jockey']}" if r.get("jockey") else ""
            owner = f" **({r['owner_name']})**" if r.get("owner_name") else ""
            lines.append(f"**{r['race_no']}R** {r['horse_name']}{barrier}{jockey}{owner}")
        fields.append({"name": f"📍 {course_name}", "value": "\n".join(lines),
                       "inline": False})

    return {
        "title": f"🏇 {group_name} POG馬出走想定",
        "description": (f"📅 **{day.strftime('%Y年%m月%d日')}**\n\n"
                        f"計**{len(rows)}頭**が出走予定です！"),
        "color": 0x3498DB,
        "timestamp": datetime.now(UTC).isoformat(),
        "fields": fields,
    }


def build_barrier_embed(group_name: str, day: date, rows: list[dict]) -> dict:
    """枠順確定（🎯 赤）の embed。移設元 `notify_pog_group_barrier_draws` を踏襲。"""
    by_course: dict[str, list[dict]] = {}
    for r in rows:
        by_course.setdefault(r["course_name"], []).append(r)

    fields = []
    for course_name, items in by_course.items():
        lines = []
        for r in items:
            owner = f" **({r['owner_name']})**" if r.get("owner_name") else ""
            lines.append(f"**{r['race_no']}R** {r['horse_name']}{owner}")
            lines.append(
                f"　　{r.get('barrier') or '未定'}枠{r.get('horse_no') or '未定'}番　 "
                f"{r.get('jockey') or '未定'}　 {r.get('weight') or '未定'}kg"
            )
        fields.append({"name": f"📍 {course_name}", "value": "\n".join(lines),
                       "inline": False})

    return {
        "title": f"🎯 {group_name} POG馬枠順確定",
        "description": f"📅 **{day.strftime('%Y年%m月%d日')}**\n\n枠順が確定しました！",
        "color": 0xE74C3C,
        "timestamp": datetime.now(UTC).isoformat(),
        "fields": fields,
    }


def notify_day(
    session: Session, day: date, kind: str, *, dry_run: bool = False
) -> NotifyResult:
    """その日ぶんをまとめて 1 通送る通知（`entry` / `barrier`）。

    Args:
        kind: `entry`（出走想定）または `barrier`（枠順確定）。

    🔴 result と違い**日付単位で 1 通**なので、重複判定も日付単位。
    一度送った後に頭数が増えても送り直さない（移設元と同じ）。
    """
    if kind not in ("entry", "barrier"):
        raise ValueError(f"未知の通知種別: {kind}")

    result = NotifyResult()
    ymd = day.strftime("%Y%m%d")
    sql = _PROJECTED_SQL if kind == "entry" else _BARRIER_SQL
    build = build_entry_embed if kind == "entry" else build_barrier_embed
    setting_key = f"{kind}_notification"

    for group in session.execute(_GROUPS_SQL).mappings():
        settings = group["notification_settings"] or {}
        if settings.get(setting_key) is False:
            continue
        result.groups += 1

        if session.execute(_SENT_DAY_SQL, {
            "group_id": group["id"], "kind": kind, "race_date": day,
        }).first():
            result.already += 1
            continue

        rows = [dict(r) for r in session.execute(
            sql, {"group_id": group["id"], "ymd": ymd}
        ).mappings()]
        result.candidates += len(rows)
        if not rows:
            continue

        logging_name = group["name"] or "POG"
        logger.info("%s(%d年) %s: %d 件", logging_name, group["year"], kind, len(rows))
        if dry_run:
            for r in rows:
                logger.info("  [dry-run] %s %sR %s", r["course_name"], r["race_no"],
                            r["horse_name"])
            continue

        if not send_discord(group["discord_webhook_url"], build(logging_name, day, rows)):
            result.failed += 1
            continue

        label = "出走確定" if kind == "entry" else "枠順確定"
        session.execute(_RECORD_DAY_SQL, {
            "group_id": group["id"], "kind": kind, "race_date": day,
            "content": f"{len(rows)}頭の{label}通知",
        })
        session.commit()
        result.sent += 1

    return result
