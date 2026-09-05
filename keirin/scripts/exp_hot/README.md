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

## 2026-09-06 — 好調予想家14人 × 26,602商品の商品設計逆解析

記録: `keirin/docs/highpay_5slots_2026_09_06.md` / 予想家ごと `reports/*.md`

```bash
python3 fetch_hot.py                                   # /yoso/hot/ を取り直す
python3 fetch_month2.py 20260801 20260905 month2.jsonl # 14人の全商品（一覧API）
NETKEIBA_INTERVAL=2.0 python3 profile.py 546 20260820 20260905 --stride 3
                                                       # 詳細ページ → prof/<yid>_<from>_<to>.jsonl
```

| ファイル | 役割 |
|---|---|
| `fetch_month2.py` | `fetch_month.py` の可変版（期間 × 予想家 × 出力先を引数で） |
| `profile.py` | 1人の商品構成（券種・点数・1点賭け金・形式）を全商品から実測 |
| `an_*.py` | 予想家グループごとの集計（サブエージェントが書いたもの） |
| `reports/*.md` | グループごとのレポート |

- `NETKEIBA_INTERVAL`（既定 0.8秒）で取得間隔を広げられる。**並列で回すときは 2.0 以上**
- 一覧の `li` から `公開日時` と `コメント` も取るようにした（入稿の運用と商品ティアが読める）
- ⚠️ `raw/` と `*.jsonl` は git 管理外（`.gitignore`）
