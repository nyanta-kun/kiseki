# netkeirin 入稿の Cloudflare Zero Trust 対応 — 現状調査と設計案（2026-11-10）

依頼: 「二軸探偵｜netkeirin入稿処理の Mac mini 移行 検討事項」（2026-11-10）
範囲: **調査と設計提案まで**。本番の cron / launchd / 入稿設定 / DB は変更していない。

## 結論（先に）

- **推奨は案 D「VPS はそのまま、netkeirin 向けの通信だけ Mac mini を出口にする」**。
  Mac mini から VPS へ逆向きの SSH トンネル（SOCKS）を張り、VPS の入稿処理は
  netkeirin への HTTP だけをそのトンネル経由で出す。Cloudflare から見ると
  **Mac mini（Cloudflare One Client 接続済み）からの通信**になる。
- 理由: netkeirin への通信は `keirin/src/netkeirin_client.py` の1か所に集まっており、
  変更は「プロキシを環境変数で差す」＋「入稿前チェック」で済む。**DB の書き手は VPS
  1本のまま**なので二重入稿の心配が増えない。入稿の中身は今と同じコードが作るので、
  並行ドライランでの突き合わせも不要になる。
- **11/17 とは無関係に、今すぐ直すべき穴が2つある**:
  1. ログインに全件失敗した日、**Discord に1通も出ず、誰も気づけない**（§1.6）
  2. 承認制（入稿案だけ作るモード）でも **netkeirin へのログインを必ず通る**ため、
     VPS がはじかれると入稿案すら作れない（§1.7）
- 最大の未確定事項は、**プログラム（Python の requests）からの通信を、WARP 接続端末
  経由なら Access が通すか**。これは運営の切替後にしか確かめられない。
  通さない場合は案 A（Mac mini で処理を動かす）でも同じくはじかれるので、
  代替は「`cloudflared` のアクセストークン方式」か「運営にサービストークンを依頼」になる（§5）。

---

## 1. 現状の棚卸し（VPS・コードと設定から確認）

### 1.1 netkeirin にアクセスしている処理

| # | 処理 | 起動元・時刻（実測） | アクセス先 | 読み書きするテーブル | 失敗時の通知 |
|---|---|---|---|---|---|
| 1 | 朝の型ラボ入稿 `keirin/scripts/netkeirin_submit_type_lab.py <日> morning` | VPS crontab `0 7` → `daily_picks_wt.sh` → `type_lab_daily.sh`。**入稿は 07:09 開始・約11秒**（11/8〜11/10 で 38〜57件） | 入稿ツール `tool.syakenv2.netkeiba.com/bettool`（ログイン・場コード取得・商品登録・公開） | 読: `type_lab_picks` `wt_races` `netkeirin_settings` ほか／書: `netkeirin_submissions` `submission_skips` | 成功時のみ Discord `netkeirin`。**失敗は `submission_skips` に記録されるだけ**（§1.6） |
| 2 | 昼・夕の波 `type_lab_wave.sh noon / evening` | VPS crontab `5 13` / `5 18`（実測 13:11 / 18:11 開始） | 同上 | 同上。未入稿レースは `type_lab_morning.py --race-key` で組み直してから入稿 | 同上 |
| 3 | 自動公開 `auto_publish_submitted()` | 1・2 の同じ実行の最後 | 同上（`action=change_status`） | `netkeirin_submissions.status` → `published` | 公開失敗は入稿通知の本文に「⚠️ 公開失敗 n件」 |
| 4 | 画面からの操作（galloplab `/keirin/review` 等） | backend コンテナ → `http://172.18.0.1:8010` → **VPS ホストの `keirin-webhook.service`（systemd）** → 各スクリプト | 同上 | `/submit-race`（型ラボ `--race-key`）、`/approve` `/cancel` `/publish`（`netkeirin_approve_wt.py`）、`/publish-sync`（`netkeirin_sync_status.py`）、`/publish-wait`（`netkeirin_publish_wait.py`） | 画面へ JSON で返す |
| 5 | 売上取得 `scripts/scrape_netkeirin_sales.sh` | VPS crontab `40 9`（keirin の venv で実行・Docker ではない） | 分析支援ツール `umaiaggre.yosoka.netkeiba.com/tool_keirin`（**独自のログイン API**・ID/PW は入稿ツールと同じ） | 書: `keirin.netkeirin_sales_daily` / `netkeirin_sales_race` | 終了コードをログへ。Discord には売上通知 |
| 6 | 予想家成績 `collect_netkeirin_rivals.sh` | VPS crontab `50 1` | 公開ページ `keirin.netkeiba.com/yoso/api/...`（**ログインなし**） | 書: `netkeirin_rival_goods` | ログのみ |
| 7 | 日次アップデート（Claude in Chrome・10:30） | **端末は不明**。この Mac mini の launchd には該当ジョブが無い | 分析支援ツール | — | — |

- 旧経路（`netkeirin_submit_wt.py` の通常ランク・`submit_marquee_wt.py`・`reselect_7s_evening.py`）はクライアントを持っているが、
  現在の crontab からは呼ばれていない（Web の手動入稿も 2026-09-03 から型ラボ経路）。
- `notify_pending_approvals_wt.py`（8:30/14:30/19:30）は DB と Discord だけで、netkeirin へは通信しない。

**Cloudflare の対象になりうるもの**

| ドメイン | 種類 | 対象か |
|---|---|---|
| `tool.syakenv2.netkeiba.com` | 入稿ツール（管理ツール） | **ほぼ確実に対象**（今回の通知の本題） |
| `umaiaggre.yosoka.netkeiba.com` | 分析支援ツール | **運営に確認**（§8）。認証方式は入稿ツールと別系統 |
| `keirin.netkeiba.com` | 公開ページ | 対象外の見込み |

11/10 18:45 に Mac mini から各1回 GET した結果（**この時点で Cloudflare は未適用**）:

```
tool.syakenv2   302 -> /bettool/auth/login.html        server: Apache（cf-ray なし）
umaiaggre       302 -> /tool_keirin/auth/login.html    server: Apache（cf-ray なし）
keirin.netkeiba 200                                    server: Apache
```

VPS からの同じ確認は、こちらからの実行が許可されなかった。§7 の手順にユーザー作業として入れてある。
VPS からの入稿は 11/10 時点で正常（朝38件・昼8件）。

### 1.2 入稿までの依存関係

```
07:00 daily_picks_wt.sh
  ├ 朝オッズ退避 snapshot_morning_odds_wt.py → wt_odds_snapshot
  ├ 朝の出走表退避 snapshot_morning_entries_wt.py
  └ type_lab_daily.sh
      ├ type_lab_morning.py（型判定・買い目・配分）→ type_lab_picks
      ├ netkeirin_submit_type_lab.py morning  ← ここだけが netkeirin へ通信
      │    ゲート（軸信頼・日次上限・締切）→ 入稿 → 自信あり選定 → 高額枠 → 逃げ先頭ライン → 自動公開
      └ settle_type_lab_picks.py（採点）
13:05 / 18:05 type_lab_wave.sh → build_race_shapes.py → netkeirin_submit_type_lab.py noon/evening
```

netkeirin へ通信するのは入稿スクリプト（とクライアントを使う補助スクリプト）だけ。
モデル・買い目作成・DB は VPS で完結している。

### 1.3 データの所在

- **正本は VPS の PostgreSQL（`hrdb` の `keirin` スキーマ）**。crontab で `KEIRIN_DB_URL` を設定し、
  `keirin/src/database.py::get_connection()` が PG に接続する（SQLite への無言フォールバックは廃止済み）。
- 朝バッチに `migrate_sqlite_to_pg.py` が残っているが、入稿系は `get_connection()` 経由で PG に直接読み書きしている。
- 入稿の記録: `keirin.netkeirin_submissions`（主キー `(race_key, rank_key)`・CHECK 制約なし）。
  11月分は 531行すべて `published`（自動公開 ON で運用中）。
- 見送りの記録: `submission_skips`。設定: `netkeirin_settings`（`_global.require_approval` が承認制／自動公開の切替）。

### 1.4 認証情報の保管場所

| もの | 場所 |
|---|---|
| ID・パスワード | VPS `keirin/.env` の `NETKEIRIN_LOGIN_ID` / `NETKEIRIN_PASSWORD`（`keirin-webhook.service` も `EnvironmentFile` で読む）。売上取得もこれを使う |
| 入稿ツールの Cookie | VPS `keirin/data/netkeirin_session.json`（**権限 664**。600 にすべき） |
| 場コードのキャッシュ | VPS `keirin/data/netkeirin_venue_codes.json`（39場） |

⚠️ 調査中に、VPS の crontab に **DB 接続文字列がパスワードごと平文で書かれている**ことを確認した（今回の範囲外。値はこの文書に載せていない）。

### 1.5 ログ

VPS `keirin/data/logs/` の `netkeirin_<日>.log`（朝）、`type_lab_<日>.log`（昼・夕）、`cron.log`、
`netkeirin_submit.log`（webhook 経由）。売上は `logs/scrape_netkeirin_sales.log`。

### 1.6 🔴 失敗が無言になる経路（11/17 に起きる形）

1. Cloudflare にはじかれると、`NetkeirinClient._is_logged_in()` は False、続く `login()` の POST も失敗し、
   `submit_pick_multi()` は全レース `"ログイン失敗"` を返す。
2. 各レースは `submission_skips` に `submit_failed` として記録されるだけ。
3. Discord 通知は **`n_ok > 0` のときしか送られない**ので、全件失敗の日は1通も出ない。
4. スクリプト自体は正常終了するので、`type_lab_daily.sh` の「⚠️ 入稿に失敗」も出ない。
5. 「当日の入稿0件」を拾う監視も見当たらない（`nightly_review.sh` は翌 00:10 の振り返り）。

→ **入稿前チェックと即時通知は、移行方式に関係なく今すぐ要る。**

### 1.7 🔴 承認制でもログインを通る

`NetkeirinClient(propose_only=True)` の docstring は「ログインもしない」だが、実装の
`submit_pick_multi()` は `_post_goods()` で止まる**前に** `login()` と `resolve_venue_code()`（netkeirin への GET）を通る。
そのため VPS がはじかれると、承認制に切り替えても入稿案が作れない。案 A を取る場合の前提になる修正。

### 1.8 二重入稿の防止（現状）

| 層 | 仕組み |
|---|---|
| DB | 主キー `(race_key, rank_key)`。実行開始時に `_already_submitted()` で既存行（入稿案・送信済み・公開済み・取消。※「入力待ち取消」だけ除く）を読み、あるレースは出さない |
| 1実行の中 | 同じ回で出したレースを記憶し、1レース1商品を守る |
| netkeirin 側 | 同じレースへ再送すると**同じ商品の上書き**になる（`submit_pick_multi` の docstring・2026-08-06 実機確認）。公開後は修正不可 |
| 締切 | `_load_closed_races()`：発走15分前を過ぎたレースは出さない（`src/submit_window.py`） |
| 排他 | 朝・日中・前日処理は `flock`（`wt_picks_writer.lock`）。**`type_lab_wave.sh` には無い** |

弱い点: 記録は **送信成功の後** に書く。送信直後に落ちると次の波で再送するが、netkeirin 側が上書きなので
公開前なら実害は無い。承認経路 `approve_and_submit()` 自体には締切チェックが無く、Web API 側（409）に頼っている。

---

## 2. 役割分担の設計案

| 案 | 内容 | 変更量 | 二重入稿 | 11/17 に間に合うか |
|---|---|---|---|---|
| A. Mac mini が取りに行く | VPS は入稿案（`proposed`）だけ作り、Mac mini が DB から読んで送る | 中〜大（送信ループ新設・webhook 5経路も Mac mini へ・§1.7 修正） | 書き手が2台になる。取り合い防止の仕組みが要る | ぎりぎり |
| B. VPS が送り込む | VPS から Mac mini の入稿 API を呼ぶ | 中 | 同上 | 自宅に受け口が要る。非推奨 |
| C. 全部 Mac mini へ | パイプライン全体を移す | 大（モデル 212MB・実行時状態・cron 群） | 単一 | 間に合わない |
| **D. 出口だけ Mac mini（推奨）** | VPS の処理はそのまま。netkeirin 向け HTTP だけ Mac mini 経由で出す | **小**（クライアントにプロキシ設定・入稿前チェック・トンネル常駐） | **今と同じ（書き手は VPS 1本）** | **間に合う** |

### 2.1 案 D の構成

```
 VPS（変更最小）                                   自宅 Mac mini
 ┌──────────────────────────────┐                ┌──────────────────────────────┐
 │ cron / webhook                │                │ launchd（常駐・自動再接続）     │
 │  netkeirin_submit_type_lab.py │                │  ssh -N -R 127.0.0.1:18080    │
 │   NetkeirinClient             │  SSH(22番)      │      sekito                    │
 │    proxies = socks5h://       │◀──────────────│  （逆向きの SOCKS プロキシ）     │
 │      127.0.0.1:18080 ─────────┼──トンネル──────▶│  ssh クライアントが代理接続     │
 │ PostgreSQL（正本）・モデル     │                │     │                          │
 └──────────────────────────────┘                │  Cloudflare One Client（常時接続）│
                                                  └─────┼────────────────────────┘
                                                        ▼
                                        Cloudflare（nd-group）→ tool.syakenv2.netkeiba.com
```

- Mac mini 側は `ssh -N -R 127.0.0.1:18080 sekito`（OpenSSH の逆向き動的転送。Mac mini 側の ssh が SOCKS として振る舞う）。
  VPS の sshd は通常ユーザーの転送を禁止していない（制限は `kisekilab` ユーザーだけ）。SSH は全開放なので WARP で送信元 IP が変わっても届く。
- VPS 側は `keirin/.env` に `NETKEIRIN_PROXY_URL=socks5h://127.0.0.1:18080` を置き、**netkeirin のクライアントだけ**がそれを使う。
  ⚠️ `HTTPS_PROXY` を crontab や systemd に入れてはいけない（winticket・Discord まで Mac mini 経由になる）。
  `socks5h` にするのは名前解決も Mac mini 側（＝ Cloudflare 側）で行うため。
- プロキシが設定されているのにトンネルが落ちているときは**直接接続へ戻さない**（どうせはじかれる＋原因が分かりにくくなる）。「出口断」として即通知する。
- Mac mini には netkeirin の ID・パスワードも DB 接続情報も置かない。
- 入稿キュー・状態遷移は現状のまま（`proposed → submitted → published` / `deleted`）。webhook も backend も無変更。
- 切り戻しは `NETKEIRIN_PROXY_URL` を消すだけ（11/17 より前なら VPS 直結に戻る）。

### 2.2 案 A（予備）を使う場面

案 D は「Cloudflare から見て Mac mini 発の通信」になる点で案 A と同じなので、
**Access がプログラムからの通信を拒む場合は案 A に切り替えても解決しない**（§5）。
案 A が意味を持つのは「逆トンネルを常駐させたくない／不安定」なときに限る。その場合の作りは:

- `netkeirin_settings._global` に送信方式（VPS 直送／Mac mini 自動送信／人の承認）を持たせ、
  **1か所の値で送り手を1台に決める**（VPS と Mac mini を同時に有効にしない）。
- VPS は `proposed` を作るだけ（§1.7 の修正が前提）。
- Mac mini は 1分ごとに `proposed` を `UPDATE ... SET status='sending' WHERE status='proposed'` で
  **取ってから**送る（取れた行だけ送る）。成功で `submitted`→公開、失敗で `proposed` に戻して理由を残す。
  `sending` のまま10分以上残った行は自動で再送せず通知する。締切後の行は送らずに取消＋通知。
- 画面の承認・取消・公開も netkeirin へ通信するため、webhook の該当5経路も Mac mini で動かす必要がある。

---

## 3. Mac mini の実行環境

| 項目 | 状態 | 内容 |
|---|---|---|
| 機種 | ✅ 確認済み | Mac mini（arm64）。この Claude Code はこの機体で動いている |
| タイムゾーン | ✅ | Asia/Tokyo |
| 時刻のずれ | ✅ 確認済み（11/10） | `sntp time.apple.com` でずれ −0.0003秒（1台目のサーバーはタイムアウト、2台目で応答）。自動同期の設定自体は管理者権限が要り未確認だが、ずれが無いので実害なし |
| スリープ無効・停電後の自動起動 | ✅ 確認済み（11/10） | `pmset -g custom`（AC）で `sleep 0` / `disksleep 0` / `autorestart 1` / `tcpkeepalive 1`。画面だけ10分で消える（`displaysleep 10`・影響なし） |
| 自動ログイン | ✅ 確認済み（11/10） | FileVault は Off、自動ログインは `ysuzuki`。停電から復旧すると自動でログインし、LaunchAgent（トンネル）も手作業なしで動き出す |
| 既存のジョブ基盤 | ✅ | `scripts/macmini/run_job.sh`（ハートビート・Discord 通知・多重起動防止）。既に kiseki の LaunchAgent が16本動いている |
| Python | ✅（案 D では不要） | `~/GitHub/kiseki/keirin/.venv` は 3.12（VPS も 3.12.3）。Homebrew は 3.14 |
| VPS への SSH | ✅ | `ssh sekito` で接続できる。autossh は無いので launchd の `KeepAlive` で再接続させる |
| DB への経路 | ⚠️ 要対応 | 今は Mac mini → VPS 5432 に**直結**（ufw で自宅の IP 2つだけ許可）。WARP 接続後は送信元 IP が変わり**切れる見込み**（§4） |
| Tailscale | 未稼働 | アプリはあるが CLI が `Failed to load preferences`。WARP と同時に使うと経路を取り合う恐れ |
| Cloudflare One Client | 未導入 | `warp-cli` も無い |
| 秘密情報 | ✅（案 D） | Mac mini に netkeirin の認証情報は置かない |

---

## 4. Cloudflare One Client（macOS）まわり

### 4.1 全通信がトンネル経由になったときの影響（要確認）

| 通信 | 予想される影響 |
|---|---|
| VPS への SSH（案 D のトンネル・DB バックアップ） | 届く（22番は全開放）。送信元 IP が変わるだけ |
| VPS PostgreSQL への直結（`psql service=kiseki`・DB を直接読む LaunchAgent） | **切れる**（ufw は自宅 IP だけ許可）。SSH トンネル経由へ切り替えるか、VPS の IP を Split Tunnel で除外する |
| Parallels の Windows VM（JV-Link / UmaConn） | VM の通信は Mac の NAT を通るので**Cloudflare 経由になりうる**。JRA-VAN・UmaConn への接続が変わる。要実測 |
| netkeiba のスクレイプ（Mac mini の `scrape-projected` 等） | Cloudflare の共用 IP から出ることになり、netkeiba の IP 制限にかかる恐れ |
| Discord・GitHub・Claude Code | 通常は問題ないが、組織のフィルタ設定次第 |
| 自宅 LAN | 既定ではプライベート IP は除外されることが多いが、組織設定次第 |

- **Split Tunnel を利用者側で変えられるかは組織（nd-group）の設定次第**。運営に「netkeirin のドメインだけを通す（Include）設定にできるか」を聞くのが最善（§8）。
- 状態確認: macOS 版にも `warp-cli` が同梱される見込み。導入後に `warp-cli status` が使えるか確認する。
  ただし案 D では **VPS から実際に入稿ツールへ GET する**のが最も確実な確認になる（Mac mini の接続状態・トンネル・Access の認証が一度に分かる）。

### 4.2 再認証への備え

- 切れたときの見え方は切替後に実測して記録する。想定は「`*.cloudflareaccess.com` への 302」「Cloudflare の 403 ページ（`server: cloudflare`・`cf-ray`）」「ログイン画面の HTML」。
- 入稿前チェック（§5）でこれを「Cloudflare 認証切れ」と分類し、**再試行せず即通知**。再認証は鈴木さんがメールのコードを入力する。
- 10分おきの死活確認（§5）で、波の前に気づけるようにする。

---

## 5. 入稿前チェックと失敗時の扱い

**入稿前チェック**（`NetkeirinClient.preflight()` を新設し、各波の最初に1回）:
入稿ツールのトップへ転送を追わずに GET し、次のように分類する。

| 結果 | 判定 | 扱い |
|---|---|---|
| 200・本文に「ログアウト」 | 正常 | 続行 |
| 302 → `bettool/auth/login.html` | netkeirin のセッション切れ | 通常ログインして続行（現行どおり） |
| 302 → `*.cloudflareaccess.com`、または 403 で `server: cloudflare` | **Cloudflare 認証切れ** | **入稿を中止・再試行しない・即通知** |
| プロキシへ接続できない | **出口断**（Mac mini・回線・トンネル） | 中止・即通知 |
| タイムアウト・5xx | 一時的 | 間隔をあけて2回まで再試行 |

**通知**（Discord `system`）: 未入稿のレースと締切時刻（発走15分前）を並べ、確認画面のリンクを付ける。
手で入稿するときに必要な情報が通知だけで揃うようにする。

**今すぐ直すもの（§1.6）**: 入稿失敗が1件でもあれば、入稿0件でも通知する。全件失敗なら非0で終了する。

**死活確認**（VPS cron・10分おき・06:50〜21:00）: 上の入稿前チェックだけを実行し、**状態が変わったときだけ**通知する
（正常→異常・異常→正常）。波の前に再認証切れやトンネル断に気づける。

**締切ガード**: 現行の「発走15分前を過ぎたら出さない」をそのまま使う。

**Access がプログラムからの通信を拒む場合の代替**:
1. Mac mini で `cloudflared access login https://tool.syakenv2.netkeiba.com` → 得たトークンをリクエストのヘッダに付ける方式（Cloudflare が CLI 向けに用意している方法）。トークンの期限切れは同じく「認証切れ」で通知
2. 運営にサービストークンを依頼（用意されれば VPS 直結のまま続けられる）
3. どちらも無理なら、入稿だけブラウザ自動操作（Mac mini の Chrome）に寄せる。変更量が大きく 11/17 には間に合わない

---

## 6. ほかの netkeirin アクセス処理への影響

- **売上取得（09:40・分析支援ツール）**: 分析支援ツールが対象なら、同じ `NETKEIRIN_PROXY_URL` を `backend/scripts/scrape_netkeirin_sales.py` の Session にも差す。対象外ならそのまま。
- **予想家成績（01:50・公開ページ）**: 影響なしの見込み。11/17 以降のログで確認する。
- **日次アップデート（10:30・Claude in Chrome）**: どの端末の Chrome か確認が要る。Mac mini の Chrome なら、WARP 接続中に動けば影響なし。別の PC ならその端末にも Cloudflare One Client が要る。
- **X 自動投稿**: X API なので対象外。Mac mini で動かすなら §4.1 の経路の影響だけ確認する。

---

## 7. 変更点リスト

| 種別 | ファイル | 内容 |
|---|---|---|
| 修正 | `keirin/src/netkeirin_client.py` | `NETKEIRIN_PROXY_URL` があれば Session に設定／`preflight()` 新設（§5 の分類）／`propose_only` のときはログインせず、場コードはキャッシュだけで解決（§1.7） |
| 修正 | `keirin/scripts/netkeirin_submit_type_lab.py` | 冒頭で `preflight()`。認証切れ・出口断なら未入稿一覧つきで通知して終了（非0）／入稿失敗があれば `n_ok=0` でも通知 |
| 修正 | `keirin/scripts/type_lab_daily.sh` / `type_lab_wave.sh` | 入稿スクリプトの非0終了を Discord `system` へ |
| 新設 | `keirin/scripts/check_netkeirin_egress.py` | 死活確認（状態変化時のみ通知） |
| 修正（別 PR・jra 柱） | `backend/scripts/scrape_netkeirin_sales.py` | 分析支援ツールが対象なら同じプロキシ設定 |
| 依存 | `keirin/requirements*.txt` | `PySocks`（`requests[socks]`）。VPS の keirin venv には今は無い |
| 新設 | `keirin/scripts/macmini/com.kiseki.netkeirin-egress.plist`（keirin 柱に置く。ルートの `scripts/launchagents/` は shared） | `ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -R 127.0.0.1:18080 sekito` を `KeepAlive` で常駐 |
| 本番設定（要承認） | VPS `keirin/.env` | `NETKEIRIN_PROXY_URL=socks5h://127.0.0.1:18080` |
| 本番設定（要承認） | VPS crontab | 死活確認ジョブを追加 |
| 本番設定（要承認） | VPS `keirin/data/netkeirin_session.json` | 権限を 600 に |
| テスト | `keirin/tests/` | `preflight()` の分類（転送先・ステータス別）、プロキシ未設定時は従来どおり、`propose_only` で HTTP が一切出ないこと |

### 7.1 実装済み（2026-11-11・PR `feat/keirin-netkeirin-egress`）

上表のうち**売上取得（jra 柱）と本番設定以外はすべて実装した**。**プロキシを設定するまで本番の挙動は変わらない**
（`NETKEIRIN_PROXY_URL` が無ければ Session は従来どおり直結。入稿前チェックが1回 GET を増やすだけ）。

- 入稿前チェックで止めたときの終了コードは **3**。Python 側が未入稿一覧（発走・締切つき）を `system` へ送るので、
  シェルは 3 では通知しない（二重通知を避ける）。それ以外の非0はシェルが `system` へ送る
- `NETKEIRIN_PROXY_URL` が SOCKS なのに PySocks が無いと、**クライアントを作る時点で例外**にする
  （放っておくと「ログイン失敗」に化けて全件 SUBMIT_FAILED になる）
- webhook（承認・公開・取消）と公開待ちの同期も同じ `NetkeirinClient` を使うので、`.env` を入れれば同じ出口を通る
- テストは `tests/conftest.py` が**入稿前チェックを既定で「正常」に差し替える**（差し替えないと CI から本物の
  netkeirin へ GET する）。分類を確かめるテストだけ `@pytest.mark.real_preflight` で外す
- 死活確認は `scripts/check_netkeirin_egress.py`。正常（ok / セッション切れ）以外を状態が変わったときと
  異常が続く間3時間ごとに `system` へ送る。一時的なエラーは3回（30分）続いたら送る

### 7.2 本番で行う作業（それぞれ承認を得てから）

```bash
# VPS: 依存を入れる（入れるだけなら挙動は変わらない）
ssh sekito "cd ~/GitHub/kiseki/keirin && .venv/bin/pip install PySocks==1.7.1"

# Mac mini: トンネルを常駐させる
#   ⚠️ launchd には ssh-agent が無い。sekito の鍵にパスフレーズがあると BatchMode で即失敗する
cp keirin/scripts/macmini/com.kiseki.netkeirin-egress.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kiseki.netkeirin-egress.plist
tail /tmp/netkeirin-egress.log

# VPS: トンネル経由で届くか（入稿はしない）。出口が自宅回線かも見る
ssh sekito "curl -s --socks5-hostname 127.0.0.1:18080 https://api.ipify.org"
ssh sekito "cd ~/GitHub/kiseki/keirin && NETKEIRIN_PROXY_URL=socks5h://127.0.0.1:18080 .venv/bin/python scripts/check_netkeirin_egress.py --no-notify"

# VPS: 切り替え（keirin/.env に1行足す。外せば直結に戻る）
#   NETKEIRIN_PROXY_URL=socks5h://127.0.0.1:18080
# VPS: 死活確認を cron へ（10分ごと）
#   */10 * * * * cd $KEIRIN_HOME && .venv/bin/python3 scripts/check_netkeirin_egress.py >> $KEIRIN_HOME/data/logs/cron.log 2>&1
# VPS: Cookie の権限（次のログインからは自動で 600 になる）
ssh sekito "chmod 600 ~/GitHub/kiseki/keirin/data/netkeirin_session.json"
```

---

## 8. リスクと未確定事項

1. **プログラムからの通信を Access が通すか**（最大の不確定要素）。WARP の端末認証で通るなら案 D はそのまま動く。ブラウザの Cookie が要る設定なら §5 の代替へ。
2. **WARP の全通信トンネルの副作用**（§4.1）。特に DB 直結・Windows VM（JV-Link / UmaConn）・netkeiba スクレイプ。導入直後に一つずつ確かめる。
3. **Mac mini が単一障害点になる**。停電・回線断・macOS 更新の再起動で入稿が止まる。死活確認の通知と手動入稿で受ける。
4. **VPS の 127.0.0.1:18080 は VPS 上のどのプロセスからも使える**（自宅回線から外へ出られる）。VPS 内の利用者は自分だけなので許容範囲と考えるが、気になるなら sshd の `PermitListen` で絞る。
5. **規約・運用の可否**（運営の回答待ち）: 自動入稿の継続、常時接続、サービストークン、分析支援ツールが対象か、再認証の間隔。
6. 再認証の間隔が短い（毎日など）と、毎朝の手作業になる。

---

## 9. 11/17 までの作業計画

| 日 | 作業 | 担当 |
|---|---|---|
| 11/10（火） | この文書の確認・方針決定。運営へ §8-5 の質問を送る | **鈴木さん** |
| 11/11（水） | §7 のコード変更を PR に（keirin 柱。売上取得は別 PR）。テスト | Claude |
| ~~11/11（水）~~ | ~~Mac mini の `pmset -g custom`・自動ログイン・FileVault を確認~~ → **11/10 に確認済み・問題なし** | 鈴木さん |
| 11/12（木） | マージ・デプロイ。VPS の keirin venv に PySocks を入れる。**この時点ではプロキシ未設定＝挙動は今と同じ** | Claude（本番作業は都度承認） |
| 11/12（木） | Mac mini で逆トンネルを常駐。VPS から**トンネル経由で入稿ツールへ GET** し、出口が自宅回線になっていることと応答を確認（入稿はしない） | Claude（承認後） |
| 11/13（金） | Mac mini に Cloudflare One Client を入れ `nd-group` に登録（**メールのコード入力**）。§4.1 の影響を一つずつ確認。問題があれば一旦切断 | **鈴木さん**＋Claude |
| 11/13（金） | 接続状態で、ブラウザとスクリプト（トンネル経由）の両方から入稿ツールのログインまで確認 | **鈴木さん**＋Claude |
| 11/14（土） | VPS の `keirin/.env` に `NETKEIRIN_PROXY_URL` を設定し、**昼の波から**トンネル経由で本番入稿。件数・公開数を `netkeirin_submissions` で確認 | Claude（承認後） |
| 11/15〜16 | トンネル経由で全波運用。死活確認ジョブを有効化 | Claude |
| 11/16（月）夜 | 切替前の記録: VPS から直接 `! ssh sekito "curl -sI https://tool.syakenv2.netkeiba.com/bettool/top/index.html"` を実行し応答を残す | **鈴木さん** |
| 11/17（火） | 07:09 の朝の波を監視。切替後の「認証切れ」の見え方を記録 | Claude＋**鈴木さん** |

**切り戻し**: 11/17 より前は `NETKEIRIN_PROXY_URL` を消せば VPS 直結に戻る。11/17 以降は通知に出る未入稿一覧から手動入稿。
**同時に有効にしない**: 案 D では送り手は常に VPS 1本なので、この問題自体が起きない。案 A へ切り替えるときは、VPS を入稿案だけ作るモードにしてから Mac mini の送信を有効にする。

### 運営への確認事項（§8-5 の再掲）

- 自動入稿（プログラムからの入稿）を切替後も続けてよいか
- 端末を常時接続にしてよいか。Split Tunnel を netkeirin のドメインだけにできるか
- サーバー用のアクセス手段（サービストークン・IP 許可）を用意してもらえるか
- 分析支援ツール（`umaiaggre.yosoka.netkeiba.com`）も対象か
- 再認証（メールコード）を求められる間隔
