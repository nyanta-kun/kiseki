"""Windows Agent コマンドキューAPIルーター

Mac側からWindows Agentへのコマンド送信（コマンドキュー方式）と
Agentのステータス報告を管理するエンドポイント。

コマンドフロー:
  Mac (Claude Code)
    POST /api/agent/command  {"action": "setup", "params": {...}}
        ↓
  FastAPI (このルーター) - メモリ内キュー
        ↓ polling
  Windows Agent (jvlink_agent.py)
    GET /api/agent/command  → {"action": "setup"} を受け取り実行

Agentステータスフロー:
  Windows Agent
    POST /api/agent/status  {"status": "running", "mode": "setup", ...}
        ↓
  GET /api/agent/status  → Mac側から現在状態を確認
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])

# -------------------------------------------------------------------
# インメモリ状態管理
# -------------------------------------------------------------------

# コマンドキュー（FIFO）
_command_queue: deque[dict] = deque()

# 最新のエージェントステータス
_agent_status: dict[str, Any] = {
    "status": "unknown",
    "mode": None,
    "last_seen": None,
    "message": "No status reported yet",
}


# -------------------------------------------------------------------
# 認証
# -------------------------------------------------------------------


def verify_api_key(x_api_key: Annotated[str, Header()] = "") -> None:
    """API Key 認証。本番環境ではAPIキーが必須。"""
    if not settings.change_notify_api_key or not settings.change_notify_api_key.strip():
        if settings.api_env == "production":
            logger.error("CHANGE_NOTIFY_API_KEY is not set in production environment")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="API key not configured",
            )
        return  # 開発環境では認証省略
    if x_api_key != settings.change_notify_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )


ApiKeyDep = Annotated[None, Depends(verify_api_key)]


# -------------------------------------------------------------------
# リクエスト/レスポンスモデル
# -------------------------------------------------------------------


class CommandRequest(BaseModel):
    """Mac側からAgentへのコマンド。"""

    action: str  # "setup" | "daily" | "retry" | "stop"
    params: dict[str, Any] = {}  # オプションパラメータ


class AgentStatusReport(BaseModel):
    """Windows AgentからのステータスPOST。"""

    status: str  # "running" | "idle" | "error" | "done"
    mode: str | None = None  # "setup" | "daily" | "realtime"
    message: str = ""
    progress: dict[str, Any] = {}  # 任意の進捗情報


# -------------------------------------------------------------------
# エンドポイント
# -------------------------------------------------------------------


@router.post("/command", summary="コマンドをキューに追加（Mac → Agent）")
async def enqueue_command(
    cmd: CommandRequest,
    _: ApiKeyDep,
) -> dict:
    """Mac側からWindows Agentへコマンドをキューイングする。

    Windows Agentは GET /api/agent/command をポーリングしてこれを受け取る。

    有効なアクション:
    - setup: JVOpen(option=3)で過去全データを取得
    - daily: JVOpen(option=1/2)で当日データを取得
    - retry: ペンディングキューを再送
    - stop: Agentを停止
    - recent: JVOpen(option=3)で指定年以降のデータを再取得
      params: {"from_year": 2023, "year_month": "202301"}
    """
    valid_actions = {"setup", "daily", "retry", "stop", "recent", "odds_prefetch"}
    if cmd.action not in valid_actions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid action: {cmd.action}. Valid: {valid_actions}",
        )
    entry = {
        "action": cmd.action,
        "params": cmd.params,
        "queued_at": datetime.now().isoformat(),
    }
    _command_queue.append(entry)
    logger.info(f"Command queued: {cmd.action} params={cmd.params}")
    return {"queued": True, "queue_length": len(_command_queue), **entry}


@router.get("/command", summary="次のコマンドを取得（Agent polling）")
async def dequeue_command(_: ApiKeyDep) -> dict:
    """Windows AgentがポーリングしてMac側からのコマンドを受け取る。

    キューに積まれたコマンドがあれば取り出して返す。
    キューが空なら {"action": null} を返す（コマンドなし）。
    """
    if _command_queue:
        cmd = _command_queue.popleft()
        logger.info(f"Command dispatched to agent: {cmd['action']}")
        return cmd
    return {"action": None}


@router.post("/status", summary="Agentステータスを報告（Agent → Mac）")
async def report_status(report: AgentStatusReport, _: ApiKeyDep) -> dict:
    """Windows Agentが現在の状態をBackendへ報告する。

    Mac側は GET /api/agent/status でこれを確認できる。
    """
    global _agent_status
    _agent_status = {
        "status": report.status,
        "mode": report.mode,
        "message": report.message,
        "progress": report.progress,
        "last_seen": datetime.now().isoformat(),
    }
    logger.info(f"Agent status: {report.status} mode={report.mode} msg={report.message}")
    return {"received": True}


@router.get("/status", summary="Agentの最新ステータスを取得")
async def get_status(_: ApiKeyDep) -> dict:
    """Mac側からWindows Agentの最新ステータスを確認する。"""
    return {
        **_agent_status,
        "queue_length": len(_command_queue),
        "queued_commands": list(_command_queue),
    }
