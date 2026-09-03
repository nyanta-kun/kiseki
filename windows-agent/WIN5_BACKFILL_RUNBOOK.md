# WIN5 バックフィル 実行手順（Windows 実機）

> **この作業は Windows(Parallels) 上でしか実行できない。**
> 2011年4月以降の全 WIN5（約750回）を1回だけ取り込む。

## 0. 前提

| 確認すること | どうやって |
|---|---|
| `keiba.win5_events` / `win5_legs` / `win5_payouts` が存在する | 本番適用済み（PR #439・2026-09-02 確認） |
| `/api/import/win5` が生きている | PR #442 |
| **実機のパーサ更新は不要** | パースはサーバ側で行う（下記 1.） |
| メンテナンス窓を外している | **毎週火 08:00-15:00 は JVOpen を呼ばない**（`JVLINK_MAINTENANCE_WINDOWS`） |
| TARGET が JV-Link を掴んでいない | JV-Link は同時1接続のみ |
| 🔴 **`jvlink_agent.py --mode realtime` が止まっている** | 下記 1. |

## 1. 🔴 JV-Link の排他を確保する

**JV-Link は同時1接続のみ。** 実機では `jvlink_agent.py --mode realtime` が
常駐して接続を掴んでいる（2026-09-02 実測: `pythonw.exe` PID 2100）。
これを止めないと `JVOpen` は通らない。

```
REM 稼働中のものを確認
wmic process where "name='pythonw.exe'" get ProcessId,CommandLine

REM realtime を止める（開催日を避けること）
taskkill /PID <jvlink_agent の PID> /F
```

⚠️ **開催日（土日）に止めてはいけない。** realtime はオッズ・馬体重・
速報成績を約30秒ごとに拾っており、止めると当日の指数が欠ける。
JRA が開催しない平日（火曜のメンテナンス窓を除く）に行うこと。

⚠️ バックフィル終了後は必ず realtime を戻すこと。

### パーサについて（2026-09-02 に方針変更）

**実機の `jvlink_parser.py` を更新する必要は無い。**
`win5_backfill.py` は WF の生レコードをそのまま POST し、
`/api/import/win5` がサーバ側で `parse_wf` する。

🔴 更新しようとして分かったこと（実機で確認済み）:
- 実機のパーサは **2026-05-04 付**で main より4か月古い
- main のパーサは `from ..bet_types import BET_TYPES` という**相対 import** を
  持つため、実機へ置くと単体 import できず、
  **既存の HR 払戻経路（`from jvlink_parser import parse_hr`）まで壊れる**
- したがって「パーサをコピーする」運用そのものが成立しない。
  `/api/import/weights`（0B11）が生を送ってサーバでパースしているのと同じ形にした

## 2. 調査モード — WF がどのファイル名に入るかを実測する

WF の**ファイル名接頭辞は未確認**なので、推測でスキップ規則を書かない。
まず POST せずに内訳だけ見る。

```
python win5_backfill.py --from-year 2011 --option 4 --discover
```

- `--option 4` は**ダイアログ無しセットアップ**（全再ダウンロード）。JVOpen が**数時間ブロックする**。
  `--option 3` はモーダルダイアログを出すことがあり、閉じる者がいないと COM ごと固まる
- ログ末尾に「ファイル名の先頭1文字ごとの rec_id 内訳」が出る。
  `WF はここ` と付いた接頭辞を控える
- この段階では **DB を一切触らない**

## 3. 本実行

```
# 接頭辞が分かった場合（速い）
python win5_backfill.py --from-year 2011 --option 4 --only-prefix <控えた文字>

# 分からなかった場合（遅いが確実。未処理ファイルを全部読む）
python win5_backfill.py --from-year 2011 --option 4
```

## 4. 🔴 実行後の確認 — 200 が返ったことは取り込めた証拠にならない

0B11（速報馬体重）は **200 を返し続けながら全件捨てていた**。必ず実体で確認する。

```sql
-- 開催数（WIN5 は2011年4月開始。年約50回 × 15年 ≒ 750 前後を期待）
SELECT count(*) AS events,
       min(held_date) AS oldest,
       max(held_date) AS newest
FROM keiba.win5_events;

-- 🔴 対象レースが races に解決できたか（unresolved が残っていないか）
SELECT count(*) FILTER (WHERE race_id IS NULL) AS unresolved,
       count(*)                                AS total_legs
FROM keiba.win5_legs;

-- 払戻とキャリーオーバーが入っているか
SELECT count(*) AS payout_rows,
       count(*) FILTER (WHERE payout > 0) AS with_payout
FROM keiba.win5_payouts;

SELECT held_date, carryover_start, carryover_balance, no_hit_flag
FROM keiba.win5_events
WHERE carryover_start > 0
ORDER BY held_date DESC LIMIT 10;
```

**期待値と、外れたときの疑い先**

| 症状 | 疑うところ |
|---|---|
| `events = 0` | completed の共有 / `--only-prefix` の絞りすぎ / WF が届いていない |
| `unresolved > 0` | `races` の取込が先に済んでいない（RA/SE を先に流す） |
| `with_payout = 0` | 中止レコードの弾き過ぎ、または区分7が届いていない |
| 最古が 2011-04 より新しい | `--option 4`（ダイアログ無しセットアップ）を使っていない |

⚠️ `win5_backfill.py` 自身も、1件も取り込めなかった場合・未解決が残った場合・
サーバ側でパースできなかった場合にログへ 🔴 を出す。ログの最終行を必ず読むこと。

⚠️ **終わったら realtime を戻す**（1. を参照）。

## 5. 取り込めたら

過去の**実際の WIN5 対象5レース**が入るので、`jra_win5_min_hit_points.py` を
proxy（その日の最後の5レース）ではなく実集合で回し直せる。

```
cd backend
.venv/bin/python scripts/jra_win5_min_hit_points.py --race-ids <実際の5レース>
```

そのうえで**最初にやるべき分析**は、地方の重勝式が示した形の再現である
（`backend/docs/chihou_triple_umatan_2026_09_02.md`）:

> 上位K頭を機械的に買った場合の15年 ROI を、プール平均 EV と並べる。
> 地方では 上位2頭 0.547 / 3頭 0.590 / 4頭 0.654 / 5頭 0.615 / 6頭 0.405 と
> **広げるほど悪化し、全てプール平均 0.773 を下回った**。
> WIN5 も控除率30%・pari-mutuel・キャリーオーバーありという同じ構造なので、
> 同じ形が出るなら WIN5 も的中率商品としてしか成立しない。

**推奨生成や画面より先にこれを測ること。**
