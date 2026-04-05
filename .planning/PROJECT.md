# GallopLab (kiseki)

## What This Is

JRA-VAN Data Lab SDKから競馬データを直接取得し、独自の指数（9〜13エージェント）を算出する競馬予測システム。
オッズとの期待値比較で合理的な馬券購入判断を支援し、競馬新聞風PWA（galloplab.com）として提供する。

## Core Value

指数と期待値に基づく「買うべきレース・買うべき馬」の提示 — データに裏付けられた買い目判断を、誰でも即座に参照できる。

## Requirements

### Validated

<!-- MS1〜MS6で実装・稼働確認済み -->

- ✓ JV-Link SDK経由でレース・成績・オッズデータを取得 (MS1)
- ✓ PostgreSQL(VPS)へのデータ格納・UPSERT (MS1)
- ✓ スピード指数・上がり指数算出 (MS1)
- ✓ コース適性・枠順バイアス・騎手・展開・ローテーション指数算出 (MS2-MS3)
- ✓ 血統指数・調教指数・ポジションアドバンテージ指数・巻き返し指数算出 (MS4)
- ✓ 複合指数（v9、13エージェント・重み最適化済み）(MS4)
- ✓ 穴ぐさ（sekito.anagusa）連携 (MS3)
- ✓ リアルタイムオッズ取得・WebSocket配信 (MS5)
- ✓ 変更検知（出走取消・騎手変更）→ 選択的再算出 (MS5)
- ✓ 競馬新聞風PWA（Next.js 16 / App Router）(MS6)
- ✓ Google OAuth認証（Auth.js v5）(MS6)
- ✓ 招待コード・アクセス管理・ペイウォール基盤 (MS6)
- ✓ 実績ページ（回収率・的中履歴）(MS6)
- ✓ Claude API推奨5レース機能 (MS6)
- ✓ Google Analytics導入 (MS6)
- ✓ 自動cronパイプライン（daily_fetch.sh）(MS6)
- ✓ 前日発売オッズ取得（odds-prefetchモード）(MS6)

### Active

<!-- v7.0: 管理画面整備 -->

- [ ] 管理画面タブUI（ユーザー / データ / 設定）再構成
- [ ] 登録ユーザーテーブル改善（1行表示・10件ページング・予想家名表示）
- [ ] データタブ（年/月単位取得状況・Windows Agentへの取得指示）
- [ ] 設定タブ（PAID_MODEのDB管理・UI切り替え）

### Out of Scope

- 地方競馬対応 — 将来拡張候補だが現在はJRAのみ
- TARGETとの連携 — JV-Link SDK直接利用方針のため不使用
- ネイティブモバイルアプリ — PWAで十分

## Context

- サービス名: GallopLab（galloplab.com、本番稼働中）
- データ: JRA-VAN DataLab個人利用契約（JRAVAN_SID）
- 有料化: JRADB商用利用条件（法人形態必須）のため、現在クローズドβ運用中
- 指数v9: スピアマン相関比例重み（ROI=86.4%）、バックフィル2024/01〜完了
- netkeibaバックフィル: 2024-01〜2025-03の`remarks`データが未収集（巻き返し指数の学習データ）
- Windows Agent: Parallels VM（Python 32bit + pywin32 + JV-Link COM）
- フロントエンド: https://galloplab.com/ にCI/CDデプロイ済み

## Constraints

- **Tech Stack**: Python 3.12 / FastAPI / Next.js 16 / PostgreSQL — 変更しない
- **DB**: keibas スキーマ + sekito スキーマ / Alembic経由のみDDL変更
- **JV-Link**: Windows 32bit Python必須、同時1接続のみ
- **法規制**: JRA-VAN個人利用許諾内での運用（商用化は法人化後）
- **セキュリティ**: .envは絶対にコミットしない

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| 指数重み: スピアマン相関比例 | Nelder-Mead最適化は過学習（血統0.345）を確認 | ✓ Good |
| パドック・馬体重変化 weight=0 | 有効性検証でROI改善なし | ✓ Good |
| Auth.js v5 JWT strategy | サーバーセッション不要、スケーラブル | ✓ Good |
| JRADB商用契約: 法人化必要 | 個人事業主では申請不可（2026-04-02確認） | — Pending |
| クローズドβ継続 | 法人化意思決定までJRA-VAN個人利用で運用 | — Pending |

## Current Milestone: v7.0 管理画面整備

**Goal:** 管理画面をタブ構成に再編し、ユーザー管理の使い勝手向上・データ取得状況の可視化・PAID_MODEのDB管理を実現する。

**Target features:**
- タブUI（ユーザー / データ / 設定）
- ユーザーテーブル改善（1行・ページング・予想家名）
- データタブ（月別取得状況 + 取得指示）
- 設定タブ（PAID_MODE DB化）

---
*Last updated: 2026-04-05 after v7.0 milestone start*
