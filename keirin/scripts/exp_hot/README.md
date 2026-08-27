# 好調予想家（netkeiba `/yoso/hot/`）の高額的中の逆解析 — 2026-08-27

記録: `keirin/docs/rival_hot_highpay_2026_08_27.md`

```bash
python3 fetch_hot.py                       # /yoso/hot/ の生HTML -> raw/hot.html
python3 run_hot.py                         # 高額的中30件の詳細 -> hot_hits.jsonl / hot_hits2.json
python3 fetch_month.py 20260801 20260827   # 10人の8月全商品（一覧API）-> month.jsonl（約4分）
```

| ファイル | 役割 |
|---|---|
| `fetch_hot.py` / `fetch_goods.py` | 生HTML・一覧JSONの取得（キャッシュ式。`exp_gensen` と同じ作法） |
| `parse_hot.py` | 買い目表を**1点=1行**まで展開（通常 / フォーメーション / 流し / ボックスの3形式に対応） |
| `run_hot.py` | 高額的中30件を `券種・点数・1点賭け金・的中倍率` へ分解 |
| `fetch_month.py` | 一覧の `購入金額 / 払戻 / 収支` だけで真の的中率・ROI を出す（詳細ページ不要） |

- ⚠️ 詳細ページに「予想の転載はお控えください」。**内部分析限定**とし買い目は再配信しない
- ⚠️ `/yoso/hot/` は**勝ちだけを見せる**。傾向を語るときは必ず `month.jsonl` の母集団と併記する
- 🔴 `parse_gensen.parse()` は「通常（1行=1点・金額バラバラ）」形式で点数を拾えない。
  点数・1点賭け金を数えるときは本ディレクトリの `parse_hot.parse_bets()` を使うこと
