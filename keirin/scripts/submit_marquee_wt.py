#!/usr/bin/env python3
"""看板レースの取りこぼしを埋める（2026-08-09 新設）。

通常の波（`daily_picks_wt.sh` 07:00 / `wave_submit_wt.sh` noon・evening）が
出し終えた**後**に走り、**看板レースとその前後で商品が無いもの**を
`--marquee` 経路で入稿する。

🔴 **各波のシェルスクリプトの末尾から呼ばれる**（2026-08-12〜）。cron に
   独立したエントリを置かないこと。以前は「波の20分後」（07:20 / 13:20 / 18:20）
   に別建てで動かしていたが、それは**「波が20分以内に終わる」という暗黙の仮定**
   に依存していた。朝のバッチはライン情報や race_point のリトライ（5分待機×2回）
   が入ると容易に超えるため、**穴埋めがランク入稿より先に走りうる**
   ＝ランクが取るはずのレースを横取りする。同じ波の中で順に呼べば
   その競合は構造的に消え、入稿と Discord 通知のタイミングも揃う。

## なぜ必要か

ランクのゲートは的中率・ROI で切っているので、**売れるかどうかは見ていない**。
その結果 2026-08-08 / 08-09 と連続で、当日最大の看板（GIII S級決勝・
GI ガールズ決勝）に商品がゼロだった。08-09 は手作業で11件を埋めた。
本スクリプトはそれを自動化する。詳細は `src/marquee.py` の docstring。

## 判定と軸

- 対象 = 看板レース（決勝/特選/選抜/特秀）とその前後1R（`src/marquee.py`）
- 既にどれかのランクで入稿済み → skip（1レース1商品）
- 発走済み → skip
- 車数が 7/9 以外 → skip（ランクが7車/9車しか無いため構造的に入稿できない）
- 軸 = 当日の指数（allindex JSON）の pred 上位2車を、**ラインで組み替える**
  （9車 2026-08-16 / 7車 2026-08-19・`_axes()` の docstring に根拠）

🔴 **開催は自分の波でしか埋めない**（`src/meeting_wave.py`・2026-08-19 是正）。
   ランクの入稿（`netkeirin_submit_wt.py`）と**同じ判定**を使う。ここが食い違うと
   穴埋めがランクより早い波で走り、**1レース1商品の取り合いに先に勝ってしまう**。

   2026-08-19 以前は「第1R 18時以降か」だけを見ており、**ナイター（第1R 12〜17時台）
   を朝7時の波で埋めていた**。ランクがその開催を出すのは昼13:00 なので5時間の
   先回りになる。実測（2026-08-09〜08-19・穴埋め196件）:

   | 入稿波 × 開催の波 | 件数 | ランクが候補を持っていた | うち賭け金0（＝取れなかった） |
   |---|---|---|---|
   | morning × morning | 67 | 24 | **0** |
   | morning × **noon** | 78 | 53 | **25** |
   | noon × noon | 6 | 2 | **0** |
   | evening × night | 31 | 10 | **0** |

   **ランクが候補を持ちながら商品を取れなかった25件は、全て波がずれた
   バケツにだけ現れる。** 板が育つ前に出すので傾斜配分も効かない
   （ナイターの三連複 未確定率は 朝8時台 30.8% → 12:00 5.3%）。

使い方:
    python scripts/submit_marquee_wt.py [YYYY-MM-DD] [--session morning|noon|evening] [--dry-run]

⚠️ `--session` は**呼び出し元のシェルがランク入稿へ渡したものと同じ値**を渡すこと。
   省略時のみ実行時刻から導く（手動実行用のフォールバック）。
"""
from __future__ import annotations

import argparse
import json
import subprocess

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402
from src.marquee import marquee_race_nos
from src.meeting_wave import WAVE_LABEL_JP, wave_of_first_hour, waves_due_by  # noqa: E402
from src.odds_prediction import predicted_trio_board  # noqa: E402
from src.submit_window import is_closed  # noqa: E402
# 🔴 確認画面の URL は入稿側の定義を借りる（二重管理にしない）
from scripts.netkeirin_submit_wt import (  # noqa: E402
    MEAN_PAYOUT_SKIP_TAG,
    REVIEW_URL,
)

JST = timezone(timedelta(hours=9))
PICKS = Path(__file__).resolve().parent.parent / "data" / "picks"
# 🔴 **ここの値は必ず `netkeirin_submit_wt.MANUAL_ALLOWED_RANKS` に載っていること。**
#    載っていないランク名を書くと `--manual-rank-key` の choices で argparse が
#    即死し、**穴埋めが1件も入稿されないまま「失敗」だけがログに残る**。
#    - 9車: 2026-08-14 に 9A を廃止して 9C へ集約（穴埋めは 9A 入稿22件中12件の主経路）
#    - 7車: 2026-08-14 の PR#145 で 7A を RANK_7S へ統合したのに**ここだけ 7A のまま
#      残り**、2026-08-15 の7車穴埋め9件が全滅した（9車は 9C で成功していたため
#      「穴埋めは動いている」ように見えていた）。2026-08-16 に 7S へ付け替え
# 手動入稿で使うランク。ゲート表示の付かない中立のものを選ぶ
# （看板レースは「必ず出す」ので「自信あり」を意味するランクは使わない）。
# 7S / 9C はいずれも `gate_filter=None`＝ゲート表示が付かない。
RANK_BY_CARS = {7: "7S", 9: "9C"}


def _load_allindex(date: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for suffix in ("", "_night"):
        p = PICKS / f"wave_picks_wt_{date}{suffix}_allindex.json"
        if not p.exists():
            continue
        for x in json.loads(p.read_text(encoding="utf-8")):
            out[str(x["race_key"])] = x
    return out


def _lines(race_key: str) -> dict[int, dict]:
    """`wt_entries` から車番→ライン情報を引く。

    ⚠️ allindex JSON はライン構成を持っていない（`line_position` は脚質の
       「逃/両/追」であってラインではない）。DB を見るしかない。
       取れなければ空 dict を返し、呼び出し側は従来どおり指数上位2車へ落ちる。
    """
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT frame_no, line_group, line_size, is_line_leader "
                "FROM wt_entries WHERE race_key = ?",
                (race_key,),
            )
            return {int(r["frame_no"]): dict(r) for r in cur.fetchall()}
    except Exception as e:  # noqa: BLE001
        print(f"[marquee] {race_key}: ライン情報の取得に失敗（従来の軸で続行）: {e}", flush=True)
        return {}


def _is_leader(ln: dict[int, dict], n: int) -> bool:
    """単騎を先頭に数えない（番手がいないので「先頭＋番手」が作れない）。"""
    e = ln.get(n)
    return bool(e and e["is_line_leader"] and (e["line_size"] or 1) > 1)


def _same_line(ln: dict[int, dict], order: list[int], head: int) -> int | None:
    """`head` と同じラインの車を、指数順（`order`）で最上位から1車返す。"""
    g = ln[head]["line_group"]
    for n in order:
        if n != head and ln.get(n) and ln[n]["line_group"] == g:
            return n
    return None


def _axes(entry: dict, lines: dict[int, dict] | None = None) -> tuple[int, int] | None:
    """穴埋めの軸2車を決める。

    既定は指数上位2車。そこからラインで組み替える
    （9車 2026-08-16 導入 / 7車 2026-08-19 追加）:

      ルール1  指数1位がライン先頭   → 軸2 = その同ライン最上位（番手）
      ルール2  指数1位が非先頭 かつ 指数2位がライン先頭
               → 軸2 を軸へ格上げし、軸2 = その同ライン最上位

    ## なぜ

    穴埋めはゲートを通っていない帯なので、素の上位2車だと二軸 30.4% / ROI 69.4%
    しか出ない。実測（9車・GIII以上・2025-01〜2026-08 の穴埋め帯 n=1,742）で
    **二軸 33.8% / 的中 30.9% / ROI 78.5%** へ改善する。paired bootstrap で
    二軸 +3.4pt [+1.6,+5.3]・的中 +2.9pt [+1.1,+4.6]・ROI +9.2pt [+1.2,+17.6]
    と3指標とも有意。2025 / 2026 の両年で同じ向きに再現する。

    ルール2 が効くのは**別ライン**のとき（n=293・現行 二軸22.9%/ROI51.1% →
    29.4%/99.5%）。番手と別線先頭を2車とも要求する、構造的に最も噛み合わない
    組み方だったのを、同一ラインの先頭＋番手へ替えている。
    同ラインのときは組み替えても同じペアになる（実測 変更0件/307件）ので実害なし。
    三連複は順序を問わないため、**ペアが変わらない限り買い目は一切動かない**。

    ## 7車（2026-08-19 追加）

    9車と同じ規則を7車でも測った（`scripts/exp_marquee_fill_7car_axis.py` /
    `..._legs.py`・穴埋め帯7車 17,035R・2025-01-01〜2026-08-18・確定オッズ採点・
    `pred_top3_pct` は月次凍結 vintage）。組み替わるのは 5,138R（30.2%）:

      | | 二軸 | 的中 | 表示的中 | ROI | 配当中央 | 10倍+ |
      |---|---|---|---|---|---|---|
      | 現行（素の上位2車） | 48.3% | 48.3% | 28.5% | 73.1% | 1.16 | 9.9% |
      | ライン組み替え     | 48.5% | 48.5% | 28.6% | **75.5%** | 1.18 | 10.6% |

    ROI +2.5pt [+0.9, +4.2]（レース単位 paired bootstrap・有意）。
    **的中も表示的中も落ちない**（+0.2pt / +0.1pt）ので、看板帯の目的関数
    （売上加重の的中率）を損なわずに配当だけ上がる。2025 / 2026 の両年で
    同じ向きに再現する（72.4→75.4 / 74.1→75.7）。組み替わった 5,138R だけで
    見ると ROI 68.4→76.7%（+8.2pt [+2.7, +13.8]）で、二軸・表示的中・10倍+ も
    すべて改善する。

    ⚠️ **穴埋めが実際に出る側（どのランクも取っていない 9,263R）に絞ると
       ROI +1.9pt [−0.1, +3.8] で有意ではない**（`--only-unranked`）。
       的中 50.3→49.9%・表示的中 28.3→28.1% と、こちらでは僅かに下がる。
       採用したのは「点推定が両母集団・両年で同じ向き（+1.9〜+2.5pt）」かつ
       **失うものがほぼ無い**からで、有意性を根拠にしていない。
       上の 17,035R は帯全体（ランクが取った分を含む）の数字である。

    ⚠️ **軸を「◎◯と重ならないように」差し替える案は不採用**（同スクリプトで検証）。
       ROI は +2〜3pt 上がるが **的中が 48.3→35.8%・表示的中が 28.5→25.1%** 落ちる。
       穴埋めが実際に出る母集団（どのランクも取っていない 9,263R）に絞ると
       ROI 差は +1.9pt [−1.9, +5.7] で有意ですらない。
    ⚠️ **「別ラインの先頭同士が軸＝やり合って共倒れ」も不成立**
       （`scripts/exp_marquee_fill_7car_duel.py`）。軸2車がそろって3着を外す率は
       その構造で **6.4%** ・それ以外で **7.9%** と**むしろ低い**。番手2車へ
       振り替えると的中 38.6→9.0%・ROI −12.9pt（逃×逃に絞ると −26.2pt・有意に悪化）。
    ⚠️ **相手の点数を削っても表示的中は動かない**（`..._legs.py`）。
       5点→4点→3点で ガミは 19.9→16.1→11.3% と減るが的中も同じだけ減り、
       表示的中は 28.5 / 28.8 / 28.3% で横ばい・ROI も誤差内。
       7M1 型（相手 下位3点）は表示的中 −12.4pt で看板には使えない。
    🔴 軸から降りた指数1位は**相手に残る**（指数最上位なので相手の足切りに掛からない。
       実測 293件すべて相手側に入った）。買い目から消えるわけではない。
    """
    riders = sorted(entry.get("riders") or [], key=lambda r: r.get("ai_rank", 99))
    if len(riders) < 2:
        return None
    order = [int(r["frame_no"]) for r in riders]
    a1, a2 = order[0], order[1]

    ln = lines or {}
    # 🔴 車数は `RANK_BY_CARS`（＝穴埋めが入稿できる車数）と揃える。ここだけ
    #    別の数字を書くと「入稿はされるが組み替えだけ効かない」車数が生まれる。
    if len(riders) not in RANK_BY_CARS or not all(n in ln for n in order):
        return a1, a2

    if _is_leader(ln, a1):
        partner = _same_line(ln, order, a1)
        if partner is not None:
            return a1, partner
    elif _is_leader(ln, a2):
        partner = _same_line(ln, order, a2)
        if partner is not None:
            return a2, partner
    return a1, a2


def session_of_hour(hour: int) -> str:
    """実行時刻（時）から波ラベルを返す。

    DB の `netkeirin_submissions.session` と「どの開催を埋めてよいか」の両方に使う。
    固定にすると後から「どの波で埋めたか」が追えない。
    """
    return "morning" if hour < 12 else ("noon" if hour < 18 else "evening")


def due_waves_for(session: str) -> set[str]:
    """その回で埋めてよい開催の波（自分の波 + 取りこぼした過去の波）。

    🔴 **ランク側（`netkeirin_submit_wt.SESSION_WAVE`）を import して使う。**
       ここへ対応表を書き写すと、片方だけ動かしたときに無言でずれ、
       穴埋めがランクより早い波で走って商品を横取りする（本モジュール docstring）。
    """
    from scripts.netkeirin_submit_wt import SESSION_WAVE  # noqa: PLC0415
    return set(waves_due_by(SESSION_WAVE[session]))


def _can_pull_forward(race_key: str) -> bool:
    """後の波の開催を、この回へ前倒しして埋めてよいか（2026-08-21 新設）。

    穴埋めの買い目は 7S / 9C（`RANK_BY_CARS`）＝**どちらも三連複**なので、
    条件は「予測オッズの盤面を作れるか」の1つだけ。作れれば賭け金の配分は
    板を使わないので、朝に出しても夜の波で出しても中身が変わらない。

    ⚠️ 例外は握り潰して False（＝従来どおり自分の波へ残す）。前倒しは上積みで
       あって、判定できないことを理由に商品を落としてはいけない。
    """
    try:
        return bool(predicted_trio_board(race_key))
    except Exception:
        return False


def venue_waves(races: list[dict]) -> dict[str, str]:
    """会場（venue_id）→ 入稿の波。

    ⚠️ **会場で括る**——`src/meeting_wave.py` の「開催」は会場×日であって
       cup_id ではない。ランク側 `netkeirin_submit_wt._load_meeting_waves()` と
       括り方を揃えないと、同じ開催の判定が2箇所で食い違う。
    ⚠️ 発走時刻が1つも取れない会場は `wave_of_first_hour(None)`＝朝扱い（安全側）。
       分からないことを理由に商品を落とさない。
    """
    first: dict[str, float] = {}
    for r in races:
        if not r.get("start_at"):
            continue
        v = str(r["venue_id"])
        try:
            hour = (int(r["start_at"]) + 9 * 3600) % 86400 / 3600
        except (TypeError, ValueError):
            continue
        first[v] = min(first.get(v, 1e9), hour)
    return {str(r["venue_id"]): wave_of_first_hour(first.get(str(r["venue_id"])))
            for r in races}


def _rank_of(label: str) -> str:
    """`立川3R(7C)` から `7C` を取り出す。取れなければ空文字。"""
    a, b = label.rfind("("), label.rfind(")")
    return label[a + 1:b] if 0 <= a < b else ""


def _send_merged_notice(path: str, date: str, done: list[str],
                        failed: list[str],
                        skipped: list[str] | None = None) -> bool:
    """ランク入稿が保留した通知に**穴埋めぶんを足して1通**送る（2026-08-23）。

    🔴 **これが無いと Discord の件数が確認画面と食い違う。**
       穴埋めはランク入稿の**後**に走るので、ランク側が自分で送ると
       穴埋めぶんが数に入らない。2026-08-23 朝の実害:
       Discord「計25件」に対し確認画面は「45件」で、
       **看板穴埋め20件（7S18・9C2）がどこにも出ていなかった**。

    🔴 **通知は1通のまま**（2026-08-14 のユーザー判断を変えない）。
       増やすのではなく、既にある1通の件数を正しくする。

    ⚠️ ファイルが無い場合は False を返し、呼び出し側が従来経路へ落ちる。
       ランク入稿が落ちた日や手動実行でも穴埋めは動く必要がある。
    """
    import json as _json
    from pathlib import Path as _Path

    from src.notify.discord import send

    f = _Path(path)
    if not path or not f.exists():
        return False
    try:
        d = _json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[marquee] 保留通知の読み込みに失敗（従来経路で送る）: {e}", flush=True)
        return False

    fill: dict[str, int] = {}
    for label in done:
        r = _rank_of(label)
        if r:
            fill[r] = fill.get(r, 0) + 1
    n_fill = len(done)
    total = int(d.get("total", 0)) + n_fill
    parts = [f"{k}{v}件" for k, v in (d.get("breakdown") or {}).items()]
    breakdown = "・".join(parts) if parts else "なし"
    if fill:
        breakdown += "／看板穴埋め " + "・".join(f"{k}{v}件" for k, v in fill.items())

    if d.get("propose_only"):
        msg = (f"📝 **[netkeirin入稿案] {d.get('target_date', date)}"
               f"（{d.get('session_jp', '')}）: {breakdown}（計{total}件）**\n"
               f"確認・承認: {REVIEW_URL}\n"
               f"⚠️ 承認するまで netkeirin へは出ません。")
    else:
        from src.netkeirin_client import RACE_AUTH_URL
        msg = (f"📮 **[netkeirin入稿完了] {d.get('target_date', date)}"
               f"（{d.get('session_jp', '')}）: {breakdown}（計{total}件）**\n"
               f"確認: {RACE_AUTH_URL}\n内容を確認の上、公開してください。")
    # 🔴 ランク入稿ぶんの見送り（`_write_deferred_notice` が持ち越す）と
    #    看板穴埋めぶんの見送りを**合算して1行**にする（§11.6.3）。
    skips = (list(d.get("mean_payout_skips") or [])
             + [f"{x}(看板穴埋め)" for x in (skipped or [])])
    if skips:
        msg += f"\n💸 安い配当で {len(skips)}件 見送り: " + " / ".join(skips)
    fails = list(d.get("failures") or []) + [f"{x}(看板穴埋め)" for x in failed]
    if fails:
        msg += f"\n⚠️ 入稿失敗 {len(fails)}件: " + " / ".join(fails)
    try:
        send(msg, channel="netkeirin")
    except Exception as e:  # noqa: BLE001 — 通知失敗で入稿結果を失わない
        print(f"[marquee] Discord通知失敗: {e}", flush=True)
    finally:
        # 🔴 同じ内容を翌日また送らないよう必ず消す（送信の成否によらない）。
        try:
            f.unlink()
        except OSError:
            pass
    return True


def _notify_summary(date: str, done: list[str], failed: list[str],
                    skipped: list[str] | None = None) -> None:
    """看板レースの入稿結果を**まとめて1通**だけ Discord へ出す。

    🔴 これは人手の入稿ではなく**自動入稿**なので「手動入稿」と書かない。
       2026-08-11 に「netkeirin手動入稿 … 1件」が16通届き、
       自動で埋めた分を人が出したものと誤読しかねない状態だった。

    🔴 **承認制のときは1通も送らない**（2026-08-14・ユーザー判断）。
       承認制では子プロセスが `propose_only=True` で走るので、作られるのは
       netkeirin へ出ていない**入稿案（status='proposed'）**であって入稿ではない。
       それを「自動入稿: 成功12件」と通知していたため、直前に出る
       「[netkeirin入稿案]」と矛盾し、**承認制が効いていないように見えていた**。
       入稿案の存在は承認催促の通知と `/keirin/review` が伝えるので、
       ここから重ねて出す必要はない。ログには必ず残す（黙って消さない）。
    """
    from src.netkeirin_client import RACE_AUTH_URL
    from src.notify.discord import send

    from scripts.netkeirin_submit_wt import _approval_required

    if _approval_required():
        print(f"[marquee] 承認制のため Discord 通知は出さない"
              f"（入稿案 {len(done)}件・失敗 {len(failed)}件）", flush=True)
        return

    head = f"🏁 **[netkeirin自動入稿] {date} 看板レース: 成功{len(done)}件"
    head += f"・失敗{len(failed)}件**" if failed else "**"
    body = ""
    if done:
        body += "\n" + " / ".join(done)
    # 平均払戻ゲートの見送りは失敗ではないので別行にする（§11.6.3）
    if skipped:
        body += f"\n💸 安い配当で {len(skipped)}件 見送り: " + " / ".join(skipped)
    if failed:
        body += "\n⚠️ 失敗: " + " / ".join(failed)
    body += f"\n確認: {RACE_AUTH_URL}\n内容を確認の上、公開してください。"
    try:
        send(head + body, channel="netkeirin")
    except Exception as e:  # noqa: BLE001 — 通知失敗で入稿結果を失わない
        print(f"[marquee] Discord通知失敗: {e}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default=datetime.now(JST).strftime("%Y-%m-%d"))
    ap.add_argument("--dry-run", action="store_true")
    # 🔴 ランク入稿が `--defer-notify` で書き出した集計。ここに穴埋めを足して
    #    **1通だけ**送る（`_send_merged_notice` の docstring 参照）。
    ap.add_argument("--defer-notify", metavar="PATH", default="",
                    help="ランク入稿が保留した通知の JSON パス")
    # 🔴 **波はランク入稿と同じ値を受け取る**（2026-08-19）。
    #    実行時刻から導くと、朝のバッチが正午を跨いだ日に
    #    `netkeirin_submit_wt.py` は morning で走ったのに穴埋めだけ noon になり、
    #    **ナイター開催をランクより先に取る**（本モジュール docstring の事故の再発）。
    #    実際 session='morning' の穴埋めに submitted_at 12:08 の実績がある。
    #    省略時のみ実行時刻へフォールバック（手動実行の利便のため）。
    ap.add_argument("--session", choices=("morning", "noon", "evening"), default=None)
    args = ap.parse_args()
    date = args.date
    now_ts = int(datetime.now(JST).timestamp())

    session = args.session or session_of_hour(datetime.now(JST).hour)
    due_waves = due_waves_for(session)

    with get_connection() as conn:
        races = [dict(r) for r in conn.execute(
            "SELECT race_key, venue_id, race_no, race_type, n_entries, start_at, cup_id, cup_grade "
            "FROM wt_races WHERE race_date = ? ORDER BY venue_id, race_no", (date,))]
        # 🔴 **取消（status='deleted'）も「その日は処理済み」として扱う**
        #    （2026-08-13 変更・ユーザー判断）。取消は論理削除なので行が残る。
        #    人が確認して落とした看板レースを穴埋めが出し直すと、
        #    「確認して落としたはずのものが勝手に戻る」＝確認の意味が消える。
        #    判定は `netkeirin_submit_wt.py::_already_submitted` と**同じ条件**に
        #    すること（2026-08-11 に片側だけ直して食い違わせた前例がある）。
        #    `tests/test_marquee_fill_dedup_parity.py` が機械的に突き合わせている。
        submitted = {str(dict(r)["race_key"]).split("#")[0] for r in conn.execute(
            "SELECT race_key FROM netkeirin_submissions")}

    if not races:
        print(f"[marquee] {date}: レースが無い", flush=True)
        return 0

    # 開催（cup_id）ごとに看板＋前後を割り出す
    by_cup: dict[str, list[dict]] = {}
    for r in races:
        by_cup.setdefault(str(r["cup_id"]), []).append(r)

    venue_wave = venue_waves(races)

    allidx = _load_allindex(date)
    targets: list[dict] = []
    for cup, rs in by_cup.items():
        want = marquee_race_nos(rs)
        # 🔴 自分の波（+ 取りこぼした過去の波）の開催を埋める。判定は
        #    ランクの入稿と同じ `src/meeting_wave.py`（docstring の表を参照）。
        #    後の波の開催は**予測オッズを作れるレースだけ前倒しする**
        #    （2026-08-21・`netkeirin_submit_wt._can_pull_forward` と同じ考え方）。
        #    作れなければ従来どおり自分の波の回が埋める。
        wave = venue_wave.get(str(rs[0]["venue_id"]))
        ahead = wave not in due_waves
        for r in rs:
            if int(r["race_no"]) not in want:
                continue
            if r["race_key"] in submitted:
                continue
            # 締切（発走15分前）を過ぎたレースへは出さない。判定の正本は
            # `src/submit_window`（kiseki 側の keirin_submission_window.py）。
            if is_closed(r.get("start_at"), now_ts):
                continue
            if int(r.get("n_entries") or 0) not in RANK_BY_CARS:
                continue
            # 前倒しの可否は**埋める対象に残ったレースだけ**で見る（予測オッズの
            # 算出はモデルを走らせるので、開催の全レースに掛けない）。
            if ahead and not _can_pull_forward(r["race_key"]):
                print(f"[marquee] {r['race_key']}: 予測オッズを作れないので"
                      f"{WAVE_LABEL_JP.get(wave, wave)}の回へ回す", flush=True)
                continue
            targets.append(r)

    if not targets:
        print(f"[marquee] {date}: 埋める看板レースは無い", flush=True)
        return 0

    ok = ng = 0
    done: list[str] = []
    failed: list[str] = []
    # 平均払戻ゲートで見送った看板レース（成功でも失敗でもない別枠・§11.6.3）
    skipped_cheap: list[str] = []
    for r in sorted(targets, key=lambda x: int(x["start_at"] or 0)):
        e = allidx.get(r["race_key"])
        if not e:
            print(f"[marquee] {r['race_key']}: 指数が無い（skip）", flush=True)
            ng += 1
            continue
        # 🔴 ライン情報は7車でも引く。ここで None を渡すと `_axes()` は
        #    ライン情報なしのフォールバック（素の上位2車）へ落ちるので、
        #    組み替えを実装しても**静かに効かない**。
        ax = _axes(e, _lines(r["race_key"]))
        if ax is None:
            print(f"[marquee] {r['race_key']}: 軸を決められない（skip）", flush=True)
            ng += 1
            continue
        rank = RANK_BY_CARS[int(r["n_entries"])]
        cmd = [sys.executable, "scripts/netkeirin_submit_wt.py", date, session,
               "--marquee", "--race-key", r["race_key"],
               "--manual-rank-key", rank, "--axis1", str(ax[0]), "--axis2", str(ax[1]),
               # 🔴 子プロセスに通知させない。1レース1プロセスなので、各々が通知すると
               #    「手動入稿・1件」が件数ぶん飛ぶ（2026-08-11 に16通届いた実害）。
               #    まとめて1通を本スクリプト末尾で送る。
               "--no-notify"]
        if args.dry_run:
            cmd.append("--dry-run")
        print(f"[marquee] {e.get('venue_name')}{r['race_no']}R "
              f"({r['race_type']}) 軸={ax[0]}-{ax[1]} → {rank}", flush=True)
        p = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=str(Path(__file__).resolve().parent.parent))
        sys.stdout.write(p.stdout)
        label = f"{e.get('venue_name')}{r['race_no']}R({rank})"
        # 🔴 **平均払戻ゲートの見送りは「失敗」ではない**（2026-08-24・§11.6）。
        #    子プロセスは 0件・失敗0 で正常終了するので、`done` へ入れると
        #    「入稿した」と嘘になり、`failed` へ入れると毎朝の通知が警告で埋まる。
        #    stdout の目印で数えて**別枠**にする。自動化すると入稿自体が
        #    行われず `netkeirin_submissions` に痕跡が残らないので、
        #    ここが看板経路の唯一の可視性になる（§11.6.3）。
        if MEAN_PAYOUT_SKIP_TAG in p.stdout:
            skipped_cheap.append(label)
        elif p.returncode == 0 and "入稿失敗" not in p.stdout:
            ok += 1
            done.append(label)
        else:
            ng += 1
            failed.append(label)
            sys.stderr.write(p.stderr)
    print(f"[marquee] {date}: 完了（成功{ok}件・失敗{ng}件・"
          f"安い配当で見送り{len(skipped_cheap)}件）", flush=True)
    if not args.dry_run and (done or failed or skipped_cheap):
        # 🔴 ランク入稿が通知を保留していれば、そこへ穴埋めを足して1通送る。
        #    保留が無ければ従来どおり（手動実行・ランク入稿が落ちた日）。
        if not _send_merged_notice(args.defer_notify, date, done, failed,
                                   skipped_cheap):
            _notify_summary(date, done, failed, skipped_cheap)
    elif not args.dry_run:
        # 穴埋めが0件でも、ランク入稿が保留した通知は必ず送る
        _send_merged_notice(args.defer_notify, date, [], [])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
