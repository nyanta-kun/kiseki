# Phase 4（ブランドと認証）の判断材料 — 2026-09-08

統合の残り（POG 移植の 5c/5d/5e と、Phase 3 の UI 移植）は**すべてここで止まっている**。
決めるのはユーザーだが、決めるのに要る事実をここに集めた。実測はすべて 2026-09-08。

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

## 6. 決めなくても進むもの / 止まるもの

| | |
|---|---|
| 決めなくても進む | 5b-3（`sekito.entries` / `sekito.races` の移設調査） |
| **止まる** | 5c（認証の接続）・5d（読み取り API + 画面）・5e（ドラフト）・Phase 3（UI 移植） |

5a（馬マスタ・スクレイパ）と 5b-1（トリガの脱 sekito）は 2026-09-08 に完了済み。
**認証を待たずにできる整備は、ほぼ尽きている。**
