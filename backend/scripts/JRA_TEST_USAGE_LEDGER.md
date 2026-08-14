# JRA TEST_START 使用履歴台帳

TEST_START（当四半期の初日・四半期ローリング）以降のデータを採否判断に使った記録。
同一期間を条件探索に使い回さないための追跡台帳。

定義は `backend/src/jra_protocol.py`。TEST 期間を使ったら
`jra_protocol.record_test_usage()` を呼んでここに 1 行追記すること。

---

## プロトコル制定（2026-08-14）以前に既に使われていた窓

中央には 2026-08-14 までプロトコルも台帳も無く、**2026 年の窓は少なくとも
以下 6 件の採否判断に使い回されている**。ここから得た結論は
「有意な改善」として扱わないこと（`jra_protocol.BURNED_DECISIONS` と同内容）。

| 判断 | 使ったもの |
|---|---|
| v27 合成係数 `V27_OUT_WEIGHT=0.5` の選定 | `composite.py`（「honest 2窓で比較」） |
| 着外率の足切り閾値 `OUT_PROB_CUTOFF=0.80` の選定 | `train_jra_out_rate.py` |
| v26→v27 の目的関数変更（LambdaRank → 順位回帰）の採否 | `train_jra_reg_rank.py` |
| ランキング品質の再設計提案 | `jra_rank_redesign_proposal.py` |
| `recommend_rank` の `market_agree` 第一分岐化 | `confidence.py` |
| train/serve 不整合の監査（DM・馬場・馬体重） | `jra_train_serve_skew_audit.py` |

⚠️ **さらに、2026-08-14 時点の本番モデルは全期間 refit** で作られており、
上記の評価はいずれも厳密には in-sample を含む。
refit 境界を `TEST_START` の前日に変えたのは同日（`docs/jra_rebuild_2026_08.md` 13章）。
**最初の真に honest な一度きり評価は 2026Q4 のローリング（2027-01 実行）になる。**

---

## 使用履歴
