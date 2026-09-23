# 波乱レース × 少点数 検証 — サブエージェント共通ルール（2026-09-23）

## 依頼主の目的
競輪（7車中心）で「波乱の可能性があるレースだけを選び、1点1000円・10点前後の三連単で高配当を当て、
**1日単位で回収率100%を超える日を作る**」構成を探す。通算回収率が100%に届かないことは既知。
主指標は **①回収率100%超えの日の割合（1日単位）②通算ROI ③無作為対照に対する勝ち方**。

## 現在の最良（ベースライン・統括が作成）
- レース選別: ロジスティック回帰（探索窓で学習）で「三連単50倍以上」確率を予測し、**1日の上位5本**を選ぶ。
  特徴（モデルのみ・朝に分かる）: asum(p3上位2合計), pwmax, gap23(p3の2位-3位), same_line(◎○同ライン),
  p3ent, pwent, n_lines, rp_sd(競走得点sd), race_type 上位8種ダミー。列 `k_us_モデルだけ(朝に分かる)` が日内順位。
- 買い目 G1''（8点）: ◎(p3 1位)1着 → 2着 指数3・4位 → 3着 ○(p3 2位)を除く残り。
- 成績（K=5・投資4万/日）: 探索 ROI 93.3%・100%超日 23.0% ／ 確認 ROI 93.4%・100%超日 26.4%。
  同じ形を無作為5本: ROI 69.6/65.3%・100%超日 20.4/19.9%。
- 他の形（全レース）はほぼ ROI 66〜75%。確率上位10点は波乱レースで最悪(52/67%)。
  探索窓で並びパターンを選ぶと 140%→59% に崩れた（過適合の典型）。

## データ（すべてローカル・DB 接続は不可。DB は使わない）
作業ディレクトリ: `/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki-dev-keirin/215ebb0d-e9b5-401f-a659-538e08cabeb6/scratchpad/ax/`
- `races5.pkl` — 7車 48,300R（2024-07〜2026-08-04）。1行1レース。主な列:
  `race_key, race_date(str), race_type, grade, venue_id, idx`(p3降順の車番リスト), `p3, pw`(idx順の honest WF 予測),
  `res`("a-b-c" 着順), `res_odds`(三連単確定オッズ倍率・払戻=倍率×100円/100円), `mkt`(確定三連単から周辺化した市場1着確率・idx順),
  `mark, line`(車番→ライン群), `rp`(idx順の競走得点), `win`("探索"/"確認"), 上記の選別特徴と `k_us_*`。
  ⚠️ 分析は `race_date>="2024-10-01"` かつ `res_odds.notna()` に限定（それ以前は払戻欠損が約4割）。
  ⚠️ 予測は 60特徴の四半期 walk-forward（`/Users/ysuzuki/GitHub/kiseki/keirin/data/exp_cache/wf_preds_*_f60_*.pkl`）。本番は70特徴。
- `odds.pkl` — dict race_key → {"a-b-c": 確定三連単倍率}（7車・約204/210目）。
- `ten.npz` / `ten_sub.pkl` — 日内上位10本のレースについて 210目×(PL確率, オッズ, 的中) の配列。並びは itertools.permutations(range(7),3) の指数順位。
- キャッシュ（読み取りのみ）: `/Users/ysuzuki/GitHub/kiseki/keirin/data/exp_cache/`
  - `highpay_trio_7_25_1e+06.pkl` dict race_key → {frozenset(3車): 三連複確定倍率}
  - `favbust_payouts.pkl`（7車）/ `favbust_payouts_n9.pkl`（9車 3,020R）: race_key → {order, trio, tf, trio_odds, tf_odds}
  - `wf_preds9_*.pkl`: 9車の honest WF 予測（race_key, frame_no, pp3, ppw 等）
  - 特徴量: `/Users/ysuzuki/GitHub/kiseki/keirin/data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl`
    （70特徴 `FEATURE_COLS_WT`・finish_order・line_group/line_pos・race_type 等。642MB）
- Python: **`/Users/ysuzuki/GitHub/kiseki/backend/.venv/bin/python`**（numpy2/pandas3。pickle はこれでしか読めない）。
  keirin の src を import する場合は `sys.path.insert(0,"/Users/ysuzuki/GitHub/kiseki/keirin")`（lightgbm が無ければ `uv run --with lightgbm` 等で工夫）。

## 絶対ルール
- **読み取り専用**。リポジトリ（/Users/ysuzuki/GitHub/kiseki 系・kiseki-dev 系）のファイルを変更・作成しない。git 操作禁止。DB 接続禁止。
- 自分の成果物は **`ax/<自分のID>/`** 配下だけに書く。共有ファイル（races5.pkl 等）は上書きしない。
- 窓: **探索 = 2024-10-01〜2025-12-31 / 確認 = 2026-01-01〜2026-08-04**。閾値・モデル・パターンは**探索窓だけで決め**、確認窓は1回だけ評価する。
- 採否の基準（すべて満たすこと）: 探索・確認の両窓で同じ向き ／ 同数を無作為に選んだ対照 20 seed に両窓で勝つ ／ 日クラスタ bootstrap の CI を併記。
  試した腕の数を必ず数えて報告し、多重比較の注意を書く。**確認窓だけ良い結果は「未確定」扱い**。
- 1点1000円の均等買い。点数は **6〜12点**。1日の本数は 3〜10本の範囲。
- 確定オッズを「選別」や「目の絞り込み」に使った場合は look-ahead の近似なので「*」を付けて区別する（払戻計算に使うのは可）。
- 1日の評価: 日ごとに Σ払戻/Σ投資。「100%超えの日」= 払戻 ≥ 投資の日の割合（買った日の中で）。
- 実データを1件表示して目視確認してから集計する（買い目と着順が正しく突き合っているか）。

## 報告（日本語・最終メッセージ = 統括への報告。目安 600〜1200字＋表）
1. 仮説と判定（🟢採用候補 / 🟡未確定 / 🔴否定）
2. 最良の腕の数値（両窓: 件/日・点数・的中率・ROI [CI]・100%超えの日・対照との比較）
3. 試した腕の数・限界
4. 次に試すべきこと（最大3つ）
詳細は `ax/<ID>/notes.md` に保存。
