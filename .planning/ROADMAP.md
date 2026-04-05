# Roadmap: GallopLab (kiseki)

## Milestones

- ✅ **v1.0 MS1** - 環境構築 + データ取込 + スピード指数 (shipped 2025 Q4)
- ✅ **v2.0 MS2** - コース適性 + 枠順バイアス + 総合指数 (shipped 2026 Q1)
- ✅ **v3.0 MS3** - 騎手・展開・血統・ローテーション指数 (shipped 2026 Q1)
- ✅ **v4.0 MS4** - パドック・調教 + バックテスト (shipped 2026 Q1)
- ✅ **v5.0 MS5** - リアルタイム対応 + 変更検知 (shipped 2026 Q1)
- ✅ **v6.0 MS6** - 競馬新聞Web (PWA) + 有料化基盤 (shipped 2026-04-05)
- 🚧 **v7.0 管理画面整備** - Phases 15–17 (in progress)
- 📋 **v8.0 MS7** - IPAT連携 + 収支管理 (planned)

## Phases

<details>
<summary>✅ MS1–MS6 (Phases 1–14) - SHIPPED 2026-04-05</summary>

### Phase 1–5: MS1–MS4 (Indices Engine)
**Goal**: JV-Link SDKからデータ取得し、13エージェント複合指数v9を算出・格納する。
**Plans**: Completed

### Phase 6–8: MS5 (Realtime)
**Goal**: リアルタイムオッズポーリング、WebSocket配信、変更検知・選択的再算出。
**Plans**: Completed

### Phase 9–14: MS6 (PWA + Paid Service)
**Goal**: 競馬新聞風PWA（Next.js 16）、Google OAuth認証、招待コード・ペイウォール、Claude API推奨、cronパイプライン、CI/CDデプロイ。
**Plans**: Completed

</details>

---

### 🚧 v7.0 管理画面整備 (In Progress)

**Milestone Goal:** 管理画面をタブ構成に再編し、ユーザー管理の使い勝手向上・データ取得状況の可視化・PAID_MODEのDB管理を実現する。

#### Phase 15: Admin UI再構成
**Goal**: 管理画面がタブUI（ユーザー / データ / 設定）に再構成され、ユーザーテーブルの可読性・情報量が向上する
**Depends on**: Phase 14 (MS6 complete)
**Requirements**: ADMIN-01, ADMIN-02, ADMIN-03, ADMIN-04
**Success Criteria** (what must be TRUE):
  1. 管理画面に「ユーザー」「データ」「設定」の3タブが表示され、タブ切り替えで各セクションに移動できる
  2. ユーザーテーブルの各行が1行に収まり（whitespace-nowrap + 横スクロール）、長いフィールドが行を折り返さない
  3. ユーザーテーブルが10件ページングで表示され、前後ページへ移動できる
  4. ユーザーテーブルに予想家名（yoso_name）と公開設定（is_yoso_public）の列が追加されている
**Plans**: 2 plans
Plans:
- [ ] 15-01-PLAN.md — バックエンド UserResponse に yoso_name / is_yoso_public を追加
- [ ] 15-02-PLAN.md — 管理画面タブ再構成 + ユーザーテーブル改善（AdminTabs / UsersTab / CodesTab / DataTab / SettingsTab）

#### Phase 16: データタブ
**Goal**: 管理者がデータタブで年/月単位のレースデータ取得状況を確認し、UIから未取得月のデータ取得をWindows Agentへ指示できる
**Depends on**: Phase 15
**Requirements**: ADMIN-05, ADMIN-06
**Success Criteria** (what must be TRUE):
  1. データタブで年別・月別のレース件数（取得済み/ゼロ）がテーブルまたはグリッドで一覧表示される
  2. 件数がゼロの月に対して「取得」ボタンが表示され、クリックするとWindows AgentのrecentモードでそのYYYY/MMのデータ取得が開始される
  3. バックエンドに月別取得状況を返すAPIエンドポイント（GET /api/admin/data-coverage）が存在し、正常なJSONレスポンスを返す
  4. バックエンドにWindows Agent取得指示を受け付けるAPIエンドポイント（POST /api/admin/fetch-data）が存在し、recentモードでエージェントコマンドを発行する
**Plans**: TBD

#### Phase 17: 設定タブ + PAID_MODE DB化
**Goal**: PAID_MODEがDBで管理され、管理者がUIからON/OFFを切り替えるとリアルタイムにサービスの公開/制限状態が変わる
**Depends on**: Phase 16
**Requirements**: ADMIN-07, ADMIN-08
**Success Criteria** (what must be TRUE):
  1. keiba.app_settings テーブルが存在し、PAID_MODE キーの値が格納・取得できる
  2. 設定タブにPAID_MODEトグルが表示され、ONにするとDBに反映され、フロントエンドの次回アクセス時にペイウォールが有効化される
  3. バックエンドにGET /api/admin/settings と PUT /api/admin/settings エンドポイントが存在し、ADMIN_KEY認証で保護されている
  4. フロントエンドがビルド時の環境変数ではなく、起動時にAPIからPAID_MODEを取得し、DBの値に基づいてペイウォールを制御する
**Plans**: TBD

---

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1–5. Indices Engine | MS1–MS4 | — | Complete | 2026 Q1 |
| 6–8. Realtime | MS5 | — | Complete | 2026 Q1 |
| 9–14. PWA + Paid | MS6 | — | Complete | 2026-04-05 |
| 15. Admin UI再構成 | v7.0 | 0/2 | Not started | - |
| 16. データタブ | v7.0 | 0/TBD | Not started | - |
| 17. 設定タブ + PAID_MODE DB化 | v7.0 | 0/TBD | Not started | - |
