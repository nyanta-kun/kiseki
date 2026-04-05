---
phase: 15-admin-ui-restructure
verified: 2026-04-05T00:00:00Z
status: passed
score: 6/6 must-haves verified
re_verification: false
---

# Phase 15: Admin UI Restructure Verification Report

**Phase Goal:** 管理画面を「ユーザー」「データ」「設定」の3タブ構成に再編し、ユーザーテーブルの表示品質を向上させる。バックエンドAPIに予想家情報（yoso_name / is_yoso_public）を追加する。
**Verified:** 2026-04-05
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| #  | Truth | Status | Evidence |
|----|-------|--------|----------|
| 1  | GET /api/admin/users レスポンスの各ユーザーオブジェクトに yoso_name と is_yoso_public フィールドが含まれる | VERIFIED | `backend/src/api/users.py` L129-130: `yoso_name: str \| None` と `is_yoso_public: bool` が UserResponse に宣言済み |
| 2  | yoso_name は文字列またはnull、is_yoso_public は真偽値として正しく返る | VERIFIED | `_make_user_response()` L161-162: `yoso_name=user.yoso_name`, `is_yoso_public=user.is_yoso_public` でORMから手動マッピング |
| 3  | 管理画面に「ユーザー」「データ」「設定」の3タブが表示され、タブ切り替えで各セクションに移動できる | VERIFIED | `AdminTabs.tsx` L39-43: `TABS` 配列に3タブ定義、L51: `useState<Tab>("users")` でアクティブタブ管理、L75-77: 条件分岐レンダリング |
| 4  | ユーザーテーブルの各行が1行に収まり、長いフィールドが折り返さない（whitespace-nowrap） | VERIFIED | `UsersTab.tsx` L44: `overflow-x-auto` ラッパー、L64-122: 全 `<td>` に `whitespace-nowrap` クラス付与 |
| 5  | ユーザーテーブルが10件ページングで表示され、前後ページボタンで移動できる | VERIFIED | `UsersTab.tsx` L8: `PAGE_SIZE = 10`、L28: `useState(0)` でページ管理、L31: `users.slice(...)` でページ切り出し、L173-195: ページングコントロール実装 |
| 6  | ユーザーテーブルに予想家名（yoso_name）と公開設定（is_yoso_public）の列が表示される | VERIFIED | `UsersTab.tsx` L67: `user.yoso_name ?? "—"`、L68-77: `is_yoso_public` バッジ（公開=緑、非公開=グレー） |

**Score:** 6/6 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `backend/src/api/users.py` | UserResponse スキーマ拡張 | VERIFIED | `yoso_name: str \| None` と `is_yoso_public: bool` を L129-130 に追加済み |
| `frontend/src/app/admin/AdminTabs.tsx` | タブコンテナ（useState でアクティブタブ管理）、User/InvitationCode 型 export | VERIFIED | 80行の実装、`User` / `InvitationCode` 型 export、`useState<Tab>` 実装済み |
| `frontend/src/app/admin/UsersTab.tsx` | ユーザーテーブル（ページング + whitespace-nowrap + yoso列） | VERIFIED | 202行の実装、PAGE_SIZE=10、全 td に whitespace-nowrap、yoso_name/is_yoso_public 列あり |
| `frontend/src/app/admin/CodesTab.tsx` | 招待コード管理（page.tsx から分離） | VERIFIED | "use client" + `createInvitationCode` / `toggleInvitationCode` を actions.ts から import |
| `frontend/src/app/admin/DataTab.tsx` | データタブプレースホルダー（Phase 16 実装予定） | VERIFIED | 仕様通りの placeholder テキスト表示 |
| `frontend/src/app/admin/SettingsTab.tsx` | 設定タブプレースホルダー（Phase 17 実装予定） | VERIFIED | 仕様通りの placeholder テキスト表示 |
| `frontend/src/app/admin/page.tsx` | Server Component（認証 + データ取得 → AdminTabs へ渡す） | VERIFIED | "use client" なし、`auth()` 認証チェック、`fetchUsers()` + `fetchInvitationCodes()` 後に `<AdminTabs users={users} codes={codes} />` レンダリング |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `page.tsx` | `AdminTabs` | props (users, codes) | VERIFIED | L37: `<AdminTabs users={users} codes={codes} />` |
| `UserResponse` | User ORM モデル | `_make_user_response()` | VERIFIED | L161: `yoso_name=user.yoso_name` |
| `UsersTab.tsx` | backend /api/admin/users | props.users (Server Componentが取得済み) | VERIFIED | L31: `users.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)` |
| `UsersTab.tsx` | `actions.ts` | `import { updateUser } from './actions'` | VERIFIED | L4: import 済み、L127/143: フォームアクションで呼び出し |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| ADMIN-01 | 15-02 | 管理画面がタブUI（ユーザー / データ / 設定）に再構成される | SATISFIED | `AdminTabs.tsx` の3タブ実装 |
| ADMIN-02 | 15-02 | 登録ユーザーテーブルの各行が1行表示（whitespace-nowrap + テーブル内横スクロール） | SATISFIED | `UsersTab.tsx` L44 `overflow-x-auto` + 全 td `whitespace-nowrap` |
| ADMIN-03 | 15-02 | 登録ユーザーテーブルが10件ページングで閲覧できる | SATISFIED | `UsersTab.tsx` L8 `PAGE_SIZE = 10`、L173-195 ページングコントロール |
| ADMIN-04 | 15-01, 15-02 | 登録ユーザーテーブルに予想家名（yoso_name）・公開設定（is_yoso_public）が表示される | SATISFIED | バックエンド `UserResponse` に両フィールド追加、フロントエンド `UsersTab.tsx` で列表示 |

**REQUIREMENTS.md との照合:** ADMIN-01〜04 は全て Phase 15 に割り当てられており、全件 SATISFIED。ADMIN-05〜08 は Phase 16/17 担当のため本フェーズでは対象外（orphaned なし）。

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `CodesTab.tsx` | 59, 86 | `placeholder="..."` | Info | HTML input placeholder 属性。コードのプレースホルダーではなくフォームUI要素のため問題なし |

ブロッカーなし。

---

### Commit Verification

SUMMARY.md に記載されたコミットハッシュを実際の git log で確認済み：
- `b9f8d5a` — feat(15-01): UserResponse に yoso_name / is_yoso_public を追加 — 存在確認
- `7dc14b9` — feat(15-02): 管理画面タブコンポーネント群を新規作成 — 存在確認
- `a876fcf` — refactor(15-02): page.tsx を Server Component + AdminTabs 委譲構成に書き換え — 存在確認

---

### Human Verification Required

#### 1. 管理画面タブ切り替えとインタラクションの動作確認

**Test:** 管理者アカウントで `/admin` にアクセスし、タブ切り替え・ユーザー操作・招待コード操作を実際に実行する
**Expected:** 3タブが表示され切り替え可能、ユーザー操作（ロール変更・有効化/無効化）が DB に反映される、招待コード CRUD が正常動作する
**Why human:** UI の実際のレンダリング、クリックイベント、Server Action の DB 反映はコード静的解析では確認不可

SUMMARY.md の記録では Task 3 の human-verify チェックポイントでユーザー承認済みとされている（"ユーザー承認済み"）。自動検証の範囲では全コードパスが正しく実装されていることを確認済み。

---

### Gaps Summary

ギャップなし。全 must-haves が検証済み。

---

## Regression Check

- `UserResponse` の既存フィールド（`can_input_index` 等）が引き続き存在することを確認済み（L125, L157）
- `page.tsx` のインライン Server Action が全削除され、Client Component（CodesTab/UsersTab）が `actions.ts` 経由に統一されていることを確認済み
- `model_config = {"from_attributes": False}` が維持されていることを確認済み（L132）

---

_Verified: 2026-04-05_
_Verifier: Claude (gsd-verifier)_
