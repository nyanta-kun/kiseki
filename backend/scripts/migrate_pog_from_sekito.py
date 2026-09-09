#!/usr/bin/env python3
"""POG のグループ・参加者・指名を sekito から keiba へ引き継ぐ（統合 Phase 5 の 5d-2）。

`sekito.pog_group`(22) / `sekito.pog_user`(1,401・2005〜2026 の22年分) を
#532 で作った `keiba.pog_groups` / `pog_group_members` / `pog_picks` へ写す。

## 変換の要点

| | 移設元 | ここ |
|---|---|---|
| メンバー | `pog_group.user_ids` = **text にカンマ区切り**（`"3,4,6,8"`） | `pog_group_members` へ展開 |
| 指名順 | `pog_user.order`（SQL の予約語） | `pick_order` |
| 利用者 | `sekito.users.id` | **email で突き合わせた `keiba.users.id`** |

利用者の対応は 2026-09-09 の事前登録で作ってある（10人・全員一致）。
`sekito.users` と `keiba.users` は**元々ほとんど重なっていなかった**（共通は1人）ので、
9人を新規登録した上での突き合わせになる。

## 🔴 やり直しは「全消し → 入れ直し」

`sekito.pog_user` に**行の識別子が無い**（`id` 列が無く、`(group_id, uid, order)` も
`order=0` の 10 行が重複する）ので、行単位の UPSERT ができない。

そのため、既に `keiba.pog_picks` に行がある場合は `--force` を要求し、
指定されたら**対象グループの指名を全部消してから入れ直す**。

⚠️ **kiseki 側で指名を書き始めた後にこれを流してはいけない。**
一度きりの引っ越し用。sekito を落とすときに一緒に消してよい。

## 使い方

    # 件数だけ見る（既定）
    docker exec -w /app galloplab-backend-1 /app/.venv/bin/python \
        scripts/migrate_pog_from_sekito.py

    # 実際に書く / 既にデータがある状態でやり直す
    ... scripts/migrate_pog_from_sekito.py --apply
    ... scripts/migrate_pog_from_sekito.py --apply --force

終了コード: 0 = 正常 / 1 = 対応の付かない利用者が居る（引き継ぎは中止）。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy import text

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.db.session import SyncSessionLocal  # noqa: E402

# sekito.users.id → keiba.users.id。email（小文字）で突き合わせる。
USER_MAP_SQL = text(
    """
    SELECT su.id AS sekito_id, su.nickname, ku.id AS keiba_id
    FROM sekito.users su
    LEFT JOIN keiba.users ku ON lower(ku.email) = lower(su.email)
    ORDER BY su.id
    """
)

GROUPS_SQL = text(
    """
    SELECT id, year, name, user_ids,
           notification_platform, discord_webhook_url,
           coalesce(discord_messaging_enabled, false) AS discord_enabled,
           line_group_id, coalesce(line_messaging_enabled, false) AS line_enabled,
           notification_settings
    FROM sekito.pog_group
    WHERE year IS NOT NULL
    ORDER BY year
    """
)

PICKS_SQL = text(
    """
    SELECT g.year, pu.uid, pu.netkeiba_horse_id, pu."order" AS pick_order,
           pu.draft_order, coalesce(pu.visible, true) AS visible
    FROM sekito.pog_user pu
    JOIN sekito.pog_group g ON g.id = pu.group_id
    ORDER BY g.year, pu.uid, pu."order"
    """
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="POG のグループ・指名を sekito から keiba へ引き継ぐ（一度きり）"
    )
    parser.add_argument("--apply", action="store_true", help="実際に書き込む")
    parser.add_argument("--force", action="store_true",
                        help="既に keiba.pog_picks に行があっても、全消しして入れ直す")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout)

    with SyncSessionLocal() as session:
        # ---- 利用者の対応 ----
        user_map: dict[int, int] = {}
        missing: list[str] = []
        for r in session.execute(USER_MAP_SQL).mappings():
            if r["keiba_id"] is None:
                missing.append(f"{r['sekito_id']}:{r['nickname']}")
            else:
                user_map[r["sekito_id"]] = r["keiba_id"]
        logging.info("利用者の対応: %d 人", len(user_map))
        if missing:
            # 🔴 対応が付かない人が居ると指名が落ちる。**部分的に入れない。**
            logging.error("keiba.users に対応が無い利用者: %s", ", ".join(missing))
            logging.error("先に scripts/preregister_pog_users.py で登録すること")
            return 1

        groups = [dict(r) for r in session.execute(GROUPS_SQL).mappings()]
        picks = [dict(r) for r in session.execute(PICKS_SQL).mappings()]
        existing = session.execute(
            text("SELECT count(*) FROM keiba.pog_picks")
        ).scalar_one()

        members: list[tuple[int, int]] = []  # (year, keiba_user_id)
        unknown_member = 0
        for g in groups:
            for raw in (g["user_ids"] or "").split(","):
                raw = raw.strip()
                if not raw.isdigit():
                    continue
                kid = user_map.get(int(raw))
                if kid is None:
                    unknown_member += 1
                    continue
                members.append((g["year"], kid))

        logging.info("グループ %d / メンバー %d 件 / 指名 %d 件",
                     len(groups), len(members), len(picks))
        if unknown_member:
            logging.warning("user_ids に対応の付かない id が %d 件（読み飛ばす）",
                            unknown_member)
        logging.info("keiba.pog_picks の現在: %d 行", existing)

        if not args.apply:
            logging.info("--apply が無いので書き込みません")
            return 0
        if existing and not args.force:
            logging.error("既に %d 行あります。やり直すなら --force を付けること", existing)
            return 1

        # ---- グループ ----
        for g in groups:
            session.execute(
                text(
                    """
                    INSERT INTO keiba.pog_groups
                        (year, name, notification_platform, discord_webhook_url,
                         discord_enabled, line_group_id, line_enabled,
                         notification_settings)
                    VALUES (:year, :name, :notification_platform,
                            :discord_webhook_url, :discord_enabled,
                            :line_group_id, :line_enabled,
                            :notification_settings)
                    ON CONFLICT (year) DO UPDATE SET
                        name = EXCLUDED.name,
                        notification_platform = EXCLUDED.notification_platform,
                        discord_webhook_url = EXCLUDED.discord_webhook_url,
                        discord_enabled = EXCLUDED.discord_enabled,
                        line_group_id = EXCLUDED.line_group_id,
                        line_enabled = EXCLUDED.line_enabled,
                        notification_settings = EXCLUDED.notification_settings,
                        updated_at = now()
                    """
                ),
                {k: v for k, v in g.items() if k not in ("id", "user_ids")},
            )
        session.flush()
        year_to_id = {
            r[0]: r[1]
            for r in session.execute(text("SELECT year, id FROM keiba.pog_groups"))
        }

        # ---- メンバー ----
        for year, kid in members:
            session.execute(
                text(
                    "INSERT INTO keiba.pog_group_members (group_id, user_id) "
                    "VALUES (:g, :u) ON CONFLICT DO NOTHING"
                ),
                {"g": year_to_id[year], "u": kid},
            )

        # ---- 指名（全消し → 入れ直し。行の識別子が無いため）----
        if existing:
            n = session.execute(text("DELETE FROM keiba.pog_picks")).rowcount
            logging.info("既存の指名 %d 行を削除", n)
        inserted = 0
        for p in picks:
            session.execute(
                text(
                    """
                    INSERT INTO keiba.pog_picks
                        (group_id, user_id, netkeiba_horse_id, pick_order,
                         draft_order, visible)
                    VALUES (:g, :u, :h, :o, :d, :v)
                    """
                ),
                {
                    "g": year_to_id[p["year"]],
                    "u": user_map[p["uid"]],
                    "h": p["netkeiba_horse_id"],
                    "o": p["pick_order"],
                    "d": p["draft_order"],
                    "v": p["visible"],
                },
            )
            inserted += 1
        session.commit()

        # ---- 突き合わせ ----
        got = session.execute(text("SELECT count(*) FROM keiba.pog_picks")).scalar_one()
        gm = session.execute(
            text("SELECT count(*) FROM keiba.pog_group_members")
        ).scalar_one()
        gg = session.execute(text("SELECT count(*) FROM keiba.pog_groups")).scalar_one()
        logging.info("投入: グループ %d / メンバー %d / 指名 %d", gg, gm, got)
        if got != len(picks):
            logging.error("指名の件数が合いません（元 %d / 先 %d）", len(picks), got)
            return 1
        # 馬マスタに無い指名（空指名は除く）
        orphan = session.execute(
            text(
                "SELECT count(*) FROM keiba.pog_picks p "
                "LEFT JOIN keiba.pog_horses h "
                "       ON h.netkeiba_horse_id = p.netkeiba_horse_id "
                "WHERE p.netkeiba_horse_id IS NOT NULL AND h.id IS NULL"
            )
        ).scalar_one()
        logging.info("馬マスタに無い指名: %d 件", orphan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
