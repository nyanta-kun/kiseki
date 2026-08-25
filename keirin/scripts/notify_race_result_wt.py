"""推奨レースの結果を確定直後に取得し、レース単位で Discord 通知する（2026-08-04 新設）。

ユーザー要望:
  「レース確定後、レース単位で即時取得・discordへ結果通知できないか。
    ただしスクレイピングのため過負荷にならないようにする必要がある」
  → 「推奨レースのみ対応。その他は定期的に結果更新（現行 intraday のまま）」

## 過負荷にならない理由（むしろ現行より軽い）

現行 `intraday_results_wt.sh` は 15分ごとに `collect-wt --date <今日>` を実行し、
`_get_collected_keys`（finish_order>=1 をスキップ）以外の**未確定レースを全部**取得する。
つまり**まだ発走していないレースまで15分おきに叩いている**。

  現行  : 60回/日 × 平均40R × 2req ≒ 4,800 req/日
  本script: 推奨13R × 最大4回 × 2req ≒ 100 req/日（実際は初回で取れるものが多い）

本スクリプトは**発走時刻を過ぎた推奨レースだけ**を、経過時間が
CHECK_MINUTES のいずれかに一致した分だけ取得する（バックオフ）。
毎分 cron で起動しても、対象が無ければ1件も通信しない。

## 設計

- 対象 = 当日の picks_history に存在するレース（＝推奨を出したレース）で結果未確定のもの
- 発走からの経過分が CHECK_MINUTES に一致したときだけ fetch
  （競輪は発走から確定まで概ね5分前後。6分で大半が取れ、遅延分を10/15/25分で拾う）
- 取得できたら Discord（results チャンネル）へ 1レース1通知
- 二重通知は notified テーブルではなくログ用 JSON で抑止（DBスキーマを増やさない）

多重起動は flock（呼び出し側シェル）ではなく本体の PID ロックで防ぐ
（macOS に flock が無い前例があるため Python 側で完結させる）。

使い方:
    python scripts/notify_race_result_wt.py [--date YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.combo_label import axis_cars, format_bet_lines, format_pred_combo, is_hit
from src.sold_performance import (
    _as_dict, payout_per_100, settle_submission, winning_combo_labels,
)
from src.rank_visibility import disabled_rank_names
from src.database import get_connection
from src.notify.discord import send
from src.scraper.pipeline_wt import _save_batch
from src.scraper.winticket import WinticketScraper

JST = timezone(timedelta(hours=9))
# 発走からの経過分。競輪は発走〜確定が概ね5分前後なので6分で大半が取れる。
# 以降は遅延（写真判定・失格審議など）を拾うためのバックオフ。
CHECK_MINUTES = (6, 10, 15, 25)
# 確定配当（`wt_odds` の最終オッズ）が引けないまま何分待つか。これを過ぎたら
# 「⏳ 確定待ち」の行を含んだまま通知して打ち切る（永久に黙るより出す）。
SETTLE_WAIT_MINUTES = 60
STATE = Path(__file__).resolve().parent.parent / "data" / "notified_race_results.json"
LOCK = Path(__file__).resolve().parent.parent / "data" / "notify_race_result.lock"
MARK = {1: "◎", 2: "◯", 3: "△", 4: "×"}
# `netkeirin_submissions.status`。値の正本は `scripts/netkeirin_submit_wt.py`。
# ⚠️ あちらを import すると入稿一式（netkeirin クライアント・モデル読み込み）まで
#    引き込むので、毎分 cron で回る本スクリプトでは文字列を持つ。
#    食い違い検知は tests/test_notify_race_result.py が担う。
STATUS_SUBMITTED = "submitted"
# 公開済み（2026-08-16 に netkeirin 側の「公開」を通すようになって増えた状態）。
# 🔴 **これを「売った」に含め忘れると通知から金額が丸ごと消える。**
#    2026-08-16 以降の入稿は当日中に submitted → published へ進むため、
#    submitted だけを見ていた本スクリプトは
#      (1) 候補行の無い（看板穴埋め）レースが対象から落ちる
#      (2) 入稿原本の行が出ず「投資 → 払戻」が消える
#      (3) 当日累計が常に 0R で行ごと出ない
#    という3つの欠落を起こしていた（2026-08-20 の通知で実測・例外は出ない）。
#    「売った」の定義は Web と同じ submitted ∪ published
#    （`backend/src/api/keirin_router.py` の sold 判定）。
STATUS_PUBLISHED = "published"
# 却下（レビューUIで取り消した）入稿。**通知から外すために要る**。
STATUS_DELETED = "deleted"
#: 実際に売った商品の状態。当日累計・対象抽出はこれ。
SOLD_STATUSES = (STATUS_SUBMITTED, STATUS_PUBLISHED)
#: 通知に出す状態。取消も「出した推奨」として表示する（金額は「想定」）。
NOTIFY_STATUSES = SOLD_STATUSES + (STATUS_DELETED,)
# 三連複/三連単の組み合わせ区切り。**表で違う**（wt_odds は '1-2-3'）ので両方受ける。
_SEP_RE = re.compile(r"[-=]")


def _acquire_lock() -> bool:
    """PIDロック。前回プロセスが生きていれば False。"""
    try:
        if LOCK.exists():
            pid = int(LOCK.read_text().strip() or 0)
            if pid and pid != os.getpid():
                try:
                    os.kill(pid, 0)
                    return False          # 生きている
                except OSError:
                    pass                  # 死んでいる → 奪う
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        LOCK.write_text(str(os.getpid()))
        return True
    except Exception:
        return True                       # ロック機構の失敗で処理を止めない


def _load_state() -> set[str]:
    try:
        return set(json.loads(STATE.read_text()))
    except Exception:
        return set()


def _save_state(s: set[str]) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(sorted(s), ensure_ascii=False))
    except Exception as e:
        print(f"[warn] 状態保存に失敗: {e}", flush=True)


def _targets(date: str, now_ts: int) -> list[dict]:
    """当日の推奨レースのうち、発走からの経過分が CHECK_MINUTES に一致し
    かつ結果未確定のものを返す。"""
    with get_connection() as c:
        # 🔴 対象は **picks_history ∪ 入稿済み**。
        #    picks_history だけを見ていたため、**看板の穴埋めだけで売っている
        #    レースが1件も通知されなかった**（2026-08-15 松山 2R/3R/10R。
        #    当日の 9C 入稿11件は全て marquee_fill で、うち3件は候補行が無い）。
        #    「推奨を出したレース」の定義は**実際に売ったかどうか**であって、
        #    ペーパーの候補行があるかどうかではない。
        rows = c.execute(
            """
            SELECT DISTINCT r.race_key AS base,
                   r.venue_id, r.race_no, r.start_at, r.cup_id, r.day_index,
                   COALESCE(v.name, r.venue_id) AS venue_name
            FROM wt_races r
            LEFT JOIN venue_info v ON v.venue_code = r.venue_id
            WHERE r.race_key IN (
                SELECT split_part(race_key, '#', 1) FROM picks_history
                WHERE race_date = ?
                UNION
                SELECT race_key FROM netkeirin_submissions
                WHERE status IN (?, ?) AND left(race_key, 8) = ?
            )
            """,
            (date, *SOLD_STATUSES, date.replace("-", "")),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r) if not isinstance(r, dict) else r
            try:
                start = int(d["start_at"])
            except (TypeError, ValueError):
                continue
            elapsed = (now_ts - start) // 60
            if elapsed < min(CHECK_MINUTES):
                continue                   # まだ確定していない時間帯（通信しない）
            done = c.execute(
                "SELECT COUNT(*) AS n FROM wt_entries "
                "WHERE race_key = ? AND finish_order >= 1", (d["base"],)
            ).fetchone()
            n = (done["n"] if isinstance(done, dict) else done[0]) or 0
            # 🔴 **結果が既にあるレースを対象から外してはいけない。**
            #    旧実装は `n > 0` で切っていたため、
            #      (1) 15分毎の `intraday_results_wt.sh` に先を越されたレース
            #      (2) 確定が CHECK_MINUTES(6/10/15/25分) を過ぎたレース
            #    が**永久に通知されなかった**（2026-08-15 松山8R は +25分まで
            #    「まだ結果なし」で諦め、その後 intraday が取り込んで終わり）。
            #    結果が既にあるなら**取得せずそのまま通知する**（fetch=False）。
            #    二重通知は従来どおり notified JSON が抑止する。
            d["elapsed"] = elapsed
            d["fetch"] = n == 0
            d["date"] = date
            if d["fetch"] and elapsed not in CHECK_MINUTES:
                continue                   # 取得はバックオフの分だけ
            out.append(d)
    return out


def _fetch_one(scraper: WinticketScraper, t: dict, date: str) -> dict | None:
    data = scraper.fetch_race_data(
        t["venue_id"], date, int(t["race_no"]),
        cup_id=t["cup_id"], day_index=t["day_index"])
    if not data:
        return None
    if not any(e.get("finish_order") is not None for e in data.get("entries", [])):
        return None                        # まだ結果が載っていない
    try:
        data["odds"] = scraper.fetch_odds(
            t["venue_id"], date, int(t["race_no"]), t["cup_id"], t["day_index"]) or {}
    except Exception:
        data["odds"] = {}
    return data


def _confirmed_payouts(conn, base: str) -> dict[str, int]:
    """`{当たり目の表記: 100円あたりの確定配当}`（三連複 `1=2=4` / 三連単 `1-2-4`）。

    🔴 **入稿時点のオッズ（`bet_detail.odds`）を払戻に使ってはいけない。**
       発走までに動くので必ず過大・過小になる。実測 2026-08-15 松山9R は
       入稿時 9.0倍 → 確定 6.3倍で、そのまま使うと払戻が 33,300円（正しくは
       23,310円）と **43% 過大**になる。
    ⚠️ `wt_race_payouts` は当日には入らない（松山の当日分は0件だった）ので、
       レース後に最終値へ上書きされる `wt_odds` を使う。
    ⚠️ 組み合わせ表記は **`wt_odds` が '1-2-3'**（券種を問わず `-` 区切り・
       三連複は昇順）。買い目・当たり目の表記とは別なので変換する。
    ⚠️ 端数は**10円未満切り捨て**（`payout_per_100`）。実払戻との一致は
       2026-07-12 に検証済みで、切り捨てないと Web の表示と数円ずれる。
    """
    out: dict[str, int] = {}
    rows = conn.execute(
        "SELECT bet_type, combination, odds_value FROM wt_odds "
        "WHERE race_key = ? AND bet_type IN ('trio', 'trifecta')", (base,),
    ).fetchall()
    for r in rows:
        bt = r["bet_type"] if isinstance(r, dict) else r[0]
        comb = r["combination"] if isinstance(r, dict) else r[1]
        odds = r["odds_value"] if isinstance(r, dict) else r[2]
        pay = payout_per_100(odds)
        if not pay:
            continue
        try:
            cars = [int(x) for x in _SEP_RE.split(str(comb)) if x != ""]
        except ValueError:
            continue
        if len(cars) != 3:
            continue
        label = ("=".join(map(str, sorted(cars))) if bt == "trio"
                 else "-".join(map(str, cars)))
        out[label] = pay
    return out


def _sold_lines(base: str, finishers: list[tuple[int, int]],
                payouts: dict[str, int]) -> tuple[list[tuple[str, str]], bool]:
    """入稿した推奨の採点行。**取消したものも含む**。

    returns ([(表示行, ランク)], 採点待ちが残っているか)

    🔴 **取消を落とさない**（2026-08-18 ユーザー方針）。「全推奨（取消含む）を
       通知し、取消ならそれと分かるようにする」。取消行は `7S（取消）` と出し、
       金額は「投資」ではなく「想定」と書く（実際には買っていないため）。
       ⚠️ 当日合計 `_day_total` は**実売のみ**（`SOLD_STATUSES`）のままにする。
          ここに取消を混ぜると売上が水増しされる。

    🔴 **picks_history ではなく `netkeirin_submissions.bet_detail` が正本。**
       候補行が無いレース（看板の穴埋め）でも売っている以上は結果を出す。
       金額も傾斜配分（ダッチング）を反映する——均等割りで計算すると
       的中点の賭け金がずれる（松山9R は的中点に 3,700円 / 均等なら 1,400円）。
    """
    out: list[tuple[str, str]] = []
    pending = False
    with get_connection() as c:
        subs = c.execute(
            "SELECT rank_key, bet_detail, status FROM netkeirin_submissions "
            "WHERE race_key = ? AND status IN (?, ?, ?) ORDER BY rank_key",
            (base, *NOTIFY_STATUSES),
        ).fetchall()
        if not subs:
            return out, pending
        combos = {
            str(r["rank"] if isinstance(r, dict) else r[0]).replace("RANK_", ""):
            (r["pred_combo"] if isinstance(r, dict) else r[1])
            for r in c.execute(
                "SELECT rank, pred_combo FROM picks_history "
                "WHERE split_part(race_key, '#', 1) = ?", (base,)).fetchall()
        }
    for s in subs:
        rank = s["rank_key"] if isinstance(s, dict) else s[0]
        detail = s["bet_detail"] if isinstance(s, dict) else s[1]
        status = s["status"] if isinstance(s, dict) else s[2]
        got = settle_submission(detail, finishers, payouts)
        if got is None:
            continue                       # 買い目が残っていない（2026-08-07 以前）
        bet, pay, hit = got.bet, got.payout, got.hit
        # 🔴 **「まだ分からない」を外れにしない。** 当たっているのに確定配当が
        #    引けていない状態がある（着順は入ったがオッズの最終値が未着）。
        #    ここで金額を作ると Web の「未確定」と食い違うので、待っていると出す。
        #    呼び出し側は `pending` を見て**通知を確定させず次回に回す**。
        if not got.settled:
            pending = True
            mark = "⏳ 確定待ち"
            money = f"投資 ¥{bet:,}"
            buy = format_bet_lines((_as_dict(detail) or {}).get("lines")) or ""
            buy = f"{buy}  → " if buy else ""
            head = (f"{rank}（取消）" if status == STATUS_DELETED else rank)
            out.append((f"{head}: {buy}{mark}  {money}", rank))
            continue
        mark = "🎯 **的中**" if hit else "❌ 不的中"
        if hit and pay < bet:
            mark = "😖 **ガミ**"            # 当たったが元返し割れ
        # 🔴 **取消も必ず出す**（2026-08-18 ユーザー方針）。
        #    「全推奨（取消含む）を通知し、取消ならそれと分かるようにする」。
        #    黙って落とすと、出した推奨の結果が追えなくなる。
        cancelled = (status == STATUS_DELETED)
        head = f"{rank}（取消）" if cancelled else rank
        money = (f"想定 ¥{bet:,} → ¥{pay:,}" if cancelled
                 else f"投資 ¥{bet:,} → 払戻 ¥{pay:,}")
        # 🔴 買い目は**実際に入稿した bet_detail** が正本。picks_history は候補で、
        #    看板の穴埋めで売ったレースには行が無く買い目が空欄になっていた。
        buy = format_bet_lines((_as_dict(detail) or {}).get("lines")) \
            or format_pred_combo(combos.get(rank), labels=False)
        buy = f"{buy}  → " if buy else ""
        out.append((f"{head}: {buy}{mark}  {money}", rank))
    return out, pending


def _race_payout_line(payouts: dict[str, int], won: list[str]) -> str | None:
    """そのレースの三連複・三連単の確定配当（100円あたり）の表示行。

    ユーザー要望「三連複、三連単の払い戻しを載せて下さい」（2026-08-21）。
    ⚠️ **買い目の的中とは無関係**。買っていなくても出す（相場の目安として要る）。
    ⚠️ 引けないときは黙って落とす。`—` を出すより行ごと無いほうが読みやすい。
    ⚠️ 同着では当たり目が複数あるが、この行は**相場の目安**なので券種ごとに
       1つずつ出す（採点はそれとは独立に当たり目ごとの配当を積む）。
    """
    parts = []
    for kind, sep in (("3連複", "="), ("3連単", "-")):
        pay = next((payouts[c] for c in won if sep in c and c in payouts), None)
        if pay:
            parts.append(f"{kind} ¥{pay:,}")
    return "確定配当: " + " / ".join(parts) if parts else None


def _day_total(date: str) -> tuple[int, int, int]:
    """当日の**実際に売った商品**の (投資, 払戻, 確定レース数)。

    ユーザー要望「推奨した結果として払い戻し総額も出して」（2026-08-15）。
    ⚠️ 母集団は入稿済み（取消は除く）。picks_history のペーパー成績とは別物で、
       Web の「売った商品の成績」と同じ定義に揃えている。
    """
    ymd = date.replace("-", "")
    with get_connection() as c:
        subs = c.execute(
            "SELECT race_key, rank_key, bet_detail FROM netkeirin_submissions "
            "WHERE status IN (?, ?) AND left(race_key, 8) = ?", (*SOLD_STATUSES, ymd),
        ).fetchall()
        bet = pay = n = 0
        for s in subs:
            rk = s["race_key"] if isinstance(s, dict) else s[0]
            detail = s["bet_detail"] if isinstance(s, dict) else s[2]
            ents = c.execute(
                "SELECT frame_no, finish_order FROM wt_entries WHERE race_key = ? "
                "AND finish_order BETWEEN 1 AND 3", (rk,),
            ).fetchall()
            # ⚠️ **車番だけに畳まない**。同着があると着順が潰れて当たり目を誤る。
            finishers = sorted(
                (int(e["finish_order"] if isinstance(e, dict) else e[1]),
                 int(e["frame_no"] if isinstance(e, dict) else e[0])) for e in ents)
            got = settle_submission(detail, finishers, _confirmed_payouts(c, rk))
            # 🔴 未採点（未確定・配当待ち）は数えない。0円として足すと
            #    当たっているレースが回収率を押し下げる。
            if got is None or not got.settled:
                continue
            bet += got.bet
            pay += got.payout
            n += 1
    return bet, pay, n


def _build_message(t: dict, base: str) -> tuple[str, bool]:
    """着順と、そのレースに出していた推奨の的中可否をまとめる。

    returns (本文, 採点待ちが残っているか)。採点待ちがあるうちは呼び出し側が
    **通知を確定させない**（次のチェックでもう一度組み直す）。
    """
    with get_connection() as c:
        ents = c.execute(
            "SELECT frame_no, prediction_mark, finish_order "
            "FROM wt_entries WHERE race_key = ? ORDER BY finish_order", (base,)
        ).fetchall()
        picks = c.execute(
            "SELECT race_key, rank, pred_combo FROM picks_history "
            "WHERE split_part(race_key, '#', 1) = ?", (base,)
        ).fetchall()
        payouts = _confirmed_payouts(c, base)

    def _g(r, k):
        return r[k] if isinstance(r, dict) else r[list(r.keys()).index(k)]

    # 🔴 **選手名は出さない**（2026-08-21 ユーザー方針）。買い目は車番で書くので、
    #    名前があると1行が折り返して車番と印が読み取りにくくなる。
    top3 = [(int(_g(e, "finish_order")), int(_g(e, "frame_no")), _g(e, "prediction_mark"))
            for e in ents
            if _g(e, "finish_order") and 1 <= int(_g(e, "finish_order")) <= 3]
    top3.sort()
    # ⚠️ **`(着順, 車番)` のまま持つ**。車番だけに畳むと同着が潰れ、当たり目を誤る。
    finishers = [(fo, f) for fo, f, _ in top3]
    order3 = tuple(f for _, f, _ in top3)   # picks_history 側の判定に渡す（同着は先頭3件）
    order = " − ".join(f"**{f}**{MARK.get(m, '')}" for _, f, m in top3)
    top3_set = {f for _, f, _ in top3}
    won = winning_combo_labels(finishers)

    lines = [f"🏁 **{t['venue_name']}{t['race_no']}R 確定**",
             f"着順: {order}"]
    pay_line = _race_payout_line(payouts, won)
    if pay_line:
        lines.append(pay_line)
    # 入稿した推奨（取消を含む）を先に出す。picks_history の候補行は
    # 「一度も入稿していないランク」の分だけ後段で補う。
    sold, pending = _sold_lines(base, finishers, payouts)
    lines.extend(text for text, _ in sold)
    sold_ranks = {rank for _, rank in sold}
    # 🔴 入稿 OFF のランクは通知しない（2026-08-14）。`enabled` は入稿だけを
    #    止めており、判定・記録・通知は動き続けていたため、廃止したはずの
    #    9H1 の不的中通知が毎レース届いていた（ユーザー指摘）。
    #    kiseki Web は同じフラグで非表示にしているので Discord だけが
    #    食い違っていた。判定は `src/rank_visibility`（fail-open）が正本。
    _off = disabled_rank_names()
    for p in picks:
        if _g(p, "rank") in _off:
            continue
        rank = _g(p, "rank").replace("RANK_", "")
        if rank in sold_ranks:
            continue          # 入稿原本を出した分は重複させない
        combo = _g(p, "pred_combo") or ""
        # 🔴 解釈は `src/combo_label` が単一正本（2026-08-14）。
        #    ここに自前パースを書いてはいけない。以前は「畳んだ形」だけを想定した
        #    自前実装で、7H1/7H2 の**展開形（1点ずつ）を渡すと軸と相手を取り違え**、
        #    三連複が当たっていても `❌ 不的中（軸3/2）`（2車のはずが3車）と
        #    通知していた。表示だけの問題ではなかった。
        hit = is_hit(combo, order3)
        axes = axis_cars(combo)
        n_in = len(set(axes) & top3_set)
        if hit:
            mark = "🎯 **的中**"
        elif len(axes) == 2 and n_in == 2:
            # 軸2車が3着内なのに外れ＝相手（三連単なら着順も）を外した形。
            mark = "😖 軸的中・相手外し"
        elif len(axes) == 2:
            mark = f"❌ 不的中（軸{n_in}/2）"
        else:
            # BOX 等で共通の軸が無い買い方（7H1 の三連複）。「軸n/2」は出せない。
            mark = "❌ 不的中"
        # 🔴 券種は区切り文字が表す（三連複 `=` / 三連単 `-`）ので `三単:` は出さない。
        lines.append(f"{rank}: {format_pred_combo(combo, labels=False)}  → {mark}")

    bet, pay, n = _day_total(t["date"])
    if n:
        roi = f"{pay / bet * 100:.1f}%" if bet else "—"
        lines.append(f"── 本日累計（{n}R 確定）: 投資 ¥{bet:,} → **払戻 ¥{pay:,}**（回収 {roi}）")
    return "\n".join(lines), pending


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now(JST).strftime("%Y-%m-%d"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not _acquire_lock():
        return
    now_ts = int(time.time())
    targets = _targets(args.date, now_ts)
    if not targets:
        return                              # 対象なし＝1件も通信しない

    print(f"[notify_race_result] 対象 {len(targets)}件", flush=True)
    done = _load_state()
    scraper = WinticketScraper()
    for t in targets:
        base = t["base"]
        if base in done:
            continue                        # 二重通知の抑止（通信より先に判定する）
        if t["fetch"]:
            try:
                data = _fetch_one(scraper, t, args.date)
            except Exception as e:
                print(f"[warn] {base} 取得失敗: {e}", flush=True)
                continue
            if not data:
                print(f"[info] {base} まだ結果なし（発走+{t['elapsed']}分）", flush=True)
                continue
            if args.dry_run:
                print(f"[dry-run] {base} 結果取得（保存・通知はしない）", flush=True)
                continue
            _save_batch([data])
        elif args.dry_run:
            print(f"[dry-run] {base} 既に結果あり（通知はしない）", flush=True)
            continue
        try:
            body, pending = _build_message(t, base)
            # 🔴 **配当が引けるまで通知を確定させない**（2026-08-25）。当たっているのに
            #    `wt_odds` の最終値が未着だと金額が出せない。以前はその場を
            #    入稿時点のオッズで埋めていたが、Web は同じ状態を「未確定」と出すため
            #    **同じレースで別の数字**になっていた。通知は1レース1回きりなので、
            #    採点待ちのうちは `done` に入れず次のチェックで組み直す。
            #    ⚠️ 待ちきれない場合の逃げ道は `SETTLE_WAIT_MINUTES`。
            if pending and t["elapsed"] < SETTLE_WAIT_MINUTES:
                print(f"[info] {base} 採点待ち（発走+{t['elapsed']}分）", flush=True)
                continue
            send(body, channel="results")
            done.add(base)
            _save_state(done)
            print(f"[ok] {base} 通知", flush=True)
        except Exception as e:
            print(f"[warn] {base} 通知失敗: {e}", flush=True)


if __name__ == "__main__":
    main()
