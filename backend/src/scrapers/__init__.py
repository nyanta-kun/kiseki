"""外部サイトのスクレイパ（sekito から移設）。

2026-09-06 の統合 Phase 2 で、sekito 側 `backend/scripts/bin/scrape/*` にあった
稼働中スクレイパを kiseki へ移した。移設の狙いは 2 つ:

  1. **sekito スキーマ依存を実データテーブルだけに絞る。** 移設前のスクレイパは
     対象レースを `sekito.races` から引いていたが、その `sekito.races` 自体が
     `keiba.races` / `chihou.races` からの日次同期（sekito の
     `sync-jra-from-jvlink` / `sync-nar-from-umaconn`）で作られていた。
     kiseki から直接読めば同期ジョブが 2 本まるごと不要になる。
     2026-09-06 実測で両者は完全一致する（中央 直近14日 180/180 差分 0、
     地方 15 日ぶんすべて差分 0）。
  2. **静かな停止を減らす。** 同期の遅れ・取りこぼしが「スクレイプ対象 0 件」に
     化けて success を返す経路が消える（[[sekito-silent-data-breakage]] の構図）。

書き込み先は移設時点では sekito スキーマのまま（`sekito.anagusa` /
`sekito.kichiuma` / `sekito.netkeiba` / `sekito.data_fetch_status`）。
テーブルの引っ越しは sekito UI を落とす段階でまとめて行う。ここで同時に
動かすと、切り替え中に sekito 側の画面が空になる。
"""
