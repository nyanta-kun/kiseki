# Milestones: GallopLab (kiseki)

## Completed Milestones

### MS1: 環境構築 + データ取込 + スピード指数

**Shipped:** 2025 Q4

**What shipped:**
- Windows Agent（JV-Link SDK, Python 32bit + pywin32）
- FastAPI Backend（データ受信・UPSERT）
- PostgreSQL（VPS）接続確立
- スピード指数・上がり指数算出エンジン

---

### MS2: コース適性 + 枠順バイアス + 総合指数

**Shipped:** 2026 Q1

**What shipped:**
- コース適性指数（CourseAptitudeCalculator）
- 枠順バイアス指数（FrameBiasCalculator）
- 総合複合指数（CompositeIndexCalculator）

---

### MS3: 騎手・展開・血統・ローテーション指数

**Shipped:** 2026 Q1

**What shipped:**
- 騎手指数（JockeyIndexCalculator）
- 展開指数（PaceIndexCalculator）
- 血統指数（PedigreeIndexCalculator）
- ローテーション指数（RotationIndexCalculator）
- 穴ぐさ（sekito.anagusa）連携

---

### MS4: パドック・調教 + 巻き返し指数 + バックテスト

**Shipped:** 2026 Q1

**What shipped:**
- 調教指数（TrainingIndexCalculator）
- ポジションアドバンテージ指数（PositionAdvantageCalculator）
- 巻き返し指数（ReboundIndexCalculator）
- 指数v8〜v9（スピアマン相関比例重み）
- ROIシミュレーション・バックテスト実装

**Phases completed:** 1–5

---

### MS5: リアルタイム対応 + 変更検知

**Shipped:** 2026 Q1

**What shipped:**
- JVRTOpen() によるリアルタイムオッズポーリング（30秒間隔）
- WebSocket配信（/api/races/{id}/odds/ws, /api/races/{id}/results/ws）
- 変更検知ハンドラ（出走取消・騎手変更 → 選択的再算出）
- 前日発売オッズ取得（odds-prefetchモード）

**Phases completed:** 6–8

---

### MS6: 競馬新聞Web (PWA) + 有料化基盤

**Shipped:** 2026-04-05

**What shipped:**
- Next.js 16 App Router PWA（galloplab.com）
- Google OAuth認証（Auth.js v5）
- レース一覧・詳細ページ（指数・確率チャート・期待値・穴ぐさ）
- 実績ページ（回収率・的中履歴）
- 招待コード・アクセス管理・ペイウォール（PaywallGate）
- Claude API推奨5レース機能
- cronパイプライン（daily_fetch.sh）完全自動化
- Google Analytics（G-WFN6SC1KT5）
- CI/CDデプロイ（GitHub Actions → galloplab.com）

**Phases completed:** 9–14

---

## Upcoming Milestones

### MS7: IPAT連携 + 収支管理（予定）

### MS8: 全自動投票 + 継続最適化（予定）
