# Phase 17: 設定タブ + PAID_MODE DB化 - Research

**Researched:** 2026-04-05
**Domain:** FastAPI / SQLAlchemy 2.0 / Alembic / Next.js 16 App Router — PAID_MODE DB化・設定タブ実装
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| ADMIN-07 | 設定タブでPAID_MODEのON/OFFをUI上で切り替えられる | `SettingsTab.tsx` がプレースホルダー（"設定タブは準備中です"）として存在。`AdminTabs.tsx` 内の `activeTab === "settings" && <SettingsTab />` に差し込むだけで済む。設定APIへの書き込みは `actions.ts` の Server Action 経由で行う。 |
| ADMIN-08 | PAID_MODEがDBで管理され、フロントエンドが起動時にAPIから動的取得する | 現在は `NEXT_PUBLIC_PAID_MODE` 環境変数（ビルド時埋め込み）で制御。`keiba.app_settings` テーブル新設 + `GET/PUT /api/admin/settings` エンドポイント + Next.js Route Handler（`/api/settings/paid-mode`）で起動時取得に移行する。影響箇所は3ファイル：`PaywallGate.tsx`・`layout.tsx`・`my/page.tsx`。 |
</phase_requirements>

---

## Summary

Phase 17 は「PAID_MODE の管理をビルド時環境変数からDB管理へ移行する」フェーズ。変更は4層に渡る：（1）DBに `keiba.app_settings` テーブルを新設し Alembic マイグレーションを作成、（2）バックエンドに `GET/PUT /api/admin/settings` エンドポイントを追加、（3）フロントエンドに Next.js Route Handler（`/api/settings/paid-mode`）を追加してサーバー側でDB値を取得、（4）`SettingsTab.tsx` にトグル UI を実装する。

最も注意すべきアーキテクチャ上の変化は、**`NEXT_PUBLIC_PAID_MODE` 環境変数の使用箇所がビルド時バンドルに埋め込まれている**点。`PaywallGate.tsx` は `"use client"` であるため `process.env.NEXT_PUBLIC_*` はビルド時に文字列として焼き込まれる。DBの値を使うには **サーバーコンポーネント側でDB値を取得して props として渡す**か、**Client Component がAPI経由で取得する**かのどちらかが必要。`layout.tsx` と `my/page.tsx` はサーバーコンポーネントなので API 経由での取得が自然。`PaywallGate.tsx` は props 受け取り型（`paywallEnabled: boolean`）に変更することでクライアントバンドルへの環境変数依存を排除する。

`my/page.tsx` の `paidMode` もサーバーコンポーネント内の API 呼び出しに切り替えることで、DBの値がリアルタイムに反映されるようになる（リクエストごとに `no-store` で取得）。

**Primary recommendation:** `keiba.app_settings` テーブルをシンプルなキーバリューストア（key TEXT PK, value TEXT）として設計し、`PAID_MODE` を `"true"/"false"` で管理する。バックエンドは `admin_router`（`users.py`）に2エンドポイントを追加。フロントエンドはサーバーコンポーネント（`layout.tsx`・`my/page.tsx`）が Route Handler 経由でDB値を取得し、`PaywallGate.tsx` には `paywallEnabled` prop を追加して環境変数依存を除去する。

---

## 現在のPAID_MODE実装（変更前の状態）

### 環境変数での管理

現在 `NEXT_PUBLIC_PAID_MODE` という環境変数を使用。値は `.env.local` に `NEXT_PUBLIC_PAID_MODE=false` として設定されており、ビルド時にバンドルに焼き込まれる。

### 影響を受けるファイル（変更前）

| ファイル | 使用箇所 | 変更方針 |
|--------|---------|---------|
| `frontend/src/components/PaywallGate.tsx` | `process.env.NEXT_PUBLIC_PAID_MODE === "true"` → `paywallEnabled` | props 受け取りに変更 |
| `frontend/src/app/layout.tsx` | `{process.env.NEXT_PUBLIC_PAID_MODE === "true" && <Footer />}` | API 呼び出しで `paidMode` を取得 |
| `frontend/src/app/my/page.tsx` | `const paidMode = process.env.NEXT_PUBLIC_PAID_MODE === "true"` | API 呼び出しで取得 |

### PaywallGate の使用箇所

`PaywallGate.tsx` は `"use client"` コンポーネントで、`RaceDetailClient.tsx` から呼ばれている。現在は `isPremium` と `raceNumber` の2 props を受け取る。`paywallEnabled` を第3の prop として追加し、呼び出し側（`RaceDetailClient.tsx`）から渡す設計にする。`RaceDetailClient.tsx` はサーバーコンポーネント（`races/[id]/page.tsx`）から呼ばれるため、サーバー側で `paidMode` を取得して渡すことが可能。

---

## Standard Stack

### Core（既存プロジェクトスタック）

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Next.js | 16.2.1 | App Router / Server Components / Route Handler | プロジェクト標準 |
| React | 19.2.4 | UI + `useState` / `useTransition` | プロジェクト標準 |
| Tailwind CSS | v4 | スタイリング | プロジェクト標準 |
| FastAPI | 最新 | バックエンド API | プロジェクト標準 |
| SQLAlchemy | 2.0 async | DB アクセス | プロジェクト標準 |
| Pydantic | v2 | スキーマ検証 | プロジェクト標準 |
| Alembic | 最新 | DBマイグレーション | プロジェクト標準（必須） |

### 追加不要

新規パッケージは不要。既存スタックで完結できる。

---

## Architecture Patterns

### 全体の変更箇所マップ

```
backend/src/
├── db/models.py                        ← AppSettings モデルを追加
├── api/users.py                        ← GET/PUT /api/admin/settings エンドポイントを追加
└── main.py                             ← 変更なし（users_admin_router は登録済み）

backend/alembic/versions/
└── m3n4o5p6q7r8_add_app_settings.py   ← 新規マイグレーション

frontend/src/
├── app/api/settings/
│   └── paid-mode/route.ts              ← 新規 Route Handler（認証不要・公開）
├── app/api/admin/settings/
│   └── route.ts                        ← 新規 Route Handler（admin 認証）
├── app/admin/
│   ├── SettingsTab.tsx                 ← プレースホルダーから実装に置き換え
│   └── actions.ts                      ← updatePaidMode Server Action を追加
├── app/layout.tsx                      ← API 経由で paidMode を取得
├── app/my/page.tsx                     ← API 経由で paidMode を取得
├── app/races/[id]/page.tsx             ← paidMode をサーバー側で取得・RaceDetailClient に渡す
├── components/PaywallGate.tsx          ← paywallEnabled prop を追加・環境変数依存を除去
└── components/RaceDetailClient.tsx     ← paywallEnabled prop を受け取り PaywallGate に渡す
```

### Pattern 1: keiba.app_settings テーブル設計

シンプルなキーバリューストア。将来の設定追加にも対応できる汎用的な設計。

```python
# backend/src/db/models.py に追加
class AppSettings(Base):
    """アプリケーション設定テーブル。キーバリュー形式で設定値を管理する。"""

    __tablename__ = "app_settings"
    __table_args__ = {"schema": SCHEMA}

    key: Mapped[str] = mapped_column(String(100), primary_key=True, comment="設定キー（例: PAID_MODE）")
    value: Mapped[str] = mapped_column(String(500), nullable=False, comment="設定値（文字列）")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment="最終更新日時",
    )
    updated_by: Mapped[str | None] = mapped_column(String(100), comment="更新者メールアドレス")
```

初期データ: `PAID_MODE` キーに `"false"` を insert するマイグレーションも同時に追加する。

### Pattern 2: Alembic マイグレーション

既存パターン（`l2m3n4o5p6q7_add_race_recommendations.py`）に倣って作成。`down_revision` は `l2m3n4o5p6q7` を指定。

```python
# backend/alembic/versions/m3n4o5p6q7r8_add_app_settings.py
revision: str = "m3n4o5p6q7r8"
down_revision: str = "l2m3n4o5p6q7"

def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(100), primary_key=True, comment="設定キー"),
        sa.Column("value", sa.String(500), nullable=False, comment="設定値"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", sa.String(100), nullable=True),
        schema="keiba",
    )
    # 初期データ: PAID_MODE=false
    op.execute("INSERT INTO keiba.app_settings (key, value) VALUES ('PAID_MODE', 'false')")

def downgrade() -> None:
    op.drop_table("app_settings", schema="keiba")
```

### Pattern 3: バックエンド設定エンドポイント

`admin_router`（`users.py`）に2エンドポイントを追加。既存の `ApiKeyDep` 認証を使用。

```python
# backend/src/api/users.py に追加

# --- Pydantic スキーマ ---
class AppSettingResponse(BaseModel):
    """設定レスポンス。"""
    key: str
    value: str
    updated_at: datetime | None = None
    updated_by: str | None = None

class AppSettingsResponse(BaseModel):
    """全設定レスポンス。"""
    settings: list[AppSettingResponse]

class UpdateSettingRequest(BaseModel):
    """設定更新リクエスト。"""
    key: str
    value: str

# --- エンドポイント ---
@admin_router.get("/settings", response_model=AppSettingsResponse)
async def get_settings(_: ApiKeyDep, db: DbDep) -> AppSettingsResponse:
    """全アプリ設定を取得する。"""
    from ..db.models import AppSettings
    result = await db.execute(select(AppSettings).order_by(AppSettings.key))
    rows = result.scalars().all()
    return AppSettingsResponse(
        settings=[
            AppSettingResponse(
                key=row.key,
                value=row.value,
                updated_at=row.updated_at,
                updated_by=row.updated_by,
            )
            for row in rows
        ]
    )

@admin_router.put("/settings")
async def update_setting(body: UpdateSettingRequest, _: ApiKeyDep, db: DbDep) -> AppSettingResponse:
    """設定値を更新（または挿入）する。UPSERT 方式。"""
    from ..db.models import AppSettings
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = (
        pg_insert(AppSettings)
        .values(key=body.key, value=body.value)
        .on_conflict_do_update(
            index_elements=["key"],
            set_={"value": body.value, "updated_at": datetime.now(UTC)},
        )
        .returning(AppSettings)
    )
    result = await db.execute(stmt)
    row = result.scalar_one()
    await db.commit()
    return AppSettingResponse(key=row.key, value=row.value, updated_at=row.updated_at, updated_by=row.updated_by)
```

**注意:** PostgreSQL の UPSERT（`INSERT ... ON CONFLICT DO UPDATE`）を使う。SQLAlchemy 2.0 + asyncpg では `postgresql.insert` の `.returning()` が使用可能。

### Pattern 4: フロントエンド Route Handler（公開向け）

`/api/settings/paid-mode` は認証不要のパブリックエンドポイント。サーバーコンポーネントが `INTERNAL_API_KEY` を使わずに呼べるように Next.js Route Handler 経由にする（APIキーはサーバー側のみで使用）。

```typescript
// frontend/src/app/api/settings/paid-mode/route.ts
import { NextResponse } from "next/server";

const BACKEND_URL =
  process.env.BACKEND_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";
const API_KEY = process.env.INTERNAL_API_KEY ?? "";

export async function GET() {
  try {
    const res = await fetch(`${BACKEND_URL}/admin/settings`, {
      headers: { "X-API-Key": API_KEY },
      cache: "no-store",
    });
    if (!res.ok) {
      // バックエンドが落ちている場合はフェイルセーフ（false）
      return NextResponse.json({ paid_mode: false });
    }
    const data = await res.json() as { settings: { key: string; value: string }[] };
    const paidModeSetting = data.settings.find((s) => s.key === "PAID_MODE");
    const paidMode = paidModeSetting?.value === "true";
    return NextResponse.json({ paid_mode: paidMode }, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json({ paid_mode: false });
  }
}
```

**フェイルセーフ設計:** バックエンドが応答しない場合は `paid_mode: false`（ペイウォール無効）を返す。サービス障害時に一般ユーザーがブロックされるのを防ぐ。

### Pattern 5: 管理画面向け Route Handler（admin 認証付き）

```typescript
// frontend/src/app/api/admin/settings/route.ts
import { auth } from "@/auth";
import { NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000/api";
const API_KEY = process.env.INTERNAL_API_KEY ?? "";

export async function GET() {
  const session = await auth();
  if (session?.user?.role !== "admin") {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }
  const res = await fetch(`${BACKEND_URL}/admin/settings`, {
    headers: { "X-API-Key": API_KEY },
    cache: "no-store",
  });
  if (!res.ok) return NextResponse.json({ error: "Backend error" }, { status: 502 });
  return NextResponse.json(await res.json());
}

export async function PUT(req: Request) {
  const session = await auth();
  if (session?.user?.role !== "admin") {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }
  const body = await req.json();
  const res = await fetch(`${BACKEND_URL}/admin/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
    body: JSON.stringify(body),
  });
  if (!res.ok) return NextResponse.json({ error: "Backend error" }, { status: 502 });
  return NextResponse.json(await res.json());
}
```

### Pattern 6: layout.tsx のサーバー側 paidMode 取得

`layout.tsx` はサーバーコンポーネント（`async function RootLayout`）なので、直接バックエンドを叩ける。ただし `INTERNAL_API_KEY` を使うパターンより、**自サーバーの Route Handler を経由**する方がポート・ホスト設定の問題を避けられる。

```typescript
// frontend/src/app/layout.tsx 変更箇所
async function fetchPaidMode(): Promise<boolean> {
  try {
    // Route Handler（/api/settings/paid-mode）を経由する
    const res = await fetch(
      `${process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000"}/api/settings/paid-mode`,
      { cache: "no-store" }
    );
    if (!res.ok) return false;
    const data = await res.json() as { paid_mode: boolean };
    return data.paid_mode;
  } catch {
    return false;
  }
}

// RootLayout 内で:
const paidMode = await fetchPaidMode();
// ...
{paidMode && <Footer />}
```

**代替案:** `layout.tsx` から直接 `BACKEND_URL` + `INTERNAL_API_KEY` でバックエンドを叩く。こちらの方がシンプルで Route Handler 不要。`my/page.tsx` や `races/[id]/page.tsx` も同様のパターンを採用しているため一貫性がある。

**推奨:** `layout.tsx` と `my/page.tsx` は **直接バックエンドを叩くパターン**（`BACKEND_URL` + `INTERNAL_API_KEY`）を採用する。Route Handler は管理画面の `SettingsTab.tsx`（Client Component）が使う場合のみ作成する。これにより自己参照の複雑さを避けられる。

### Pattern 7: PaywallGate.tsx の変更

`paywallEnabled` を外から受け取るよう変更。環境変数依存を完全に除去。

```typescript
// frontend/src/components/PaywallGate.tsx
type Props = {
  isPremium: boolean;
  raceNumber: number;
  paywallEnabled: boolean;  // 追加（サーバー側から渡す）
  children: React.ReactNode;
};

export function PaywallGate({ isPremium, raceNumber, paywallEnabled, children }: Props) {
  // const paywallEnabled = process.env.NEXT_PUBLIC_PAID_MODE === "true";  // 削除
  const isFree = !paywallEnabled || isPremium || raceNumber === 1;
  // ...
}
```

### Pattern 8: SettingsTab.tsx の実装

```tsx
"use client";

import { useState, useEffect, useTransition } from "react";
import { updatePaidMode } from "./actions";

export function SettingsTab() {
  const [paidMode, setPaidMode] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [isPending, startTransition] = useTransition();

  async function loadSettings() {
    setLoading(true);
    try {
      const res = await fetch("/api/admin/settings");
      if (res.ok) {
        const data = await res.json() as { settings: { key: string; value: string }[] };
        const pm = data.settings.find((s) => s.key === "PAID_MODE");
        setPaidMode(pm?.value === "true");
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadSettings(); }, []);

  function handleToggle() {
    if (paidMode === null) return;
    const newValue = !paidMode;
    startTransition(async () => {
      const result = await updatePaidMode(newValue);
      if (result.error) {
        alert(`更新に失敗しました: ${result.error}`);
      } else {
        setPaidMode(newValue);
      }
    });
  }

  if (loading) return <div className="py-8 text-center text-gray-400 text-sm">読み込み中...</div>;

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h3 className="text-sm font-bold text-[#0d1f35] mb-1">有料モード (PAID_MODE)</h3>
        <p className="text-xs text-gray-500 mb-4">
          ONにするとペイウォールが有効になり、非プレミアムユーザーは1R目のみ閲覧できます。
        </p>
        <button
          onClick={handleToggle}
          disabled={isPending || paidMode === null}
          className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none disabled:opacity-40 ${
            paidMode ? "bg-[#1a5c38]" : "bg-gray-300"
          }`}
          role="switch"
          aria-checked={paidMode ?? false}
        >
          <span
            className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
              paidMode ? "translate-x-6" : "translate-x-1"
            }`}
          />
        </button>
        <span className="ml-3 text-sm text-gray-700">
          {paidMode ? "有効（ペイウォールON）" : "無効（ペイウォールOFF）"}
        </span>
      </div>
    </div>
  );
}
```

### Pattern 9: actions.ts への updatePaidMode 追加

```typescript
// frontend/src/app/admin/actions.ts に追加
export async function updatePaidMode(enabled: boolean): Promise<{ error?: string }> {
  const res = await fetch(`${BACKEND_URL}/admin/settings`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
    },
    body: JSON.stringify({ key: "PAID_MODE", value: enabled ? "true" : "false" }),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    return { error: (body as { detail?: string }).detail ?? "更新に失敗しました" };
  }
  return {};
}
```

### 推奨ファイル構成（変更ファイル一覧）

```
backend/src/
├── db/models.py                              # AppSettings クラスを追加
├── api/users.py                              # AppSettingResponse / AppSettingsResponse / UpdateSettingRequest
│                                             # + GET /api/admin/settings
│                                             # + PUT /api/admin/settings
└── alembic/versions/
    └── m3n4o5p6q7r8_add_app_settings.py     # 新規マイグレーション

frontend/src/
├── app/api/admin/settings/route.ts           # 新規 Route Handler（admin 認証）
├── app/admin/
│   ├── SettingsTab.tsx                       # プレースホルダー → 実装
│   └── actions.ts                            # updatePaidMode 追加
├── app/layout.tsx                            # fetchPaidMode() 追加・環境変数削除
├── app/my/page.tsx                           # fetchPaidMode() API 経由に変更
├── app/races/[id]/page.tsx                   # paidMode 取得・RaceDetailClient に渡す
├── components/PaywallGate.tsx                # paywallEnabled prop 追加・env 変数削除
└── components/RaceDetailClient.tsx           # paywallEnabled prop 追加・渡す
```

### Anti-Patterns to Avoid

- **`NEXT_PUBLIC_PAID_MODE` をビルド時環境変数として残す:** `NEXT_PUBLIC_` プレフィックスはブラウザバンドルに焼き込まれる。DBの値に変更してもビルドしなければ変わらない。除去が必須。
- **`PaywallGate.tsx` 内で API を直接 fetch する:** `"use client"` Component が起動時に毎回 fetch するとユーザーごとに余分なリクエストが発生し、SSR のメリットが失われる。サーバー側（`layout.tsx` 等）で取得して props で渡す。
- **バックエンドの `GET /api/admin/settings` を認証なしにする:** 設定値は管理者のみが参照すべき。ただし公開向けの `paid_mode` フラグは Next.js Route Handler（`/api/settings/paid-mode`）でラップして認証不要で提供する（値は `true/false` のみ）。
- **PUT エンドポイントで INSERT のみを使う:** テーブル新設時に初期データが既に入っているため、2回目以降の更新で一意制約エラーになる。PostgreSQL の UPSERT（`INSERT ... ON CONFLICT DO UPDATE`）を使う。
- **`layout.tsx` の `paidMode` 取得で自己参照 Route Handler を呼ぶ:** `layout.tsx` がレンダリング中に自分の `/api/settings/paid-mode` を呼ぶと localhost:3000 への HTTP リクエストが発生する。コンテナ環境では名前解決の問題が起きることがある。**直接バックエンドを叩く**方が安全。

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| キーバリューの UPSERT | INSERT + UPDATE の分岐ロジック | PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` | 原子的で競合しない |
| 設定テーブルの ORM | Raw SQL | SQLAlchemy 2.0 mapped_column | 型安全・Alembic autogenerate 対応 |
| トグルUI | カスタムCSSトグル from scratch | Tailwind transform + translate-x パターン | プロジェクトで使われているシンプルな実装 |
| バックエンド認証 | 独自認証 | 既存 `ApiKeyDep` / `verify_api_key` | Phase 16 と同じパターン |

---

## Common Pitfalls

### Pitfall 1: `NEXT_PUBLIC_PAID_MODE` の削除漏れ

**What goes wrong:** `PaywallGate.tsx` や `my/page.tsx` で `process.env.NEXT_PUBLIC_PAID_MODE` を削除し忘れると、ビルド時に `"false"` が焼き込まれ、DBを `true` に更新してもペイウォールが有効にならない。
**Why it happens:** `NEXT_PUBLIC_*` は Next.js のビルド時にインライン展開される（`process.env.NEXT_PUBLIC_PAID_MODE` → 文字列リテラル）。DB変更は実行時のため無視される。
**How to avoid:** 全3箇所（`PaywallGate.tsx`・`layout.tsx`・`my/page.tsx`）の環境変数参照を削除し、props または API 呼び出しに置き換える。

### Pitfall 2: SettingsTab のトグルが反映されるのは「次回アクセス時」

**What goes wrong:** 管理者が PAID_MODE を ON にしても、既にブラウザを開いているユーザーにはすぐ反映されない（サーバーコンポーネントはページロード時にしか実行されない）。
**Why it happens:** `layout.tsx` の `paidMode` はリクエストごとに取得されるが、SPA 遷移中は再取得しない。
**How to avoid:** 成功条件 4「フロントエンドの**次回アクセス時**にペイウォールが有効化される」と要件に明記されており、これで要件を満たす。リアルタイム反映（WebSocket 等）は対象外。UI に「次回アクセス時から反映されます」と説明文を添える。

### Pitfall 3: `layout.tsx` の自己 fetch による循環・遅延

**What goes wrong:** `layout.tsx` の `fetchPaidMode()` が `http://localhost:3000/api/settings/paid-mode`（自分自身）を呼ぶと、コンテナ内では `localhost:3000` が解決できずにタイムアウトする場合がある。
**Why it happens:** Docker コンテナ内では `localhost` は自コンテナを指すが、Next.js サーバーが起動していない段階では接続できない（startup 時など）。
**How to avoid:** `layout.tsx`・`my/page.tsx`・`races/[id]/page.tsx` はサーバーコンポーネントなので **直接 `BACKEND_URL` + `INTERNAL_API_KEY` でバックエンドを叩く**。Route Handler（`/api/settings/paid-mode`）は `SettingsTab.tsx`（Client Component）が使う場合のみ。

### Pitfall 4: Alembic `down_revision` の指定ミス

**What goes wrong:** `down_revision` を誤ったリビジョンに指定すると、`alembic upgrade head` でマイグレーションの順序が壊れる。
**Why it happens:** リビジョン ID が手書きのため、最新リビジョンの確認漏れ。
**How to avoid:** `alembic current` で現在の head を確認してから新規マイグレーションを作成する。現在の最新は `l2m3n4o5p6q7`（`add_race_recommendations`）。

### Pitfall 5: PUT エンドポイントの returning() 非対応

**What goes wrong:** `INSERT ... ON CONFLICT DO UPDATE ... RETURNING *` は asyncpg + SQLAlchemy 2.0 で `.returning()` が使える。しかし `scalar_one()` は `RETURNING` なしの場合 `NoResultFound` を起こす。
**Why it happens:** UPSERT の `.returning()` 結果が空になるエッジケース（影響行ゼロ）はないが、コーディングミスで `returning()` を忘れると例外が出る。
**How to avoid:** UPSERT の後に `result = await db.execute(select(AppSettings).where(AppSettings.key == body.key))` で別途取得するシンプルな方式でも可（パフォーマンスは問題なし）。

---

## Code Examples

### SQLAlchemy 2.0 PostgreSQL UPSERT パターン

```python
# Source: SQLAlchemy 2.0 ドキュメント / postgresql dialect
from sqlalchemy.dialects.postgresql import insert as pg_insert

# UPSERT（既存なら UPDATE、なければ INSERT）
stmt = (
    pg_insert(AppSettings)
    .values(key=body.key, value=body.value, updated_at=datetime.now(UTC))
    .on_conflict_do_update(
        index_elements=["key"],
        set_={"value": body.value, "updated_at": datetime.now(UTC)},
    )
)
await db.execute(stmt)
await db.commit()

# 更新後のレコードを SELECT で取得（RETURNING が不要な方式）
result = await db.execute(select(AppSettings).where(AppSettings.key == body.key))
row = result.scalar_one()
```

### Next.js Route Handler パターン（Phase 16 で確立済み）

```typescript
// Source: frontend/src/app/api/admin/data-coverage/route.ts（Phase 16 実装）
import { auth } from "@/auth";
import { NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000/api";
const API_KEY = process.env.INTERNAL_API_KEY ?? "";

export async function GET() {
  const session = await auth();
  if (session?.user?.role !== "admin") {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }
  const res = await fetch(`${BACKEND_URL}/admin/settings`, {
    headers: { "X-API-Key": API_KEY },
    cache: "no-store",
  });
  if (!res.ok) return NextResponse.json({ error: "Backend error" }, { status: 502 });
  return NextResponse.json(await res.json());
}
```

### paidMode をサーバーコンポーネントで取得するパターン

```typescript
// layout.tsx / my/page.tsx / races/[id]/page.tsx 共通パターン
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000/api";
const API_KEY = process.env.INTERNAL_API_KEY ?? "";

async function fetchPaidMode(): Promise<boolean> {
  try {
    const res = await fetch(`${BACKEND_URL}/admin/settings`, {
      headers: { "X-API-Key": API_KEY },
      cache: "no-store",
    });
    if (!res.ok) return false;
    const data = await res.json() as { settings: { key: string; value: string }[] };
    return data.settings.find((s) => s.key === "PAID_MODE")?.value === "true";
  } catch {
    return false;
  }
}
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `NEXT_PUBLIC_PAID_MODE` 環境変数（ビルド時） | `keiba.app_settings` テーブル + API（実行時） | Phase 17 | ビルドなしで PAID_MODE の ON/OFF 切り替えが可能に |
| SettingsTab.tsx プレースホルダー | トグル UI 実装 | Phase 17 | 管理者が UI から PAID_MODE を制御可能に |
| PaywallGate が env 変数を直接参照 | props 経由で受け取る | Phase 17 | サーバーコンポーネントからの値注入が可能に |

**Deprecated/outdated:**
- `NEXT_PUBLIC_PAID_MODE` 環境変数: Phase 17 完了後は削除。`.env.local` からも除去してよい（残っていても `NEXT_PUBLIC_` 参照コードがなくなれば影響なし）。

---

## Open Questions

1. **`layout.tsx` からの `fetchPaidMode()` 呼び出し：直接バックエンド vs. 自己 Route Handler**
   - What we know: `layout.tsx` はサーバーコンポーネント。直接 `BACKEND_URL` を叩けるが、`INTERNAL_API_KEY` が `layout.tsx` に書かれる。
   - What's unclear: `my/page.tsx` も同様のパターンを採用しており一貫性はある。
   - Recommendation: **直接バックエンドを叩く**（`BACKEND_URL` + `INTERNAL_API_KEY`）。`INTERNAL_API_KEY` は他のサーバーコンポーネントでも使われているため問題なし。

2. **`NEXT_PUBLIC_PAID_MODE` の削除タイミング**
   - What we know: 環境変数を残しても（参照コードが削除されれば）動作には影響しない。
   - Recommendation: Phase 17 完了時に `.env.local` から削除し、コード上のすべての参照を除去する。本番 `.env` からも削除してデプロイすること（デプロイ手順のメモが必要）。

3. **`races/[id]/page.tsx` の `paidMode` 取得コスト**
   - What we know: レース詳細ページはアクセスのたびにバックエンドを叩く（`no-store`）。`app_settings` テーブルは小さいので高速。
   - What's unclear: 高トラフィック時の余分な DB クエリ。
   - Recommendation: Phase 17 のスコープでは最適化不要。`app_settings` 取得は単純な primary key lookup（数ms以下）。将来必要なら Redis キャッシュを検討。

---

## 変更箇所サマリ（プラン作成用）

### バックエンド変更（Plan 17-01 推奨）

1. `backend/src/db/models.py` — `AppSettings` ORM クラスを追加
2. `backend/alembic/versions/m3n4o5p6q7r8_add_app_settings.py` — 新規マイグレーション（テーブル作成 + `PAID_MODE=false` 初期データ）
3. `backend/src/api/users.py` — `AppSettingResponse`・`AppSettingsResponse`・`UpdateSettingRequest` スキーマ + `GET /api/admin/settings`・`PUT /api/admin/settings` エンドポイント
4. `alembic upgrade head` — 本番 DB への適用（SUMMARY に記録）

### フロントエンド変更（Plan 17-02 推奨）

5. `frontend/src/app/api/admin/settings/route.ts` — 新規 Route Handler（GET/PUT、admin 認証）
6. `frontend/src/app/admin/SettingsTab.tsx` — プレースホルダーから実装に置き換え（トグル UI）
7. `frontend/src/app/admin/actions.ts` — `updatePaidMode()` Server Action を追加
8. `frontend/src/components/PaywallGate.tsx` — `paywallEnabled` prop 追加、`NEXT_PUBLIC_PAID_MODE` 参照削除
9. `frontend/src/components/RaceDetailClient.tsx` — `paywallEnabled` prop 追加・`PaywallGate` へ渡す
10. `frontend/src/app/races/[id]/page.tsx` — サーバー側で `paidMode` 取得・`RaceDetailClient` に渡す
11. `frontend/src/app/layout.tsx` — サーバー側で `paidMode` 取得・`Footer` 表示制御
12. `frontend/src/app/my/page.tsx` — サーバー側で `paidMode` 取得（env 変数参照削除）

---

## Sources

### Primary (HIGH confidence)

- 直接コード調査: `frontend/src/components/PaywallGate.tsx` — 環境変数 `NEXT_PUBLIC_PAID_MODE` 参照箇所
- 直接コード調査: `frontend/src/app/layout.tsx` — `NEXT_PUBLIC_PAID_MODE` 参照箇所
- 直接コード調査: `frontend/src/app/my/page.tsx` — `NEXT_PUBLIC_PAID_MODE` 参照箇所
- 直接コード調査: `frontend/src/app/admin/SettingsTab.tsx` — プレースホルダー確認
- 直接コード調査: `frontend/src/app/admin/AdminTabs.tsx` — タブ構成・SettingsTab 呼び出し確認
- 直接コード調査: `frontend/src/app/admin/actions.ts` — Server Action パターン確認
- 直接コード調査: `frontend/src/app/api/admin/data-coverage/route.ts` — Route Handler パターン確認（Phase 16 実装）
- 直接コード調査: `backend/src/api/users.py` — `admin_router` / `ApiKeyDep` / Pydantic スキーマパターン確認
- 直接コード調査: `backend/src/db/models.py` — 既存モデル構造・`SCHEMA` 定数確認
- 直接コード調査: `backend/src/main.py` — ルーター登録状況確認（`users_admin_router` 登録済み）
- 直接コード調査: `backend/src/config.py` — Settings クラス・`change_notify_api_key` 確認
- 直接コード調査: `backend/alembic/versions/l2m3n4o5p6q7_add_race_recommendations.py` — Alembic パターン確認

### Secondary (MEDIUM confidence)

- `.planning/REQUIREMENTS.md` — ADMIN-07/08 詳細要件
- `.planning/ROADMAP.md` — Phase 17 成功条件
- `.planning/STATE.md` — 「PAID_MODEをDBで管理する方針（keiba.app_settingsテーブル新設）」決定確認

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — 既存プロジェクトコードから直接確認
- Architecture: HIGH — Phase 16 の確立済みパターン（Route Handler / Server Action / admin_router）を踏襲
- DB設計: HIGH — 既存 Alembic パターンに倣ったシンプルなキーバリューテーブル
- 影響箇所: HIGH — `NEXT_PUBLIC_PAID_MODE` の全参照箇所をコード検索で確認済み（3ファイル）
- Pitfalls: HIGH — ビルド時焼き込み問題・自己参照 fetch 問題を具体的に特定

**Research date:** 2026-04-05
**Valid until:** 2026-05-05（プロジェクト内変更がなければ安定）
