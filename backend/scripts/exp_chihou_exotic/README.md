# chihou エキゾチック実測（2026-08-29）

結論と全実測表は `backend/docs/chihou_exotic_type_lab_2026_08_29.md`。

実行順（すべて Mac の scratchpad で回した。中間ファイルは pickle で受け渡す）:

```
01_dump_payouts.py       chihou.race_payouts + 1-3着の人気/オッズ → exotic.pkl (40,034R)
02_dump_runners.py       全出走馬の確定単勝オッズ・着順       → runners.pkl (409,283行)
03_market_structure_box.py  市場構造(q1/top3_share/entropy) + BOX買いのROI → race.pkl
04_trio_band_roi.py      全三連複の組を Harville で値付け → 帯別の実測ROI
05_payout_calibration_check.py 的中組の 実払戻 / 含意価格 の比 → d.pkl
06_trio_topk_product.py  三連複 確率上位k点 の商品指標（件/日・表示的中・ガミ）
07_trifecta_band_roi.py  三連単の帯別ROI（買い目 3,363万点）
08_fit_payout_model.py   予測払戻モデル（2次多項式較正）学習24-25 / 検証2026
09_model_vs_market_ranking.py  指数(honest WF) vs 市場 の並べ替え比較
10_perrace_plans.py      レース単位のプラン結果表 → perrace.pkl
11_upset_band_stability.py     波乱度×閾値のスイープ・CI・期間分割 → band.pkl
12_model_upset_increment.py    指数側の波乱度が市場に上乗せするか
```

指数の honest 予測は本番相当（市場特徴なし）で作る:

```
.venv/bin/python scripts/chihou_darkhorse_wf_build.py --no-market --out <SP>/wf_nomarket.csv   # 約7分
```

🔴 09/10/11/12 は **確定オッズで選別している**（look-ahead）。実運用条件の再測定は
`chihou_odds_query` の T−6分スナップで組み直すこと（2026-04-07 以降のみ可能）。

実運用条件での再測定（結論はこちら・§10）:

```
13_dump_prerace_odds.py     発走6分前の単勝オッズ（2026-04-07以降・5,245R）→ preodds.pkl
14_prerace_band_eval.py     発走前選別 vs 確定オッズ選別を同一レースで対照 → prerace_eval.pkl
15_prerace_screen_sensitivity.py  型の切り口（波乱度/上位3頭シェア/1番人気）を振る
```
