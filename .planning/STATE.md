# State: GallopLab (kiseki)

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-05)

**Core value:** 指数と期待値に基づく「買うべきレース・買うべき馬」の提示
**Current focus:** Milestone planning

## Current Position

Phase: Not started (defining new milestone)
Plan: —
Status: Defining next milestone requirements
Last activity: 2026-04-05 — MS6 completed, starting new milestone

## Accumulated Context

### Known Issues / Blockers
- netkeibaバックフィル（2024-01〜2025-03のremarks）: 未収集。VPS負荷対策必須（深夜実行推奨）
- VPS SSHプロンプトハング: 再起動後に発生することあり（~/.bashrcかMOTDが原因候補）

### Technical Debt
- 巻き返し指数重み最適化: バックフィルデータが揃い次第、composite v9 の weight 再最適化
- v9バックフィルは2026-04-05完了済み（118,276件）

### Infrastructure Notes
- ローカル: `docker compose up -d` → frontend:3000 / backend:8000
- 本番: main → GitHub Actions CI → 自動デプロイ → galloplab.com
- Alembicマイグレーション: 複数ヘッドに注意（新規追加後に統合が必要な場合あり）
