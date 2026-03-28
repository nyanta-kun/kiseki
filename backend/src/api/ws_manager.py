"""WebSocket接続マネージャー

レースごとのオッズWebSocket接続を管理し、リアルタイムブロードキャストを行う。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """レースIDごとのWebSocket接続を管理するシングルトン。"""

    def __init__(self) -> None:
        # race_id → set of active WebSocket connections
        self._connections: dict[int, set[WebSocket]] = {}

    async def connect(self, race_id: int, ws: WebSocket) -> None:
        """クライアントを接続してコネクションプールに追加する。"""
        await ws.accept()
        self._connections.setdefault(race_id, set()).add(ws)
        logger.debug(f"WS connect: race_id={race_id}, total={len(self._connections[race_id])}")

    def disconnect(self, race_id: int, ws: WebSocket) -> None:
        """クライアントをコネクションプールから削除する。"""
        conns = self._connections.get(race_id, set())
        conns.discard(ws)
        if not conns:
            self._connections.pop(race_id, None)
        logger.debug(f"WS disconnect: race_id={race_id}")

    async def broadcast(self, race_id: int, data: dict[str, Any]) -> None:
        """指定レースに接続中の全クライアントへデータを送信する。"""
        conns = list(self._connections.get(race_id, set()))
        if not conns:
            return
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(data)
            except Exception as e:
                logger.debug(f"WS broadcast error: {e}")
                dead.append(ws)
        for ws in dead:
            self.disconnect(race_id, ws)


# モジュールレベルのシングルトン
manager = ConnectionManager()          # オッズ用
results_manager = ConnectionManager()  # 成績用
