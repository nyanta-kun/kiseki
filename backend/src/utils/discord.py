"""Discord Webhook 通知ユーティリティ"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path


def _load_webhook_url() -> str:
    for candidate in [
        Path(__file__).parents[3] / ".env",
        Path(__file__).parents[3] / "backend" / ".env",
    ]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                if line.startswith("DISCORD_WEBHOOK_URL="):
                    return line.split("=", 1)[1].strip()
    return os.environ.get("DISCORD_WEBHOOK_URL", "")


def send(content: str) -> bool:
    """Discord にメッセージを送信。成功で True を返す。"""
    url = _load_webhook_url()
    if not url:
        print("[Discord] DISCORD_WEBHOOK_URL が未設定です")
        return False

    payload = json.dumps({"content": content}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (kiseki, 1.0)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 204
    except urllib.error.URLError as e:
        print(f"[Discord] 送信失敗: {e}")
        return False
