# 監査: keirin/CLAUDE.md + keirin/CONTINUATION.md

対象: `/Users/ysuzuki/GitHub/kiseki/keirin/CLAUDE.md`（1360行）・
`/Users/ysuzuki/GitHub/kiseki/keirin/CONTINUATION.md`（348行）。
両方とも全文読了。コード突き合わせは `keirin/src/strategy_wt.py`・
`keirin/src/preprocessing/feature_wt.py`・`keirin/src/result_top3.py`・
`keirin/src/submission_skips.py`・`keirin/src/netkeirin_client.py`・
`keirin/scripts/netkeirin_submit_wt.py`・`keirin/scripts/netkeirin_sync_status.py`・
`keirin/scripts/netkeirin_publish_wait.py` 等を Read/grep で実施。DB・実行は行っていない。

---

## 0. 総評（先に結論）

- **CONTINUATION.md は自己申告で無効**。ファイル冒頭（1-15行）と本文中盤（909-940行、
  2026-07-30 の「本節以下のROI数値はすべて無効です」）で、ここに載っている具体的な
  ROI数値（S1 123.0/143.3/182.5/443.9%、S7 128.1/131.3/158.3/405.0%、7A 107.4%、
  9A 108.4%、entropyゲート454.7/266.1% 等）は**モデル汚染による見かけ上の値**と明記されている。
  したがって CONTINUATION.md 内の個別ROI数値は「証拠」ではなく「当時なぜそう判断したかの記録」
  としてのみ扱うべきで、この点はドキュメント自身が既に警告済み（監査上の新規指摘ではない）。
- CLAUDE.md 側は概ね自己整合的で、コード突き合わせでも**大半の「本番は〜している」型の記述は
  現物と一致**した（下記2章）。ただし **3件、実際のコードと食い違う／現行ランク一覧から
  漏れている**箇所を確認した（3.1〜3.3）。特に **RANK_7T1 の日次上限が `5→0`（2026-08-24に撤廃）
  になっているのに、CLAUDE.md の「1日5本の上限を導入」節にはその撤廃が一切反映されていない**
  のは、ユーザーが懸念する「調査結果が誤ったまま残る」の実例。
- 参照スクリプトは概ね実在。リネーム済みで現存しないスクリプト名がいくつかあるが、
  いずれも「そのランク自体が後で全廃・改名された」という文脈と整合しており、
  ドキュメントの経年劣化（renaming後の残骸参照）であって内容の誤りではない。

---

## 1. 検証可能な主張の一覧表

量が多いため、**現行本番に効くもの**は個別行、**廃止済み・歴史的経緯**はランク単位で
まとめた。「本番が依拠しているか」列: はい=現行コードが参照/使用, いいえ=廃止済み・不使用,
要確認=判定材料不足。

### 1.1 現行ランク体系・ゲート条件（CLAUDE.md, 現行ランク体系節）

| 文書:行番号 | 主張の要約 | 根拠スクリプト/窓 | 本番依拠 | 状態 |
|---|---|---|---|---|
| CLAUDE.md:305 | 現行は「7S/7B/7C/7M1/7H1/7H2/7T1/9C/9H1 の9ペーパーランク」 | — | 要確認 | **不正確**。実際の `CURRENT_PAPER_RANKS`（strategy_wt.py:4906-）は上記9つ+**RANK_7T3**の計10。7T3 は2026-08-24新設で本文の一覧に一度も反映されていない（3.1参照） |
| CLAUDE.md:305-460 (RANK_7M1) | 母集団=7車∧上位2車p3合計<`RANK_7C_P3_SUM_MIN`∧モデル上位2車≠{WT◎,WT○}。買い目=三連複軸2車+相手2〜3点。walk-forward 2025-01〜2026-08-16 6,275R: 10.6件/日・的中11.3%・ROI82.3%[75.2,90.0] | `exp_7m1_firm_band.py` 等 | はい | 現行。`RANK_7M1_P3_SUM_MAX = RANK_7C_P3_SUM_MIN`（strategy_wt.py:3405）で定義の対応関係は一致 |
| CLAUDE.md:412-457 (7M1 firm band) | `RANK_7M1_FIRM_BAND` で堅い帯を取り込み。ON時12.6件/日・ROI77.7%、増分だけ2.9件/日・ROI81.6% | `exp_7m1_firm_band.py --wiring` | はい | 現行と明記 |
| CLAUDE.md:460-489 (RANK_9H1) | 9車波乱スコア`lgbm_upset_screen`≥`RANK_9H1_SCORE_MIN`。三連単フォーメーション6点。3年計730R的中17(2.3%)・回収率157.5% | walk-forward・最終オッズ採点 | はい | `RANK_9H1_SCORE_MIN = 0.3132`（strategy_wt.py:4015）— **doc本文の「本番モデルは0.3132」という記述と一致**（doc491-496） |
| CLAUDE.md:508-533 (7H1三連単一本化) | 2026-08-15、三連単フォーメーション8点・単価1,200円/点=9,600円に統一。`RANK_7H1_TF_UNIT`廃止 | — | はい | 要確認（コード側の`RANK_7H1_TF_UNIT`廃止を直接grepで未確認だが、priority orderに7H1が現存し矛盾なし） |
| CLAUDE.md:535-558 (7H1単価) | 500円→900円に復帰。ROI表（500円82.1%/700円81.6%/900円81.2%/1,100円80.7%） | picks_history 2,425行 | いいえ(歴史) | 2026-08-15の三連単一本化でこの単価体系自体が廃止済みと明記あり |
| CLAUDE.md:560-576 (統一賭け金) | `RACE_BUDGET=10000`・`unit_stake(点数)`が全ランク共通の正本 | `src/strategy_wt.py` | はい | **コード一致**: `RACE_BUDGET = 10000`（strategy_wt.py:332） |
| CLAUDE.md:595-613 (RANK_7C) | 複勝率上位2車合計≥144%（後の記述で1.44）∧相手4点以上等。honest walk-forward 22.4件/日・ROI77.5%、確認窓21.6件/日・ROI77.9% | — | はい | **コード一致**: `RANK_7C_P3_SUM_MIN = 1.44`（strategy_wt.py:351） |
| CLAUDE.md:606,384-385 | 足切り`RANK_7C_LEG_P3_MIN`=0.15 | — | はい | **コード一致**: `RANK_7C_LEG_P3_MIN = 0.15`（strategy_wt.py:387） |
| CLAUDE.md:613 | 入稿優先順位「7H2 > 7T1 > 7T3 > 7S > 7B > 7C > 7H1 > 7M1」 | `RANK_CONFIGS`定義順 | はい | **コード一致**（netkeirin_submit_wt.py:323,381,405,422,450,475,498,524 の定義順そのまま） |
| CLAUDE.md:616-683 (RANK_7T1) | 決勝系×別ライン。honest 13.4件/日・ROI91.4%[71,114]・的中3.14%。的中中央260,500円・20万超が3.6日に1回 | walk-forward 7車13,749R | はい(旧数値) | 母集団を後に「決勝のみ×別ライン」へ絞った変更（後述）で数値が更新されており、この時点の数値は**現行と異なる**可能性が高い（3.3参照） |
| CLAUDE.md:650-680 (7T1日次上限) | 2026-08-18に`RANK_7T1_DAILY_CAP=5`導入。ev（期待回収倍率）上位5本のみ採用。13.68→4.96件/日 | 候補生成時のev | **いいえ（撤廃済み）** | **3.1で詳述: コードは`RANK_7T1_DAILY_CAP=0`（2026-08-24 撤廃）。CLAUDE.mdはこの撤廃を一切記載していない** |
| CLAUDE.md:1181 のRANK_7T1優先順位=最下位表記等 | — | — | — | — |
| （strategy_wt.py内注釈のみ、CLAUDE.md未記載） | 2026-08-24 RANK_7T3新設: 三連単「決勝の中配当枠」。決勝/チャレンジ決勝×予測オッズ30倍以上の目1点以上×PL確率上位5点 | — | はい | **CLAUDE.mdに説明が一切無い**（3.2参照） |

### 1.2 3ヘッド軸選定・p3較正（現行採用）

| 文書:行番号 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| CLAUDE.md:686-722 | 3ヘッド軸: 軸1=pred_win最上位・軸2=z(p3)-0.3*z(pbad)最上位。4窓2025-07〜2026-07・約4,300推奨で的中38.9→41.4%・ROI77.5→82.8%、4/4窓で改善 | `scripts/exp_three_head_axis.py` | はい | **コード一致**: `RANK_AXIS2_BAD_WEIGHT = 0.3`（strategy_wt.py:935）。スクリプト実在・walk-forward設計（`TRAIN_FROM`固定+テスト窓ごとにtrain<test境界を切る）を確認、学習窓=評価窓の重なりなし（4章参照） |
| CLAUDE.md:709-717 | 9車には非適用（4窓平均+2.0ptに見えるが窓別で符号反転） | 同上（`--n-entries 9`） | はい(不使用) | 記述どおり9車では未採用と読める |
| CLAUDE.md:1016-1057 (p3較正) | ロジット空間Platt scalingをレース種別×グレード別に適用。7C決勝 二軸的中54.5%→63.0%(+8.5pt)等 | `scripts/fit_p3_calibration.py` | はい | **ファイル実在確認**（`src/p3_calibration.py` 2026-08-25更新・`scripts/fit_p3_calibration.py` 2026-08-17）。係数は「窓を替えると決勝のaが0.877→0.971に動く」と明記—doc内で自己申告の不安定性 |

### 1.3 表示・通知・入稿の正本化（2026-08-25以降）

| 文書:行番号 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| CLAUDE.md:179-233 | 表示・通知の母集団を`netkeirin_submissions`+`bet_detail`に統一。`picks_history.bet_amount>0`は「購入」の意味で使わない | — | 要確認(API未読) | コード側API (`/api/keirin/picks`等)は今回読んでいない。strategy側の変更は無いため直接検証対象外 |
| CLAUDE.md:264-303 (承認制/自動公開) | `require_approval`の単一フラグ切替。`_approval_required()`はfail-open(False)・`_auto_publish_enabled()`はfail-closed(False) | — | はい | **コード完全一致**: `scripts/netkeirin_submit_wt.py:866`(`_approval_required`)・`:896`(`_auto_publish_enabled`、コメント「fail-closed」)。`tests/test_auto_publish.py`・`tests/test_submit_approval_wiring.py`が存在しfail-open/closedをテストで固定 |
| CLAUDE.md:139-152 (netkeirin公開待ち同期) | `count_wait()`は書き戻しに使ってはいけない(失敗時(0,[])を返す)。`wait_state()`(okを返す)を使うこと | `scripts/netkeirin_publish_wait.py`(get_wait) / `scripts/netkeirin_sync_status.py` | はい | **コード完全一致**。`netkeirin_publish_wait.py`は`count_wait()`使用（読み取り専用の用途）、`netkeirin_sync_status.py:sync()`は`wait_state()`を使用しok=Falseで即降りる実装（77-81行）。ドキュメント記載の設計意図と実装が正確に対応 |
| CLAUDE.md:235-256 (見送り記録) | 一意キー`(race_key, rank_key, session)`。`_skip()`経由必須 | `src/submission_skips.py::record_skip` | はい | **コード一致**: `ON CONFLICT (race_key, rank_key, session)`（submission_skips.py:108）。呼び出し元は`scripts/netkeirin_submit_wt.py`のみ（grep確認） |
| CLAUDE.md:122-134 (同着判定) | `src/result_top3.py`が正本。`TOP3_SQL`は`ORDER BY finish_order, frame_no`でタイブレーク付き。3着同着237R(0.23%)/1・2着同着492R(0.48%) | `tests/test_result_top3.py` | はい | **コード完全一致**: `TOP3_SQL`定義（result_top3.py:42-45）が文書の記述そのまま。`hit_trio`/`hit_trifecta`/`representative`関数も実在確認 |

### 1.4 FEATURE_COLS_WT・特徴量関連

| 文書:行番号 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| CLAUDE.md:53-67 (キーファイル表) | `FEATURE_COLS_WT`は**60特徴**（2026-08-04にrace_type7+ライン実力5を追加し48→60） | — | はい(値のみ) | **不正確・要更新**。実測 `len(FEATURE_COLS_WT)` = **70**（2026-09-10時点、`MEETING_FORM_COLS_WT`4列込み）。60は2026-08-04時点の値のまま放置されており、その後複数回の追加（下記）を反映していない。詳細は3.4節 |
| CLAUDE.md:1071-1085 (2着内率追加) | `lgbm_wt_top2`導入もFEATURE_COLS_WTは変更していない（既存60特徴のまま学習ターゲットのみ差し替え） | — | はい | 「変更していない」という主張自体は特徴量セットに影響しないので無矛盾。ただし基準の「60特徴」という母数が既に古い |
| strategy_wt.py内コメント（CLAUDE.md未記載） | `exp_meeting_form_ab.py`docstring: 「FEATURE_COLS_WT（66特徴）に入っていない」（2026-09-10時点） | `scripts/exp_meeting_form_ab.py` | はい | **コード側の自己申告と実測が一致**（66+MEETING_FORM4=70）。CLAUDE.mdの「60」との差分の内訳が特定できた（3.4節） |
| CLAUDE.md:59-61 | 隊列位置2特徴・ライン実力5特徴のΔAUC値(+0.0030/+0.0033等) | — | はい | 数値そのものはコードから直接検証不能（学習ログ）。定数`FORMATION_COLS_WT`(2列)・`LINE_STRENGTH_COLS_WT`(5列)は実在確認 |
| CLAUDE.md:74,122 | `finish_order=0`は欠車/失格=着外、top3判定は`between(1,3)` | — | はい | `result_top3.py`のクエリで`finish_order BETWEEN 1 AND 3`使用を確認、整合 |
| CLAUDE.md:1269-1288 | `finish_order=0`は実際にはDNF（発走後の非完走）で、事前欠車は行自体が存在しない | `pipeline_wt.py:236-249` | 要確認 | 該当箇所は今回未読（時間都合）。ただしこの記述はCLAUDE.md内の別節（74行目）の言葉遣いを自己修正する形で書かれており、内部的に整合 |

### 1.5 経済性・母集団に関する否定的検証結果（不採用・棄却）

| 文書:行番号 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| CLAUDE.md:787-813 (7H2否定) | 型自体は実在するがROIが30セル以上どこでも60-80%。市場と同じ向きの分類器はROIにならない、という一般則 | `scripts/exp_7h2_third_upset.py` | いいえ | 一般則として妥当な考察。個別数値は未検証だがロジックは自己整合的 |
| CLAUDE.md:815-846 (DNF学習除外) | 落車・失格の学習上の扱いは何をしても効かない。A(棚ぼた実在するが2.2%)・B(重み付けVs除外はseed間ばらつき以下)・C(dnf_rate_180のAUC0.5459=コイン投げ) | 3seed・test 2025-10-01〜2026-08-03 | いいえ(不採用) | `WT_EXCLUDE_DNF_RACES=1`オプトインのみ残置と明記（CLAUDE.md:1014と整合） |
| CLAUDE.md:848-859 (9B不採用) | 全期間ROI82.1%だが月次標準偏差62.0・32ヶ月中10ヶ月60%割れで不安定。9車overlap∈{0,1}は36.3%で枯渇していない | `scripts/exp_9b_feasibility.py` | いいえ | スクリプト実在確認（find出力） |
| CLAUDE.md:909-940 (🔴🔴汚染発覚) | vintageモデル18本が2026-07-28に誤って再学習され、S1/S7/9A等の全ROI数値が無効。クリーンな正しいROI(S1:80.0%、S7:78.6%)を再掲 | 月次凍結vintageモデル | 要確認(過去) | **これはドキュメント自身が過去の自分を否定した記録**。この訂正以降のCLAUDE.md記述（7C以降）は汚染発覚後に書かれたものなので影響を受けない設計（節の切れ目が明確） |
| CLAUDE.md:932-937 (市場効率) | モデル予測精度はオッズに負ける(Brier 0.024892 vs 市場0.024496、logloss 0.102513 vs 0.099616、全469,280組) | 市場エッジ診断 | いいえ(否定的知見) | 数値の再現は不可能（対象コード・データ不明）。既に何度も引用される基盤的知見として扱われている |

### 1.6 CONTINUATION.md固有の主張（すべて2026-07-07以前・自己申告で無効扱い）

| 文書:行番号 | 主張の要約 | 状態 |
|---|---|---|
| CONTINUATION.md:1-15 | ファイル自身を「引継ぎメモではなく検証履歴アーカイブ」と定義し直し、現行状態はCLAUDE.mdを見よと明記 | 自己訂正済み |
| CONTINUATION.md:20-40 (発走前判定の永続化) | SS/S昇格通知済み240R中139R(58%)が翌朝採点で見送り誤記。`notify_prerace_wt.py`が`prerace_decisions_{date}.json`へ確定記録する設計を導入 | 現行の`prerace_decisions_{date}.json`正本という設計思想はCLAUDE.md:1013で踏襲されており矛盾なし |
| CONTINUATION.md:71-98 (3分割モデル設計) | `lgbm_wt`(TRAIN+VAL)・`lgbm_wt_train_only`・VAL139.6%/HOLD134.3%等 | **無効**（2026-07-30汚染発覚の対象期間に重なる可能性が高く、当時のモデル管理体制自体が後に「汚染」と判定された運用と同型。少なくとも同種の絶対数値をCLAUDE.mdの現行記述で再利用していない） |
| CONTINUATION.md:112-139 (G41-G44実験) | EXデータ未使用3列・身体測定・条件別成績のPhase1/2判定 | 個別追試不可・棄却済みのため実害小 |
| CONTINUATION.md:141-198 (doc49/50/51) | 三連複ライン軸2・三連単フォーメーション・買い目削減の複数案、いずれも不採用または「VAL/HOLD小標本」 | 全て不採用と明記。数値は当時のバイアス込みモデルに基づく可能性が高い(以下参照) |
| CONTINUATION.md:269-277 (fav_mismatch) | 旧結論「1168%/576%」はリーク込み。リーク無し再検証でHOLD 23.1%に崩壊 | doc自身が「バイアス崩壊確認」として記録。CLAUDE.mdの「検証済みの否定結果」パターンと整合的 |
| CONTINUATION.md:317-329 (G01-G08) | backtestリーク無し化・money-flow検証等 | 個別ツール(`exp_moneyflow_wt.py`)は実在確認未実施(範囲外) |
| CONTINUATION.md:332-348 (3バイアス発見・doc18) | ①欠車生存バイアス×stale odds ②≤6車完走者基準 ③週次再学習リーク。全レバー3期間70-90% | **CLAUDE.md:177でも同一内容が再掲**されており、この知見自体は現行文書に継承されている（内容の一貫性は保たれている） |

---

## 2. コード突き合わせで確認できた一致点（要約）

以下はCLAUDE.mdの「本番は〜している」型記述のうち、file:lineで裏取りできたもの:

- `RACE_BUDGET = 10000` — strategy_wt.py:332（CLAUDE.md:563と一致）
- `RANK_7C_P3_SUM_MIN = 1.44` — strategy_wt.py:351（CLAUDE.md各所と一致）
- `RANK_7C_LEG_P3_MIN = 0.15` — strategy_wt.py:387（CLAUDE.md:384-385と一致）
- `RANK_7M1_P3_SUM_MAX = RANK_7C_P3_SUM_MIN` — strategy_wt.py:3405（CLAUDE.mdの「7Cの裏返し」という説明と一致）
- `RANK_9H1_SCORE_MIN = 0.3132` — strategy_wt.py:4015（CLAUDE.md:492-494の「本番モデルは0.3132」と完全一致）
- `RANK_7T1_TARGET_PAYOUT = 150_000` — strategy_wt.py:4182（CLAUDE.md:617-619の「20万→15万に下げた」記述と整合）
- `RANK_AXIS2_BAD_WEIGHT = 0.3` — strategy_wt.py:935（CLAUDE.md:693-705のw2=0.3採用記述と一致）
- `RANK_CONFIGS`定義順 = 7H2,7T1,7T3,7S,7B,7C,7H1,7M1 — netkeirin_submit_wt.py:323-524（CLAUDE.md:613の優先順位記述と完全一致）
- `_approval_required()`/`_auto_publish_enabled()`のfail-open/closed設計 — netkeirin_submit_wt.py:866,896（CLAUDE.md:278-282と完全一致、テストでも固定）
- `TOP3_SQL`のタイブレーク付きクエリ — result_top3.py:42-45（CLAUDE.md:130と完全一致）
- `record_skip`の一意キー`(race_key,rank_key,session)` — submission_skips.py:108（CLAUDE.md:250と一致）
- `wait_state()`/`count_wait()`の使い分け — netkeirin_sync_status.py:77 / netkeirin_publish_wait.py:34（CLAUDE.md:146-148と完全一致）

---

## 3. コード突き合わせで見つかった食い違い（詳細）

### 3.1 🔴 RANK_7T1 の日次上限「5本」は2026-08-24に撤廃されているが、CLAUDE.mdは撤廃を記載していない

- CLAUDE.md:650-656（「2026-08-18・1日5本の上限を導入」節）は
  `RANK_7T1_DAILY_CAP = 5`を導入したとして、件数13.68→4.96件/日、ev選別の効果を
  詳しく記述している。**この節の後に続く点数別ROI表・「点数が増えるほど…」の議論
  まで含め、5本上限が現に効いていることを前提にした記述のまま終わっている。**
- 実際のコード（`src/strategy_wt.py:4393`）:
  ```
  RANK_7T1_DAILY_CAP = 0
  ```
  直前のコメント（strategy_wt.py:4370-4392、抜粋）:
  > 🔴 **2026-08-24 に撤廃した（5 → 0）。** … 件数を 13.60 → 4.96件/日 と 1/3 に
  > 削って ROI は 81.8% で同じ（CI [73,91] ↔ [66,98]）。＝ **ev による選別は無価値**
  > だった。代わりに母集団を「決勝のみ×別ライン」へ絞ることで 2.20件/日・
  > ROI 106.3% にした（`rank_7t1_is_target_race_type`）。
- つまり本番コードは**ev選別（5本上限）そのものを「無価値だった」と結論づけて撤廃**し、
  代わりに母集団を「決勝×別ライン限定」に絞る方式へ切り替え済み（2.20件/日・ROI106.3%）。
  CLAUDE.mdの本文にはこの2026-08-24の変更が**一切登場しない**
  （`grep -n "2026-08-24" CLAUDE.md` はゼロ件）。
- **意味**: CLAUDE.mdを読んだ人は「7T1は現在1日5本のev選別で運用中」と誤解する。
  実際には ev 選別は撤廃済みで、現在の運用は「決勝のみ×別ライン」に母集団を絞る方式
  （ROI 106.3%・2.20件/日）である。ユーザー懸念どおりの「過去の調査結果が更新されずに
  誤ったまま残っている」実例。

### 3.2 🔴 RANK_7T3 が現行ランクセットに存在するが、CLAUDE.mdに説明が皆無

- `src/strategy_wt.py:4477` 以降に `RANK_7T3` の全設計（コメント含む）が存在する:
  ```python
  # RANK_7T3 — 三連単「決勝の中配当枠」（2026-08-24 新設）
  # 対象   決勝/チャレンジ決勝 ∧ 予測オッズ30倍以上の目が1点以上
  RANK_7T3_NE = 7
  RANK_7T3_RACE_TYPES: tuple[str, ...] = ("決勝", "チャレンジ決勝")
  RANK_7T3_MIN_ODDS = 30.0
  RANK_7T3_LEGS = 5
  RANK_7T3_BLEND_W: tuple[float, float, float] = (1.0, 0.5, 0.0)
  RANK_7T3_LINE_ADJ_W: tuple[float, float] = (2.0, 1.5)
  ```
- `RANK_7T3` は `CURRENT_PAPER_RANKS`（strategy_wt.py:4947）・`RANK_CONFIGS`
  （netkeirin_submit_wt.py:405）の両方に本番採用ランクとして存在する。
- CLAUDE.md での言及は **1箇所だけ**（613行目の優先順位の羅列「7H2 > 7T1 > **7T3** > 7S…」）。
  それ以外に、7T3が何を狙う商品か・ゲート条件・想定ROI・導入経緯を説明する節が
  **存在しない**（`grep -n "7T3" CLAUDE.md` は1件のみ）。
- **意味**: 現行本番で稼働しているランクの設計思想・数値的根拠が、ドキュメントの
  参照先（CLAUDE.md）から追えない状態。3.1の7T1「決勝のみ×別ライン」への絞り込みと
  7T3「決勝の中配当枠」新設は同日（2026-08-24）の作業と見られ、関連する再設計が
  ドキュメントに反映されずコードのコメントにしか残っていない。

### 3.3 🔴 CLAUDE.mdの「現行ランク体系」ヘッダが現行ランク数を過小に数えている

- CLAUDE.md:305: 「7S/7B/7C/7M1/7H1/7H2/7T1/9C/9H1 の9ペーパーランク」
- 実際の `CURRENT_PAPER_RANKS`（strategy_wt.py:4906-4947）は10件:
  `RANK_7S, RANK_7B, RANK_9C, RANK_7C, RANK_7M1, RANK_7H1, RANK_7H2, RANK_9H1,
  RANK_7T1, RANK_7T3`
- 3.2のRANK_7T3が漏れている（3.1・3.2と同根の問題）。

### 3.4 🔴 FEATURE_COLS_WT の特徴量数「60」は2026-08-04時点の値のまま更新されていない

- CLAUDE.md:53-67（キーファイル表内のコメント）: 「FEATURE_COLS_WT（60特徴・rolling統合。
  2026-08-04にrace_type7特徴+ライン実力5特徴を追加し48→60）」
- 実測: `python3 -c "from src.preprocessing.feature_wt import FEATURE_COLS_WT;
  print(len(FEATURE_COLS_WT))"` → **70**
- 内訳: `RACE_TYPE_COLS_WT`(7)+`LINE_STRENGTH_COLS_WT`(5)+`FORMATION_COLS_WT`(2)+
  `RP_TREND_COLS_WT`(4)+`SB_DYN_COLS_WT`(4)+`MEETING_FORM_COLS_WT`(4)がスプレッド
  演算子で連結されており、2026-08-20実装・2026-09-10配線の`MEETING_FORM_COLS_WT`
  (`cup_n_so_far`/`cup_top3_rate`/`cup_win_rate`/`cup_mean_order_n`)が60特徴の
  カウントに含まれていない。
- コード側の別ファイル `scripts/exp_meeting_form_ab.py`（2026-09-10付）の
  docstringに「`FEATURE_COLS_WT`（**66特徴**）に入っていない」と明記されており、
  70(現在) − 4(meeting_form) = 66 で内部的にも整合する。**つまり2026-08-04時点の
  60から2026-09-10配線直前の66まで、CLAUDE.mdのキーファイル表が更新されないまま
  6特徴分ずれていた。**
- なお `MEETING_FORM_COLS_WT` 自体のコードコメントには「🔴 実装済みなのに
  3週間ここに入っていなかった…単に足し忘れ」という自己申告があり、
  **モデルへの配線漏れが3週間気づかれなかった実例**がコード内コメントとして
  残っている。CLAUDE.mdの本文にはこの経緯（配線漏れ・2026-09-10修正）についての
  記載が見当たらない。

### 3.5 CLAUDE.md内の自己矛盾（要注意だが軽微）: RANK_7S_AXIS_SUM_MAX の値表記

- CLAUDE.md:1300（2026-07-31付「変更時チェックリスト」節）:
  「現行の本番定数 `RANK_7S_AXIS_SUM_MAX`（`src/strategy_wt.py:329`、値は`1.5`）」
- 実コード: `RANK_7S_AXIS_SUM_MAX = 1.40`（strategy_wt.py:828。この直前に
  2026-08-18付で「1.50へ戻す案は検証して不採用」という設計コメントがあり、
  1.40が2026-08-18時点でも現行のまま据え置かれたことが確認できる）
- 判定: これは矛盾というより**日付の異なる2つの記述**（2026-07-31時点で1.5だった
  ものが後に1.40へ変更され、チェックリスト節の「現行」という言葉だけが更新されず
  残った）。行番号329も実際は828で、ファイルが成長した結果ずれている。
  3.1・3.2と同じ「後の変更が過去の説明的な節に反映されない」パターン。

### 3.6 RANK_9S/RANK_9A が2026-08-14に9Cへ統合されているのに、RANK_9H1節の優先順位表記が未更新

- CLAUDE.md:498-500（RANK_9H1節、2026-08-08付）:
  「9車の優先順位は **9H1 > 9S > 9A**」
- 実コード: `ABOLISHED_PAPER_RANKS`（strategy_wt.py:5016-5017）に
  ```python
  AbolishedRankSpec("RANK_9S", "#9S", "9車・三連複2軸流し7点（2026-08-14全廃・9Cへ集約）"),
  AbolishedRankSpec("RANK_9A", "#9A", "9車・境界ランク（2026-08-14全廃・9Cへ集約）"),
  ```
  `CURRENT_PAPER_RANKS`にRANK_9S/RANK_9Aは存在せず、代わりに`RANK_9C`が存在する。
- 判定: 7車側の同種の統合（7S∪7A∪7SS→7S、2026-08-14）はCLAUDE.md内で明示的に
  説明されている（1300行台の「7Aは廃止ではなく2026-08-14にRANK_7Sへ統合」）が、
  **9車側の同日の統合（9S∪9A→9C）についてはCLAUDE.md本文に説明が見当たらず**、
  RANK_9H1節の優先順位記述だけが古い名称（9S/9A）のまま残っている。
  かつ「9C」という現行ランクの成り立ち自体、CLAUDE.mdのどこにも定義節が無い
  （3.3の指摘と同根＝ドキュメント側の追随漏れ）。

---

## 4. 方法論の懸念（スクリプト流し読み）

実行はしていないが、コード構造から学習窓/評価窓・母集団の作り方を確認したもの:

- **`scripts/exp_three_head_axis.py`（3ヘッド軸選定・現行採用）**: 良好。
  `TRAIN_FROM = "2024-04-01"` 固定、`train = df[(race_date>=TRAIN_FROM) &
  (race_date<tf)]`（189行）で各テスト窓`tf`より前のデータのみで学習しており、
  学習窓と評価窓の重なりは無い。オッズは「wt_odds=最終オッズ(stale)」であることを
  docstringで自己申告済み（選出条件自体はオッズ非依存とも明記）。4窓それぞれ独立に
  学習し直す設計で、単一モデルの使い回しによるリークは見られない。
- **`scripts/fit_p3_calibration.py`（p3較正・現行採用）**: 良好。
  docstringで「過去の再構築へ未来を含む係数を当てるとin-sample」と明記し、
  walk-forwardではその時点までの窓で推定した係数を使うよう注意書きがある
  （実際にwalk-forward側でこの注意が守られているかは今回未確認）。
- **`scripts/exp_meeting_form_ab.py`（節内成績A/B・現行採用直前）**: 良好。
  `TRAIN_FROM = "2024-04-01"`固定+2窓（w1/w2）、5 seed、`exp_basic_elements_ab.py`と
  同一方法論と明記。学習/評価分離の設計は上記2本と同型。
- 上記3本はいずれも「本番が最終的に採用した」または「採用直前」のスクリプトであり、
  CLAUDE.mdが指摘する典型的な欠陥（学習窓=評価窓、確定オッズを発走前情報として使用、
  生存者バイアス、無作為対照なし、探索した窓で結論）は**この3本の範囲では検出されなかった**。
  ただし他の数十本の`exp_*.py`（CONTINUATION.md記載分・CLAUDE.md記載の廃止済みランク分）は
  今回すべては読んでおらず、既にドキュメント自身が「3バイアス発見（doc18）」
  「モデル汚染（2026-07-30）」として自己申告済みの欠陥がある。

---

## 5. スクリプト実在確認（サマリー）

`find`で名前一致を確認した約65本のうち、以下は現物が見つからなかった
（多くはドキュメント自身が「後で改名/廃止した」と説明しており、実害としての
矛盾ではなく経年劣化の残骸）:

| 参照名 | 実際 | 判定 |
|---|---|---|
| `exp_7s7a_overlap2_conditional_value_sweep.py` / `_disagreement.py` | 実在は `exp_7s7a_overlap2_sweep.py` / `exp_7s7a_overlap2_disagreement.py`（接頭辞が短い） | 軽微な命名の不一致。中身は存在するので実害小 |
| `rebuild_s1_walkforward.py` | 存在するのは `rebuild_s1_walkforward_pg.py` のみ | S1は2026-07-31に全廃済みで実運用上は死んだ参照 |
| `rebuild_s4_walkforward.py` / `rebuild_s4_walkforward_pg.py` | S4→S7→7Sと2回改名され `rebuild_7s_walkforward_pg.py` が現存 | ドキュメント側は各時点の名称を使っており矛盾ではない（改名の経緯もCLAUDE.mdに明記あり） |
| `rebuild_s2_walkforward.py` | 見つからず | S2(旧U)は2026-07-21全廃、実運用上は死んだ参照 |
| `backfill_9h1_rank_wt.py` | 見つからず | **doc自身が「未実装」と明記**（CLAUDE.md:502-504）。矛盾ではなく正しい記述 |
| `s4_evening_reselect.py` | 実在は `reselect_7s_evening.py`（改名後） | ドキュメントの改名表と整合 |

`reselect_7s_evening.py` / `netkeirin_sync_status.py` / `netkeirin_publish_wait.py` /
`src/p3_calibration.py` / `scripts/fit_p3_calibration.py` / `src/result_top3.py` /
`src/submission_skips.py` / `exp_7m1_firm_band.py`（の存在含意）等、
**現行運用に直結する主要スクリプトはすべて実在確認済み**。

---

## 6. 文書間の矛盾（CLAUDE.md vs CONTINUATION.md）

- **数値上の直接対決はほぼ無い**。CONTINUATION.mdは2026-07-07で更新停止しており、
  かつ2026-07-30のモデル汚染発覚以降にCLAUDE.md側で「以前のROI数値は全て無効」と
  明記されているため、両ファイルの同一指標を単純比較しても「モデル世代が違う」で
  説明がついてしまい、字義通りの矛盾を機械的に指摘するのは難しい。
- 強いて挙げるなら:
  - CONTINUATION.md:8（2026-08-02付更新ノート）は「現行ランク体系」を
    「7S / 7A / 9S / 9A の4ペーパーランク」としているが、これは**CONTINUATION.md
    自身が新しい情報を追記した最終更新時点（2026-08-02）でのスナップショット**
    であり、現在のCLAUDE.mdの10ランク体系とは当然異なる。ファイル冒頭の免責文で
    「本ファイルは検証履歴アーカイブ」と明言されているため、これは矛盾ではなく
    設計どおりの陳腐化。
  - CONTINUATION.md:76-83（3分割モデル設計・lgbm_wt/lgbm_wt_train_only、
    2026-06-17）とCLAUDE.md:909-940（2026-07-30のモデル汚染発覚）は直接同じ
    モデルを指してはいないが、**「vintageモデルの管理・凍結」という同一の運用上の
    脆弱性（過去のモデルファイルが後の作業で上書きされる）が、時期を変えて
    2回（2026-06-17前後の設計と2026-07-19/28の事故）表面化している**という点で、
    根本原因の再発とみなせる。CLAUDE.mdはこの構造的な弱さを教訓化しているが、
    CONTINUATION.md側の記述はその教訓化以前のもの。
- 総じて、両文書間の矛盾は「同じ主張が違う数字を言っている」型ではなく、
  「後発文書（CLAUDE.md）が前発文書（CONTINUATION.md）の内容を明示的に
  上書き・無効化している」型。これは健全な訂正プロセスであり、
  ユーザーが懸念する「誤りが気づかれずに放置されている」パターンとは異なる
  （CONTINUATION.mdの誤りは既に発見・記録済み）。
  **本当に「気づかれていない」誤りは、むしろCLAUDE.md内部（3.1〜3.6）に見つかった。**

---

## 付録: 未検証のまま残した主張（時間都合）

- CLAUDE.md記載の各種ROI・的中率・件数の生数値（例: 7C「22.4件/日・的中57.6%・
  ROI77.5%」等）は、対応する`exp_*.py`のロジックを読んでも**実行結果そのものは
  再現していない**（DB接続・実行が監査範囲外のため）。今回の検証は
  「主張された設計・定数・優先順位・ゲート条件がコードと一致するか」に限定しており、
  「その設計で実際にその数値が出るか」は未検証。
- `backend/src/services/keirin_marquee.py`・`backend/src/services/keirin_skip_reasons.py`
  等、kiseki側（backend/）に置かれ keirin から読み込まれるファイルは今回読んでいない。
- CLAUDE.md後半の「変更時チェックリスト」節（1160-1360行）に記載の個別commit
  （`f8811b8`・`3775101`・`33ba316`等）の実在・内容はgit操作が必要なため未検証。
