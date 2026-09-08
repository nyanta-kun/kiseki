# Phase 4（ブランドと認証）— 決定と、そこから決まる作業

## 🔴 決定（2026-09-08・ユーザー）

| | |
|---|---|
| **認証** | **A. Google に一本化**（kiseki の Auth.js v5 + Google OAuth） |
| **ブランド** | **併存**。`sekito-stable.com` と `galloplab.com` の両方を維持し、POG は sekito のドメインのまま中身を kiseki の Next.js に差し替える |

この組み合わせは「**2 ドメイン・1 認証**」になる。何が要るかは §7 に書いた。

以下は判断材料として集めた実測（すべて 2026-09-08）。

関連: `docs/pog_migration_plan_2026_09_08.md`

---

## 1. 🔴 両者の利用者はほぼ重なっていない

| | 人数 | 中身 |
|---|---:|---|
| `sekito.users` | 10 | POG の参加者（赤兎 / 友 / 永 / フクシゲ / 渡 / のださか / はせがわ / 松 / 黒 / 井） |
| `keiba.users` | 11 | GallopLab の利用者（運営 + メンバー） |
| **両方に居る** | **1** | `yuichiszk@gmail.com`（sekito 「友」= keiba 「鈴木友一郎」） |

**「email で機械的に対応付ける」という話ではない。POG の 9 人を kiseki 側へ迎え入れる話**になる。

⚠️ 9 人のうち 2 人はメールアドレスが Google アカウントとして実在するか怪しい:

    kuroki@sekito-stable.com     独自ドメイン
    ida@gmail-stable.com         `gmail.com` の打ち間違いに見える

この 2 人は sekito 側でも Firebase の uid ではなく手書きの id（`sekito-kuroki` /
`sekito-ida`）で作られている。**Google ログインへ寄せるなら、この 2 人の扱いを
先に決める必要がある。**

`keiba.users` は `google_sub` が NULL の行を許す（＝事前登録して、初回ログインで
紐付ける）設計になっているので、メールさえ確かなら先に作っておける。

---

## 2. 🔴 sekito の「認証」は認証ではない

`middleware/auth.js` は **クライアントが送ってきた `uid` を信じるだけ**。
コード自身がそう書いている:

    TODO: Firebase Admin SDK によるIDトークン検証に移行すべき。
    現在はuidパラメータの存在確認のみ（クライアント送信値を信頼）。

`uid` は query か body から取る。他人の uid を知っていればその人として振る舞える。

### POG の 44 ルートでの適用状況

    requireAuth   0 件
    requireAdmin  3 件（/user/visible・/user/order・/user/skip）

**指名（`POST /user`）・サイコロ（`POST /roll`）・確定（`POST /confirm`）・
指名取消（`DELETE /user`）にも認証は無い。**

### 読み取りが公開なのは実証済み

匿名の curl で 2026年の順位表がそのまま返る:

    $ curl -s "https://sekito-stable.com/api/pog/owners?group_id=27"
    [{"rank":1,"uid":8,"nickname":"松","win":4,...,"prize":6488,...}]

---

## 3. kiseki 側は「画面」だけを守っている

`frontend/src/proxy.ts` は `/` と `/login` 以外の**画面**を Google ログイン必須にする。
`/admin` と `/keirin` は admin ロールのみ。

    $ curl -L https://galloplab.com/races
    → https://galloplab.com/login?callbackUrl=%2Fraces

**ただし API は無認証**（proxy.ts のコメント自身が `/api/keirin/*` について認めている）:

    $ curl "https://api.galloplab.com/api/races?date=20260908"   → 200

つまり **API 層で見れば両者の差は小さい**。差があるのは画面の入口だけ。

---

## 4. キーの付け替え

    sekito.pog_user.uid (integer)  →  sekito.users.id
    sekito.users.uid  (varchar)    →  Firebase の UID（認証にしか使わない）

POG の内部キーは `users.id`。移行時は **`sekito.users.id` → `keiba.users.id` の
対応表**が要る（1,401 行の指名と 22 グループが参照している）。
`pog_group.user_ids` は **text 列にカンマ区切り**で入っているので、ここも書き換える。

---

## 5. 決めること

### (a) 認証

| 案 | 中身 | 起きること |
|---|---|---|
| **A. Google に一本化** | kiseki の Auth.js v5 + Google OAuth に寄せる | 9 人が Google ログインする必要。メールが怪しい 2 人の扱いを先に決める。POG の書き込みが**初めて本物の認証で守られる** |
| **B. 読み取りは公開のまま** | POG の閲覧は無認証、書き込み（指名・サイコロ）だけ認証 | 今の使い勝手を変えない。kiseki の proxy に公開パスを足す設計変更が要る |
| **C. 現状維持** | uid を信じる方式を kiseki へ持ち込む | 移植は最短。ただし**認証と呼べないものを新しい家に持ち込む**ことになる |

### (b) ブランド

`sekito-stable.com` を残すか、`galloplab.com` に寄せるか、併存させるか。
リバースプロキシでパス単位に付け替えられるので**全画面同時のカットオーバーは要らない**
（Phase 1 の決定どおり）。ドメインを残す場合でも中身は kiseki の Next.js になる。

---

## 7. 決定から決まる作業（5c）

### 🔴 kiseki はホワイトリスト方式

`POST /api/users/upsert`（Auth.js のログイン時に呼ばれる）は
**email が事前登録されていなければ 404 で拒否する**。合言葉を廃止した代わりの入口ゲート。

    事前登録あり・google_sub NULL   → 初回ログインで google_sub を確定して紐付け
    事前登録なし                    → 404「このメールアドレスは登録されていません」

つまり POG の 9 人は**先に `keiba.users` へ登録しておく**必要がある
（`POST /api/admin/users` / `google_sub` は NULL のままでよい）。

⚠️ **2 人はこのままでは登録できない。**

    kuroki@sekito-stable.com    独自ドメイン。Google アカウントとして実在するか要確認
    ida@gmail-stable.com        `gmail.com` の打ち間違いに見える

この 2 人は sekito 側でも Firebase uid ではなく手書き id で作られている。
**実在する Google アカウントのメールを聞くところから。**

### 🔴 2 ドメイン・1 認証で要ること

現状の実測:

    AUTH_URL      https://galloplab.com/api/auth   ← **単一値**
    trustHost     true（既に有効）
    nginx         galloplab.com/api/auth/ → :3002（Next.js）
                  sekito-stable.com/      → :8080（sekito の Vue）
                  sekito-stable.com/api/  → :5000（sekito の Node）
                  sekito-stable.com/kiseki → **404 を返す設定が残っている**

`AUTH_URL` が固定なので、**今のままでは sekito-stable.com でログインできない**
（コールバックが galloplab.com へ飛ぶ）。要るのは次の 4 つ:

| # | 作業 | 誰が |
|---|---|---|
| 1 | `AUTH_URL` の固定をやめ、`trustHost` にホスト解決を任せる（`injectBasePath()` は AUTH_URL を読むので併せて見直す） | コード |
| 2 | Google Cloud のリダイレクト URI に `https://sekito-stable.com/api/auth/callback/google` を追加 | 🔴 **ユーザーの操作** |
| 3 | nginx で sekito-stable.com の `/api/auth/` と移植済み画面パスを :3002 へ向ける（`/kiseki` の 404 も撤去） | 🔴 **本番設定・要承認** |
| 4 | `keiba.users` へ 9 人を事前登録し、`sekito.users.id → keiba.users.id` の対応表を作る | コード + 上記のメール確認 |

⚠️ **セッションはドメインごとに別**（Cookie がホストに紐づく）。
galloplab と sekito-stable の両方を使う人は**それぞれでログインが要る**。

### 履歴: 以前 kiseki は sekito-stable.com/kiseki に載っていた

`frontend/src/app/api/auth/[...nextauth]/route.ts` の `injectBasePath()` は
`AUTH_URL=https://sekito-stable.com/kiseki/api/auth` を例として書いており、
nginx にも `location /kiseki { return 404; }` が残っている。
**サブパス運用の実装は既にある**ので、1 の作業はゼロからではない。

### Socket.IO を忘れない

POG のドラフトはサイコロを `socket.io` で全員へ配信している
（`sekito-stable.com/socket.io/` → :5000）。5e（ドラフト）を移すときは
この経路も kiseki 側で用意する必要がある。

---

## 6. 決めなくても進むもの / 止まるもの

決定が出たので 5c に着手できる。**残る外部依存は 2 つだけ**:

| # | 待ち | 誰が |
|---|---|---|
| 1 | `kuroki@` / `ida@` の実在する Google アカウントのメール | ユーザー |
| 2 | Google Cloud のリダイレクト URI 追加 / nginx の変更承認 | ユーザー |

5a（馬マスタ・スクレイパ）と 5b-1（トリガの脱 sekito）は 2026-09-08 に完了済み。
