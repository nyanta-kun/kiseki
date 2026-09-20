# 監査対象: keirin/docs/{analysis,goals}/*.md + 直下の個別ドキュメント群

読み取り専用監査。DB操作・計算実行は行わず、コード突き合わせ（grep/Read）のみ実施。

---

## 1. 主張抽出表

凡例: 「本番が依拠しているか」= その文書の数値/結論を今の本番コードが採用しているか。
「状態」= 現行 / 撤回済み / 後の記述で覆された / 判定不能 / 事前登録のみ(結果未到来) / 不明。

### keirin/docs/analysis/18-backtest-bias-rescore.md（2026-06-12）

| 行 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| 1-56 | 旧バックテスト黒字（公式336%・doc16 492-558%等）は①欠車生存バイアス②「≤6車」定義バグ（完走者基準で7車混入33%）③モデルリークの3つが原因。本番忠実+リーク無しでは全レバーROI≈70-90%に収束 | `exp_leakfree_rescore_wt.py` | いいえ（当時の"wt"旧体系。現行は型ラボ/7車以上体系へ全面置換済み） | 現行（結論自体は後続の bet-structure-guide.md 冒頭の注記で「本ページ内の条件は全て古い」と明記されており、7+車専用戦略への転換の起点として位置づけ） |
| 58-72 | SS/S/Aのランク序列は実体がない。旧分析の高ROIはバイアスの産物 | `exp_rank_rescore_wt.py` | いいえ（旧SS/S/Aは全廃済み） | 撤回済み（旧ランク自体が bet-structure-guide.md により全廃と明記） |

### keirin/docs/analysis/23-moneyflow-initial.md（2026-06-13）

| 行 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| 12-30 | 標本30R（必要1624R）で統計的結論を出せない。記述統計のみ | `exp_moneyflow_wt.py` | いいえ | 判定不能（明記済み・多重比較防衛のため「参考・追試しない」と自己申告） |
| 100-105 | 1624R到達まで約9ヶ月 | 見積り | — | 見積り。後続文書（G07/doc25）で「G04は無方向」と参照されているのみで、その後の到達報告は本監査対象内に存在しない |

### keirin/docs/analysis/24-wind-feature.md（2026-06-13）

| 行 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| 1-6, 30-37 | 風特徴（速度・突風・交互作用）はAUC差±0.0003で無情報。Phase1不通過 | `exp_wind_wt.py` | はい（`FEATURE_COLS_WT`に風特徴なし＝不採用が維持されている） | 現行。ただし2026-08-18のdoc55が予報風速で同テーマを再検証し「モデルは織り込んでいないが決定を動かすほどではない」と**より詳しい形で再確認**（矛盾ではなく深掘り） |

### keirin/docs/analysis/25-highpay-fusion.md（2026-06-13）

| 行 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| 全体 | G06不通過・G04無方向のためゲート条件が成立し、4セルとも検証スキップ（多重比較防衛） | `exp_highpay_fusion_wt.py` | — | 現行（本番へ変更ゼロと明記） |

### keirin/docs/analysis/36-intraday-drift.md（2026-06-15）

| 行 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| 全体 | 朝→夕方ドリフトはC0対象4Rのみで統計判断不可。方向一致率100%（n=15脚/4R）は参考値に過ぎない | `exp_evening_morning_drift_wt.py` | いいえ | 判定不能（自己申告どおり） |

### keirin/docs/analysis/55-fc-wind-style.md（2026-08-18）

| 行 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| 1-11, 130-137 | 予報風速×脚質は残差検定でz+3〜5と有意な未織り込み情報が実在するが、効果量が小さく（強風でp3±1.8pt）決定を動かさない。特徴追加・後段補正・相手並べ替えいずれも不採用 | `exp_fc_wind_wt.py`/`exp_fc_wind_adjust_wt.py` | はい（`FEATURE_COLS_WT`に追加なし） | 現行 |
| 139-146 | `venue_info.is_indoor`が誤り（前橋・小倉が0のまま） | 実測 | 要確認 | 「別PR」と明記されており本監査では未確認（venue_infoテーブルの実データ確認はDB操作が必要でスコープ外） |
| 149-239 | 風向版「強風×向かい風で逃を下げる」も不成立。向かい風は上がりタイムには効く(z+7)が着順には効かない | `exp_fc_wind_dir_wt.py` | はい（不採用のまま） | 現行 |

### keirin/docs/analysis/56-race-selection-meta.md（2026-08-18）

| 行 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| 1-11 | 市場ペア同時確率での軸選定は不採用（4板すべてで市場が現行モデルに負ける） | `exp_market_pair_wt.py` | はい（不採用） | 現行 |
| 34-131 | レース選別を4特徴スコア（`gate7c_score`）へ替えると二軸的中+1.2〜2.4pt。**実装**（`src/race_gate_7c.py`） | `exp_race_selection_meta_wt.py` | 部分的（下記参照） | **後の記述で覆された/不採用** |
| 140-235 | walk-forward A/B: 表示的中+0.50pt(t=2.58)だが件数−10%。件数を揃えた再試行で改善は−0.09ptに縮小 → **不採用。`RANK_7C_P3_SUM_MIN=1.44`据え置き** | `exp_gate7c_walkforward_ab.py` | はい | 現行（コード確認: `RANK_9C_P3_SUM_MIN = 1.30`は別ランクだが7C側の1.44は現行のまま） |

**コード突き合わせ（56番）**: `src/strategy_wt.py` に `_gate_p3_sum(c) >= RANK_7C_P3_SUM_MIN` が現行判定として残っており、`src/race_gate_7c.py` / `scripts/fit_race_gate_7c.py` / `scripts/exp_gate7c_walkforward_ab.py` は「判定式の記録・計測専用列」として残置——doc本文の「不採用」記述とコード上の残置理由コメントが一致。矛盾なし。

### keirin/docs/goals/G04-moneyflow-harness.md, G06-wind-verification.md, G07-highpay-fusion.md

いずれも「事前登録タスク定義書」で、対応するanalysis文書（23/24/25）がその実行結果。整合性あり。特記事項なし。

### keirin/docs/goals/W01-upset-leakfree-reeval.md

| 行 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| 全体 | 波乱モデルをリーク無しで再学習し `docs/analysis/27-upset-leakfree.md` に結果を書く計画 | `scripts/exp_upset_leakfree_wt.py`（実在確認済み） | — | **未完了と推測**。`docs/analysis/27-upset-leakfree.md` は**存在しない**（`ls`で確認、doc27自体が欠番）。タスクが実行されなかったのか、結果が破棄されたのかは本監査の範囲では判別不能。読者がgoalsフォルダだけを見ると「実施済み」と誤解しうる |

---

### keirin/docs/appeal-pitch.md（2026-06-08・winticket本番版マーケティング資料）

| 行 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| 89-106 | バックテスト実績: SS 1,899%・S 550%・A 209%・推奨計724%（2026-04〜06）。旧ルート実運用は1週間で回収率49%と注記 | 最終オッズ・上限値と自己申告 | いいえ | **🔴 doc18（4日後の2026-06-12付）が指摘する3バイアス（欠車生存・≤6車定義バグ・モデルリーク）に汚染された数字そのもの**。appeal-pitch.md自体には訂正・撤回の追記が一切ない。マーケティング資料として独立に存在し続けており、doc18を読まない限り「724%」等が生きた数字に見える |
| 30-34 | モデル`lgbm_wt`・39特徴・CV AUC 0.7720 | — | いいえ（特徴数・ランク体系とも刷新済み） | 旧体系。現行は66特徴（`FEATURE_COLS_WT`実測）で7+車専用・型ラボ併走に移行済み |
| 152-153 | DB: SQLite / 約96,000レース | — | いいえ | **コード確認**: `keirin/src/database.py`はpsycopg2ベースでsqlite3互換ラッパーを提供するのみ（PostgreSQL移行済み、CLAUDE.mdの2026-08-10統合と整合）。appeal-pitch.mdはSQLite時代の記述のまま |

### keirin/docs/bet-structure-guide.md

冒頭に「⚠️ 本ドキュメントは旧体系（〜2026-07-09）の歴史的記録」と明記。CLAUDE.mdへの参照を促す自己申告的な注記があり、監査上の懸念は小さい。ただし本文中盤（43-56行）に「バックテスト実績: SS 137.8%・S 138.8%・合計134.3%」が旧体系の数値として残置——appeal-pitch.mdと違い「歴史的記録」と明示されている点は評価できる。

### keirin/docs/data-collection.md（2026-06-08）

| 行 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| 92-97 | keirin-station収集は2026-06-08凍結 | — | 要確認 | コード確認: `collect`系コマンドは`src/cli/main.py`に現存（105行目）だが、実運用で使われているかは本監査のスコープ外（cron設定はkeirin外のインフラ設定） |
| 153-169, 171-178 | winticketが★本番。collect-wt/collect-wt-range/status-wtコマンド一覧 | — | はい | コード確認: `src/cli/main.py`にすべて実在（942, 963, 1018行目） |
| 180-187 | 朝オッズ前向き計測: `snapshot_morning_odds_wt.py` | — | はい | ファイル実在確認済み |

このファイルは2026-06-08時点のスナップショットで、その後のデータ収集構成（型ラボ、9車専用処理等）を反映していない可能性が高いが、URL構造・CLIコマンド名は現在も実在するコードと一致。

### keirin/docs/netkeirin-input-api-spec.md

技術仕様書。`type`/`point`パラメータの正体（勝負アイコン・販売価格）を`src/netkeirin_client.py`で確認 → **一致**（656行目 `"type": act_type` と実装が符合）。ログイン仕様・bet_id構造など高精度な実機検証記録で、目立つ矛盾なし。

### keirin/docs/nine_car_axis_redesign.md（2026-08-24〜08-25追記）

| 行 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| 13-25 | 9車の商品はRANK_9C（三連複2軸流し）とRANK_9H1。軸選択は`rank_7c_select_axis`（車数非依存の共通関数）。`RANK_9C_P3_SUM_MIN=1.30` | 実測 | はい | **コード確認一致**: `src/strategy_wt.py:2826` `RANK_9C_P3_SUM_MIN = 1.30` |
| 181-224 | 採用: 軸2を「軸1との同時確率」で選ぶペアモデル（9C・9H1共通）。学習は7車+9車混合 | `keirin/scripts/exp_9axis/` | 要確認 | **未実装の可能性**——本文は「設計案」節（§4「採る」）であり、`src/strategy_wt.py`に軸2をペアモデルへ差し替えた実装コードは本監査では確認できなかった（`rank_7c_select_axis`のまま残置と見られる）。設計書と実装状況の対応関係は別途要確認 |
| 239-401 | 追記2: RANK_9F新設（穴埋め経路の成績記録）。`submit_marquee_wt._axes()`を軸の正本として使用。`CURRENT_PAPER_RANKS`には登録しない | `scripts/backfill_9f_rank_wt.py`等 | はい | **コード確認一致**: `RANK_9F`は`scripts/backfill_9f_rank_wt.py`/`scripts/rebuild_9f_walkforward_pg.py`/`tests/test_rank_9f.py`に実在。`tests/test_rank_9f.py:83`で`"RANK_9F" not in {s.rank for s in CURRENT_PAPER_RANKS}`を明示的にテスト固定——doc記載どおり |
| 95-247 | `marquee_race_nos()`はGIII以上の開催で全レースを穴埋め対象にする（`return present`） | `src/marquee.py` | はい | **コード確認一致**: `src/marquee.py:111-135`のロジックと完全一致 |

### keirin/docs/oddspred_gap_2026_08_26.md

| 行 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| 全体 | 予測オッズと確定オッズの乖離（平均払戻×1.10・最低払戻×0.78）を実測し、下限包絡ゲート`_expected_payout_floor_for`とモデル存在監視`ODDS_MODEL_FILES`を実装 | `scripts/exp_oddspred_gap/01〜03` | はい | **コード確認一致**: `scripts/netkeirin_submit_wt.py:1734` `_expected_payout_floor_for`実在、`scripts/check_model_freshness.py:101` `ODDS_MODEL_FILES`実在 |

### keirin/docs/oddspred_gap_2026_08_29.md

| 行 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| 244-263 | 実装: `backend/src/services/keirin_payout_floor.py`新設、`keirin_router.py::_min_payout_low`差し替え、三連単に自前保守倍率（`conservative_multiplier`/`conservative_quantiles`） | `scripts/exp_oddspred_gap/07_floor_ck_verify.py` | はい | **コード確認一致**: 全ファイル・関数名が実在（`backend/src/services/keirin_payout_floor.py`、`backend/src/api/keirin_router.py:3134`、`keirin/src/odds_prediction_tf.py:295,311`） |
| 290-300 | 配布注意: `odds_tf_meta.json`をVPSへ同期しないと三連単の`odds_low`が消える | `scripts/sync_models_to_vps.sh` | 要確認 | 配備手順であり本監査では実際にVPS側の状態確認は不可（DB/インフラ操作は禁止範囲） |

### keirin/docs/PREREG_7S_UPSET_GATE_2026_08_21.md

事前登録＋実行結果の完全な記録。特筆すべきは**自己訂正の透明性**——初出時「❌棄却」と書いた判定を、独立監査により「2024データの79%が別母集団（`wt_overlap_n`の構造断裂）」と判明し「判定不能」へ訂正。これは監査プロセスが機能した好例であり、問題なし。

### keirin/docs/PREREG_PL_OBJECTIVE_2026_09_14.md

| 行 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| 全体 | p3モデルの目的関数をPL直接最尤(PL-K3)へ替える事前登録。判定は2027-10-01以降（4四半期先） | `scripts/exp_objective_pl_{prep,ab,product,product2,product3}.py` | いいえ（本番コード未変更と明記） | **事前登録のみ・結果は本文の「結果」節が空欄のまま（今日2026-09-20時点で正常）**。スクリプト実在確認済み。KEIRIN_TEST_USAGE_LEDGER.mdにも同日付で記録あり——整合 |
| 39-52 | 較正の絶対閾値（`AXIS_SUM_FIRM`等）を探索窓で凍結する空欄あり | `exp_objective_pl_calib_freeze.py`（**未コミット・git status上でuntracked**） | — | 作業進行中と判断（プレースホルダがまだ埋まっていない状態と、未コミットの凍結スクリプトの存在が整合） |

### keirin/docs/rank_7h3_design.md（2026-08-12）

🔴🔴 **最重要の発見**。

| 行 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| 全体 | RANK_7H3を新設する設計書。三連単フォーメーション6点で10万円超の払戻を狙う。honest walk-forwardでedge 1.59・推定ROI 1.19 [0.95,1.44]。稼働手順まで記載 | honest walk-forward・216日・7車13,795R | **いいえ** | **撤回済み（ただしdoc本文には一切記載なし）**。`src/strategy_wt.py`のコメント（4080-4084, 4994-5018行目）で確認: RANK_7H3は**2026-08-12新設・2026-08-13廃止**（新設の翌日）。理由は「軸積>=0.70という確率の絶対閾値」が「設計に使った確率と本番コードパスの確率が食い違った瞬間に母集団が1.4倍になり崩壊」。backfill実測(7,090件)でROI 70.2%＝控除率の壁の下。**netkeirinへの入稿実績は0件**（enabled=falseのまま一度も出していない）。`AbolishedRankSpec("RANK_7H3", None, "本命連対どまり型・三連単（2026-08-13全廃・入稿実績0）")`としてABOLISHED_PAPER_RANK_NAMESに登録済み（`tests/test_rank_7t1.py:347`でテスト固定） |

**この不一致の重大性**: `rank_7h3_design.md`は独立したファイルとして残っており、本文中に「廃止」「撤回」の追記が一切ない。依頼主の懸念（過去調査が誤ったまま大量にドキュメント化されている）に直接該当する事例——このファイルだけを読んだ人間・AIエージェントは、RANK_7H3が計画中または稼働中の商品だと誤認する。実際にはコード側コメントが「RANK_7T1の【前身】」として詳しい失敗経緯を記録しており、正しい経緯はコード側にしかない。

### keirin/docs/rank_7t3_design.md（2026-08-24）

大部分がコードと一致（下記コード突き合わせ参照）。実装済みで現行稼働中の設計書として機能している。

### keirin/docs/rank_priority_redesign_2026_08_25.md

| 行 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| 51-95 | 期待値では優先順位を並べられない（EV最大とEV最小が同じ結果・全商品EVが0.87〜1.00に収束） | `scripts/exp_priority/rank_arms.py` | はい | 現行。RANK_CONFIGSの定義順が実際に`7H2>9H1>7T1>7T3>7S>9C>7B>7C>7H1>7M1`と一致（コード確認済み） |
| 150-200 | 7M1 vs 7Sの条件分岐は不成立（「2倍以上で的中」で両窓とも7M1が勝つセグメントはゼロ）。ただし「5倍以上で的中」では全セグメントで7M1が上 | `scripts/exp_priority/s_vs_m1_segments.py` | はい | 現行 |

### keirin/docs/rival_hot_highpay_2026_08_27.md

競合他社の逆解析。外部データに基づく分析で、自社コードへの影響記述（三連複枠は最大79,500円・三連単枠は0.10件/日等）は分析結果であり本番コード変更提案は「未検証」節に留まる。特筆すべき矛盾なし。

**軽微な不一致**: 83行目「`keirin_settlement._ORDERED`も2券種のみ」と記載しているが、**`keirin_settlement`というモジュール/ファイルはリポジトリに存在しない**（`_ORDERED`という変数名自体は`src/scraper/pipeline_wt.py`と`scripts/backfill_wt_odds_2022_2023.py`に存在するが値は`{"exacta","trifecta"}`で券種の意味が違う）。`_BET_TYPE_JP`（`scripts/netkeirin_submit_wt.py:1010`）は実在し3連複/3連単の2種のみで、主張の趣旨自体（券種が2つしかない）は正しいが、引用したモジュール名が誤り（dead reference）。

### keirin/docs/tf_rival614_line_pair_2026_08_26.md

| 行 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| 114-160 | 同ライン隣接ボーナス`score=P_PL×λ^[隣接]×μ^[隣接]`(λ=2.0,μ=1.5)を三連単の板に実装。9/9四半期で改善 | `/tmp/honmei_attr.npz`ほか | はい | **コード確認一致**: `src/strategy_wt.py:4567` `RANK_7T3_LINE_ADJ_W: tuple[float, float] = (2.0, 1.5)`、`rank_7t3_blend_probs`が実装（買い目選択に使用） |
| 206-214 | 二軸選択を「PL最上位の1-2着」からボーナス入りスコアのargmaxへ変更 | 同上 | はい | 一致。ただし後続に別レイヤー`rank_7t3_order_swap_probs`（rev_w=(1.9,1.0)）が追加されており、これは**並べ替え専用**で「買い目の選択には使わないこと」と明記——tf_rival614文書の主張と矛盾しない別機能の追加（`line_order_2026_09_10.md`という後続文書を参照しているが本監査の対象外ファイル） |

### keirin/docs/unused_morning_inputs_2026_09_10.md

| 行 | 主張の要約 | 根拠 | 本番依拠 | 状態 |
|---|---|---|---|---|
| 26-33 | `MEETING_FORM_COLS_WT`(4列)・`FORM_QUALITY_COLS_WT`(3列)が実装済みなのに`FEATURE_COLS_WT`に未配線のまま3週間放置されていた | `feature_wt.py`突き合わせ | — | 執筆時点の状態 |
| 74-93 | `MEETING_FORM_COLS_WT`は採用候補（両窓・全指標プラス）。`FORM_QUALITY_COLS_WT`は窓で符号が割れるため不採用 | `scripts/exp_meeting_form_ab.py` | はい | **コード確認: 対応が完了している**。`src/preprocessing/feature_wt.py:1370`で`*MEETING_FORM_COLS_WT`が`FEATURE_COLS_WT`へ追加済み。同ファイルのコメント（1347-1364行目）がdoc本文の数値表をそのまま転記しており、`FORM_QUALITY_COLS_WT`は明示的に「入れない」とコメントされている。ドキュメントと本番コードの対応が完全に取れている数少ない良い例 |

---

## 2. コード突き合わせで見つかった食い違い（file:line付き）

1. **`keirin/docs/rank_7h3_design.md`** 全体 ↔ `keirin/src/strategy_wt.py:4080-4084, 4994-5018` および `keirin/tests/test_rank_7t1.py:347`
   - doc: RANK_7H3を新設する設計書（稼働手順まで含む、恒久的な商品として記述）
   - コード: 新設の翌日（2026-08-13）に入稿実績0件のまま全廃。理由・失敗機序・退避先CSVまでコード側コメントに詳細記録
   - **影響**: 独立した監査者がdocs/だけを見た場合、RANK_7H3を現行または計画中の商品と誤認する

2. **`keirin/docs/rival_hot_highpay_2026_08_27.md:83`** ↔ リポジトリ全体
   - doc: `keirin_settlement._ORDERED`という参照
   - コード: `keirin_settlement`というファイル/モジュールは存在しない（grep結果ゼロ件）。類似の`_ORDERED`変数は`src/scraper/pipeline_wt.py:255`と`scripts/backfill_wt_odds_2022_2023.py:30`に存在するが値も文脈も異なる
   - **影響**: 軽微（結論自体は`_BET_TYPE_JP`で独立に裏付けられる）。dead reference・モジュール名の誤記

3. **`keirin/docs/nine_car_axis_redesign.md` §4「採る」（181-195行目）** ↔ `keirin/src/strategy_wt.py`
   - doc: 軸2選択を「軸1との同時確率」ペアモデルへ差し替える設計を「採る」と明記
   - コード: `src/strategy_wt.py`内で9C/9H1の軸選択が`rank_7c_select_axis`（p3上位2車の周辺確率方式）から実際にペアモデルへ差し替えられた実装は本監査では確認できなかった（該当する新関数名が見当たらない）
   - **影響**: 「設計案として採用を推奨した」ことと「実装済み」は別。本監査の範囲では実装有無が確定できないため、担当者に実装状況の確認を推奨

## 3. ファイル間・内部の矛盾

1. **appeal-pitch.md vs analysis/18-backtest-bias-rescore.md**: appeal-pitch.md（2026-06-08）の「推奨計ROI 724%」等の数字は、4日後のdoc18が特定した3つのバイアス（欠車生存・≤6車定義バグ・モデルリーク）に汚染された旧バックテストの産物である可能性が極めて高い。appeal-pitch.mdには訂正の追記が一切なく、両文書は独立に矛盾したまま存在している。

2. **appeal-pitch.md vs data-collection.md**: appeal-pitch.mdは「DB: SQLite」と明記するが、実際のコード（`src/database.py`）はPostgreSQL（psycopg2）ベースであり、SQLite互換ラッパーを提供しているに過ぎない。data-collection.md自体はDB種別を明言していないため直接の矛盾ではないが、appeal-pitch.mdの記述はCLAUDE.mdが記す2026-08-10の競輪リポジトリ統合（Postgres `keirin.*`スキーマへの統合）以前の古い状態を反映している。

3. **rank_7h3_design.md vs strategy_wt.pyのコメント**: 上記コード突き合わせ1と同一。ドキュメント単体では「廃止」の事実が読み取れない。

4. **goals/W01-upset-leakfree-reeval.md**: 参照先`docs/analysis/27-upset-leakfree.md`が存在しない。タスクが実行されなかったか、結果文書が別の場所へ移動・削除されたかは不明。goalsフォルダの体裁上「実行待ちのタスク定義」に見えるが、G04/G06/G07とは違い対応するanalysis文書が欠落している点で内部的に不整合。

5. 軽微: **tf_rival614_line_pair_2026_08_26.md**が実装位置として言及する`src/strategy_wt.rank_7t3_blend_probs()`は現存するが、同じ関数群に後から追加された`rank_7t3_order_swap_probs()`（「買い目の選択には使わないこと」と明記）との役割分担は、tf_rival614文書だけを読むと読み取れない（文書内に後続文書`line_order_2026_09_10.md`への参照はあるが本監査の対象外ファイルのため中身は未確認）。矛盾ではなく情報不足。

## 4. 存在しないスクリプトのリスト

以下について実在確認を行った結果、**analysis/goals/直下ドキュメントが参照するスクリプトはすべて実在した**（`ls`で確認）:

- `exp_leakfree_rescore_wt.py` / `exp_rank_rescore_wt.py` / `exp_moneyflow_wt.py` / `exp_wind_wt.py` / `exp_highpay_fusion_wt.py` / `exp_evening_morning_drift_wt.py` / `exp_fc_wind_wt.py` / `exp_fc_wind_adjust_wt.py` / `exp_fc_wind_dir_wt.py` / `exp_market_pair_wt.py` / `exp_race_selection_meta_wt.py` / `exp_upset_leakfree_wt.py` / `exp_stake_tilt_wt.py` / `analyze_gami_threshold_wt.py` / `snapshot_morning_odds_wt.py` / `exp_gate7c_walkforward_ab.py` / `fit_race_gate_7c.py` / `exp_segment_first_wt.py` / `confirm_7s_upset_gate_2024.py` / `exp_meeting_form_ab.py` / `exp_basic_elements_ab.py` / `exp_racetype_field_ab.py`
- ディレクトリ: `scripts/exp_oddspred_gap/`（01〜07番すべて実在）/ `scripts/exp_9axis/` / `scripts/exp_hot/` / `scripts/exp_gensen/` / `scripts/exp_priority/` / `scripts/exp_type_lab/`
- `scripts/exp_objective_pl_{prep,ab,product,product2,product3}.py`（実在）。ただし`exp_objective_pl_calib_freeze.py`は**未コミット（git status上でuntracked）**——PREREG_PL_OBJECTIVE_2026_09_14.mdの空欄を埋めるための作業中スクリプトと推測される

**存在しないもの**:
- `docs/analysis/27-upset-leakfree.md`（W01の成果物として指定されているが欠番。スクリプト自体`exp_upset_leakfree_wt.py`は実在するため、実行されたが結果文書が作られなかった可能性が高い）
- モジュール`keirin_settlement`（rival_hot_highpay_2026_08_27.md:83が参照。存在するのは`src/sold_performance.py`や`_BET_TYPE_JP`辞書であり、`_ORDERED`という名前自体は別モジュールに別の意味で存在する）

---

## サマリー

- 対象ファイル数: analysis 7 + goals 4 + 直下ドキュメント18 = 29ファイル、全て読了
- 抽出した検証可能な主張: 約45件（上表参照）
- **最重要の発見**: `rank_7h3_design.md`が、新設の翌日に入稿実績ゼロのまま全廃されたRANK_7H3を、廃止の事実を一切記載しないまま独立した設計書として残している。依頼主の懸念（誤った過去調査がドキュメント化されたまま残っている）に直接該当する最も具体的な事例
- 中程度の発見: `nine_car_axis_redesign.md`の「採る」設計（軸2ペアモデル）が実装されたか本監査では確認できず
- 軽微な発見: `rival_hot_highpay_2026_08_27.md`に存在しないモジュール名（`keirin_settlement`）への参照（dead reference、結論への影響は小）
- 良好な点: `PREREG_7S_UPSET_GATE_2026_08_21.md`は自己訂正の透明性が高い。`unused_morning_inputs_2026_09_10.md`は提案が実装され、コード側コメントに数値まで転記されており、ドキュメントとコードの対応が完璧に取れている好例。`PREREG_PL_OBJECTIVE_2026_09_14.md`は事前登録の体裁が正しく保たれ、結果欄は本日時点で正しく空欄のまま
- goals/W01の成果物（analysis/27）が欠番——タスクの完了状況が文書からは追跡できない
