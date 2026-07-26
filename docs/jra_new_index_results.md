# JRA新指数開発 - 施策リストと結果まとめ

中央競馬(JRA)向けに開発・検証した新指数施策の一覧と結果。**有用性が確認できたものは地方競馬(chihou)・競輪への横展開を検討する**ため、このドキュメントで随時追跡する。

背景・全体設計方針: `memory/jra_new_index_web_research_2026_07_26.md`（Web調査6分野の統合レポート）

## サマリー表

| Phase | 施策 | 検証方法 | 結果 | chihou/keirin展開 |
|---|---|---|---|---|
| 1 | 新規特徴量7種（下記詳細） | 単一hold-out (test: 2026-01〜04, n=1068レース) | ❌ 複勝的中率−0.57pt・単勝ROIほぼ横ばい。改善確認できず | 見送り（効果未確認のため展開しない） |
| 2 | Listwise Ranking(LambdaRank)への転換 | — | ⚠️ **既に本番実施済みと判明**（`v26_lightgbm_rank.txt`がデフォルト本番モデル）。新規性なし | 対象外（既存本番構成の再確認のみ） |
| 3 | 市場混戦度スコア(HHI/Shannon Entropy)による見送り判定強化 | 診断期間(2023-07〜2025-12, n=8,377)で探索→確認期間(2026-01〜07, n=1,905)で固定閾値のまま再現検証 | ✅ **既存tier方式への上乗せとして有効。確認期間で再現済み**（tier=C内をentropy_norm中央値分割で複勝的中率11〜13pt分離） | **検討候補**（本番実装方式は要ユーザー判断。実装後にchihou/keirin展開を検討） |

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
