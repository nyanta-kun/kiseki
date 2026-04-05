# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-05)

**Core value:** 指数と期待値に基づく「買うべきレース・買うべき馬」の提示
**Current focus:** Phase 15 — Admin UI再構成

## Current Position

Phase: 15 of 17 (Admin UI再構成)
Plan: — (not yet planned)
Status: Ready to plan
Last activity: 2026-04-05 — Milestone v7.0 roadmap created (Phases 15–17)

Progress: [██████████████░░░░░░] 70% (MS1–MS6 complete)

## Performance Metrics

**Velocity:**
- Total plans completed: — (v7.0 not started)
- Average duration: —
- Total execution time: —

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 15–17 (v7.0) | TBD | - | - |

**Recent Trend:** N/A (new milestone)

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- v7.0 start: PAID_MODEをDBで管理する方針（keiba.app_settingsテーブル新設）
- v7.0 start: Windows AgentへのUI取得指示はrecentモードのエージェントコマンド経由

### Known Issues / Blockers

- netkeibaバックフィル（2024-01〜2025-03のremarks）: 未収集。VPS負荷対策必須（深夜実行推奨）
- VPS SSHプロンプトハング: 再起動後に発生することあり

### Technical Debt

- 巻き返し指数重み最適化: バックフィルデータが揃い次第、composite v9 の weight 再最適化
- v9バックフィルは2026-04-05完了済み（118,276件）

### Pending Todos

None yet.

## Session Continuity

Last session: 2026-04-05
Stopped at: Roadmap created for v7.0 (Phases 15–17). Ready to plan Phase 15.
Resume file: None
