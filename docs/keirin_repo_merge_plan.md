# keirin リポジトリを kiseki へ統合する計画（2026-08-10 策定）

## 目的

`nyanta-kun/keirin` を `nyanta-kun/kiseki` へ取り込み、**デプロイと CI/CD を一本化**する。
現状は同じ VPS・同じ DB（`hrdb` の `keirin` スキーマ）を使いながら、
リポジトリ・CI・デプロイ経路が二重になっている。

## 🔴 実測で分かった前提（見積もりが変わった点）

**kiseki の cron もホスト上の素スクリプトで動いている。**
Docker 化されているのは Web（`galloplab-backend-1` / `galloplab-frontend-1`）だけで、
日次バッチ12本は `/home/ysuzuki/GitHub/kiseki/scripts/*.sh` を直接叩いている。

→ **keirin を Docker へ載せ替える必要はない。** 統合は
「ディレクトリを移して cron のパスを変える」規模になる。

| 項目 | 実測 |
|---|---|
| keirin の cron | 14本（総26本のうち） |
| kiseki の cron | 12本（すべてホスト素実行） |
| keirin `.git` | 291MB / 455コミット |
| keirin 現HEADの追跡ファイル | **31MB** |
| keirin `data/models` | 212MB（git 追跡は3ファイルのみ・本体は GitHub Release 経由） |
| keirin `data/picks` | 159MB（実行時状態・大半は未追跡） |
| keirin `.venv` | 549MB（LightGBM 等） |
| VPS 空きメモリ | 1,120MB / 1,966MB |

## 方式: 新規移植（subtree ではなく）

**履歴291MBのうち現在必要なのは31MB。** 残りは `data/picks/*.pdf`（1本4MB）や
旧モデル `.pkl` の堆積で、subtree で取り込むと kiseki が約600MBになり
clone が倍以上重くなる。

- **履歴は失われない。** keirin リポジトリをアーカイブとして残せば `git log` は読める。
  移植コミットに **最終SHAとアーカイブURL** を記録する
- 失うのは kiseki 上での `git blame` 連続性のみ
- 中間案: `git filter-repo` で `data/` を履歴から落としてから subtree すれば
  30〜50MB でコード履歴を保てる。工程が1つ増えるため今回は採らない

## 配置

```
kiseki/
  backend/            既存（Web API・Docker）
  frontend/           既存（Web・Docker）
  scripts/            既存（JRA/地方の日次バッチ・ホスト実行）
  keirin/             ← 新規。keirin リポジトリの中身をそのまま
    src/  scripts/  tests/  config/  requirements.txt
    data/             実行時状態（.gitignore・VPS上のものを引き継ぐ）
    .venv/            既存を再利用（作り直さない）
```

⚠️ **`keirin/data` と `keirin/.venv` は移動せずVPS上の実体を使う。**
作り直すと 212MB のモデル再取得と 549MB の venv 再構築が発生し、
切替時間とディスクを浪費する。**シンボリックリンクか mv で引き継ぐ。**

## CI/CD

- kiseki の `CI` ワークフローに **keirin のテスト（pytest 949件）** を追加
- keirin の `Deploy to VPS`（models を Release から取得して scp）を kiseki 側へ移設
- keirin リポジトリの CI は停止（アーカイブ化）

## 🔴 Mac も移行対象（VPSだけではない）

**学習は Mac で行い、モデルを VPS へ配布している。** VPS は 1.9GB しかなく
LightGBM の再学習を回せないため。Mac の crontab に keirin のジョブが2本ある。

| cron | 内容 |
|---|---|
| `30 23 * * 0`（**日曜 23:30**） | `weekly_retrain_wt.sh` → `sync_models_to_vps.sh` |
| `5 0 1 * *`（毎月1日 00:05） | `ensure_monthly_vintage.sh`（月次凍結vintage） |

Mac 側の実体: `data/models` **402MB** / `.venv` **395MB**
（VPS の 212MB / 549MB とは別物。学習用なので中身が違う）

⚠️ **Mac を移行し忘れると、週次再学習が旧パスで走り続ける。**
移植後の `~/GitHub/kiseki/keirin` にコードが入る一方、学習は
`~/GitHub/keirin` の**古いコード**で回り、そこで作られたモデルが VPS へ
配布される——「コードは新しいのにモデルは古いコード由来」という
最も気づきにくい形の不整合になる。

⚠️ **次の週次再学習は 2026-08-16(日) 23:30。** それまでに Mac 側を切り替えること。
   今夜（08-10 月曜）の切替とは衝突しない。

### Mac 側の手順（VPS と同じ形）

```bash
cd ~/GitHub/kiseki && git pull origin main
mv ~/GitHub/keirin/data  ~/GitHub/kiseki/keirin/data
mv ~/GitHub/keirin/.venv ~/GitHub/kiseki/keirin/.venv
mv ~/GitHub/keirin ~/GitHub/keirin.bak     # ロールバック用に残す

# crontab の2行を書き換え（パスを kiseki/keirin へ）
crontab -l > ~/crontab_mac_before_keirin_merge_$(date +%Y%m%d).txt
```

⚠️ Mac の crontab は `KEIRIN_HOME` を使っておらず**絶対パス直書き**。
   VPS のように1行では切り替わらないので、2行とも直すこと。

## 🔴 切替の窓と手順（夜間）

**窓: 23:45 〜 翌 06:00。** この間に動く keirin の cron は 00:40 のバックフィルのみ。
入稿・通知・採点はすべて停止している。

### 事前（日中・本番に触らない）
1. kiseki に `keirin/` を追加する PR を作る（**cron は旧パスのまま**＝無影響）
2. kiseki CI に keirin のテストを追加し、緑を確認
3. 本ドキュメントとロールバック手順を確定

### 切替（夜間）
```bash
# 0. 退避
crontab -l > ~/crontab_before_keirin_merge_$(date +%Y%m%d).txt
cp -a ~/keirin ~/keirin.bak            # 旧構成を丸ごと残す（ロールバック用）

# 1. kiseki を最新へ
cd ~/GitHub/kiseki && git pull origin main

# 2. 実行時状態とvenvを引き継ぐ（コピーせず移動）
mv ~/keirin/data   ~/GitHub/kiseki/keirin/data
mv ~/keirin/.venv  ~/GitHub/kiseki/keirin/.venv

# 3. 疎通確認（DBは読むが書かない）
cd ~/GitHub/kiseki/keirin && PYTHONPATH=. .venv/bin/python3 \
  scripts/submit_marquee_wt.py --dry-run

# 4. cron を差し替え（KEIRIN_HOME を変えるだけ）
#    KEIRIN_HOME=/home/ysuzuki/GitHub/kiseki/keirin
```

### ロールバック
`crontab` を退避ファイルから書き戻し、`~/keirin.bak` を `~/keirin` へ戻す。
**旧構成を消さずに残すのが要点**（`KEIRIN_HOME` の切替だけで往復できる）。

## ⚠️ 事故りやすい点

- **`KEIRIN_HOME` は crontab の環境変数**。1行変えれば14本すべてが移る＝
  戻すのも1行。だが**変え忘れると混在**（旧パスと新パスの両方が走る）になり、
  同じレースへ二重入稿しうる。切替後は `crontab -l | grep KEIRIN_HOME` で必ず確認
- **`netkeirin_session.json`** など認証状態が `data/` にある。移動を忘れると
  入稿が全滅する（ログには「失敗（継続）」としか出ない）
- **ロックファイル**（`data/*.lock`）も `data/` 配下。移動時に古いロックが残ると
  多重起動防止が誤作動する。切替前に `rm -f data/*.lock`
- 入稿・採点経路は**壊れても例外が出ない**箇所が多い
  （2026-08-09 に採点が着順を見ていなかった件・`--marquee` が三連複を出す
   ところだった件・9H1 の記録が落ちていた件）。切替後は翌朝の
   `[marquee]` ログと `netkeirin_submissions` の件数を必ず突き合わせる

## 統合後に消える二重管理

- 看板レース判定（`backend/src/services/keirin_marquee.py` と
  keirin `src/marquee.py`）→ **前者を唯一の正本にする**
- CI・デプロイ経路
- リポジトリ運用（worktree・ブランチ保護・PR フロー）
