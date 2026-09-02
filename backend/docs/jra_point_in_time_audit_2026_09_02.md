# JRA サブ指数の point-in-time 監査（2026-09-02）

## 1. 違反の所在

`backend/src/indices/` の過去成績を集計する12モジュールのうち、**2つに日付上限が無い**。

| モジュール | 日付境界 | 状態 |
|---|---|---|
| `pedigree.py` `SireStatsCache._load()` の6本のSQL | **無し** | 🔴 違反 |
| `frame_bias.py` `_compute_frame_stats()` | **無し** | 🔴 違反 |
| `jockey.py` / `course_aptitude.py` / `going_pedigree.py` / `jockey_trainer_combo.py` / `last3f.py` / `speed.py` / `pace.py` / `rebound.py` / `career_phase.py` / `distance_change.py` / `meet_bias.py` / `rotation.py` ほか | `Race.date < before_date` あり | OK |

`pedigree.py` の各クエリの WHERE は
`rr.finish_position IS NOT NULL AND rr.abnormality_code = 0 AND p.sire IS NOT NULL
AND ra.course IN (...)` のみで、**`ra.date` の条件が無い**。

## 2. なぜ再学習では消えないか

学習は `calculated_indices` の**遡及生成値**を読む
（`train_jra_out_rate.FETCH_SQL` → `SUBINDEX_SOURCE_SQL`）。
バックフィルした過去行の `pedigree_index` は「全期間の種牡馬統計」で作られており、
**モデルを学習し直しても入力側の汚染はそのまま残る**。
直すには指数側に日付境界を入れて **全期間をバックフィルし直す**必要がある。

`pedigree_index` は v26 の feature gain **3位**、v24 線形和では**最大重み 0.202**。

## 3. 汚染の大きさ（実測・2026-09-02）

### 3.1 統計に占める「そのレースより後」の割合

2024-04〜06 のレース × 種牡馬 1,886 組:

- 種牡馬統計のうち**そのレースより後**のデータ: **平均 31.3% / 中央値 27.3%**
- 平均サンプル: レース時点まで 4,767 件 / 以後 1,940 件

### 3.2 統計値そのものはどれだけ動くか

同じ母集団で、種牡馬勝率を as-of（PIT）と全期間（現行）で比べる
（as-of サンプル30件以上・7,339 組）:

| | 値 |
|---|---|
| 平均勝率 as-of | 0.0831 |
| 平均勝率 全期間 | 0.0808 |
| \|差\| 平均 | **0.0069**（水準 0.081 に対し **相対 8.5%**） |
| \|差\| 中央値 | 0.0041 |
| \|差\| p95 | 0.0252 |
| 相関 | **0.927** |

→ **違反は実在するが、統計値の動きは中程度。**
サンプルの3割が未来由来である一方、種牡馬勝率は緩やかにしか動かないため、
順位はおおむね保たれる（推測: 指数の順位への影響はさらに小さい）。
**モデルのエッジ不足（市場への上乗せ ΔAUC +0.0025）を説明する規模ではない。**

## 4. 対応方針

| いつ | 何を |
|---|---|
| **この PR** | 違反の所在と規模を記録し、`test_index_point_in_time.py` で**違反リストが増えないよう**固定した。**コードは変えていない** |
| 次の四半期ローリング（2026-10-01） | 日付境界を入れる → 全期間バックフィル → 再学習を1セットで行う。単独でやると指数だけ変わってモデルとずれる |

**この PR で直さない理由**: 日付境界を入れると `SireStatsCache` はレース日ごとに
別の集計を持つことになり、キャッシュ設計の作り直しと全期間バックフィルが要る。
バックフィルせずに指数側だけ変えると、**本番の指数と学習データの指数が別物**になり、
v14 が市場特徴で踏んだのと同じ train/serve 不整合を作る。

---

## 付録: 報告されていた「DM 欠損の定数 50.0」は train/serve skew ではない

並行調査で「`composite.py:530-531` が DM 欠損を 50.0 で埋めており、
LightGBM の欠損分岐が使えない」と報告されたが、**学習側も同じ 50.0 で埋めている**。

```
backend/scripts/train_jra_out_rate.py:114-118
    # 推論側は sub-indices 欠損を 50.0、jvan 欠損を 50.0 で埋めるため学習側も揃える
    df[subidx] = df[subidx].fillna(50.0)
    df["jvan_time_dm"] = df["jvan_time_dm"].fillna(50.0)
    df["jvan_battle_dm"] = df["jvan_battle_dm"].fillna(50.0)
```

→ **train/serve は一致している。** 「50.0 を NaN にする」のは
*両側を同時に変えて再学習する*モデリング判断であって、片側だけ直すと
**逆向きの skew を作る**。

DM 欠損日の性能低下（28.10% → 22.81%・DM 抜きモデルなら 26.46%）は実測だが、
これは「DM に依存したモデルが DM を失うと弱い」という話で、
埋め方の不整合ではない。対策は**DM 抜きフォールバックモデル**であり、
NaN 化はその一部として次の再学習で A/B するのが筋。
