# Phase 16: データタブ - Research

**Researched:** 2026-04-05
**Domain:** FastAPI / SQLAlchemy 2.0 / Next.js 16 App Router — 管理画面データ取得状況可視化 + Windows Agent コマンド連携
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| ADMIN-05 | データタブで年/月単位のレースデータ取得状況（件数）を確認できる | `Race.date` (YYYYMMDD) を `LEFT(date,6)` でグループ化するSQLで月別カウントが取得可能。バックエンドに `GET /api/admin/data-coverage` を新設。 |
| ADMIN-06 | データタブで未取得月のデータをUIからWindows Agentへ取得指示できる | 既存 `POST /api/agent/command` にアクション `recent` が未実装。`agent_router.py` と `jvlink_agent.py` 両方への追加が必要。`params.from_year` または `params.year_month` で年月を指定する方式を採用。 |
</phase_requirements>

---

## Summary

Phase 16 は管理画面の「データタブ」を実装するフェーズ。既存の `DataTab.tsx`（プレースホルダー）を実際の機能で置き換える。変更点は3箇所に及ぶ：（1）バックエンドに2本の新規APIエンドポイント、（2）`agent_router.py` と `jvlink_agent.py` の `recent` アクション対応追加、（3）フロントエンド `DataTab.tsx` の実装。

最も注意すべきギャップは、**`POST /api/agent/command` の `valid_actions` セットに `recent` が含まれていない**点。`agent_router.py`（バックエンド）と `jvlink_agent.py`（Windows Agent コマンドループ）の両方を更新する必要がある。`jvlink_agent.py` の既存 `run_recent(from_year)` 関数はすでに実装済みなので、コマンドループでのディスパッチを追加するだけでよい。

フロントエンドは `"use client"` の `DataTab.tsx` として実装する。データタブは独自のフェッチ（Server Action 経由 or クライアントサイド fetch）を持ち、`AdminTabs.tsx` を介したサーバーコンポーネントのデータ流しは不要（データ量が動的で取得指示後に再取得が必要なため）。

**Primary recommendation:** `DataTab.tsx` を `useEffect` + `fetch` で自律的にデータ取得する Client Component として実装する。「取得」ボタンは Server Action（`actions.ts` に追加）を呼び、成功後に `fetchCoverage()` を再実行して表示を更新する。

---

## Standard Stack

### Core（既存プロジェクトスタック）
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Next.js | 16.2.1 | App Router / Client Component | プロジェクト標準 |
| React | 19.2.4 | UI + `useState` / `useEffect` | プロジェクト標準 |
| Tailwind CSS | v4 | スタイリング | プロジェクト標準 |
| FastAPI | 最新 | バックエンド API | プロジェクト標準 |
| SQLAlchemy | 2.0 async | DB アクセス | プロジェクト標準 |
| Pydantic | v2 | スキーマ検証 | プロジェクト標準 |

### 追加不要
新規パッケージは不要。既存スタックで完結できる。

---

## Architecture Patterns

### 全体の変更箇所マップ

```
backend/src/api/
├── users.py         ← admin_router に data-coverage / fetch-data エンドポイントを追加
                        (または新ファイル admin_data_router.py に分離)
├── agent_router.py  ← valid_actions に "recent" を追加

windows-agent/
└── jvlink_agent.py  ← run_command_loop() に "recent" ハンドラーを追加

frontend/src/app/admin/
├── DataTab.tsx      ← 実装（プレースホルダーから置き換え）
└── actions.ts       ← fetchData() Server Action を追加
```

### Pattern 1: 月別取得状況クエリ（バックエンド）

`Race.date` は `String(8)` 型（例: `"20260405"`）。PostgreSQL の `LEFT(date, 6)` で年月 `"202604"` を取得し `GROUP BY` する。

```python
# Source: backend/src/db/models.py + SQLAlchemy 2.0 ドキュメント
from sqlalchemy import func, select, text

stmt = (
    select(
        func.left(Race.date, 6).label("year_month"),  # "YYYYMM"
        func.count(Race.id).label("race_count"),
    )
    .group_by(func.left(Race.date, 6))
    .order_by(func.left(Race.date, 6))
)
result = await db.execute(stmt)
rows = result.all()
# rows: [("202301", 144), ("202302", 132), ...]
```

この結果を年別にグルーピングしてレスポンスを構築する。

### Pattern 2: data-coverage APIレスポンス構造

```python
# Pydantic スキーマ設計
class MonthCoverage(BaseModel):
    year_month: str      # "YYYYMM"
    race_count: int      # 0 = 未取得

class YearCoverage(BaseModel):
    year: str            # "YYYY"
    months: list[MonthCoverage]  # 01〜12月分（データなし月も含む）
    total: int           # 年間合計

class DataCoverageResponse(BaseModel):
    coverage: list[YearCoverage]
    total_races: int
```

月は必ず01〜12の12件を返す（count=0でも含める）ことで、フロントエンドが「未取得月」を判定しやすくなる。

### Pattern 3: fetch-data APIエンドポイント（バックエンド → agent_router への橋渡し）

`POST /api/admin/fetch-data` は agent_router の内部キュー関数を直接呼ぶのではなく、`POST /api/agent/command` を内部的に実行するか、共有のキューオブジェクトをモジュールレベルで参照する。

**推奨方式：共有キューオブジェクトを直接 import する**

```python
# backend/src/api/users.py (admin_router) または新ファイル
from .agent_router import _command_queue  # インメモリキューを直接操作

@admin_router.post("/fetch-data")
async def trigger_fetch_data(body: FetchDataRequest, _: ApiKeyDep) -> dict:
    """指定年月のデータ取得をWindows Agentへ指示する。"""
    # from_year を year_month から導出（例: "202301" → 2023）
    from_year = int(body.year_month[:4])
    entry = {
        "action": "recent",
        "params": {"from_year": from_year, "year_month": body.year_month},
        "queued_at": datetime.now().isoformat(),
    }
    _command_queue.append(entry)
    return {"queued": True, "action": "recent", "params": entry["params"]}
```

または既存の `enqueue_command` 関数ロジックを踏まえて `agent_router.py` の `valid_actions` に `"recent"` を追加し、フロントからは admin エンドポイントのみを叩く設計にする。

**推奨方式：admin エンドポイントから agent_router の enqueue ロジックを呼ぶ形にして、`valid_actions` を拡張する。**

### Pattern 4: agent_router.py の recent アクション追加

```python
# backend/src/api/agent_router.py
valid_actions = {"setup", "daily", "retry", "stop", "recent"}  # "recent" を追加
```

コメント更新も必要:
```python
"""
- recent: JVOpen(option=3)で指定年以降のデータを再取得
  params: {"from_year": 2023, "year_month": "202301"}  # year_month はログ用
"""
```

### Pattern 5: jvlink_agent.py コマンドループへの recent ハンドラー追加

```python
# windows-agent/jvlink_agent.py — run_command_loop() 内
elif action == "recent":
    from_year = cmd.get("params", {}).get("from_year", 2023)
    year_month = cmd.get("params", {}).get("year_month", "")
    report_status(
        "running",
        mode="recent",
        message=f"Starting recent mode ({from_year}+ / {year_month})",
    )
    run_recent(jv, from_year=from_year)
    report_status("idle", message=f"Recent fetch completed ({from_year}+)")
```

`run_recent(jv, from_year)` は既存実装済みのため、ディスパッチを追加するだけ。

### Pattern 6: DataTab.tsx の構造

```tsx
"use client";

import { useEffect, useState, useTransition } from "react";
import { triggerFetchData } from "./actions";

type MonthCoverage = { year_month: string; race_count: number };
type YearCoverage = { year: string; months: MonthCoverage[]; total: number };
type DataCoverageResponse = { coverage: YearCoverage[]; total_races: number };

export function DataTab() {
  const [coverage, setCoverage] = useState<DataCoverageResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [isPending, startTransition] = useTransition();

  async function fetchCoverage() {
    setLoading(true);
    const res = await fetch("/api/admin/data-coverage");
    if (res.ok) setCoverage(await res.json());
    setLoading(false);
  }

  useEffect(() => { fetchCoverage(); }, []);

  function handleFetch(yearMonth: string) {
    startTransition(async () => {
      await triggerFetchData(yearMonth);
      // 取得指示後、カバレッジを再取得（非同期なので即時反映はしない）
      await fetchCoverage();
    });
  }

  // ... テーブルレンダリング
}
```

フロントエンドからバックエンド管理エンドポイントを直接 fetch することはできない（INTERNAL_API_KEY が必要）ため、`actions.ts` の Server Action を経由する。

### Pattern 7: actions.ts への Server Action 追加

```typescript
// frontend/src/app/admin/actions.ts
"use server";

export async function triggerFetchData(yearMonth: string): Promise<{ error?: string }> {
  const res = await fetch(`${BACKEND_URL}/admin/fetch-data`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
    },
    body: JSON.stringify({ year_month: yearMonth }),
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    return { error: (body as { detail?: string }).detail ?? "取得指示に失敗しました" };
  }
  return {};
}

export async function fetchDataCoverage(): Promise<DataCoverageResponse | null> {
  const res = await fetch(`${BACKEND_URL}/admin/data-coverage`, {
    headers: { "X-API-Key": API_KEY },
    cache: "no-store",
  });
  if (!res.ok) return null;
  return res.json();
}
```

### 推奨ファイル構成

```
backend/src/api/
├── users.py             # admin_router に 2エンドポイント追加
├── agent_router.py      # valid_actions に "recent" 追加
└── main.py              # 変更なし（既に users_admin_router を登録済み）

windows-agent/
└── jvlink_agent.py      # run_command_loop() に "recent" ハンドラー追加

frontend/src/app/admin/
├── DataTab.tsx          # プレースホルダーから実装に書き換え
└── actions.ts           # triggerFetchData / fetchDataCoverage 追加
```

### Anti-Patterns to Avoid

- **`page.tsx` に data-coverage fetch を追加する:** `page.tsx` はサーバーコンポーネントで初期表示時のみ実行される。取得指示後の再取得ができないため NG。`DataTab.tsx` が `useEffect` で独自 fetch する構成を採用する。
- **`POST /api/agent/command` をフロントエンドから直接叩く:** INTERNAL_API_KEY がブラウザに露出するため禁止。必ず Server Action 経由にする。
- **月別集計に Raw SQL（`text()`）を使う:** SQLAlchemy の `func.left()` で型安全に書ける。`text()` は不要。
- **全年分のデータを常に1クエリで取得する:** 将来データが増えても月別COUNT程度のクエリは軽量（インデックスが `date` カラムにある）。ページングは不要。

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 年月ごとのレース件数集計 | Python ループで1月ずつクエリ | `GROUP BY LEFT(date, 6)` の単一クエリ | N+1 クエリ防止。date カラムにインデックスがある |
| Windows Agent への通知 | WebSocket プッシュ / SSH 実行 | 既存 `_command_queue`（agent_router.py）を利用 | コマンドキュー方式は既存インフラ。再実装不要 |
| カレンダーUI | 外部ライブラリ | Tailwind ベースのシンプルなテーブルグリッド | 取得状況表示は件数テーブルで十分。DatePicker 等は不要 |

**Key insight:** Windows Agent コマンドキュー（`_command_queue`）はすでに稼働している仕組み。`recent` アクションのディスパッチを追加するだけで UI からのデータ取得指示が実現できる。

---

## Common Pitfalls

### Pitfall 1: agent_router の valid_actions と jvlink_agent のハンドラーが不一致
**What goes wrong:** `agent_router.py` に `"recent"` を追加しても `jvlink_agent.py` のコマンドループに対応ハンドラーがなければ `"[command] 未知のaction"` としてスキップされる。逆もしかり。
**Why it happens:** バックエンドとWindows Agentは別プロセス（別リポジトリ位置）なので同期更新が漏れやすい。
**How to avoid:** 両ファイルを同一プランで同時に更新する。

### Pitfall 2: `recent` モードの from_year は月単位の精度がない
**What goes wrong:** `run_recent(jv, from_year=2023)` は「2023年1月以降」のデータを全件取得する。例えば「2023年7月だけ欲しい」という月単位の指定には対応していない。
**Why it happens:** JV-Link の `JVOpen()` は `from_time`（`YYYYMMDD000000`）単位での開始指定はできるが、月末終了の指定はできない。
**How to avoid:** 成功条件の確認。`ADMIN-06` の要件は「未取得月のデータをUIからWindows Agentへ取得指示できる」であり、「その月だけを取得する」とは書かれていない。`from_year` で年単位の取得を指示する形で要件を満たせる。ユーザーに「その年以降のデータを再取得します」と表示するUIにする。
**Warning signs:** UIが「2023年7月のみ取得」と誤解させる表現になっていないか確認する。

### Pitfall 3: coverage取得後の「ゼロ月」判定
**What goes wrong:** クエリはデータが存在する年月のみ返す（ゼロ件の月はレコードがない）。「ゼロの月に取得ボタン表示」するためには存在しない月を補完する必要がある。
**Why it happens:** `GROUP BY` は値があるものしか返さない。
**How to avoid:** バックエンドで月01〜12を全件生成し、クエリ結果とマージしてゼロ埋めする。

```python
# バックエンドで年月リストを生成してゼロ埋め
from datetime import date
import calendar

def build_year_coverage(year: str, month_counts: dict[str, int]) -> YearCoverage:
    months = []
    for m in range(1, 13):
        ym = f"{year}{m:02d}"
        months.append(MonthCoverage(year_month=ym, race_count=month_counts.get(ym, 0)))
    return YearCoverage(year=year, months=months, total=sum(m.race_count for m in months))
```

### Pitfall 4: DataTab から INTERNAL_API_KEY が漏れる
**What goes wrong:** `DataTab.tsx` が `"use client"` なのに `fetch("/api/admin/data-coverage")` を直接呼ぶと `X-API-Key` ヘッダーをブラウザから送信することになりキーが露出する。
**How to avoid:** 2段階の対応が必要：
  - `GET /api/admin/data-coverage` は認証方式を Next.js 経由（Server Action / Route Handler）にするか、認証不要のエンドポイントにする（閲覧は管理者セッションで保護済み）。
  - `POST /api/admin/fetch-data`（取得指示）は必ず `actions.ts` の Server Action を経由する。
  - **最もシンプルな解決策:** `GET /api/admin/data-coverage` を Next.js の Route Handler（`/app/api/admin/data-coverage/route.ts`）でラップし、セッション認証 + バックエンド呼び出しを行う。`DataTab.tsx` は `/api/admin/data-coverage`（Next.js側）を fetch する。

### Pitfall 5: recent モード実行中の二重起動
**What goes wrong:** 管理者が取得ボタンを連打すると、コマンドキューに複数の `recent` コマンドが積まれる。
**Why it happens:** コマンドキューに上限がない。
**How to avoid:** フロントエンドで取得指示後にボタンを disabled にする（`isPending` state で制御）。Phase 16 のスコープでは複雑なロック機構は不要。

---

## Code Examples

### バックエンド: data-coverage エンドポイント

```python
# Source: プロジェクト内 backend/src/api/users.py + SQLAlchemy 2.0 パターン
from datetime import datetime
from sqlalchemy import func, select
from ..db.models import Race

class MonthCoverage(BaseModel):
    year_month: str   # "YYYYMM"
    race_count: int

class YearCoverage(BaseModel):
    year: str
    months: list[MonthCoverage]
    total: int

class DataCoverageResponse(BaseModel):
    coverage: list[YearCoverage]
    total_races: int

@admin_router.get("/data-coverage", response_model=DataCoverageResponse)
async def get_data_coverage(_: ApiKeyDep, db: DbDep) -> DataCoverageResponse:
    """年/月別のレースデータ取得状況を返す。"""
    stmt = (
        select(
            func.left(Race.date, 6).label("year_month"),
            func.count(Race.id).label("race_count"),
        )
        .group_by(func.left(Race.date, 6))
        .order_by(func.left(Race.date, 6))
    )
    result = await db.execute(stmt)
    rows = result.all()

    # year_month -> count の辞書
    month_counts: dict[str, int] = {row.year_month: row.race_count for row in rows}

    # 年の範囲を決定（データがある年のみ）
    if not month_counts:
        return DataCoverageResponse(coverage=[], total_races=0)

    years = sorted({ym[:4] for ym in month_counts})

    coverage = []
    for year in years:
        months = [
            MonthCoverage(
                year_month=f"{year}{m:02d}",
                race_count=month_counts.get(f"{year}{m:02d}", 0),
            )
            for m in range(1, 13)
        ]
        coverage.append(
            YearCoverage(year=year, months=months, total=sum(m.race_count for m in months))
        )

    return DataCoverageResponse(
        coverage=coverage,
        total_races=sum(month_counts.values()),
    )
```

### バックエンド: fetch-data エンドポイント

```python
# Source: プロジェクト内 backend/src/api/agent_router.py パターン
from datetime import datetime
from .agent_router import _command_queue

class FetchDataRequest(BaseModel):
    year_month: str  # "YYYYMM" 形式

@admin_router.post("/fetch-data")
async def trigger_fetch_data(body: FetchDataRequest, _: ApiKeyDep) -> dict:
    """指定年月のデータ取得をWindows Agentへ指示する。

    recentモードで from_year を指定してコマンドキューに積む。
    """
    if len(body.year_month) != 6 or not body.year_month.isdigit():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="year_month は YYYYMM 形式で指定してください",
        )
    from_year = int(body.year_month[:4])
    entry = {
        "action": "recent",
        "params": {"from_year": from_year, "year_month": body.year_month},
        "queued_at": datetime.now().isoformat(),
    }
    _command_queue.append(entry)
    return {"queued": True, "action": "recent", "from_year": from_year, "year_month": body.year_month}
```

### フロントエンド DataTab.tsx（骨格）

```tsx
"use client";

import { useEffect, useState, useTransition } from "react";
import { triggerFetchData } from "./actions";

type MonthCoverage = { year_month: string; race_count: number };
type YearCoverage = { year: string; months: MonthCoverage[]; total: number };
type CoverageData = { coverage: YearCoverage[]; total_races: number };

export function DataTab() {
  const [data, setData] = useState<CoverageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchingMonth, setFetchingMonth] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  async function loadCoverage() {
    setLoading(true);
    try {
      const res = await fetch("/api/admin/data-coverage"); // Next.js Route Handler
      if (res.ok) setData(await res.json());
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadCoverage(); }, []);

  function handleFetch(yearMonth: string) {
    setFetchingMonth(yearMonth);
    startTransition(async () => {
      const result = await triggerFetchData(yearMonth);
      if (result.error) {
        alert(result.error);
      }
      setFetchingMonth(null);
    });
  }

  if (loading) return <div className="py-8 text-center text-gray-400 text-sm">読み込み中...</div>;
  if (!data) return <div className="py-8 text-center text-red-400 text-sm">取得失敗</div>;

  return (
    <div className="space-y-6">
      <p className="text-sm text-gray-500">総レース数: {data.total_races.toLocaleString()} 件</p>
      {data.coverage.map((year) => (
        <div key={year.year}>
          <h3 className="font-medium text-sm text-[#0d1f35] mb-2">{year.year}年 （計 {year.total.toLocaleString()} 件）</h3>
          <div className="grid grid-cols-6 gap-1">
            {year.months.map((m) => {
              const month = m.year_month.slice(4); // "MM"
              const isEmpty = m.race_count === 0;
              return (
                <div
                  key={m.year_month}
                  className={`rounded border p-2 text-xs text-center ${
                    isEmpty ? "border-red-200 bg-red-50" : "border-gray-200 bg-white"
                  }`}
                >
                  <div className="font-medium">{month}月</div>
                  <div className={isEmpty ? "text-red-500" : "text-gray-600"}>
                    {isEmpty ? "0" : m.race_count.toLocaleString()}件
                  </div>
                  {isEmpty && (
                    <button
                      onClick={() => handleFetch(m.year_month)}
                      disabled={isPending || fetchingMonth !== null}
                      className="mt-1 w-full text-xs bg-[#0d1f35] text-white rounded py-0.5 disabled:opacity-40"
                    >
                      取得
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
```

### Next.js Route Handler (API鍵を隠す中継層)

```typescript
// frontend/src/app/api/admin/data-coverage/route.ts
import { auth } from "@/auth";
import { NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000/api";
const API_KEY = process.env.INTERNAL_API_KEY ?? "";

export async function GET() {
  const session = await auth();
  if (session?.user?.role !== "admin") {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const res = await fetch(`${BACKEND_URL}/admin/data-coverage`, {
    headers: { "X-API-Key": API_KEY },
    cache: "no-store",
  });

  if (!res.ok) return NextResponse.json({ error: "Backend error" }, { status: 502 });
  return NextResponse.json(await res.json());
}
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| DataTab.tsx プレースホルダー | 実装済みの月別グリッド + 取得ボタン | Phase 16 | データ取得状況の可視化 |
| agent_router の valid_actions に recent なし | "recent" アクション追加 | Phase 16 | UI から Windows Agent への recent モード指示が可能に |
| jvlink_agent コマンドループに recent なし | "recent" ハンドラー追加 | Phase 16 | コマンドキュー経由での recent 実行が可能に |

---

## Open Questions

1. **`GET /api/admin/data-coverage` の認証方式**
   - What we know: 既存の `admin_router` エンドポイントは `X-API-Key` ヘッダーで認証する。ブラウザから直接叩けない。
   - What's unclear: Route Handler でラップするか、Server Action で呼ぶか。
   - Recommendation: `GET` は Next.js Route Handler（`/app/api/admin/data-coverage/route.ts`）でラップして `auth()` セッション認証にする。`POST /fetch-data` は `actions.ts` の Server Action 経由。これが最もセキュアで既存パターンとも整合する。

2. **取得ボタンの粒度（月単位 vs 年単位）**
   - What we know: `run_recent(from_year)` は年単位の指定しかできない。「2023年7月取得」は実際には「2023年以降全件再取得」になる。
   - What's unclear: ユーザーが月ごとにボタンを押した場合に年単位のジョブが重複キューイングされるリスク。
   - Recommendation: UIに「2023年以降のデータを取得します」と表示して意図を明確化する。年ごとに1ボタンとする設計も検討余地あり。Phase 16 スコープでは月単位ボタンを採用し、説明文を添えることでユーザー混乱を防ぐ。

3. **Windows Agent が `wait` モードで起動していない場合**
   - What we know: コマンドキューはメモリ内（`_command_queue: deque`）。Agent が polling していなければコマンドは消費されない。
   - What's unclear: Agent が offline の場合のエラー通知。
   - Recommendation: Phase 16 スコープでは「キューに積んだ」ことを成功とし、Agentオフライン検知は `ADMIN-F02`（Future Requirement）として対応しない。

---

## Sources

### Primary (HIGH confidence)
- 直接コード調査: `/frontend/src/app/admin/DataTab.tsx` — プレースホルダー確認
- 直接コード調査: `/frontend/src/app/admin/AdminTabs.tsx` — タブ構成確認
- 直接コード調査: `/frontend/src/app/admin/actions.ts` — Server Action パターン確認
- 直接コード調査: `/frontend/src/app/admin/page.tsx` — Server Component + Route Handler パターン確認
- 直接コード調査: `/backend/src/api/agent_router.py` — valid_actions / _command_queue 確認（`"recent"` が未実装）
- 直接コード調査: `/backend/src/api/users.py` — admin_router / verify_api_key パターン確認
- 直接コード調査: `/backend/src/api/main.py` — ルーター登録状況確認
- 直接コード調査: `/backend/src/db/models.py` — `Race.date` が `String(8)` (YYYYMMDD) 確認
- 直接コード調査: `/windows-agent/jvlink_agent.py` — `run_recent()` 実装済み確認 / コマンドループに `recent` ハンドラーなし確認

### Secondary (MEDIUM confidence)
- `.planning/REQUIREMENTS.md` — ADMIN-05/06 詳細要件
- `.planning/ROADMAP.md` — Phase 16 成功条件
- `.planning/STATE.md` — 「Windows AgentへのUI取得指示はrecentモードのエージェントコマンド経由」決定確認

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — 既存プロジェクトコードから直接確認
- Architecture: HIGH — 既存 agent_router / admin_router パターンを踏襲した設計
- Pitfalls: HIGH — コードから具体的なギャップ（valid_actions 未追加、コマンドループ未追加、APIキー漏洩リスク）を特定
- Backend changes: HIGH — 追加箇所が明確（users.py への2エンドポイント追加 + agent_router.py の1行追加 + jvlink_agent.py のelif追加）

**Research date:** 2026-04-05
**Valid until:** 2026-05-05（プロジェクト内変更がなければ安定）
