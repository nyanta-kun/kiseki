---
phase: 16-データタブ
plan: "01"
subsystem: api
tags: [fastapi, pydantic, windows-agent, data-coverage, command-queue]

# Dependency graph
requires:
  - phase: 15-AdminUI再構成
    provides: admin_router パターン・ApiKeyDep・DbDep 依存性注入
provides:
  - GET /api/admin/data-coverage（年/月別レース件数、ゼロ月含む全12ヶ月）
  - POST /api/admin/fetch-data（recent コマンドを _command_queue に積む）
  - agent_router.py valid_actions に "recent" 追加
  - jvlink_agent.py run_command_loop() の recent ハンドラー
affects: [16-02, DataTab フロントエンド]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "admin_router への GET/POST エンドポイント追加パターン（ApiKeyDep + DbDep）"
    - "_command_queue.append(entry) で fetch-data → agent_router 橋渡しパターン"
    - "jvlink_agent run_command_loop() の elif action == X: ディスパッチパターン"

key-files:
  created: []
  modified:
    - backend/src/api/users.py
    - backend/src/api/agent_router.py
    - windows-agent/jvlink_agent.py

key-decisions:
  - "datetime.now(UTC) を使用（タイムゾーン aware）。関数内 import の datetime を使わず既存 import の UTC を活用"
  - "fetch-data エンドポイントは DbDep を取らない（キューに積むだけ）"
  - "agent_router._command_queue を users.py から直接 import して append する（既存パターン踏襲）"

patterns-established:
  - "data-coverage: func.left(Race.date, 6) で YYYYMM グループ化し全12月ゼロ埋め"

requirements-completed: [ADMIN-05, ADMIN-06]

# Metrics
duration: 15min
completed: 2026-04-05
---

# Phase 16 Plan 01: データタブ API 基盤 Summary

**月別レースデータ取得状況API（data-coverage）と取得指示API（fetch-data）をバックエンドに追加し、Windows Agent に recent コマンドハンドラーを実装**

## Performance

- **Duration:** 15 min
- **Started:** 2026-04-05T10:35:00Z
- **Completed:** 2026-04-05T10:50:00Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- GET /api/admin/data-coverage: Race.date を YYYYMM でグループ化し、ゼロ件月も含む全12ヶ月グリッドを DataCoverageResponse で返す
- POST /api/admin/fetch-data: year_month 検証後 recent コマンドエントリを _command_queue に append
- agent_router.py の valid_actions に "recent" を追加し API 経由の recent コマンド受付を有効化
- jvlink_agent.py の run_command_loop() に elif action == "recent" ブランチを追加し run_recent() を呼び出す

## Task Commits

各タスクを個別にコミット:

1. **Task 1: data-coverage / fetch-data エンドポイント追加** - `d20ab41` (feat)
2. **Task 2: agent_router recent 追加 + jvlink_agent recent ハンドラー** - `c7bacb2` (feat)

## Files Created/Modified
- `backend/src/api/users.py` - Pydantic スキーマ 4 種追加 + GET /data-coverage + POST /fetch-data エンドポイント追加
- `backend/src/api/agent_router.py` - valid_actions に "recent" 追加 + コマンドフロー docstring 更新
- `windows-agent/jvlink_agent.py` - run_command_loop() に elif action == "recent" ハンドラーを追加

## Decisions Made
- `datetime.now(UTC)` を使用（タイムゾーン aware）。関数内 import の datetime を避け既存 import の `UTC` を活用
- fetch-data エンドポイントは DB セッションを必要としない（キューに積むだけの設計）
- `_command_queue` は `agent_router` から直接 import して append（既存パターン踏襲）

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- 開発環境の venv には FastAPI がインストールされていないため（Docker 実行環境）、`python -c "from src.api.users import admin_router"` による実行時検証は不可。代わりに構文チェック（`py_compile`）と grep による静的検証を実施。

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- API 基盤が整備され、16-02 DataTab フロントエンド実装が可能な状態
- Windows Agent も recent コマンドを受け取れるよう準備完了

---
*Phase: 16-データタブ*
*Completed: 2026-04-05*
