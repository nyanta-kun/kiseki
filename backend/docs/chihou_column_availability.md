# 地方（UmaConn）で「入っているが使えない」列の一覧

> **充足率は「値が入っている行の割合」であって「使える値の割合」ではない。**
> この取り違えで 2026-08〜09 に未着手リストへ誤った項目が載り続けた
> （`HANDOFF_2026-09-02.md` の項目9・訂正済み）。

UmaConn の差分 SE レコードは **379 バイト**で、JVLink のフル SE（555 バイト）より短い。
pos 379 より後ろのフィールドは空文字で返り `parse_se` が None に落とす
（`backend/src/importers/jvlink_parser.py:625-626`）。

## 使えない / 部分的にしか使えない列

| 列 | 見かけの充足 | 実態 | 出典 |
|---|---|---|---|
| `race_results.running_style` | 100% | 🔴 **全 411,645 行が `'0'` の定数**。pos553 のため UmaConn では構造的に取得不能。**特徴化は不可能** | 2026-08-29 実測 / 2026-09-02 再確認 |
| `race_results.passing_1` | 「97.9%」と誤記されていた | **64.5%**（`passing_4` は 88.4%） | 2026-09-02 実測 |
| `races.lap_times` | 100%（文字列として） | **73.8% が全ゼロ**。実データは南関中心の約26% | `chihou_exotic_type_lab_2026_08_29.md` |
| `races.condition` | — | 配信時は NULL（`_POST_RACE_ONLY_COLS`）。v14 の `is_bad`/`is_good` の gain が極小なのはこれが一因 | `chihou_race_importer.py:37-40` |
| `races.head_count` | 98.9% | 確定値。**発走前には入らない**ので配信側は登録頭数へフォールバックする。学習側は確定値を使っており **train/serve が約10%のレースでずれる**（下記） | 2026-09-02 実測 |
| `race_results.place_odds` | 期間による | 2026-04 以前は 1〜3着馬にしか入らず、**複勝 ROI の全期間検証は不可能** | `chihou_rebuild_2026_08.md:188-248` |

## 死んだ列を読んでいるコード（挙動は変えていない）

| 場所 | 状態 |
|---|---|
| `scripts/chihou_extra_indices.py` の `pace_fit` | `front_score = (4.0 - running_style)/3.0` が常に 1.333 になる。**2026-09-02 に分散が無いことを検出してニュートラルを返すガードを入れた**（定数から「脚質適性」を名乗る指数を作って配らないため） |
| `src/indices/chihou_calculator.py` `_dark_horse_batch` の ⑤前走後方 | `prev_style in ("3","4")` は常に False。実際に効いているのは `passing_1 > 70%頭数` の側だけ。**条件は残してコメントで明示**（UmaConn が配信し始めたら自然に効き出す） |

## 🟡 未修正: `head_count` の train/serve skew

- 学習側 `scripts/train_chihou_market_lgb.py:146,182` は `r.head_count`（**確定出走頭数**）を使い、
  さらに `head_count >= 6` で母集団を絞る
- 配信側 `src/indices/chihou_calculator.py:592` は
  `float(race.head_count or len(entries))` で、発走前は**登録頭数**にフォールバックする
- `head_count` は v14 の gain 第5位（38,431）
- 実測（2025年以降 25,424R）: `head_count` NULL は 1.1% だが、**登録頭数と確定頭数の
  不一致が 10.0%**。同じ値が `front_density` の分母と composite のレース内平均にも入る

→ v14 が市場特徴で直したのと**同型の不整合**。ただし直すには学習側の特徴を変える＝
**再学習が要る**ので、次の月次ローリングで A/B として扱うこと。
この文書を書いた時点では未修正。

## 追加するときの作法

新しい列を使う前に、**充足率ではなく分散**を見ること。

```sql
SELECT count(*), count(DISTINCT col), min(col), max(col) FROM chihou.race_results;
```

`count(DISTINCT col) = 1` なら、その列は入っていても使えない。
