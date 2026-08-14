# 中央競馬（JRA） 予想ロジック 再整理（2026-08 〜）

地方競馬で行った再整理（`docs/chihou_rebuild_2026_08.md`）と**同じ手順**を中央競馬に適用する。
本ドキュメントを**今回の作業の唯一の作業台帳**とし、フェーズが進むたびに追記する。

- 前提: **取得データ（JV-Link / JV-Next / スクレイピング）は資産として維持する。
  生成済みデータ（`calculated_indices` / 学習済みモデル / バックフィル結果）は破棄・再構築しうる。**
- 先行ドキュメント: `CLAUDE.md`（v27 / 着外率 / hit_tier の現行仕様）、
  memory `jra_rank_quality_redesign_2026_08_02` / `jra_out_rate_3head_verification_2026_08_02`
- ⚠️ **DB の `calculated_indices` は全期間 refit モデルの遡及適用で in-sample。
  過去の ROI・的中率をそこから測ってはいけない。** 本書の数値はすべて
  train ≤2025-06-30 / valid ≤2025-12-31 / test 2026-01-04〜 で**学習し直した honest 値**。

---

## 0. フェーズ計画

| # | フェーズ | 状態 | 内容 |
|---|---|---|---|
| **P0** | **監査** | ✅ **完了（1〜6章）** | データ源・train/serve 整合・生成データの棚卸し |
| **P1** | **DM 取得経路の修復** | ✅ **完了（11章）** | 3 つの独立したバグを修正し、7/19・8/9・8/15 を回収した |
| **P2** | **TRAIN/VAL/TEST プロトコルの制定** | ✅ **完了（13章）** | 四半期ローリング + 開封台帳 + 自動化 |
| P3 | train/serve 整合の是正 | 🔄 **一部完了（12章）** | 4.7（学習ソース凍結）と 4.6（`weight_change`）は解消。配信条件での学習は未 |
| **P4** | **死んだ特徴の除去・特徴量再検討** | ✅ **判定完了（15.1）— 不採用** | `paddock` / `going_pedigree` / `rebound` |
| **P5** | **推奨（hit_tier）の前向き記録** | ✅ **実装完了（14章）** | 地方 `chihou_place_pick_log.py` の移植 |
| **P6** | **生成データの整理** | ✅ **完了（15.3 / 15.4）** | `calculated_indices` −77.8% / `odds_history` −68.3% |

---

## 1. データ源の全体像

中央は「JRA-VAN から全部来る」と思われがちだが、**指数の入力は 5 系統**あり、
壊れ方も回収方法もそれぞれ違う。

| 系統 | 取得元 | 格納先 | 経路 | 実行場所 |
|---|---|---|---|---|
| **A. 本体データ** | JRA-VAN Data Lab（JV-Link COM） | `keiba.races` / `race_entries` / `race_results` / `race_payouts` | 蓄積系 `JVOpen` + 速報系 `JVRTOpen` | Windows VM `jvlink_agent.py` |
| **B. オッズ** | 同上（速報 `0B31` 他） | `keiba.latest_odds` / `keiba.odds_history` | realtime ポーリング（約30秒） | 同上 |
| **C. JV-Next DM 指数** | **JV-Next の `GateServlet` プロトコル**（1403） | `keiba.race_entries.jvan_time_dm` / `jvan_battle_dm` | Mac LaunchAgent → SSH → Windows パイプライン → HTTP POST | Mac + Windows VM |
| **D. 穴ぐさ** | sekito リポジトリのスクレイピング | `sekito.anagusa` | HTTP スクレイプ | **sekito リポジトリ**・VPS |
| **E. 調教・パドック** | JV-Link（調教）/ netkeiba（パドック） | `keiba.slope_training` / `wood_training` / パドックは netkeiba 由来 | 混在 | Windows VM / sekito |

> 🔴 **C（JV-Next DM）が総合指数の入力として突出して重い。** 順位回帰ヘッドの
> gain の **71.8%**（`jvan_battle_dm` 55.2% + `jvan_time_dm` 16.7%）を 2 列で占める。
> ところが取得経路は 5 系統の中で**最も脆く、最も監視が薄い**（4.4 節）。

### 現在の実行スケジュール（VPS crontab 実見・**サーバは JST**）

```
30  7 * * *  jra_calculate_trigger.sh            # 07:30 JST — 当日分の指数算出
 0 22 * * *  jra_calculate_trigger.sh tomorrow   # 22:00 JST — 翌日分の指数算出
 0  9-21 * * *  odds_prefetch_trigger.sh         # 毎正時 オッズ先読み
```
加えて **馬体重（`0B11`）到着時に該当レースだけ再算出**（`import_weights` の BackgroundTask）。
DM 取得は VPS ではなく **Mac の LaunchAgent** `com.kiseki.dm-auto-fetch`
（12:00 / 14:00 / 18:00 / 22:30 毎日 + 土日 8:00, 11:00）。

---

## 2. 監査結果サマリ（期間 × 使用可否）

🟢 使用可 / 🟡 条件付き / 🔴 使用不可

| データ | 〜2018 | 2019-2022 | 2023 | 2024 | 2025 | 2026上 | 2026-06〜 |
|---|---|---|---|---|---|---|---|
| `races` / `race_entries` | 🟡 断片 | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 | 🟢 |
| `race_results` | 🟡 | 🟢 | 🟢 | 🟢 | 🟢 | 🟡 41R 欠 | 🟢 |
| `race_payouts` | — | — | 🟢 100% | 🟢 99.9% | 🟢 99.6% | 🟢 | 🟢 |
| `race_results.win_odds` | — | 🟢 | 🟢 99.6% | 🟢 | 🟢 | 🟢 | 🟢 |
| `race_results.place_odds` | — | 🔴 | 🔴 **21.6%** | 🔴 21.8% | 🔴 21.5% | 🔴 21.2% | 🔴 |
| `odds_history`（発走前時系列） | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 | 🟡 **2026-03-28〜** |
| `jvan_*_dm`（DM 指数） | — | — | 🟢 99% | 🟢 99% | 🟢 99% | 🟢 99% | 🔴 **直近が落ちている** |
| `sekito.anagusa` | — | — | 🟡 2024-01〜 | 🟢 | 🟢 | 🟢 | 🟢 |
| `paddock_index` | — | — | 🔴 定数 | 🔴 定数 | 🟡 2025-07〜生きる | 🟡 〜2026-04 | 🔴 **2026-05 停止** |
| `going_pedigree_index` | — | — | 🟡 概ね中立 | 🟡 | 🟡 | 🟡 | 🔴 **2026-05 以降 100% が 50** |

### 結論として「揃っている」窓

| 用途 | 使ってよい期間 | 制約 |
|---|---|---|
| 総合指数（34特徴）の学習 | **2023-05-06 〜 2026-08-02** | `calculated_indices version=26` 行のある期間に厳密に一致（5.2 節）。**2026-08-02 で凍結している** |
| 単勝 ROI 検証 | 2019〜現在 | `race_results.win_odds` 99.6% |
| **複勝 ROI 検証** | 🔴 **できない** | `race_results.place_odds` が**全期間 21% しか無い**（1〜3着馬のみ＝地方 3.5 節と同型の上方バイアス源） |
| 3連単等の払戻検証 | 2023〜現在 | `race_payouts` 99.6〜100% |
| 発走前オッズを使う検証 | **2026-03-28 以降のみ**（40 開催日） | `odds_history` 13GB / 60.4M 行 |

> 🔴 **中央の `place_odds` は地方 3.5 節とまったく同じ壊れ方をしている**（充足 21.2〜21.8% ＝
> 概ね 1〜3着馬のみ）。`place_odds.notna()` で絞ってから複勝 ROI を出しているコードが
> あれば、その数値は無効。**P5 に入る前に全スクリプトを検査すること。**
> 地方には母集団ガード `chihou_place_odds_guard.py` を入れてあるので、同型を移植する。

---

## 3. 本体データ（A系統）の監査

### 3.1 カバレッジ

`keiba.races` 81,916 行。中央10場（`course IN ('01'..'10')`）の年別:

| 年 | レース | 結果欠損 | `condition` NULL |
|---|---|---|---|
| 1986〜1989 | 各 3,284〜3,335 | 0 | 39〜45 |
| 1990 | 2,811 | **282** | 41 |
| 2000〜2002 | 各 3,448〜3,452 | 0 | 37〜48 |
| 2019 | 3,491 | 56 | 128 |
| 2020 | 3,466 | 10 | 83 |
| 2021〜2023 | 各 3,456 | 0 | 92〜98 |
| 2024 | 3,456 | 2 | 102 |
| 2025 | 3,468 | 13 | 86 |
| 2026（8/13時点） | 2,196 | **41** | 47 |

- **学習窓（2023-05〜）の本体データは実質欠損なし。** 地方のような大量の取得抜けは無い。
- 2026 の結果欠損 41R は **2026-02-07〜09 に集中**（積雪等による開催中止の可能性が高い。
  レース行だけ作られて結果が来ていない）。P1 で中止か取得抜けかを確定させること。
- 1954〜1985 は年 1〜17 レースの断片（重賞のみ）。学習には使わない。
- `condition` NULL が毎年 40〜130 あるのは**発走前に作られたまま確定値で上書きされなかった行**
  と障害競走が混在。全体の 2.7% で実害は小さい。

### 3.2 `head_count` / `condition` / `weather` は**発走前は NULL**

これは欠損ではなく**タイミングの問題**だが、指数の入力なので重大（4章）。実測:

```
   date   | n  | cond_null | wx_null | hc_null | fin_null
 20260808 | 36 |         0 |       0 |       0 |        0   ← 実施済み
 20260809 | 36 |         0 |       0 |       0 |        0   ← 実施済み
 20260815 | 36 |        36 |      36 |      36 |       36   ← 今週末（未実施）
 20260816 | 36 |        36 |      36 |      36 |       36
```

`condition` / `weather` / `head_count` / `finishers_count` は **`0B12`（速報成績＝レース確定後）
で初めて入る**。したがって発走前の算出では 4 列とも常に NULL。

### 3.3 馬体重は発走の約1時間前に届く（2026-08-08 に経路が直った）

`race_entries.horse_weight` の充足率は**歴史的に 99.7〜100%**（週次の蓄積系 SE 取込で埋まる）。
`weight_change` は 82〜97%（不足分は初出走馬）。

⚠️ **CLAUDE.md の「`race_entries.horse_weight` は 0B12 経由で 1〜3着馬にしか入っていない」
という記述は、DB 実測と一致しない。** 実際は蓄積系の取込で全馬に入る。
2026-08-08 に直した `0B11`（WH）の意義は「**発走前に**入るようになった」ことであって、
「初めて入るようになった」ことではない。この記述は要訂正。

---

## 4. 🔴 特徴量の train/serve 監査（本監査の核心）

### 4.1 学習の入力と配信の入力は別経路である

| | 学習（`train_jra_out_rate.FETCH_SQL`） | 配信（`composite._build_v26_features`） |
|---|---|---|
| サブ指数 17列 | `keiba.calculated_indices` **version=26** の行 | その場で 19 個の Calculator を実行 |
| レースメタ 10列 | `keiba.races`（**確定後**の値） | `Race` オブジェクト（**発走前**の値） |
| 馬メタ 7列 | `race_entries` + **`race_results.weight_change`** | `race_entries` + **`race_results.weight_change`** |

34 列を出所で分類すると、**発走前に値が変わる（＝ train/serve が食い違う）のは 8 列**:

| 列 | 学習時 | 配信時 | 原因 |
|---|---|---|---|
| `is_good` `is_yaya` `is_heavy` `is_bad` | 必ず 1 本が立つ | **4本とも 0** | `races.condition` が発走前 NULL（3.2） |
| `going_pedigree_index` | 2026-04 まで一部で非50 | **全馬 50** | 同上（Calculator が早期 return） |
| `head_count` | 実出走頭数 | `len(entries)`（取消馬込み） | 同上 |
| `horse_weight` | ほぼ 100% | 07:30 は NaN / **T-1h から入る** | `0B11` の到着（2026-08-08〜） |
| `weight_change` | 82〜97% | **常に NaN** | 🔴 **読み先が違う**（4.6 節） |
| `jvan_time_dm` `jvan_battle_dm` | 99% | **DM 取得の成否しだい** | 🔴 **経路が壊れている**（4.4 節） |

### 4.2 実測 — シナリオ別の順位精度

`backend/scripts/jra_train_serve_skew_audit.py`（本ブランチで新設）。
**train ≤2025-06-30 / valid ≤2025-12-31 で学習し直し**、
honest test **2026-01-04〜2026-08-02（2,082R / 28,783行）** を、
入力だけ差し替えて評価した。シード 42/123/456 の平均。

| # | 学習の入力 | 配信の入力 | Spearman | **指数1位 勝率** | 1位 複勝率 | NDCG@3 | 1位一致率 |
|---|---|---|---|---|---|---|---|
| ① | DB（確定後） | DB（＝学習と同条件） | 0.5076 | **28.10%** | 60.81% | 0.5282 | — |
| ② | DB | 馬場状態のみ欠落 | 0.5072 | 28.15% | 60.95% | 0.5288 | 98.8% |
| ③ | DB | **現行本番（T-1h）** | 0.5042 | **27.76%** | 60.57% | 0.5233 | — |
| ④ | DB | 当日 07:30（馬体重も無い） | 0.5043 | 27.38% | 60.28% | 0.5218 | 90.4% |
| ⑤ | **配信条件** | 配信条件 | 0.5074 | **28.29%** | **61.38%** | 0.5289 | — |
| ⑥ | DB | **DM 2列だけ欠落** | 0.4188 | **22.81%** | 52.88% | 0.4581 | — |
| ⑦ | DB | 07:30 + DM 欠落 | 0.3968 | 22.19% | 52.16% | 0.4477 | **50.2%** |
| ⑧ | **DM を使わず学習** | DM 無し | 0.4709 | 26.46% | 58.60% | 0.5038 | — |

読み取れること:

1. 🟢 **馬場状態 / `going_pedigree_index` の train/serve 不整合は無害。**
   ②は①と**同等かむしろ僅かに良い**（+0.05pt）。
   条件ダミー 4 本の gain は合計 0.09%、`going_pedigree_index` は 0.09% しかない。
   **仮説1の具体例として挙がっていた `going_pedigree` は、実害ゼロと判定してよい。**
2. 🟡 **馬体重・体重増減の欠落は −0.34〜−0.72pt。** 小さいが `weight_change` は
   1行の修正で取り返せる（4.6）。
3. 🟢 **配信条件で学習し直すと 28.29%** ＝ 現行本番の実効（③ 27.76%）より **+0.53pt**、
   学習条件で測った上限（① 28.10%）すら上回る。**モデルを配信条件で学習するのが正しい。**
   ⑤で①と⑤の予測が完全一致するのは、学習時に定数だった列でモデルが一度も分岐しないため。
4. 🔴 **DM 2列が欠けるだけで −5.29pt（28.10 → 22.81%）。複勝率は −7.9pt。**
   指数1位馬が変わるレースが**半分（一致率 50.2%）**。他のどの要因より一桁大きい。
5. 🔴 **DM 欠損日には「最初から DM を使わないモデル」の方が 3.65pt 良い**（⑧ 26.46% vs ⑥ 22.81%）。
   DM を前提にしたモデルに中立値 50 を与えるのは、**DM を持たないより悪い**。

> **結論: 中央の train/serve 問題の本体は馬場状態ではなく「DM が配信時に在るか」である。**
> 地方 13章の「市場込みで学習・市場なしで配信」と同じ構図が、
> **市場ではなく DM で、しかも間欠的に**起きている。

### 4.3 DM は現に落ちている

`race_entries` の DM 充足率（中央10場・出走馬単位）:

| 開催日 | time_dm | battle_dm |
|---|---|---|
| 2026-07-12 | 100.0% | 97.0% |
| **2026-07-19** | **78.0%** | **78.0%** |
| 2026-07-25〜08-08 | 99.3〜99.6% | 96.2〜97.4% |
| **2026-08-09** | **5.9%** | **5.9%** |
| **2026-08-15（今週土）** | **0.0%** | **0.0%** |
| **2026-08-16（今週日）** | **0.0%** | **0.0%** |

月次では 2023-05〜2026-07 が一貫して 99%台なので、**学習データ側はほぼ完全**。
壊れているのは**直近の配信**だけである。

⚠️ **2026-08-09 は 5 日経った今も 5.9% のまま**＝レース後にも回復していない。
つまり当日の指数は「71.8% の gain を持つ 2 列が全馬 50」の状態で出ていた。
④→⑦ の差から、その日の指数1位馬の勝率は **22% 前後**（通常 28%）だったと推定される。

### 4.4 🔴 根本原因 — 取り込めなかったレースを「完了」として記録している

`windows-agent/jvnext_dm_importer.py` の `run_import`:

```python
result = post_race_records(jravan_race_id, records)
updated = result.get("updated", 0)
logger.info(f"  {dm_path.name} → {jravan_race_id} updated={updated}")
progress[jravan_race_id] = "ok"      # ← updated==0 でも "ok"
ok += 1
```

**`updated == 0`（1頭も更新できなかった）でも `progress` に `"ok"` を書く。**
`progress` に `"ok"` があるレースは以後 `skip_ok=True` で**永久にスキップされる**
（実測 `skipped=12519`）。

2026-08-14 12:18 のログ（実物）:

```
1403202608150101.dat → 2026081501010701 updated=0
1403202608150102.dat → 2026081501010702 updated=0
   … 36 レース全て updated=0 …
=== 完了 ok=38, failed=0, skipped=12519 ===
```

`updated=0` の理由は **その時点で `race_entries` がまだ無かった**ため
（API `/api/import/jvan_dm` は `(race_id, horse_number)` で `race_entries` を引き、
見つからなければ `skipped` に落とす）。同日 18:36 時点では 8/15 の出走馬 488 行が
DB に揃っているので、**もう一度流せば入る**。しかし `progress` が `"ok"` なので二度と流れない。

同じ構図が **2026-08-09（5.9%）・2026-07-19（78%）** も説明する。
DM の `.dat` ファイル自体は Windows 側の永続ストアに**正常なサイズで存在する**
（8/8 平均 2,382 バイト / 8/9 平均 2,830 バイト / 8/15 も同等）。
**取得は成功していて、DB への反映だけが恒久的に失われている。**

→ **11章で修正・回収済み**（2026-08-14）。実際には**独立した 3 つのバグ**が重なっていた。

⚠️ **「DM 取得は動いている」と `OVERALL: saved=N` だけを見て判断しないこと。**
`saved` はファイル取得の数であって DB 反映の数ではない。見るべきは
`race_entries.jvan_time_dm` の充足率。

### 4.5 DM 欠損時のフォールバックを持つべきか

⑧（DM を使わず学習した 32 特徴モデル）は honest 26.46%。
DM が在る日の 28.10% には及ばないが、**DM が欠けた日の 22.81% よりは 3.65pt 良い**。

> **提案（P3 で判断）**: DM 充足率がレース単位で閾値未満なら、DM 抜きモデルへ切り替える。
> 「壊れても静かに劣化する」ではなく「壊れたら劣化を最小化する」設計にする。

ただし **本筋は 4.4 の修復**であり、フォールバックはその保険である。順序を逆にしないこと。

### 4.6 `weight_change` は読み先が間違っている（1行バグ）

- `0B11`（WH）の取込 `_apply_wh_records` は
  **`race_entries.horse_weight` と `race_entries.weight_change` の両方**を書く
- ところが `composite._get_weight_change_map` は
  **`race_results.weight_change`** を読む。`race_results` はレース確定後にしか存在しない

```python
async def _get_weight_change_map(self, race_id, horse_ids):
    """過去 race_results から馬体重増減を取得（前走の値）。"""
    # 当該レース自体の race_results.weight_change が入っていれば使う
    rows = await self.db.execute(
        select(RaceResult.horse_id, RaceResult.weight_change).where(
            RaceResult.race_id == race_id, ...))
```

docstring は「過去 race_results から」と書いてあるが、実装は**当該レース自身**を見ている。
結果として `weight_change` は**発走前は必ず NaN**、学習時は 82〜97% 充足という食い違いになる。

`race_entries.weight_change` を優先して読むよう変えれば ③→② の差
（27.76% → 28.15%、**+0.39pt**）が取れる。⑤（配信条件で再学習）と併用すれば、
そもそもこの列を定数として学習するので影響は消える。**どちらか一方でよい。**

### 4.7 🔴 学習データが 2026-08-02 で凍結している

`train_jra_out_rate.FETCH_SQL` / `train_jra_reg_rank` / `jra_train_serve_skew_audit` は
いずれも **`WHERE ci.version = 26`** でサブ指数を引く。

しかし本番は `COMPOSITE_VERSION = 27` なので、**v26 の行はもう作られていない**。

```
version | rows   | races | 期間                  | calculated_at 最終
     26 | 155620 | 11304 | 20230506〜20260802    | 2026-08-01
     27 | 156577 | 11376 | 20230506〜20260809    | 2026-08-09
```

→ **学習に使えるデータは 2026-08-02 で止まっており、今後どれだけ開催しても増えない。**
エラーは出ず、行数が増えないだけなので気付けない。地方が
`CHIHOU_SUBINDEX_MIN_VERSION = 9` で踏んだのと同型（ただし中央は下限指定ではなく
**完全一致指定**なので、より静かに壊れる）。

**P3 でやること**: 参照先を `version >= 26` の `DISTINCT ON` にするか、
サブ指数専用の最小バージョン定数を切る。**版を上げるたびに学習が止まる構造をやめる。**

---

## 5. 生成済みデータ（再構築対象）の現状

### 5.1 `calculated_indices` — **23 世代 / 2,689,460 行 / 817MB**

| version | 行数 | レース | 期間 | 位置づけ |
|---|---|---|---|---|
| 1〜25 | 16〜250,592 | | 2019-01〜2026-04 | **死蔵**（2026-03〜05 の試行錯誤の残骸） |
| **26** | 155,620 | 11,304 | 2023-05-06〜2026-08-02 | **学習の唯一の入力**（4.7） |
| **27** | 156,577 | 11,376 | 2023-05-06〜2026-08-09 | **現本番**（API が読む） |

- v1〜25 は削除候補（約 237 万行・推定 700MB 超）。ただし **v26 を消すと学習が動かなくなる**
  ので、4.7 の付け替えを先に済ませること。地方 5.1 と同じ順序。
- v27 の直近カバレッジは開催日 100%（8/8・8/9 とも 36/36）。

### 5.2 ⚠️ DB の行は「live 算出」と「backfill」の混成である

`calculated_at` を見ると、同じ version の中に**性質の違う 2 種類の行**が混ざっている。

| 対象日 | `calculated_at`（UTC） | 実体 |
|---|---|---|
| 2026-07-25 / 26 / 08-01 / 08-02 | 2026-08-02 08:11 に一括 | `inference_v27.py` による **backfill** |
| 2026-08-08 | 08-08 06:24〜08:49 | **live**（07:30 cron + 馬体重再算出） |
| 2026-08-09 | 08-09 00:09〜08:50 | **live** |

つまり **backfill を流した日を境に、それ以前は「確定後の入力」・それ以降は「発走前の入力」** で
書かれている。`going_pedigree_index` が 2026-05 を境に「一部非50 → 100% が 50」へ切り替わるのは
これが理由（4.1）。

> **`calculated_indices` を横断して時系列分析をしてはいけない。**
> 2026-05 前後で生成過程そのものが違う。
> なお `inference_v27.py` は v26 行のサブ指数を**そのまま流用**するので、
> サブ指数の値は「その日に live で算出された値」が backfill 後も残る。
> 変わっているのは合成部（composite / out_probability / win_probability）だけ。

### 5.3 `odds_history` は 13GB

60,368,822 行 / **13GB**。`fetched_at` は **2026-03-28 01:38 〜**、開催 **40 日分**のみ。
`bet_type` は win / place / trio / trifecta / exacta / quinella_place / quinella の 7 種。
（地方は win / place の 2 種で 9.9GB。中央は券種が多いぶん増加が速い）

`latest_odds` は別に 774MB / 4,375,343 行。

**保持方針が無い。** P6 で「発走前 N 分のみ残す」「券種を絞る」等を決めること。

### 5.4 推奨（hit_tier）は**どこにも残っていない**

- `/api/recommendations` は**都度算出**（60秒プロセス内キャッシュ）。DB 書き込みなし
- `keiba.race_recommendations` テーブルは存在するが **117 行・最終書き込み 2026-07-11**。
  これは旧「Claude 定期エージェントによる推奨提出」（`recommender.submit_recommendations`）の
  残骸で、**現行の hit_tier エンジンとは無関係**

→ **中央の推奨には、前向きの記録が一件も無い。** 8章で移植する。

---

## 6. 検出した課題一覧（優先度順）

| # | 課題 | 影響 | フェーズ |
|---|---|---|---|
| ~~**1**~~ | ~~**DM 取り込みが `updated=0` を "ok" として焼き付ける**（4.4）~~ | ✅ **解消（11章）** | P1 |
| **2** | DM 欠損時のフォールバックが無い。中立値 50 は「DM 無しで学習したモデル」より 3.65pt 悪い（4.5） | 🔴 高 | P3 |
| ~~**3**~~ | ~~**学習データが v26 固定で 2026-08-02 に凍結**（4.7）~~ | ✅ **解消（12.1）** | P3 |
| ~~**4**~~ | ~~`weight_change` の読み先が `race_results`（4.6）~~ | ✅ **解消（12.2）** | P3 |
| **5** | 配信条件で学習していない（4.2 ⑤）。配信条件で学習すれば +0.53pt | 🟡 中 | P3 |
| **6** | `paddock_index` が **TRAIN 期間で完全に定数**・VAL で生き・TEST 後半で再び死ぬ（4章 / 下記） | 🟡 中 | P4 |
| **7** | **TRAIN/VAL/TEST の取り決めが無い**。スクリプトごとに境界が違う（7章） | 🟡 中 | P2 |
| ~~**8**~~ | ~~推奨（hit_tier）の前向き記録が無い（5.4）~~ | ✅ **解消（14章）** | P5 |
| **9** | `race_results.place_odds` が全期間 21% しか無い。複勝 ROI 検証は不可（2章） | 🟡 中 | P5 |
| ~~**10**~~ | ~~`calculated_indices` に 23 世代 817MB が滞留~~ | ✅ **解消（15.3）** | P6 |
| ~~**11**~~ | ~~`odds_history` 13GB・保持方針なし~~ | ✅ **解消（15.4）** | P6 |
| **12** | 2026-02-07〜09 の 41R が結果欠損（中止か取得抜けか未確定） | 🟢 低 | P1 |
| **14** | `calculated_at` に UTC（更新時）と JST（挿入時）が混在（11.9）。前後関係の判断に使えない | 🟢 低 | P6 |
| **13** | CLAUDE.md の記述が実測と食い違う 2 件: ①「`race_entries.horse_weight` は 1〜3着馬にしか入っていない」→ **実測は全期間 99.6〜100%**。1〜3着のみになるのは週次の蓄積系取込が走る前の一時的な状態で、恒常的な記述としては誤解を招く ②「`paddock_index` は v26 学習期間中も全月 sd=0」→ **2025-07〜2026-04 は生きている**（sd 5.6〜11.8） | 🟢 低 | P2 |

### 死んだ特徴（課題#6）の実態

`calculated_indices version=26` の月次ばらつき（実測）:

| 特徴 | 2023-05〜2025-06（**TRAIN**） | 2025-07〜2025-12（**VAL**） | 2026-01〜2026-04 | 2026-05〜（**現在**） |
|---|---|---|---|---|
| `paddock_index` | **sd 0.00 / 100% が 50** | sd 5.6〜11.5 | sd 10.0〜11.8 | **sd 0.00 / 100% が 50** |
| `going_pedigree_index` | sd 0.0〜3.3 / 77〜100% が 50 | sd 1.5〜1.9 | sd 0.0〜2.2 | **sd 0.00 / 100% が 50** |
| `rebound_index` | 2023 は sd 0.00 → 2024 以降 2.0〜3.1 | sd 2.6〜2.9 | sd 2.7〜2.8 | **2.27→1.43→0.99→0.57 と減衰中** |
| `anagusa_index` | 2023 は sd 0.7〜0.9（実質死）→2024 以降 4.4〜5.7 | 5.5〜5.8 | 4.9〜5.5 | 4.0〜5.2 🟢 |

> ⚠️ **`paddock_index` は TRAIN 期間で完全な定数なので、honest 分割で学習したモデルは
> 一度も分岐しない。** 実際 `--drop-features paddock_index` の A/B は
> baseline と**小数点以下5桁まで完全に一致**した。
> 一方で**本番モデルは全期間 refit** なので paddock が生きている 2025-07〜2026-04 を学習に含み、
> gain 0.80% / 158 分岐を持つ。**その分岐は現在の配信では必ず定数 50 側へ落ちる。**
> つまり paddock は「honest 評価では無害に見えるが、本番モデルでだけ害がある」特徴である。

3 本まとめて外す A/B（honest・同条件）:

| | Spearman | top1 勝率 | top1 複勝率 |
|---|---|---|---|
| 34特徴（現行） | 0.5076 | 28.10% | 60.81% |
| **31特徴**（paddock / going_pedigree / rebound を除去） | **0.5093** | **28.63%** | **61.62%** |

+0.53pt だが n=2,082R では 1レース = 0.048pt であり、**この差はまだ有意ではない**。
P4 でレース単位 paired bootstrap をかけて判断すること。

---

## 7. TRAIN / VAL / TEST プロトコル（**案・P2 で確定させる**）

中央には取り決めが無く、スクリプトごとに境界が違う:

| スクリプト | train_end | valid_end | test |
|---|---|---|---|
| `train_jra_reg_rank.py` | 20250630 | 20251231 | 2026-01〜 |
| `train_jra_out_rate.py` | 20250630 | 20251231 | 2026-01〜 |
| `jra_rank_quality_review.py` | 20250630 / 20240630 | 20251231 / 20241231 | 2026 / 2025 |
| `jra_verify_signals.py` | 20250630 | — | **20250701〜**（他が valid と呼ぶ期間を test と呼んでいる） |

さらに **2026-01〜08 の窓は既に何度も使われている**:
v27 の `V27_OUT_WEIGHT=0.5` の選定（「honest 2窓で比較」）、
`OUT_PROB_CUTOFF=0.80` の選定、`jra_rank_redesign_proposal`、そして本監査。
**この窓は焼けていると見なすべき。**

### 制定した内容（2026-08-14・13章で実装済み）

`backend/src/jra_protocol.py`。

| 定数 | 値 | 根拠 |
|---|---|---|
| `TRAIN_END` | `20250630` | 既存スクリプトの事実上の標準。動かさない |
| `VAL_START` | `20250701` | |
| `TEST_START` | **当四半期の初日（四半期ローリング）** | 下記 |
| `VAL_END` / `TRAIN_DATA_END` | `TEST_START` の前日 | 本番モデルの refit 終端でもある |
| 環境変数 | `JRA_TEST_START=YYYYMMDD` | 過去分析の再現用 |

**地方（月次）と違い四半期にした理由**: 中央は開催が週2日で
**年 約3,460レース（月 約288レース）**しかない。指数1位馬の勝率（約28%）を
月次 TEST で測ると標準誤差が **約2.6pt**、tier S に絞ると母集団が月 約55レースまで
落ちて **約6.5pt** になる。**本監査で見つかった改善の実効サイズ（0.4〜0.5pt）を
月次では原理的に判定できない。** 四半期なら約865レースで標準誤差 約1.5pt。

半期（約1.1pt）はさらに鋭いが、中央は季節性が強く窓ごとに開催地が偏る
（夏の小倉・冬の中山等）ため四半期で止めた。代償として**本番モデルは最大3か月古くなる**。

- `record_test_usage()` と `backend/scripts/JRA_TEST_USAGE_LEDGER.md`（開封台帳）を新設
- `BURNED_DECISIONS` に既使用分 6 件を記載済み
- 自動化: `scripts/jra_quarterly_rollover.py` + LaunchAgent
  `com.kiseki.jra-quarterly-rollover`（1/4/7/10 月の 1 日 03:20）

---

## 8. 推奨（hit_tier）の前向き記録 — 移植計画（P5）

### 8.1 なぜ後付けでは作れないか

- **推奨は DB に残らない**（5.4）。`/api/recommendations` は都度算出
- **指数も残らない**。当日の `calculated_indices` v27 行は
  馬体重到着ごとに上書きされ、最後の1回しか残らない（5.2）
- **tier 判定にはオッズが要る**。`recommend_rank` の第一分岐は `market_agree`
  （指数1位＝単勝1番人気か）で、これは**その時点のオッズ**に依存する。
  `odds_history` は 2026-03-28 以降しか無く、しかも保持方針が未定（5.3）

→ 地方 16章とまったく同じ結論: **発走前に撮る以外に手が無い。**

### 8.2 移植する構造（地方 `chihou_place_pick_log.py` に対応）

| | 地方 | 中央（新規） |
|---|---|---|
| テーブル | `chihou.place_pick_races` / `place_picks` | `keiba.hit_tier_races` / `keiba.hit_tier_picks`（仮） |
| 本体 | `services/chihou_place_pick_log.py` | `services/jra_hit_tier_log.py`（仮） |
| API | `POST /api/chihou/place-picks/snapshot` / `settle` | 同型 |
| cron | 毎分 snapshot / 日次 settle | 同型 |
| 集計 | `scripts/chihou_pick_log_report.py` | 同型 |

### 8.3 地方から必ず引き継ぐ設計判断

1. 🔴 **発走時刻を過ぎたレースは撮らない。** 撮り逃しは記録から欠けるが、
   締切間際の資金移動が混ざれば記録自体が look-ahead になる（地方 10.3.1）
2. **推奨が出なかったレース（tier C = 見送り）も `skip_reason` 付きで記録する。**
   中央の hit_tier は「C は推奨しない」ので、**棄権の質**を測れなければ片肺になる
3. **推奨馬だけでなく全出走馬の指数・オッズ・人気を残す。**
   指数が上書きされる以上、「tier 閾値を変えていたら」の事後評価は
   全馬ぶんが残っていなければ二度とできない
4. **判定は本番と同じ関数を呼ぶ**（`confidence.calculate_recommend_rank` /
   `recommender.build_hit_tier_recommendations`）。閾値は `rule_version` として毎行に埋める
5. **オッズ SQL は表示と共有する。** 地方 16.4b で「`VALUES ('win','place')` が
   2列1行になって複勝が丸ごと落ちる」バグを踏んでいる。同じ形を書かないこと

### 8.4 中央固有の論点

- **リード時間**: 地方は発走6分前。中央は 1 日 36R・発走間隔が長いので
  もう少し早く（T-10分 等）でも取りこぼしにくい。ただし**オッズの確定度が下がる**。
  `odds_history` で「T-N分にオッズが何頭ぶん揃っているか」を先に測ること
- **母集団の小ささ**: 中央は開催が週2日・年間約 3,460R。tier S は約 19%（CLAUDE.md）なので
  **月 100〜130 レースしか貯まらない**。地方（月 約140レース）と同程度で、
  **数か月では結論が出ない**ことを最初から織り込むこと

---

## 9. 再現手順

### 9.1 DB

```bash
cd /Users/ysuzuki/GitHub/kiseki-wt/jra/rebuild
set -a && . ./.env && set +a
PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME"
```

```sql
-- 世代の棚卸し
SELECT version, count(*) rows, count(DISTINCT race_id) races,
       min(r.date) d0, max(r.date) d1, max(ci.calculated_at) c1
FROM keiba.calculated_indices ci JOIN keiba.races r ON r.id=ci.race_id
GROUP BY version ORDER BY version;

-- DM 充足率（配信の生命線）
SELECT r.date, count(*) entries,
  round(100.0*count(re.jvan_time_dm)/count(*),1) time_dm,
  round(100.0*count(re.jvan_battle_dm)/count(*),1) battle_dm
FROM keiba.races r JOIN keiba.race_entries re ON re.race_id=r.id
WHERE r.date >= '20260601' AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
GROUP BY 1 ORDER BY 1;

-- 発走前に NULL である列（3.2）
SELECT r.date, count(*) n,
  count(*) FILTER (WHERE r.condition IS NULL OR r.condition='') cond_null,
  count(*) FILTER (WHERE r.head_count IS NULL) hc_null
FROM keiba.races r
WHERE r.date >= '20260801' AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
GROUP BY 1 ORDER BY 1;

-- サブ指数の死活（6章）
SELECT substr(r.date,1,6) ym, count(*) n,
  round(stddev_pop(ci.paddock_index)::numeric,2) padd_sd,
  round(stddev_pop(ci.going_pedigree_index)::numeric,2) going_sd,
  round(stddev_pop(ci.rebound_index)::numeric,2) reb_sd,
  round(stddev_pop(ci.anagusa_index)::numeric,2) ana_sd
FROM keiba.calculated_indices ci JOIN keiba.races r ON r.id=ci.race_id
WHERE ci.version = 26 GROUP BY 1 ORDER BY 1;
```

### 9.2 train/serve 監査（4.2 の表を再生成する）

```bash
cd backend
# 全シナリオ（約 40 秒 / DB 読み込み込み）
.venv/bin/python scripts/jra_train_serve_skew_audit.py \
  --scenarios db,cond_only,morning,t1h_actual,prerace,dm_only,nodm

# 配信条件で学習し直した場合（⑤）
.venv/bin/python scripts/jra_train_serve_skew_audit.py \
  --train-scenario t1h_actual --scenarios db,t1h_actual

# DM を最初から使わないモデル（⑧）
.venv/bin/python scripts/jra_train_serve_skew_audit.py \
  --scenarios db --drop-features jvan_time_dm,jvan_battle_dm

# 死んだ特徴の除去 A/B（6章）
.venv/bin/python scripts/jra_train_serve_skew_audit.py \
  --scenarios db,t1h_actual \
  --drop-features paddock_index,going_pedigree_index,rebound_index
```

⚠️ このスクリプトは **`ci.version = 26` の行に依存する**（4.7）。
学習ソースを付け替えたら本スクリプトも同時に直すこと。

### 9.3 DM 経路の確認

```bash
# Mac 側の取得ログ（saved は「ファイル取得数」で DB 反映数ではない）
tail -40 ~/GitHub/kiseki/logs/dm_auto_fetch.log

# Windows 側の DB 反映ログ（updated=0 が並んでいたら 4.4 の症状）
ssh windows-vm "powershell -NoProfile -Command \"Get-Content 'C:\kiseki\windows-agent\jvnext_dm_importer.log' -Tail 40\""

# 永続ストアにファイルが在るか（在れば取得は成功している）
ssh windows-vm "powershell -NoProfile -Command \"Get-ChildItem 'C:\kiseki\data\dm_1403' -Recurse -Filter '140320260815*' | Measure-Object Length -Average\""
```

---

## 10. 次の一手（P0 完了時点）

| 優先 | やること | なぜ今か |
|---|---|---|
| ~~1~~ | ~~DM 取り込みの修復~~ | ✅ **完了（11章）** |
| ~~2~~ | ~~DM 充足率の死活監視~~ | ✅ **完了（11.4）** |
| ~~3~~ | ~~学習ソースを v26 固定から外す~~ | ✅ **完了（12.1）** |
| ~~5~~ | ~~`weight_change` の読み先修正~~ | ✅ **完了（12.2）** |
| ~~4~~ | ~~TRAIN/VAL/TEST プロトコルを確定~~ | ✅ **完了（13章）** |
| ~~6~~ | ~~モデルの再学習~~ | ✅ **試走・検証済み。デプロイは 2026-10-01 のローリングまで見送り（13.7）** |
| ~~7~~ | ~~前向き記録の実装~~ | ✅ **完了（14章）**。あとは貯まるのを待つ |
| ~~8~~ | ~~死んだ特徴の除去を paired bootstrap で判定~~ | ✅ **完了（15.1）— 不採用** |
| ~~9~~ | ~~`OUT_PROB_CUTOFF` の較正確認~~ | ✅ **現状維持（15.2）**。10/1 のローリングで同四半期比較 |

### 判断済みで蒸し返さないもの

- **`going_pedigree_index` の train/serve 不整合は実害ゼロ** — 馬場状態を欠いても
  順位精度はむしろ僅かに良い（4.2 ②）。仮説として挙がっていたが**否定された**
- **地方 v14 のような「市場特徴の削除」に相当する問題は中央には無い** —
  中央の 34 特徴にオッズ・人気は最初から入っていない
- **`paddock_index` は honest 分割の A/B では評価できない** — TRAIN 期間で定数のため
  モデルが一度も分岐しない（6章）
- **死んだ特徴 3 本は外さない** — VAL 3,450R の paired bootstrap で有意差なし。
  paddock は害を暴くために設計した実験でも点推定が逆方向に出た（15.1）
- **`OUT_PROB_CUTOFF` は 0.80 のまま** — 本番モデルの 2026Q2 実測は設計値と整合（15.2）
- **`odds_history` の win/place 発走前時系列は削らない** — 前向き記録と
  odds 系分析が使う。削るべきは発走後と exotic の中間スナップショットだった（15.4）

---

## 11. P1: DM 取得経路の修復（2026-08-14・完了）

課題#1。**独立した 3 つのバグが重なっていた。** 症状はどれも同じ
「ファイルは取れているのに DB に入らない」で、ログ上は正常終了に見えていた。

### 11.1 バグ① 反映0件を成功として焼き付ける（`jvnext_dm_importer.py`）

4.4 節のとおり。`updated == 0` でも `progress[race_id] = "ok"` を書くため、
以後 `skip_ok` で永久にスキップされていた。

```python
# 修正前
progress[jravan_race_id] = "ok"; ok += 1
# 修正後
if updated > 0:
    progress[jravan_race_id] = "ok"; ok += 1
else:
    progress.pop(jravan_race_id, None)   # 記録を消して次回に再試行させる
    empty += 1
```

`updated=0` になる典型は **DM ファイルの方が出走馬（`race_entries`）より先に届く**ケース。
API は `(race_id, horse_number)` で引くので、出走馬が未取込なら全件 `skipped` を返す。
**これは異常ではなく通常の順序**なので、失敗ではなく「次回やり直す」として扱うのが正しい。

回収用に `--recheck-dates YYYYMMDD,...`（進捗を無視して必ず再 POST）を追加した。

### 11.2 バグ② 新規取得が無い回は取り込みを走らせない（`protocol_dm_orchestrator.py`）

```python
if not no_import and results["total_saved"] > 0:   # ❌
```

①と噛み合って**回復不能ループ**を作っていた。1 回目は「ファイルは取れたが出走馬が無い」で
0 件、2 回目以降は「ファイルは既にある」ので `saved=0` となり**取り込み自体が起動しない**。

対象日を明示して毎回 recheck する形に変えた。あわせて `--all`（全ストア 12,519 ファイル走査・
約 14 分）をやめ、`--recheck-dates <その回の対象日>` にした。

### 11.3 バグ③ 小倉（場コード `10`）でヘッダ行を DM 行と取り違える

1403 ファイルの構造:

```
行0  19文字   '1020260208080910364'   ← ヘッダ。先頭2文字が場コード
行1  459文字  '10718113005877000...'  ← DM レコード（先頭1文字が更新回数）
行4,5         '0001030513040002...'   ← 別種別（更新回数 0）
```

`load_1403_file` は「先頭1文字が数字で 1 以上」の行を DM 行の候補にしていた。
**小倉は場コードが `10` なのでヘッダ行の先頭も `1` になる。**
DM 行の更新回数も 1 だと `max()` が同点になり、**先に現れるヘッダ行**が選ばれる。
ヘッダは 19 文字しか無くレコード領域（`LINE1_HEADER_LEN=23` 以降）に届かないので、
`DM data なし` として**そのレースが丸ごと落ちる**。

長さの下限（`LINE1_HEADER_LEN + RECORD_LEN`）を候補条件に加えて解決した。
更新が入って `upd>=2` になったファイルはたまたま正しく読めていたため、
**小倉だけが常に壊れるのではなく「更新が来なかったレースだけ」が落ちる**という
再現しにくい形になっていた（場別 DM 充足率: 小倉 95.2% で他場と大差なし）。

### 11.4 死活監視を追加

`protocol_dm_orchestrator.py` に `report_unrecovered()` を追加した。
**終了済みの開催日**で DM が入っていないものがあれば `WARNING: DM MISSING (past)` を
出力し、**終了コード 1** を返す。`dm_auto_fetch.sh` はそれを
`ERROR: rc=1` としてログに残し、`launchctl list com.kiseki.dm-auto-fetch` の
`LastExitStatus` にも出る。

未来日は「JV-Next がまだ公開していない」だけのことが多いので警告にしない
（実際 8/16 の DM は 8/14 時点では取得できない）。**過去日の欠損だけが確実な損失**。

### 11.5 回収結果

| 開催日 | 修正前 | 修正後 | 原因 |
|---|---|---|---|
| 2026-07-19 | 78.0% | **99.6%** | ③（小倉12レース） |
| 2026-08-09 | 5.9% | **99.2%** | ①②（出走馬より先に DM 到着 → 焼き付き） |
| 2026-08-15 | 0.0% | **99.2%** | ①②（同上） |
| 2026-08-16 | 6.5% | 6.5% | **未公開**。JV-Next 側にまだ無い（正常。翌日の巡回で入る） |

### 11.6 🟢 本番の指数に現れていた痕跡 — レース内ばらつきが半分に潰れる

DM 欠損は `composite_index` の**レース内ばらつき**に直接出る。
gain の 71.8% を占める 2 列が全馬同値になるため、モデルが馬を分離できなくなる。

| 開催日 | DM 充足 | レース内 幅（平均） | レース内 sd（平均） |
|---|---|---|---|
| 2026-08-08 | 99.6% | **29.36** | 8.79 |
| **2026-08-09** | **5.9%** | **14.07** | **3.87** |
| 2026-08-15（修正前の8R） | 0% | 14.98 | — |
| 2026-08-15（修正・再算出後） | 99.2% | **27.74** | 7.32 |

**幅がちょうど半分に潰れている。** これは表示順の劣化だけでなく、
`confidence.calculate_race_confidence` が**指数差スコアと分散スコアを入力にしている**ため
**tier（S/A/B/C）判定も同時に壊れていた**ことを意味する。
8/9 の推奨は tier が過小に出ていたはずである。

> **この「レース内幅」は DM 欠損の最も安価な検知指標である。**
> 平常 28〜30 に対して欠損日は 14 前後。閾値監視を入れる余地がある（未実装）。

### 11.7 8/15 の再算出（実施済み）

DM を入れ直しただけでは既に書かれた指数は直らないので、本番の算出を叩き直した。

```bash
ssh sekito "API_KEY=\$(grep '^CHANGE_NOTIFY_API_KEY=' ~/GitHub/kiseki/.env | cut -d= -f2- | tr -d '\"')
  curl -s -X POST 'http://127.0.0.1:8003/api/import/calculate?date=YYYYMMDD' -H \"X-API-Key: \$API_KEY\""
```

⚠️ **1 回では全レースが更新されないことがある**（実測: 1 回目は 36 レース中 28 レースのみ。
残り 8 レースは古い値のまま残った）。**再算出のあとは必ずレース内幅で確認すること**:

```sql
WITH per_race AS (
  SELECT ci.race_id, max(ci.composite_index)-min(ci.composite_index) AS spread
  FROM keiba.calculated_indices ci JOIN keiba.races r ON r.id=ci.race_id
  WHERE ci.version=27 AND r.date='YYYYMMDD' GROUP BY 1)
SELECT count(*) races, round(avg(spread)::numeric,2) avg_spread FROM per_race;
-- 平常 28〜30 / DM 欠損日は 14 前後（11.6）
```

⚠️ **レース単位の閾値（例「幅 < 20 なら異常」）で判定してはいけない。**
少頭数や同質なメンバー構成では平常時でも 18〜19 に収まるレースがある
（2026-08-15 は DM 100% でも 3 レースが 20 未満だった）。
**日単位の平均**で見ること。個別に疑うなら、そのレースの DM 充足率を直接見る。

### 11.8 2026-08-09 の指数は直していない（意図的）

DM が入った今なら再算出できるが、**あえてそのままにした**。

- 過去日を今算出すると、馬場状態・確定結果が入った状態での算出になり、
  5.2 節の「live と backfill の混成」を自分で増やすことになる
- **8/9 の行は「DM 欠損時に本番の指数がどう壊れるか」の唯一の実測標本**であり、
  上書きすると失われる（11.6 の表がそれ）

### 11.9 ⚠️ `calculated_at` は UTC と JST が混在している

調査中に踏んだ罠。`_bulk_upsert_for_race` は

- **既存行の更新**: `existing.calculated_at = datetime.now()` ＝ **コンテナのローカル時刻（UTC）**
- **新規行の挿入**: 列のデフォルト `now()` ＝ **DB の時刻（`SHOW timezone` は `Asia/Tokyo`）**

を使っており、**同じ日のデータに 9 時間ずれた 2 種類のタイムスタンプが混ざる**。
「21:05 に書かれた行」と「13:13 に書かれた行」が両方あって混乱したが、
前者は JST（挿入）・後者は UTC（更新）だった。
**`calculated_at` で処理の前後関係を判断してはいけない。** どちらかへ揃えること（未対応）。

---

## 12. P3: train/serve 整合の是正（一部・2026-08-14）

### 12.1 学習ソースの版固定をやめた（課題#3）

4.7 節のとおり `WHERE ci.version = 26` 固定で、本番の v27 移行後は
**学習データが 2026-08-02 で凍結**していた。

`composite.py` に 2 つの定数を追加した:

```python
SUBINDEX_MIN_VERSION = 26          # サブ指数を読んでよい版の下限
SUBINDEX_SOURCE_SQL = """          # (race_id, horse_id) ごとに最大版を1行だけ取る
SELECT DISTINCT ON (race_id, horse_id) *
FROM keiba.calculated_indices
WHERE version >= 26
ORDER BY race_id, horse_id, version DESC
"""
```

**現行版に追従させる（`= COMPOSITE_VERSION`）のも誤り**である。版を上げた直後は
バックフィル前でその版の行が無く、学習が 0 件で落ちる。サブ指数は v26 以降不変
（v27 で変わったのは合成部だけで、`inference_v27.py` も v26 のサブ指数を流用している）
なので、**下限を置いて各馬の最大版を取る**のが正しい。地方の
`CHIHOU_SUBINDEX_MIN_VERSION = 9` と同じ考え方。

適用先: `train_jra_out_rate.FETCH_SQL`（→ `train_jra_reg_rank` /
`jra_train_serve_skew_audit` / `backfill_jra_out_probability` が共有）、
`jra_rank_quality_review.FETCH_SQL`。

効果（実測）:

```
修正前: date range 20230506 〜 20260802
修正後: date range 20230506 〜 20260815
```

⚠️ `backfill_jra_out_probability.py` は**書き込み先**も `version = 26` 固定だった。
v27 移行後は 1 行も更新しないまま正常終了していたので `COMPOSITE_VERSION` に変えた。

⚠️ **v22 等を明示している古いバックテスト（`backtest_dm*.py` 等）は直していない。**
当時の版で評価することが目的なので、固定が正しい。

### 12.2 `weight_change` を `race_entries` から読むようにした（課題#4）

4.6 節のとおり `_get_weight_change_map` が `race_results`（レース確定後にしか
存在しない）だけを見ていたため、**発走前は必ず欠損**していた。

`race_entries.weight_change` は 0B11（速報馬体重・発走の約1時間前）で埋まる。
両表に値がある行での一致を確認してから置き換えた:

| | 行数 |
|---|---|
| 両表に値がある（2025-01 以降） | 70,466 |
| **値が一致** | **70,466** |
| 値が異なる | **0** |

完全一致なので、学習時の分布を変えずに配信時の欠損だけが解消する。
`race_entries` 側が空の馬だけ `race_results` にフォールバックする
（古いデータ向けの保険）。

honest test での効果は **指数1位馬の勝率 +0.39pt**（27.76% → 28.15%）。
検査: `backend/tests/test_weight_change_source.py`（読み先の順序を固定）。

### 12.3 まだやっていない: 配信条件での学習

4.2 ⑤ の「配信条件で学習し直すと 28.29%」は未適用。12.2 で `weight_change` が
配信時にも入るようになったため、**残る不整合は馬場状態 4 列と
`going_pedigree_index` だけ**になった。これらは 4.2 ② のとおり**実害ゼロ**なので、
配信条件での再学習の期待効果は当初の +0.53pt より小さいはずである。
**再測定してから判断すること**（`--train-scenario t1h_actual` は
`weight_change` を欠損させる定義なので、12.2 後は定義を見直す必要がある）。

---

## 13. P2: TRAIN/VAL/TEST プロトコルの制定（2026-08-14・完了）

7章の内容を実装した。**四半期ローリング**（判断: ユーザー・2026-08-14）。

### 13.1 作ったもの

| | |
|---|---|
| 定義 | `backend/src/jra_protocol.py` |
| 開封台帳 | `backend/scripts/JRA_TEST_USAGE_LEDGER.md` |
| 四半期バッチ | `backend/scripts/jra_quarterly_rollover.py` |
| LaunchAgent | `com.kiseki.jra-quarterly-rollover`（1/4/7/10 月の 1 日 03:20・**インストール済み**） |

現在の境界:

```
TRAIN ≤20250630 / VAL 20250701〜20260630 / TEST 20260701〜 (本番学習終端 20260630)
```

### 13.2 🔴 本番モデルの refit 境界を変えた

**これが本章で最も影響の大きい変更。**

`train_jra_reg_rank.py` / `train_jra_out_rate.py` は
**test 期間を含む全期間で refit** していた（`refit_period: [20230506, 20260801]`）。
「未来のレースを予測する運用上は正しい」が、**その結果 DB に入る過去分の指数が
すべて in-sample になり、一度きり評価が成立しない**（`inference_v27.py` の
docstring もこれを警告している）。

`--refit-end`（既定 = `TRAIN_DATA_END` = `TEST_START` の前日）を追加し、
**TEST 期間を学習に含めないようにした**。

> ⚠️ **これは本番モデルの実力をわずかに下げる方向の変更である**
> （直近1四半期ぶんのデータを学習に使わなくなる）。
> それでも入れるのは、**測れないモデルは改善もできない**ため。
> 四半期ごとに `retrain` で境界ごと前進させるので、古さは最大3か月に留まる。

### 13.3 3 フェーズの順序

地方と同じく **evaluate → retrain → backfill**。順序に意味がある。

1. `evaluate` — 前四半期を一度きり評価。DB の指数は前サイクルのモデル（前々四半期までで
   学習）の出力なので**この時点では honest**。先に再学習すると in-sample になる
2. `retrain` — 前四半期までを含めて再学習。旧モデルは `data/backup/jra_model_YYYYMMDD/` へ
3. `backfill` — **デプロイ後に**実行（`inference_v27.py` + `backfill_jra_out_probability.py`）

自動で回すのは 1 と 2 まで。**コミット・デプロイ・backfill は人が実行する**
（外向きの操作なので自動では踏み込まない）。レポート
（`backend/docs/quarterly_rollover/YYYYQn.md`）の末尾に次に打つコマンドが出る。

⚠️ `evaluate` は DB の `calculated_indices` を読むので、
**前四半期のバックフィルを走らせたあとに evaluate してはいけない**（5.2 の混成問題）。

### 13.4 レポートに必ず載せるもの

- **同四半期の過年度比較**。中央は季節性が強く四半期ごとに開催地が偏るため、
  直前の四半期と比べると「劣化した」と誤読する（地方の月次で実際に踏んだ罠）
- **足切りの較正**（除外率・除外馬の実着外率・1着取りこぼし）。
  `OUT_PROB_CUTOFF=0.80` の較正がずれていないかは四半期ごとに見る値

動作確認（2026Q2・864レース）:

```
top1_win 0.3148 / top1_place 0.6447
cut_rate 0.3073 / cut_actual_out_rate 0.9110 / cut_missed_win 0.0416
```

足切りの較正は学習時の設計値（除外30% / 実着外率 88.7% / 1着取りこぼし 5.0%）と
整合している。ただし**この数値は in-sample**（13.2 の変更前のモデルの出力）。

### 13.5 ⚠️ 最初の真に honest な評価は 2026Q4（2027-01 実行）

- 2026-08-14 時点のモデルは全期間 refit で作られている
- 13.2 の境界変更を反映するには `retrain` が要る（未実施）
- 2026Q3 は既に進行中で、その指数は旧モデルが書いている

したがって **2026Q3 の評価も厳密には honest ではない**。
台帳の冒頭にもこの但し書きを入れてある。

### 13.6 再学習の試走と、危うく誤読しかけた数字（2026-08-14）

`--phase retrain` を実際に走らせて経路を確認した（モデルは**デプロイしていない**・13.7）。

```
学習データ: 154,466行 / 11,319レース (20230506〜20260809)   ← 12.1 で終端が伸びた
train=101,612 valid=47,273 test=5,581
refit: 148,885行 (20230506〜20260628)                       ← 13.2 で TEST を除外
honest test: spearman=0.4612
```

**旧モデルの honest test は 0.5086 だったので、一見 0.047 の劣化に見える。**
しかし両者は **test 窓が違う**（旧 2026-01〜08 / 新 2026-07〜）。
同一の学習設定で窓だけを揃えて測り直すと:

| 学習設定 | test 窓 | レース | Spearman |
|---|---|---|---|
| valid_end=20251231（旧） | 2026-01〜08 | 2,154 | 0.5068 |
| valid_end=20251231（旧） | **2026-07以降のみ** | 432 | **0.4612** |
| valid_end=20260630（新） | 2026-07〜 | 432 | **0.4612** |

**完全に一致する。** 差はモデルではなく**夏開催（小倉・新潟・札幌・函館）が
単に難しい**ことによる窓効果だった。valid 窓を半年から1年に広げても
test 窓の結果は 1 ミリも動いていない。

> 🟢 **これが 13.4 で「同四半期の過年度比較を必ず載せる」とした理由そのものである。**
> 四半期をまたいで数字を比べると、季節性を性能変化と読み違える。
> 中央は四半期ごとに開催地が総入れ替えになるので地方より影響が大きい。

⚠️ **足切りの較正は少し動いた**（新: 除外25.4% / 実着外率0.880 / 1着取りこぼし4.4%。
CLAUDE.md の記載は 除外30% / 88.6% / 4.8%）。これも夏の窓の効果か
較正のずれかは切り分けていない。**`OUT_PROB_CUTOFF` を動かす前に同四半期比較で見ること。**

### 13.7 再学習したモデルはデプロイしていない（判断）

試走で作ったモデルは**破棄し、本番モデルは元のまま**に戻した
（退避は `backend/data/backup/jra_model_20260814/` にある）。理由:

1. **測れる範囲では改善していない。** 同一窓での Spearman は 0.4612 で旧と同値。
   新モデルの利点は「TEST を含まないので**今後**評価が成立する」ことであって、
   今の精度が上がることではない
2. **学習データは 1.5 か月ぶん減る**（refit 終端 2026-08-01 → 2026-06-28）。
   デプロイすると本番の入力は確実に少し古くなる
3. 差し替えの自然なタイミングは **2026-10-01 の四半期ローリング**（Q3 を評価してから
   Q3 込みで再学習する）。今やると Q3 の評価機会を 1 回捨てることになる

> **デプロイするなら**: `--phase retrain` → モデルをコミット → PR/デプロイ →
> `--phase backfill` → 当日・翌日の `calculate` を叩く（14.4b 相当の穴に注意）。

---

## 14. P5: 推奨（hit_tier）の前向き記録（2026-08-15 実装）

8章の設計を実装した。

### 14.1 🔴 後付けで作れない理由は 3 つあり、どれ 1 つでも致命的

1. `/api/recommendations` は**都度算出**で DB に何も残さない（5.4）
2. `calculated_indices` の現行 version 行は馬体重到着ごとに上書きされ、
   バックフィルで丸ごと置き換わる（5.2）
3. **tier の第一分岐 `market_agree` は発走直前まで動く**

3 番目は今回はじめて実測した。2026-08 の 4 開催日・144レースで、
各時点の単勝1番人気が**確定1番人気と一致する割合**:

| 発走何分前 | オッズが取れているレース | 1番人気が確定と一致 |
|---|---|---|
| 30分 | 138/144 | 72.5% |
| 15分 | 140/144 | 77.1% |
| **10分** | **140/144** | **80.7%** |
| 5分 | 141/144 | 83.7% |
| 2分 | 141/144 | 86.5% |

**発走2分前ですら 13.5% のレースで1番人気が入れ替わる。**
`recommend_rank` は market_agree を第一分岐にしているので、
**確定オッズから tier を作り直しても、ユーザーが見た tier とは約2割ずれる。**

> これは地方 5.4 の「日中ユーザーに提示された指数は DB に残らない」と同型だが、
> 原因が違う。地方は**再算出で上書きされる**からで、中央は**そもそも保存していない**
> ことに加えて**入力そのものが時間とともに動く**から。

### 14.2 撮影リードを 10 分にした根拠

上表から選んだ。3 点の折り合い:

- **賭けられる時点であること**。締切は発走1〜2分前。30分前は早すぎて実勢と離れ、
  2分前では「見て買う」時間が無い
- **オッズ充足が頭打ちになる点**（10分前で 140/144。5分前でも 141 で大差ない）
- 一致率 80.7% で、そこから先の改善が緩やか（5分 83.7 / 2分 86.5）

⚠️ **`odds_history.fetched_at` は UTC・DB のセッション TZ は `Asia/Tokyo`。**
`now()` と直接比べると 9 時間ずれる。最新オッズは**最大時刻からの相対**で絞ること
（`_latest_win_place_odds` / `recommender._collect_race_data` と同じ手）。
`calculated_indices.calculated_at` の UTC/JST 混在（11.9）とは別の食い違いなので注意。

### 14.3 作ったもの

| | |
|---|---|
| テーブル | `keiba.hit_tier_races`（レース単位）/ `keiba.hit_tier_picks`（出走馬単位） |
| マイグレーション | `alembic/versions/202608150500_jra_add_hit_tier_log.py` |
| 本体 | `backend/src/services/jra_hit_tier_log.py` |
| API | `POST /api/jra/hit-tier/snapshot` / `POST /api/jra/hit-tier/settle?date=` |
| cron | `scripts/jra_pick_snapshot_trigger.sh`（毎分）/ `scripts/jra_pick_settle_trigger.sh`（日次） |
| 集計 | `backend/scripts/jra_pick_log_report.py --start --end` |
| 検査 | `backend/tests/test_jra_hit_tier_log.py`（21 tests） |

### 14.4 設計上、意図してそうしていること

1. 🔴 **発走時刻を過ぎたレースは撮らない。** 撮り逃しは記録から欠けるが、
   締切後のオッズが混ざれば記録自体が look-ahead になり**欠けているより悪い**。
   `is_snapshot_due()` が発走後を必ず弾き、テストで固定している
2. **推奨が出なかったレース（tier=C）も記録する。** hit_tier は C を見送るので、
   棄権側が無いと「見送って正解だったか」を一切測れない。
   `skip_reason` に理由（`tier_c` / `no_odds` / `no_index`）を残す
3. **推奨馬だけでなく全出走馬を記録する。** 指数が上書きされる以上、
   「tier の閾値を変えていたら」「指数2位も買っていたら」の事後評価は、
   全馬ぶんがここに無ければ二度とできない。着外率と足切りフラグも残す
4. **判定は本番と同じ関数**（`calculate_race_confidence` / `is_market_favorite` /
   `calculate_market_chaos` / `calculate_recommend_rank`）を呼ぶ。
   閾値は `rule_version` として毎行に埋まるので、変更しても世代が自動で分かれる
5. **settle 時に「確定オッズでの tier」も計算して保存する。**
   これで「発走前 tier」と「確定 tier」の差＝オッズの動きが追加コスト無しで測れる。
   14.1 の 80.7% を**自分たちのデータで**継続的に確認できる

### 14.5 読み取り専用ドライラン（2026-08-15）

本番 DB に対して書き込みなしで判定だけ流した:

```
新潟  1R post=0940 tier=S   conf=93 agree=True  | 1位 3番 フルーツバスケット 2.9倍
中京  1R post=0950 tier=C+  conf=83 agree=False | 1位 15番 ブラックミューズ 2.7倍
札幌  1R post=1000 tier=C+  conf=62 agree=False | 1位 6番 フレンチブラッサム 4.7倍
新潟  2R post=1010 tier=C   conf=60 agree=False | 1位 9番 テリオスブギ 10.0倍
新潟  3R post=1040 tier=S   conf=75 agree=True  | 1位 2番 リボンロード 1.4倍

36レース  tier内訳 S5 / A2 / B7 / C+8 / C14  推奨22 / 棄権14
オッズが取れているレース: 36/36
```

⚠️ **この tier 分布を live の結果と比べてはいけない。** この時点で読めるオッズは
前夜 21:00 JST のもので、発走10分前の値ではない。SQL と判定経路が通ることの
確認以上の意味は無い。

### 14.6 デプロイ手順（実施済み・2026-08-15）

main への push で CI の Blue-Green デプロイが走り、その中で `alembic upgrade head` まで
自動実行される。人手で要るのは **cron 登録だけ**。

```bash
# 1. migration は CI が実施（確認: keiba.alembic_version = 202608150500_jra）
# 2. 疎通確認（対象が無ければ 0 件で戻る）
ssh sekito "API_KEY=\$(grep '^CHANGE_NOTIFY_API_KEY=' ~/GitHub/kiseki/.env | cut -d= -f2-)
  curl -s -X POST 'http://127.0.0.1:8003/api/jra/hit-tier/snapshot' -H \"X-API-Key: \$API_KEY\""

# 3. cron 2本を追加（⚠️ VPS の TZ は JST。crontab も JST で書く）
* * * * *   /home/ysuzuki/GitHub/kiseki/scripts/jra_pick_snapshot_trigger.sh
45 23 * * * /home/ysuzuki/GitHub/kiseki/scripts/jra_pick_settle_trigger.sh

# 4. 開催日の翌日に記録されているか確認
cd backend && .venv/bin/python scripts/jra_pick_log_report.py --start YYYYMMDD --end YYYYMMDD
```

`45 23` にしたのは地方の settle（`30 23`）と 15 分ずらすため。中央の成績確定は
最終レース（16:30 前後）の直後には揃うので、時刻そのものに強い制約は無い。

**稼働開始: 2026-08-15。** 同日の第1レース（新潟1R・発走 09:40 JST）が初回の対象。

### 14.7 いつ結論が出るか

中央は年 約3,460レース・週 約72レース。ドライランの比率（推奨 61%）が続くとして
**週 約44レース・月 約190レースの推奨**が貯まる。

tier S は 36 レース中 5（14%）＝ **月 約27レース**しかない。
tier 別の的中率を ±5pt の精度で見るには S だけで数百レース要るので、
**tier 別の結論は最短でも 2026Q4〜2027Q1**。全体の的中率なら 1〜2 か月で形が見える。

⚠️ **記録が貯まるまで、この運用点の実力は「CLAUDE.md に書かれた tier 別勝率
（S 45〜51% / A 33〜40% / B 27〜35%）」のままである。** それらは
2026年の窓（既に焼けている・7章）で測られた値であり、前向きの確認はまだ 1 件も無い。

---

## 15. P4/P6: 残りの判定と整理（2026-08-15）

### 15.1 🔴 死んだ特徴の除去は「効果なし」— 不採用

課題#6。6章の「31特徴にすると +0.53pt」を**レース単位 paired bootstrap** で検定した。
`backend/scripts/jra_feature_drop_ab.py`。

⚠️ **特徴量の採否は条件探索なので VAL（2025-07〜2026-06）で判定した。**
TEST を使うと一度きりの窓が焼ける（7章）。

**実験1 `val`**（train ≤2024-12-31 / 評価 2025-07〜2026-06・**3,450R**）
除去: `going_pedigree_index` + `rebound_index`（paddock は TRAIN で定数のため効かない）

| 指標 | 現行34特徴 | 除去 | 差 | 95%CI | 判定 |
|---|---|---|---|---|---|
| 指数1位 勝率 | 27.86% | 28.00% | +0.14pt | [−0.46, +0.73] | 有意差なし |
| 指数1位 複勝率 | 61.07% | 61.51% | +0.43pt | [−0.20, +1.04] | 有意差なし |
| Spearman | 0.5100 | 0.5104 | +0.0004 | [−0.0009, +0.0018] | 有意差なし |

**実験2 `paddock`**（train ≤2026-02-29 = paddock が**生きている**期間 /
評価 2026-05〜06 = paddock が**死んだ**期間・600R）

これは**本番と同じ状況を意図的に作った実験**である。本番は全期間 refit なので
paddock が生きていた 2025-07〜2026-04 の分岐を持ち、配信では必ず定数 50 側へ落ちる。
「今は死んでいる特徴を学習に残しておくと害があるか」を測れる唯一の形。

| 指標 | 現行 | paddock 除去 | 差 | 95%CI | 判定 |
|---|---|---|---|---|---|
| 指数1位 勝率 | 28.50% | 27.67% | **−0.83pt** | [−2.17, +0.50] | 有意差なし |
| Spearman | 0.5114 | 0.5086 | −0.0027 | [−0.0057, +0.0003] | 有意差なし |

> **結論: 3 本とも外さない。** 6章の +0.53pt はノイズだった。
> paddock に至っては、害を暴くために設計した実験で**点推定が逆方向**（外すと悪化）に出た。
> 「配信時に定数だから外すべき」という直感は、少なくとも中央のこの構成では成立しない。
> LightGBM は定数入力を単に無視するので、害が出るのは
> 「定数だがモデルがそこに強く依存している」場合に限られる。

⚠️ **`paddock_index` は honest 分割（TRAIN ≤2025-06-30）では検証できない。**
TRAIN 期間で完全な定数なのでモデルが一度も分岐せず、外しても数値が
**小数点以下5桁まで一致する**。この罠を踏まないよう、スクリプトは学習期間の sd が
0 の列を検出して警告を出す。

### 15.2 足切り閾値 `OUT_PROB_CUTOFF` は現状維持

課題#9。13.6 で「除外25.4%」が出て較正のずれを疑ったが、**本番モデルの実測は健全**:

| | 除外率 | 除外馬の実着外率 | 1着取りこぼし |
|---|---|---|---|
| 学習時の設計値 | 30% | 88.6% | 4.8% |
| **本番モデル × 2026Q2（864R）** | **30.7%** | **91.1%** | **4.2%** |
| 再学習モデル × 2026-07〜（432R） | 25.4% | 88.0% | 4.4% |

25.4% は「**別のモデル × 別の窓**」の値で、稼働中のモデルの話ではない。
**動かす理由が無い。** 10/1 のローリングで 2026Q3 を同四半期比較（2024Q3 / 2025Q3）
しながら再確認する（レポートに自動で出る）。

⚠️ 足切りは Web のグレーアウト＝ユーザーに見える挙動を直接変える。
除外を増やすと 1着の取りこぼしも増える（0.78 で 3着内 6.6% / 0.85 で 2.9%）。

### 15.3 `calculated_indices` の世代整理（実施済み）

課題#10。**2,097,240 行（テーブルの 77.8%）を削除**した（2,696,099 → 597,211 行）。
⚠️ 15.4 と同じく**実サイズ（817MB）はすぐには縮まない**（DELETE は領域を再利用可能に
するだけ）。効果は「今後しばらく増えない」こと。
`backend/scripts/prune_calculated_indices.py`（既定は dry-run）。

🔴 **「v26 以降だけ残す」では既存スクリプトが 9 本壊れる。** 調べたところ
古い版を明示指定しているものが残っていた:

| version | 参照 |
|---|---|
| 22 | `backtest_dm{,_signal,_signal_segments}.py` / `backtest_combined_signals.py` |
| 24 | `inference_v26.py` / `train_v26_lightgbm.py` / `jra_ensemble_weight_sweep.py` 他 |

→ **残すのは v22 / v24 / v26 / v27 の 4 世代**にした。
削除できる行数は 2,097,240（全体の 77.8%）で、**「v26 以降だけ」にした場合との差は
約 50MB / 全体の 6%**。スクリプトを 1 本も壊さずにほぼ同じ効果が得られる。

- `SUBINDEX_MIN_VERSION` と `COMPOSITE_VERSION` は削除指定できないようガードしてある
- 実行前に当日の DB バックアップ（03:30 JST）を確認すること。**復元以外に戻す手段は無い**
- `VACUUM FULL` は打たない（排他ロック）。autovacuum に任せる

### 15.4 `odds_history` の刈り込み（実施済み）

課題#11。13GB / 60.7M 行・年 約18GB 増。
`backend/scripts/prune_odds_history.py`（既定は dry-run）。

**実測した内訳**（2026-08 の 4 開催日・9,878,382 行）:

| 帯 | 行数 | 割合 | うち win/place |
|---|---|---|---|
| 発走前 0〜60分 | 4,273,706 | 43.3% | 296,632（**7%**） |
| 発走前 60〜180分 | 586,398 | 5.9% | 100% |
| 発走前 180〜360分 | 618,214 | 6.3% | 100% |
| 発走前 360分超 | 460,134 | 4.7% | 100% |
| **発走後** | **3,939,930** | **39.9%** | 84% |

⚠️ **選択肢にあった「発走前N分のみ残す」は採らなかった。** 理由は 2 つ:

- **60分より前は 100% が win/place** で、そこを削ると
  `jra_phase4a_odds_movement_analysis.py`（前半20%点と後半20%点のオッズ比）が
  意味を失う。しかも削減効果は **16.9% しかない**
- **削るべきは発走後（39.9%）と exotic（3連単等）の中間スナップショット**だった。
  発走前60分以内の **93% が exotic** で、行数を支配しているのはこちら

採った方針:

| policy | 内容 | 削減 |
|---|---|---|
| `post` | 発走後に書かれた行を削除。realtime が終わったレースを叩き続けた空回りで、**読み手が無い**（確定オッズは `race_results.win_odds` にある） | 39.9% |
| `exotic` | exotic 券種は**発走前の最終スナップショットだけ残す**。`odds_history` の exotic 時系列を読むスクリプトは**1本も無い**（3連単の検証は `race_payouts` の実払戻を使う） | 27.4% |

**実績: 42,218,369 行を削除**（`post` 31,686,390 / `exotic` 10,531,979）。
**60,699,974 → 19,246,186 行（−68.3%）**。増加ペースは 18GB/年 → 約 6GB/年になる。

🔴 **ただしテーブルの実サイズ（13GB）はすぐには縮まない。** PostgreSQL の DELETE は
行を「死んだタプル」にするだけで、autovacuum はそのページを**再利用可能にする**が
OS へは返さない。したがって効果は「**縮む**」ではなく「**しばらく増えなくなる**」である
（削除した 42M 行ぶんの領域を今後の書き込みが埋めていく）。

実際に縮めたいなら開催の無い日に `VACUUM FULL` を打つ必要があるが、
**排他ロックを取り一時的に同容量の空きディスクを要求する**ので、
本番稼働中には絶対に打たないこと。急ぐ理由が無ければ再利用に任せるのが安全。

⚠️ **exotic 券種の収集開始は 2026-06-14。** `win`/`place` は 2026-03-28 からあるが、
trio / trifecta / exacta / quinella / quinella_place は 6月半ばからしか無い
（刈り込みの実行ログで 6/13 までは `exotic=0` と出るのはこのため。異常ではない）。
**exotic の時系列はそもそも 2 か月ぶんしか存在しない。**
**`win` / `place` の発走前時系列は一切触っていない**（前向き記録・
`jra_odds_cross_bettype_arbitrage`・`jra_phase4a` が使う）。

副次効果: `jra_phase4a` の「late 20%点」は**発走後の値で汚染されていた**
（時系列をフィルタ無しで読んでいる）。`post` の削除でこれが直る。
**過去に出したその分析の数値は、汚染された状態のもの**である点に注意。

⚠️ **日付ごとに回すこと。** 全期間を 1 本のクエリでやると `fetched_at > post_utc` を
支える索引が無く、バッチのたびに 6,000万行を全走査して返ってこない（実際に踏んだ）。
`race_id` には索引があるので、対象レースを絞ってから条件を当てる。

---

*作成: 2026-08-14 / ブランチ `feat/jra-rebuild`*
