---
phase: 17-設定タブ-paid-mode-db化
verified: 2026-04-05T12:00:00Z
status: passed
score: 7/7 must-haves verified
re_verification: false
---

# Phase 17: 設定タブ PAID_MODE DB 化 Verification Report

**Phase Goal:** PAID_MODE が DB で管理され、管理者が UI から ON/OFF を切り替えるとリアルタイムにサービスの公開/制限状態が変わる
**Verified:** 2026-04-05T12:00:00Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | keiba.app_settings テーブルが存在し PAID_MODE='false' の初期行が格納される | VERIFIED | `m3n4o5p6q7r8_add_app_settings.py` に `op.create_table("app_settings", schema="keiba")` と `INSERT INTO keiba.app_settings (key, value) VALUES ('PAID_MODE', 'false')` が存在 |
| 2 | GET /api/admin/settings が全設定を JSON で返す（ApiKeyDep 認証） | VERIFIED | `users.py` に `@admin_router.get("/settings", response_model=AppSettingsResponse)` が実装済み、`_: ApiKeyDep` で保護 |
| 3 | PUT /api/admin/settings が PAID_MODE の値を UPSERT で更新できる | VERIFIED | `users.py` に `pg_insert(AppSettings).on_conflict_do_update(index_elements=["key"], ...)` が実装済み |
| 4 | 設定タブに PAID_MODE トグルが表示され ON/OFF 切り替えで DB に反映される | VERIFIED | `SettingsTab.tsx` が `useTransition` + `updatePaidMode` Server Action + `fetch('/api/admin/settings')` を含む完全実装 |
| 5 | layout.tsx・my/page.tsx・races/[id]/page.tsx が NEXT_PUBLIC_PAID_MODE を参照せず API から paidMode を取得する | VERIFIED | `grep -r "NEXT_PUBLIC_PAID_MODE" frontend/src/` が 0 件。3 ファイルすべてに `fetchPaidMode()` 関数が実装済み |
| 6 | PaywallGate.tsx が NEXT_PUBLIC_PAID_MODE を参照せず paywallEnabled prop でペイウォールを制御する | VERIFIED | `PaywallGate.tsx` Props に `paywallEnabled: boolean` が存在、環境変数参照なし、`const isFree = !paywallEnabled || isPremium || raceNumber === 1` で制御 |
| 7 | バックエンド障害時はフェイルセーフとして paidMode=false（ペイウォール無効）になる | VERIFIED | 3 ファイルの `fetchPaidMode()` がすべて `try/catch` で囲まれ、`return false` でフォールバック |

**Score:** 7/7 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `backend/alembic/versions/m3n4o5p6q7r8_add_app_settings.py` | keiba.app_settings テーブル作成 + 初期データ | VERIFIED | ファイル存在。`CREATE TABLE keiba.app_settings` 相当の `op.create_table(..., schema="keiba")` と `INSERT` 文を含む。`down_revision = "l2m3n4o5p6q7"` 正しい |
| `backend/src/db/models.py` | AppSettings ORM モデル | VERIFIED | `class AppSettings(Base)` が存在、`__tablename__ = "app_settings"`, `__table_args__ = {"schema": SCHEMA}`, `key / value / updated_at / updated_by` フィールドすべて定義済み |
| `backend/src/api/users.py` | GET/PUT /api/admin/settings エンドポイント | VERIFIED | `get_settings` / `update_setting` 関数が `admin_router` に登録済み。Pydantic スキーマ `AppSettingResponse` / `AppSettingsResponse` / `UpdateSettingRequest` 存在 |
| `frontend/src/app/api/admin/settings/route.ts` | admin 認証付き設定 Route Handler（GET/PUT） | VERIFIED | 新規作成済み。`export async function GET()` / `export async function PUT(req)` が存在、`auth()` で role チェック後バックエンドへプロキシ |
| `frontend/src/app/admin/SettingsTab.tsx` | PAID_MODE トグル UI（Client Component） | VERIFIED | `"use client"` 宣言、`useTransition` 使用、`fetch("/api/admin/settings")` で初期値取得、`updatePaidMode` Server Action でトグル反映 |
| `frontend/src/components/PaywallGate.tsx` | paywallEnabled prop でペイウォールを制御 | VERIFIED | Props に `paywallEnabled: boolean` 追加済み、`NEXT_PUBLIC_PAID_MODE` 参照なし |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `backend/src/api/users.py` | `backend/src/db/models.py` | `from ..db.models import AppSettings` | WIRED | エンドポイント内で `from ..db.models import AppSettings` を lazy import |
| `backend/src/api/users.py` | `keiba.app_settings` | PostgreSQL UPSERT | WIRED | `pg_insert(AppSettings).on_conflict_do_update(index_elements=["key"], set_=...)` が存在 |
| `frontend/src/app/admin/SettingsTab.tsx` | `/api/admin/settings` | `fetch('/api/admin/settings')` | WIRED | `fetch("/api/admin/settings")` が `loadSettings()` 内で呼ばれ、レスポンスが `setPaidMode` へ渡る |
| `frontend/src/app/layout.tsx` | backend GET /api/admin/settings | `fetchPaidMode()` with BACKEND_URL + INTERNAL_API_KEY | WIRED | `BACKEND_URL` + `/admin/settings` フェッチ実装済み、`paidMode` が `{paidMode && <Footer />}` で使用 |
| `frontend/src/app/races/[id]/page.tsx` | `frontend/src/components/RaceDetailClient.tsx` | `paywallEnabled={paidMode}` prop | WIRED | `paywallEnabled={paidMode}` が `RaceDetailClient` に渡され、`PaywallGate` まで伝達 |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| ADMIN-07 | 17-02-PLAN.md | 設定タブで PAID_MODE の ON/OFF を UI 上で切り替えられる | SATISFIED | `SettingsTab.tsx` がトグル UI として実装済み。`updatePaidMode` Server Action が DB を更新 |
| ADMIN-08 | 17-01-PLAN.md, 17-02-PLAN.md | PAID_MODE が DB で管理され、フロントエンドが起動時に API から動的取得する | SATISFIED | `AppSettings` テーブル + `fetchPaidMode()` が 3 サーバーコンポーネントに実装済み。`NEXT_PUBLIC_PAID_MODE` 参照 0 件確認済み |

REQUIREMENTS.md での Phase 17 マッピング: ADMIN-07, ADMIN-08 のみ。すべて Plan frontmatter に記載されており、孤立要件なし。

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| なし | - | - | - | - |

対象ファイルをスキャンした結果、TODO/FIXME/placeholder コメント、空実装、`return null`/`return {}` などの赤旗パターンは検出されなかった。

---

### Human Verification Required

#### 1. PAID_MODE トグル ON/OFF の E2E 動作確認

**Test:** 管理画面 `/admin` → 設定タブを開き、PAID_MODE トグルを ON にしてから別ウィンドウで `/races/...` を開く
**Expected:** 非プレミアムユーザー（2R 以降）にペイウォールのぼかしオーバーレイが表示される。トグルを OFF に戻すと非表示になる
**Why human:** SSR の動的取得挙動とリアルタイム切り替え反映は、ブラウザ操作とページリロードを組み合わせた手動確認が必要

#### 2. バックエンド障害時のフェイルセーフ確認

**Test:** バックエンドを停止した状態でフロントエンドにアクセスする
**Expected:** ペイウォールが無効（フリー状態）でページが表示される（`paidMode=false` フォールバック）
**Why human:** ネットワーク障害シミュレーションはプログラム検証困難

---

### Gaps Summary

ギャップなし。Phase 17 の全 must-have が実装済みかつ接続済みであることを確認した。

---

## Verification Detail Notes

- `NEXT_PUBLIC_PAID_MODE` の参照が `frontend/src/` に **0 件**であることを実際の grep で確認済み（SUMMARY の主張と一致）
- `updatePaidMode` Server Action は `actions.ts` に存在し `SettingsTab.tsx` から正しく import されている
- Alembic マイグレーションの `down_revision` が `"l2m3n4o5p6q7"` で正しい
- 3 つのサーバーコンポーネント（`layout.tsx`, `my/page.tsx`, `races/[id]/page.tsx`）すべてに `fetchPaidMode()` が独立定義されており、`try/catch { return false }` フェイルセーフが実装されている
- コミット `48ddb44`, `c8cf23d`, `273c6d2`, `681c853` がすべて git log で確認済み

---

_Verified: 2026-04-05T12:00:00Z_
_Verifier: Claude (gsd-verifier)_
