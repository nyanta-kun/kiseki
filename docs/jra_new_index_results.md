# JRA新指数開発 - 施策リストと結果まとめ

中央競馬(JRA)向けに開発・検証した新指数施策の一覧と結果。**有用性が確認できたものは地方競馬(chihou)・競輪への横展開を検討する**ため、このドキュメントで随時追跡する。

背景・全体設計方針: `memory/jra_new_index_web_research_2026_07_26.md`（Web調査6分野の統合レポート）

## サマリー表

| Phase | 施策 | 検証方法 | 結果 | chihou/keirin展開 |
|---|---|---|---|---|
| 1 | 新規特徴量7種（下記詳細） | 単一hold-out (test: 2026-01〜04, n=1068レース) | ❌ 複勝的中率−0.57pt・単勝ROIほぼ横ばい。改善確認できず | 見送り（効果未確認のため展開しない） |
| 2 | Listwise Ranking(LambdaRank)への転換 | — | ⚠️ **既に本番実施済みと判明**（`v26_lightgbm_rank.txt`がデフォルト本番モデル）。新規性なし | 対象外（既存本番構成の再確認のみ） |
| 3 | 市場混戦度スコア(HHI/Shannon Entropy)による見送り判定強化 | 診断期間(2023-07〜2025-12, n=8,377)で探索→確認期間(2026-01〜07, n=1,905)で固定閾値のまま再現検証 | ✅ **既存tier方式への上乗せとして有効。確認期間で再現済み**（tier=C内をentropy_norm中央値分割で複勝的中率11〜13pt分離） | **本番実装済み**(2026-07-26デプロイ)。chihou/keirin展開は今後検討 |
| 4-A | オッズ時系列特徴（締切直前の下げ足） | 単一時系列2分割・予備検証 (探索656R/確認437R, n=1,093) | ⚠️ **信頼度低い参考値。本番実装せず。** 方向性(下げ馬の的中率高)は両期間一致もQ1-Q4差が13.1pt→7.5ptに半減、tier内追加分離はn≥100かつ同符号再現0件 | 見送り（odds_history蓄積(1年以上・数千レース)を待って再検証） |
| 4-B | 調教データ(坂路)特徴量 | 拡張窓(2025-07〜2026-07, train1924R/valid431R/test1200R)で複数seed(5)平均 | 🟡 **有望だが未確証。本番実装せず。** 複勝的中率は3窓(前回363R・今回541R・今回1200R)すべて一貫してプラス(+0.33〜2.37pt)だがseed std大・期間依存性残る。3年backfill未達成 | 保留（3年backfill実現後に再評価） |

## Phase 1: 新規特徴量7種 詳細結果

### 検証方法
- 既存v26モデル（34特徴量、LightGBM binary分類）に7特徴量を追加した拡張モデル(41特徴量)を学習し、同一のtrain(2023-05〜2025-06)/valid(2025-07〜2025-12)/test(2026-01〜2026-04)で比較
- 実装: `backend/scripts/train_v26_phase1_features.py`（研究用、DB書き込みなし）
- テスト: `backend/tests/test_v26_phase1_features.py`（23件、point-in-timeリーク検証含む、全pass）
- メトリクス: `backend/models/v26_phase1_metrics.json`

### 比較結果（test: 2026-01-01〜2026-04-30, n_races=1068）

| metric | baseline(34特徴) | extended(41特徴) | diff |
|---|---|---|---|
| top1_win_pct | 26.22% | 26.59% | +0.37pt |
| top1_place_pct | 60.96% | 60.39% | **−0.57pt** |
| top1_win_roi | 0.724 | 0.723 | −0.001 |

**結論: 複勝的中率・単勝ROIの改善に寄与しなかった。** 単勝的中率のみ微増したが、trainでの伸び(+0.56pt)より小さくtest/validでの改善幅は限定的（過学習気味の兆候）。

### 個別施策リスト（各特徴量の実装ロジックと結果）

| # | 施策名 | ロジック概要 | データソース | feature importance順位(41中) | 個別評価 |
|---|---|---|---|---|---|
| 1 | corner_stretch_regression | 直近3走の(4角通過順−着順)平均。終い伸び/failる検知 | JV-Link (passing_4, finish_position) | 16位 (gain 2009.4) | モデルには使われるが寄与度は中位以下 |
| 2 | bounce_score | 前走speed_index − 前3走平均。反動理論 | calculated_indices(v24) | 24位 (gain 973.1) | 下位。2023年前半はデータ制約で0埋め多数 |
| 3 | pace_index_pci | 前後半ペースバランス連続値化 | JV-Link (finish_time, last_3f, distance) | 9位 (gain 3331.2) | 新規特徴中最上位。単体では有望な可能性 |
| 4 | collateral_form | 対戦相手のpoint-in-time複勝率平均(直近3走) | JV-Link履歴(346,474行) | 14位 (gain 2267.0) | 中位 |
| 5 | nicks_score | 父×母父ペアの複勝率(ベイズ縮小k=20) | pedigrees | 19位 (gain 1575.6) | 中位〜下位 |
| 6 | peak_weight_proximity | 自己ベスト着順時の馬体重との乖離 | race_entries/race_results | 28位 (gain 623.4、全41中最下位) | 効果ほぼなし |
| 7 | jockey_trainer_combo | 騎手×調教師ペアの複勝率(ベイズ縮小k=20) | race_entries | 11位 (gain 2795.5) | 新規特徴中2番目に高い |

参考: 既存特徴量の最上位は `jvan_battle_dm`(gain 68216)・`jvan_time_dm`(gain 33333) — DM指数が支配的で、新規7特徴は全てこれらを大きく下回る。

### 留意点・今後の検討余地
- 単一hold-out窓(4ヶ月・n=1068レース)のみの検証であり、季節・重賞比率等の偏りの影響は否定できない。「確定的に効果なし」と断定するにはサンプルがまだ小さい
- 7特徴を一括投入した結果であり、**個別施策(特にpace_index_pciとjockey_trainer_combo)は単体では別の閾値・使い方（ルールベースのフィルタ等）で価値が出る可能性が残る** — バンドルすると弱い特徴同士が過学習ノイズを増やした可能性
- 現時点では本番指数への統合は見送り。chihou/keirinへの展開も見送り

## Phase 2: Listwise Ranking転換（計画修正）

Web調査時点では「現行v26はpoint-wise回帰ベースと推測」としていたが、実装確認の結果 **既に`objective=lambdarank`（LightGBM LambdaRank、`v26_lightgbm_rank.txt`）が本番デフォルトモデル**であることが判明（`inference_v26.py:45`）。このためPhase2としての新規性はなし。今後は以下へ計画修正:
- Plackett-Luceによる複勝(3着以内)確率の理論的導出（本番のLambdaRankスコアをworth値として活用）
- Classwise-ECE（キャリブレーション誤差）をモデル選択基準に導入
- 詳細は今後の実施結果をこのドキュメントに追記していく

## Phase 3: 市場混戦度スコア(HHI/Shannon Entropy)による見送り判定強化 ✅ 有効性確認済み

### 検証方法
- 実装: `backend/scripts/jra_phase3_market_chaos_analysis.py`（研究用、DB書き込みなし。`src/indices/confidence.py`の既存関数`calculate_race_confidence`/`is_market_favorite`/`calculate_recommend_rank`をそのままimportして使用、ロジック二重管理なし）
- サマリー: `backend/models/v26_phase3_market_chaos.json`
- 診断期間 2023-07-01〜2025-12-31 (n=8,377レース) でパターン探索 → 確認期間 2026-01-01〜2026-07-23 (n=1,905レース、完全ホールドアウト) で**診断期間の閾値をそのまま固定して**再現検証（多重比較・チェリーピッキング防止のため）
- 定義: 全馬の単勝オッズから implied probability `p_i=(1/odds_i)/Σ(1/odds_i)` を算出し、`HHI=Σp_i²`、`entropy_norm=Shannon entropy / ln(head_count)` (0〜1、1に近いほど大混戦)

### 既存tier別 的中率・ROI（診断/確認期間）

| tier | n(診断) | 複勝的中率 | 単勝的中率 | 単勝ROI |
|---|---|---|---|---|
| S | 1,460 | 80.1% / 78.4% | 49.3% / 46.8% | 0.906 / 0.800 |
| A | 1,433 | 70.0% / 67.3% | 36.9% / 33.3% | 0.855 / 0.761 |
| B | 1,737 | 64.4% / 63.3% | 31.3% / 28.9% | 0.802 / 0.745 |
| C(現状見送り) | 3,747 | 49.6% / 48.9% | 19.4% / 17.2% | 0.975 / 0.839 |

既存tierは診断→確認で単調性・水準ともによく維持されており健全（再検証済み）。

### 発見1: tier=C内をentropy_normで分割すると複勝的中率が11〜13pt分離（確認期間で再現）

診断期間の中央値閾値(tier=C: entropy_norm=0.7757)をそのまま確認期間に固定適用:

| セグメント | 複勝的中率(診断) | 複勝的中率(確認) |
|---|---|---|
| tier=C 低entropy(混戦度低) | 55.4% | 55.2% |
| tier=C 高entropy(真の大混戦) | 43.8% | 42.6% |
| 差分 | 11.6pt | 12.6pt |

S/A/B tierでも同方向・同程度の分離が再現（例: tier=S 低entropy 88.4%/87.5% vs 高entropy 71.9%/68.8%）。entropy_normは特定tierに限らず**全tier横断で効く直交的な情報**。

### 発見2: Risk-Coverage曲線比較で複合ソートキーが一貫して優位（確認期間で再現）

| ソートキー | 平均複勝的中率(診断) | 平均複勝的中率(確認) |
|---|---|---|
| (a) confidence_score単独 | 68.9% | 65.8% |
| (b) 現行tier複合キー(market_agree→confidence_score) | 70.9% | 68.7% |
| (c) entropy_normペナルティ単独 | 69.9% | 67.4%（(b)に劣後） |
| (d) 現行tier複合キー + entropy_normペナルティ(×30) | **71.6%** | **69.3%**（全カバレッジdecileで(b)を上回り確認期間でも再現） |

### 結論・実装候補
- **entropy_normは既存tier方式の置き換えではなく上乗せとして有効**。閾値65/80自体を置き換える根拠は得られず、market_agree優先→confidence_score次点、という既存の骨格は維持すべき
- 実装候補A: tier=C内をentropy_norm(tier別中央値)でさらに2分割し、低entropy側を「C+（準見送り・複勝候補）」に格上げ
- 実装候補B: 推奨順位付けに使う複合スコアに`entropy_norm*30`程度のペナルティを追加（ソート順の微調整）
- n<100のセグメントは全て除外/フラグ付きで報告済み。診断→確認で一貫して再現した頑健な結果

**→ Phase1(特徴量)と異なり、これはchihou/keirinへの横展開を検討する価値がある候補。**

### 本番実装（2026-07-26、A・B両方実装済み）

ユーザー承認により候補A・Bを両方実装:
- `backend/src/indices/confidence.py`: `calculate_market_chaos(win_odds)`関数追加(HHI/entropy_norm算出)。`ENTROPY_THRESHOLDS={"S":0.6951,"A":0.7414,"B":0.7564,"C":0.7757}`（診断期間の中央値をハードコード、確認期間で再現済みの値そのまま）。`calculate_recommend_rank()`に`entropy_norm`パラメータ追加し、tier=C判定後に`entropy_norm < ENTROPY_THRESHOLDS["C"]`なら"C+"に格上げ（候補A）
- `backend/src/services/recommender.py`: `build_hit_tier_recommendations()`で`calculate_market_chaos()`を呼び出しentropy_normを算出・`calculate_recommend_rank`に渡す。`_HIT_TIER_BET["C+"]="place"`・`_HIT_TIER_CONFIDENCE["C+"]=0.40`・`_HIT_TIER_LABEL["C+"]="準見送り（要注意）"`を追加。同tier内の並び替えキーを固定値`confidence`から`priority_score`(`confidence_score - entropy_norm*30`)に変更（候補B。従来は同tier内で常に同値だったため実質無効だった）
- `backend/src/api/races.py`: `/races/{race_id}/indices`エンドポイントにも`calculate_market_chaos`を追加し、推奨リストと表示tierを一致させた（このエンドポイントは元々全馬オッズを保持していたため対応可能。レース一覧エンドポイントは意図的に全馬オッズを取得しない設計のため対象外・従来通りCのまま）
- フロントエンド: `frontend/src/lib/api.ts`の`Recommendation.tier`/`RaceConfidence.recommend_rank`型に`"C+"`を追加。`RecommendCard.tsx`のTIER_STYLE・`ConfidencePanel.tsx`のRANK_CONF/RECOMMEND_MEANINGに"C+"のスタイル・説明文を追加
- テスト: `backend/tests/test_confidence.py`に`calculate_market_chaos`・C+分岐の新規テスト9件追加（全43件pass）。chihou(`chihou_races_router.py`)は`entropy_norm`引数を渡さず呼び出しているため後方互換・影響なし（常にNone→従来通りCのまま）
- 検証: Ruff・pytest(backend全844件)・`tsc --noEmit`いずれもクリーン

chihou/keirinへの横展開は本結果を踏まえ、今後別途検討する。

## Phase 4-A: オッズ時系列特徴（締切直前の下げ足） ⚠️ 信頼度低い参考値

### データ制約
`keiba.odds_history`は**2026-03-28開始・約5ヶ月分**しか蓄積がなく、Phase3のような診断期間(2年+)/確認期間(半年)の二段階honest検証は不可能。ユーザー合意の上、単一時系列2分割による低信頼度の予備検証のみ実施し、本番実装はしない前提で進めた。

### 検証方法
- 実装: `backend/scripts/jra_phase4a_odds_movement_analysis.py`（研究用、DB書き込みなし。`confidence.py`の既存関数を再利用）
- 対象: JRA10場・head_count≥8、指数1位馬(v26 composite)のwin odds時系列からearly(20%点)/late(80%点)を取り`odds_trend_ratio=odds_late/odds_early`を算出
- 探索用(2026-03-28〜06-07, 656R)で四分位境界を決定→確認用(2026-06-07〜07-19, 437R)にそのまま固定適用

### 結果
| quartile | 探索用 複勝的中率 | 確認用 複勝的中率 |
|---|---|---|
| Q1(最も下げた) | 61.0%(n=164) | 61.2%(n=170) |
| Q2 | 62.4%(n=396) | 53.5%(n=200) |
| Q4(最も上がった) | 47.9%(n=96) | 53.7%(n=67) |

Q1-Q4差: 探索用13.1pt→確認用7.5pt（方向は一致するが幅がほぼ半減）。既存tier(S/A/B/C/C+)内での追加分離効果は、n≥100かつ同符号で再現した組み合わせが**0件**。

### 結論
サンプル数(n=1,093)がPhase3(診断8,377/確認1,905)と比べて統計的信頼性が大幅に低く、**本番実装はしない**。方向性自体（下げた馬の方が的中率高い）は両期間で一致しているため完全に無価値とは言えないが、tierレベルの追加分離効果は確認できず、既存推奨エンジンへの直交シグナルとしての証拠は今回得られなかった。**odds_historyが1年以上・数千レース規模まで蓄積された時点で、Phase3と同じ二段階ホールドアウトで再検証する価値がある。**

## Phase 4-B: 調教データ(坂路)特徴量 🟡 有望だが未確証

### 前提（重要な発見）
本施策は2026-05-31〜06-07に**既に一度実施済み**だった（`memory/training_data_integration.md`）。`train_v26_chokyo.py`（坂路特徴5種）は実装済みだが、当時の目的指標「top5捕捉率」がほぼ横ばい(+0.014)だったため本番採用は見送られていた。3年全期間backfillは2回試行し**両方とも失敗**（JV-Link `JVOpen option=4` setupが数日ハング）。

今回のユーザー目標「1〜3着的中(複勝的中率)」は、当時プラスに動いた指標そのものだったため、拡張データで再評価した。

### 実施内容
- Windows側で調教データの差分取得(`option=1`)を実行し、`keiba.slope_training`/`wood_training`を**2026-05-30止まり→2026-07-26まで拡張**(571,202件)
- `train_v26_chokyo.py`（working tree上に再構築、mainに未マージ）をそのまま実行。train/valid期間は前回と同一、test期間のみ延長
- `calculated_indices.version=24`が2026-04-26でbackfill停止済みと判明したため、同一値で継続更新中の`version=26`に切り替えてtest期間を拡張

### 結果（5seed平均±std、複勝的中率が主指標）
| test窓 | baseline | +単純5特徴 | +自己相対込み8特徴 |
|---|---|---|---|
| 旧窓(541R, 前回同一期間) | 61.070±0.857% | 61.404±0.356% (+0.334) | 61.886±0.676% (+0.816) |
| 拡張窓(1,200R) | 59.068±0.572% | 59.614±0.497% (+0.546) | 60.016±0.432% (+0.948) |

前回(363R)の+2.37ptと合わせ、**3つの独立したテスト窓すべてで複勝的中率が一貫してプラス**。ただしn増加により数値はより穏当な値に収束（過大評価の是正）。`chokyo_last1f_z`(終い1Fトレセンz)がfeature importance上位7〜8位/39〜42特徴で安定使用。前回見られた「自己相対系を足すと単勝の伸びが消える」現象は今回は再現せず、8特徴版が5特徴版を一貫して上回った。

### 結論
方向性は一貫してプラスだが、複勝的中率+1pt未満はseed std(最大1.33pt)を踏まえると統計的に明確とは言い切れない幅であり、**現時点での本番統合(`train_v26_lightgbm.py`統合)は時期尚早**。3年全期間backfillが実現すれば信頼度を大きく上げられる可能性が高い。**保留・3年backfill実現後に再評価**が規律的な判断。

関連ファイル: `backend/scripts/train_v26_chokyo.py`（研究用、未コミット）・`backend/models/v26_phase4b_chokyo_extended_metrics.json`
