# 手動入稿を型ラボ経路へ移す（2026-09-03）

> ユーザー指示:「手動入稿が旧ランクでの処理になりそうです」
> →「手動入稿は今後も考え、型ラボの方に合わせて入稿できるようにして」

## 0. 何が問題だったか

型ラボへ**全面移行したのは 2026-08-28**（`GO_LIVE_2026_08_28.md`）だが、
**手動入稿だけが旧ランク経路のまま**残っていた。

    フロント /keirin の一覧 → 推奨外レース → ダイアログで**ランク（7S / 9C）と軸2車**を選ぶ
      → POST /api/keirin/submit-race {rank_key, axis1, axis2}
      → keirin webhook /submit-race
      → netkeirin_submit_wt.py --race-key … --manual-rank-key 7S --axis1 --axis2

これは次の3つを同時に壊していた:

1. **買い目が旧ランクのロジックで作られる**（型ラボの型判定・プラン選択を通らない）
2. **`type_lab_picks` に行が残らない** → 採点（`settle_type_lab_picks`）・
   `/keirin/type-lab`・夜間レビュー・成績集計から**まるごと漏れる**
3. 🔴 **`bet_detail` の書式は共通なので入稿自体は成功する。** 例外もログも出ないので
   気づけない（このリポジトリが繰り返している「無言の取りこぼし」の型）

さらに **ユーザーが軸2車を選ぶ**という前提自体が型ラボと噛み合っていない。
型ラボは**型（A〜F）から商品・軸・買い目が自動で決まる**ので、選ばせるものが無い。

## 1. 変更

    フロント → 「このレースを型ラボで入稿」ボタンだけ（ランク・軸の選択を撤去）
      → POST /api/keirin/submit-race {race_key, date, session}
      → keirin webhook /submit-race
      → netkeirin_submit_type_lab.py {date} {session} --race-key {race_key}

`--race-key`（「このレースだけ（手動入稿用）」）は元からあったので、
**繋ぎ替えるだけで型ラボの全経路に乗る**。

| 箇所 | 変更 |
|---|---|
| `keirin/scripts/keirin_webhook.py` | 型ラボのスクリプトを叩く。`session` に `noon` を追加。`rank_key`/`axis1`/`axis2` が来たら **400** |
| `keirin/scripts/netkeirin_submit_type_lab.py` | **`--race-key` 指定時は session を問わず組み直す** |
| `backend/src/api/keirin_router.py` | `_MANUAL_RANK_KEYS` 廃止。`session` に `noon`。ランク・軸は 400 |
| `frontend/src/app/keirin/page.tsx` | `MANUAL_SUBMIT_RANKS` とランク選択 UI を撤去 |
| `frontend/src/lib/api.ts` | `ManualKeirinRankKey` を撤去 |
| `frontend/src/app/keirin/actions.ts` | 引数から `manual` を落とす |

### 🔴 手動入稿は session を問わず組み直す

手動は「**いま出す**」操作なので、朝に組んだ買い目をそのまま出すと
**並びが出た後・オッズが動いた後の盤面と食い違う**。
自動の朝バッチ（`only_key` なし）の挙動は変えていない。
⚠️ dry-run では組み直さない（`type_lab_picks` への書き込みなので
「何も変えずに中身を見る」という約束を破る。2026-08-28 に実際に踏んだ）。

### 🔴 ランク・軸が来たら黙って無視せず 400 を返す

無視すると「**軸を選んだのに効いていない**」という読めない状態になる。
フロントは同日に送信をやめているので、来るのは古いクライアントだけ。

## 2. 副次的に直ったこと

- **`session` に `noon` を通せるようになった。** 旧経路は `morning` / `evening` しか
  受けず、昼の手動入稿ができなかった（型ラボは3波）
- **ランク集合のコピーが3箇所（フロント / backend / webhook）から消えた。**
  これは 2026-08-08・2026-08-16 と**2回同じ取り残しを起こしていた**場所
  （webhook だけ 9C に追随せず「選ぶと必ず400」が2日続いた）。
  コピーが無くなったので同型の事故は起きない

## 3. 検査

| テスト | 何を固定するか |
|---|---|
| `keirin/tests/test_approve_cli_and_webhook.py::test_webhook_submit_race_uses_type_lab` | webhook が型ラボのスクリプトを叩き、旧スクリプト・`--manual-rank-key` が無いこと |
| `backend/tests/test_keirin_rank_consistency.py::test_manual_submit_has_no_rank_or_axis_anywhere` | `_MANUAL_RANK_KEYS` / `MANUAL_SUBMIT_RANKS` / `ManualKeirinRankKey` が**復活していない**こと |
| 同 `::test_manual_submit_rejects_rank_and_axis` | ランク・軸が来たら 400・`noon` を通すこと |

🔴 **どちらのテストも「コメント／docstring を外してから」見る。**
「なぜ移したか」の説明に旧スクリプト名やランク集合の名前が出てくるので、
素の文字列検索だと**自分の注記で落ちる**（2026-09-03 に実際に踏んだ）。
webhook 側は `ast.unparse` を使うが、**引用符が正規化される**（`"noon"` → `'noon'`）
ので引用符を含めて検査しないこと。

## 4. 残っていること

⚠️ **`netkeirin_submit_wt.py` の `--manual-rank-key` 経路自体は残してある。**
看板穴埋め（`marquee_fill`）が同じスクリプトを使っているため。
手動入稿からは呼ばれなくなったので、旧ランクを完全に畳むときに一緒に消す。

⚠️ **型ラボが商品を組めないレースでは入稿されない**（入稿ゲート＝想定平均払戻2万円・
1点2.0倍）。旧経路は軸さえ指定すれば必ず出せたので、**ここは挙動が変わる**。
ゲートに掛かった理由は `submission_skips` に残り確認画面から読める。
