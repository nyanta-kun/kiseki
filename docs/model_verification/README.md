# モデル検証の記録

一回性の検証・A/B・品質レビューが吐いた JSON。**モデル成果物ではない**ので
`backend/models/` から 2026-09-02 にここへ移した。

## なぜ分けたか

`backend/models/` に「推論時に読み込むモデル」と「過去の検証を回したときの記録」が
混ざっており、**どれを消してよいか判断できない状態**だった（61ファイル16MB）。
実測でコード・文書とも参照ゼロだったものだけをここへ移し、
`backend/models/` は「実際に読み込むもの＋そのメタデータ」に絞った。

判定に使ったコマンド:

```bash
for f in $(git ls-files 'backend/models/*.json' | xargs -n1 basename); do
  grep -rl "$f" backend/src backend/scripts keirin/src keirin/scripts scripts 2>/dev/null
done
```

## 中身

| ファイル | 何の記録か |
|---|---|
| `jra_rank_quality_review_2025.json` / `_2026.json` | JRA ランキング品質レビュー（年別） |
| `jra_feature_drop_ab_honest.json` / `_paddock.json` | 特徴量を落とした A/B。**paddock_index を除去しても改善しない**ことを示した検定（VAL 3,450R の paired bootstrap） |
| `jra_skew_drop3.json` / `_nodm_model.json` / `_trainserve.json` | train/serve skew 監査 |
| `v26_metrics_rank.json` | v26 の指標 |
| `chihou_rank_quality_review_nomarket.json` | 市場特徴を外した地方モデルの品質（v14 の根拠） |
| `chihou_cutoff_venue_*_20260701_20260731.json` | 地方の場別足切り検証（DB 集計 / honest 版の対比） |
| `chihou_prod_lgb*_metrics.json` | 旧世代モデル（無印 / v11 / v12）の学習時メトリクス |

## 🔴 消さなかったもの

`backend/models/` に残したのは**推論時に実際に読み込むモデルとそのメタデータ**。
特に次は削除しないこと:

- `chihou_prod_lgb.v14_39feat.txt` / `chihou_prod_lgb_win.v14_39feat.txt`
  — **本番**（`indices/chihou_calculator.py` が読む）
- `chihou_prod_lgb.v12_44feat.txt` / `chihou_prod_lgb_win.v12_44feat.txt`
  — **walk-forward の再現に要る**（`chihou_rebuild_walkforward.py` /
  `train_chihou_prod_lgb.py` が参照）
- `jra_reg_rank_lgb.txt` / `jra_out_rate_lgb.txt` / `v26_lightgbm_rank.txt`
  — JRA 本番（`indices/composite.py`）

同時に削除した旧世代モデル（参照ゼロを実測）:
`chihou_prod_lgb.v11_35feat.txt` / `chihou_prod_lgb_win.txt` /
`chihou_prod_lgb_win.v11_35feat.txt` / `v26_phase1_baseline.txt` /
`v26_phase1_extended.txt`。必要になったら git 履歴から復元できる。

⚠️ **新しい検証記録をここへ足すときは上の表に1行足すこと。**
何の記録か分からない JSON は、結局また「消してよいか判断できない」に戻る。
