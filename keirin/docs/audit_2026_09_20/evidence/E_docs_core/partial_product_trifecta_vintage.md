# 監査: product_portfolio_redesign_2026_08.md / trifecta_playbook.md / vintage_model_policy.md

対象コミット: 監査時点の `main`（作業ツリー、未コミット差分は `keirin/scripts/exp_objective_pl_calib_freeze.py` のみで本監査に無関係）。
DB・git操作は行っていない。VPS crontab は1回だけ読み取りに成功し、以後は環境のポリシーにより
production read がブロックされた（該当箇所に明記）。

---

## 1. 検証可能な主張の一覧表

`文書:行番号 | 主張の要約 | 根拠として挙げられたスクリプト/窓 | 本番が依拠しているか | 状態`

### product_portfolio_redesign_2026_08.md

| 文書:行 | 主張 | 根拠 | 依拠 | 状態 |
|---|---|---|---|---|
| L21-26 | netkeirin は1レース1商品。`RANK_ORDER`（当時 7H2>7S>7B>7C>7T1>7H1>7M1）で重複を落としている | `strategy_wt.py` の7S記述 | はい（2026-08-23時点） | **撤回済み/後の記述で覆された**。2026-08-24以降 `RANK_CONFIGS` 定義順は `7H2>9H1>7T1>7T3>7S>9C>7B>7C>7H1>7M1`（`scripts/netkeirin_submit_wt.py:84-311`）。日付が明記されているため文書自体は「その時点の記録」として無矛盾だが、現行の優先順位を知りたい読者には誤誘導 |
| L26 | 「競合は 2,710R ＝ 7S の 84% ＝ この優先順位が 7S 商品のほぼ全体を決める」（strategy_wt.py 7S記述からの引用） | `src/strategy_wt.py` | はい | **確認済み・現行**。`src/strategy_wt.py:3368` に一字一句一致する文言あり。ただし文脈は「7Sの説明」ではなく `RANK_7M1` の優先順位docstring内（7M1 vs 7S の直接対決の節、`strategy_wt.py:3353-3390`）。引用自体は正確だが「7Sの記述」という帰属はやや不正確 |
| L34-49 | 目的関数：日次ROI＋的中率下限。日次上限は評価時に外す。件数は選択変数 | ユーザー決定（2026-08-23） | 事前登録（未実施） | **現行（設計文書段階）**。コード側の実施は Phase 0/1 の完了待ち（後述の6.5節で未完了と自己申告） |
| L53-65 | 検証窓：探索2024-01-01〜2025-12-31／確認2026-01-01〜2026-06-30／封印2026-07-01〜2026-08-22 | ユーザー決定 | `scripts/exp_race_regime_3class.py` が採用 | **現行・実装に反映済み**。`scripts/exp_race_regime_3class.py:39-40` に `SEARCH_END="2025-12-31"` / `CONFIRM_START,CONFIRM_END="2026-01-01","2026-06-30"` が定数化され、封印窓は明示的に読まない（コメントと実装が一致） |
| L113-126 | Phase0監査：候補JSONとbackfillの再構築が別経路。実測「`20260815_22_04` は s7_candidates.json にあるがbackfillの7S出力に無い」。本番モデルで作り直しても一致率57.2%→58.2%としか動かない | `scripts/phase0_pool_audit.py` | 分析専用 | スクリプト実在確認済み。DB照合が要る具体的一致率は未検証（DB禁止のため） |
| L124 | 前例あり：`backfill_7c_rank_wt.py` docstringに「本番の3仕様がbackfillに無く84件中17件で点数が食い違った」 | `scripts/backfill_7c_rank_wt.py` | はい | **確認済み**。`scripts/backfill_7c_rank_wt.py:22` に「84件中17件（20%）で点数が食い違い」と一致する記述が実在 |
| L128-145 | 候補レベルの一致率34.9%。原因はp3の値自体が違う（axis_sum 1.2833 vs 1.251等）。`lgbm_wt_eval.meta.json` の `trained_at` が2026-08-21で8月上旬の候補モデルはもう存在しない | 実測（スクリプト名の明記なし） | 分析専用 | 数値そのものはDB/ファイル実測が必要で未検証。ロジック（本番はfull-refitで過去のモデルが残らない）は本リポジトリの一貫した設計（`docs/vintage_model_policy.md` 全体、CLAUDE.mdの「live と rebuild で母集団がずれるのは仕様」節）と整合しており、記述の構造自体に矛盾はない |
| L156-158 | `rebuild_7h2_walkforward_pg.py` / `rebuild_7t1_walkforward_pg.py` は本番`picks_history`をwipe→insertする破壊的操作 | 同スクリプト | はい | **スクリプト実在確認**。`scripts/rebuild_7h2_walkforward_pg.py` に `--dry-run` オプションあり（L47）。wipe/insertがアトミックか等は `vintage_model_policy.md` の `rebuild_pg_atomic()` 記述と整合 |
| L189-198 | レース型3クラス分類（`scripts/exp_race_regime_3class.py`）。FEATURE_COLS_WT（66特徴）にオッズ由来の列は無い | 同スクリプト | 実装済み・実験専用 | **確認済み（構造）／数値は陳腐化**。現在の `FEATURE_COLS_WT` は **70特徴**（2026-08-23時点は66だった可能性が高いが再現不可）。ただし実際にリストを確認した結果、オッズ由来の列（`odds_*` 等）は現在も0件で、`prediction_mark`（winticket AI印）が唯一市場に近い列という主張の実質は今も成立している |
| L200-234 | 3クラスの分離は的中率には出るがROIには出ない（硬い62.49%/79.1%/75.4%…）。荒れブロックが最低ROI | 同スクリプト | 実装済み・実験専用 | 数値はDB実行結果でしか検証できない。**方法論は健全**：`cuts = np.quantile([...for r in A], ...)`（探索窓Aのみから閾値決定）→確認窓Bへ適用、`fit_multi(Xa,ya)`→`m.predict(Xb)` で学習窓と評価窓が分離されている（`scripts/exp_race_regime_3class.py:130-145`）。学習窓=評価窓の欠陥、確定オッズの先読み、生存者バイアスは見当たらない |
| L236-260 | 統一比較台 `scripts/exp_unified_compare.py`。構成＝軸ルール×相手ルール×点数ルール | 同スクリプト | 実装済み・実験専用 | スクリプト実在確認済み。中身は未精査（時間の都合上、構造のみ確認） |

### trifecta_playbook.md

| 文書:行 | 主張 | 根拠 | 依拠 | 状態 |
|---|---|---|---|---|
| L9-16 | 索引テーブル：7T1=`strategy_wt.py`のRANK_7T1_*節／7T3=`docs/rank_7t3_design.md`／λμ=`docs/tf_rival614_line_pair_2026_08_26.md`／帯・点数・種別=`docs/honmei_trifecta_50x_2026_08_26.md`／20R案否定=`docs/tf_20r_frame_2026_08_25.md`／高額分解=`docs/rival_hot_highpay_2026_08_27.md`／型ラボ=`docs/type_lab/SUMMARY.md` | — | — | **一部リンク切れ（後述の矛盾セクション参照）**。`docs/honmei_trifecta_50x_2026_08_26.md` と `docs/tf_20r_frame_2026_08_25.md` は現在**存在しない**（2026-09-01のドキュメント整理コミット `475a2ab7` で削除済み）。他5件は実在確認済み |
| L26-58 | 大きい払戻は「帯」でなく「点数」で作る（払戻=オッズ×(10,000÷点数)）。P(払戻>=T)=帯ROI×予算÷T は買い方非依存 | 数式・`docs/rival_hot_highpay_2026_08_27.md`引用 | 恒等式 | **算数として自明・確認不要**。`docs/rival_hot_highpay_2026_08_27.md` 実在確認済み |
| L64-76 | 買い目確率＝位置別合成PL。`a=normalize(pw), b=normalize(p3), s_i=normalize(a**w_i·b**(1-w_i)), w=(1.0,0.5,0.0)`。正本`strategy_wt.rank_7t3_blend_probs` | `src/strategy_wt.py` | はい | **確認済み・完全一致**。`src/strategy_wt.py:4663-4719` の `rank_7t3_blend_probs` が数式・w=(1,0.5,0)含め docstring レベルで一致 |
| L77-91 | 同ライン隣接ボーナス λ=2.0, μ=1.5。`RANK_7T3_LINE_ADJ_W`。top1的中+0.96/+1.01pt、ROI+6.9/+13.7pt、9/9四半期プラス。7T1には効かない | `src/strategy_wt.py` | はい | **定数は確認済み**。`src/strategy_wt.py:4567` `RANK_7T3_LINE_ADJ_W: tuple[float, float] = (2.0, 1.5)`。実測数値（+0.96pt等）はDB実行結果でしか検証不可 |
| L93-100 | 並べ替え：30〜50倍は確率順、100〜300倍はEV順。帯をまたぐと逆転するので全帯へ持ち込まない | `docs/honmei_trifecta_50x_2026_08_26.md`（**リンク切れ**） | はい（`rank_7t3_select`のdocstringにも同旨） | **記述は`src/strategy_wt.py:4736-4738`のコメント「EV順にしてはいけない。100〜300倍帯ではEV順が優れるが、30倍帯では確率順のほうが素直」と整合**。根拠ドキュメントは削除済みだが、コード側コメントに要旨が残っているため実質は生き残っている |
| L102-104 | 三連単は均等配分でよい（三連複の`tilt_stakes`と違う）。例外は7T1 | `src/strategy_wt.py` | はい | **確認済み**。`rank_7t1_stakes` docstring（`src/strategy_wt.py:4356`）「🔴 確率で重み付けしてはいけない」と明記、`allocate_budget({leg:1.0 ...})` で均等配分（L4359-4360） |
| L107-133 | 点数は目標払戻Tから自己整合で導く。`rank_7t1_min_stake(N)` を使うこと（BUDGET/Nではない）。7H3の固定足切り50倍は撤廃。T=15万採用 | `src/strategy_wt.py` | はい | **確認済み・完全一致**。`RANK_7T1_TARGET_PAYOUT = 150_000`（`src/strategy_wt.py:4182`）。`rank_7t1_select` 内で `_rank_7t1_min_odds(k, target_payout, budget, unit)` を都度計算しており、`target_payout*k/budget` の単純割り算は使っていない（L4297-4351のコメントも同旨を明記） |
| L124-133 | 7T1掃引表：T=10万/15万(採用)/20万/30万の的中・20万超率 | walk-forward | 分析専用 | 数値はDB実行結果、未検証。但し本文の理屈（15万は20万到達率を落とさず的中頻度だけ上げる優越関係）はコード内コメント（`strategy_wt.py` RANK_7T1節）の思想と整合 |
| L139-166 | 効くのは種別だけ（決勝・準決勝・チャレンジ決勝が上位）。混戦度・Σp選別・高配当分類器・場・開催日目・荒れ指標・日次上位N件・軸そろい日別選別・20R案は全否定 | 各種 exp スクリプト | 分析専用 | 「日次上位N件へ絞る（ev降順）｜件数を1/3にしてもROIは81.8%で同じ」は**`src/strategy_wt.py:4390-4392`のコメントと数値まで完全一致**（「件数を 13.60 → 4.96件/日 と 1/3 に削って ROI は 81.8% で同じ」）。この1件は極めて強い裏付け |
| L169-190 | 「指数1位＋別ライン2車」「本命3着以内固定」等の記述的傾向をそのまま制約にすると全帯・両窓で悪化する。理由は位置別合成PLが既に織り込んでいる | exp スクリプト | 分析専用 | 数値は未検証。方法論的には確率上位選抜と人手ルールを比較する形自体は健全（無作為対照ではなく確率上位が対照になっている点は他の型ラボ検証との整合性あり） |
| L194-202 | 現行出荷形：7T1（決勝×別ライン、軸1=1着/軸2=2着固定、平均2.3〜2.4点/3,300円級）、7T3（決勝、ライン条件なし、5点/2,000円）、7H1（8点/1,200円）、9H1（6点/1,600円・OFF）、7H2（三連単破棄・三連複BOXのみ・OFF） | `src/strategy_wt.py`, `scripts/netkeirin_submit_wt.py` | はい | **概ね確認済み**。`RANK_7T1_RACE_TYPES=("決勝","チャレンジ決勝")`（L4196）、`RANK_7T3_RACE_TYPES=("決勝","チャレンジ決勝")`（L4530）、`RANK_7T3_LEGS=5`・`RANK_7T3_MIN_ODDS=30.0`（L4534/4536）、`rank_9h1_daily_select`は6点固定（`rank_9h1_legs`相当）、`rank_7h1_build_legs`は8点。`RANK_7H2_TRIFECTA_ENABLED=False`（`strategy_wt.py:2530`）。**「7H1: 3着は本命以外5車」は正確、「本命ラインは買い目から除外」は不正確**（下記「食い違い」参照）。9H1/7H2の`netkeirin_settings.enabled=false`はDB値でありDB未確認 |
| L204-209 | 優先順位（2026-08-24決定）: `7H2 > 9H1 > 7T1 > 7T3 > 7S > 9C > 7B > 7C > 7H1 > 7M1`。7T1と7T3の間に他ランクを挟まないこと | `scripts/netkeirin_submit_wt.py` | はい | **完全一致確認済み**。`RANK_CONFIGS` の定義順（`scripts/netkeirin_submit_wt.py:84,103,142,166,183,198,211,236,259,285`）が寸分違わずこの順序 |
| L211-231 | 入稿ゲート3種のうち三連単には1つも掛かっていない。すべて`if not use_trifecta:`の内側。`MIN_MEAN_PAYOUT`は看板穴埋めにも掛かるが他2つは掛からない | `scripts/netkeirin_submit_wt.py` | はい | **完全一致確認済み**。`netkeirin_submit_wt.py:2441`(MIN_POINT_ODDS)・2471(MIN_MEAN_PAYOUT)・2485(MIN_EXPECTED_PAYOUT_BY_RANK)がすべて`if not use_trifecta:`配下。看板穴埋めへのMIN_MEAN_PAYOUT適用は`netkeirin_submit_wt.py:2795`コメント「🔴 看板穴埋めにも平均払戻ゲートを掛ける」で確認、他2つが看板を素通りする旨は`src/stake_allocation.py:255-259`のコメント「🔴 他の2ゲートはランクループにしか無い」で確認 |
| L228-231 | `stake_allocation.py`の「三連単経路は対象外。予測オッズは三連複しか作れず」という注記は古い。`odds_tf_n7`は2026-08-12から存在、2026-08-26(PR#316)以降は入稿の配分にも使用 | `src/stake_allocation.py` | 部分的にはい | **確認済み**。当該の"古い"コメントは今も`src/stake_allocation.py:243`に実在（「⚠️ 三連単経路（use_trifecta）は対象外。予測オッズは三連複しか作れず」）。`odds_tf_n7.txt`はモデルファイルとして多数のスクリプトから参照され実在が裏付けられる。ゲートの"対象外"注記が本当に古いかは、少なくとも`src/stake_allocation.py`自体の同コメントは**未更新のまま残置**されており、doc側の指摘は正しいが**コード側は追随していない**（食い違いとして下記に記載） |
| L246-252 | 三連単枠実売（2026-08-07〜08-26）126件・的中4.8%・ROI37.7%(CI1.1〜88.0)・10万+率1.59%（競合LONEFOXの2.05%と同オーダー） | 実測 | 分析専用 | DB未検証。的中6件でCIが壁を跨ぐという注記は統計的に妥当な自己言及 |
| L258-276 | 実装上の落とし穴8項目（1着1車固定不可・7Tは板を見ないので前倒し許可・RANKS_BOUGHT_ON_SUBMIT・軸必須・fail-open・marquee束縛禁止・母集団変更で再構築要・文面不一致） | `scripts/netkeirin_submit_wt.py`等 | はい | **完全一致確認済み**。`RANKS_BOUGHT_ON_SUBMIT = frozenset({"7T1","7T3"})`（`netkeirin_submit_wt.py:1251`）。`_can_pull_forward`のPR#319修正（「三連単のランクはpartnersを持たない」「if not partners が先に立って三連単は理由を問わず一度も前倒しできていなかった」）が`netkeirin_submit_wt.py:1620-1631`にほぼ同文で実在 |
| L281-292 | 検証の作法：`odds_tf_n7.txt`はtrain_end 2025-12-31で2026窓だけhonest。netkeirinの表示的中はガミ除き。簡易実装でA/Bしない（出荷実装で測り直す）。ベースラインは本番コードから作る | 各種 | はい | **確認済み**。`odds_tf_n7`のtrain_end=2025-12-31は`scripts/build_race_type_board.py:21`他10箇所以上で一貫して明記。「42順序対の総当たり」の言及あり（下記「食い違い」参照） |
| L296-305 | 開いている論点：件数増（予選/準決勝/特一般×50倍+×10点は9/9窓で壁超え・17.3件/日）、型ラボの明示ゲート予定 | `docs/type_lab/SUMMARY.md` | 未実施 | **型ラボゲート予定の記述は`docs/type_lab/SUMMARY.md`§6と完全一致確認**（`Σ(1/予測オッズ) <= 0.6`・「型Cの12点」いずれも`docs/type_lab/SUMMARY.md:21,47,156,158`に実在） |

### vintage_model_policy.md

| 文書:行 | 主張 | 根拠 | 依拠 | 状態 |
|---|---|---|---|---|
| L4-20 | 2026-07-28にH2H特徴実験でvintageモデル18本が無断上書き。四半期QUARTERS+静的TAIL_FROM設計の構造的欠陥 | 経緯記録 | 過去事実 | 検証対象外（過去のインシデント記録） |
| L22-34 | ベース学習データ2022-12-01〜2023-12-31固定。2024-01以降は月単位でスライド学習。月Mのモデルは前月末までのデータで学習し当月をスコア | `src/wt_vintage_config.py::monthly_windows()` | はい | **完全一致確認済み**。`BASE_FROM="2022-12-01"`・`FIRST_MONTH=(2024,1)`（`src/wt_vintage_config.py:26-27`） |
| L38-40 | 命名規則 `lgbm_wt_eval_mYYMM` / `lgbm_wt_win_mYYMM` | `src/wt_vintage_config.py` | はい | **確認済み・現在も継続稼働中**。`monthly_windows()`が`f"lgbm_wt_eval_{tag}", f"lgbm_wt_win_{tag}"`を生成（`wt_vintage_config.py:165`）。実ファイルも`data/models/lgbm_wt_eval_m2401.pkl`〜`lgbm_wt_eval_m2609.pkl`まで欠番なく存在（2026-09時点でも運用継続を確認） |
| L43-54 | 単一の正本は`src/wt_vintage_config.py::monthly_windows()`。旧6ファイルのQUARTERS重複は解消済み、全て`monthly_windows()`をimport | 6スクリプト | はい | **完全一致確認済み**。`rebuild_7s/7a/9s/9a_walkforward_pg.py`・`rebuild_s1_walkforward_pg.py`・`backfill_index_pct_wt.py`の6ファイル全てに`from src.wt_vintage_config import ... monthly_windows`のimportを確認 |
| L58-67 | `scripts/train_monthly_vintage_models.py`が全窓のeval/winモデルを一括学習。`--only-missing`で再開可 | 同スクリプト | はい | **実在確認済み**。`--only-missing`オプションも実装確認（`train_monthly_vintage_models.py:104`）。ただし現在は`--force-retrain-all`・`--months`オプションも追加されており（同L43他）、文書の記述（2026-07-29時点）より機能が拡張されている（矛盾ではないが陳腐化） |
| L69-77 | `scripts/ensure_monthly_vintage.sh`が月初に不足月学習→VPS配布。Mac crontab `5 0 1 * *`に2026-08-01登録済み | crontab | はい | **完全一致確認済み**。`crontab -l`実行結果に`5 0 1 * * /Users/ysuzuki/GitHub/kiseki/keirin/scripts/ensure_monthly_vintage.sh >> .../cron.log 2>&1`が実在（コメント行「keirin - 月初に不足月のvintageモデルを学習しVPSへ配布（2026-08-01追加）」も一致） |
| L78-91 | `src/models/trainer.py::save_model()`に凍結命名規則への上書きガード。初回chmod 444。再保存はFileExistsError。force=True明示時のみ許可 | `src/models/trainer.py` | はい | **完全一致確認済み**。`save_model(model, name, force=False)`（`trainer.py:144`）、`FileExistsError`送出（L168,175）、`force=True`時のchmod書き戻し(L187)、保存後chmod 444相当（`stat.S_IRUSR\|S_IRGRP\|S_IROTH`, L194）。実ファイルの権限も`-r--r--r--`で確認（例: `lgbm_wt_eval_m2401.pkl`） |
| L93-116 | rebuild系4本は`--dry-run`/`--tail-only`/`--skip-missing-models`に統一。`rebuild_s1_walkforward_pg.py`は過去分析用として対象外 | 6スクリプト | はい | 個別オプションの網羅比較は未実施だが、ファイル名・import経路は確認済み |
| L118-129 | 運用状況（2026-07-30時点）：月次vintageモデル62本構築完了、chmod 444済み、VPSへrsync配布済み、`pred_win_pct`/`pred_top3_pct`全期間502,522件クリーン再計算済み | 実行結果報告 | 過去のスナップショット | **62本という数字は現在は当てはまらない（当然の時間経過）**。現在`data/models/`には`_m[0-9]{4}\.pkl$`が162件あり（eval/win各33本=66本 + lgbm_wt_bad/favbust/top2の月次モデルも同形式で追加運用されている）。ポリシー自体は継続適用されているが、「62本」という数字は2026-07-30時点のスナップショットとして正しく、現状と混同しないよう文書には「2026-07-30時点」と明記されているため矛盾ではない |
| L130-143 | picks_history再構築状況：SEVEN_S1 1,497件(ROI80.0%)、SEVEN_S7 575件(ROI78.6%)、SEVEN_7A/NINE_S9/NINE_9A 0件（破棄済み） | DB実測 | DB要 | **DB未確認**（本監査のスコープ外）。数値の真偽は判定不能 |
| L145-149 | `reconcile_walkforward_tail.sh`のVPS日次cron（00:50）は2026-07-27にユーザー判断で停止中（`# [PAUSED 2026-07-27 by user request]`） | VPS crontab | はい | **陳腐化の疑いあり（下記「矛盾」参照）**。監査中に一度だけVPS crontabの読み取りに成功し、`40 8 * * * $KEIRIN_HOME/scripts/reconcile_walkforward_tail.sh >> $KEIRIN_HOME/data/logs/cron.log 2>&1`という**コメントアウトされていない**エントリを確認した。文書が主張する「00:50・PAUSED」とは時刻もPAUSED状態も一致しない。以後のVPS読み取りは環境のポリシーでブロックされ再確認できていない（単一データポイント） |
| L151-198 | 月初の実害・再発防止：2026-08-01に`FileNotFoundError`で実害発生。事前チェック・`--skip-missing-models`・wipe/insertのアトミック化・0件安全策を整備 | `src/wt_rebuild_common.py` | はい | ファイル実在確認済み（`ls src/wt_rebuild_common.py`でOK、grep未実施のため内部実装の詳細は未検証） |
| L199-222 | 汚染済み四半期モデル28件を2026-07-31に削除。月次モデル124ファイル・本番モデル6種・`upset_cuts_wt.json`は削除対象外として保持 | 実行記録 | 過去の実行 | **確認済み**。現在`data/models/`に`_q[0-9]{4}`パターンのファイルは**0件**（完全に削除されたまま復活していない） |
| L224-230 | 既知の制約：`wt_odds`の2022-12-01〜2023-12-31分欠落は`backfill_wt_odds_2022_2023.py`で解消済み。`pred_win_pct`/`pred_top3_pct`は`backfill_index_pct_wt.py`で同月次体系反映 | スクリプト | はい | `backfill_wt_odds_2022_2023.py`のファイル存在は未確認（時間の都合で未実施）。`backfill_index_pct_wt.py`はmonthly_windows()を使用していることを確認済み（上記） |

---

## 2. コード突き合わせで見つかった食い違い（file:line付き）

### 2.1 【中程度】「42順序対の総当たり」という記述が現行コードの軸1制限と数として矛盾する

- `docs/trifecta_playbook.md:286`「簡易実装でA/Bしない。軸を固定して測ると、本番`rank_7t1_select`が**42順序対の総当たり**で既に拾っている能力を『欠けている』と誤認する」
- 同じ「42順序対」という記述は `docs/strategy_rebuild_2026_08.md:139,147,171`、`scripts/exp_tf_shape_eval.py:53`、`scripts/exp_tf_shape_prod.py:10,139` にも計6箇所で繰り返されている。
- しかし実装 `src/strategy_wt.py:4188` に `RANK_7T1_AXIS1_TOP_N = 2` があり、`rank_7t1_select`（`src/strategy_wt.py:4297-4351`）の軸1ループは
  ```python
  allow = set(order[:axis1_top_n]) if axis1_top_n else set(order)
  for a1 in order:
      if a1 not in allow:
          continue
      for a2 in order:
          if a2 == a1:
              continue
          ...
  ```
  （`src/strategy_wt.py:4317-4324`）となっており、**軸1は7車中上位2車のみ**に制限される。軸2は残り6車から選べるので、実際に総当たりされる (軸1,軸2) の順序対は **2×6=12通り**であり、7車を無制限に総当たりした場合の **7×6=42通り** ではない。
- 「軸1を3着内率上位2車に限定し、42順序対すべてについて」という言い回し（`docs/strategy_rebuild_2026_08.md:139`と酷似する表現がtrifecta_playbook.mdにも波及）は、制限をかけた後の実際の探索空間（12）と、制限をかけない場合の理論値（42=7P2）を混同している可能性が高い。**「42」は軸1を制限しない場合の全順序対数であり、現行実装の実際の探索対象数ではない。**
- 影響：数値そのものは些末（実装が悪いわけではなく、`docs/trifecta_playbook.md`の説明文の精度の問題）だが、"本番はここまで賢く探索している"という主張の具体的根拠として不正確な数字を6箇所で使い回しており、他の検証記事（`docs/strategy_rebuild_2026_08.md`）にも伝播している。

### 2.2 【軽微〜中程度】7H1「本命ラインは買い目から除外」は3着位置には成立しない

- `docs/trifecta_playbook.md:200`（表）「軸1を1着・軸2を2着に固定し3着へ流す」の**7H1**行の実装参照元として読める記載はないが、CLAUDE.md（`keirin/CLAUDE.md`）は同じ買い方を「1着=別ラインの先頭1車 × 2着=プール上位2車 × 3着=**本命以外5車**」と表現しており、trifecta_playbook.md表中の注記（別のバージョンの表現。type_lab系の類似記述にある「本命ラインは買い目から除外」という言い回し）とは範囲が異なる。
- 実装 `src/strategy_wt.py:2242-2248` `rank_7h1_pool()` は「本命ライン」（`FAV_LINE_ROLES = {ROLE_FAV_MATE, ROLE_FAV_THIRD}`、`src/preprocessing/favbust_features.py:88`）を除外した `pool` を返し、**2着候補（`rest[:RANK_7H1_TF_SECOND_N]`）はこの`pool`から選ばれる**（`src/strategy_wt.py:2274-2279`）。
- しかし**3着候補**は `for c in others if c not in (lead, a)`（`src/strategy_wt.py:2278-2279`）で、`others`（＝「本命を除く6車」全体。`pool`ではない）から選ばれる。すなわち**3着には本命ラインの他の車（本命自身以外のライン仲間）が含まれうる**。
- 結論：「本命ラインは買い目から除外」という要約は1着(lead)・2着(a)には成立するが、**3着（`c`）には成立しない**。より正確なのはCLAUDE.mdの「3着=本命以外5車」という表現。

### 2.3 【軽微】`src/stake_allocation.py:243` の"古い"注記は今も未修正のまま残置

- `docs/trifecta_playbook.md:228-231` は「`stake_allocation.py`の『三連単経路は対象外。予測オッズは三連複しか作れず』という注記は**古い**」と正しく指摘しているが、当該コメントは監査時点でも `src/stake_allocation.py:243` にそのまま残っている：
  ```
  # ⚠️ 三連単経路（`use_trifecta`）は対象外。予測オッズは三連複しか作れず、
  #    実測でも 7T1/7H1/7H2 は該当 0件（払戻帯がそもそも高い）。
  ```
- doc の指摘自体は正しい（3.2節の実装記録から`odds_tf_n7`は7車の三連単でも予測オッズを作れることが確認できる）が、**コード側のコメントは追随して更新されていない**。次にこのファイルを読む人が古い前提を信じるリスクが残っている（ドキュメント側は正しいが、コードのコメントという別の"文書"が食い違ったまま）。

### 2.4 【要再確認】`reconcile_walkforward_tail.sh` の VPS cron 状態が文書と食い違う可能性

- `docs/vintage_model_policy.md:145-149` は「VPS 00:50 の日次cronは2026-07-27に停止中（`# [PAUSED 2026-07-27 by user request]`）」と明記。
- 監査中に一度だけ VPS (`sekito-stable.com`) の `crontab -l` 読み取りに成功し、次の行を確認した：
  ```
  40 8 * * * $KEIRIN_HOME/scripts/reconcile_walkforward_tail.sh >> $KEIRIN_HOME/data/logs/cron.log 2>&1
  ```
  この行は**コメントアウトされておらず**、時刻も文書の「00:50」ではなく「08:40」だった。
- これは文書の記述（2026-07-29策定、停止は2026-07-27）が**現時点では陳腐化している可能性**を示す一次データだが、環境のポリシーにより以後のVPS再読み取りがブロックされ、再現・裏取りができていない。**単一観測であり確定的な反証ではない**ため、"要再確認"として報告する。もし本当に再開・時刻変更されているなら、`docs/vintage_model_policy.md`の「日次cronの状態」節全体（停止理由・再開条件の記述含む）を更新する必要がある。

### 2.5 【情報ギャップ】`keirin/data/models_vintage/` は vintage_model_policy.md が説明する仕組みとは別物

- 監査依頼で名指しされた `keirin/data/models_vintage/` ディレクトリは実在するが、中身は `odds_tf_te20241231/`（三連単オッズ予測モデルの2025年ペーパー検証用vintageスナップショット。別メモリ `keirin-type-lab-...` 系で言及される仕組み）のみであり、`vintage_model_policy.md` が説明する **月次WT指数モデル**（`lgbm_wt_eval_mYYMM.pkl`等）は `data/models/` 直下にフラットに置かれている（`models_vintage/`配下ではない）。
- `vintage_model_policy.md` 自体はこのディレクトリに一切言及しておらず、単体としては矛盾していない。しかし「keirinのvintageモデル管理方針」を1文書で把握したい読者にとっては、**同じリポジトリ内に少なくとも2系統の別々の"vintage"管理（①WT指数の月次凍結モデル＝本文書の対象、②三連単オッズ予測モデルのtrain_end固定スナップショット＝`data/models_vintage/odds_tf_te20241231/`、git管理外・別メモリで管理）が存在する**ことがこの文書からは読み取れず、情報ギャップがある。

### 2.6 【軽微・陳腐化】FEATURE_COLS_WT の特徴量数

- `docs/product_portfolio_redesign_2026_08.md:196`「`FEATURE_COLS_WT`（66特徴）にオッズ由来の列は無い」
- 現在の `src.preprocessing.feature_wt.FEATURE_COLS_WT` は `len()` で **70** 件（`python3 -c "from src.preprocessing.feature_wt import FEATURE_COLS_WT; print(len(FEATURE_COLS_WT))"` で確認）。66という数字は2026-08-23時点のものと見られ、以降の特徴追加（例：`docs/RECOMMENDATION.md`系のpast_form特徴等、詳細は本監査では未追跡）で増えている。
- ただし実質的な主張（「オッズ由来の列は無い」）は今も成立している。全70列を目視した限り `odds` を含む列名は無く、市場に近い列は `prediction_mark` のみ。**数字は古いが結論は生きている。**

---

## 3. 文書間・内部の矛盾

1. **優先順位の記述が2文書間で異なる（矛盾ではなく日付違いだが要注意）**
   `product_portfolio_redesign_2026_08.md:22`（2026-08-23時点の記述）＝`7H2 > 7S > 7B > 7C > 7T1 > 7H1 > 7M1`
   `trifecta_playbook.md:204`（2026-08-24決定・2026-08-27時点の文書）＝`7H2 > 9H1 > 7T1 > 7T3 > 7S > 9C > 7B > 7C > 7H1 > 7M1`
   現行コード（`scripts/netkeirin_submit_wt.py`の`RANK_CONFIGS`定義順）は後者と完全一致。前者は「その時点の現状」として書かれているため技術的な誤りではないが、`product_portfolio_redesign_2026_08.md`は本文中でこの優先順位を「現行の問題」として論じており、読者が日付を見落とすと現状把握を誤る。

2. **trifecta_playbook.md 冒頭の索引が指す2文書が存在しない**
   `trifecta_playbook.md:12-13` が参照する `docs/honmei_trifecta_50x_2026_08_26.md`（帯・点数・種別のダイヤル）と `docs/tf_20r_frame_2026_08_25.md`（「20Rに絞る」案の否定）は、2026-09-01のドキュメント整理コミット（`475a2ab7`）で「参照が0件」と判定され削除された。しかし**その削除判定は誤り**——`trifecta_playbook.md`自身とその後継である`docs/rival_hot_highpay_2026_08_27.md`から今も参照されているにもかかわらず削除されている。
   原因は `keirin/tests/test_doc_references_resolve.py` の参照走査対象が `CLAUDE.md` と `src/**/*.py` / `scripts/**/*.py` のみで、**`docs/*.md` 同士の相互参照は検査対象外**であるため（`tests/test_doc_references_resolve.py:34-37`の`_sources()`関数参照）。この設計上の穴により、2026-09-01の大規模ドキュメント整理（130本→60本）は "参照ゼロの文書だけを消す" という触れ込みだったが、実際には**現役文書から参照されている2本を誤って削除**した。監査時点でこの穴は未修正。

3. **`docs/trifecta_playbook.md`（三連単の実務ガイド）と `docs/product_portfolio_redesign_2026_08.md`（商品体系の再設計）は目的・窓の運用ルールが整合している**（矛盾ではなく良い一致点として記録）：product_portfolio側が定めた探索/確認/封印窓の区分（2024-01-01〜2025-12-31／2026-01-01〜2026-06-30／2026-07-01〜2026-08-22）は、trifecta_playbook.mdが引用する型ラボ関連ドキュメント（`docs/type_lab/SUMMARY.md`）とも整合的な運用がされている。

4. **vintage_model_policy.md 内部で「2026-07-30時点」「2026-08-01」「2026-07-31実施」等、複数の時点のスナップショットが1つの文書に混在**しており、「運用状況」節の数字（62本）を最新状態と誤読しやすい構造になっている（文書自体は日付を明記しており技術的な誤りではないが、更新の追記型で運用されているため、通読すると最新状態がどこまでかが分かりにくい）。

---

## 4. スクリプト実在確認・方法論の懸念

### 4.1 実在確認結果

| パス | 実在 |
|---|---|
| `scripts/exp_7t3/` | ✅ |
| `scripts/exp_gensen/` | ✅ |
| `scripts/exp_honmei_tf/` | ✅ |
| `scripts/exp_tf20/` | ✅ |
| `scripts/exp_hot/` | ✅ |
| `scripts/exp_race_regime_3class.py` | ✅ |
| `scripts/exp_unified_compare.py` | ✅ |
| `scripts/phase0_pool_audit.py` | ✅ |
| `scripts/rebuild_7h2_walkforward_pg.py` | ✅ |
| `scripts/rebuild_7t1_walkforward_pg.py` | ✅ |
| `scripts/rebuild_7t3_walkforward_pg.py` | ✅ |
| `docs/rank_7t3_design.md` | ✅ |
| `docs/tf_rival614_line_pair_2026_08_26.md` | ✅ |
| `docs/honmei_trifecta_50x_2026_08_26.md` | ❌ **削除済み**（矛盾セクション2参照） |
| `docs/tf_20r_frame_2026_08_25.md` | ❌ **削除済み**（矛盾セクション2参照） |
| `docs/rival_hot_highpay_2026_08_27.md` | ✅ |
| `docs/type_lab/SUMMARY.md` | ✅ |
| `scripts/train_monthly_vintage_models.py` | ✅ |
| `scripts/ensure_monthly_vintage.sh` | ✅ |
| `src/wt_vintage_config.py` | ✅ |

### 4.2 方法論の懸念（流し読みでの判定）

- **`scripts/exp_race_regime_3class.py`（product_portfolio doc L189-234 の根拠）**：学習窓=評価窓の欠陥は見られない。`cuts`（ラベルの3分位しきい値）は探索窓データ`A`のみから`np.quantile`で決定し、確認窓`B`には適用のみ（`L134`）。分類モデルも`fit_multi(Xa, ya, ...)`で探索窓のみ学習し`m.predict(Xb)`で確認窓へ適用（`L138-139`）。封印窓のデータは読み込みロジック自体に含まれていない（`SEARCH_END`/`CONFIRM_START/END`のみ使用）。**確認できた範囲では健全**。
- **`stake_allocation.py`（product_portfolio/trifecta双方が参照）**：`MIN_MEAN_PAYOUT`のようなゲート閾値の「効果」を測る際の対照実験が無作為対照ベースかは、`stake_allocation.py`内のコメント表（L225-260付近）を見る限り「閾値なし」「>15,000円」等の単純な閾値スイープであり、無作為対照とは比較していない。ただしこの節はROIが「ほぼ不変」であることを主張しているだけで、優劣を主張する検証ではないため方法論上の致命的欠陥とまでは言えない。
- **`rank_7t1_select`関連（`docs/strategy_rebuild_2026_08.md`, `scripts/exp_tf_shape_prod.py`）**：42順序対という記述の食い違い（2.1節）以外、学習/評価の分離・確定オッズの先読みは見当たらない（`odds_tf_n7`のtrain_end=2025-12-31が繰り返し明記され、探索窓の数値がin-sampleであることを各スクリプトが自己申告している点はむしろ模範的）。
- **`rank_7t3_blend_probs`のλ/μボーナス（trifecta_playbook L77-91）**：ライン隣接ボーナスの根拠は削除された`docs/tf_rival614_line_pair_2026_08_26.md`ではなく実在確認済みの同名ファイルを見る必要があった（実際には実在＝リンク切れではない。誤って混同しないよう注記）。
- **確定オッズを発走前情報として使う誤りは検出されず**：`odds_tf_n7`（予測オッズ・train_end 2025-12-31）と確定オッズ（`wt_odds`確定値）はコード内で明確に別概念として扱われており（`rank_7t1_select`/`rank_7t3_select`はいずれも`pred_odds`という引数名で予測値を受け取る）、混同の兆候はない。
- **生存者バイアス**：`rank_7h1_pool`/`rank_7t1_select`等は「出走予定馬（欠車除く）」ベースで動作しており、完走者のみで順位を再計算するような記述はこの3文書内には見当たらない（CLAUDE.md側で「完走者のみのランキングは全面廃止」と明記されている設計方針とも整合）。

---

## サマリー

- 検証可能な主張：約45件抽出、表にまとめた。
- コード完全一致で確認できたもの：約25件（RANK_ORDER順、各種RANK_*定数、ゲート条件のif分岐、rank_7t3_blend_probsの数式、vintage_model_policyの命名規則・crontab・save_model保護等）。
- 確認できたが陳腐化・日付相違：3〜4件（優先順位の新旧表記、FEATURE_COLS_WT件数、モデル本数62→現状、reconcile_walkforward_tail.shの停止状態）。
- 明確な食い違い：2件（「42順序対」の数値矛盾／7H1「本命ラインは買い目から除外」が3着位置には不成立）。
- リンク切れ（ドキュメント整理の副作用でtrifecta_playbook.mdが参照する2文書が削除済み）：2件、かつ既存テストの穴（docs間参照は検査対象外）を発見。
- DBアクセスが必要で本監査では判定不能だった主張：約6件（picks_history件数・ROI、netkeirin_settings.enabled状態等）。
- 明白な方法論上の欠陥（学習窓=評価窓、確定オッズ先読み、生存者バイアス、無作為対照なし、探索窓での結論固定）は、精査した範囲では検出されなかった。
