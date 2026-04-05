---
phase: 16-データタブ
verified: 2026-04-05T11:00:00Z
status: passed
score: 7/7 must-haves verified
re_verification: false
---

# Phase 16: データタブ Verification Report

**Phase Goal:** 管理者がデータタブで年/月単位のレースデータ取得状況を確認し、UIから未取得月のデータ取得をWindows Agentへ指示できる
**Verified:** 2026-04-05T11:00:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | GET /api/admin/data-coverage が年/月別レース件数（ゼロ月含む）を JSON で返す | VERIFIED | `backend/src/api/users.py` L278-316: `func.left(Race.date, 6)` で YYYYMM グループ化、全12月ゼロ埋め実装済み |
| 2 | POST /api/admin/fetch-data が year_month を受け取り _command_queue に recent コマンドを積む | VERIFIED | `backend/src/api/users.py` L319-336: バリデーション + `_command_queue.append(entry)` 実装済み |
| 3 | Windows Agent の run_command_loop() が action='recent' を受け取り run_recent() を実行する | VERIFIED | `windows-agent/jvlink_agent.py` L1139-1148: `elif action == "recent"` ブランチで `run_recent(jv, from_year=from_year)` 呼び出し確認 |
| 4 | 管理画面のデータタブで年/月別レース件数グリッドが表示される | VERIFIED | `frontend/src/app/admin/DataTab.tsx`: `useEffect` + `fetch('/api/admin/data-coverage')` + `grid grid-cols-6` グリッドレンダリング実装済み |
| 5 | 件数ゼロの月に「取得」ボタンが表示され、クリックするとその年以降の取得指示がキューに積まれる | VERIFIED | `DataTab.tsx` L79-87: `isEmpty` 条件で「取得」ボタン表示、`handleFetch()` → `triggerFetchData(yearMonth)` 呼び出し確認 |
| 6 | 取得ボタンクリック中は isPending で disabled になり二重送信を防ぐ | VERIFIED | `DataTab.tsx` L82: `disabled={isPending \|\| fetchingMonth !== null}` 実装済み |
| 7 | GET /api/admin/data-coverage（Next.js Route Handler）が admin セッション認証の上でバックエンドへ中継する | VERIFIED | `frontend/src/app/api/admin/data-coverage/route.ts`: `auth()` → `role !== "admin"` → 403、X-API-Key 付きバックエンド中継 + `cache: "no-store"` 実装済み |

**Score:** 7/7 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `backend/src/api/users.py` | GET /data-coverage + POST /fetch-data + Pydantic スキーマ | VERIFIED | L143-168: MonthCoverage/YearCoverage/DataCoverageResponse/FetchDataRequest スキーマ定義。L278-336: 両エンドポイント実装済み |
| `backend/src/api/agent_router.py` | valid_actions に 'recent' を追加 | VERIFIED | L122: `valid_actions = {"setup", "daily", "retry", "stop", "recent"}` 確認 |
| `windows-agent/jvlink_agent.py` | run_command_loop() 内の recent ハンドラー | VERIFIED | L1139-1148: `elif action == "recent"` ブランチ実装済み |
| `frontend/src/app/api/admin/data-coverage/route.ts` | admin セッション認証 + バックエンド中継 Route Handler | VERIFIED | 全実装確認。admin role チェック + X-API-Key 中継 + cache: "no-store" |
| `frontend/src/app/admin/DataTab.tsx` | 月別グリッド表示 + 取得ボタン + useEffect fetch | VERIFIED | 97行の完全実装。プレースホルダーなし |
| `frontend/src/app/admin/actions.ts` | triggerFetchData Server Action | VERIFIED | L82-97: `triggerFetchData` が `POST /admin/fetch-data` を呼ぶ Server Action として実装済み |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `users.py (trigger_fetch_data)` | `agent_router._command_queue` | `_command_queue.append(entry)` | WIRED | L322: `from .agent_router import _command_queue`、L335: `_command_queue.append(entry)` |
| `jvlink_agent.py (run_command_loop)` | `run_recent(jv, from_year)` | `elif action == "recent"` ブランチ | WIRED | L1139-1148: ブランチ存在、`run_recent(jv, from_year=from_year)` 呼び出し確認 |
| `DataTab.tsx` | `/api/admin/data-coverage` | `fetch('/api/admin/data-coverage') in loadCoverage()` | WIRED | L19: `fetch("/api/admin/data-coverage")` 確認 |
| `DataTab.tsx` | `triggerFetchData (actions.ts)` | `handleFetch() → triggerFetchData(yearMonth)` | WIRED | L4: import 確認、L35: `triggerFetchData(yearMonth)` 呼び出し確認 |
| `route.ts` | `BACKEND_URL/admin/data-coverage` | `fetch with X-API-Key header` | WIRED | L14-16: `fetch(${BACKEND_URL}/admin/data-coverage, { headers: { "X-API-Key": API_KEY } })` 確認 |
| `AdminTabs.tsx` | `DataTab` | `activeTab === "data" && <DataTab />` | WIRED | `AdminTabs.tsx` L6: import、L76: レンダリング確認 |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| ADMIN-05 | 16-01, 16-02 | データタブで年/月単位のレースデータ取得状況（件数）を確認できる | SATISFIED | `GET /api/admin/data-coverage` (backend) → Route Handler (Next.js) → `DataTab.tsx` の年/月グリッド表示 |
| ADMIN-06 | 16-01, 16-02 | データタブで未取得月のデータをUIからWindows Agentへ取得指示できる | SATISFIED | 「取得」ボタン → `triggerFetchData()` → `POST /admin/fetch-data` → `_command_queue.append(recent)` → Windows Agent `run_command_loop()` の recent ハンドラー |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| (なし) | - | - | - | - |

プレースホルダー文字列（「準備中」等）、`TODO/FIXME`、スタブ返却、空実装は全ファイルで検出されなかった。

### Human Verification Required

#### 1. 実際のデータ取得フロー統合テスト

**Test:** 管理画面のデータタブを開き、件数ゼロの月の「取得」ボタンをクリックする
**Expected:** confirm ダイアログが表示され、OKをクリックするとボタンが「指示中...」になり、disabled になる。その後 Windows Agent が recent モードで起動し、jvlink_agent.log に "Starting recent mode" のログが出る
**Why human:** Windows Agent の実際の起動とコマンドキューのポーリングはブラウザ/Dockerコンテナ/Windows VMを横断するため、静的検証では確認不可

#### 2. TypeScript ビルド完全性

**Test:** `cd frontend && pnpm build` を実行
**Expected:** ビルドエラーなし
**Why human:** `npx tsc --noEmit` は通過済みだが、Next.js のページ最適化・バンドルエラーは tsc 単体では検出できない場合がある

### Gaps Summary

ギャップなし。全 7 つの観測可能な真実が検証された。

---

## 検証詳細メモ

- `agent_router.py` の `valid_actions` は L122 で `"recent"` を含む集合として定義されており、`POST /api/agent/command` 経由でも `recent` コマンドを受け付けられる（二重の経路）
- `DataTab.tsx` にプレースホルダー文字列「データタブは準備中です」が存在しないことを grep で確認済み
- TypeScript 型チェック（`npx tsc --noEmit`）がエラーゼロで通過
- `fetch-data` エンドポイントは DB セッションを持たない（キューに積むだけ）設計で、PLAN 通りに実装されている
- `datetime.now(UTC)` を使用しタイムゾーン aware な `queued_at` を生成（SUMMARY で言及された決定事項通り）

---

_Verified: 2026-04-05T11:00:00Z_
_Verifier: Claude (gsd-verifier)_
