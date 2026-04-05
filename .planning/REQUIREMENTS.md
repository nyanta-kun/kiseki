# Requirements: GallopLab (kiseki) — v7.0

**Defined:** 2026-04-05
**Core Value:** 指数と期待値に基づく「買うべきレース・買うべき馬」の提示

## v7.0 Requirements

管理画面整備: タブ再構成・ユーザーテーブル改善・データ取得管理・PAID_MODE設定のDB化。

### Admin（管理画面）

- [x] **ADMIN-01**: 管理画面がタブUI（ユーザー / データ / 設定）に再構成される
- [x] **ADMIN-02**: 登録ユーザーテーブルの各行が1行表示（whitespace-nowrap + テーブル内横スクロール）
- [x] **ADMIN-03**: 登録ユーザーテーブルが10件ページングで閲覧できる
- [x] **ADMIN-04**: 登録ユーザーテーブルに予想家名（yoso_name）・公開設定（is_yoso_public）が表示される
- [x] **ADMIN-05**: データタブで年/月単位のレースデータ取得状況（件数）を確認できる
- [x] **ADMIN-06**: データタブで未取得月のデータをUIからWindows Agentへ取得指示できる
- [ ] **ADMIN-07**: 設定タブでPAID_MODEのON/OFFをUI上で切り替えられる
- [x] **ADMIN-08**: PAID_MODEがDBで管理され、フロントエンドが起動時にAPIから動的取得する

## Future Requirements

### Admin（拡張候補）

- **ADMIN-F01**: 招待コード発行と同時にユーザーへメール送信
- **ADMIN-F02**: データタブでWindows Agentのリアルタイムステータス表示

## Out of Scope

| Feature | Reason |
|---------|--------|
| マイページへの予想設定移動 | /yoso/settings に実装済みのため不要 |
| ユーザーへのメール通知 | Stripe未導入フェーズのため対象外 |
| Stripe決済 | 法人化方針未確定のため今回対象外 |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| ADMIN-01 | Phase 15 | Complete |
| ADMIN-02 | Phase 15 | Complete |
| ADMIN-03 | Phase 15 | Complete |
| ADMIN-04 | Phase 15 | Complete |
| ADMIN-05 | Phase 16 | Complete |
| ADMIN-06 | Phase 16 | Complete |
| ADMIN-07 | Phase 17 | Pending |
| ADMIN-08 | Phase 17 | Complete |

**Coverage:**
- v7.0 requirements: 8 total
- Mapped to phases: 8
- Unmapped: 0 ✓

---
*Requirements defined: 2026-04-05*
*Last updated: 2026-04-05 after initial definition*
