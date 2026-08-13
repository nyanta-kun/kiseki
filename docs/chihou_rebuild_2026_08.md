# 地方競馬 予想ロジック 再整理（2026-08 〜）

競輪で行っている予想ロジック見直しと同じ手順を地方競馬（chihou）にも適用する。
本ドキュメントを**今回の作業の唯一の作業台帳**とし、フェーズが進むたびに追記する。

- 前提: **スクレイピング（取得）データは資産として維持する。生成済みデータ
  （`calculated_indices` / 学習済みモデル / バックフィル結果）は破棄・再構築しうる。**
- 先行ドキュメント: `docs/chihou_logic_review_2026_08_02.md`（前回の検討リスト。
  Phase 0〜2 の判定はそちらが正）
- プロトコル: `backend/src/chihou_protocol.py`（TRAIN_END / VAL / TEST_START の定義）

---

## 0. フェーズ計画

| # | フェーズ | 状態 | 内容 |
|---|---|---|---|
| **P0** | **取得データ監査** | ✅ **完了（1〜5章）** | 期間別の取得可否・欠測・不整合の確定 |
| **P1** | **欠測の回収** | 🔄 **進行中（8章）** | 原因特定と importer ガードは完了。回収の実行は承認待ち |
| P2 | 生成データの棚卸しと再構築 | 未着手 | `calculated_indices` 13世代の整理、サブ指数の再算出方針 |
| P3 | train/serve 整合の是正 | 未着手 | 市場特徴のライブ無効化問題（5.4 節）の解消 |
| P4 | 特徴量・目的関数の再検討 | 未着手 | 未使用列の活用、ばんえい・複勝の扱い |
| P5 | 推奨カテゴリの再設計 | 未着手 | sweet_spot / place_bet の honest 再評価 |

---

## 1. データ源の全体像

「スクレイピング」と一括りにされがちだが、chihou には**性質の違う3系統**がある。
壊れ方も回収方法も別なので、まず分けて考える。

| 系統 | 取得元 | 格納先 | 取得方式 | 実行場所 |
|---|---|---|---|---|
| **A. 本体データ** | UmaConn SDK（NVDTLab.dll） | `chihou.races` / `race_entries` / `race_results` / `race_payouts` / `horses` | COM（スクレイピングではない） | Windows VM `umaconn_agent.py` |
| **B. オッズ時系列** | UmaConn SDK（速報） | `chihou.odds_history` | COM ポーリング | 同上（realtime モード） |
| **C. 外部指数** | netkeiba / 吉馬 の**Webスクレイピング** | `sekito.netkeiba` / `sekito.kichiuma` | HTTP スクレイプ | **sekito リポジトリ**（`backend/scripts/bin/scrape/*`）・VPS |

- **C は kiseki リポジトリの管轄外**。壊れているときの修理は `~/GitHub/sekito` 側で行う。
- `sekito.anagusa`（穴ぐさ）は**中央専用**で、地方場のレコードは 0 件。地方の特徴量には
  一切入っていない（CLAUDE.md の記述どおり）。
- 取得の成否は `sekito.data_fetch_status`（date × course × race × data_type）に記録される。
  **C 系統の障害診断はまずこのテーブルを見ること**（4.4 節）。

### モデルが実際に使っている外部データは 2 列だけ

44特徴のうち外部指数由来は 5 本（`kc_sp_z` / `nk_idx_z` / `kc_rank_n` / `nk_rank_n` /
`ext_missing`）で、その入力は

- `sekito.kichiuma.sp_score`
- `sekito.netkeiba.idx_ave`（**`is_time_index = true` の行に限定**）

の 2 列のみ。`sp_trust` / `sp_adjust` / `sp_max` / `sueashi` / `senko`、netkeiba の
`idx_max` / `idx_distance` / `idx_course` / `p_rank` / `sire` などは**取得済みだが未使用**。
学習側 `train_chihou_prod_lgb.add_external_features` と serve 側
`chihou_calculator._fetch_external_raw` は同条件で、parity は取れている。

---

## 2. 監査結果サマリ（期間 × 使用可否）

🟢 使用可 / 🟡 条件付き / 🔴 使用不可

| データ | 2023 | 2024上 | 2024下 | 2025上 | 2025下 | 2026上 | 2026-06〜 |
|---|---|---|---|---|---|---|---|
| `races` / `race_entries` | 🔴 皆無 | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 |
| `race_results` | 🔴 | 🟡 欠損日あり | 🟡 欠損日多 | 🟡 | 🟡 | 🟡 | 🟢 |
| `race_payouts`（払戻） | 🔴 | 🔴 | 🔴 ほぼ皆無 | 🔴 | 🔴 | 🟢 2026-01〜 | 🟢 |
| `race_results.place_odds` | 🔴 | 🔴 0% | 🔴 0% | 🔴 0% | 🔴 0% | 🟡 28%→100% | 🟢 |
| `odds_history`（時系列） | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 | 🟢 2026-04-07〜 | 🟢 |
| `kichiuma.sp_score` | 🟡 H1のみ | 🟢 | 🟡 **2024-07 皆無** | 🟢 | 🟢 | 🟢 | 🟢 |
| `netkeiba.idx_ave`（タイム指数） | 🔴 | 🔴 未提供 | 🔴 未提供 | 🔴 未提供 | 🟢 2025-06〜 | 🟢 〜2026-05 | 🔴 **崩壊** |
| `chihou.pedigrees`（血統） | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 **全期間 0 件** |

### 結論として「揃っている」窓

| 用途 | 使ってよい期間 | 制約 |
|---|---|---|
| 基本特徴（速度・騎手・調教師・コーナー等）の学習 | **2024-01-01 〜 現在** | 結果欠損 482レースを除く |
| 外部指数 5特徴を含む学習 | **kichiuma のみなら 2024-01〜**（2024-07 除く）<br>**netkeiba も含めるなら 2025-06 〜 2026-05 の 12か月のみ** | netkeiba は前後とも欠測 |
| 単勝 ROI / EV 検証 | 2024-01 〜 現在 | `race_results.win_odds` は全期間 100% |
| **複勝 ROI 検証** | **2026-04 以降のみ**（実質 2026-05〜） | それ以前は `place_odds` が存在しない |
| 3連単等の払戻検証 | **2026-01 以降のみ** | `race_payouts` が 2026 年からの運用 |
| 発走前オッズ（時系列）を使う検証 | **2026-04-07 以降のみ** | 68.5M 行・9.9GB |

> 🔴 **最重要**: 「複勝」を扱う検証（`place_bet` / `upset_place` / `place_ev_index`）で
> 使える実データは **2026-04 以降の約 4 か月しかない**。しかも既存の複勝バックテストには
> **`place_odds` の欠損パターンに起因する重大な上方バイアスがある**（3.5 節で実証）。
> CLAUDE.md の「place_bet 複勝ROI 0.972（2024-10〜2026-07 walk-forward）」は
> **無効として扱うこと**（6章 課題 #1）。

---

## 3. 本体データ（A系統）の監査

### 3.1 カバレッジ

| 項目 | 実測 |
|---|---|
| レース総数 | 40,030（うち 2024-01-01 以降 39,983） |
| 期間 | **2024-01-01 〜 2026-08-14**（連続。欠落月なし） |
| 競馬場 | 15（NAR 14場 + `course='83'` = ばんえい帯広） |
| 出走馬エントリ | 409,397 |
| 確定結果 | 402,758 |
| 馬マスタ | 26,160 |
| 重複レース `(date, course, race_number)` | **0** ✅ |
| `umaconn_race_id` NULL | **0** ✅ |

- **2021-10-05 に孤立レコードが 47 レース存在する**（門別5・盛岡10・大井12・金沢11・笠松9、
  entries 478 / results 478）。内部的には完結しているが期間外の試験投入と思われる。
  日付フィルタ（`>= 20240101`）で常に除外されるため実害はないが、削除して構わない。
- **2023 年のデータは 1 件も無い**。学習開始が実効 2024-01-01 なのは
  CLAUDE.md にある「`calculated_indices version>=9` が 2024 年からしかない」ためだけでなく、
  **そもそも本体データが存在しない**ため。2023 年へ遡るなら UmaConn の
  `--mode fetch-results` 等でレース本体から取り直す必要がある
  （外部指数は 2023 H1 しか無いので、遡っても外部指数付きになるのは半年分だけ）。

### 3.2 結果の取得抜け — **482 レース / 49 日**

> 📌 **8.1 で再分類済み。** このうち **346 レースは「開催中止」で取得抜けではない**。
> 真に回収すべきは **425 レース**（内訳と根拠は 8.1）。以下は分類前の生の観測。

`race_entries` はあるのに `race_results` が 1 行も無いレース（未来分・ばんえい除く）。
**日 × 場 まるごと 12 レース単位で欠ける**パターンが支配的で、
「その日その場の結果取得ジョブが走らなかった」ことを示す。

| 規模 | 日付 |
|---|---|
| 全場丸ごと（4〜5場） | **2024-07-24, 2024-07-25, 2024-09-03, 2024-09-28, 2024-09-29, 2024-09-30, 2024-10-22** |
| 1場丸ごと（12R） | 2024-08-12 盛岡, 2024-08-16 大井, 2024-08-29 佐賀, 2024-12-16 水沢, 2025-04-26(ばんえい), 2025-12-15(ばんえい) |
| 部分（1〜14R） | 2024-01-24, 2024-02-05, 2024-02-22, 2024-03-10, 2024-06-03, 2024-06-18, 2024-07-09, 2024-08-04, 2024-08-14, 2024-08-19, 2024-09-16, 2024-12-29, 2025-03-17, 2025-03-19, 2025-04-02, 2025-04-11, 2025-05-24, 2025-06-25, 2025-07-01, 2025-07-10, 2025-07-30, 2025-08-18, 2025-08-30, 2025-09-01, 2025-09-11, 2025-12-14, 2025-12-26, 2026-04-04, 2026-04-20, 2026-05-21, 2026-06-25, 2026-06-26, 2026-07-12, 2026-07-23, 2026-07-24, 2026-07-28 |

- 集中しているのは **2024-07 〜 2024-10**（この4か月で 300 レース超）。
- 2025 年以降は 1 日あたり数レースの散発に減っており、
  `kiseki-UmaConn-FetchResults`（5分おき）導入後は改善している。
- 回収手段: `umaconn_agent.py --mode fetch-results --fetch-date YYYYMMDD`。
  **UmaConn 側の保持期間内かどうかは未確認**。1 日試して可否を確定させること（P1）。

### 3.3 head_count と entries の不一致（**正常**）

3,939 レース（約10%）で `entries` が `head_count` を **1〜4 頭上回る**。逆方向は 0 件。
これは**出走取消・発走除外馬が entries に残る**ためで、仕様どおり。
本番 `chihou_recommender.rank_by_hn` は出走予定馬全体で順位を確定するので、
**バックテスト側も LEFT JOIN で母集団を揃えること**（CLAUDE.md の生存者バイアス監査で確定済み）。

### 3.4 列の充足率

`chihou.races`（2024-01 以降・ばんえい除く）

| 列 | 充足 | 備考 |
|---|---|---|
| `post_time` / `prize_1st..3rd` / `lap_times` / `race_type_code` | **100%** | 未使用（活用余地あり） |
| `condition` / `weather` | 97〜99% | ばんえいのみ `condition` 24%（水分率が別体系） |
| `finishers_count` | 97〜99% | |
| `grade` | 24% | 重賞のみ |
| `first_3f` / `last_3f_race` | **21〜29%** | **場依存**（下記） |

`last_3f_race`（レース単位の上がり3F）は**南関4場でしか取れていない**:
浦和 97.9% / 船橋 99.3% / 大井 98.1% / 川崎 97.3% / 園田 15.1% / **その他 9場は 0%**。
一方 `race_results.last_3f`（馬単位）は**全場 100%** 取れているので、
レース単位の値は馬単位から導出可能。レース単位列に依存する処理を作らないこと。

`chihou.race_results`（2024-01 以降・ばんえい除く・完走馬）

| 列 | NULL 率 | 判定 |
|---|---|---|
| `win_odds` / `win_popularity` / `finish_time` / `last_3f` / `running_style` / `horse_weight` | 0.0〜0.1% | 🟢 |
| `passing_1..4`（コーナー通過順） | **26〜28%** | 🟡 **場によって激しく偏る** |
| `margin`（着差） | **100%** | 🔴 **全期間・全場で空。使用不可** |
| `place_odds` | 2024/2025 は 100% | 🔴 → 2026-05 以降 🟢 |

**`passing_*` の場別 NULL 率**（コーナー特徴 `c_early_n` / `c_late_gain_n` / `c_makuri_n` の入力）:

| 場 | NULL率 | | 場 | NULL率 |
|---|---|---|---|---|
| 盛岡 | **95.6%** | | 園田 | 15.0% |
| 門別 | **80.9%** | | 浦和 | 15.5% |
| 大井 | **61.4%** | | 名古屋 | 7.8% |
| 船橋 | **56.7%** | | 佐賀 | 4.1% |
| 川崎 | 20.5% | | 笠松 | 3.7% |
| 水沢 | 20.4% | | 金沢 | 2.9% |
| 姫路 | 18.1% | | 高知 | 1.1% |

> ⚠️ **Phase6 のコーナー特徴 9本は「場によって効いたり効かなかったりする」特徴である。**
> 盛岡・門別・大井・船橋では実質ほぼ欠損値（中立値埋め）で動いている。
> A/B で全体平均が改善していても、**場別に見ると南関・門別・盛岡では寄与ゼロ**のはず。
> P4 で場別に効果を分解して確認すること。

### 3.5 🔴 複勝バックテストの上方バイアス（実証済み）

`place_odds` の欠損は**ランダムではなく着順と強く相関している**。
HR 払戻（`race_payouts`）由来の補完は**1〜3着馬にしか値を与えない**ためである。

`place_odds` 充足率（ばんえい除く・完走馬）:

| 期間 | 1-3着 | 4着以下 |
|---|---|---|
| 2024-01 〜 2025-12 | 0.4% | 0.0% |
| **2026-01 〜 2026-03** | **98.3%** | **0.0%** |
| 2026-04 〜 | 99.8% | 93.3% |

一方、複勝 ROI を計算している 3 スクリプトはいずれも
**`place_odds` が NULL の行を黙って落としてから ROI を出している**:

- `scripts/chihou_rebuild_walkforward.py:309` — `valid = sub[sub["place_odds"].notna()]`
- `scripts/backtest_chihou_sweetspot.py:80` — 同上
- `scripts/aggregate_chihou_recent.py:176` — 同上

**2026-01〜03 の期間では、この絞り込みが「4着以下の馬を全て捨てて 1〜3着馬だけ残す」
操作と等価になる。** すなわち母集団 `valid` の的中率が定義上ほぼ 100% になり、
`ROI = Σ(的中馬の複勝オッズ) / len(valid)` は**複勝オッズの平均値そのもの（2〜5倍）**に
なる。2024-10〜2025-12 の行はほぼ全て捨てられ、残るのはこの偏った 3 か月と
クリーンな 2026-04 以降だけ。

> **したがって「place_bet 複勝ROI 0.972」「複勝ROI 1.046」等、
> `place_odds` を使って算出された既存の数値はすべて信頼できない。**
> P5 で **2026-04 以降のみ**を母集団として再計算すること
> （それでも 4 か月・n は大幅に減る）。
> 併せて 3 スクリプトに「`place_odds` 欠損率が着順帯で偏っていたら中断する」
> ガードを入れること。落として黙って進むのが事故の直接原因である。

**バイアスの大きさ（実測）**: 2026-01〜03 の完走馬全体（ばんえい除く）で
旧コードと同じ計算をすると:

| 指標 | 値 |
|---|---|
| `place_odds.notna()` 後の母集団 | 8,579 行 |
| そのうち 1〜3着 | **8,579 行（100.0%）** |
| naive 複勝ROI | **2.440** |
| レース単位で全馬揃っているレース | **0 / 2,910** |

的中率が定義上 100%、ROI は複勝オッズの平均そのもの。**この期間の複勝 ROI は
数値としての意味を持たない。**

**対応（本ブランチで実施済み）**: `src/services/chihou_place_odds_guard.py` を新設し、
**レース単位で全出走馬の複勝オッズが揃っているレースだけ**を ROI の母集団にする。
期間で切るのではなく充足そのものを見るので、データが増えれば自動的に対象が広がる。
`chihou_rebuild_walkforward.py` / `backtest_chihou_sweetspot.py` /
`aggregate_chihou_recent.py` の 3 本に適用し、監査結果（着順帯別の充足率と
落としたレース数）を必ず出力するようにした。

⚠️ **払戻を 2024 年まで遡ると、この壊れ方が 2024〜2025 にも広がる**
（HR は 1〜3着しか持たないため同じ形の欠損が作られる）。
**バックフィルの前に本ガードを入れること**が必須。

`chihou.odds_history`（2026-04-07 〜）から発走前最終スナップを引けば全馬の複勝オッズが
取れるので、**2026-04 以降については `place_odds` の NULL を埋めきることが可能**
（`backfill_chihou_place_odds.py` が既にその実装）。それ以前は**恒久的に回復不能**。

### 3.6 `chihou.horses` は薄い

26,160 頭。`name` / `sex` / `birthday` は 100% だが、
**`coat_color` / `owner` / `breeder` は全件 0%**。
`chihou.pedigrees` は **0 件**（テーブルはあるが空）＝ **地方に血統特徴は一切存在しない**。
`chihou_pedigree_importer.py` は実装されているが一度も実効していない。

### 3.7 ばんえい帯広（`course='83'`）の扱い

- 4,536 レース（全体の 11%）。`surface='90'` / `distance=200` で判別可能。
- `chihou_calculator.calculate_and_save` は `BANEI_COURSE_CODE` でスキップ、
  学習クエリも `r.course != '83'` で除外 → **完全に対象外**。
- `course_name` が `'83'` のまま未解決なのは、`sekito.racecourse` の帯広（`NOBH`）の
  `netkeiba_id` が **`65`** で、UmaConn の場コード **`83`** と体系が違うため。
  ばんえいを扱う気がないなら実害はないが、**マッピング欠落であることは記録しておく**
  （扱う場合は 200m 専用の別モデルが必要。現行 44特徴は流用不可）。

---

## 4. 外部指数（C系統・スクレイピング）の監査

### 4.1 kichiuma — 概ね健全

| 期間 | 状態 |
|---|---|
| 2023-01 〜 2023-06 | 取得あり（本体データが無いので使えない） |
| 2023-07 〜 2023-12 | 皆無 |
| 2024-01 〜 2026-08 | 🟢 供給率 **93〜98%**・全14場 |
| **2024-07** | 🔴 **1 行も無い（完全欠測・1か月まるごと）** |
| 2024-03 / 2024-06 | 🟡 21日 / 26日分のみ（75% / 82%） |
| 2025-10 / 2026-04 / 2026-05 | 🟡 85〜95% |

`sp_score` / `sueashi` / `senko` の内部充足率は一貫して 93〜98% で、劣化トレンドは無い。

> CLAUDE.md の「kichiuma は 95%→76% に劣化中で要監視」という記述は
> **現時点の実測と一致しない**（2026-06/07 はいずれも 100%）。当時の一時的な落ち込みが
> 回復したものと見られる。記述を更新すること。

### 4.2 netkeiba — 二重の問題

**(a) タイム指数はそもそも 2025-06 からしか無い**

モデルが使う `nk_idx` は `is_time_index = true` の行に限定されるが、
`is_time_index` が立った行が出るのは **2025-05（部分・620行）→ 2025-06（本格・12,232行）** から。
**2024-01 〜 2025-04 は `is_time_index` が 1 行も無い＝ nk_idx は全件 NULL。**

さらに元テーブル自体も **2024-02 〜 2024-07 の 6 か月が完全欠測**。

→ **`nk_idx_z` / `nk_rank_n` は学習期間の大半で「欠損時の既定値」しか入っていない。**
（`nk_idx_z`=0.0 / `nk_rank_n`=0.5 の定数）

**(b) 2026-06-08 にスクレイパーが停止した**

| 月 | 行数 | 取得日数 | 場数 |
|---|---|---|---|
| 2026-04 | 10,878 | 29 | 12 |
| 2026-05 | 10,839 | 29 | 13 |
| **2026-06** | **3,300** | **10** | 10 |
| **2026-07** | **691** | **4** | 5 |
| **2026-08** | **369** | **2** | 3 |

取得できている日を並べると停止の形がはっきり出る:

```
2026-06-01 .. 06-05, 06-07, 06-08   ← ここまで日次で動いていた
2026-06-15, 06-22, 06-29,
2026-07-06, 07-13, 07-20, 07-27,
2026-08-03, 08-10                    ← 以降は「月曜だけ・2場だけ」
```

**日次ジョブが 2026-06-08 に死に、週次（月曜）の別ジョブだけが生き残っている。**

### 4.3 sekito 側で同時に死んでいる他のスクレイパー

`sekito.data_fetch_status` を見ると、**netkeiba だけでなく 3 系統が別々に落ちている**。

| data_type | 2026-04 | 2026-05 | 2026-06 | 2026-07 | 2026-08 |
|---|---|---|---|---|---|
| `kichiuma` | 1,080 ✅ | 1,050 ✅ | 1,208 ✅ | 1,256 ✅ | 443 ✅ |
| `netkeiba_time_index` | 1,041 | 1,038 | **318** | **76** | **38** |
| `netkeiba_blood` | 1,048 | **60** | **76** | **76** | **38** |
| `netkeiba_data_analysis` | 1,048 | **59** | **73** | **73** | **38** |
| `horse_weight` | 295 | 65 | **0（全て not_available）** | **0** | **0** |
| `results` | 1,137 | **41** | **0** | **0** | **0** |

- **`fetch_failed` はほとんど無く、そもそも「取りに行っていない」**。
  → 障害は取得処理ではなく **enqueue / スケジューラ側**。sekito の
  `backend/scripts/scheduler-control.js` と `bin/scrape/netkeiba*` を疑う。
- **崩壊時期が data_type ごとに違う**（blood/data_analysis は 2026-05、
  time_index は 2026-06、results/horse_weight は 2026-05〜06）。単一原因ではない可能性。
- **`horse_weight` と `results` の死は chihou には影響しない**
  （chihou は UmaConn 由来で `race_entries.horse_weight` 充足 92〜95%、
  `race_results` も UmaConn 経由）。**sekito（中央）側の問題**。
- 復旧後の遡り取得手段は `backend/scripts/bin/scrape/backfill-all-data` /
  `refetch-pending-data` が存在する。**netkeiba 側に過去日の指数が残っているかは要確認**
  （タイム指数は開催後しばらくで消える可能性がある）。

### 4.4 外部指数の場別カバレッジ（健全期 2025-06〜2026-05）

全 14 場で netkeiba 89〜100% / kichiuma 92〜100%。**特定の場だけ落ちている構図は無い**。
（最低: 門別 netkeiba 89.3% / 盛岡 kichiuma 92.4%）

### 4.5 anagusa は地方に存在しない

`sekito.anagusa` の地方場（`course_code LIKE 'N%'`）レコードは **0 件**。
JRA 側の `has_anagusa` / `SIGNAL_ANAGUSA_ELITE` 相当のものは地方には無い。

---

## 5. 生成済みデータ（再構築対象）の現状

### 5.1 `calculated_indices` — 13 世代が全部残っている

3,339,509 行 / **855MB**。version 1〜13 が同居している。

| version | 行数 | レース数 | 期間 | 位置づけ |
|---|---|---|---|---|
| 1〜9 | 各 17〜35万 | 各 1.6〜3.4万 | 2024-01 〜 2026-05 | **死蔵**（v9 のみサブ指数取得元として学習クエリが参照） |
| 10 | 349,968 | 33,811 | 2024-01 〜 2026-07-02 | 旧本番 |
| 11 / 12 | 3,176 / 10,929 | 305 / 1,079 | 2026-07 のみ | 短命 |
| **13** | **358,845** | **34,541** | **2024-01 〜 2026-08-13** | **現本番** |

- v13 のバックフィルは実施済み（CLAUDE.md の「v10 で止まっている」は解消済み）。
- **v13 のカバレッジは 97〜99% で、欠けている分は 3.2 節の結果欠損日と一致する**
  （例: 2024-09 は 1,013 / 1,189 = 85%）。結果を回収すれば v13 も埋まる。
- **v1〜9・11・12 は削除候補**。ただし学習クエリ
  （`train_chihou_market_lgb.BASE_QUERY`）が `version >= 9` の DISTINCT ON で
  **サブ指数（speed/last3f/jockey/rotation/last_margin）を v9 から取っている**ため、
  **v9 を消すと学習が動かなくなる**。整理する場合はサブ指数の供給元を先に v13 へ寄せること。
- `last_margin_index` の NULL 率は 2024 年 11.0% / 2025 年 3.9% / 2026 年 3.7%。
  それ以外のサブ指数は全期間 0%。

### 5.2 `odds_history` は 10GB ある

68,455,423 行 / 9,943MB。2026-04-07 04:43 〜。`bet_type` は **`win` / `place` の2種のみ**。
1 レースあたり約 1,400 スナップショット（1日 48R で約 14 万行）。
このペースだと**年間およそ 30GB 増える**。P2 で保持方針（間引き・パーティション・
発走前 N 分のみ残す等）を決めること。

### 5.3 `race_payouts` は 2026 年からの運用

2026-01 以降は 99〜100% 充足（8種: win/place/bracket/quinella/exacta/wide/trio/trifecta）。
2025 年以前は 2024-07 の 12 レースと 2024-10 の 96 レースを除いて**皆無**。

### 5.4 🔴 市場特徴がライブでは無効化されている（train/serve 不整合）

**これは監査中に見つかった最大の実装上の問題**であり、P3 で扱う。

`ChihouIndexCalculator.calculate_and_save(race_id, odds_map=None)` は
`odds_map` が None のとき `_fetch_win_odds()` で **`chihou.race_results.win_odds`**
（＝レース確定後にしか入らない値）を読む。

そして **コードベース全体で `odds_map` を渡している呼び出しは 1 つも無い**
（`src/api/chihou_import_router.py::_run_chihou_calculate` / `import_router.py` /
`scripts/chihou_backfill_indices.py` すべて省略）。

⚠️ **タイムスタンプの TZ が列によって違う**（読み違えの罠。実際に一度誤読した）:

| 列 | TZ | 根拠 |
|---|---|---|
| `chihou.race_results.created_at` | **JST** | 大井1R post_time 15:30 → created_at 15:43（10レース全て発走+13〜25分） |
| `chihou.calculated_indices.calculated_at` | **UTC** | 12:30 = VPS cron `30 21`（21:30 JST）と一致 |

VPS crontab（実見・サーバは JST）:

```
30 21 * * *  chihou_calculate_trigger.sh            # 21:30 JST — 当日分
 0 22 * * *  chihou_calculate_trigger.sh tomorrow   # 22:00 JST — 翌日分
```

つまり **同じレースの v13 行が 2 回書かれ、後の 1 回が前の 1 回を上書きする**:

| 実行 | 対象 | 実行時点の `race_results` | 市場特徴 |
|---|---|---|---|
| **前夜 22:00 JST** | 翌日のレース | **0 件（未実施）** | 🔴 **中立値** |
| **当日 21:30 JST** | その日のレース | **57/57 が確定済み**（2026-08-12 実測） | 🟢 確定オッズ入り |

→ **当日ユーザーに提示されているのは前夜 22:00 の「オッズなし」版**。
その状態では `odds_rank_n` = 0.5 固定・`is_heavy_fav` = 0 固定・`is_dark_horse` = 0 固定、
`speed_mkt_gap` / `kc_mkt_gap` は `0.5 − speed_rank_n` / `0.5 − kc_rank_n` に退化して
オッズ非依存の量になる（＝市場5特徴が実質死んでいる）。

→ **その値は当日 21:30 に確定オッズ入りで上書きされ、DB にはそちらだけが残る。**
`scripts/inference_chihou_v13.py` によるバックフィルも `race_results.win_odds`
（確定オッズ）を使う。したがって:

> **DB に残っている `composite_index` は「確定オッズ入り」版であり、
> 当日ユーザーに提示された「オッズなし」版とは別物。**
> API（`chihou_races_router`）は DB の値をそのまま返すため、
> **過去分を使った再現検証は本番の再現になっていない。**

CLAUDE.md の「v12(44特徴) が v10 に対して top1勝率 +6.6pt、その差の実体は市場（オッズ）特徴」
という結論は、**確定オッズを与えた条件での比較**であり、**ライブの改善量を意味しない**。

**参考数値の扱いに注意**: 2026-08-12 の v13 指数と確定オッズ順位の rank 相関 **0.988**
（n=559）は、上表のとおり**確定オッズ入りで上書きされた後の値**を見たものなので、
「オッズ無しでも市場を再現できている」証拠には**ならない**。
**当日提示版（オッズなし）の品質は現時点で一度も測られていない。**
P3 で「同一レースを odds あり / なしで算出して差分を取る」直接比較を行うこと。

---

## 6. 検出した課題一覧（優先度順）

| # | 課題 | 影響 | 対応フェーズ |
|---|---|---|---|
| **1** | **複勝バックテストに構造的な上方バイアス**（3.5節・実証済み）。`place_odds` の欠損が着順と相関しているのに NULL 行を黙って落としている。既存の複勝 ROI は全て無効 | 🔴 致命 | P5 |
| **2** | **市場5特徴がライブで無効**（5.4節）。train/serve 不整合 | 🔴 致命 | P3 |
| **3** | netkeiba スクレイパーが 2026-06-08 から停止。blood/data_analysis は 2026-05 から | 🔴 高 | P1（sekito リポジトリ） |
| **4** | `nk_idx` は 2025-06 より前が全件 NULL。外部指数2本が学習期間の大半で定数 | 🟡 中 | P1 / P4 |
| **5** | 結果取得抜け 482レース / 49日（2024-07〜10 に集中） | 🟡 中 | P1 |
| **6** | kichiuma 2024-07 が 1か月まるごと欠測 | 🟡 中 | P1 |
| **7** | `passing_*` が場によって最大 95.6% 欠損 → コーナー特徴9本が場依存 | 🟡 中 | P4 |
| **8** | `chihou.pedigrees` が 0 件（血統特徴なし） | 🟡 中 | P4 |
| **9** | `race_results.margin` が全期間 100% NULL | 🟢 低 | P2（列削除 or 補完） |
| **10** | `calculated_indices` に 13 世代 855MB が滞留。v9 が学習の依存先 | 🟢 低 | P2 |
| **11** | `odds_history` 10GB・年 +30GB ペース。保持方針なし | 🟢 低 | P2 |
| **12** | ばんえい（`course='83'`）が `sekito.racecourse` と紐づかない（65 vs 83） | 🟢 低 | P2 |
| **13** | 2021-10-05 の孤立 47 レース | 🟢 低 | P2（削除） |
| **14** | `chihou_calculate_trigger.sh` のコメントが「13:00 UTC = 22:00 JST」等と書いているが VPS は **JST 運用**。TZ 記述が実態と食い違う（`calculated_at` は UTC で保存されるため二重に紛らわしい） | 🟢 低 | P3 |

### 次に確定させること（P0 の残タスク）

1. ~~**課題#1 の裏取り**~~ → **実施済み。3.5 節のとおりバイアスを実証した**（3 スクリプトとも
   `place_odds.notna()` で黙って除外していた）。既存の複勝 ROI 数値は破棄する。
2. **課題#5 の回収可否**: `umaconn_agent.py --mode fetch-results --fetch-date 20240724` を
   1 回試し、UmaConn の保持期間内かを確定させる。
3. **課題#3 の切り分け**: sekito 側で `netkeiba_time_index` の enqueue が止まった原因を
   特定し、過去日の遡り取得が可能かを確認する。

---

## 7. 再現用クエリ

本章の数値はすべて VPS PostgreSQL `hrdb` への直接クエリで得た。再実行手順:

```bash
cd /Users/ysuzuki/GitHub/kiseki-wt/chihou/upset-model
set -a && . ./.env && set +a
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME"
```

主要クエリ:

```sql
-- 月次カバレッジ
SELECT substr(date,1,6) ym, count(*) races, count(DISTINCT date) days,
       count(DISTINCT course) venues
FROM chihou.races GROUP BY 1 ORDER BY 1;

-- 結果の取得抜け（未来・ばんえい除く）
SELECT r.date, count(*) missing, count(DISTINCT r.course) venues
FROM chihou.races r
LEFT JOIN (SELECT DISTINCT race_id FROM chihou.race_results) s ON s.race_id = r.id
WHERE s.race_id IS NULL AND r.date >= '20240101'
  AND r.date < to_char(now(),'YYYYMMDD')
GROUP BY 1 ORDER BY 1;

-- 外部指数の供給率（出走馬単位・月次）
WITH base AS (
  SELECT r.date, re.horse_number, rc.code cc, r.race_number
  FROM chihou.races r
  JOIN chihou.race_entries re ON re.race_id = r.id
  JOIN sekito.racecourse rc ON rc.netkeiba_id = r.course
  WHERE r.date >= '20240101' AND r.course <> '83'
)
SELECT substr(b.date,1,6) ym, count(*) entries,
  round(100.0*count(*) FILTER (WHERE nk.is_time_index)/count(*),1) nk_time_pct,
  round(100.0*count(kc.horse_no)/count(*),1) kc_pct
FROM base b
LEFT JOIN sekito.netkeiba nk ON nk.course_code=b.cc
  AND nk.date=to_date(b.date,'YYYYMMDD')
  AND nk.race_no=b.race_number AND nk.horse_no=b.horse_number
LEFT JOIN sekito.kichiuma kc ON kc.course_code=b.cc
  AND kc.date=to_date(b.date,'YYYYMMDD')
  AND kc.race_no=b.race_number AND kc.horse_no=b.horse_number
GROUP BY 1 ORDER BY 1;

-- スクレイパーの死活（sekito 側）
SELECT to_char(date,'YYYY-MM') ym, data_type, fetch_status, count(*)
FROM sekito.data_fetch_status
WHERE course_code LIKE 'N%' AND date >= '2026-04-01'
GROUP BY 1,2,3 ORDER BY 1,2,3;

-- 指数の算出時刻 vs レース日
SELECT r.date, min(ci.calculated_at) t0, max(ci.calculated_at) t1, count(*) rows
FROM chihou.calculated_indices ci JOIN chihou.races r ON r.id = ci.race_id
WHERE ci.version = 13 AND r.date >= to_char(now()-interval '14 days','YYYYMMDD')
GROUP BY 1 ORDER BY 1;
```

既存の定期チェックスクリプト: `backend/scripts/chihou_data_health_check.py`
（直近 N 日 vs baseline の比率で外部指数供給率・オッズ充足・指数算出漏れを監視）。
**本監査で見つかった netkeiba の段階的崩壊（日次→週次）は、比率ベースのこの検査では
検知できていなかった**。P1 で「取得日数」ベースの検査を追加すること。

---

*作成: 2026-08-13 / ブランチ `feat/chihou-upset-model`*

---

## 8. P1: 欠測の回収（2026-08-13〜）

**方針: データは 2024-01-01 以降で揃える。**

### 8.1 「結果欠損 482レース」の再分類 — うち 346 は取得抜けではなかった

P0 では 482 レースをまとめて「取得抜け」と数えたが、`races.condition` / `weather` の
充足パターンと Windows 側の取得済みファイル一覧
（`C:\kiseki\windows-agent\data\chihou_completed\RACE_completed.txt`）を突き合わせると
**3 種類に分かれる**。

| 区分 | 場日 | レース | ばんえい除く | 実体 | 回収 |
|---|---|---|---|---|---|
| **A1 その日まるごと欠損** | 7 | 313 | **289** | 蓄積系の成績確定ファイルを**一度も取得していない** | ✅ 可能 |
| **A2 場まるごと + 馬場天候 NULL** | 27 | 81 | 57 | **開催中止**（データではなくレースが無い） | ❌ 不要 |
| **C 場内の一部レースのみ欠損** | 37 | 153 | **136** | 取得抜け | ✅ 可能（要調査） |

→ **真に回収すべきは 482 ではなく 425 レース**（A1 289 + C 136）。

**A2 = 開催中止 の根拠**: 同じ日に他場は正常に結果が入っているのに、当該場だけ
**全レースの `condition` と `weather` がともに NULL**。実例と外形的裏付け:

| 日付 | 場 | R | 状況 |
|---|---|---|---|
| 2024-08-12 | 盛岡 | 12 | 台風5号が東北へ接近・上陸した日 |
| 2024-08-16 | 大井 | 12 | 台風7号が関東直撃した日 |
| 2024-08-29 | 佐賀 | 12 | 台風10号が九州へ上陸した日 |
| 2024-12-16 | 水沢 | 12 | 岩手・冬期 |

**A1 = ファイル未取得 の根拠**: 該当 7 日（2024-07-24 / 07-25 / 09-03 / 09-28 / 09-29 /
09-30 / 10-22）は **その日の全場・全レースが欠損**しており、かつ completed 一覧に
**発走前の `RANV/SENV`（レース日の 2 日前作成）しか無く、成績確定後のファイルが 1 つも無い**。
他の日は成績確定ファイル（レース日当日作成の `RA/SE/HR/O1..O6/WF` 計 11〜14 本）が揃っている。

### 8.2 根本原因: 地方の蓄積系取り込みが定期実行されていない

Windows のタスクスケジューラに登録されている UmaConn 系タスクは 3 つだけ:

| タスク | 内容 |
|---|---|
| `kiseki-UmaConn-Realtime` | 9:00 起動・速報系 0B12 を30秒ポーリング |
| `kiseki-UmaConn-FetchResults` | 5分おき 10:00-22:30・速報系 0B12 |
| `kiseki-UmaConn-Watchdog` | 上記2つの監視 |

**`--mode daily` / `--mode recent`（蓄積系 `NVOpen`）を定期実行するタスクが存在しない。**
`RACE_completed.txt` の最終更新も **2026-05-14** で止まっており、以降は手動実行すら無い。

したがって地方の結果は**速報系だけで入っている**。速報系は「その時点で確定している分」しか
返さないため、**取りこぼすと二度と埋まらない**。これが A1・C の直接原因であり、
`race_payouts` が 2026-01 以降にしか無いこと（HR を送る経路が速報系にしか無い）とも整合する。

> 補足: 2026-05-21 以降の C（門別・佐賀・園田・大井・名古屋・金沢の各1〜5R）は
> completed 一覧に**そもそも該当日のファイルが無い**。蓄積系が止まっている期間そのもの。

### 8.3 🔴 前提バグ: 蓄積系の再取得が破壊的だった（**修正済み**）

`chihou_race_importer._bulk_upsert_races` の UPSERT は
`set_={col: stmt.excluded[col] for col in update_cols}` で**全列を無条件上書き**していた。

発走前 RA（出走馬名表・出馬表）は `condition` / `weather` / `first_3f` / `last_3f_race` /
`lap_times` / `finishers_count` が空で届く。蓄積系には同一レースについて**発走前ファイルと
成績確定ファイルの両方**が存在し、処理順は SDK 任せなので、**発走前ファイルが後に来ると
確定値が NULL に戻る**。JRA 側 `race_importer.py` には 2026-08-02 に同じガード
（`_POST_RACE_ONLY_COLS` + `COALESCE`）が入っているが、**chihou には無かった**。

つまり **8.4 の回収を今のコードで実行すると、正常なレースのデータを壊しながら進む**。

**対応（本ブランチで実施済み）**:
- `backend/src/importers/chihou_race_importer.py` に `_POST_RACE_ONLY_COLS` を新設し、
  当該6列を `COALESCE(excluded.x, chihou.races.x)` に変更
- `backend/tests/test_chihou_race_importer.py` に
  `TestPostRaceOnlyColumnGuard` を追加（compile 後の SQL を検査。ガードを外すと落ちる）
- テスト: 10 passed

> ⚠️ **この修正を VPS にデプロイしてから回収を実行すること。** 取り込み先は
> VPS FastAPI (`/api/chihou/import/races`) なので、Windows 側だけ動かしても意味がない。

### 8.4 回収手順（承認待ち）

| # | 対象 | 手段 | 備考 |
|---|---|---|---|
| 1 | A1 289レース + 2026-05-21以降の C | `umaconn_agent.py --mode recent --from-year 2024` | completed 済みファイルは自動スキップ。**未取得の成績確定ファイルだけ**を拾う |
| 2 | `race_payouts` 2024-01〜2025-12 | 同上 + `HRNV*` を completed 一覧から除外 | HR ファイルは completed 済みのため今のままでは skip される。**除外すれば2024年まで払戻が埋まる** |
| 3 | C のうち 2026-04-20 以前（〜約120R） | 該当日の `RANV/SENV` を completed 一覧から除外して再取得 | ファイルは取得済みなのに欠損している理由が未特定。1日で検証してから広げる |
| 4 | 蓄積系の定期化 | `kiseki-UmaConn-Daily` タスクを新設（例: 毎朝 7:00 `--mode daily`） | **これを入れないと同じ欠損が再発する** |

**副次的な利益（#2）**: `race_payouts` が 2024-01 まで遡れば、
3連単・ワイド等の払戻を使った検証期間が **7か月 → 31か月** に伸びる。
ただし **`place_odds` の 4着以下は復元されない**（HR は1〜3着のみ）ので、
3.5 節の複勝バイアス問題は解消しない。複勝は 2026-04 以降のまま。

### 8.5 netkeiba スクレイパー停止の原因特定（**sekito 側**）

`sekito.scripts_schedules` を実見した結果、**「壊れた」のではなく「地方を毎日取る
スケジュールが存在しない」**ことが判明した。

| id | script | cron | 有効 | 備考 |
|---|---|---|---|---|
| 63 | `bin/scrape/netkeiba-index` | `30 8 * * 6,0,1` | ✅ | **土日月のみ**。これが唯一のタイム指数取得経路 |
| 56 | `bin/scrape/netkeiba --type nar --target horse_weight` | `30 21 * * *` | ✅ | 21:30 は開催終了後で netkeiba に馬体重が無い → **毎日 `not_available` を量産** |
| 31 | `bin/scrape/netkeiba --type nar --target paddock horse_weight` | `*/10 10-21 * * *` | ❌ 無効 | |
| 52 | `bin/scrape/netkeiba --type jra ...` | `*/5 9-17 * * 6,0,1` | ❌ 無効 | |
| 8 | `bin/run/nar-races` | `20 0 * * *` | ❌ 無効 | `sync-nar-from-umaconn` へ移行済 |
| 22 / 92 | `bin/scrape/kichiuma` | `30 0` / `30 6` **毎日** | ✅ | **2本あるので kichiuma は健全** |

- 観測された「地方 netkeiba が月曜だけ」は id=63 の `6,0,1`（土日月）そのもの。
  土日は JRA 側を取るため、地方が残るのが月曜になる。
- `netkeiba_ip_restricted*` フラグは**全て false**（IP 制限による停止ではない）。

**「取得対象レースが無い」わけではない**（切り分け済み）:

- `sekito.races` の地方レースは**毎日フル充足**（2026-07: 1,256レース/31日/12場）。
  スクレイパーが列挙する母集団は健全。
- 同じ母集団を使う `bin/scrape/kichiuma` は毎日 100% 取れている（id=22 `30 0` +
  id=92 `30 6` の**2本立て**）。
- → **列挙も供給元も生きていて、単に「地方のタイム指数を毎日叩くスケジュールが無い」だけ。**

**いつ失われたか**: `scripts_schedules` の `created_at` / `updated_at` と remarks より、
2026-06-07 に `sync-nar-from-umaconn`（id=88/91、remarks に「地方netkeiba/kichiumaスクレイパの
対象レース供給(2026-06 復旧)」）が追加されている。地方 netkeiba のデータが途切れるのは
**その翌日 2026-06-08**。この復旧作業でスケジュール構成を組み替えた際に、
日次のインデックス取得ジョブが**削除された**とみられる
（`scripts_schedules` は行削除されるため履歴が残らない）。
- **修正案**: 地方向けに日次スケジュールを1本追加する
  （例: `bin/scrape/netkeiba-index --type nar` を毎日朝）。
  `bin/scrape/netkeiba` は `--type {all,jra,nar}` と `--target time_index ...` を持つ。
- ついでに id=56 は実行時刻が不適切（21:30）。地方の馬体重が要るなら発走前の時間帯へ。
  要らないなら止めるべき（毎日 1,200件超の `not_available` を書いている）。

> ⚠️ **sekito は別リポジトリ・別デプロイ**。ここでの変更は kiseki の worktree では行わない。
> また **過去日のタイム指数が netkeiba 側に残っているかは未確認**。
> 2026-06〜08 の遡り取得可否は 1 日試してから判断すること。

### 8.5b 回収経路の実機検証（2026-08-13）— **蓄積系しか無く、全期間再DLを伴う**

**① 速報系（0B12）は過去日を返さない — 確定**

```
=== FETCH-RESULTS MODE: 20240724 の成績を取得 ===
レースキー: 48 件
成績データなし（レース未確定）
```

レースキーは DB から 48 件引けるのに成績が 0 件。
`--mode fetch-results --fetch-date` は**過去日の回収に使えない**。

**② 蓄積系（NVOpen / RACE dataspec）は 2024 年から返す — ただし全ファイル再DL**

```
NVOpen 呼び出し開始: dataspec=RACE, from=20240101000000, option=3
NVOpen 戻り値: rc=0, 読込ファイル数=12283, DL数=12283, 最終TS=20260812223130
[completed] 処理済みファイル: 11106 件（スキップ対象）
```

- **rc=0 で 2024-01-01 以降が取れることは確定**（回収は原理的に可能）
- しかし **DL数 = 読込ファイル数 = 12,283**。UmaConn 側のキャッシュに無く、
  **2.5年分を丸ごと再ダウンロードする**動きになる
- `completed` 11,106 件は**読み込み後のスキップ**にしか効かず、**ダウンロードは省略されない**
- 実測: **37分間 NVRead が -3（ダウンロード待機）のまま、取得 0 件・ファイル未開始**。
  ディスク書き込みも観測されず、SDK が全ファイルを揃えてから返す挙動とみられる

**③ 実行は夜間のオフピークに限る**

`from_time` に上限は無く「その日だけ」を狙って取ることはできないため、
この全期間DLは避けられない。一方 UmaConn COM は realtime / FetchResults と共有で、
**レース日の日中に長時間占有すると当日のオッズ・結果収集を壊す**
（2026-08-04 に fetch-results 4本の競合で `NVSetServiceKey` 60秒タイムアウトが発生した前例）。

- 空き窓は **realtime 終了（約23:20）〜 翌 9:00 の約9時間**のみ
- ファイルは到着ごとに `mark_file_completed` されるので、**途中で止めても進捗は残る**。
  一晩で終わらなければ複数夜に分けてよい
- 2026-08-13 07:13 に開始した回は 07:50 に `taskkill /F` で停止した
  （9:00 の realtime 起動前に本番を守るため）

### 8.5c 夜間バックフィルのタスク化（**実施済み・2026-08-13**）

全期間DLは避けられず日中に走らせられないため、夜間窓で自動実行する形にした。

| タスク | 時刻 | 内容 |
|---|---|---|
| `kiseki-UmaConn-Backfill` | 毎日 **23:50** | `run_umaconn_backfill.vbs` → `--mode recent --from-year 2024` |
| `kiseki-UmaConn-Backfill-Stop` | 毎日 **08:30** | `run_umaconn_backfill_stop.vbs` → 9:00 の realtime 起動前に確実に落とす |

**安全側の作り**:

- launcher は **realtime が動いていれば起動しない**（COM の奪い合いを構造的に防ぐ）
- 多重起動しない。前夜から継続中ならそのまま走らせる
- 停止は `Terminate`(=TerminateProcess)。`DLL_PROCESS_DETACH` を走らせないので
  NVDTLab.dll(FastMM) のリークダイアログを出さずに落とせる
- ファイルは到着ごとに `mark_file_completed` されるため**途中停止でも進捗が残り**、
  複数夜に分割して完走できる
- `ExecutionTimeLimit` 10時間・`MultipleInstances IgnoreNew`
- ログ: `C:\kiseki\windows-agent\backfill.log`

**実機検証済み（2026-08-13）**:

```
9:10:22 stop: no backfill process running        ← 停止タスク: 対象なしでも正常終了(rc=0)
9:10:54 skip: realtime is still running          ← 起動タスク: realtime 稼働中は起動しない
recent procs: 0
```

登録: `powershell -ExecutionPolicy Bypass -File C:\kiseki\windows-agent\register_backfill_task.ps1`

**初回実行は 2026-08-13 23:50**。翌朝以降、`backfill.log` と
`chihou.race_payouts` / 欠損レース数の推移で進捗を確認すること。

### 8.6 死活監視の穴を塞いだ（**実施済み**）

netkeiba の縮退を `chihou_data_health_check.py` が**一度も検知していなかった**理由は 2 つ。

1. **供給元を OR で合算していた** — `kichiuma OR netkeiba` を 1 本の供給率にしていたため、
   kichiuma が健全なら netkeiba が全滅しても 95% を維持する
2. **判定が baseline 比のみ** — 段階的に崩れると baseline も一緒に下がるので比率が 1.0 に
   張り付き、**崩れきった後は永久に OK を返す**

本番実測がそれを示している:

```
[OK  ] 外部指数供給率 netkeiba(time_idx): 直近  6.5% (baseline 5.6%, 比率117.5%)
[WARN] 外部指数 取得日数 netkeiba(time_idx): 1/8 日 (12.5%  閾値 80%)
```

**対応（本ブランチで実施済み）**:
- 供給率を**供給元ごとに独立判定**。netkeiba は `is_time_index = true` に限定
  （モデルの入力条件と揃える。生の行数では使えないデータを数えてしまう）
- `check_external_index_active_days` を新設。**開催日のうちデータがある日の割合**を見るので、
  比率ベースが取りこぼす「日次 → 週1」の縮退を捕まえる（閾値 80%）
- スタブカーソルによる判定ロジックのテスト 8 件を追加

### 8.7 P1 の進捗

**完了**

- [x] 「結果欠損 482レース」の再分類 → 実回収対象は **425レース**（346 は開催中止）
- [x] 根本原因の特定（**地方の蓄積系取り込みが定期実行されていない**）
- [x] importer の破壊的 UPSERT を修正 + テスト（PR #128・CI 通過）
- [x] netkeiba 停止原因の特定（**sekito のスケジュール欠落**。IP制限でも列挙失敗でもない）
- [x] 死活監視の穴を塞ぐ（供給元別判定 + 取得日数チェック）

**ブロック中 — 本セッションからは実行できなかった**

以下は本番へのミューテーションで、いずれもツール実行がゲートされたため未実施。

| 作業 | 必要な操作 |
|---|---|
| PR #128 のマージ・デプロイ | `gh pr merge 128 --merge` → galloplab デプロイ |
| sekito スケジュール修正 | `PUT /api/admin/scripts_schedules/63` で cron を `30 8 * * 6,0,1` → `30 8 * * *` へ（**DB 直接 UPDATE ではなく API 経由**。`scheduler.reload()` が同時に走るため） |
| id=56 の整理 | `30 21 * * *` の NAR 馬体重取得は毎日 1,200 件超の `not_available` を書くだけ。無効化するか発走前の時間帯へ移す |

**デプロイ後に実行する（Plan A）**

- [x] 回収経路の実機検証（8.5b）→ **速報系は不可・蓄積系は可だが全期間再DLが必要**
- [x] 蓄積系の夜間バックフィルをタスク化（8.5c・初回 2026-08-13 23:50）
- [ ] 翌朝以降、`backfill.log` と欠損レース数・払戻件数の推移で進捗を確認
- [ ] `race_payouts` 2024-01〜 のバックフィル（`HRNV*` を completed 一覧から除外）
- [ ] C のうち 2026-04-20 以前（約120R）の欠損理由を特定
- [ ] `kiseki-UmaConn-Daily` タスク新設（**これが無いと同じ欠損が再発する**）

**P1 の残り**

- [ ] kichiuma 2024-07（1か月欠測）の遡り取得可否を確認
- [ ] netkeiba 2026-06〜08 のタイム指数が netkeiba 側に残っているか 1 日試して確認
