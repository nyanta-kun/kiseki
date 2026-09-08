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

### 5b-1 の本番検証（2026-09-08 16:21 JST デプロイ）

    alembic head                202609081613_shared
    keiba.race_results   ->  keiba.notify_mv_horse_runs_dirty
    chihou.race_results  ->  keiba.notify_mv_horse_runs_dirty
    16:28:27  [mv-refresh] scheduled refresh   ← 差し替え後に届いた通知

sekito のリスナーが差し替え後も通知を受け取っている（＝端から端まで通っている）ことを
実データで確認した。トリガの状態だけを見て済ませないこと。

### REFRESH の実測コスト — 5b-2 を単独で先行させない根拠

sekito-backend の直近24時間のログ:

    REFRESH 回数        96 回/日
    所要時間            中央値 30.9秒（min 22.7 / max 37.7）
    合計                **約50分/日**（DB 時間の約 3.4%）
    debounce            **本番は 5分**（リポジトリの実装は 30秒。乖離している）

`keiba.mv_horse_runs` を先に作って kiseki 側でも REFRESH すると、
**消費者がいないのに +50分/日**を払うことになる。5b-2 は消費者（5d）と同時に行い、
その時点で sekito 側リスナーを `mv_graded_wins` だけに絞る。

---

## 5. 着手前に決める / 測ること

1. ~~44 endpoint のうち実際に使われているのはどれか~~ → **実測済み（下記 §5.1）**
2. **青本データ（`pog_aobon` 7,818行）の入手経路。**
   毎年どうやって入れているのか（`POG2026_指名候補スコア.xlsx` が sekito リポジトリにある）。
   自動化されていないなら移植対象は「テーブルと取込口」だけでよい
3. **通知の宛先。** 2026 は Discord のみ。LINE 経路を移植する必要があるか
4. **ドラフトの時期。** 年1回・春（2026年産の bulk 収集は 2026-05）。
   2026 の指名は 73 件で確定済み → **次のドラフトは 2027年春**。5e はそこから逆算する
5. **`sekito.users`(10) と `keiba.users`(11) の対応表。** email で機械的に付くか、
   手で決める必要があるか

### 5.1 エンドポイントの実測（nginx access log・2026-08-25〜09-08 の14日）

`/api/pog/*` に来たリクエストは **44本中 7本だけ**。うち4本がほぼ全量:

| endpoint | 件数 |
|---|---:|
| `GET /owners-history` | 143 |
| `GET /group/:id/recent-races` | 143 |
| `GET /owners` | 142 |
| `GET /group/:id/user/:uid/horses` | 111 |
| `GET /group/:id/user/all/horses` | 9 |
| `GET /group/:id/sire-count` | 1 |
| `GET /graded-wins` | 1 |

画面ロード（SPA ルート）も3本だけ:

    /pog-details                22
    /pog/group/:id/user/:uid    18
    /pog/group/:id/user/all      3

日別は 8〜107件で、**土日（開催日）に寄る**。

→ **移植の第一陣は 5 endpoint + 2〜3画面**で足りる。2,668行を素直に写す必要はない。

🔴 **「残り37本は死んでいる」と読んではいけない。**
ドラフト系（`/suggest` `/aobon` `/roll` `/confirm` `POST /user`）は**年1回・春だけ**
使われる。14日の窓はオフシーズンなので、出てこないのは当然。
記録室・ランキング・スコア集計系も「めったに見ない画面」であって不要とは限らない。
判断材料にしてよいのは**移植の順序**であって、削除の可否ではない。

## 5.2 🔴 移設元の取得経路は既に死んでいた（2026-09-08 実測）

`netkeiba-horses-bulk` は `GET /?pid=horse_list&under_age=N&over_age=N` を叩いていたが、
今日の実測では **97バイトのスタブ**しか返らない（ログイン済みセッションでも同じ）。

    GET  /?pid=horse_list ...           97 バイト（`URL: /?pid=horse_list` とだけ書かれた赤字）
    GET  /?pid=horse_search_detail      76 バイト（同じくスタブ）
    POST /                              DB トップページ（74KB）が返る
    GET  /horse/search_all.html         JS 描画の枠だけ（馬リンク 0）
    GET  /horse/list.html?age_f=2&...   **これが現行**（388KB・7,943件・100件/ページ）

そのまま移植していたら「rc=0・0件」を作るところだった。

### 現行の契約（`/horse/search_detail.html` のフォームから読んだ）

    GET https://db.netkeiba.com/horse/list.html
      range=all  word=  match=p
      age_f / age_t     ← **馬齢**。生年ではない
      sort=name-asc     limit=20|50|100     page=N

`limit=100` が使えるので移設元（20件/ページ）の 1/5 のリクエスト数で済む（2024年産で 80 ページ）。

### 移設元が「静かに間違う」作りだった3点

| | 移設元 | 実測 |
|---|---|---|
| 馬齢 | `2026 - 生産年` と**年を固定**で記述 | 翌年から黙って別世代を取る |
| 馬ID | 数字10桁前提（先頭4桁＝生産年） | **外国産馬は `000a02d612`**。1ページ100頭中 15頭 |
| 馬名 | `name[:15]` で切り詰め | 1ページ100頭中 **11頭**が15文字超（例 `Angel Numberの2024`） |

加えて `birthday` に `{生産年}-01-01` を**捏造**していた（一覧に生年月日は無い）。
実際 `sekito.horse` の 2024年産 7,855頭は**全件 01-01**。移植版は入れない。

父・母・母父のセルには詳細リンクのアイコンが同居するので、セル全体のテキストを取ると
`Palace Pier[]` になる。最初の `<a>` の title を使う。

---

## 6. やってはいけないこと

- 🔴 sekito スキーマの DROP を 5b より先に行う（本番のレース結果取り込みが死ぬ）
- 🔴 ドラフト期間中に 5e を切り替える
- 🔴 通知3ジョブを止めたまま移植する（POG は現役。最終送信 2026-09-06）
- 🔴 テーブル追加とその参照を同じ PR に入れる（デプロイは「コード切替 → マイグレーション」の順）
