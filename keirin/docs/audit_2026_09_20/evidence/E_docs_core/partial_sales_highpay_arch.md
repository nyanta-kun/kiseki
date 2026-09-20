# 監査: sales_kpi.md / highpay_5slots_2026_09_06.md / system-architecture.md

対象:
- `keirin/docs/sales_kpi.md`（最終更新記載 2026-08-24、更新履歴は08-26まで）
- `keirin/docs/highpay_5slots_2026_09_06.md`（2026-09-06、末尾に09-06付の追記2本あり）
- `keirin/docs/system-architecture.md`（最終更新記載 **2026-08-03**）

検証方法: DB・実行はせず、`keirin/src/`・`keirin/scripts/`・`backend/src/services/`・`frontend/src/`
の実物を Read/Grep で突き合わせた。ドキュメントの記述そのものは証拠として扱っていない。

---

## 1. 主張抽出表

凡例: 本番依拠列「はい」= 現在の入稿・判定・cronの挙動に直接効く定数/経路の主張。
「要確認」= コードでは検証不能（DB実測・外部サイトの実測・売上ランキング等）。

| 文書:行番号 | 主張の要約 | 根拠として挙げられた窓/スクリプト | 本番依拠 | 状態 |
|---|---|---|---|---|
| sales_kpi:5 | 販売価格は300pt据え置き（`SALE_PRICE_DEFAULT`） | — | はい | 未検証（値未確認・下記参照） |
| sales_kpi:16-25 | 用語定義（有償pt/表示的中率/売上/紹介料/収益の計算式） | `keirin_sales_report.REVENUE_RATE`/`REFERRAL_RATE` | 要確認 | 未検証（財務系ファイル未確認・時間の都合） |
| sales_kpi:36-45 | 「売れ筋10位」= 有償17,000pt/日という推定値 | α=0.5〜0.7の当てはめ | 要確認 | 明示的に「推定であって測定ではない」と自己申告済み（現行） |
| sales_kpi:59-86 | 直近実測 2026-08-17〜23: 入稿332R・個/R 1.10・有償pt 12,264/日等 | `netkeirin_sales_race` | 要確認（DB実測） | 時点実測。現行かは要DB確認 |
| sales_kpi:113-118 | 商品系統の日次回帰: 高配当系1本＝的中系6.4本、係数775/121 | 8/03-23・21日・736R | 要確認 | 自己申告で「n=21日・交絡あり」と限定 |
| sales_kpi:196-212 | 高配当系候補供給表: 7T2候補20.0(未投入)/7M1 12.2(稼働・優先最下位)/7H2 10.7(`enabled=false`)/7T1 4.9(`RANK_7T1_DAILY_CAP=5`)/9H1 4.2(`enabled=false`)/7H1 2.9 | `picks_history` 2026-08 | **はい** | **`RANK_7T1_DAILY_CAP` は現在 `0`（無効=上限なし）。§2.1参照で不一致** |
| sales_kpi:210-212 | 「1レース1商品」の実効優先順位は `RANK_CONFIGS` の定義順だが `enabled=false` は飛ばされるので定義順と実効順は不一致 | — | はい | 一般論としては現在も正しい構造 |
| sales_kpi:216-284 | 運用基準（1日36〜43本・出す/出さない優先度・的中時ターゲット・的中率目標） | — | 要確認（方針・DBでのみ検証可） | 未実装確認（コードにこの数値のenforceはない=方針文書） |
| sales_kpi:287-309 | 移行計画フェーズP1〜P3（高配当系6→9→12→15本/日） | — | 要確認 | 方針。現状値は§2.1参照で既に大きく変化 |
| sales_kpi:303-306 | 実装上の前提: ①7H2/9H1のenabled化 ②`RANK_7T1_DAILY_CAP=5`引き上げ検討 ③予選×デイ/ナイター除外 ④7Sの配分下限 | — | はい | ①③④は要DB/コード確認。②は**前提が既に崩れている**（cap自体が0=撤廃済み。§2.1） |
| sales_kpi:328-332 | 取得元: `netkeirin_sales_race`（VPS cron 9:40）/`netkeirin_submissions.rank_key` | `scrape_netkeirin_sales.sh` | はい | **確認**: `scripts/scrape_netkeirin_sales.sh` 実在（kiseki直下） |
| sales_kpi:336-362 | 競合14者の週次実測台帳（回収率・お気に入り数） | netkeirin公表値+復元式 | 要確認（外部サイト） | 検証対象外 |
| sales_kpi:385-419 | 平均払戻2万円ゲート導入と影響実測（三連複のみ対象・件数/pt/R/無売上率） | `bet_detail` | 要確認（DB実測） | — |
| sales_kpi:403-419 | 差し替え先候補: 7T2(3.1/日・ペーパー未投入)/7H2(0.8・`enabled=false`)/7T1(0.5・cap=5で頭打ち) | — | はい | `RANK_7T1_DAILY_CAP` は現状0のため「cap=5で頭打ち」という記述はもう成立しない |
| sales_kpi:449-478 | 公開は自動でなく人がボタンを押す/公開済みは取消不可/取消は必ず公開前 | — | はい | コード側で裏付け可能な運用ルール。§11.6以降で自動化に切替（後述） |
| sales_kpi:462-475 | 日次取消率の表（08-18〜08-24・`netkeirin_submissions.status='deleted'`基準） | DB実測 | 要確認 | **自己申告で「自動化後はこの表は作れなくなる」と明記**。現在は自動化済み（下記）なのでこの監視方法は既に廃止対象 |
| sales_kpi:532-539 | 平均払戻ゲートを自動化する2026-08-24ユーザー判断。`test_min_mean_payout_gate.py`の「自動では落とさない」docstringを反転させる指示 | — | はい | **確認**: 実装は自動化済み（下記コード確認） |
| sales_kpi:541-557 | 自動化により`continue`規約で差し替えが無料。実効優先順位 **7S > 9C > 7B > 7C > 7T1 > 7H1 > 7M1**。7T1/7H1はゲート対象外（三連単経路） | `netkeirin_submit_wt.py` L1964 | **はい** | **不一致（file:line付きで下記）**。実際の順序は `7H2 > 9H1 > 7T1 > 7T3 > 7S > 9C > 7B > 7C > 7H1 > 7M1`。sales_kpi の並びは 7H2・7T3・9H1 が抜け、かつ 7T1 の位置が7Sより下という誤り |
| sales_kpi:559-585 | 看板穴埋め経路`_process_manual()`に3ゲート（`MIN_POINT_ODDS`/`MIN_EXPECTED_PAYOUT_BY_RANK`/`MIN_MEAN_PAYOUT`）が「どれも入っていない」 | 実装読解（当時） | **はい** | **当時は正しいが現在は一部訂正済み**（下記）。`MIN_MEAN_PAYOUT` は2026-08-24中に`_process_manual`へ追加済み。他2つは意図的に未追加のまま（doc自身が「勝手に広げるな」と書いている方針どおり） |
| sales_kpi:587-601 | 自動化で失う可視性の手当て（1行ログ・見送り件数のサマリー化・Discord通知） | — | 要確認 | 実装有無は本タスク範囲外（`submission_skips`系に一部吸収されている可能性。CLAUDE.md記載と整合） |
| sales_kpi:603-627 | レビュー画面の一括取消UIと`mean_payout`/`cheap_mean_payout`を2026-08-26に削除 | commit | はい | **確認済み（file:line付きで下記）**。整合 |
| sales_kpi:629-637 | 公開時刻の実測差（自社07:17 vs競合各社） | — | 要確認 | 外部・DB |
| — | 高配当系=7T1/7H1/7H2/9H1、的中系=7S/7C/7B/9C/7M1（用語定義） | — | はい | 用語自体は現行のランク集合（`CURRENT_PAPER_RANKS`）と整合。ただし 7T3 が「高配当系/的中系」どちらにも分類されておらず（新設ランク未反映） |
| highpay:22-30 | 競合14人・26,602件で「的中率×平均払戻=ROI×購入額」が例外なく成立、ROI63-89% | `exp_hot/month2.jsonl` | 要確認（外部） | ファイル実在確認（下記） |
| highpay:26-32 | 自社は既に高額枠1日約5.7本(`F_sign` 4.0 + `A_ana` 1.8)。9/1川崎3Rの224,130円は`F_sign`の2点 | `type_lab_picks` | はい | 概念上整合。数値自体は09-06時点のDB実測（未再検証） |
| highpay:93-107 | `*_hit`系は10万+が構造的に作れない(0.00〜0.11%)。`F_pay`10万+比率2.20% | 確認窓2026-01〜08-26・238日 | 要確認（DB） | — |
| highpay:109-131 | 看板枠`*_sign`型別ROI: 型B/Cが型A/E/Fより10-20pt上（両窓） | ブートストラップCI | 要確認 | 自己申告でCI重複を明記。方向のみ主張 |
| highpay:189-214 | `daily_cap`落ち型B 2.8+型C 2.6+型D 1.2=6.6R/日で5本を置換なしで置ける | 2026-08-30〜09-05実測 | はい（設計根拠） | 実装（`HIGHPAY_TYPES=("B","C","D")`）と整合（下記確認） |
| highpay:230-232 | 1レース1万円では10万+は1日1回出せない。看板枠上限1.7件/日（`SIGNBOARD_TYPES`節） | `src/type_lab.py` | はい | **確認**: `SIGNBOARD_TYPES=("F",)` 型Fのみ。1.7件/日の値自体は未検証だがロジック整合 |
| highpay:482-573 | 2026-09-06実装: `HIGHPAY_TYPES=("B","C","D")` / `HIGHPAY_SLOTS_PER_DAY=5` / `HIGHPAY_N_ENTRIES=7` 他 | `src/type_lab.py`変更点表 | **はい** | **確認**（file:line、下記）。ただし現在の `HIGHPAY_SLOTS_PER_DAY` は09-06時点の5→09-12に10へ変更→09-19に再び5へ戻された経緯があり、**doc記載の「5」は結果的に現在値と一致するが、根拠は09-06当時のものでその後の10本実験・失敗は本ファイルに記載されていない**（別ファイルへ分岐） |
| highpay:595 | 倍率型枠は「未測定（台`/tmp/race_type_board.npz`の再構築が要る）」 | — | はい | **確認**: 現在は`/tmp/race_type_board.npz`が存在する（09-19生成）。本書§9で同日中に再構築・検討済み、後続文書で決着 |
| highpay:602-613 | 層3（別アカウント）は2026-09-06時点で「予想家アカウントは1つ」というユーザー確定制約により不可 | — | 要確認（事業判断） | 事業判断。コード検証対象外 |
| highpay:732-759 | 2026-09-06判断②: 5本の内訳を3:2(`{型}_sign`/`{型}_big`)、`HIGHPAY_BIG_TARGET=400,000`,`HIGHPAY_BIG_SLOTS={2,4}` | `src/type_lab.py` | **はい** | **確認**（file:line、下記）。`HIGHPAY_BIG_SLOTS`は09-12時点でも「据え置き」と後続コード内コメントにあり整合 |
| system-arch:9-11 | 「7車立て・9車立てレースを7S/7A/7B/9S/9A（4内部rank・4表示ラベル）で予想」 | — | **はい** | **重大な不一致（file:line付き、下記）**。7A/9A は2026-08-14に7S/9Cへ統合され現存しない。9Sも9Cへ置換済み |
| system-arch:252-273 | 「現行ランクは以下の5内部rank/5表示ラベル（2026-08-03時点）」としつつ表に7行（7S/7A/7B/9S/9A/7C/7H1）を掲載 | — | はい | **文書内部矛盾**（見出し「5」と実表「7行」が食い違う）。かつ現在の実際のランク集合（10種）とも不一致 |
| system-arch:269-273 | 優先順位 `7H2 > 7T1 > 7T3 > 7S > 7B > 7C > 7H1 > 7M1`（`RANK_CONFIGS`定義順が正本） | `netkeirin_submit_wt.RANK_CONFIGS` | **はい** | **部分的に不一致**：実際の完全な定義順は `7H2, 9H1, 7T1, 7T3, 7S, 9C, 7B, 7C, 7H1, 7M1`。system-architecture.md の並びは9H1・9Cを省いているだけで、書かれている7ランク間の相対順は現在のコードと一致（7Cが直前に更新されたコメントとも一致） |
| system-arch:194-251 | 毎朝8:00単一バッチフロー（daily_picks_wt.sh、①〜⑩の手順） | crontabバックアップファイル名 | はい | `daily_picks_wt.sh` 実在確認（下記）。ただし**型ラボ（`type_lab_daily.sh`・`netkeirin_submit_type_lab.py`・`nightly_review.sh`）が一切登場しない**——2026-08-27以降に本番稼働している別の入稿経路が本フローの記述から完全に欠落 |
| system-arch:288-343 | モデル配布 `sync_models_to_vps.sh`・`ensure_monthly_vintage.sh`・cron登録詳細 | — | はい | スクリプト実在確認（下記） |
| system-arch:1 | 最終更新: 2026-08-03 | — | — | **今日は2026-09-20。約48日間、本番の主要変更（7A/9A統合・9C新設・7M1/7H2/9H1/7T1/7T3新設・型ラボ全体）が未反映** |

---

## 2. コード突き合わせで見つかった食い違い（詳細）

### 2.1 🔴 `RANK_7T1_DAILY_CAP` — sales_kpi.md の主張は現在のコードと矛盾する

- **sales_kpi.md:203** 「7T1 | 4.9 | 3.9 | 稼働（`RANK_7T1_DAILY_CAP = 5`）」
- **sales_kpi.md:304** 「`RANK_7T1_DAILY_CAP = 5` の引き上げを検討する（候補は4.9件/日でほぼ上限に張り付いている）」
- **sales_kpi.md:412** 「7T1 | 8 | 0.5 | 稼働（cap=5 で頭打ち）」

これらはいずれも「7T1の日次上限が5」という前提を置いている。しかし:

```
keirin/src/strategy_wt.py:4393
RANK_7T1_DAILY_CAP = 0
```

コード直前のコメント（`strategy_wt.py:4386-4392`）は次のように明記している:

> 🔴 **2026-08-24 に撤廃した（5 → 0）。** … 代わりに母集団を「決勝のみ×別ライン」
> へ絞ることで 2.20件/日・ROI 106.3% にした（`rank_7t1_is_target_race_type`）。
> 比率の問題は母集団が薄くなったこと自体で解決している。
> ⚠️ 0 は「上限なし」。`rank_7t1_daily_select` は falsy を上限なしとして扱う。

sales_kpi.md の最終更新記載も **2026-08-24**（更新履歴の最新行も08-26）であり、
このcap撤廃と**同じ日付**である。ドキュメントとコードのどちらが先だったかは
git blame でしか判断できないが、少なくとも**現在(2026-09-20)の本番は
`RANK_7T1_DAILY_CAP=0`（無制限、ただし母集団を「決勝×別ライン」に絞り込み済み）
であり、sales_kpi.md の§5.2・§7・§11.2の「cap=5」前提の記述は全て現状と食い違う**。
これに依拠した移行計画（§7 P1〜P3、7T1供給4.9件/日という数字）はその後の
母集団変更（決勝×別ラインへの絞り込みで2.20件/日に減少）を反映していない。

### 2.2 🔴🔴 入稿優先順位の実効順 — sales_kpi.md と system-architecture.md で異なり、どちらも完全ではない

実際の `RANK_CONFIGS`（`keirin/scripts/netkeirin_submit_wt.py:312-`）のキー定義順を
`grep -n '^    "[0-9A-Za-z]*":'` で確認した結果:

```
7H2 (L323) → 9H1 (L342) → 7T1 (L381) → 7T3 (L405) → 7S (L422) → 9C (L437)
→ 7B (L450) → 7C (L475) → 7H1 (L498) → 7M1 (L524)
```

コード自身のコメント（`netkeirin_submit_wt.py:269`）も
「優先順位（2026-08-21 現在）: **7H2 > 7T1 > 7T3 > 7S > 7B > 7C > 7H1 > 7M1**」
とほぼ同じ順（9H1/9Cは9車専用で7車ランクと衝突しないため注記から省かれている）。

| 文書 | 記載された優先順位 |
|---|---|
| **system-architecture.md:272** | `7H2 > 7T1 > 7T3 > 7S > 7B > 7C > 7H1 > 7M1` |
| **sales_kpi.md:552** | `7S > 9C > 7B > 7C > 7T1 > 7H1 > 7M1` |
| **実コード（現在）** | `7H2, 9H1, 7T1, 7T3, 7S, 9C, 7B, 7C, 7H1, 7M1` |

- system-architecture.md の記載は**7車ランクに限れば現在のコードと一致**（9H1/9Cを
  除外している点は明示的にそう書かれており矛盾ではない）。
- **sales_kpi.md の記載は現在のコードと食い違う**。7T1 を 7S・7B・7C より**下位**に
  置いているが、実際は 7T1 は 7S より**上位**（7H2, 9H1 の次）にある。7H2・7T3 の
  存在も欠落している。sales_kpi.md の当該節（§11.6.1）は2026-08-24時点の
  記述であり、7T3ランク自体が新設されたのは2026-08-24（ドキュメントの
  自己主張の直後）〜08-26頃と見られるため、単純な更新漏れの可能性が高い。

### 2.3 🟡 看板穴埋め経路のゲート欠落は一部是正済み（sales_kpi.md は追記していない）

sales_kpi.md §11.6.2（603行手前、`看板穴埋め経路にはゲートが1つも入っていない`）は、
`_process_manual()` に `MIN_POINT_ODDS` / `MIN_EXPECTED_PAYOUT_BY_RANK` /
`MIN_MEAN_PAYOUT` の3ゲートが「どれも入っていない」と述べる。

現在のコード（`netkeirin_submit_wt.py:2789-2822`）を確認すると:

```python
# 🔴 看板穴埋めにも平均払戻ゲートを掛ける（2026-08-24・§11.6.2）。
#    この経路は `submit_marquee_wt.py` → subprocess → ここ、で
#    実入稿の43%（240/562件）を占める。入れないとゲートは
#    対象の半分以下にしか効かない。
...
_mean = _mean_payout_too_low(
    build_bet_lines(legs, manual_pred_board),
    n_cars=n_entries, race_key=race_key)
if _mean is not None:
    _skip(race_key, rank_key, session, SKIP_GATE_MEAN_PAYOUT, ...)
```

＝ **`MIN_MEAN_PAYOUT` は既に `_process_manual()` に実装済み**（コード内コメント日付も
2026-08-24で、sales_kpi.md 自身が要求した是正と一致）。一方、コード内コメントは
明示的に

> ⚠️ `MIN_POINT_ODDS` と `MIN_EXPECTED_PAYOUT_BY_RANK` は**ここへ広げないこと**。
> 今回のユーザー判断に含まれておらず…広げるには別途のユーザー判断が要る。

としており、この2つは意図的に未実装のまま。sales_kpi.md の記述「3ゲートがどれも
入っていない」は**現在は不正確**（1/3は実装済み）。ただし更新履歴（§末尾）は
08-26付までしかなく、この是正を追記していない。実害は小さい（是正内容は
sales_kpi.md 自身が提案した方向と一致しており、結論を誤らせるものではない）。

### 2.4 🔴🔴 system-architecture.md の「現行ランク体系」は全面的に陳腐化している

`system-architecture.md:9-11`:
> 7車立て・9車立てレースを **7S / 7A / 7B / 9S / 9A**（4内部rank・4表示ラベル。
> S1 は 2026-07-31 全廃・7SS は 2026-08-02 全廃）で予想し…

`system-architecture.md:252-260` の表も同じ5(実表記は7)ランクを列挙。

しかし `keirin/src/strategy_wt.py:4906-4948` の `CURRENT_PAPER_RANKS`（コード内の
「単一正本」）を見ると、現在の実ランクは:

```
RANK_7S, RANK_7B, RANK_9C, RANK_7C, RANK_7M1, RANK_7H1, RANK_7H2, RANK_9H1,
RANK_7T1, RANK_7T3    （計10ランク）
```

かつコード内コメント（同ファイル4907-4916行）に明記されている通り:
> 🔴 2026-08-14: 旧 7SS / 7A を RANK_7S へ統合した（ユーザー判断）
> 9車のベースモデル（2026-08-14〜）。旧 9S/9A を置換した。

＝ **`7A` と `9A` は2026-08-14に廃止され、`9S` も同日 `9C` に置き換わっている**。
system-architecture.md が「現行」として掲げる5ランク中、7A・9S・9A の3つは
現存しない内部rankであり、逆に現行10ランクのうち 9C・7M1・7H1・7H2・9H1・7T1・7T3
の7つ（7H1のみ system-architecture.md に記載あり、他6つは完全に欠落）が
未記載である。

さらに system-architecture.md 自身の見出し「**5内部rank / 5表示ラベル**」と、
直後の実表（7行: 7S/7A/7B/9S/9A/7C/7H1）の**行数（7）が一致しない**という
文書内部の矛盾もある。

### 2.5 🔴 system-architecture.md の毎朝フロー図に「型ラボ」経路が一切登場しない

`system-architecture.md:194-251` の「毎朝の自動実行フロー」節は
`daily_picks_wt.sh` の①〜⑩の手順を詳述するが、以下のファイル・仕組みへの
言及が一切ない:

- `keirin/scripts/type_lab_daily.sh`（実在確認済み）
- `keirin/scripts/netkeirin_submit_type_lab.py`（実在確認済み・74,802 bytes）
- `keirin/scripts/nightly_review.sh` / `nightly_review_type_lab.py` /
  `nightly_report_html.py` / `nightly_triage.sh`（すべて実在確認済み）
- `keirin/src/type_lab.py`（高額枠・看板枠・6型判定など、highpay文書が
  参照する実装本体）

`keirin/CLAUDE.md`（同ディレクトリの正本ドキュメント）には
「`docs/RECOMMENDATION.md` を読む」「型ラボは PR #324〜336 で出荷、cron=日次7:15」
との記載があり、型ラボは少なくとも2026-08-27時点で本番稼働している別経路である。
system-architecture.md はこの経路の存在に一切触れておらず、「毎朝の自動実行
フロー」という節タイトルに反して**本番で実際に毎朝走っているジョブの半分
（旧ランクのnetkeirin_submit_wt経路のみ）しか記述していない**。

### 2.6 確認できた一致（highpay_5slots_2026_09_06.md 側）

以下は file:line で突き合わせ、記載どおりであることを確認した:

| doc記載 | 実装 |
|---|---|
| `HIGHPAY_TYPES=("B","C","D")` | `src/type_lab.py:1382` 一致 |
| `HIGHPAY_SLOTS_PER_DAY=5` | `src/type_lab.py:1423` 一致（ただし後述の経緯注記あり） |
| `HIGHPAY_N_ENTRIES=7` | `src/type_lab.py:1425` 一致 |
| `HIGHPAY_BIG_TARGET=400,000` | `src/type_lab.py:296` 一致 |
| `HIGHPAY_BIG_SLOTS={2,4}` | `src/type_lab.py:298` 一致 |
| `AXIS_GATE_EXEMPT_PLANS`に`{B,C,D}_sign`/`{B,C,D}_big` | `backend/src/services/keirin_type_lab_gate.py:145-160` 一致 |
| `SIGNBOARD_TYPES=("F",)`（現行看板枠は型Fのみ） | `src/type_lab.py:189` 一致 |
| `SIGNBOARD_TARGET=150,000`（計画15万） | `src/type_lab.py:263` 一致 |
| `cancelKeirinPicksAction()` / `mean_payout`/`cheap_mean_payout` の削除（2026-08-26） | `frontend/src/app/keirin/actions.ts:255` コメントに削除記録あり／`frontend/src/lib/api.ts:1990`／`backend/src/api/keirin_router.py:3103` すべて確認 |
| `ORIGIN_HIGHPAY = "highpay_fill"` | `scripts/netkeirin_submit_wt.py:174` 一致、`scripts/netkeirin_submit_type_lab.py:125,1093` で使用確認 |
| `_is_enabled()` が fail-open（設定行が無ければ常時ON） | `scripts/netkeirin_submit_wt.py:861-863` 一致 |
| `_approval_required()`/`_auto_publish_enabled()` の存在（承認制/自動公開の実装） | `scripts/netkeirin_submit_wt.py:866,896` 一致 |

⚠️ ただし `HIGHPAY_SLOTS_PER_DAY=5` の一致は**経緯を伴わない偶然の一致**である点に
注意。`src/type_lab.py:1385-1421` のコメントによれば、2026-09-06時点の「5」は
**2026-09-12に一度「10」へ変更され、2026-09-19に「表示的中の代償が見合わない」
という理由で再び「5」へ戻された**。highpay_5slots_2026_09_06.md 自体はこの
中間の10本実験・失敗の経緯を一切記載していない（当然、文書の日付が09-06のため）。
現在のリポジトリ内では `docs/type_lab/highpay_slots_measured_2026_09_12.md` が
その後継文書として存在すると推測される（本タスクの対象外のため中身は未読）。
**「この文書の数字は今も正しい」は成立するが、「この文書に書かれた理由で今の値が
決まっている」は成立しない**——監査上は区別して報告する必要がある。

---

## 3. 文書間・内部の矛盾

### 3.1 「高額枠」という語が指すものが sales_kpi.md と highpay_5slots_2026_09_06.md で完全に別物

- **sales_kpi.md** の「高配当系」= `7T1 / 7H1 / 7H2 / 9H1`（`netkeirin_submit_wt.py` の
  `RANK_CONFIGS` 経由・旧来のランク体系）
- **highpay_5slots_2026_09_06.md** の「高額枠」= `type_lab.py` の `{B,C,D}_sign` /
  `{B,C,D}_big`（型ラボ経由・`netkeirin_submit_type_lab.py`）

両者は実装上まったく別の入稿経路・別のテーブル（`picks_history` vs
`type_lab_picks`）・別のゲート機構であり、どちらも「1日◯本の高額狙い枠」を
論じているが**重複や整合について言及した記述がどちらの文書にも無い**。
2026-09-06時点で両方の仕組みが並行稼働している場合、「高額枠は合計で1日何本
出ているか」「netkeirinの1レース1商品の制約はランク横断（7T1系 と type_lab系）
でも効くのか」という疑問に、この2文書のどちらも答えていない。
sales_kpi.md（8月時点）はtype_labの存在自体に触れていない。

⚠️ 依頼で指摘された「高額枠 1日10本→5本」というコミット（#588）は
**type_lab側の`HIGHPAY_SLOTS_PER_DAY`の話であり、sales_kpi.md の「高配当系」議論
（7T1/7H1/7H2/9H1の本数）とは無関係**。sales_kpi.md 側に「1日10本→5本」に
相当する記述は無い（探索したが該当なし）。両文書の間で本数についての直接的な
数値矛盾（同じ対象について違う数字）は見つからなかった——ただし、これは
「対象が違うので矛盾しようがない」だけであり、望ましい状態ではない
（章立てが分かれているため読者はこの2つの「高額枠」施策が別物だと気づきにくい）。

### 3.2 system-architecture.md 内部の矛盾（見出し数値と実表の行数）

`system-architecture.md:252` 「現行ランクは以下の**5内部rank / 5表示ラベル**」
という見出しの直後（253-263行）の表は **7行**（7S/7A/7B/9S/9A/7C/7H1）を
掲載している。見出しの「5」がどの時点のどの集合を指すのか本文からは分からず、
数字が実表と一致しない。

### 3.3 sales_kpi.md 内部の時系列矛盾（cap=5 記述と自動化判断が同日）

sales_kpi.md の最終更新日は2026-08-24。同じ2026-08-24付けで
「`RANK_7T1_DAILY_CAP = 5` を前提にした差し替え候補試算（§11.2/§7）」と
「平均払戻ゲートの自動化ユーザー判断（§11.6）」の両方が記載されているが、
実際のコード（`strategy_wt.py:4386`）では**同じ2026-08-24にcapが5→0へ撤廃**
されている。ドキュメント内でこの撤廃に言及した記述は無く、§5.2・§7・§11.2の
「cap=5」記述がいつの間にか古い前提のまま残った可能性が高い。

---

## 4. スクリプト実在確認・方法論の懸念

### 4.1 実在確認結果

| 参照スクリプト/ファイル | 実在 | 備考 |
|---|---|---|
| `keirin/scripts/scrape_netkeirin_sales.sh`（root相当、kiseki直下） | ✅ `kiseki/scripts/scrape_netkeirin_sales.sh` | sales_kpi.mdの説明はkeirin CLAUDE.md記載と整合 |
| `keirin/scripts/netkeirin_publish_wait.py` / `netkeirin_sync_status.py` | ✅ | — |
| `keirin/scripts/exp_hot/month2.jsonl` | ✅ | highpay文書の一次資料 |
| `keirin/scripts/exp_hot/reports/*.md`（424_345_410.md 等） | ✅（4ファイル確認） | 546_583.md / 614_482_428_506.md / 465_354_401_585_350.md 等、doc内で言及した予想家番号の組み合わせと一致するファイル名が存在 |
| `keirin/scripts/exp_type_lab/big30.py` | ✅ | §9再現コマンドどおり |
| `/tmp/race_type_board.npz` | ✅（2026-09-19生成） | 09-06時点は「消えている」と記載、その後別作業で再構築された形跡（本タスク範囲外） |
| `keirin/scripts/daily_picks_wt.sh` | ✅ | — |
| `keirin/scripts/sync_models_to_vps.sh` / `ensure_monthly_vintage.sh` | ✅（`ls scripts/`一覧で確認） | — |
| `keirin/scripts/type_lab_daily.sh` / `netkeirin_submit_type_lab.py` / `nightly_review.sh` / `nightly_review_type_lab.py` / `nightly_report_html.py` / `nightly_triage.sh` | ✅ すべて実在 | system-architecture.md 未記載（§2.5） |
| VPS/Mac の実 crontab ファイル | ❌ 確認不能 | 本エージェントはリポジトリのみ読み取り可能。`~/crontab_backup_20260801.txt` 等はリポジトリ外（VPS/Mac側）の退避ファイルで、この監査では実物を見られない。**system-architecture.md の cron 時刻（8:00等）はスクリプトのコメント・ヘッダーからの間接確認のみ**で、実際にVPS crontabへ登録されているかは検証できていない |

### 4.2 スクリプトの方法論の流し読み所見

`keirin/scripts/exp_type_lab/big30.py`（highpay §9 の再現スクリプト）、
`keirin/scripts/netkeirin_submit_type_lab.py`（型ラボ入稿本体）を対象に
明白な欠陥の有無を確認する目的で概観した。

- `netkeirin_submit_type_lab.py` は**本番の入稿スクリプトそのもの**であり、
  母集団・窓の議論は行っていない（学習/評価ではなく配信ロジック）。
  過去分の値を使ってその日の意思決定をする類のリークは構造的に発生しない
  （読んでいるのは当日生成された `type_lab_picks` 行のみ）。
- `big30.py` は探索窓（2025年通し）と確認窓（2026年）を明示的に分けて集計する
  構成になっている（highpay文書 §9 の「探索 / 確認」の列がそのまま
  このスクリプトの出力形式と一致）。ざっと見た範囲では**学習窓=評価窓の混同は
  見当たらない**。ただし本エージェントは実行していないため、実際に呼んでいる
  予測オッズモデル（`odds_tf_n7`）の `train_end` が2025-12-31であることは
  **highpay文書自身が§9.6で「探索窓は in-sample」と明記**しており、
  この点はドキュメントが誠実に自己申告している（隠されていない）。
- `exp_hot/*.py`（競合14人の逆解析スクリプト群）は外部サイトのスクレイピング
  結果（`month2.jsonl`）を集計するだけで、モデル学習・評価窓の概念自体が
  無い（記述統計）。生存者バイアスや学習=評価窓の混同は構造的に該当しない
  カテゴリのスクリプト。
- 母集団の選び方（highpay §4「捨てているレースに置いても看板の本数は落ちない」
  の実測）は `axis_sum` 降順の**近似**であると doc 自身が §9.6 で明記しており
  （「本番は `(2×軸信頼+実力伯仲)/3` を波ごと」）、正確な本番ロジックの再現では
  ないことを自己申告している。これは欠陥というより限界の適切な開示。

**総じて、この2文書（sales_kpi.md / highpay_5slots）は「効かない」「不採用」
「効いた」を主張する際に対照実験・CI・両年窓比較を伴っており、他のMEMORY.md記載
の否定済み事例（対照なしで結論、学習=評価窓混同）のような明白な方法論的欠陥は
見当たらなかった。** 見つかった問題は主に「その後の変更が文書に反映されていない
（鮮度）」「複数文書間で同じ実効値が食い違う（優先順位・cap値）」という
**ドキュメントの経年劣化**であり、測定手法そのものの欠陥ではない。

---

## 5. まとめ（重大度順）

1. 🔴🔴 **system-architecture.md は最終更新2026-08-03のまま48日間放置**され、
   現行ランク体系（7A/9A/9S廃止・9C/7M1/7H2/9H1/7T1/7T3新設）と型ラボ
   サブシステム全体が完全に欠落している。「本番の構成そのもの」を語る文書として
   現状を全く反映していない。
2. 🔴🔴 **sales_kpi.md の入稿優先順位（§11.6.1）と `RANK_7T1_DAILY_CAP=5`前提
   （§5.2, §7, §11.2）は現在のコードと矛盾する**。特に優先順位は7T1の
   位置が実際と逆転しており、この文書を根拠に構成判断をすると誤る。
3. 🟡 sales_kpi.md §11.6.2 の「ゲート0/3」は現在1/3是正済み（悪い方向の
   ズレではなく、文書の追記漏れ）。
4. 🟡 system-architecture.md 見出し「5内部rank」と実表7行の内部矛盾。
5. 🟢 highpay_5slots_2026_09_06.md は自己申告（in-sample窓・近似再現・CI幅）が
   誠実で、数値の大半はfile:lineで裏付けが取れた。ただし「高額枠」という語が
   sales_kpi.md の「高配当系」と別物であることに触れておらず、読者が混同する
   リスクがある。`HIGHPAY_SLOTS_PER_DAY=5` は現在値と一致するが、それは
   09-06時点の根拠がそのまま生きているからではなく、09-12→09-19の
   変更と揺り戻しを経て偶然同じ値に戻っただけ。
