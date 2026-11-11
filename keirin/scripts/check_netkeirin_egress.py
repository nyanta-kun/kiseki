#!/usr/bin/env python3
"""netkeirin 入稿ツールへ届くかを確かめ、状態が変わったときだけ知らせる（2026-11-11 新設）。

    python scripts/check_netkeirin_egress.py              # cron（10分ごと）
    python scripts/check_netkeirin_egress.py --no-notify  # 手で確かめる（結果を出すだけ）

## なぜ要るか

2026-11-17 目安で入稿ツールが Cloudflare Zero Trust の内側へ入り、VPS からの
通信は Mac mini の逆向き SOCKS トンネル（`NETKEIRIN_PROXY_URL`）経由になる。
入稿は 07:00 / 13:00 / 18:00 の3回しか走らないので、夜中にトンネルが落ちたり
Cloudflare の本人確認が切れたりしても、**次の入稿で全件失敗するまで誰も気づかない**。
先に知らせて、朝までにメールコードの入力やトンネルの復旧を済ませられるようにする。
設計: `docs/netkeirin_macmini_egress_plan_2026_11.md` §5。

## 判定（`NetkeirinClient.preflight`）

| 状態 | 扱い |
|---|---|
| ok / needs_login | 正常（netkeirin のセッション切れは入稿時に通常ログインで直る） |
| cf_auth / egress_down / blocked | すぐ知らせる（人が手を入れるまで直らない） |
| transient | 3回続いたら知らせる（一時的な揺れで鳴らさない） |

- 知らせるのは**状態が変わったとき**と、異常が続く間 `--realert-hours` ごと
- 🔴 **ログインしない・Cookie を書かない。** 入稿バッチのセッションに触らない
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.netkeirin_client import (  # noqa: E402
    DATA_DIR,
    PREFLIGHT_FATAL,
    PREFLIGHT_NEEDS_LOGIN,
    PREFLIGHT_OK,
    PREFLIGHT_TRANSIENT,
    NetkeirinClient,
    PreflightResult,
)

STATE_FILE = DATA_DIR / "netkeirin_egress_state.json"
HEALTHY = frozenset({PREFLIGHT_OK, PREFLIGHT_NEEDS_LOGIN})
TRANSIENT_ALERT_STREAK = 3

_HINT = {
    "cf_auth": "Mac mini の Cloudflare One Client でメールの6桁コードを入力し直してください。",
    "egress_down": ("Mac mini の電源・ネットワークと "
                    "`launchctl list com.kiseki.netkeirin-egress` を確認してください。"),
    "blocked": "入稿ツールをブラウザで開いて状態を確認してください。",
    "transient": "入稿ツールの応答が続けて不安定です。",
}


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def decide(prev: dict, res: PreflightResult, now: datetime,
           realert_hours: float) -> tuple[dict, str | None]:
    """前回の状態と今回の結果から、次の状態と送る通知文（無ければ None）を決める。

    純関数（テスト用）。`alerting` は「異常を知らせて、まだ回復を知らせていない」。
    """
    streak = int(prev.get("streak", 0)) + 1 if prev.get("status") == res.status else 1
    alerting = bool(prev.get("alerting"))
    last_alert = prev.get("last_alert_at")
    state = {"status": res.status, "detail": res.detail, "streak": streak,
             "checked_at": now.isoformat(timespec="seconds"),
             "alerting": alerting, "last_alert_at": last_alert}

    if res.status in HEALTHY:
        if alerting:
            state.update(alerting=False, last_alert_at=None)
            return state, f"✅ **netkeirin 入稿ツールに再び届きます**（{res.detail}）"
        return state, None

    bad = res.status in PREFLIGHT_FATAL or (
        res.status == PREFLIGHT_TRANSIENT and streak >= TRANSIENT_ALERT_STREAK)
    if not bad:
        return state, None

    changed = prev.get("status") != res.status
    due = True
    if alerting and not changed and last_alert:
        try:
            due = now - datetime.fromisoformat(last_alert) >= timedelta(hours=realert_hours)
        except ValueError:
            due = True
    if alerting and not changed and not due:
        return state, None
    state.update(alerting=True, last_alert_at=now.isoformat(timespec="seconds"))
    again = "（継続中）" if alerting and not changed else ""
    msg = (f"🚨 **netkeirin 入稿ツールに入れません{again}**: {res.status}\n"
           f"理由: {res.detail}\n{_HINT.get(res.status, '')}\n"
           "次の入稿（07:00 / 13:00 / 18:00）までに直らなければ、その回は入稿を止めて"
           "未入稿の一覧を知らせます。")
    return state, msg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-notify", action="store_true",
                    help="Discord に送らず、状態ファイルも書かない（手で確かめる用）")
    ap.add_argument("--realert-hours", type=float, default=3.0)
    a = ap.parse_args()

    res = NetkeirinClient(propose_only=False).preflight(retries=2)
    now = datetime.now()
    print(f"[netkeirin_egress] {now:%F %T} {res.status} {res.detail}", flush=True)
    if a.no_notify:
        return 0 if res.status in HEALTHY else 1

    prev = _load_state(STATE_FILE)
    state, msg = decide(prev, res, now, a.realert_hours)
    if msg:
        try:
            from src.notify.discord import send
            send(msg, channel="system")
        except Exception as e:                               # noqa: BLE001
            print(f"[netkeirin_egress] Discord通知失敗: {e!r}", flush=True)
            # 送れなかった通知は次回もう一度試す（状態を「知らせた」にしない）
            state["alerting"] = bool(prev.get("alerting"))
            state["last_alert_at"] = None
    _save_state(STATE_FILE, state)
    return 0 if res.status in HEALTHY else 1


if __name__ == "__main__":
    sys.exit(main())
