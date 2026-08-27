#!/usr/bin/env python3
"""既存の `keirin.type_lab_picks` に答え合わせ用の2列を埋める（2026-08-27 新設）。

    # 決着の三連単オッズ（採点済みの行すべて・軽い）
    python scripts/backfill_type_lab_outcome.py --odds --from 2026-01-01 --to 2026-08-27

    # 指数の並び（paper の四半期 walk-forward ぶん・/tmp/race_type_board.npz から）
    python scripts/backfill_type_lab_outcome.py --order-from-board --from 2026-01-01 --to 2026-08-04

    # 指数の並び（モデルを回して復元する。paper は月次 vintage / live は本番モデル）
    python scripts/backfill_type_lab_outcome.py --order-from-models --mode paper \
        --from 2026-08-05 --to 2026-08-26

🔴🔴 **見るのは「行が合っているか」ではなく「ソースが正しいか」。**
   突き合わせられるのは `axis1`/`axis2` ＝ **並びの先頭2つだけ**で、3位以下は
   照合しようがない。そして「先頭2つが合っていれば3位以下も合っている」は
   **成り立たない**:

     2025-07-15 の実測 — **違うソース**で復元した 47 行のうち
       完全一致 24 / **先頭2つだけ一致 23** / 不一致 0

   つまり違うソースだと、先頭2つが合った行でも**半分は3位以下が違う**。
   3位以下こそが「順当（3着が指数3〜4位）」と「軸2+穴（指数5〜7位）」を分ける情報。

   幸い**ソースが正しいかは食い違い率で判る**。実測値は極端に分かれる:

     | 復元ソース | 対象 | 先頭2つの食い違い率 |
     |---|---|--:|
     | 板 npz | paper 2026-01〜08-04 | **0.00%** ✅ 正しいソース |
     | 月次 vintage | paper 2026-08-05〜08-26 | **0.00%** ✅ |
     | 月次 vintage | paper 2025-01 | **0.07%**（2/2,797）✅ |
     | 板 npz | paper 2025 全期間 | **34%** 🔴 違うソース |

   → 食い違い率が `AXIS_MISMATCH_LIMIT_PCT` を超えたら**その範囲は1行も書かない**。
   下回れば「同じソース＋端数の同点」とみなし、食い違った行だけ飛ばして書く。

   ⚠️ 2026-08-27 に「先頭2つが合った行だけ書く」実装で 2025年ぶん 16,864 行を
   埋めてしまい、上の実測に気づいて全部 NULL へ戻した。

🔴 **`--order-from-models` は当日の本番バッチと同時に走らせないこと。**
   特徴量の構築とモデル読み込みでメモリを食う。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from src.database import get_connection  # noqa: E402

#: 復元ソースが正しいかの判定線（先頭2つの食い違い率・%）。
#: 🔴 「行が合っているか」ではなく「**ソースが正しいか**」を見るための数字。
#:    正しいソースなら実測 0.00〜0.07%、違うソースだと 34% と桁で分かれるので、
#:    1% はどちらからも十分に離れている（docstring の表を参照）。
AXIS_MISMATCH_LIMIT_PCT = 1.0


# ───────────────────────── 決着の三連単オッズ ─────────────────────────

def fill_win_tf_odds(date_from: str, date_to: str, mode: str | None) -> None:
    """採点済みで `win_tf_odds` が空の行を `wt_odds` から埋める。

    `win_combo` は券種によって "1-2-3"（三連単）と "1=2=3"（三連複）が混ざるので、
    **着順から三連単の並びを引き直す**（三連複の行にも三連単のオッズを入れる）。
    """
    where = "settled_at IS NOT NULL AND win_tf_odds IS NULL AND race_date BETWEEN ? AND ?"
    params: list = [date_from, date_to]
    if mode:
        where += " AND mode = ?"
        params.append(mode)
    with get_connection() as c:
        rows = c.execute(
            f"SELECT id, race_key FROM type_lab_picks WHERE {where}", tuple(params)
        ).fetchall()
    if not rows:
        print("[odds] 対象なし")
        return
    keys = sorted({r[1] for r in rows})
    print(f"[odds] 対象 {len(rows):,} 行 / {len(keys):,} レース")

    fin = _finish(keys)
    odds = _tf_odds(keys, fin)
    upd = []
    for pid, rk in rows:
        f = fin.get(rk)
        if not f:
            continue
        o = odds.get(rk)
        if o is None:
            continue
        upd.append((float(o), pid))
    with get_connection() as c:
        if upd:
            c.executemany("UPDATE type_lab_picks SET win_tf_odds = ? WHERE id = ?", upd)
        c.commit()
    print(f"[odds] 埋めた {len(upd):,} 行 / 引けなかった {len(rows) - len(upd):,} 行")


def _finish(keys: list[str]) -> dict[str, tuple[int, int, int]]:
    """{race_key: (1着, 2着, 3着)}。1〜3着がそろったレースだけ。"""
    out: dict[str, dict[int, int]] = defaultdict(dict)
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = keys[i:i + 900]
            q = ("SELECT race_key, frame_no, finish_order FROM wt_entries "
                 f"WHERE race_key IN ({','.join('?' * len(ch))}) "
                 "AND finish_order IS NOT NULL")
            for rk, fn, fo in c.execute(q, ch).fetchall():
                out[rk][int(fo)] = int(fn)
    return {k: (v[1], v[2], v[3]) for k, v in out.items()
            if all(i in v for i in (1, 2, 3))}


def _tf_odds(keys: list[str], fin: dict) -> dict[str, float]:
    """{race_key: 決着した 1-2-3 の三連単確定オッズ}。"""
    want = {rk: "-".join(str(x) for x in f) for rk, f in fin.items()}
    out: dict[str, float] = {}
    with get_connection() as c:
        for i in range(0, len(keys), 900):
            ch = [k for k in keys[i:i + 900] if k in want]
            if not ch:
                continue
            q = ("SELECT race_key, combination, odds_value FROM wt_odds "
                 f"WHERE race_key IN ({','.join('?' * len(ch))}) "
                 "AND bet_type = 'trifecta'")
            for rk, comb, od in c.execute(q, ch).fetchall():
                if str(comb).replace("→", "-").replace("=", "-") != want.get(rk):
                    continue
                try:
                    v = float(od)
                except (TypeError, ValueError):
                    continue
                if 0 < v < 99999:
                    out[rk] = v
    return out


# ───────────────────────── 指数の並び ─────────────────────────

def fill_order_from_board(date_from: str, date_to: str) -> None:
    """`/tmp/race_type_board.npz`（四半期 walk-forward）の p3 から並びを復元する。"""
    import numpy as np
    path = Path("/tmp/race_type_board.npz")
    if not path.exists():
        raise SystemExit("[order] /tmp/race_type_board.npz がありません "
                         "（`python scripts/build_race_type_board.py` で作る）")
    zf = np.load(path, allow_pickle=True)
    keys = [str(k) for k in zf["KEY"]]
    p3 = zf["P3"]
    board = {k: {c: float(p3[i][c - 1]) for c in range(1, 8)}
             for i, k in enumerate(keys)}
    _apply_order(date_from, date_to, "paper", board, "board")


def fill_order_from_models(date_from: str, date_to: str, mode: str) -> None:
    """モデルを回して並びを復元する。

    paper は月次 vintage（`monthly_windows()`）、live は本番モデル。
    🔴 **どちらも「その行を作ったときのモデル」と同じものを選ぶ**。本番モデルを
       過去へ当てると in-sample になり、並びも当時と変わる。
    """
    from scripts.build_type_lab_picks import predict_p3_pw
    from src.wt_vintage_config import monthly_windows

    if mode == "paper":
        windows = [(w_from, w_to, ev) for w_from, w_to, ev, _ in monthly_windows()]
    else:
        windows = [(date_from, date_to, "lgbm_wt_eval")]

    for w_from, w_to, ev in windows:
        lo, hi = max(w_from, date_from), min(w_to, date_to)
        if lo > hi:
            continue
        # ⚠️ **窓ごとにまとめて1回**で予測する。同じ vintage 窓の中はモデルが同じなので
        #    1日ずつ回す必要が無く、特徴量の構築は期間の長さにほとんど比例しない
        #    （履歴の読み込みが支配的）。1日ずつだと 2025年ぶんで9時間かかった。
        p3, _ = predict_p3_pw(lo, ev, ev.replace("_eval", "_win"), day_to=hi)
        if not p3:
            print(f"[order/{ev}] {lo}〜{hi}: 特徴量が作れませんでした", flush=True)
            continue
        _apply_order(lo, hi, mode, p3, ev)


def _apply_order(date_from: str, date_to: str, mode: str,
                 p3_by_race: dict, source: str) -> bool:
    """復元した p3 から `p3_order` を書く。**軸が1行でも食い違ったら1行も書かない。**

    戻り値は書いたかどうか（食い違いで見送ったら False）。
    """
    with get_connection() as c:
        rows = c.execute(
            "SELECT id, race_key, axis1, axis2 FROM type_lab_picks "
            "WHERE p3_order IS NULL AND mode = ? AND race_date BETWEEN ? AND ?",
            (mode, date_from, date_to)).fetchall()
    if not rows:
        return True
    upd, miss, no_p3 = [], 0, 0
    for pid, rk, a1, a2 in rows:
        p3 = p3_by_race.get(rk)
        if not p3 or len(p3) < 3:
            no_p3 += 1
            continue
        order = sorted(p3, key=lambda car: (-float(p3[car]), car))
        # 🔴 復元が当時と同じであることの確認。ここを外すと静かにずれる。
        if a1 is not None and a2 is not None and (order[0], order[1]) != (int(a1), int(a2)):
            miss += 1
            continue
        upd.append(("-".join(str(x) for x in order), pid))

    # 🔴🔴 **食い違い率で「ソースが正しいか」を判定する。**
    #    照合できるのは先頭2つだけで、違うソースだと先頭2つが合った行でも
    #    3位以下が半分違う（2025-07-15 実測: 完全一致 24 / 先頭2つだけ一致 23）。
    #    3位以下こそ「順当」と「軸2+穴」を分ける情報なので、違うソースの行を
    #    部分的に書くと土台が静かに壊れる。
    checked = len(upd) + miss
    rate = miss / checked * 100 if checked else 0.0
    if rate > AXIS_MISMATCH_LIMIT_PCT:
        print(f"[order/{source}] {date_from}〜{date_to} mode={mode}: "
              f"🔴 軸不一致 {miss:,}/{checked:,} = {rate:.2f}% "
              f"(> {AXIS_MISMATCH_LIMIT_PCT}%) → **この範囲は1行も書きません**。"
              f"その期間の行はこのソースで作られていません。"
              f"範囲を狭めるか別のソースを使ってください", flush=True)
        return False

    with get_connection() as c:
        if upd:
            c.executemany("UPDATE type_lab_picks SET p3_order = ? WHERE id = ?", upd)
        c.commit()
    print(f"[order/{source}] {date_from}〜{date_to} mode={mode}: "
          f"埋めた {len(upd):,} / 軸不一致 {miss:,} ({rate:.2f}%) / p3なし {no_p3:,}",
          flush=True)
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", required=True)
    ap.add_argument("--to", dest="date_to", required=True)
    ap.add_argument("--mode", choices=("paper", "live"))
    ap.add_argument("--odds", action="store_true", help="win_tf_odds を埋める")
    ap.add_argument("--order-from-board", action="store_true")
    ap.add_argument("--order-from-models", action="store_true")
    a = ap.parse_args()
    if not (a.odds or a.order_from_board or a.order_from_models):
        raise SystemExit("--odds / --order-from-board / --order-from-models のどれかが要ります")
    if a.odds:
        fill_win_tf_odds(a.date_from, a.date_to, a.mode)
    if a.order_from_board:
        fill_order_from_board(a.date_from, a.date_to)
    if a.order_from_models:
        if not a.mode:
            raise SystemExit("--order-from-models には --mode が要ります")
        fill_order_from_models(a.date_from, a.date_to, a.mode)


if __name__ == "__main__":
    main()
