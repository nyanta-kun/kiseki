# Netkeiba スクレイピング仕様

## 概要

JV-Dataで取得不可能なフィールドをnetkeibaからスクレイピングして補完する。

---

## 取得対象フィールド（JV-Data取得不可）

| フィールド | 内容 | 表示例 |
|-----------|------|-------|
| **ペース** | レース前半ペース区分 | S（スロー）/ M（ミドル）/ H（ハイ）|
| **不利フラグ** | コーナー通過時の不利・出遅れ | 通過順位に丸印で表示 |
| **レース短評** | 1行のレース評コメント | 「後方一気」「出遅れ」等 |

> **注意**: 1着馬名・2着馬名はSEレコードから取得可能だが実装不要の判断。

---

## Netkeibaの更新タイミング

| タイミング | 更新内容 | 備考 |
|-----------|---------|------|
| 前週日曜 18時頃 | 特別登録 | G1は前々週 |
| 水曜 20時まで | 出走想定 | プレミアムコースのみ |
| 木曜 18時頃 | 出走確定 |  |
| レース前日 11時頃 | 枠順確定 | **スクレイピング実行タイミング** |

---

## スクレイピング戦略

### 基本方針

- **スクレイピングするのは「前走」のみ**
  - 前々走以上はすでに過去のレースとして格納済み（または取得済みのはず）
  - 枠順確定後に各馬の直近レースページを1回だけ取得
- **1回の出馬表確定で最大18頭分** → 1レースあたり最大18リクエスト
- **重複取得を防ぐ**: 同一race_id + horse_idの組み合わせはキャッシュして再取得しない

### IP制限対策

| 対策 | 内容 |
|-----|------|
| リクエスト間隔 | 最低 **3〜5秒** のランダムウェイト |
| User-Agent | 一般ブラウザと同等のヘッダーを設定 |
| 1日あたり上限 | 土日のレース最大で約 36レース × 18頭 = 648リクエスト。分散して送信 |
| 冪等性 | 同一(race_id, horse_id)の取得済みレコードがあれば再スクレイピングしない |
| エラー時 | 429 / 403 を受信したら即時停止し、翌日リトライキューに積む |

### スクレイピング実行タイミング

```
枠順確定（前日11時頃）
    ↓
出走馬リスト（race_entries）を取得
    ↓
各馬の前走 race_id を特定（race_results の最新1件）
    ↓
netkeiba レースページをスクレイピング（3〜5秒間隔）
    ↓
netkeiba_race_extras テーブルへ格納
```

---

## ログイン方法（確認済み）

```
POST https://regist.netkeiba.com/account/
Content-Type: application/x-www-form-urlencoded

pid=login&action=auth&login_id={NETKEIBA_USER_ID}&pswd={NETKEIBA_PASSWORD}&return_url2=&mem_tp=
```

- レスポンスCookieの `nkauth` がセッションキー
- その後 `https://www.netkeiba.com/` にGETしてCookieをドメイン全体に確立
- 以降 `db.netkeiba.com` でもセッションが有効

## スクレイピング対象URL

```
https://db.netkeiba.com/race/{netkeiba_race_id}/
```

> **netkeiba race_id 形式**: `YYYYJJKKHHRR`（12文字）
> JV-Link race_id（16文字）とは異なる。変換方法は別途要調査。

## 取得フィールドとHTML構造（確認済み）

### ① ペース（S/M/H）

ラップ行から前半・後半を抽出して自力計算：

```html
<tr>
  <th>ペース</th>
  <td class="race_lap_cell">6.9 - 17.9 - ... - 151.5&nbsp;(29.7-35.6)</td>
</tr>
```

```python
# (29.7-35.6) → 前半29.7秒、後半35.6秒
diff = front - back  # 前半 - 後半
# diff < -1.0 → "S"（スロー：前半ゆっくり）
# diff > +1.0 → "H"（ハイ：前半速い）
# それ以外   → "M"（ミドル）
```

> ※JV-Dataのlap_timesからも同じ計算が可能。スクレイピング不要な場合は自力計算を優先。

### ② 備考（出遅れ・不利・レース短評）- 馬ごと

結果テーブルの「備考」列（プレミアム）。クラス指定なし、列位置で特定：

```html
<td nowrap="nowrap">

出遅れ

  <div class="txt_c">
</td>
```

- 値の例: `出遅れ`、`内に張られる`、`後方一気`、空欄
- `diary_snap_cut` タグで囲まれた直前のtdが備考列

### ③ 注目馬 レース後の短評（プレミアム）

```html
<table summary="注目馬 レース後の短評">
  <tr><th>1着:ミュージアムマイル</th></tr>
  <tr><td>スタートは速くなく…（長文テキスト）</td></tr>
  <tr><th>2着:コスモキュランダ</th></tr>
  <tr><td>序盤から前へ…</td></tr>
</table>
```

- 上位入線馬のみ掲載（全馬分はなし）

### ④ 分析コメント（プレミアム）

```html
<th>分析コメント</th>
<td>序盤はコスモキュランダ…（レース全体の流れ説明）</td>
```

---

## DB設計（新規テーブル）

```sql
CREATE TABLE keiba.netkeiba_race_extras (
    id              SERIAL PRIMARY KEY,
    race_id         VARCHAR(16)     NOT NULL REFERENCES keiba.races(id),
    horse_id        VARCHAR(10)     NOT NULL REFERENCES keiba.horses(id),
    pace            VARCHAR(1),                 -- S / M / H
    disadvantage    BOOLEAN DEFAULT FALSE,      -- 不利フラグ（丸印の有無）
    race_comment    VARCHAR(100),              -- レース短評テキスト
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (race_id, horse_id)
);
```

---

## 実装モジュール構成

```
backend/src/importers/
    netkeiba_scraper.py     # スクレイピングロジック（HTMLパース）
    netkeiba_importer.py    # DB格納・重複チェック

backend/src/
    scheduler/
        netkeiba_job.py     # 枠順確定トリガー後に実行するジョブ
```

---

## 注意事項

- Netkeibaの利用規約の範囲内での個人利用を前提とする
- 商用利用・大量取得は規約違反となる可能性があるため注意
- HTML構造変更によりパーサーが壊れることがある。定期的に確認すること
- 速報ページ（odds, entry）ではなく **確定成績ページ（result.html）** をターゲットとする
