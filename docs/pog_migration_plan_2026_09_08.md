# POG 移植（統合 Phase 5）設計 — 2026-09-08

sekito → kiseki 統合の Phase 5。**POG は sekito に残る機能のうち唯一「ユーザーが
書き込む」もの**で、いまも毎日動いている（通知の最終送信 2026-09-06）。
Phase 2 までのスクレイパ移設とは質も量も違うので、着手前に地図を残す。

関連: `docs/`（統合の全体計画は memory `sekito-kiseki-integration`）

---

## 0. 前提の訂正 — provisional_horses は関係ない

2026-09-08 に決着した。`keiba.provisional_horses` が 0 行なのは**障害ではなく廃案の跡**で、
POG の前提は壊れていなかった。

- 93行は 2026-05-04〜06 に手で2回走らせた `scrape_netkeiba_2yo.py` の産物。
  増分走査（`.scrape_state` の last_known_id 以降だけ見る）の設計上それ以上拾えなかった
- sekito が `bin/scrape/netkeiba-horses-bulk`（netkeiba 馬検索の全件ページング）へ
  **明示的に置き換えた**。同スクリプトの docstring に理由がそのまま書いてある
- 93行は **1件もマージされていない**（`merged_horse_id` 全件 NULL）
- kiseki 側の死んだ経路は **PR #510 で撤去済み**

→ Phase 5 は provisional_horses の復活ではなく、**bulk スクレイパと馬マスタを
kiseki へ持つ**話。

---

## 1. 実測した規模（2026-09-08）

### データ

| テーブル | 行数 | 中身 |
|---|---:|---|
| `sekito.horse` | 16,926 | 馬マスタ。2024年産 7,855 / 2023年産 7,764 / それ以前は各70前後 |
| `sekito.pog_user` | 1,401 | **指名（2005〜2026 の22年分）**。キーは `netkeiba_horse_id` |
| `sekito.pog_group` | 22 | 年度別グループ（1年=1グループ） |
| `sekito.pog_aobon` | 7,818 | 青本データ（34列・販売価格 / 生産者 / 写真 / 寸評5本） |
| `sekito.pog_notifications` | 323 | 通知履歴（result 198 / barrier 63 / entry 62） |
| `sekito.pog_roll` | 0 | ドラフトのサイコロ。年ごとに使い捨て |
| `sekito.users` | 10 | ← `keiba.users`(11) と **email で対応付ける**必要がある |
| `sekito.mv_horse_runs` | 1,342,915 | keiba + chihou の race_results 統合マテビュー |

### コード

| 面 | 規模 |
|---|---|
| API | `backend/routes/pog.js` **2,668行 / 44 endpoint** |
| UI | `frontend/src/pages/pog/` **9ページ・約4,500行 TSX** + `PogRecentRacesTable`(289行) |
| ジョブ | 3本すべて**稼働中**（下表） |
| 供給 | `netkeiba-horses-bulk`（手動・年1回）/ `pog-horse-netkeiba`（**死亡**・0行の provisional を読む） |

稼働中のジョブ（`sekito.scripts_schedules` の有効6件のうち3件が POG）:

    93  bin/notify/pog-result         */10 10-23 * * *
    94  bin/notify/pog                0 13 * * *
    95  bin/notify/pog-weekly-entries 30 19 * * 3

通知先は**年度ごとに違う**: 2024=LINE / 2025=Discord(LINE 併用) / **2026=Discord のみ**。

---

## 2. 依存の地図

```
sekito.pog_user(1,401) --netkeiba_horse_id--> sekito.horse(16,926)
                                                 ↑ netkeiba-horses-bulk（年1回・手動）
sekito.pog_group(22) --group_id--> sekito.pog_user
sekito.users(10) --uid--> sekito.pog_user

通知3ジョブが読むもの:
  sekito.v_entries / v_races / racecourse   ← keiba.* へのビュー
  keiba.projected_entries / keiba.racecourse_map   ← 既に kiseki 側
  sekito.pog_notifications                  ← 重複送信の抑止

統計系 API（44本の大半）が読むもの:
  sekito.mv_horse_runs(1.34M)  ← keiba.race_results + chihou.race_results
```

🔴 **結合キーはすべて `netkeiba_horse_id`（内部 id ではない）。**
馬マスタを keiba スキーマへ移しても指名は壊れない。これが移植を現実的にしている一点。

---

## 3. 🔴 順序の罠（本丸）— 書き込み経路の依存は 2026-09-08 に解消

`keiba.race_results` と `chihou.race_results` の文トリガが
**sekito スキーマの関数** `sekito.notify_mv_horse_runs_dirty()` を呼んでいた。

- sekito コンテナを先に落とす → POG のマテビューが更新されなくなる
- sekito スキーマを先に DROP → **本番のレース結果取り込みがトリガ関数欠損で失敗する**

### 実測した依存の全体（2026-09-08）

`keiba` / `chihou` の中で sekito を参照しているオブジェクトを全部数えた:

| 種別 | 件数 |
|---|---|
| 関数 | **0** |
| ビュー・マテビュー | **0** |
| 外部キー | **0** |
| トリガ | **2**（`trg_notify_mv_horse_runs_dirty_{keiba,chihou}`） |

→ **書き込み経路の sekito 依存はこの2トリガだけ**だった。

### 5b-1（完了）

同じ本体の関数を `keiba` へ作り、両トリガの参照先を差し替えた。
**通知チャンネル名 `mv_horse_runs_dirty` は変えていない**ので、sekito の
`services/mv-refresh-listener.js` はそのまま両マテビューを REFRESH し続ける。挙動の変化はゼロ。

⚠️ sekito 側の関数は**残してある**（後述のとおり `mv_graded_wins` がまだ sekito に居るため）。
⚠️ sekito の node-pg-migrate は適用済み記録があるので再実行されないが、
**DB を作り直すと `20260503000003` が参照先を sekito へ戻す**。移植完了時に消すこと。

---

## 4. サブフェーズ案

順序には理由がある。**5b は他と独立で最も危険なので単独・最優先**。

| # | 内容 | 依存 | 備考 |
|---|---|---|---|
| ~~5b-1~~ | ~~トリガの参照先を `keiba.notify_mv_horse_runs_dirty()` へ~~ | なし | ✅ **2026-09-08 完了**。書き込み経路の sekito 依存は消えた |
| 5b-2 | `keiba.mv_horse_runs` + kiseki 側 REFRESH リスナー | なし | 消費者（5d）と同時でよい。先に作ると REFRESH が二重に走るだけ |
| 5b-3 | `mv_graded_wins` の移設 | **`sekito.entries` / `sekito.races` の移設** | 🔴 単独では**動かせない**（下記） |
| 5a | 馬マスタ（`sekito.horse` → `keiba.pog_horses`）+ `netkeiba-horses-bulk` 移植 | なし | 供給を止めない。年1回なので cron 化は任意 |
| 5c | 認証の接続（`sekito.users` ↔ `keiba.users` を email で対応付け） | **Phase 4（ブランド・認証方式）の決定** | ここが Phase 5 全体のボトルネック |
| 5d | 読み取り API + 画面（統計・成績・ランキング） | 5a, 5b | 44 endpoint を**使われている分だけ**に絞る |
| 5e | 書き込み（ドラフト指名・サイコロ・管理） | 5c, 5d | 🔴 **ドラフト期間外にカットオーバーする** |
| 5f | 通知3ジョブ移設 + nginx 切替 + sekito 停止 | 全部 | `keiba.cron_runs` に記録させる |

### 🔴 マテビュー2本は性質が違う

| マテビュー | 依存 | 移設可否 |
|---|---|---|
| `mv_horse_runs`(1.34M) | `keiba.*` と `chihou.*` **のみ** | **単独で移せる** |
| `mv_graded_wins` | `sekito.entries` / `sekito.races` / `sekito.jvlink_to_sekito_course()` | **移せない**。sekito の実データ移設待ち |

memory の「マテビュー2本とトリガを keiba へ移設」は、**2本を同時にはできない**。
`mv_graded_wins` は netkeiba 由来の `sekito.entries` で馬IDを解決しているため、
`sekito.entries` / `sekito.races` の引っ越しとセットでしか動かせない。

両マテビューは同じ通知チャンネルで REFRESH されているので、
**リスナーを kiseki へ移す時点では両方を REFRESH できる必要がある**
（＝ kiseki 側リスナーの導入は sekito リスナーの停止とは別のタイミングでよい）。

---

## 5. 着手前に決める / 測ること

1. **44 endpoint のうち実際に使われているのはどれか。**
   nginx のアクセスログで実測してから移植対象を決める。2,668行を素直に写すのは無駄が大きい
2. **青本データ（`pog_aobon` 7,818行）の入手経路。**
   毎年どうやって入れているのか（`POG2026_指名候補スコア.xlsx` が sekito リポジトリにある）。
   自動化されていないなら移植対象は「テーブルと取込口」だけでよい
3. **通知の宛先。** 2026 は Discord のみ。LINE 経路を移植する必要があるか
4. **ドラフトの時期。** 年1回・春（2026年産の bulk 収集は 2026-05）。
   2026 の指名は 73 件で確定済み → **次のドラフトは 2027年春**。5e はそこから逆算する
5. **`sekito.users`(10) と `keiba.users`(11) の対応表。** email で機械的に付くか、
   手で決める必要があるか

---

## 6. やってはいけないこと

- 🔴 sekito スキーマの DROP を 5b より先に行う（本番のレース結果取り込みが死ぬ）
- 🔴 ドラフト期間中に 5e を切り替える
- 🔴 通知3ジョブを止めたまま移植する（POG は現役。最終送信 2026-09-06）
- 🔴 テーブル追加とその参照を同じ PR に入れる（デプロイは「コード切替 → マイグレーション」の順）
