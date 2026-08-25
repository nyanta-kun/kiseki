# exp_9axis — 9車の軸モデル再設計の検証（2026-08-24）

設計書: `keirin/docs/nine_car_axis_redesign.md`

## 実行順

```bash
python scripts/exp_9axis/build_features.py      # /tmp/feat_all.pkl（66特徴・全車数）
python scripts/exp_9axis/build_cache.py         # /tmp/diag9.pkl（vintage予測 + 実績 + ライン）
python scripts/exp_9axis/calib.py               # 較正・Σp3・p3順位別
python scripts/exp_9axis/discrim.py             # レース内 concordance（競走得点との比較）
python scripts/exp_9axis/residual_line.py       # ライン構造別の残差
python scripts/exp_9axis/residual_role.py       # ライン数×役割の相対残差
python scripts/exp_9axis/dependence.py          # 二軸の依存構造（同ライン/別ライン）
python scripts/exp_9axis/dependence_by_year.py  # 同上・年別（再現性）

python scripts/exp_9axis/base_model_ab.py       # ベースモデル4腕（約40分）
python scripts/exp_9axis/base_model_report.py

python scripts/exp_9axis/pair_axis2.py [--swap]      # 軸2の条件付きモデル
python scripts/exp_9axis/pair_axis2_roi.py [--swap]  # 本番の9Cの買い方で採点
python scripts/exp_9axis/gate_swap.py [--swap]       # レース選別も同時確率へ替える
```

## 作法

- 予測は必ず vintage walk-forward（`data/exp_cache/wf_preds9_*.pkl` / `wf_preds_*.pkl`）。
  backfill された `wt_entries.pred_top3_pct` を使うと look-ahead が入る
- ペアモデルの学習・検定は**年をまたぐ独立窓**（`--swap` で逆向きも必ず回す）
- ROI は均等配分で出す。本番 9C は `tilt_stakes` のダッチングなので**目安**
- 母集団は腕の間で完全に揃える（ゲートは現行の `p3_sum_top2` で判定して固定する）

## 追記（2026-08-25）— 開催まるごと網羅した前提の分析

```bash
python scripts/exp_9axis/coverage_split.py         # 全網羅での当たり／外れの切り分け
python scripts/exp_9axis/coverage_pair_by_group.py # 群別に軸2ペアモデルを当てる
python scripts/exp_9axis/axis_three_way.py         # 🔴 p3上位2車 / 本番の穴埋め / ペアモデル
```

🔴 **ベースラインは `p3上位2車` ではなく `submit_marquee_wt._axes()`（ライン組み替え）**。
穴埋め経路は既に組み替えているので、p3上位2車と比べると効果を過大に見積もる
（外れ群で +3.22pt → +1.24pt・有意でなくなる）。`axis_three_way.py` を既定にすること。

## 追記2（2026-08-25）— 安い配当の帯と買い方の腕

```bash
python scripts/exp_9axis/cheap_band.py        # 三連複5点 / 三連複2点 / ワイド1点 を帯別に比較
python scripts/exp_9axis/cheap_band_cross.py  # 想定平均払戻 × 信頼度 のクロス
```

🔴 **ワイド1点の的中条件は「二軸そろい」と定義上同じ**。的中率は最高になるが
平均2.06倍・中央1.70倍なので ROI は三連複5点に劣る。
🔴 払戻の帯分けは**確定オッズからの逆算**（ダッチングの逆算 `10000/Σ(1/o)`）なので
look-ahead を含む。結論は必ず**信頼度（較正後 p3_sum・発走前確定）の帯**でも確認すること。
