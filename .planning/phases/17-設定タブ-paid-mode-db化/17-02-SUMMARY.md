---
phase: 17-設定タブ-paid-mode-db化
plan: "02"
subsystem: ui
tags: [next.js, paid-mode, paywall, server-action, route-handler, admin]

requires:
  - phase: 17-01
    provides: バックエンド GET/PUT /api/admin/settings エンドポイント + keiba.app_settings テーブル

provides:
  - admin 認証付き設定 Route Handler（GET/PUT /api/admin/settings）
  - 設定タブの PAID_MODE トグル UI（SettingsTab.tsx）
  - PaywallGate の paywallEnabled prop 化（ビルド時環境変数依存の除去）
  - layout.tsx / my/page.tsx / races/[id]/page.tsx の PAID_MODE 動的取得実装

affects: [paywall, paid-mode, admin, race-detail, my-page]

tech-stack:
  added: []
  patterns:
    - "Server Component の fetchPaidMode() パターン: BACKEND_URL で直接バックエンドを叩き、失敗時は false（フェイルセーフ）"
    - "Client Component の useTransition + Server Action パターン: SettingsTab でトグル操作を非同期実行"
    - "admin 認証付き Route Handler: auth() で role チェック → バックエンドへプロキシ"

key-files:
  created:
    - frontend/src/app/api/admin/settings/route.ts
  modified:
    - frontend/src/app/admin/SettingsTab.tsx
    - frontend/src/app/admin/actions.ts
    - frontend/src/components/PaywallGate.tsx
    - frontend/src/components/RaceDetailClient.tsx
    - frontend/src/app/layout.tsx
    - frontend/src/app/my/page.tsx
    - frontend/src/app/races/[id]/page.tsx

key-decisions:
  - "自己参照 Route Handler を使わず、各サーバーコンポーネントで直接バックエンドを叩く（Pitfall 3 回避）"
  - "バックエンド障害時のフェイルセーフは paidMode=false（ペイウォール無効）"
  - "updatePaidMode は Server Action として actions.ts に追加（INTERNAL_API_KEY をブラウザに露出させない設計を継続）"

patterns-established:
  - "fetchPaidMode(): 各サーバーコンポーネントにインライン定義、try/catch で false を返すフェイルセーフパターン"

requirements-completed: [ADMIN-07, ADMIN-08]

duration: 1min
completed: 2026-04-05
---

# Phase 17 Plan 02: フロントエンド PAID_MODE 動的取得移行 Summary

**管理画面設定タブに PAID_MODE トグル UI を実装し、3 つのサーバーコンポーネントが NEXT_PUBLIC_PAID_MODE 環境変数を参照せず DB から動的取得するよう移行完了**

## Performance

- **Duration:** 1 min
- **Started:** 2026-04-05T11:15:00Z
- **Completed:** 2026-04-05T11:16:00Z
- **Tasks:** 2 of 3 (Task 3 は checkpoint:human-verify)
- **Files modified:** 7 (1 created)

## Accomplishments

- `/api/admin/settings` Route Handler (GET/PUT) を admin 認証付きで新規作成
- `SettingsTab.tsx` をプレースホルダーから実機能トグル UI に全面置き換え（useTransition + Server Action）
- `PaywallGate.tsx` の `paywallEnabled` prop 化により、ビルド時環境変数依存を完全除去
- `layout.tsx` / `my/page.tsx` / `races/[id]/page.tsx` の 3 ファイルで `fetchPaidMode()` による動的取得を実装
- `NEXT_PUBLIC_PAID_MODE` の参照が `frontend/src/` に 0 件であることを確認済み

## Task Commits

1. **Task 1: 管理者向け設定 Route Handler + SettingsTab.tsx 実装 + actions.ts に updatePaidMode 追加** - `273c6d2` (feat)
2. **Task 2: PAID_MODE 動的取得へ移行（PaywallGate / RaceDetailClient / layout.tsx / my/page.tsx / races/[id]/page.tsx）** - `681c853` (feat)
3. **Task 3: PAID_MODE DB 管理の動作確認** - checkpoint:human-verify（人手確認中）

## Files Created/Modified

- `frontend/src/app/api/admin/settings/route.ts` - 新規作成: admin 認証付き GET/PUT Route Handler
- `frontend/src/app/admin/SettingsTab.tsx` - PAID_MODE トグル UI（useTransition + fetch + Server Action）
- `frontend/src/app/admin/actions.ts` - updatePaidMode() Server Action を追加
- `frontend/src/components/PaywallGate.tsx` - paywallEnabled prop 化、NEXT_PUBLIC_PAID_MODE 参照削除
- `frontend/src/components/RaceDetailClient.tsx` - paywallEnabled? prop を追加して PaywallGate に渡す
- `frontend/src/app/layout.tsx` - fetchPaidMode() を追加し Footer 表示に利用
- `frontend/src/app/my/page.tsx` - fetchPaidMode() でバックエンドから動的取得
- `frontend/src/app/races/[id]/page.tsx` - fetchPaidMode() を追加し RaceDetailClient に paywallEnabled を渡す

## Decisions Made

- 自己参照 Route Handler（`/api/admin/settings` → Next.js 自身）を使わず、各サーバーコンポーネントで直接 `BACKEND_URL/admin/settings` を叩くパターンを採用（Server Component からの自己参照は Pitfall 3: ループの懸念があるため）
- バックエンド障害時のフェイルセーフとして `paidMode=false`（ペイウォール無効）を選択（サービス継続を優先）

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- `git add frontend/src/app/races/[id]/page.tsx` でブラケットを含むパスがシェルのグロブ展開にマッチしてエラーになったため、クォートで対処した（Rule 3 に相当する軽微なブロッカー）

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Task 3 のチェックポイント確認後に Phase 17 が完了
- バックエンドをリビルドしてマイグレーションを実行すること: `docker compose up --build backend -d && docker compose exec backend alembic upgrade head`
- 管理画面 `/admin` → 設定タブ で PAID_MODE トグルが動作することを確認すること

---
*Phase: 17-設定タブ-paid-mode-db化*
*Completed: 2026-04-05*
