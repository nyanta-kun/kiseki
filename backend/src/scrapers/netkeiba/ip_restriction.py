"""netkeiba の IP 制限の検出と門番。

🔴 **この移設でいちばん重要なモジュール。**

sekito では `scheduler.js` の `checkIpRestriction()` が、script_name に
`netkeiba` / `odds` を含むジョブを**実行の直前に**弾いていた:

    SELECT value FROM sekito.system_settings
    WHERE key LIKE 'netkeiba_ip_restricted%' AND value = 'true' LIMIT 1

kiseki は cron 起動なので、この門番が居ない。**ゲートを持たずにスクレイパだけ
移すと、IP 制限を食らっている最中も叩き続けて制限を伸ばす。**
ここでその門番を kiseki 側に持つ。

## 読み方: どれか 1 つでも true なら制限中（fail closed）

キーは環境ごとに分かれている（`netkeiba_ip_restricted_server` /
`_local` / 接尾辞なしの旧キー）。sekito のゲートと同じく **LIKE で全部見て、
1 つでも true なら制限中**とみなす。

    誤検知（古い `_local` が true のまま）→ しばらく取りに行かない。戻せる。
    見逃し（自分の環境のキーだけ見て false）→ IP 制限を食らって伸ばす。戻せない。

後者の方がはるかに高くつくので **fail closed** にしてある。この設計のおかげで、
`SCRAPER_ENVIRONMENT_ID` の設定を忘れても**安全側にしか倒れない**。

## 書き方: `scheduler_enabled` には触らない

移設元の `mark_ip_restricted()` は `sekito.system_settings.scheduler_enabled`
を false にして sekito のスケジューラ全体を止めていた。**移植版はこれをやらない。**

理由（2026-09-06 に `scheduler.js` を読んで確認した罠）:
`runJob()` は `isSchedulerEnabled` を見ておらず、`scheduler_enabled` が効くのは
**次の `loadSchedules()`（＝再起動か管理 API のリロード）だけ**。
つまり自動停止した状態でコンテナが再起動すると `loadSchedules()` が何も読まず、
**復旧役の `check-ip-restriction`(id=34) 自身も起動しなくなり、自力で戻れなくなる。**

`netkeiba_ip_restricted_*` を立てるだけで、sekito 側の netkeiba ジョブは
`checkIpRestriction()` で弾かれ、kiseki 側もこのモジュールで弾かれる。
全体を止める必要はない。

## 復旧

`try_recover()` が netkeiba へ 1 回だけアクセスして、通れば制限フラグを落とす。
毎時 cron から呼ぶ（sekito `bin/maintenance/check-ip-restriction` の移設先）。

🔴 **復旧のとき `scheduler_enabled` を true に戻す。** 移植版は自分では
false にしないが、過去に sekito 側が落とした値が残っていることがある。
その状態で sekito のコンテナが再起動すると `loadSchedules()` が何も読まず、
**スケジューラごと止まって自力で戻れなくなる**（上記の罠）。復旧の機会に
直しておく。
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

from ...utils import discord
from .decode import decode_page

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")

# IP 制限とみなすエラー文言。
# ⚠️ 移設元のコメントのとおり、**確実に IP 制限を示すものだけ**を入れる。
#    「jQuery 読み込みタイムアウト」のような、ページ遅延やレース未確定でも
#    起きるものを入れると誤検知でスクレイプが止まる。
IP_RESTRICTION_PATTERNS: tuple[str, ...] = (
    "HTTP 400 (IPブロックまたはBot検出の可能性)",
    "HTTP 403",
    "Access Denied",
    "Forbidden",
)

# `system_settings` のキー。接尾辞に環境 ID が付く（例: netkeiba_ip_restricted_server）。
# 読むときは接頭辞一致で全環境ぶんを見る（sekito の scheduler.js と同じ）。
CHECK_URL = "https://race.netkeiba.com/top/"

_RESTRICTED_KEY_PREFIX = "netkeiba_ip_restricted"
_DETECTED_AT_KEY_PREFIX = "netkeiba_ip_restriction_detected_at"


class IPRestricted(RuntimeError):
    """IP 制限中に取得を試みようとしたときに送出する。"""


def looks_like_ip_restriction(error_message: str | None) -> bool:
    """エラー文言が IP 制限を示しているか。"""
    if not error_message:
        return False
    lowered = error_message.lower()
    return any(p.lower() in lowered for p in IP_RESTRICTION_PATTERNS)


def restricted_keys(session: Session) -> list[str]:
    """いま true になっている制限キーを返す。空なら制限なし。

    どのキーで止まっているかを返すのは、古い `_local` が残って止まっている
    ような場合に**原因のキー名がログに出る**ようにするため。
    """
    rows = session.execute(
        text(
            "SELECT key FROM sekito.system_settings "
            "WHERE key LIKE :prefix AND value = 'true' ORDER BY key"
        ),
        {"prefix": f"{_RESTRICTED_KEY_PREFIX}%"},
    ).all()
    return [r[0] for r in rows]


def is_restricted(session: Session) -> bool:
    """IP 制限中か。1 つでも true のキーがあれば True。"""
    return bool(restricted_keys(session))


def require_not_restricted(session: Session, *, context: str = "netkeiba") -> None:
    """制限中なら `IPRestricted` を送出する。

    スクレイパの**開始時**と、長いジョブでは**途中でも**呼ぶこと。
    移設元のメモにあるとおり、sekito のゲートはジョブ起動時にしか効かないため、
    走っている最中に制限を食らうと最後まで叩き続けてしまう。

    Args:
        session: 同期 DB セッション。
        context: ログに出す呼び出し元の名前。

    Raises:
        IPRestricted: 制限中のとき。
    """
    keys = restricted_keys(session)
    if keys:
        raise IPRestricted(
            f"netkeiba の IP 制限中のため {context} をスキップします "
            f"(system_settings: {', '.join(keys)})"
        )


def mark_restricted(
    session: Session,
    *,
    source: str = "netkeiba",
    environment_id: str = "local",
    notify: bool = True,
) -> bool:
    """IP 制限を記録する。

    既に記録済みなら何もしない（通知の連打を防ぐ。移設元と同じ判断）。

    🔴 `scheduler_enabled` には触らない。理由はモジュールの docstring を参照。

    Args:
        session: 同期 DB セッション。
        source: 検出元（ログと通知に出る）。
        environment_id: 書き込むキーの接尾辞。`SCRAPER_ENVIRONMENT_ID` 相当。
            **間違っていても安全側にしか倒れない** — 読む側は接頭辞一致で
            全キーを見るので、どの接尾辞で書いても両システムのゲートが効く。
        notify: Discord へ通知するか（`utils.discord.send` を使う）。

    Returns:
        新たに記録したら True、既に記録済みなら False。
    """
    key = f"{_RESTRICTED_KEY_PREFIX}_{environment_id}"
    detected_at_key = f"{_DETECTED_AT_KEY_PREFIX}_{environment_id}"

    already = session.execute(
        text("SELECT value FROM sekito.system_settings WHERE key = :k"), {"k": key}
    ).scalar()
    if already == "true":
        logger.info("[%s] IP 制限は既に記録済みです。通知をスキップします。", environment_id)
        return False

    now = datetime.now(JST)
    session.execute(
        text(
            "INSERT INTO sekito.system_settings (key, value, description, updated_at) "
            "VALUES (:k, 'true', :d, CURRENT_TIMESTAMP) "
            "ON CONFLICT (key) DO UPDATE SET value = 'true', updated_at = CURRENT_TIMESTAMP"
        ),
        {"k": key, "d": f"NetKeiba IP制限状態 (環境: {environment_id})"},
    )
    session.execute(
        text(
            "INSERT INTO sekito.system_settings (key, value, description, updated_at) "
            "VALUES (:k, :v, :d, CURRENT_TIMESTAMP) "
            "ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = CURRENT_TIMESTAMP"
        ),
        {"k": detected_at_key, "v": now.isoformat(),
         "d": f"IP制限検出時刻 (環境: {environment_id})"},
    )
    session.commit()

    logger.critical("🚨 [%s] %s で IP 制限を検出しました (%s)。netkeiba ジョブを停止します。",
                    environment_id, source, now.strftime("%Y-%m-%d %H:%M:%S"))

    if notify:
        discord.send(
            "🚨 **NetKeiba IP制限検出**\n\n"
            f"**発生源**: {source}（kiseki）\n"
            f"**実行環境**: {environment_id}\n"
            f"**検出時刻**: {now.strftime('%Y-%m-%d %H:%M:%S')} JST\n\n"
            "⚠️ netkeiba 系ジョブは kiseki・sekito とも自動でスキップされます\n"
            "- sekito の `check-ip-restriction`（毎時）が解除を検知して復旧します\n"
            "- スケジューラ全体は止めていません（復旧ジョブを生かすため）\n\n"
            "NetKeiba へのアクセスを控え、解除されるまでお待ちください。"
        )
    return True


# 本文にこれが出ていたら、HTTP 200 でも制限中とみなす。
# ⚠️ 誤検知しても**安全側**（制限中のままなので取りに行かない）に倒れるだけ。
#    逆に見逃すと制限中に叩き続けるので、緩めにしてある。
_BLOCKED_PAGE_MARKERS = (
    "アクセスが制限", "Access Denied", "403 Forbidden", "400 Bad Request",
    "IPアドレス", "Bot", "ロボット", "自動アクセス",
)

# これより短い本文は、まともなページが返っていない。
_MIN_PAGE_BYTES = 100


def can_access(*, timeout: float = 15.0) -> bool:
    """netkeiba に通れるか、1 リクエストだけ試す。

    ⚠️ この 1 回は**レートリミッタを通さない**。毎時 1 回だけ、しかも制限中に
    しか呼ばれないので、間隔を空ける意味がない。

    Returns:
        通れれば True。制限中・異常なら False。
    """
    from .session import random_user_agent

    try:
        response = requests.get(
            CHECK_URL, headers={"User-Agent": random_user_agent()}, timeout=timeout
        )
    except requests.Timeout:
        logger.warning("netkeiba アクセス確認: タイムアウト")
        return False
    except Exception as e:
        logger.error("netkeiba アクセス確認でエラー: %s", e)
        return False

    if response.status_code in (400, 403):
        logger.warning("netkeiba アクセス確認: HTTP %d（IP 制限または Bot 検出）",
                       response.status_code)
        return False

    body = decode_page(response)
    if len(body) < _MIN_PAGE_BYTES:
        logger.warning("netkeiba アクセス確認: 本文が短すぎる（%d 文字）", len(body))
        return False

    hit = next((m for m in _BLOCKED_PAGE_MARKERS if m in body), None)
    if hit:
        logger.warning("netkeiba アクセス確認: 制限を示す文言があります（%r）", hit)
        return False

    logger.info("netkeiba アクセス確認: 正常（status=%d, %d 文字）",
                response.status_code, len(body))
    return True


def try_recover(session: Session, *, notify: bool = True) -> bool:
    """制限中なら解除を確認し、通れるならフラグを落とす。

    Returns:
        復旧したら True。制限中でない・まだ通れないなら False。
    """
    keys = restricted_keys(session)
    if not keys:
        logger.debug("IP 制限中ではありません")
        _ensure_scheduler_enabled(session)
        return False

    logger.info("IP 制限の解除を確認します（立っているキー: %s）", ", ".join(keys))
    if not can_access():
        logger.warning("まだ制限中です。解除を待ちます")
        return False

    session.execute(
        text(
            "UPDATE sekito.system_settings SET value = 'false', updated_at = CURRENT_TIMESTAMP "
            "WHERE key LIKE :prefix AND value = 'true'"
        ),
        {"prefix": f"{_RESTRICTED_KEY_PREFIX}%"},
    )
    _ensure_scheduler_enabled(session)
    session.commit()

    logger.info("✅ IP 制限が解除されました（落としたキー: %s）", ", ".join(keys))
    if notify:
        discord.send(
            "✅ **NetKeiba IP制限が解除されました**\n\n"
            f"**解除時刻**: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')} JST\n"
            f"**落としたフラグ**: {', '.join(keys)}\n\n"
            "netkeiba 系ジョブは次回の起動から通常どおり動きます。"
        )
    return True


def _ensure_scheduler_enabled(session: Session) -> None:
    """`scheduler_enabled` が false なら true に戻す。

    🔴 移植版は自分では false にしないが、過去に sekito 側が落とした値が
    残っていることがある。`scheduler.js` の `runJob()` はこの値を見ないので
    普段は無害だが、**その状態で sekito のコンテナが再起動すると
    `loadSchedules()` が何も読まず、復旧ジョブごと止まって自力で戻れなくなる。**
    復旧の機会に直しておく。
    """
    result = session.execute(
        text(
            "UPDATE sekito.system_settings SET value = 'true', updated_at = CURRENT_TIMESTAMP "
            "WHERE key = 'scheduler_enabled' AND value <> 'true'"
        )
    )
    if getattr(result, "rowcount", 0):
        logger.warning("scheduler_enabled が false でした。true に戻しました "
                       "（この状態で sekito が再起動すると全ジョブが止まります）")
