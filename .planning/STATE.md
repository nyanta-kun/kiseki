---
gsd_state_version: 1.0
milestone: v7.0
milestone_name: 管理画面整備
status: planning
stopped_at: Completed 15-02-PLAN.md
last_updated: "2026-04-05T10:31:26.993Z"
last_activity: 2026-04-05 — Milestone v7.0 roadmap created (Phases 15–17)
progress:
  total_phases: 3
  completed_phases: 1
  total_plans: 2
  completed_plans: 2
  percent: 50
---

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

Progress: [█████░░░░░] 50%

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
| Phase 15 P01 | 5 | 1 tasks | 1 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- v7.0 start: PAID_MODEをDBで管理する方針（keiba.app_settingsテーブル新設）
- v7.0 start: Windows AgentへのUI取得指示はrecentモードのエージェントコマンド経由
- [Phase 15]: model_config={'from_attributes': False}を維持し_make_user_response()での手動セット方式を継続
- [Phase 15]: 型定義（User, InvitationCode）は AdminTabs.tsx で export し他コンポーネントが import type で再利用する構成を採用
- [Phase 15]: page.tsx のインライン Server Action を全削除し CodesTab/UsersTab が actions.ts から直接 import する形に統一
- [Phase 15]: Server Action への引数渡しは .bind(null, id, patch) パターンで型安全な呼び出しを実現

### Known Issues / Blockers

- netkeibaバックフィル（2024-01〜2025-03のremarks）: 未収集。VPS負荷対策必須（深夜実行推奨）
- VPS SSHプロンプトハング: 再起動後に発生することあり

### Technical Debt

- 巻き返し指数重み最適化: バックフィルデータが揃い次第、composite v9 の weight 再最適化
- v9バックフィルは2026-04-05完了済み（118,276件）

### Pending Todos

None yet.

## Session Continuity

Last session: 2026-04-05T10:29:04.023Z
Stopped at: Completed 15-02-PLAN.md
Resume file: None
