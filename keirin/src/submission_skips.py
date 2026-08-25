"""入稿を見送った理由を DB へ残す。

## なぜ必要か

各ゲート（平均払戻・安い目・想定払戻の下限）は `continue` で抜けるだけで、
理由は VPS のログにしか無かった。表示を「売った商品」へ揃えると
見送ったレースは一覧から購入表示が消えるが、**なぜ売らなかったのかが
画面から分からなくなる**。ここで記録して、Web がバッジで出せるようにする。

🔴 **`print` を出す場所と同じ場所で必ず呼ぶこと。** ログにだけ出て DB に
   残らない経路ができると、その理由は永久に「理由不明」になる。
   `tests/test_submission_skips.py` が `netkeirin_submit_wt.py` の
   スキップ地点と記録の対応を機械的に見ている。

🔴 **記録に失敗しても入稿処理を止めない。** これは表示のための付随情報で、
   商品を出す/出さないの判断には一切関わらない。失敗はログに出して先へ進む。

語彙（reason_code）の正本は `backend/src/services/keirin_skip_reasons.py`。
看板判定（`marquee.py`）・採点（`sold_performance.py`）と同じく、
ファイル指定で読み込んで束縛する。**ここに文字列を書き直さないこと。**
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

# keirin/src/submission_skips.py → parents[2] が kiseki のリポジトリルート。
_CANONICAL = (Path(__file__).resolve().parents[2]
              / "backend" / "src" / "services" / "keirin_skip_reasons.py")
_MODULE_NAME = "kiseki_keirin_skip_reasons"


def _load_canonical() -> ModuleType:
    """kiseki 側の理由コードの正本をファイルから読み込む。

    ⚠️ `sys.path` に `backend/` を足す方式は使えない（keirin にも `src`
       パッケージがあり名前が衝突する）。`sold_performance._load_canonical`
       と同じ理由・同じ形。
    """
    cached = sys.modules.get(_MODULE_NAME)
    if cached is not None:
        return cached
    if not _CANONICAL.exists():
        raise ImportError(
            f"見送り理由の正本が見つかりません: {_CANONICAL}\n"
            "keirin は kiseki リポジトリ内（<kiseki>/keirin）で動かす前提です。"
        )
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _CANONICAL)
    if spec is None or spec.loader is None:        # pragma: no cover - 実質起きない
        raise ImportError(f"正本を読み込めません: {_CANONICAL}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_canonical = _load_canonical()

GATE_MEAN_PAYOUT = _canonical.GATE_MEAN_PAYOUT
GATE_POINT_ODDS = _canonical.GATE_POINT_ODDS
GATE_EXPECTED_FLOOR = _canonical.GATE_EXPECTED_FLOOR
RANK_CONFLICT = _canonical.RANK_CONFLICT
CLOSED = _canonical.CLOSED
DEFER_WAVE = _canonical.DEFER_WAVE
CANDIDATE_INVALID = _canonical.CANDIDATE_INVALID
SUBMIT_FAILED = _canonical.SUBMIT_FAILED
ALL_CODES = _canonical.ALL_CODES
label = _canonical.label
describe = _canonical.describe


def race_date_of(race_key: str) -> str:
    """`'20260825_47_07#7S'` → `'2026-08-25'`。

    >>> race_date_of("20260825_47_07#7S")
    '2026-08-25'
    >>> race_date_of("20260825_47_07")
    '2026-08-25'
    """
    head = race_key.split("#", 1)[0]
    ymd = head.split("_", 1)[0]
    return f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"


def base_key_of(race_key: str) -> str:
    """ランク接尾辞を落とす。`netkeirin_submissions.race_key` と同じ形にする。

    >>> base_key_of("20260825_47_07#7S")
    '20260825_47_07'
    """
    return race_key.split("#", 1)[0]


#: `INSERT ... ON CONFLICT` の SQL。SQLite / PostgreSQL 双方で同じ文が通る
#: （`src/database.py` の `_PgConn` が `?` を `%s` へ置き換える）。
_UPSERT_SQL = """
INSERT INTO submission_skips
    (race_date, race_key, rank_key, session, reason_code, reason_text)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (race_key, rank_key, session) DO UPDATE SET
    reason_code = excluded.reason_code,
    reason_text = excluded.reason_text,
    decided_at  = CURRENT_TIMESTAMP
"""


#: その回でランクが「自分の波へ回した」レースを引く SQL。
_DEFERRED_SQL = """
SELECT race_key FROM submission_skips
WHERE race_date = ? AND session = ? AND reason_code = ?
"""


def deferred_race_keys(conn, race_date: str, session: str) -> set[str]:
    """**この回のランク入稿が後の波へ持ち越した**レース（`race_key` の集合）。

    🔴 **看板穴埋めが横取りしないために要る**（2026-08-26・ユーザー判断）。
       三連単をダッチ配分するランク（7H1 / 7H2 / 9H1）は、朝は三連単の板が
       無いので後の波の開催を**必ず**持ち越す（`_can_pull_forward`）。
       ランク入稿どうしは `deferred_races`（実行中のメモリ）で守られているが、
       看板穴埋めは**別プロセス**なのでそれを見られず、同じレースを埋めていた。
       実測 2026-08-25: 松戸3R を 7H1 が昼へ回した直後に看板穴埋めが 7S で埋め、
       昼の回で 7H1 が「別ランクが入稿済み」で取れなくなった。

    🔴 **`session` で絞ること。** 持ち越しは「その回では出さない」という意味しか
       持たない。レースが自分の波に入れば持ち越しは起きないので、次の回では
       この集合に現れず、普通に優先順位勝負へ戻る。日付だけで絞ると
       **そのレースが一日中どのランクにも埋められなくなる**。

    ⚠️ 読めなければ**空集合**を返す（＝従来どおり埋める）。記録は表示のための
       付随情報で、これが引けないことを理由に看板レースを空にしない。
    """
    try:
        rows = conn.execute(_DEFERRED_SQL, (race_date, session, DEFER_WAVE)).fetchall()
    except Exception as e:                          # pragma: no cover - 経路のみ検査
        print(f"[submission_skips] 持ち越しの読み出しに失敗（継続）: {e}", flush=True)
        return set()
    # ⚠️ 行の型は接続で違う（sqlite3.Row / RealDictRow / 素のタプル）。
    #    1列しか選んでいないので、名前で引けなければ位置で取る。
    return {base_key_of(str(r["race_key"] if hasattr(r, "keys") else r[0]))
            for r in rows}


def record_skip(
    conn,
    race_key: str,
    rank_key: str,
    session: str,
    reason_code: str,
    reason_text: str | None = None,
) -> bool:
    """見送りを1件記録する。**失敗しても例外を投げない**（True/False を返す）。

    Args:
        conn: `src.database.get_connection()` が返す接続
        race_key: ランク接尾辞つきでも可（内部で落とす）
        rank_key: `'7S'` のような netkeirin 側の商品キー
        session: `'morning'` / `'noon'` / `'evening'`
        reason_code: `keirin_skip_reasons` の定数
        reason_text: 実測値つきの文言（例 `'平均払戻 19,226円 <= 20,000円'`）
    """
    if reason_code not in ALL_CODES:
        # 語彙にないコードは**書かない**。書くと表示側で「見送り」に潰れ、
        # 正本を直す機会を失う。
        print(f"[submission_skips] 未知の理由コード: {reason_code}", flush=True)
        return False
    try:
        conn.execute(_UPSERT_SQL, (
            race_date_of(race_key), base_key_of(race_key), rank_key,
            session, reason_code, reason_text,
        ))
        return True
    except Exception as e:                          # pragma: no cover - 経路のみ検査
        print(f"[submission_skips] 記録に失敗（継続）: {race_key} {rank_key} {e}",
              flush=True)
        return False
