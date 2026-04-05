---
phase: 16-データタブ
plan: "02"
subsystem: ui
tags: [next.js, route-handler, server-action, admin, data-coverage]

# Dependency graph
requires:
  - phase: 16-01
    provides: バックエンド data-coverage / fetch-data エンドポイント
provides:
  - GET /api/admin/data-coverage Route Handler（admin 認証 + X-API-Key 中継）
  - DataTab.tsx（年/月別グリッド表示 + 取得ボタン + useEffect fetch）
  - triggerFetchData Server Action（POST /admin/fetch-data 呼び出し）
affects: [admin-ui, data-pipeline]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Next.js Route Handler で admin セッション認証 + X-API-Key バックエンド中継"
    - "useTransition + isPending で二重送信防止"

key-files:
  created:
    - frontend/src/app/api/admin/data-coverage/route.ts
  modified:
    - frontend/src/app/admin/DataTab.tsx
    - frontend/src/app/admin/actions.ts

key-decisions:
  - "Route Handler 経由で INTERNAL_API_KEY をブラウザに露出させない設計を採用"
  - "取得ボタン押下時は confirm() でユーザー確認を取り、startTransition で非同期処理"

patterns-established:
  - "admin Route Handler: auth() → role check → backend fetch with X-API-Key"
  - "Client Component: useEffect でデータ取得 + useTransition で Server Action 呼び出し"

requirements-completed: [ADMIN-05, ADMIN-06]

# Metrics
duration: 2min
completed: 2026-04-05
---

# Phase 16 Plan 02: データタブ フロントエンド実装 Summary

**admin 認証付き Route Handler で data-coverage を中継し、DataTab.tsx に年/月別グリッドと「取得」ボタンを実装**

## Performance

- **Duration:** 2 min
- **Started:** 2026-04-05T10:47:30Z
- **Completed:** 2026-04-05T10:49:20Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- GET /api/admin/data-coverage Route Handler を新規作成し、admin セッション認証 + X-API-Key 中継を実装
- DataTab.tsx をプレースホルダーから完全実装に置き換え（useEffect fetch + 年/月グリッド表示）
- 件数ゼロの月に「取得」ボタンを表示し、isPending / fetchingMonth で二重送信を防止
- actions.ts に triggerFetchData Server Action を追加し POST /admin/fetch-data を呼び出し

## Task Commits

各タスクを個別にコミット:

1. **Task 1: Next.js Route Handler を作成（data-coverage API 中継）** - `08f3ddf` (feat)
2. **Task 2: DataTab.tsx 実装 + actions.ts に triggerFetchData を追加** - `c36548f` (feat)

## Files Created/Modified

- `frontend/src/app/api/admin/data-coverage/route.ts` - admin 認証 + バックエンド中継 Route Handler（新規）
- `frontend/src/app/admin/DataTab.tsx` - 年/月別グリッド + 取得ボタン Client Component（全面実装）
- `frontend/src/app/admin/actions.ts` - triggerFetchData Server Action を末尾に追加

## Decisions Made

- Route Handler 経由で INTERNAL_API_KEY をブラウザに露出させない設計を採用（セキュリティ要件）
- 取得ボタン押下時は confirm() でユーザー確認を取り、startTransition で非同期処理を実行

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 16 全 2 プランが完了。管理画面のデータタブが完全動作状態
- Windows Agent の recent モード起動との統合テストが可能な状態
- Phase 17（次フェーズ）への移行準備完了

---
*Phase: 16-データタブ*
*Completed: 2026-04-05*
