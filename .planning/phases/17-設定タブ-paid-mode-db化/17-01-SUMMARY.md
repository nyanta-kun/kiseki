---
phase: 17-設定タブ-paid-mode-db化
plan: "01"
subsystem: database, api
tags: [fastapi, sqlalchemy, alembic, postgresql, pydantic, admin-api]

# Dependency graph
requires:
  - phase: 16-データタブ
    provides: admin_router, ApiKeyDep, DbDep patterns in users.py
provides:
  - keiba.app_settings テーブル（Alembic マイグレーション + 初期データ PAID_MODE=false）
  - AppSettings ORM モデル（backend/src/db/models.py）
  - GET /api/admin/settings エンドポイント（ApiKeyDep 認証）
  - PUT /api/admin/settings エンドポイント（PostgreSQL UPSERT、ApiKeyDep 認証）
affects:
  - 17-02（設定タブ フロントエンド実装）
  - PAID_MODE をビルド時環境変数ではなく DB で管理するすべての後続フェーズ

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "AppSettings キーバリューテーブルパターン: keiba.app_settings でシステム設定を管理"
    - "PostgreSQL UPSERT: pg_insert(...).on_conflict_do_update() でキー重複時に値を上書き"
    - "lazy import inside endpoint: from ..db.models import AppSettings をエンドポイント関数内でインポート"

key-files:
  created:
    - backend/alembic/versions/m3n4o5p6q7r8_add_app_settings.py
  modified:
    - backend/src/db/models.py
    - backend/src/api/users.py

key-decisions:
  - "PAID_MODE をキーバリューテーブル (keiba.app_settings) で管理する方針を採用（環境変数ではなく DB）"
  - "初期値 PAID_MODE='false' は Alembic マイグレーションの upgrade() 内で INSERT"
  - "PUT /api/admin/settings は UPSERT 方式（pg_insert + on_conflict_do_update）で冪等性を確保"

patterns-established:
  - "設定 UPSERT パターン: pg_insert(AppSettings).on_conflict_do_update(index_elements=['key'], set_=...)"

requirements-completed: [ADMIN-08]

# Metrics
duration: 15min
completed: 2026-04-05
---

# Phase 17 Plan 01: app_settings DB 基盤 Summary

**PAID_MODE DB 管理の基盤として keiba.app_settings テーブル、AppSettings ORM モデル、管理者向け GET/PUT /api/admin/settings API を実装**

## Performance

- **Duration:** 15 min
- **Started:** 2026-04-05T10:58:00Z
- **Completed:** 2026-04-05T11:13:54Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- `keiba.app_settings` テーブルを作成する Alembic マイグレーションを追加（初期値 `PAID_MODE='false'` の INSERT 含む）
- `AppSettings` ORM モデルを `backend/src/db/models.py` に追加（String pk + value + updated_at + updated_by）
- `GET/PUT /api/admin/settings` エンドポイントを `admin_router` に追加（ApiKeyDep 保護、UPSERT 方式）

## Task Commits

Each task was committed atomically:

1. **Task 1: AppSettings モデルを追加 + Alembic マイグレーション作成** - `48ddb44` (feat)
2. **Task 2: バックエンド GET/PUT /api/admin/settings エンドポイントを追加** - `c8cf23d` (feat)

**Plan metadata:** (docs commit - TBD)

## Files Created/Modified
- `backend/alembic/versions/m3n4o5p6q7r8_add_app_settings.py` - keiba.app_settings テーブル作成 + PAID_MODE 初期値 INSERT
- `backend/src/db/models.py` - AppSettings ORM クラスを末尾に追加
- `backend/src/api/users.py` - AppSettingResponse/AppSettingsResponse/UpdateSettingRequest スキーマ + GET/PUT /api/admin/settings エンドポイント

## Decisions Made
- PAID_MODE をキーバリュー形式の DB テーブルで管理する方針（ビルド時環境変数ではなくランタイム可変）
- PUT エンドポイントは UPSERT（`pg_insert + on_conflict_do_update`）で冪等性を確保
- `updated_by` フィールドは今フェーズでは未使用（将来の監査証跡用に予約）

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Ruff I001 インポート順序の修正**
- **Found during:** Task 2 のRuff チェック
- **Issue:** `from ..db.models import AppSettings` と `from sqlalchemy.dialects.postgresql import ...` の順序が Ruff I001 違反
- **Fix:** stdlib/third-party/local の順序に並べ替え（`pg_insert` import を先に移動）
- **Files modified:** backend/src/api/users.py
- **Verification:** `ruff check src/api/users.py` が All checks passed
- **Committed in:** c8cf23d (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (import ordering)
**Impact on plan:** Ruff 準拠のためのインポート順序修正のみ。スコープ変更なし。

## Issues Encountered
- `.venv` の Python 3.14 環境には fastapi が入っていないため、plan 指定のインポート検証コマンドが使えなかった。代替として `py_compile` による構文チェックと `ruff check` + grep によるルート確認を実施した。

## User Setup Required
Alembic マイグレーションの適用が必要:
```bash
cd backend && alembic upgrade head
```
または Docker 環境で本番コンテナ起動時に自動適用される（Phase 3.5 パターン）。

## Next Phase Readiness
- `GET/PUT /api/admin/settings` エンドポイントが利用可能
- `17-02`（設定タブ フロントエンド実装）に必要なバックエンド基盤が整った
- フロントエンドから `PAID_MODE` の読み取り・変更が可能

## Self-Check: PASSED

- FOUND: backend/alembic/versions/m3n4o5p6q7r8_add_app_settings.py
- FOUND: backend/src/db/models.py
- FOUND: backend/src/api/users.py
- FOUND: commit 48ddb44
- FOUND: commit c8cf23d

---
*Phase: 17-設定タブ-paid-mode-db化*
*Completed: 2026-04-05*
