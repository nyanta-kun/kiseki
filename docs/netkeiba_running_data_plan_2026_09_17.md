# netkeiba 走行データ（個別ラップ・走行距離）の取得設計（2026-09-17）

マスターコース契約は**未加入**。契約したらすぐ回せるところまでを用意し、
DDL と本取得は契約後に行う。

## 1. 何が取れるか（2026-09-15〜17 実測）

| データ | 収録範囲 | 手元にあるか |
|---|---|---|
| レースラップ | 全平地 | 🔴 **ある**（JRA-VAN `races.lap_times` と 35/35 一致） |
| 個別ラップ（200mごと） | 2023重賞 + 2024以降 | 合計と上がり3Fは手元で出せる。**区間配分だけが新規** |
| 走行距離（200mごと・合計） | 2025以降 | **無い（完全に新規）** |
| 距離補正走破タイム | 2025以降 | 走破タイム × 距離 ÷ 走行距離の割り戻し＝走行距離があれば導出できる |
| 内外の位置 | 2025以降 | **無い**（`passing_*` は前後の位置で、内外ではない） |
| 完歩ピッチ・ストライド | 2024以降の**重賞のみ** | 無い。母集団が小さく当面使えない |

🔴 **1着馬サンプルで確認した「新規性」**（2026-09-06・35レース）:
前半の合計タイムは `finish_time − last_3f` と同じで新規ではない。
新しいのは**前半内の区間配分**（先頭馬比で最大 0.1〜1.4秒）と
**走行距離のロス**（1着馬で +0.5〜20.3m。札幌・中山のダート 1700-1800m で 12〜20m）。

## 2. 取得の制約

- 🔴 契約が無いとサーバは**1着馬1頭分しか返さない**。`is_master` で判定する
  （`src/scrapers/netkeiba/running_data.py`）。偽のまま貯めると**勝ち馬だけの標本**になる
- 公開は**レース翌週の金曜18時頃**。当日には存在しない
- 1レース1ページで3タブ分が入る（追加リクエスト不要）
- レート制限は既存の `rate_limiter.RateLimiter` に従う（実測 7.7〜8.7 秒/リクエスト）

### 所要時間の見積り

| 範囲 | レース数 | 所要（8秒/件） |
|---|---|---|
| 個別ラップ 2024-01〜 | 約 9,000 | 約 20 時間 |
| 走行距離 2025-01〜 | 約 5,000 | 約 11 時間 |

お試し14日間に収まる。開催の無い平日・深夜に分割して回す。

## 3. 🔴 学習で使うときの規律（公開遅れ）

過去走の特徴として使うとき、**その走の走行データが対象レース日までに公開されて
いたか**で絞る。公開時刻 = レース日の翌週金曜18時（JST）。

    使える条件: publication_at < 対象レース日の発走

これを無視すると、中1週・連闘の馬で「本番には無い情報」を学習に入れることになる。
JRA-VAN ラップ由来の特徴（`lap_form.py`）にはこの問題が無い（当日に揃う）。

⚠️ 走行距離は 2025-01 以降しか無い。`jra_protocol.TRAIN_END = 20250630` なので
**学習に使える期間は約半年**しかない。2026Q1 以降の walk-forward では学習側が
薄いまま評価することになるので、効果が出なくても「情報が無い」とは言えない。

## 4. 保存の設計（DDL は契約後・shared 柱なので単独 PR）

生ページは再解析できるように保存し、解析結果は別に持つ。

    data/netkeiba_running/<YYYYMMDD>/<netkeiba_race_id>.html.gz   生ページ
    data/netkeiba_running/<YYYYMMDD>/parsed.jsonl                  解析結果（1行1頭）

DB へ入れるときの案:

```sql
CREATE TABLE keiba.netkeiba_running_race (
    race_id            integer PRIMARY KEY REFERENCES keiba.races(id),
    netkeiba_race_id   varchar(12) NOT NULL,
    fetched_at         timestamp   NOT NULL,
    published_at       timestamp,           -- レース翌週金曜18時（JST）
    is_master          boolean     NOT NULL,
    n_horses           smallint    NOT NULL,
    race_laps          jsonb                -- 念のため。JRA-VAN と一致するはず
);

CREATE TABLE keiba.netkeiba_running_horse (
    race_id            integer  NOT NULL REFERENCES keiba.races(id),
    horse_number       smallint NOT NULL,
    laps               jsonb,               -- 200m ごとの区間タイム（秒）
    furlong_distances  jsonb,               -- 200m ごとの走行距離（m）
    positions          jsonb,               -- 200m ごとの内外の位置
    running_distance   numeric(7,1),
    finish_time        numeric(5,1),
    corrected_time     numeric(5,1),
    pitch              jsonb,
    stride             jsonb,
    PRIMARY KEY (race_id, horse_number)
);
```

- `horse_id` ではなく `horse_number` で持つ（ページに馬 ID が無い。突き合わせは
  `race_results` の馬番で行う）
- 🔴 **`is_master=false` の取得は `netkeiba_running_horse` へ入れない**。
  1着馬だけの行が混ざると「充足率」を見誤る
- 再取得は UPSERT。生ページを残してあるので、解析の直しは再取得なしでやり直せる

## 5. 契約したら最初にやること（順番）

1. `is_master` が真になったことを 1 レースで確認する
2. **数日分（100レース程度）だけ取り、目で見る**。1着馬の値が従来と一致するか、
   全頭ぶん入っているか、走行距離のロスが妥当な範囲か
3. 2025-01〜の走行距離を取り切る → 特徴量にして A/B（事前登録を別に書く）
4. 効果が確かめられたら 2024-01〜の個別ラップも取る

⚠️ 3 の A/B は `jra_lap_feature_ab.py` の `lap` 腕を土台にする（v28 + JRA-VAN
ラップ7列 vs それ + 走行データ）。**JRA-VAN ラップの上乗せがあるか**を見るのが目的で、
v28 との比較ではない。
