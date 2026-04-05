---
phase: 15-admin-ui-restructure
plan: "02"
subsystem: ui
tags: [nextjs, react, admin, tailwind, server-components, client-components]

# Dependency graph
requires:
  - phase: 15-01
    provides: "UserResponse に yoso_name / is_yoso_public フィールドを追加（バックエンド API）"
provides:
  - "AdminTabs コンポーネントによる「ユーザー」「データ」「設定」3タブ構成"
  - "UsersTab: whitespace-nowrap + 10件ページング + yoso_name/is_yoso_public 列"
  - "CodesTab: 招待コード管理を Server Component から Client Component に分離"
  - "DataTab/SettingsTab: Phase 16/17 向けプレースホルダー"
  - "page.tsx の Server Component 化（認証 + データ取得のみ担当）"
affects: [16-data-management, 17-settings]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Server Component → Client Component via props パターン（data fetching は Server、インタラクションは Client）"
    - "Server Action の bind パターン（updateUser.bind(null, userId, patch) で form action に渡す）"
    - "Client Component ファイル分割によるタブコンテナパターン（AdminTabs で useState で管理）"

key-files:
  created:
    - frontend/src/app/admin/AdminTabs.tsx
    - frontend/src/app/admin/UsersTab.tsx
    - frontend/src/app/admin/CodesTab.tsx
    - frontend/src/app/admin/DataTab.tsx
    - frontend/src/app/admin/SettingsTab.tsx
  modified:
    - frontend/src/app/admin/page.tsx

key-decisions:
  - "型定義（User, InvitationCode）は AdminTabs.tsx で export し、他コンポーネントが import する構成を採用"
  - "page.tsx はインライン Server Action を全削除し、Server Component として認証＋データ取得のみ担当"
  - "Server Action の部分適用は bind パターン（.bind(null, id, patch)）で FormData を使わずに実装"

patterns-established:
  - "AdminTabsパターン: Server Component がデータ取得→Client Component タブコンテナへ props 渡し"
  - "whitespace-nowrap テーブル: overflow-x-auto ラッパー + 全 td に whitespace-nowrap"
  - "ページングパターン: PAGE_SIZE=10、useState(0) でページ管理、slice でページ切り出し"

requirements-completed: [ADMIN-01, ADMIN-02, ADMIN-03, ADMIN-04]

# Metrics
duration: checkpoint included (user verification)
completed: 2026-04-05
---

# Phase 15 Plan 02: 管理画面タブ再構成 Summary

**管理画面を AdminTabs/UsersTab/CodesTab/DataTab/SettingsTab に分割し、ユーザーテーブルに whitespace-nowrap + 10件ページング + yoso_name/is_yoso_public 列を追加**

## Performance

- **Duration:** checkpoint含む（ユーザー確認待ち時間含む）
- **Started:** 2026-04-05
- **Completed:** 2026-04-05
- **Tasks:** 3（うち1件は human-verify チェックポイント）
- **Files modified:** 6

## Accomplishments

- AdminTabs コンポーネントで「ユーザー」「データ」「設定」3タブ構成を実現（ADMIN-01）
- ユーザーテーブルに `overflow-x-auto` + 全 td の `whitespace-nowrap` を適用し行折り返しを解消（ADMIN-02）
- 10件ページング（PAGE_SIZE=10）と前後ページナビゲーションを実装（ADMIN-03）
- `yoso_name`（予想家名）と `is_yoso_public`（公開/非公開バッジ）列をユーザーテーブルに追加（ADMIN-04）
- page.tsx からインライン Server Action を全削除し、Server Component として最小化

## Task Commits

1. **Task 1: 型定義と Client Component ファイル群を作成** - `7dc14b9` (feat)
2. **Task 2: page.tsx をリファクタリングして AdminTabs に委譲** - `a876fcf` (refactor)
3. **Task 3: 管理画面タブ再構成の動作確認** - human-verify チェックポイント（ユーザー承認済み）

## Files Created/Modified

- `frontend/src/app/admin/AdminTabs.tsx` - タブコンテナ（useState でアクティブタブ管理）、User/InvitationCode 型 export
- `frontend/src/app/admin/UsersTab.tsx` - ユーザーテーブル（whitespace-nowrap + 10件ページング + yoso列）
- `frontend/src/app/admin/CodesTab.tsx` - 招待コード管理（page.tsx から分離）
- `frontend/src/app/admin/DataTab.tsx` - データタブプレースホルダー（Phase 16 実装予定）
- `frontend/src/app/admin/SettingsTab.tsx` - 設定タブプレースホルダー（Phase 17 実装予定）
- `frontend/src/app/admin/page.tsx` - Server Component（認証 + データ取得 → AdminTabs へ渡す最小構成）

## Decisions Made

- 型定義（User, InvitationCode）は AdminTabs.tsx で export し、他コンポーネントが `import type` で再利用する構成を採用。型の一元管理で将来の型変更を容易化。
- page.tsx のインライン Server Action を全削除し、CodesTab/UsersTab が actions.ts から直接 import する形に統一。
- Server Action への引数渡しは `.bind(null, id, patch)` パターンを採用し、FormData を使わず型安全な呼び出しを実現。

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- ADMIN-01〜04 完了。Phase 16（データ管理タブ）と Phase 17（設定タブ）の拡張基盤が整った。
- DataTab.tsx と SettingsTab.tsx はプレースホルダーとして配置済みで、各フェーズで実装を追加するだけ。

---
*Phase: 15-admin-ui-restructure*
*Completed: 2026-04-05*
