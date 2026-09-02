"""課題リストの整形（2026-09-02 新設）。

Discord へ**課題**を通知するための整形だけを持つ。DB にも FastAPI にも
`src.notify.discord` にも依存しない純関数の集まりで、送信は呼び出し側が行う。

## 🔴 何を載せて、何を載せないか

    載せる  … 状態と次の行動（異常・仮説の状態遷移・承認待ち）
    載せない … 単日の成績数字（表示的中・ROI・払戻）

夜間レビューは §2 で毎晩「5〜95% の内側なら単日の数字は情報を持たない」と
自分で証明している。**その数字を通知に流すと必ず反応してしまう**ので、
通知の側で構造的に締め出す。累積で発火条件を超えた数字（＝判断材料になったもの）
だけは例外で、`承認待ち` の行に載る。

`nightly_review.sh` の「Discord へは1行の要約とリンクだけ」（2026-08-30・
ユーザー要望）とは衝突しない。**あちらは事実レポート、こちらは課題**で、
性質が違うから別チャンネルに分ける。

## 🔴 長文化はコードで止める

プロンプトで「短く」と書いても必ず伸びる。ここでは
**節ごとの行数上限**・**超過分の畳み込み**・**前夜と同一なら送らない**・
**Discord の 2000 文字制限**の4つを、文章ではなく関数で担保する。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

#: Discord の1メッセージ上限。実際は 2000 だが、末尾が切れると
#: 「…他 N 件」の畳み込みまで消えて**件数が分からなくなる**ので余裕を取る。
DISCORD_LIMIT = 2000
SAFE_LIMIT = 1900

#: 単日の成績数字を表す語。ここに載せてはいけない（上記の理由）。
#: 検査は `contains_daily_performance()`／固定は `tests/test_issue_list.py`。
PERFORMANCE_TOKENS = ("ROI", "表示的中", "的中率", "回収率", "払戻")


@dataclass
class Section:
    """通知の1節。`cap` が None なら全件出す（承認待ちは畳まない）。"""

    title: str
    items: list[str]
    cap: int | None = 5
    #: 0件のとき節ごと省くか。異常は「無い」ことに意味があるので False にする。
    hide_when_empty: bool = True
    empty_text: str = ""


@dataclass
class Message:
    day: str
    sections: list[Section]
    footer: str = ""
    tallies: list[str] = field(default_factory=list)


def _fold(items: list[str], cap: int | None) -> list[str]:
    """上限を超えた分を「…他 N 件」に畳む。"""
    if cap is None or len(items) <= cap:
        return list(items)
    kept = items[:cap]
    kept.append(f"・…他 {len(items) - cap} 件")
    return kept


def render(msg: Message) -> str:
    """通知本文を組み立てる。**2000 文字を超えないことを保証する**。"""
    out: list[str] = [f"📋 課題 {msg.day}"]
    for sec in msg.sections:
        if not sec.items:
            if sec.hide_when_empty:
                continue
            out += ["", f"**{sec.title}**", sec.empty_text or "・なし"]
            continue
        out += ["", f"**{sec.title}** ({len(sec.items)})"]
        out += _fold(sec.items, sec.cap)
    if msg.tallies:
        out += ["", " ・ ".join(msg.tallies)]
    if msg.footer:
        out.append(msg.footer)
    text = "\n".join(out)
    if len(text) <= SAFE_LIMIT:
        return text
    # 🔴 **後ろから削る**（後ろほど参考情報）。切り詰めたことは必ず本文に残す。
    #    黙って切ると「載っていない＝無い」と読まれる。
    while len(text) > SAFE_LIMIT and len(out) > 2:
        out.pop(-2 if msg.footer else -1)
        text = "\n".join(out[:-1] + ["⚠️ 長すぎるため省略しました → リンク先を参照"]
                         + ([msg.footer] if msg.footer else []))
    return text[:DISCORD_LIMIT]


def contains_daily_performance(text: str) -> bool:
    """単日の成績数字が混ざっていないか（`PERFORMANCE_TOKENS` の素朴な検査）。"""
    return any(t in text for t in PERFORMANCE_TOKENS)


def digest(msg: Message) -> str:
    """差分抑止に使う指紋。

    🔴 **日付・リンク・件数の集計は含めない。** 毎晩必ず変わるので、
       含めると「前夜と同じなら送らない」が永久に効かない。
    """
    body = "\n".join(f"{s.title}:{i}" for s in msg.sections for i in s.items)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def should_send(state_path: Path, kind: str, fingerprint: str) -> bool:
    """前回と同じ内容なら False。中身が空（＝課題なし）でも False。"""
    if not fingerprint or fingerprint == EMPTY_DIGEST:
        return False
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    return state.get(kind, {}).get("digest") != fingerprint


def remember(state_path: Path, kind: str, fingerprint: str, day: str) -> None:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state[kind] = {"digest": fingerprint, "day": day}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                          encoding="utf-8")


#: 中身が1件も無いときの指紋（`should_send` が送らないと判断する目印）。
EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()[:16]


def build_anomaly_message(day: str, ng_items: list[str], url: str = "",
                          n_ok: int = 0) -> Message:
    """§1 異常検知の [NG] を課題リストにする。

    ⚠️ `ng_items` は `[NG] ` の接頭辞を外した本文だけを渡すこと。
    """
    return Message(
        day=day,
        sections=[Section("今夜やること", [f"・[異常] {t}" for t in ng_items],
                          cap=5)],
        tallies=[f"正常 {n_ok} 項目"] if n_ok else [],
        footer=f"📊 <{url}>" if url else "",
    )
