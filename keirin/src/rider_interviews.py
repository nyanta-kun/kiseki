"""選手コメント（前検日・レース後）の取得・保存・分類（2026-10-01 新設）。

winticket のページの `__PRELOADED_STATE__` に選手コメントが埋め込まれている:

| kind | ページ | クエリ | 使える範囲 |
|---|---|---|---|
| `pre`  | racecard（開催初日のレース） | `FETCH_KEIRIN_INSPECTION_DAY_INTERVIEW_LIST` | その開催の全レース（開催前日の談話） |
| `post` | raceresult（そのレース）       | `FETCH_KEIRIN_RACE_RESULT_INTERVIEW_LIST`    | **次の出走以降だけ** |

- 🔴 本文があるのは **2025-12 以降だけ**。それ以前は枠（playerId）だけで answer が空
  （件数だけ数えると「100%掲載」に見える。2026-09-30 に誤読しかけた）
- 🔴 pre のページはそのレースの選手ぶんだけを持つ。開催全員を取るには**初日の全レース**を読む
- 公開時刻はページ単位の `createdAt` / `updatedAt`（UNIX 秒）。時点を守るときは
  **`updatedAt`（遅い方）で切る**（本文は取得時点の最終版なので）

分類（`TAG_*`）は Claude に付けさせる。記号の意味は `SYSTEM_PROMPT`。
2026-10-01 の検証（142,624件）で効いたのは「調子（condition）」「落車・怪我（trouble の F / I）」
「自信（confidence）」。詳細は `docs/type_lab/prereg_rider_condition_2026_10_01.md`。
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from src.scraper.winticket import VENUE_SLUGS

PRE_QUERY = "FETCH_KEIRIN_INSPECTION_DAY_INTERVIEW_LIST"
POST_QUERY = "FETCH_KEIRIN_RACE_RESULT_INTERVIEW_LIST"
PAGE_OF = {"pre": "racecard", "post": "raceresult"}
QUERY_OF = {"pre": PRE_QUERY, "post": POST_QUERY}
_UA = "Mozilla/5.0"

#: 調子が良いとみなす condition の下限（画面で選手名を青字にする条件）。
GOOD_CONDITION_MIN = 1

#: 分類に使うモデル（過去分は API Batch、日次は `claude -p --model haiku`）。
TAG_MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT = """あなたは競輪の選手コメントを分類する担当です。書かれている事実だけから項目を付け、書かれていないことは推測せず不明側（0 / "n" / "u"）にします。出力は短い記号で返します。

- i: 入力の i（通し番号）をそのまま
- c: 体の状態・調子の自己申告。-2=明確に悪い（体調不良・痛み・強い疲労）, -1=やや悪い・不安, 0=言及なし, 1=良い, 2=非常に良い
- t: トラブルの記号を並べた文字列。F=落車・転倒, I=怪我・痛み, S=体調不良・風邪, E=機材トラブル。無ければ "n"（例 "FI"）
- f: 疲労・連戦のきつさへの言及があれば 1、無ければ 0
- e: フレーム・ギヤ・セッティングを変えた／試している言及があれば 1、無ければ 0
- s: 入力の kind が post のときの自己評価。o=力負け・脚が足りない, t=展開・位置取り・判断の問題, m=自分のミス, g=内容は良かった・手応えあり, u=不明。kind が pre なら u
- r: 調子の方向。p=上向き, d=下向き, f=変わらない, u=不明
- n: 次（post）または今開催（pre）の走り方。o=自力・先行・前で, f=番手・マーク・誰かに付く, s=単騎, u=不明
- v: 本人の自信・前向きさ。-1, 0, 1

全件を返してください。"""

TAG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"x": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "i": {"type": "integer"},
            "c": {"type": "integer", "enum": [-2, -1, 0, 1, 2]},
            "t": {"type": "string"},
            "f": {"type": "integer", "enum": [0, 1]},
            "e": {"type": "integer", "enum": [0, 1]},
            "s": {"type": "string", "enum": ["o", "t", "m", "g", "u"]},
            "r": {"type": "string", "enum": ["p", "d", "f", "u"]},
            "n": {"type": "string", "enum": ["o", "f", "s", "u"]},
            "v": {"type": "integer", "enum": [-1, 0, 1]},
        },
        "required": ["i", "c", "t", "f", "e", "s", "r", "n", "v"],
        "additionalProperties": False,
    }}},
    "required": ["x"],
    "additionalProperties": False,
}

#: 分類の記号 → 列名。
TAG_COLUMNS = {"c": "condition", "t": "trouble", "f": "fatigue", "e": "equipment",
               "s": "self_eval", "r": "trend", "n": "tactic", "v": "confidence"}


# ── 取得 ──────────────────────────────────────────────────────────────────

def page_url(kind: str, venue_id: str | int, cup_id: str, day_index: int, race_no: int) -> str:
    """コメントを持つページの URL。"""
    slug = VENUE_SLUGS[str(venue_id).zfill(2)]
    return f"https://www.winticket.jp/keirin/{slug}/{PAGE_OF[kind]}/{cup_id}/{day_index}/{race_no}"


def parse_state(html: str) -> dict:
    """ページの `__PRELOADED_STATE__` を dict で返す（後ろに別の JS が続くので raw_decode）。"""
    i = html.index("__PRELOADED_STATE__")
    j = html.index("{", i)
    return json.JSONDecoder().raw_decode(html[j:])[0]


def interview_data(state: Mapping, kind: str) -> dict:
    """state からそのページの interview データ（interviews / createdAt / updatedAt）を取り出す。無ければ {}。"""
    for q in state.get("tanStackQuery", {}).get("queries", []):
        key = q.get("queryKey") or []
        if len(key) >= 2 and key[0] == "keirin/interview" and key[1] == QUERY_OF[kind]:
            return (q.get("state") or {}).get("data") or {}
    return {}


def fetch_interview_data(url: str, kind: str, retries: int = 3) -> dict:
    """ページを取ってコメントのデータを返す（失敗は例外）。"""
    for t in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            html = urllib.request.urlopen(req, timeout=30).read().decode()
            return interview_data(parse_state(html), kind)
        except Exception:
            if t == retries - 1:
                raise
            time.sleep(5)
    return {}


def _ts(v: Any) -> str | None:
    if not v:
        return None
    return datetime.fromtimestamp(int(v), tz=timezone.utc).isoformat()


def rows_from_data(kind: str, race_key: str, cup_id: str | None, race_date: str,
                   data: Mapping) -> list[dict]:
    """interview データを保存用の行にする。本文が空の選手は除く（2025-11 以前は全員空）。

    >>> d = {"interviews": [{"playerId": "015830", "threads": [{"question": "q", "answer": "力負けです。"}]},
    ...                     {"playerId": "012924", "threads": [{"question": "q", "answer": ""}]}],
    ...      "createdAt": 1790741033, "updatedAt": 1790741386}
    >>> [(r["player_id"], r["body"]) for r in rows_from_data("post", "k", "c", "2026-09-30", d)]
    [(15830, '力負けです。')]
    """
    out = []
    for iv in data.get("interviews") or []:
        body = " ".join((t.get("answer") or "").strip() for t in iv.get("threads") or []).strip()
        pid = iv.get("playerId")
        if not body or not pid:
            continue
        out.append({"kind": kind, "race_key": race_key, "cup_id": cup_id, "race_date": str(race_date),
                    "player_id": int(pid), "body": body,
                    "src_created_at": _ts(data.get("createdAt")),
                    "src_updated_at": _ts(data.get("updatedAt"))})
    return out


# ── 保存 ──────────────────────────────────────────────────────────────────

def upsert(conn, rows: Iterable[Mapping]) -> tuple[int, int]:
    """行を保存する。新規は追加、本文が変わった行は本文を差し替えて**分類を消す**（付け直させる）。

    Returns:
        (追加した数, 本文が変わって更新した数)
    """
    ins = upd = 0
    for r in rows:
        cur = conn.execute(
            "SELECT body FROM rider_interviews WHERE kind = ? AND race_key = ? AND player_id = ?",
            (r["kind"], r["race_key"], r["player_id"])).fetchone()
        if cur is None:
            conn.execute(
                "INSERT INTO rider_interviews (kind, race_key, cup_id, race_date, player_id, body, "
                "src_created_at, src_updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (r["kind"], r["race_key"], r.get("cup_id"), r["race_date"], r["player_id"], r["body"],
                 r.get("src_created_at"), r.get("src_updated_at")))
            ins += 1
        elif cur[0] != r["body"]:
            conn.execute(
                "UPDATE rider_interviews SET body = ?, src_created_at = ?, src_updated_at = ?, "
                "condition = NULL, trouble = NULL, fatigue = NULL, equipment = NULL, self_eval = NULL, "
                "trend = NULL, tactic = NULL, confidence = NULL, tag_model = NULL, tagged_at = NULL "
                "WHERE kind = ? AND race_key = ? AND player_id = ?",
                (r["body"], r.get("src_created_at"), r.get("src_updated_at"),
                 r["kind"], r["race_key"], r["player_id"]))
            upd += 1
    return ins, upd


def apply_tags(conn, tagged: Iterable[tuple[int, Mapping]], model: str = TAG_MODEL) -> int:
    """(行id, 分類の記号 dict) を保存する。記号は `TAG_COLUMNS` のキー。"""
    n = 0
    now = datetime.now(timezone.utc).isoformat()
    for row_id, t in tagged:
        vals = [t[k] for k in TAG_COLUMNS]
        sets = ", ".join(f"{col} = ?" for col in TAG_COLUMNS.values())
        conn.execute(f"UPDATE rider_interviews SET {sets}, tag_model = ?, tagged_at = ? WHERE id = ?",
                     (*vals, model, now, row_id))
        n += 1
    return n


def valid_tag(t: Mapping) -> bool:
    """分類結果の1件が記号の範囲に収まっているか（壊れた出力を保存しないため）。"""
    try:
        return (t["c"] in (-2, -1, 0, 1, 2) and t["f"] in (0, 1) and t["e"] in (0, 1)
                and t["s"] in ("o", "t", "m", "g", "u") and t["r"] in ("p", "d", "f", "u")
                and t["n"] in ("o", "f", "s", "u") and t["v"] in (-1, 0, 1)
                and isinstance(t["t"], str) and 0 < len(t["t"]) <= 8)
    except (KeyError, TypeError):
        return False
