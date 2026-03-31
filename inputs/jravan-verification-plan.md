# JRA-VAN DATALAB 大口投票検出 有効性検証計画書

## 関連文書

- 設計書: `jravan-odds-detector-design.md`（2026-03-29 v1.0）

---

## 1. 検証目的

本格的なシステム実装に先立ち、以下の仮説を統計的に検証する。

**仮説:**
最終単勝オッズ10倍以上の馬において、前日発売開始直後1時間以内に大口投票（オッズ急落）が検出された馬は、検出されなかった馬と比較して馬券内率（3着以内率）に統計的な優位性がある。

**検証の意義:**
優位性が認められない場合、システムの本格実装に進む合理性が乏しい。逆に有意差が認められれば、検出精度の向上やリアルタイム監視への投資が正当化される。

---

## 2. 検証設計

### 2.1 対象母集団

| 項目 | 条件 |
|------|------|
| 対象期間 | 直近1年間（0B41の保証提供期間内） |
| 対象レース | 中央競馬 全レース（特別・一般問わず） |
| 対象馬 | 最終確定単勝オッズ 10.0倍以上 |
| 除外条件 | 出走取消馬、競走除外馬、競走中止馬 |

**期待されるサンプルサイズ:**

- 年間約3,400レース（2場開催×12R×約144日）
- 1レースあたりオッズ10倍以上の馬は平均8〜12頭程度
- 推定母集団: 約27,000〜41,000頭

### 2.2 群分け

対象馬を以下の2群に分類する。

**検出群（Treatment）:** 前日発売開始後1時間以内にオッズ急落が検出された馬

**非検出群（Control）:** 同条件でオッズ急落が検出されなかった馬

### 2.3 「大口投票検出」の操作的定義

時系列オッズ(0B41)のスナップショットを用い、以下を満たす場合に「大口投票あり」と判定する。

```
判定条件:
  1. 前日発売開始（金曜19:00想定）から1時間以内のスナップショットが存在する
  2. 当該区間内の連続する2スナップショット間で、単勝オッズが以下を満たす
     - 下落率 ≥ 15%
     - かつ下落幅 ≥ 3.0倍

     算出式:
       下落率 = (odds_t1 - odds_t2) / odds_t1
       下落幅 = odds_t1 - odds_t2

       ※ odds_t1: 先のスナップショットのオッズ
       ※ odds_t2: 後のスナップショットのオッズ（odds_t2 < odds_t1）
```

**補足:**
- 設計書の検出条件A（下落率20%以上、下落幅5.0倍以上）より緩い閾値を初期値とする
- 検証結果を踏まえて感度分析で閾値を調整する（後述）
- スナップショット間隔が数分〜10分のため「1時間以内」は最初のスナップショットの発表時刻で判定する

### 2.4 評価指標

#### 主要指標

| 指標 | 定義 | 比較方法 |
|------|------|---------|
| 馬券内率（3着以内率） | 3着以内に入った割合 | 検出群 vs 非検出群 |

#### 副次指標

| 指標 | 定義 | 目的 |
|------|------|------|
| 勝率（1着率） | 1着になった割合 | 大口投票の「勝ちを見込んだ投票」度合い |
| 連対率（2着以内率） | 2着以内に入った割合 | 中間的な指標 |
| 単勝回収率 | 検出群に100円均等投資した場合の回収率 | 投資観点での優位性 |
| 複勝回収率 | 検出群に複勝100円均等投資した場合の回収率 | 同上 |

### 2.5 統計的検定

#### 主検定

馬券内率の群間差に対して以下を適用する。

```
帰無仮説 H0: 検出群の馬券内率 = 非検出群の馬券内率
対立仮説 H1: 検出群の馬券内率 > 非検出群の馬券内率（片側検定）

検定手法: カイ二乗検定（独立性の検定）
有意水準: α = 0.05
```

#### 効果量

```
オッズ比 (Odds Ratio):
  OR = (検出群の馬券内 / 検出群の馬券外) / (非検出群の馬券内 / 非検出群の馬券外)

  OR > 1 → 検出群が馬券内に入りやすい
  95%信頼区間がすべて1を超える → 有意
```

#### サンプルサイズの事前検討

```
想定値:
  非検出群の馬券内率（ベースライン）: 約15%（オッズ10倍以上の馬の一般的な3着内率）
  検出可能な最小効果: 馬券内率が20%以上（5ポイント差）
  検出力: 0.80
  有意水準: 0.05（片側）

  → 必要サンプルサイズ: 検出群 約500頭以上

  ※ 検出群のサンプルが500未満の場合、Fisherの正確確率検定に切り替える
```

---

## 3. データ収集手順

### 3.1 フェーズ構成

```
Phase 0: 環境準備・パーサー実装        （1〜2日）
Phase 1: データ収集                    （1〜3日）
Phase 2: データクリーニング・前処理      （1日）
Phase 3: 検出・群分け                  （1日）
Phase 4: 統計検定・分析                （1日）
Phase 5: 感度分析・考察                （1日）
                              合計目安: 6〜9日
```

### 3.2 Phase 0: 環境準備

設計書 Section 6.1 に基づき、最小限のモジュールを実装する。

**必要モジュール（検証用最小構成）:**

```
verification/
├── jvlink_proxy/               # Windows側
│   ├── main.py                 # FastAPI（最小エンドポイント）
│   ├── jvlink_client.py        # JV-Link COM操作
│   └── parsers/
│       ├── o1_parser.py        # O1レコードパーサー ★最重要
│       ├── ra_parser.py        # RAレコードパーサー
│       └── se_parser.py        # SEレコード（着順取得用）
│
├── analysis/                   # 分析側（Mac可）
│   ├── collect_timeseries.py   # 0B41一括収集スクリプト
│   ├── collect_results.py      # レース結果収集スクリプト
│   ├── detect_anomaly.py       # 大口投票検出
│   ├── verify_hypothesis.py    # 統計検定スクリプト
│   └── report.py               # 結果レポート生成
│
├── db/
│   └── schema.sql              # 検証用テーブル定義
│
└── config.yaml                 # 設定
```

**O1パーサーの実データ検証（Phase 0 内で必須）:**

```
手順:
  1. 直近開催の1レースに対して 0B31 でO1レコードを取得
  2. 生データをダンプし、設計書 Section 3 のバイトマッピングと照合
  3. JV-Data仕様書PDF (JV-Data490.pdf) のP12と突合
  4. 差異があればパーサーを修正
  5. 複数レース・複数時刻のデータでパース結果を目視確認
```

### 3.3 Phase 1: データ収集

#### 収集データ一覧

| # | データ | データ種別 | 取得単位 | 推定データ量 |
|---|-------|-----------|---------|------------|
| 1 | 時系列オッズ（単複枠） | 0B41 | 日付単位 | 約144日 × 数千レコード |
| 2 | レース詳細 | 蓄積系 RA | セットアップ or 日付 | 約3,400レース |
| 3 | 馬毎レース情報（着順） | 蓄積系 SE | セットアップ or 日付 | 約48,000頭 |
| 4 | 確定オッズ | 0B31 (区分4) | 日付単位 | 約3,400レース |

#### 収集スクリプト処理フロー

```python
# collect_timeseries.py 概要

for date in target_dates:
    # 1. 時系列オッズ取得
    records = proxy_client.get("/api/odds/timeseries/{date}")
    
    for record in records:
        parsed = o1_parser.parse(record)
        
        # race_key + snapshot_time でユニーク
        db.insert_snapshot(parsed.header)
        
        for horse in parsed.horses:
            db.insert_horse_odds(snapshot_id, horse)
    
    # 2. レース情報・結果取得
    race_info = proxy_client.get("/api/race/info/{date}")
    for race in race_info:
        db.insert_race(race)
        for horse in race.horses:
            db.insert_result(race_key, horse.umaban, horse.finish_order)
```

#### 前日発売スナップショットの特定方法

```
前日発売開始時刻の判定:
  - 土曜開催分: 金曜 19:00（PAT前日発売開始）
  - 日曜開催分: 土曜のレース終了後（通常16:30〜17:00頃）

  ※ 0B41のスナップショットの「発表月日時分」から
    最も早い時刻のレコードを発売開始直後とみなす

  1時間以内の判定:
    first_snapshot_time = そのレースの最初のスナップショット時刻
    target_window = first_snapshot_time + 60分
    → target_window 以内のスナップショットを「発売開始直後1時間」区間とする
```

### 3.4 Phase 2: データクリーニング

```
除外処理:
  1. 出走取消・競走除外のフラグが立った馬を除外
  2. 最終確定オッズが10.0倍未満の馬を除外
  3. 前日発売区間のスナップショットが2件未満のレースを除外
     （オッズ変動を比較できないため）
  4. オッズが「***」（取消表示）の馬を除外

データ品質チェック:
  - スナップショット数の分布確認（レースあたり何件か）
  - 前日発売区間のスナップショット間隔の分布確認
  - オッズ値の範囲チェック（異常値の有無）
```

### 3.5 Phase 3: 検出・群分け

```python
# detect_anomaly.py 概要

for race in all_races:
    snapshots = db.get_snapshots(race_key, window="pre_sale_1h")
    
    if len(snapshots) < 2:
        continue  # 比較不可
    
    for umaban in race.horses:
        if final_odds[umaban] < 10.0:
            continue  # 対象外
        
        detected = False
        for i in range(len(snapshots) - 1):
            odds_t1 = snapshots[i].odds[umaban]
            odds_t2 = snapshots[i+1].odds[umaban]
            
            if odds_t1 is None or odds_t2 is None:
                continue
            if odds_t2 >= odds_t1:
                continue  # 下落なし
            
            drop_rate = (odds_t1 - odds_t2) / odds_t1
            drop_abs  = odds_t1 - odds_t2
            
            if drop_rate >= 0.15 and drop_abs >= 3.0:
                detected = True
                break
        
        # 群分け結果をDBに記録
        db.insert_group_assignment(race_key, umaban, 
                                   group="DETECTED" if detected else "CONTROL",
                                   final_odds=final_odds[umaban],
                                   finish_order=results[umaban])
```

### 3.6 Phase 4: 統計検定

```python
# verify_hypothesis.py 概要

import scipy.stats as stats
import numpy as np

# データ取得
detected   = db.query("SELECT * FROM group_assignments WHERE group='DETECTED'")
control    = db.query("SELECT * FROM group_assignments WHERE group='CONTROL'")

# --- 主要指標: 馬券内率（3着以内率） ---
det_in  = sum(1 for h in detected if h.finish_order <= 3)
det_out = len(detected) - det_in
ctl_in  = sum(1 for h in control if h.finish_order <= 3)
ctl_out = len(control) - ctl_in

det_rate = det_in / len(detected)
ctl_rate = ctl_in / len(control)

# カイ二乗検定
contingency = [[det_in, det_out], [ctl_in, ctl_out]]
if min(det_in, det_out, ctl_in, ctl_out) < 5:
    # 期待度数が小さい場合はFisherの正確確率検定
    odds_ratio, p_value = stats.fisher_exact(contingency, alternative='greater')
else:
    chi2, p_value, dof, expected = stats.chi2_contingency(contingency)
    odds_ratio = (det_in * ctl_out) / (det_out * ctl_in)

# 信頼区間（オッズ比の95%CI）
log_or = np.log(odds_ratio)
se_log_or = np.sqrt(1/det_in + 1/det_out + 1/ctl_in + 1/ctl_out)
ci_lower = np.exp(log_or - 1.96 * se_log_or)
ci_upper = np.exp(log_or + 1.96 * se_log_or)

# --- 副次指標 ---
# 勝率
det_win_rate = sum(1 for h in detected if h.finish_order == 1) / len(detected)
ctl_win_rate = sum(1 for h in control if h.finish_order == 1) / len(control)

# 連対率
det_ren_rate = sum(1 for h in detected if h.finish_order <= 2) / len(detected)
ctl_ren_rate = sum(1 for h in control if h.finish_order <= 2) / len(control)

# 単勝回収率
det_win_return = sum(h.win_payout for h in detected if h.finish_order == 1)
det_win_roi = det_win_return / (len(detected) * 100) * 100

ctl_win_return = sum(h.win_payout for h in control if h.finish_order == 1)
ctl_win_roi = ctl_win_return / (len(control) * 100) * 100

# 複勝回収率
det_place_return = sum(h.place_payout for h in detected if h.finish_order <= 3)
det_place_roi = det_place_return / (len(detected) * 100) * 100

ctl_place_return = sum(h.place_payout for h in control if h.finish_order <= 3)
ctl_place_roi = ctl_place_return / (len(control) * 100) * 100
```

### 3.7 Phase 5: 感度分析

検出閾値を変動させ、結果の頑健性を確認する。

```
パラメータグリッド:
  下落率閾値:  [0.10, 0.15, 0.20, 0.25, 0.30]
  下落幅閾値:  [2.0, 3.0, 5.0, 7.0, 10.0]
  対象オッズ帯: [10倍以上, 20倍以上, 30倍以上, 50倍以上]
  時間窓:      [30分, 1時間, 2時間, 3時間]

各組み合わせで:
  - 検出群のサンプルサイズ
  - 馬券内率
  - p値
  - オッズ比
  を算出し、ヒートマップで可視化
```

---

## 4. 判定基準

### 4.1 Go / No-Go 判定

検証結果に基づき、本格実装に進むかを以下で判断する。

| 判定 | 条件 | 次のアクション |
|------|------|--------------|
| **Go（実装着手）** | p < 0.05 かつ OR ≥ 1.3 かつ検出群100頭以上 | 設計書Phase 1〜7を順次実装 |
| **条件付きGo** | p < 0.10 かつ OR ≥ 1.2 | 閾値調整・対象期間拡大で再検証 |
| **No-Go** | p ≥ 0.10 または OR < 1.2 | 仮説の見直し、別アプローチ検討 |

### 4.2 Go時の閾値決定

感度分析の結果から、以下のバランスが最良となるパラメータセットを本番閾値として採用する。

```
選定基準:
  1. 統計的有意性が維持される（p < 0.05）
  2. 検出群のサンプルサイズが実用上十分（年間100件以上目安）
  3. オッズ比が最大化される（シグナルの質）
  4. 回収率が非検出群を上回る（実用的価値）
```

### 4.3 No-Go時の代替検討方針

| 代替案 | 概要 |
|-------|------|
| 時間窓の拡大 | 1時間 → 前日発売全区間に広げて再検証 |
| 馬連・三連系への展開 | 単勝ではなく馬連(0B42)のオッズ変動で検証 |
| 複合シグナル | オッズ急落 + 別の指標（調教、血統等）の組み合わせ |
| 閾値の根本見直し | 「下落率」ではなく「推定投入金額」ベースに切り替え |

---

## 5. レポート出力項目

### 5.1 基本統計

```
■ データ概要
  対象期間: YYYY/MM/DD 〜 YYYY/MM/DD
  対象レース数: X,XXX レース
  対象馬数（オッズ10倍以上）: XX,XXX 頭
  前日発売スナップショット: 平均 X.X 件/レース（中央値 X 件）
  スナップショット平均間隔: X.X 分

■ 群分け結果
  検出群: XXX 頭（全体の X.X%）
  非検出群: XX,XXX 頭

■ 検出群の内訳
  オッズ帯別:
    10〜19.9倍: XXX 頭
    20〜29.9倍: XXX 頭
    30〜49.9倍: XXX 頭
    50〜99.9倍: XXX 頭
    100倍以上:   XXX 頭
```

### 5.2 主要結果

```
■ 馬券内率（3着以内率）
  検出群:    XX.X% (XXX / XXX)
  非検出群:  XX.X% (X,XXX / XX,XXX)
  差:        +X.X ポイント

■ 統計検定
  検定手法: カイ二乗検定 / Fisherの正確確率検定
  p値:      X.XXXX
  オッズ比:  X.XX (95%CI: X.XX - X.XX)
  判定:     有意 / 非有意

■ 副次指標
                    検出群     非検出群     差
  勝率:             X.X%       X.X%       +X.X pt
  連対率:           X.X%       X.X%       +X.X pt
  単勝回収率:       XXX%       XX%        +XX pt
  複勝回収率:       XXX%       XX%        +XX pt
```

### 5.3 感度分析結果

```
■ 閾値別 馬券内率ヒートマップ（下落率 × 下落幅）
■ オッズ帯別 馬券内率比較グラフ
■ 時間窓別 検出精度推移グラフ
■ 最適パラメータセットの推奨値
```

### 5.4 Go / No-Go 判定結果

```
■ 総合判定: Go / 条件付きGo / No-Go
■ 推奨閾値（Go判定の場合）
■ 次のアクション
```

---

## 6. 既知のリスクと対策

| リスク | 影響 | 対策 |
|-------|------|------|
| 前日発売1時間以内のスナップショットが少ない | 検出群が極端に小さくなる | 時間窓を段階的に拡大して感度分析 |
| 前日発売がない日程（月曜開催等） | 対象外になる | 当日朝の発売開始直後も検証対象に追加 |
| スナップショット間隔のバラつき | 同じ「1時間」でも取得密度が日によって異なる | 間隔の分布を記述統計で報告、外れ値を確認 |
| 出走取消によるオッズ急変 | 大口投票と誤検出 | 出走取消フラグと時刻を照合し除外 |
| 生存者バイアス | 大口投票後にさらに投票され最終オッズが10倍未満に | 「1時間時点でのオッズ10倍以上」条件でも並行検証 |
| 多重比較問題 | 感度分析で偶然有意になるパラメータが出る | 主検定は事前定義した1つの閾値で実施。感度分析は探索的と明記 |

---

## 7. スケジュール

```
Week 1
  Day 1-2: Phase 0 - 環境準備
    - FastAPIプロキシ最小構成の実装
    - O1パーサー実装 + 実データでバイト構造検証
    - 検証用DBスキーマ作成

  Day 3-5: Phase 1 - データ収集
    - 0B41 時系列オッズ（直近1年分）一括取得
    - レース情報・結果取得
    - 確定オッズ取得

Week 2
  Day 6: Phase 2 - クリーニング・前処理
    - 除外処理、スナップショット間隔分析
    - 前日発売区間の特定

  Day 7: Phase 3 - 検出・群分け
    - 大口投票検出スクリプト実行
    - 群分け結果の記述統計確認

  Day 8: Phase 4 - 統計検定
    - 主検定（カイ二乗 / Fisher）
    - 副次指標算出

  Day 9: Phase 5 - 感度分析・レポート
    - パラメータグリッド探索
    - レポート作成
    - Go / No-Go 判定
```

---

*文書作成日: 2026-03-29*
*バージョン: 1.0*
*前提文書: JRA-VAN DATALAB 大口投票検出システム 設計書 v1.0*
