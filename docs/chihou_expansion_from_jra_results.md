# JRA施策のchihou(地方競馬)展開検証

JRA新指数開発（`docs/jra_new_index_results.md`）で有効性が確認された施策のうち、地方競馬(chihou)への展開余地を検証した結果。

## サマリー

| JRA施策 | chihouでの検証 | 結果 | 展開判断 |
|---|---|---|---|
| Phase5-A: Plackett-Luce複勝確率 | コード調査のみ(実験不要) | chihouは既にECE 0.0024の較正済みMLモデル(is_top3ヘッド)を使用中。JRAの新Harville実装(ECE 0.0072〜0.0092)より優れている | **不要（既に上回っている）** |
| Phase3: 市場混戦度スコア(entropy_norm) | 診断期間(2023-01〜2025-06, 17,999R)→確認期間(2025-06〜2026-07, 13,403R)の二段階検証 | 4カテゴリ中3つで方向性再現するも、主力sweet_spotで再現せず・再現組もオッズとの共線性強い | **非推奨（現時点）** |

## Phase5-A: Plackett-Luce複勝確率 → 展開不要

`src/indices/chihou_calculator.py`のコメントに明記の通り、chihouは複勝確率を「is_top3ヘッドの較正済み生確率（Harvilleは使わない）」として、単勝・複勝それぞれ専用に直接学習・較正されたLightGBMモデルで算出している。ECEは0.0024と、JRAで新規導入したHarville式（訓練期間ECE 0.0075、テスト期間ECE 0.0092）より明確に優れている。

**結論**: chihouは既にJRAより進んだ手法を使用済み。Plackett-Luce/Harville化は不要。

## Phase3: 市場混戦度スコア(entropy_norm) → 非推奨（現時点）

### 検証方法
- 実装: `backend/scripts/chihou_entropy_norm_analysis.py`（研究用、DB書き込みなし）
- `src/indices/confidence.py::calculate_market_chaos`・`src/indices/buy_signal.py`の本番判定関数(`chihou_is_sweet_spot`/`chihou_is_place_bet`/`chihou_low_odds_trust_level`)をそのままimportして使用（再実装なし）
- 母集団: `chihou_rebuild_walkforward.py::FULL_POP_QUERY`のDISTINCT ONパターン(version fallback・LEFT JOINで生存者バイアス回避)を踏襲
- entropy_normの算出には`chihou.race_results.win_odds`(確定オッズ、全出走馬)を使用（`chihou.odds_history`は2026-04-07以降のみ・5900万行超で低速なため今回は不使用）
- 診断期間(2023-01-01〜2025-06-30, 17,999R)で中央値閾値を決定→確認期間(2025-06-30〜2026-07-26, 13,403R)に固定適用

### 結果

| カテゴリ | 診断期間 low-high差 | 確認期間 low-high差 | 再現 |
|---|---|---|---|
| sweet_spot（単勝的中率） | -1.5pt | +1.1pt（符号反転） | ❌ 不採用 |
| place_bet（複勝的中率） | +3.0pt (26.1% vs 23.1%, n=4,674/4,669) | +4.7pt (28.6% vs 23.9%, n=3,598/3,038) | ✅ 再現 |
| low_odds_trusted（単勝的中率） | +14.2pt (75.7% vs 61.5%) | +15.3pt (76.8% vs 61.6%) | ✅ 再現（大きく一貫） |
| low_odds_untrusted（単勝的中率） | +8.0pt (51.5% vs 43.5%) | +4.8pt (51.0% vs 46.2%) | ✅ 再現（効果量やや縮小） |

**重要な留保**:
- low_odds系2カテゴリはentropy低グループのwin_odds自体が系統的に低い(trusted: 中央値1.10倍 vs 1.35倍等)ことを確認。「独立した新情報」ではなく「同一オッズ帯内のさらに細かいオッズ再分割」に近い可能性が高く、共線性の疑いが強い
- place_betの複勝ROIはplace_odds過去データのバックフィル未実施区間により診断期間側で有効サンプルn=6と極端に薄く、ROI自体は未検証。的中率(n=9,343)のみ信頼できる
- 主力候補のsweet_spotでは符号反転し再現せず

### 結論
**本番実装は非推奨（現時点）**。JRAのtier=C分割（11〜13pt一貫分離、質的な市場一致/不一致の分岐）とは異なり、chihouでは市場混戦度が既存のオッズベース条件（win_odds帯・fav_odds<2.0）とかなり重複した情報を持っており、上乗せというより部分的な言い換えになっている可能性が高い。地方競馬特有の「日次多場・多レース運営」「控除率の壁に支配されやすい」特性、および過去のchihou検証で頻発した「診断期間で良さそうでも確認期間で崩壊する」というプロジェクトの経験則に照らし、4カテゴリ中1つ不採用・残り3つも共線性の疑いありという結果はJRAほどのrobust性がないと判断。

place_bet・low_odds系での「低entropy優先」の方向性自体は将来の精査候補（win_odds層別した上での純粋なentropy_norm寄与の再検証等）として記録するが、現状のまま本番tier/表示への組み込みは行わない。

関連ファイル: `backend/scripts/chihou_entropy_norm_analysis.py`・`backend/models/chihou_entropy_norm_analysis.json`
