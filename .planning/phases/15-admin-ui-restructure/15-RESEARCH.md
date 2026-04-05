# Phase 15: Admin UI再構成 - Research

**Researched:** 2026-04-05
**Domain:** Next.js 16 App Router / React 19 / Tailwind CSS v4 — フロントエンド管理画面リファクタリング
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| ADMIN-01 | 管理画面がタブUI（ユーザー / データ / 設定）に再構成される | タブナビ実装パターン（YosoTabNav参照）。クライアントサイド state + URL searchParams の2方式を調査。 |
| ADMIN-02 | 登録ユーザーテーブルの各行が1行表示（whitespace-nowrap + テーブル内横スクロール） | 現状の `overflow-x-auto` ラッパーは既存。行レベルの `whitespace-nowrap` 適用が未完全なことを確認。 |
| ADMIN-03 | 登録ユーザーテーブルが10件ページングで閲覧できる | ページングはサーバーコンポーネント + URL searchParams が適切。Server Action不使用。 |
| ADMIN-04 | 登録ユーザーテーブルに予想家名（yoso_name）・公開設定（is_yoso_public）の列が追加される | DBモデルに両フィールド確認済み。バックエンドAPIレスポンス（UserResponse）への追加が必要。 |
</phase_requirements>

---

## Summary

Phase 15 は純粋なフロントエンド改善フェーズ。既存の `/admin/page.tsx`（Server Component）を「ユーザー / データ / 設定」の3タブ構成に再編し、ユーザーテーブルの表示品質を向上させる。データとバックエンドロジックは大きく変わらないが、バックエンドの `UserResponse` スキーマに `yoso_name` / `is_yoso_public` を追加する必要がある。

タブUIは `/yoso` レイアウトで採用済みの `YosoTabNav` パターンが参照実装として使える。管理画面は特殊なレイアウト要件（幅広テーブル）があるため `max-w-6xl` を維持する。ページングは Next.js 16 App Router の URL searchParams 経由が最適（Server Component との相性、`revalidatePath` 不要）。

**Primary recommendation:** タブはクライアントコンポーネントの `useState` で実装する。`/admin` は単一URLのままで良い（Phase 16 でデータタブ機能が増えるため、サブルート分割は Phase 16 時に判断）。

---

## Standard Stack

### Core（既存プロジェクトスタック）
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Next.js | 16.2.1 | App Router / Server Components | プロジェクト標準 |
| React | 19.2.4 | UI | プロジェクト標準 |
| Tailwind CSS | v4 | スタイリング | プロジェクト標準 |
| next-auth | 5.0.0-beta.30 | セッション認証 | プロジェクト標準 |
| lucide-react | ^0.577.0 | アイコン | プロジェクト標準 |

### shadcn/ui について
`package.json` に shadcn/ui の依存は**存在しない**。Radix UI も未使用。タブUIはゼロから実装するか、既存 `YosoTabNav` パターンを流用する。

### 追加不要
ページングもタブも外部ライブラリを使わず Tailwind + React で実装できる。新パッケージ追加は不要。

---

## Architecture Patterns

### 現状の `/admin/page.tsx` 構造
```
/admin/page.tsx  — Server Component（auth() チェック + fetchUsers() + fetchInvitationCodes()）
/admin/actions.ts — Server Actions（updateUser, createInvitationCode, toggleInvitationCode, grantUserAccess）
```

### 推奨: タブUIの実装方式

**方式A（推奨）: クライアントコンポーネントによる `useState` タブ**

`/admin/page.tsx` をそのままServer Componentとして維持し、データ取得後に Client Component（`AdminTabs`）へデータを渡す。タブ切り替えはクライアントの `useState` で制御。

```
/admin/page.tsx         — Server Component（auth + fetch）
/admin/AdminTabs.tsx    — "use client" タブコンテナ（useState でアクティブタブ管理）
/admin/UsersTab.tsx     — ユーザーテーブル（Client Component、ページング state 含む）
/admin/CodesTab.tsx     — 招待コードテーブル（既存ロジックを移動）
/admin/DataTab.tsx      — プレースホルダー（Phase 16 で実装）
/admin/SettingsTab.tsx  — プレースホルダー（Phase 17 で実装）
/admin/actions.ts       — 変更なし
```

**方式B: URL searchParams `?tab=users`**

URL に状態を持つのでリロード・ブックマーク対応できる。しかし管理画面は常時使用ではなく、Phase 16/17 でタブが充実するまでは over-engineering になる。Phase 16 以降で移行検討。

**Phase 15 は方式Aを採用する。**

### 既存タブ実装の参照パターン（`YosoTabNav`）

```tsx
// /yoso/YosoTabNav.tsx — "use client" + usePathname() + Link
// 管理画面はURLベースではなくstateベースなのでこのパターンは不使用
// ただしタブのスタイルはこちらのクラスを参照できる
className={`text-xs px-3 py-2.5 whitespace-nowrap border-b-2 transition-colors ${
  isActive ? "text-white border-white" : "text-blue-200 hover:text-white border-transparent"
}`}
```

管理画面のタブは白背景に対するダークアクセントカラー（`border-[#0d1f35]`）が適切。

### ページングパターン

ユーザー一覧は全件をServer Componentで取得し、Client Component内で `useState` でスライスする。

```tsx
// UsersTab.tsx の中
const [page, setPage] = useState(0);
const PAGE_SIZE = 10;
const pageUsers = users.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
const totalPages = Math.ceil(users.length / PAGE_SIZE);
```

この実装の利点:
- Server Action / API 追加不要
- ユーザー総数が多くなければクライアントスライスで十分（管理画面で数百件以下と想定）
- `revalidatePath` は変更操作後に引き続き動作する

### 推奨ファイル構成
```
frontend/src/app/admin/
├── page.tsx          # Server Component（認証 + データ取得、変更少）
├── AdminTabs.tsx     # "use client" タブコンテナ（NEW）
├── UsersTab.tsx      # "use client" ユーザーテーブル + ページング（NEW）
├── CodesTab.tsx      # "use client" 招待コード管理（既存 page.tsx から分離）
├── DataTab.tsx       # プレースホルダー（NEW - Phase 16）
├── SettingsTab.tsx   # プレースホルダー（NEW - Phase 17）
└── actions.ts        # 変更なし
```

### Anti-Patterns to Avoid
- **`page.tsx` に全UIを詰め込む:** 既存がこの状態。タブ実装時に Client Component を分離する必要がある。
- **Server Component 内に "use client" を直書きする:** page.tsx はServer Componentのままにし、クライアントUIは別ファイルに切り出す。
- **Form Action のクロージャに大きな状態を渡す:** 既存の Server Action パターン（`action={async () => { "use server"; ... }}`）は現状のまま維持。ただし Server Component からのみ呼び出せるため UsersTab（Client Component）からは actions.ts の export を呼ぶ。

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| タブUI | カスタムタブライブラリ | Tailwind + React state | shadcn/ui 未導入。既存 YosoTabNav パターンで十分 |
| ページング | react-paginate 等 | Tailwind + useState slice | 外部ライブラリ不要な規模 |
| テーブル横スクロール | JS scroll管理 | `overflow-x-auto` + `whitespace-nowrap` | CSS で完結 |

---

## Common Pitfalls

### Pitfall 1: Server Action のクロージャと Client Component の境界
**What goes wrong:** `page.tsx` が Server Component の時、「インライン Server Action」（`action={async () => { "use server"; ... }}`）は Server Component 内でのみ定義できる。UsersTab が "use client" になると、このインラインパターンが使えない。
**Why it happens:** React の "use server" / "use client" 境界ルール。
**How to avoid:** Server Action は `actions.ts` に `export async function` として定義済みなので、Client Component から `import { updateUser } from "../actions"` で呼べる。インライン Server Action は廃止してすべて `actions.ts` 経由に統一する。
**Warning signs:** ビルドエラー「Server actions must be defined in a file with "use server" at the top or a function with "use server" annotation」

### Pitfall 2: `whitespace-nowrap` の適用範囲
**What goes wrong:** `<td>` に `whitespace-nowrap` を付けても、セル内に `<div>` がある場合は効かないことがある。
**How to avoid:** `<td className="... whitespace-nowrap">` の直下の `<div>` にも適用するか、table 全体に `table-fixed` + 各列に `min-w-*` を指定する。現状の実装では日付列のみ `whitespace-nowrap` 付きだが、メール列・名前列が折り返す可能性がある。すべての `<td>` に `whitespace-nowrap` を付ける方針にする。

### Pitfall 3: バックエンド `UserResponse` の未追加フィールド
**What goes wrong:** `yoso_name` / `is_yoso_public` は `User` ORM モデルに存在するが、`UserResponse` Pydantic モデルには**含まれていない**（`users.py` L115-130 確認済み）。
**How to avoid:** バックエンドの `UserResponse` に2フィールドを追加し、`_make_user_response()` で値をセットする必要がある。フロントエンドの型定義（`type User`）も合わせて更新する。

### Pitfall 4: Phase 16/17 向けプレースホルダータブの扱い
**What goes wrong:** データタブ・設定タブの内容が空だとUX的に不完全。
**How to avoid:** プレースホルダーに「準備中」や「Phase 16 で実装」のような表記は不要。空の `<div>` または最小限の「近日公開」テキストで十分。Phase 15 の成功条件はタブが「表示され、切り替えができること」であり、タブ内コンテンツは問わない。

### Pitfall 5: `inline Server Action` から `export` Server Action への移行時のrevalidate
**What goes wrong:** `actions.ts` の `revalidatePath("/admin")` は既に実装されているので問題ない。ただし Client Component から呼ぶ場合は `startTransition` でラップしないと React 19 では警告が出ることがある。
**How to avoid:** Client Component 内で Server Action を呼ぶ場合は `useTransition` を使うか、`<form action={serverAction}>` パターンを維持する。

---

## Code Examples

### AdminTabs.tsx の基本パターン

```tsx
// Source: プロジェクト内 YosoTabNav.tsx を参照し、state ベースに変形
"use client";

import { useState } from "react";
import { UsersTab } from "./UsersTab";
import { CodesTab } from "./CodesTab";
import { DataTab } from "./DataTab";
import { SettingsTab } from "./SettingsTab";

type Tab = "users" | "data" | "settings";

const TABS: { id: Tab; label: string }[] = [
  { id: "users", label: "ユーザー" },
  { id: "data", label: "データ" },
  { id: "settings", label: "設定" },
];

export function AdminTabs({ users, codes }: { users: User[]; codes: InvitationCode[] }) {
  const [activeTab, setActiveTab] = useState<Tab>("users");

  return (
    <div>
      {/* タブナビ */}
      <nav className="flex gap-1 border-b border-gray-200 mb-6">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2.5 text-sm font-medium whitespace-nowrap border-b-2 transition-colors ${
              activeTab === tab.id
                ? "border-[#0d1f35] text-[#0d1f35]"
                : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {/* タブコンテンツ */}
      {activeTab === "users" && <UsersTab users={users} />}
      {activeTab === "data" && <DataTab />}
      {activeTab === "settings" && <SettingsTab />}
    </div>
  );
}
```

### UsersTab.tsx のページングパターン

```tsx
"use client";

import { useState } from "react";
import { updateUser } from "./actions";

const PAGE_SIZE = 10;

export function UsersTab({ users }: { users: User[] }) {
  const [page, setPage] = useState(0);
  const totalPages = Math.ceil(users.length / PAGE_SIZE);
  const pageUsers = users.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          {/* ... thead ... */}
          <tbody>
            {pageUsers.map((user) => (
              <tr key={user.id} className="hover:bg-gray-50 whitespace-nowrap">
                {/* 各 td に whitespace-nowrap を付与 */}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ページングコントロール */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between px-4 py-3 border-t border-gray-100">
          <span className="text-xs text-gray-500">
            {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, users.length)} / {users.length}件
          </span>
          <div className="flex gap-2">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="px-3 py-1 text-xs rounded border border-gray-200 disabled:opacity-40"
            >
              前へ
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page === totalPages - 1}
              className="px-3 py-1 text-xs rounded border border-gray-200 disabled:opacity-40"
            >
              次へ
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
```

### バックエンド UserResponse への追加フィールド

```python
# backend/src/api/users.py — UserResponse に追加
class UserResponse(BaseModel):
    """ユーザーレスポンス"""
    id: int
    email: str
    name: str | None
    image_url: str | None
    role: str
    is_active: bool
    is_premium: bool
    can_input_index: bool
    access_expires_at: datetime | None
    created_at: datetime
    last_login_at: datetime | None
    yoso_name: str | None        # 追加
    is_yoso_public: bool         # 追加

    model_config = {"from_attributes": False}

# _make_user_response() に追記
return UserResponse(
    ...
    yoso_name=user.yoso_name,           # 追加
    is_yoso_public=user.is_yoso_public, # 追加
)
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `page.tsx` に全UI | タブ別 Client Component に分離 | Phase 15 | 保守性向上、Phase 16/17 の拡張容易化 |
| インライン Server Action | `actions.ts` export 関数 | Phase 15 | Client Component から呼び出し可能に |

**Deprecated/outdated:**
- インライン `action={async () => { "use server"; ... }}` パターン: Client Component に移行後は使えない。`actions.ts` の export 関数に統一する。

---

## Open Questions

1. **招待コード管理セクションのタブ配置**
   - What we know: 現状ユーザー管理と招待コード管理が同一ページに縦並び
   - What's unclear: 招待コードを「ユーザー」タブに含めるか、独立したタブにするか
   - Recommendation: 「ユーザー」タブ内のサブセクションとして含める（Phase 15 の成功条件はタブ数を問わない）

2. **`page.tsx` のデータ取得を全タブ分同時に行うか**
   - What we know: 現状 `Promise.all([fetchUsers(), fetchInvitationCodes()])` で並列取得
   - What's unclear: データタブ・設定タブは Phase 16/17 実装なので今は取得不要
   - Recommendation: Phase 15 では現状どおり users + codes の2つだけ取得。プレースホルダータブはデータ不要。

---

## Sources

### Primary (HIGH confidence)
- 直接コード調査: `/frontend/src/app/admin/page.tsx` — 現状の全UIと構造
- 直接コード調査: `/frontend/src/app/admin/actions.ts` — 全Server Actions
- 直接コード調査: `/backend/src/api/users.py` — UserResponse スキーマ（L115-130）
- 直接コード調査: `/backend/src/db/models.py` — User モデル（L525-560）
- 直接コード調査: `/frontend/src/app/yoso/YosoTabNav.tsx` — 既存タブ実装パターン
- 直接コード調査: `/frontend/src/app/yoso/layout.tsx` — タブレイアウト参照
- 直接コード調査: `/frontend/package.json` — 依存パッケージ一覧（shadcn/ui 未使用確認）

### Secondary (MEDIUM confidence)
- `.planning/REQUIREMENTS.md` — ADMIN-01〜04 の詳細要件
- `.planning/ROADMAP.md` — Phase 15 成功条件

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — package.json から直接確認
- Architecture: HIGH — 既存コードを完全把握した上での設計
- Pitfalls: HIGH — コード上の具体的な問題（UserResponse 未追加、インライン Server Action 境界）を特定
- Backend changes: HIGH — models.py と users.py の差分が明確

**Research date:** 2026-04-05
**Valid until:** 2026-05-05（プロジェクト内変更がなければ安定）
