#!/usr/bin/env python3
"""POG の参加者を `keiba.users` へ事前登録し、対応表を出す（統合 Phase 5 の 5c-4）。

## なぜ要るか

Phase 4 の決定は **A. Google に一本化**（2026-09-08）。kiseki の
`POST /api/users/upsert`（Auth.js のログイン時に呼ばれる）は
**email が事前登録されていなければ 404 で拒否する**ホワイトリスト方式なので、
POG の参加者は先に `keiba.users` へ入れておかないとログインできない。

    事前登録あり・google_sub NULL   → 初回ログインで google_sub を確定して紐付け
    事前登録なし                    → 404「このメールアドレスは登録されていません」

## 🔴 利用者はほぼ重なっていない

2026-09-08 実測で、`sekito.users`(10) と `keiba.users`(11) の共通は
`yuichiszk@gmail.com` **1 人だけ**。残る 9 人は新規登録になる。

## 🔴 メールが怪しい行は登録しない

    kuroki@sekito-stable.com    独自ドメイン。Google アカウントとして実在するか要確認
    ida@gmail-stable.com        `gmail.com` の打ち間違いに見える

どちらも sekito 側で Firebase uid ではなく手書き id（`sekito-kuroki` /
`sekito-ida`）で作られている。**実在が確認できないメールを勝手に登録しない**
（ホワイトリストは「ログインしてよい人」の一覧なので、間違ったアドレスを
入れるとその宛先の持ち主に GallopLab が開く）。

🔴 **2026-09-08 のユーザー回答: この 2 人はダミーで、本人のアクセス予定は無い。**
そこで `--dummy` を付けると **`is_active=false`** で登録する。
POG の表示と対応表のためだけの行になる。

`is_active=false` が効く場所は**画面**。`/api/users/upsert` 自体は拒否しないが、
`auth.ts` が `token.is_active` に載せ、`proxy.ts` が `/login?error=account_suspended`
へ戻す（無効化済みアカウントと同じ扱い）。**バックエンド API は元々無認証**なので、
これは「UI を開かせない」ためのゲートであって秘密を守る仕組みではない。

⚠️ **ダミーを `@gmail.com` に「直して」はいけない。** `ida@gmail.com` のような
アドレスは**実在する他人の Google アカウント**でありうる。ホワイトリストに
入れた瞬間その持ち主に GallopLab が開く。ドメインが存在しないままの方が安全。

実在するアカウントを承知の上で入れる場合だけ `--allow-email` を使う。

## 対応表

`sekito.pog_user.uid` は `sekito.users.id` を指す（`users.uid` は Firebase の
UID で認証にしか使わない）。POG のテーブルを keiba へ移すときに
`sekito.users.id → keiba.users.id` の対応が要るので、ここで出しておく。

## 使い方

    # 対応表と登録予定を見るだけ（既定）
    docker exec -w /app galloplab-backend-1 /app/.venv/bin/python \
        scripts/preregister_pog_users.py

    # 実際に登録する（保留 2 人はダミーとして is_active=false で入れる）
    ... scripts/preregister_pog_users.py --apply --dummy
    # 実在するアカウントだと確認できたメールを通常登録する
    ... scripts/preregister_pog_users.py --apply --allow-email kuroki@example.com
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

from sqlalchemy import text

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.db.session import SyncSessionLocal  # noqa: E402

# 「実在が確認できない」と判断する形。gmail.com の打ち間違いに見えるもの、
# および POG 側の独自ドメイン。ここに当たったら既定では登録しない。
_SUSPICIOUS = (
    re.compile(r"@gmail-", re.I),        # gmail-stable.com など
    re.compile(r"@sekito-stable\.com$", re.I),
)

SELECT_SQL = text(
    """
    SELECT su.id AS sekito_id, su.nickname, su.email, su.status, su.is_admin,
           ku.id AS keiba_id, ku.role, ku.google_sub IS NOT NULL AS logged_in
    FROM sekito.users su
    LEFT JOIN keiba.users ku ON lower(ku.email) = lower(su.email)
    ORDER BY su.id
    """
)


def is_suspicious(email: str) -> bool:
    return any(p.search(email) for p in _SUSPICIOUS)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="POG 参加者を keiba.users へ事前登録し、対応表を出す"
    )
    parser.add_argument("--apply", action="store_true", help="実際に登録する")
    parser.add_argument("--allow-email", action="append", default=[],
                        help="実在を確認できたメールを通常登録する（複数指定可）")
    parser.add_argument("--dummy", action="store_true",
                        help="保留になったメールを is_active=false（ログイン不可）で登録する")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout)
    allow = {e.lower() for e in args.allow_email}

    with SyncSessionLocal() as session:
        rows = [dict(r) for r in session.execute(SELECT_SQL).mappings()]

        print()
        print("  sekito.id  ニックネーム  keiba.id  状態")
        print("  ---------  ------------  --------  ----")
        to_create: list[dict] = []
        for r in rows:
            email = (r["email"] or "").strip()
            if r["keiba_id"]:
                state = "登録済み" + ("・ログイン実績あり" if r["logged_in"] else "・未ログイン")
            elif not email:
                state = "スキップ（メール無し）"
            elif is_suspicious(email) and email.lower() not in allow:
                if args.dummy:
                    state = "ダミー登録（ログイン不可）"
                    to_create.append({"email": email, "nickname": r["nickname"],
                                      "is_active": False})
                else:
                    state = "🔴 保留（メールの実在が要確認）"
            else:
                state = "新規登録する"
                to_create.append({"email": email, "nickname": r["nickname"],
                                  "is_active": True})
            print(f"  {r['sekito_id']:>9}  {r['nickname'] or '':<12}  "
                  f"{str(r['keiba_id'] or '-'):>8}  {state}  {email}")
        print()

        logging.info("新規登録の対象: %d 人", len(to_create))
        if not args.apply:
            logging.info("--apply が無いので登録しません")
            return 0

        for c in to_create:
            session.execute(
                text(
                    "INSERT INTO keiba.users (email, role, is_active) "
                    "VALUES (:email, 'member', :is_active) "
                    "ON CONFLICT (email) DO NOTHING"
                ),
                c,
            )
            logging.info(
                "登録: %s（%s）%s",
                c["email"], c["nickname"],
                "" if c["is_active"] else " ← ログイン不可",
            )
        session.commit()

        after = [dict(r) for r in session.execute(SELECT_SQL).mappings()]
        mapped = sum(1 for r in after if r["keiba_id"])
        logging.info("対応が付いた人数: %d / %d", mapped, len(after))
        if mapped < len(after):
            logging.warning(
                "%d 人がまだ対応していません（メールの確認待ち）", len(after) - mapped
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
