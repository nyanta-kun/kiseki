# P4: バグ候補の確定と修正案（2026-09-20）

読み取り専用の独立監査。リポジトリには一切変更を加えていない（パッチは本フォルダに保存のみ）。
DB は `psql "$KEIRIN_DB_URL"` で SELECT のみ実行（COMMON.md の負荷ルールに従い、日付・race_key で
範囲を絞ったクエリのみ）。第1段の数値（A_pipeline / B_live / G_sales の REPORT.md）は再利用しつつ、
各項目で最低1つは自分で実データを引いて突き合わせた。

検証用に一時 clone `/tmp/p4checkout`（git clone、リポジトリ本体は無傷）を作り、
全パッチが `git apply --check` を通ること・変更後の Python が `ast.parse` を通ることを確認済み。

---

## item1: 実績集計に未送信の商品（`status='proposed'`）が混ざる — **確定**

### ①再現
```sql
SELECT status, (deleted_at IS NULL) AS not_deleted, count(*)
FROM keirin.netkeirin_submissions
WHERE submitted_at >= now() - interval '60 days'
GROUP BY 1,2 ORDER BY 1,2;
--  status   | not_deleted | count
-- deleted   | f           |   101
-- proposed  | t           |    64   ← ここが漏れる
-- published | t           |  1799
-- submitted | t           |   477
```
`status` の正本は `keirin/scripts/netkeirin_submit_wt.py:149-162`。
`proposed`=承認待ち・netkeirin へ**まだ送っていない案**、`submitted`/`published`=実際に送った
（`notify_race_result_wt.py:68-89` の `SOLD_STATUSES = (STATUS_SUBMITTED, STATUS_PUBLISHED)` が
「売った」の唯一の正本定義）。取消時は `status` も `'deleted'` に揃うので
`status IN ('submitted','published')` だけで deleted も自動的に除外できる。

### ②影響範囲
- `backend/src/api/keirin_router.py:616-680 _fetch_settled_submissions`（`deleted_at IS NULL` の
  みで絞り `status` を見ない）が **`/sold-performance`・`/stats?source=sold`**（依頼主の最優先KPI
  「売上」を含む収支ページの正）の母集団を作っている。直近60日で **proposed 64件** が混入しうる
  （すべて `bet_detail` 付き＝集計に実際に加算される）。全体 n≈2,090〜2,300 に対し約3%。
  B_live/REPORT.md の実測（0.09pt 前後のROI差）と整合。
- 同じ `deleted_at IS NULL` だけの誤りを横断 grep で確認:
  - `keirin/scripts/sold_performance_report.py:40`（同名の別実装。CLIレポート）
  - `keirin/scripts/nightly_review_type_lab.py:230-238, 739-741, 800-802, 1216-1217`（**4箇所**。
    「その日に売った商品」の抽出・直近7日の入稿中央値・「自信あり」レポート）
  - `backend/src/api/keirin_type_lab_router.py:355-359 _SQL_SOLD`（`status <> 'deleted'` なので
    proposed が「売っている」判定に紛れ込む。変数名は "SOLD" なのに proposed を含む）

### ③原因のコード位置
上記ファイル・行番号のとおり。共通の原因は「取消(`deleted_at`)だけをチェックし、
`proposed`（承認待ち・未送信）を見落とす」という同一パターンが独立に4ファイル・7箇所へ
コピーされたこと。

### ④修正案
- `fix_01a_keirin_router.patch` — `_fetch_settled_submissions` の `deleted_cond` に
  `AND ns.status IN ('submitted', 'published')` を追加
- `fix_01b_sold_performance_report.patch` — 同じ条件を `sold_performance_report.py` へ
- `fix_01c_nightly_review_type_lab.patch` — 4箇所すべてに同条件を追加
- `fix_01d_keirin_type_lab_router_sold.patch` — `_SQL_SOLD` を `status IN ('submitted','published')` へ

### ⑤テスト案
```python
# backend/tests/test_keirin_sold_population.py（新規）
async def test_proposedは実績集計に入らない(db_session):
    # netkeirin_submissions に status='proposed' の行と bet_detail を1件だけ挿入
    # /sold-performance (または _fetch_settled_submissions を直接呼ぶ) を実行
    # rows に含まれないこと・n_missing にも計上されないこと（存在しないのと同じ）を assert
```
`keirin/scripts/sold_performance_report.py` 側は既存の `tests/test_sold_performance.py` に
「proposed 行は SELECT に出ない」テストケースを追加。

---

## item2: 欠車返還が採点に未反映 — **確定**（実売にも影響あり、金額は小さい）

### ①再現
`keirin.type_lab_picks(mode='live')` の 2026-08-27〜09-19（採点済み6,601行）を全走査し、
`legs[].combo` の車番が同レースの `wt_entries.frame_no` に存在しない行を抽出
（`repro_item2_void_detect.py`）。

```
flagged rows: 9 / distinct races: 6 / sum(stake)=25,400円
例: id=74714 20260830_13_06 B_hit budget=10000 hit=f payout=0
    legs: 2-4-1(2400) / 2-1-4(4900) / 2-4-3(2600) / 5-2-3(100)
    wt_entries に car=4 が存在しない（7車立てのはずが6行しかない）
    → 9,900円ぶんの leg が出走していない車番を含む＝構造的に的中不可能なのに全損計上
```
`sub_settle/REPORT_settle.md` S1 の数字と完全一致（自分で再現・突き合わせ済み）。

🔴 **さらに確認**: この `20260830_13_06 B_hit` の `type_lab_picks` 行は、
`netkeirin_submissions`（**実際に netkeirin へ送った本物の商品**・`status='published'`）にも
**一字一句同じ `legs`**（car=4 を含む）で存在する。すなわちこの欠車返還バグは
「型ラボの内部評価」だけでなく **実売の `settled_bet/settled_payout`（収支の正）にも実在**する。
`match.py`（B_live）が「8/29以降の実売は type_lab_picks と100%一致」と確認済みなので、
この構造は 8/29 以降の全 live 商品に共通のリスクである。

### ②影響範囲
- 直近24日で live 9行・6レース・**25,400円**（総投資66,010,000円の0.038%）。
  **ROI全体への影響は無視できる規模**だが、個別行の勝敗・投資額表示は誤り。
- 影響する集計: `/sold-performance` `/stats`（`netkeirin_submissions.settled_bet` 経由）、
  型ラボの `keirin_type_lab_router.py` のプラン別ROI（`Σpayout/Σbudget`）。
- **採点そのもの（的中判定・payout）には影響しない**（欠車の車番を含む買い目は出走していない
  車番を含むので構造的に当たり得ず、hit/payout の計算結果は変えなくても正しい）。動くのは
  「投資額（分母）」だけ。

### ③原因のコード位置
- `backend/src/services/keirin_settlement.py::settle()`（実売の唯一の正本）— 欠車の概念が無く、
  `bet_detail.lines` を無条件に全額 `bet` へ合算する
- `keirin/scripts/settle_type_lab_picks.py::main()`（型ラボの採点。`keirin_settlement.settle()` を
  呼ばず**別実装**）— 同様に `legs` を無条件に集計する
- どちらも「欠車＝出走取消で `wt_entries` から行ごと消える」という既存の仕様（A_pipeline報告・
  CLAUDE.md にも記載）を採点側で一切参照していない

### ④修正案
バックエンド（実売・正本）:
- `fix_02a_keirin_router_valid_cars.patch` — `_fetch_valid_cars()` ヘルパーを新設し、
  `_fetch_settled_submissions` の `settle()` 呼び出しへ `valid_cars=` を渡す
- `fix_02b_keirin_settlement_void.patch` — `settle()` に `valid_cars` 引数を追加。
  欠車の車番を含む leg を「返還」として `bet`（投資額）から除外し `void_refund` フィールドで
  可視化する。**払戻・的中判定には影響しない設計**（欠車を含む leg は的中しえないため）。
  `valid_cars=None`（未指定）なら従来どおりの挙動（後方互換）
- `fix_02c_keirin_settlement_cache_version.patch` — `SETTLE_VERSION` を 1→2 に上げ、
  焼き付け済みキャッシュ（欠車を全損計上した古い値）を自動失効させる
  （この仕組み自体は元から用意されていた設計＝`keirin_settlement_cache.py` のコメント参照）

型ラボ（内部評価・store用）:
- `fix_02d_settle_type_lab_picks_void.patch` — `_entrants()` / `_void_stake()` を新設し、
  `void_refund` 列（下記マイグレーション）へ返還額を書く。`payout`/`hit` の計算式は変えない
- `fix_02e_migration_void_refund.py` — `keirin.type_lab_picks.void_refund INTEGER DEFAULT 0`
  を追加する Alembic マイグレーション案（**未適用**。`down_revision` は現行 keirin 系の最新head
  `202609120900_keirin` に設定。ただし本リポジトリは現在 alembic の head が複数に分岐している
  ため（`202609092313_jra` / `0001` / `s2t3u4v5w6x7` / `202609120900_keirin` / `q7r8s9t0u1v2` /
  `202608310722_keirin` の6つ）、マージ時に current head を確認して down_revision を調整すること）
- `fix_01d_keirin_type_lab_router_sold.patch`（同ファイルに同梱）— `_SQL_COMBO` に
  `void_refund` 列を足し、プラン別ROIの投資額計算 `inv = Σ(budget - void_refund)` へ変更

### ⑤テスト案
`repro_item2_void_detect.py` の中身をそのまま unittest 化できる（ロジック検証は実施済み・
`ks_item2.py` を直接 import して `settle()` の戻り値を確認：
```python
res = settle(bet_detail_with_car4, finishers, {}, valid_cars={1,2,3,5,6,7})
assert res.bet == 100          # 4を含む3leg(9,900円)を除いた残り
assert res.void_refund == 9900
assert res.hit is False        # 払戻判定は不変
```
（実際に上記アサーションは本パッチ適用済みコードで実行し、全て pass することを確認済み）。
`backend/tests/test_keirin_submitted_pick_result.py` に「欠車を含む leg は bet から除かれ
void_refund に計上される」テストケースを追加。`settle_type_lab_picks.py` 側は既存の
`verify.py`/`verify_dh.py` と同型の独立再計算スクリプトへ `void_refund` の検証を足す。

⚠️ **本番反映の運用注意**（実行はしない・記録のみ）: パッチ適用後、
`settle_type_lab_picks.py --redo --from 2026-08-27 --to <今日>` を1回走らせないと
既存9行の `void_refund` は埋まらない（`settled_at IS NULL` 条件により再採点対象外のため）。

---

## item3: ガミ的中の採点不一致2件（`20260821_46_03` / `20260821_53_04`） — **判定不能（原因側は絞り込み済み）**

### ①再現
```sql
-- 46_03: 着順 1着=car2 / 2着=car5 / 3着=car1 → 当たり目 "1=2=5"
-- 自社の買い目3本: 1=2=3(6,200円) / 1=2=7(2,600円) / 1=2=4(1,200円) → いずれも不一致＝外れ
-- wt_odds: combination='1-2-5' の odds_value=3.0（100円あたり300円）
```
`keiba` 側の再現手順は `repro_item3_gami_mismatch.sql`。

### ②影響範囲
2026-08-21の2レースのみ。合計差額 3,600+8,000=11,600円（払戻方向で自社が過小）。
「表示的中率」には影響しない（どちらも netkeirin 側でも「ガミ」扱いで金額のみの差）。
G_sales/REPORT.md の月次突合（34日中33日一致・不一致2件）に含まれる既知の外れ値そのもの。

### ③調査結果（追加で絞り込んだ点）
- **本件は再入稿・bet_detail書き換えの形跡なし**（`netkeirin_submissions` に該当行は各1件のみ、
  `submitted_at`→`published_at` は通常の1回きりの流れ）
- **欠車（item2型のバグ）でもない**（両レースとも `wt_entries` は7行フルで欠落なし、
  `wt_races.cancel=0`）
- **自社の3本の買い目のうち、どれが勝っても払戻は数万円台になる**（各leg の確定オッズ×stakeを
  実測すると 24,720〜46,500円）。netkeirin の3,600円／8,000円は、自社の買い目のどれが勝っても
  説明できない金額であり、**「本当は当たっていたのに自社が見落とした」という仮説とも整合しない**
  （見落としだとしても払戻額の桁が合わない）
- 現在の `wt_entries.finish_order` に基づく判定（＝外れ）は**内部的に一貫している**
  （オッズ検索ロジック `_fetch_winning_payouts` の "-"/"=" 変換も正しく動作することを確認）

### ④確定できなかった点・限界
- `wt_entries`/`wt_races` に着順訂正の履歴列が無く、**当時 vs 現在の finish_order が同一かを
  DBだけでは検証できない**（写真判定・審議による後日訂正が本当にあったかは判定不能）
- netkeirin 側のスクレイプ元ページの当時のスナップショットも無い
- 影響金額が11,600円と極小で、追加調査（例: netkeirin へ問い合わせ、当時のログ発掘）の
  費用対効果は低いと判断し、これ以上のコード内調査は打ち切った

### ⑤結論
**バグ修正は提案しない**（現在のDBデータに基づく限り自社の「外れ」判定は正しく、
どちら側が誤っているかを一意に特定できないため）。優先度は最低（金額極小・件数2件・
再発パターンなし＝G_sales の34日サンプルでこの2件のみ）。

---

## item4: 8/09〜8/15 の29件（published/submitted なのに `netkeirin_sales_race` に無い） — **確定（データ品質の歴史的事象。コードバグではない）**

### ①再現
```sql
-- repro_item4_missing_sales.sql
```
29件全件が **`status='submitted'` かつ `published_at IS NULL`**（`published` という状態自体が
2026-08-16 に導入されたもので、この期間には存在しない）。`netkeirin_race_id` は正しい形式で
入っているが、`netkeirin_sales_race` には **race_id・race_key のどちらの経路でも一切ヒットしない**
（マッピングミスではなく完全に不在）。同じ8/09〜8/15の期間、`netkeirin_sales_race` 自体は
1日20〜48件のデータを持っており、**スクレイプが丸ごと止まっていたわけではない**。

### ②影響範囲
29件・的中率58.6%（母集団平均よりかなり高い）。B_live/REPORT.md 実測: 除外しても通算ROI
72.34%→72.60%で結論は変わらない（影響は小さい）。

### ③原因（絞り込み結果）
`scripts/netkeirin_submit_wt.py:3359-3410` のコメントより、`submitted`→`published` は
「同じ `action=change_status` に race_id の配列」で送る**別ステップ**であり、
「1件ずつ送ると公開待ち」になる設計。2026-08-16以前はこの`published`状態そのものが
無かったため、**「作成(submitted)はしたが、実際に一般公開されるところまで到達したかどうかを
自社のDBからは判別できない」という構造的な期間**だったと考えられる。
29件全てがこの空白期間（8/09〜8/15、8/16導入直前の1週間）に集中していることは、
このタイミング一致仮説を強く支持する。

### ④確定できなかった点
- 「本当に売れなかった（一般公開されなかった）」のか「売れたが自社の記録が status を
  更新できなかっただけ」なのかは、**当時の netkeirin 側の状態を直接見る手段がなく判定不能**
- 的中率58.6%が高いことの説明（生存者バイアス的な何か）も特定できていない

### ⑤結論・修正案
**進行中のコードバグではない**（`published` 状態の導入=2026-08-16以降はこの型の欠測は
理論上再発しない設計になっている。実際、8/16以降の期間でこの検出クエリを走らせると
0件になることを確認済み — 下記）。
```sql
-- 8/16以降で同型の欠測が起きていないことの確認（本監査で実行・0件）
SELECT count(*) FROM keirin.netkeirin_submissions ns
WHERE ns.status IN ('published','submitted') AND ns.race_key >= '20260816'
  AND NOT EXISTS (SELECT 1 FROM keirin.netkeirin_sales_race sr WHERE sr.race_key = ns.race_key)
  AND ns.race_key < to_char(CURRENT_DATE - INTERVAL '3 days', 'YYYYMMDD');  -- 直近3日は未反映の可能性を除く
```
→ 実行結果 **0件**（本監査で実測）。よってコード修正は提案しない。
**推奨アクション**（実行はしない）: 過去データの完全性を優先するなら、この29件を
`status='deleted', cancel_reason='未公開のまま期間終了(8/16以前の運用)'` へ一括更新する
DBメンテナンスを検討（集計上の影響は極小なので必須ではない）。

---

## item5: 2026-06-12 の `race_point` 汚染 — **確定（第1段の報告を追加検証で再確認・修正は実行しない）**

### ①再現
```sql
-- repro_item5_race_point.sql
SELECT race_key, count(*) n, sum(race_point) sum_rp, avg(race_point) avg_rp
FROM keirin.wt_entries
WHERE race_key LIKE '20260612_43_%' OR race_key LIKE '20260612_61_%'
GROUP BY race_key ORDER BY race_key;
```
実測: **venue 43(12レース) + venue 61(12レース) = 24レース・210行**。`Σrace_point` が
車数(7 or 9)に依らず一律 **286〜366（平均約300）**——健全なら競走得点は選手1人あたり
90〜100点で車数に比例した合計になるはずが、そうなっていない。
翌日 2026-06-13 の同一会場は Σ665〜907（正常な水準）に戻っている。

### ②検証で追加確認した点（A_pipeline報告との差分）
`race_point` は `pred_top3_pct`（同じ行の予測3着内率%）と**単純に同一の値ではない**
（例: `20260612_43_01` 車1で race_point=67.2, pred_top3_pct=80.3。比率は行ごとに0.70〜1.27と
不安定）。**「race_point に pred_top3_pct がそのまま上書きされている」という A_pipeline の
説明は仮説として合理的（Σが3着内確率の合計=300%相当に近い）だが、`pred_top3_pct` 列との
1:1完全一致では説明しきれない**。何らかの類似スケールの中間値（別モデル世代の予測値、
または規格化前の値）である可能性が残る。**汚染の存在自体は確定・厳密な発生源の特定は
未確定**として報告を修正する。

### ③影響範囲
- 210行 / 739,646行 = 0.028%（学習データ全体への影響は無視できる規模）
- 汚染対象列 `race_point` を直接・間接に使う特徴量（`score_z`・`score_rank`・
  `line_rp_*`・`rp_*_delta`、item7で見た `MED_RACE_POINT_FILL` の元データにもわずかに影響）
  への影響は規模として極小
- **記録の信頼性への影響が本題**: `docs/prediction-factors.md` は「汚染期間
  2026-06-18〜07-23 は再取得済みで解消」と書いているが、実際に壊れているのは
  **2026-06-12**（その窓の**外**）。加えて再取得パイプライン（`pipeline_wt.py:168`
  `_get_collected_keys`）は `finish_order >= 1` の行（＝結果が入った行）を再取得対象から
  スキップするため、**この24レースは今後どの自動経路でも直らない**

### ④原因のコード位置
現行コードに `UPDATE ... race_point` は無い（`grep -rn "race_point" keirin/src keirin/scripts`
で更新系のクエリを確認したが該当なし）。**過去の事故コードか、収集元（winticketのAPI/スクレイプ）
側の一時的な不具合**のいずれかで、現行コードのバグではないため恒久修正の当てどころが無い。

### ⑤結論・修正案
**依頼どおり修正（DBの書き換え）は実行しない。** 恒久対応として提案できるのは:
1. `scripts/check_race_point_sanity.py` の遡及チェックモードを新設し、
   「S級/SA混合レースで `avg(race_point) < 閾値(例: 50)`」を**過去全期間**に対して
   月次バッチ等で1回だけ回す（現状は当日ぶんにしか掛かっていない＝A_pipeline報告のR8）
2. 汚染が確認された210行に限り、`race_point` を NULL に戻して「欠損」として扱う
   （item7で導入する固定中央値 `MED_RACE_POINT_FILL` で自然に補完される）— **これは
   DBの書き換えを伴うため今回は実行しない。実行するなら対象行リストは
   `repro_item5_race_point.sql` で再現可能**
3. `docs/prediction-factors.md` の「解消済み」という記述を「2026-06-12 分は未解消」に訂正

優先度: 低（学習データに占める割合が極小・現行コードにバグは無い）。ただし
「ドキュメントが実態と食い違ったまま残っている」点は本監査の目的（依頼主の懸念）に
直結するため、記録修正の優先度はやや上げてよい。

---

## item6: 並び・印が未公開のレースの生成側ガード — **確定（コード上のギャップは実在／実害はほぼ観測されない）**

### ①再現・追加検証
`build_type_lab_picks.py` の `run_live()` は `entry_health.missing_market_inputs()` を
一度も呼んでいない（grep実測・`netkeirin_submit_type_lab.py` の入稿側だけが呼ぶ）。

**しかし** 実際に売られた商品への影響を独立に検証した（`repro_item6_lineup_skip_rebuild.sql`）:
```
WITH last_skip AS (... reason_code='missing_lineup' の最終decided_at ...)
→ type_lab_picks(mode='live') の (race_key)ごとの max(generated_at) と比較
```
- 237レース中、「最後の missing_lineup 判定より後に一度もリビルドされていない」行は **7件**、
  **全て2026-08-28以前**（型ラボの実売開始=2026-08-29より前）
- その7件のうち実際に netkeirin へ売られた3件は `origin IN ('rank','marquee_fill')`
  ＝**旧ランク経路の入稂**であり、型ラボの `type_lab_picks(live)` 行とは無関係
  （型ラボはこの時点でまだ並行検証中で自身の入稿経路を持っていなかった）
- **型ラボが実際に売り始めた8/29以降は、この検出方法で0件**
  （＝入稿スクリプト側の `rebuild()`→`_missing_market_inputs()` 再判定ループが、
  観測できた範囲では機能している）

さらに **paper/paper9 モードはこの問題と構造的に無縁**であることを確認:
```
mode   | avg_lag_days（generated_at - race_date）
live   | 0.0
live9  | 0.0
paper  | 326.9（最小1日・最大609日）
paper9 | 315.1（最小24日・最大600日）
```
paper系は常にレース確定の**最短1日後**に生成されるため、生成時点で `wt_entries` は
既にバックフィル済み（欠測が発生する「当日朝」の窓を通らない）。

### ②影響範囲
- **実売（live/live9）への実害は、観測できた範囲で0件**（8/29以降）
- **paper/paper9 への影響は構造的にゼロ**（生成タイミングの問題）
- ただし **コード上の防御は無い**（`build_type_lab_picks.py` 自体は退化入力でも
  p3/型/軸をそのまま計算して書き込む）。`src/entry_health.py` の docstring
  「商品そのものへの影響は無い」という記述は、**入稿側の再判定ループに依存した結果論**であり、
  `build_type_lab_picks.py` 自身が止めているわけではない。将来この再判定ループが
  リファクタで外れたり、type_lab_picks を読む新しい消費者（ダッシュボード等）が
  増えたりすると、無防備に露出する
- 8/29以降で「新規に生成されたが、まだリビルドされていない」一時的な行（次の波で
  上書きされる想定の中間状態）が `/keirin/type-lab` 等の閲覧APIから一時的に見える
  可能性は残る（本監査では時間の都合で個別画面までは確認していない＝限界）

### ③原因のコード位置
`keirin/scripts/build_type_lab_picks.py::run_live()`（生成側・ガード呼び出しなし）
vs `keirin/scripts/netkeirin_submit_type_lab.py:964 _missing_market_inputs(rk)`（入稿側のみ）

### ④修正案
`fix_06_build_type_lab_picks_guard.patch` — `run_live()` に `missing_market_inputs()` を
インポートして直接ガードを追加。退化しているレースは `type_lab_picks` へ**一切書き込まない**
（＝生成をスキップ）。`rebuild(--race-key)` は同じ関数を再度呼ぶだけなので、後の波での
再判定の挙動は変えない。paper系は生成タイミング上ガードがほぼ発火しないため、
既存の後方互換性への影響は無視できる。

### ⑤テスト案
```python
# keirin/tests/test_build_type_lab_picks_lineup_guard.py（新規）
def test_全員同ライン_印ゼロのレースは書き込まれない(monkeypatch):
    # _load_entries が退化データ（line_group全員1・mark全員0）を返すよう差し替え
    # run_live() の戻り値に該当 race_key の行が含まれないことを assert
```

---

## item7: `feature_wt.py:167` の `med_rp`（train/serve skew） — **確定**

### ①再現
```python
# keirin/src/preprocessing/feature_wt.py:166-168（修正前）
df["race_point"] = df["race_point"].replace(0.0, np.nan)
med_rp = df["race_point"].median()          # ← df のスコープ内だけで計算
df["race_point"] = df["race_point"].fillna(med_rp if not pd.isna(med_rp) else 50.0)
```
学習は複数年分の `df`（〜74万行）で呼ばれ中央値は安定するが、配信
（`build_type_lab_picks.py`→`predict_p3_pw`→`load_raw_data_wt(min_date=day, max_date=day)`）は
**その日1日ぶん**（実測400〜650行）だけで呼ばれるため、埋める値が呼び出しごとに変わる。

### ②影響範囲（実測）
```sql
-- 全期間(2022-12-01〜2026-09-20・737,145行)の中央値: 85.55
-- 単日サンプル: 2026-06-10→82.4 / 2026-08-15→91.44 / 2026-09-01→82.68 / 2025-03-15→86.88
```
差は最大で全期間比 **±7%程度**。影響行数は `race_point==0.0` の **2,501/739,646行（0.34%）**
に限られる（この列がそのまま使われる行では補完自体が発火しないため無関係）。
`term`（期）列も同型のパターン（`med_term`）があるが、**`term` は実測でNULLが0件**
（全期間・全行で欠損なし）のため、この列に関しては実害ゼロ（fillnaの分岐が発火しない）。

### ③原因のコード位置
`keirin/src/preprocessing/feature_wt.py:166-168`（`race_point`）。
`:179-180` の `med_term` も同型だが実害なし（上記）。

### ④修正案
`fix_07_feature_wt_med_rp.patch` — 全期間中央値を `MED_RACE_POINT_FILL = 85.55` として
モジュール定数へ固定し、学習・配信のどちらでも同じ値を使うようにする。
**この値は現行の「学習時に動的計算される値」とほぼ同一**（学習は事実上全期間データで
呼ばれるため）なので、**モデルの再学習は不要**（配信側の挙動だけが訓練時の実際の値に
揃う、片側修正で完結する低リスクな変更）。

### ⑤テスト案
```python
# keirin/tests/test_feature_wt_med_rp.py（新規）
def test_race_point_0は固定中央値で補完される():
    df = pd.DataFrame({"race_point": [0.0, 50.0, 100.0], ...必要な他列...})
    out = build_features_wt(df)
    assert out.loc[df["race_point"] == 0.0, "race_point"].iloc[0] == MED_RACE_POINT_FILL

def test_単日の少数データでも中央値がぶれない(小さいdfと大きいdfを両方通し):
    # race_point==0 の行が同じ MED_RACE_POINT_FILL で埋まることを確認
    # （旧実装ならこのテストは母集団サイズで結果が変わり失敗する）
```

---

## item8: 調査中に見つけた他の明白なバグ — **1件確定・パッチ済み**

### 型ラボの採点が「当日+前日」しか見ないため、確定オッズ待ちの行が永久保留になる（S2×S5複合）

`keirin/scripts/settle_type_lab_picks.py::main()` は「当たっているのに `wt_odds` にその
券種の確定オッズがまだ無い」行を `n_wait` として次回に持ち越すが（`外れは着順だけで確定`
という設計自体は正しい）、**日次バッチ（`type_lab_daily.sh`／`type_lab_settle.sh`）は
「当日」と「前日」の2日ぶんしか対象にしない**ため、オッズの反映が2日を超えて遅れた行は
**どのバッチからも二度と拾われず永久保留**になる。

外れは着順だけで確定するため保留に残るのは**的中している行に限られ**、この保留は
ROI・的中率の集計を**常に下振れさせる方向にしか働かない**（警告も出ない）。

#### ①再現
```sql
SELECT mode, count(*) AS n_unsettled_old
FROM keirin.type_lab_picks
WHERE settled_at IS NULL AND race_date < (CURRENT_DATE - INTERVAL '2 days')
GROUP BY mode;
--  paper  | 119   (うち大半は cancel=1 の中止レースで正常な保留。実際に「的中しているのに
--                  配当が引けない」型は sub_settle 報告のとおり12行)
--  paper9 |   3   (同型1行)
```
2026-09-20時点で live/live9 は0件（すべて2日以内に解消している）。

#### ②影響範囲
paper 12行・paper9 1行（sub_settle/REPORT_settle.md §6 で先に特定済み・本監査で0件に
更新されていないことを再確認）。実売（live/live9）には実測で影響なし。全体
100,589+5,783行に対し 13行（0.012%）と極小。

#### ③原因のコード位置
`keirin/scripts/type_lab_daily.sh`（07:15・1日1回）と `keirin/scripts/type_lab_settle.sh`
（15分おき）がどちらも `--date <当日>` `--date <前日>` の2引数しか使わない。
`settle_type_lab_picks.py::_load_targets` 自体は日付を問わず `settled_at IS NULL` を
拾えるので、**呼び出し側が窓を作っているだけ**でロジック自体に欠陥はない。

#### ④修正案
- `fix_08a_settle_type_lab_pending_only.patch` — `settle_type_lab_picks.py` に
  `--pending-only` オプションを追加（日付条件なしで `settled_at IS NULL` を全部対象にする）
- `fix_08b_type_lab_daily_catchup.patch` — 朝バッチ（`type_lab_daily.sh`）の末尾に
  `--pending-only` の呼び出しを追加（1日1回・数百行のSELECTなので負荷は無視できる。
  15分おきの `type_lab_settle.sh` には**あえて足さない**——`type_lab_picks` は
  114,000行超あり `settled_at` に索引が無いため、15分間隔で回すには軽い頻度ではない）

#### ⑤テスト案
```python
# keirin/tests/test_settle_type_lab_pending_only.py（新規）
def test_pending_onlyは日付を問わず未採点行を拾う(sqlite_conn):
    # race_date が3日前で settled_at IS NULL の行を1件仕込む
    # --pending-only で main() を実行し、その行が採点されることを確認
```

---

## 総括（統括者向け）

| # | 判定 | 影響の大きさ | 優先度 |
|---|---|---|---|
| 1 | **確定** | 中（実績集計の母集団に3%規模の混入・7箇所で同型） | **高**（依頼主の最優先KPI=売上の集計自体に効く。パッチは機械的で低リスク） |
| 2 | **確定** | 小（実売25,400円/24日・投資額の0.04%）だが実売にも実在 | 中（金額は小さいが「唯一の正本」の欠陥・是正コストが低い） |
| 3 | **判定不能** | 極小（11,600円・2件のみ） | 低（これ以上の追跡はコスト対効果が悪い） |
| 4 | **確定（コードバグではない・歴史的データ品質事象）** | 小（29件・8/16以降は再発なしを確認済み） | 低（記録の注記のみで十分） |
| 5 | **確定（存在は再確認・発生源は未確定）** | 極小（210/739,646行=0.03%） | 低（ただし「解消済み」というドキュメントの誤りは訂正推奨） |
| 6 | **確定（コードギャップは実在／実害はほぼ未観測)** | 小〜無（8/29以降の実売で0件を確認） | 中（防御が無いこと自体はリスク。安価に塞げる） |
| 7 | **確定** | 極小（0.34%の行・±7%のブレ） | 低〜中（1行修正で再学習不要という低コストな割に、CLAUDE.md自身が掲げる
  「学習と配信は同じ関数を通す」の原則違反を解消できる） |
| 8 | **確定（1件）** | 極小（0.01%・paperのみ） | 低 |

**優先して着手すべき順**: item1（母集団定義の一括修正・7箇所） → item2（唯一の正本である
`keirin_settlement.settle()` の是正、実売にも実在するため） → item7（1行・再学習不要） →
item6（防御追加） → item8（cron 1行追加） → item5（ドキュメント訂正のみ） →
item4（追加対応不要・記録の注記のみ） → item3（棚上げ）。

## 成果物一覧（本フォルダ）
```
notes.md                                          本ファイル
fix_01a_keirin_router.patch                       item1: backend/src/api/keirin_router.py
fix_01b_sold_performance_report.patch             item1: keirin/scripts/sold_performance_report.py
fix_01c_nightly_review_type_lab.patch             item1: keirin/scripts/nightly_review_type_lab.py（4箇所）
fix_01d_keirin_type_lab_router_sold.patch         item1+2: backend/src/api/keirin_type_lab_router.py
fix_02a_keirin_router_valid_cars.patch            item2: backend/src/api/keirin_router.py（valid_cars配線）
fix_02b_keirin_settlement_void.patch              item2: backend/src/services/keirin_settlement.py（正本修正）
fix_02c_keirin_settlement_cache_version.patch     item2: SETTLE_VERSION 1→2（キャッシュ失効）
fix_02d_settle_type_lab_picks_void.patch          item2: keirin/scripts/settle_type_lab_picks.py
fix_02e_migration_void_refund.py                  item2: alembicマイグレーション案（未適用）
fix_06_build_type_lab_picks_guard.patch           item6: keirin/scripts/build_type_lab_picks.py
fix_07_feature_wt_med_rp.patch                    item7: keirin/src/preprocessing/feature_wt.py
fix_08a_settle_type_lab_pending_only.patch        item8: keirin/scripts/settle_type_lab_picks.py
fix_08b_type_lab_daily_catchup.patch              item8: keirin/scripts/type_lab_daily.sh
repro_item2_void_detect.py                        item2 再現スクリプト
repro_item3_gami_mismatch.sql                     item3 再現SQL
repro_item4_missing_sales.sql                     item4 再現SQL
repro_item5_race_point.sql                        item5 再現SQL
repro_item6_lineup_skip_rebuild.sql               item6 再現SQL
repro_item7_med_rp_skew.sql                       item7 再現SQL
q2_dump.sql / q2_entries.sql / q2_races.sql        item2 のCSVダンプ用クエリ（元データ）
```

全パッチは `/tmp/p4checkout`（本監査用の一時 git clone）に対し `git apply --check` で
適用可能なことを確認済み。`fix_01a`と`fix_02a`（同一ファイル `keirin_router.py`）、
`fix_02d`と`fix_08a`（同一ファイル `settle_type_lab_picks.py`）は組み合わせても
競合なく両方適用できることも確認済み。変更後のPythonファイルは全て `ast.parse` で
構文エラーが無いことを確認済み（実際のテストスイート実行は未実施＝限界）。
