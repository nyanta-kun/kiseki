# kiseki-lab の本番 DB 読み取り専用アクセス

kiseki-lab は kiseki の指数を波乱帯別に観測する別プロジェクトです。観測と提案だけを行い、
kiseki のコードや DB は変えません。本番 `hrdb` を読むための専用ロールと SSH 利用者を
2026-10-31 に作りました。

## 構成

```
Mac (kiseki-lab)
  └─ ssh -N kisekilab@160.251.234.83  (-L 15432:127.0.0.1:5432)
       └─ VPS の 127.0.0.1:5432 → PostgreSQL 16 / hrdb / ロール kiseki_lab_ro
```

| 項目 | 値 |
|---|---|
| ロール | `kiseki_lab_ro`（LOGIN・パスワード認証 scram-sha-256） |
| DB | `hrdb` |
| 接続 | SSH トンネル経由のみ。Mac 側 `127.0.0.1:15432` → VPS 側 `127.0.0.1:5432` |
| SSH 利用者 | `kisekilab`（uid 1001） |
| SSH 鍵 | Mac の `~/.ssh/kiseki_lab_tunnel` |
| パスワード | 利用者が `\password` で設定し、Mac の `~/.pgpass` に保存。リポジトリには置かない |

外部にポートは開けていません。

## ロールの権限

| 対象 | 権限 |
|---|---|
| DB `hrdb` | CONNECT |
| スキーマ keiba / chihou / keirin | USAGE と全テーブル・ビュー・マテビューへの SELECT |
| 今後のテーブル | `ALTER DEFAULT PRIVILEGES FOR ROLE hrdb_user IN SCHEMA keiba, chihou, keirin GRANT SELECT ON TABLES` |
| sekito / public | なし |
| 属性 | NOSUPERUSER / NOCREATEDB / NOCREATEROLE / NOINHERIT / NOREPLICATION / NOBYPASSRLS |

ロールに付けた設定:

| 設定 | 値 | 目的 |
|---|---|---|
| `default_transaction_read_only` | `on` | 書き込み事故の保険 |
| `statement_timeout` | `120s` | 重い集計で確定処理を邪魔しない |
| `idle_in_transaction_session_timeout` | `60s` | 開きっぱなしのトランザクションを残さない |
| `CONNECTION LIMIT` | `3` | 接続の占有を防ぐ |

kiseki-lab 側は、確定処理の時間帯（JST 23:30〜23:45 ごろ）には集計を流さない取り決めです。

### 注意

- **書き込めない根拠は「書き込み権限を付けていないこと」です。** `default_transaction_read_only` は
  ロール自身が `SET` で外せるので、これだけでは防げません
- **自動付与は hrdb_user が作ったテーブルにしか効きません。** Alembic 経由なら hrdb_user が
  作るので問題ありません。postgres など別のロールで作ったテーブルは、手で GRANT が要ります
- 一時テーブルの作成権限（TEMP）は PostgreSQL の既定で全員に付いています。
  read_only を外せば一時テーブルは作れますが、本番データには影響しません

## 🔴 pg_hba.conf の行の位置を崩さない

`/etc/postgresql/16/main/pg_hba.conf` には、`local all postgres peer` の直後に次の4行があります。

```
host    hrdb  kiseki_lab_ro  127.0.0.1/32  scram-sha-256
host    hrdb  kiseki_lab_ro  ::1/128       scram-sha-256
host    all   kiseki_lab_ro  all           reject
local   all   kiseki_lab_ro                reject
```

pg_hba.conf は上から照合して**最初に当たった行**を使います。既存の行には、外部 IP・
Tailscale（`100.64.0.0/10`）・Docker のネットワークから**全ユーザー**を受け付けるもの
（`host all all ... md5`）があります。この4行がそれらより下に回ると、`kiseki_lab_ro` が
外から入れるようになります。

DB 名を `hrdb` に限っているのは、全員が CONNECT を持つ他の DB（`postgres` など）への接続も
同時に塞ぐためです。

編集したら、反映前に書式を検査してから設定を読み直します。**再起動はしません**
（オッズ取込や API の接続が切れるため）。

```sql
SELECT line_number, error FROM pg_hba_file_rules WHERE error IS NOT NULL;  -- 0 行なら OK
SELECT pg_reload_conf();
```

作成前のバックアップ: `/etc/postgresql/16/main/pg_hba.conf.bak-20261031082444-before-kiseki-lab`

## SSH 利用者 kisekilab

- ログインシェルなし（`/usr/sbin/nologin`）・パスワードはロック済みで、鍵でしか入れない
- sudo・docker グループには入れていない（docker グループに入ると root と同等になるため）
- バックアップ用の `ysuzuki`（sudo で postgres になれる）とは分けている。同じ利用者を使うと
  読み取り専用ロールの意味がなくなるため

`/home/kisekilab/.ssh/authorized_keys`:

```
restrict,port-forwarding,permitopen="127.0.0.1:5432",permitopen="localhost:5432",command="/bin/false" ssh-ed25519 ... kiseki-lab
```

- `permitopen` は、クライアントが指定した転送先の文字列と**文字どおり**照合されます。
  そのため `127.0.0.1` と `localhost` の両方を書いてあります
- `command="/bin/false"` なので、シェルを要求すると即座に切れます。**トンネルは必ず `ssh -N` で張ります**
- 5432 番以外への転送は `administratively prohibited` で拒否されます

## kiseki-lab 側の接続設定

`~/.ssh/config`:

```
Host kiseki-lab-tunnel
    HostName 160.251.234.83
    User kisekilab
    IdentityFile ~/.ssh/kiseki_lab_tunnel
    IdentitiesOnly yes
    LocalForward 15432 127.0.0.1:5432
    SessionType none
    ExitOnForwardFailure yes
```

既存の `sekito` の設定は利用者が `ysuzuki` なので流用できません。

`~/.pgpass`（権限 600）:

```
127.0.0.1:15432:hrdb:kiseki_lab_ro:<パスワード>
```

Mac 側を 15432 番にしているのは、この Mac にも PostgreSQL 16 が入っていて 5432 番が
使われている可能性があるためです。

## 確認方法

トンネルを張った状態で:

```bash
psql "host=127.0.0.1 port=15432 dbname=hrdb user=kiseki_lab_ro" \
  -c "show default_transaction_read_only" -c "show statement_timeout" \
  -c "select count(*) from keiba.races" \
  -c "insert into keiba.races default values" \
  -c "select count(*) from sekito.races"
```

期待値: `on` / `2min` / 件数 / `cannot execute INSERT in a read-only transaction` /
`permission denied for schema sekito`。

権限だけなら、VPS で postgres から `SET ROLE kiseki_lab_ro` に切り替えればパスワードなしで
確かめられます。ただし `ALTER ROLE ... SET` の3つの設定はログイン時にしか効かないので、
`SET ROLE` では確かめられません。

## 戻し方

```sql
-- 1. 既定権限を外す（残したまま DROP ROLE すると依存エラーで失敗する）
ALTER DEFAULT PRIVILEGES FOR ROLE hrdb_user IN SCHEMA keiba, chihou, keirin
  REVOKE SELECT ON TABLES FROM kiseki_lab_ro;
-- 2. 付与した権限をまとめて外す
DROP OWNED BY kiseki_lab_ro;
-- 3. ロールを消す
DROP ROLE kiseki_lab_ro;
```

```bash
# pg_hba.conf の4行（とコメント1行）を消して読み直す
sudo -u postgres psql -d hrdb -c "select pg_reload_conf()"
# SSH 利用者を消す
sudo deluser --remove-home kisekilab
```

一時的に止めるだけなら `ALTER ROLE kiseki_lab_ro NOLOGIN;` で足ります。
