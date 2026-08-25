"""入稿を見送った理由が**必ず記録される**ことを構造で固定する。

見送りの理由は 2026-08-25 まで**ログにしか残っていなかった**。表示を
「売った商品」へ揃えると、記録が欠けた経路はそのまま画面上の「理由不明」になる。
条件（レビューでの注意）ではなく**構造**（`_skip()` を通す）で守り、
その構造が壊れていないことをここで見る。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SUBMIT_PY = ROOT / "scripts" / "netkeirin_submit_wt.py"


def test_レース単位のスキップはprintを直接書かず_skipを通す():
    """`print(f"[netkeirin_submit] スキップ {venue_name}...` を禁止する。

    🔴 print だけを足すと、その理由は DB に残らず画面で「理由不明」になる。
       新しいゲートを足すときは `_skip()` を呼ぶこと。
    """
    src = SUBMIT_PY.read_text(encoding="utf-8")
    bad = re.findall(
        r'print\(\s*f"\[netkeirin_submit\]\s*(?:スキップ|前倒し見送り|入稿失敗)\s*\{',
        src,
    )
    assert not bad, (
        f"レース単位の見送りを print で直接出している箇所が {len(bad)} 件あります。"
        " `_skip(race_key, rank_key, session, code, detail, venue, race_no)` を使うこと。"
    )


def test_全てのゲートが理由コードつきで記録される():
    """本番の各ゲートが `_skip` を呼んでいること（コード別に少なくとも1回）。"""
    import ast

    tree = ast.parse(SUBMIT_PY.read_text(encoding="utf-8"))
    used: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_skip"):
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.Name):
                    used.add(sub.id)
    for const in ("SKIP_GATE_MEAN_PAYOUT", "SKIP_GATE_POINT_ODDS",
                  "SKIP_GATE_EXPECTED_FLOOR", "SKIP_RANK_CONFLICT",
                  "SKIP_CLOSED", "SKIP_DEFER_WAVE", "SKIP_CANDIDATE_INVALID",
                  "SKIP_SUBMIT_FAILED"):
        assert const in used, (
            f"{const} を使う `_skip()` の呼び出しがありません。"
            " ゲートを消したのならこのテストも一緒に直すこと。"
        )


def test_平均払戻ゲートのログ文言は看板側の集計が読むので変えない():
    """`submit_marquee_wt.py` が子プロセスの stdout を見て件数を数えている。"""
    src = SUBMIT_PY.read_text(encoding="utf-8")
    assert "MEAN_PAYOUT_SKIP_TAG" in src
    marquee = (ROOT / "scripts" / "submit_marquee_wt.py").read_text(encoding="utf-8")
    assert "MEAN_PAYOUT_SKIP_TAG" in marquee


def test_入稿失敗のログには入稿失敗の語が残る():
    """`submit_marquee_wt.py:550` が `"入稿失敗" not in p.stdout` で成功判定する。"""
    src = SUBMIT_PY.read_text(encoding="utf-8")
    assert 'tag="入稿失敗"' in src


# ---------------------------------------------------------------------------
# 記録そのもの
# ---------------------------------------------------------------------------

def _conn(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.execute("""
        CREATE TABLE submission_skips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            race_date TEXT NOT NULL, race_key TEXT NOT NULL,
            rank_key TEXT NOT NULL, session TEXT NOT NULL,
            reason_code TEXT NOT NULL, reason_text TEXT,
            decided_at TEXT DEFAULT (datetime('now')),
            UNIQUE (race_key, rank_key, session)
        )
    """)
    return conn


def test_見送りを記録できる(tmp_path):
    from src.submission_skips import GATE_MEAN_PAYOUT, record_skip

    conn = _conn(tmp_path)
    assert record_skip(conn, "20260825_47_07#7S", "7S", "morning",
                       GATE_MEAN_PAYOUT, "平均払戻 19,226円 <= 20,000円")
    row = conn.execute("SELECT race_date, race_key, reason_code, reason_text "
                       "FROM submission_skips").fetchone()
    # 🔴 race_key はランク接尾辞を落として netkeirin_submissions と揃える
    assert row == ("2026-08-25", "20260825_47_07", GATE_MEAN_PAYOUT,
                   "平均払戻 19,226円 <= 20,000円")


def test_同じ波で2回流しても増えず上書きされる(tmp_path):
    from src.submission_skips import GATE_MEAN_PAYOUT, GATE_POINT_ODDS, record_skip

    conn = _conn(tmp_path)
    record_skip(conn, "20260825_47_07", "7S", "morning", GATE_MEAN_PAYOUT, "a")
    record_skip(conn, "20260825_47_07", "7S", "morning", GATE_POINT_ODDS, "b")
    rows = conn.execute("SELECT reason_code, reason_text FROM submission_skips").fetchall()
    assert rows == [(GATE_POINT_ODDS, "b")]


def test_波が違えば別の行として残る(tmp_path):
    """朝に見送り→夕方に入稿、の経緯が追えること。"""
    from src.submission_skips import DEFER_WAVE, record_skip

    conn = _conn(tmp_path)
    record_skip(conn, "20260825_47_07", "7S", "morning", DEFER_WAVE, "朝は見送り")
    record_skip(conn, "20260825_47_07", "7S", "evening", DEFER_WAVE, "夕も見送り")
    assert conn.execute("SELECT COUNT(*) FROM submission_skips").fetchone()[0] == 2


def test_語彙にないコードは書かない(tmp_path):
    """未知のコードは表示側で「見送り」に潰れるので、書かずに気づけるようにする。"""
    from src.submission_skips import record_skip

    conn = _conn(tmp_path)
    assert record_skip(conn, "20260825_47_07", "7S", "morning", "nope", "x") is False
    assert conn.execute("SELECT COUNT(*) FROM submission_skips").fetchone()[0] == 0


def test_記録に失敗しても例外を投げない(tmp_path):
    """入稿処理を止めないこと。テーブルが無くても False を返すだけ。

    🔴 見送りの記録は表示のための付随情報で、商品を出す/出さないの判断には
       関わらない。ここで例外を投げると**記録の都合で入稿が止まる**。
    """
    from src.submission_skips import GATE_MEAN_PAYOUT, record_skip

    conn = sqlite3.connect(tmp_path / "empty.db")   # テーブルを作らない
    assert record_skip(conn, "20260825_47_07", "7S", "morning",
                       GATE_MEAN_PAYOUT, "x") is False


def test_理由の語彙は正本と一致している():
    """keirin 側が自前で文字列を持たず、backend の正本を読んでいること。"""
    import src.submission_skips as ss

    canonical = (ROOT.parent / "backend" / "src" / "services"
                 / "keirin_skip_reasons.py")
    assert canonical.exists(), "正本が見つかりません（リポジトリ構成が変わった？）"
    assert ss.GATE_MEAN_PAYOUT == "gate_mean_payout"
    assert ss.label("gate_mean_payout") == "平均払戻"
    # 未知でも空にしない（バッジが消えると見送りが「購入」と見分けられない）
    assert ss.label("nope") == "見送り"
    assert ss.label(None) == "見送り"


def test_正本にはFastAPIもSQLAlchemyも入っていない():
    """keirin は自分の venv からこのファイルを直接読む。依存を足すと入稿だけが落ちる。"""
    canonical = (ROOT.parent / "backend" / "src" / "services"
                 / "keirin_skip_reasons.py").read_text(encoding="utf-8")
    for ng in ("import sqlalchemy", "from sqlalchemy", "import fastapi",
               "from fastapi", "from src.", "import pydantic"):
        assert ng not in canonical, f"正本が {ng} を import しています"


# ═══════════════════════════════════════════════════════════════════════════
# 別ランクとの衝突は「入稿失敗」ではない（2026-08-26・ユーザー指示）
# ═══════════════════════════════════════════════════════════════════════════
#
# 昼(13:00)・夕(18:00)の回は、朝と**同じ候補ファイルを読み直して再判定する**
# （`_load_candidates` は `_noon` / `_night` の再生成物が無ければ朝の生成物へ
# 落ちる）。したがって朝に決着したレースが毎回もう一度候補に上がり、
# 「別ランクが同じレースを入稿済み」で降りる。
#
# 🔴 実測 2026-08-25（昼）: Discord に「**全8件が入稿失敗**」と出たが、
#    8件すべてが朝に**意図どおり**入稿済みのレースだった:
#      宇都宮2R/3R … 7S が平均払戻ゲートで降り、下位の 7M1 が拾った（差し替え）
#      防府11R/12R・和歌山11R … 上位の 7T1 が取った
#      和歌山10R … 7S 自身がゲートで降りたレースを別ランクが取った
#      松戸3R … 7H1 が「三連単は板が要る」で昼へ回し、看板穴埋めが取った
#    どれも 1レース1商品という設計どおりの譲り合いで、失敗ではない。


def test_衝突は失敗に数えない():
    """`conflicts` のループが `failures` へ入れないこと。"""
    import ast

    tree = ast.parse(SUBMIT_PY.read_text(encoding="utf-8"))
    loops = [n for n in ast.walk(tree)
             if isinstance(n, ast.For) and isinstance(n.iter, ast.Name)
             and n.iter.id == "conflicts"]
    assert loops, "conflicts のループが見つかりません"
    for loop in loops:
        for sub in ast.walk(loop):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "append"
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "failures"):
                raise AssertionError(
                    "衝突を failures へ入れています。1レース1商品なので上位ランクへ"
                    " 譲るのは正常動作で、Discord の「入稿失敗」に出してはいけません。")


def test_衝突は記録と件数に残る():
    """🔴 通知しないだけで**見えなくしない**。記録とサマリーには必ず残す。"""
    src = SUBMIT_PY.read_text(encoding="utf-8")
    assert "_rank_conflict_skips" in src, "衝突の件数を数えていません"
    assert "_rank_conflict_skips.clear()" in src, "実行ごとに数え直していません"
    assert "別ランクへ譲り" in src, "実行サマリーに件数が出ていません"
    # Discord へ出す本文には混ぜないこと（ユーザー指示 2026-08-26）。
    # 通知本文は `session_jp = ...` から実行サマリーの print までの区間で組む。
    notify = src[src.index("session_jp = SESSION_LABEL_JP[session]"):
                 src.index('f"[netkeirin_submit] {target_date} {session}: 完了（成功')]
    assert "_rank_conflict_skips" not in notify, \
        "Discord 通知に衝突を混ぜています（通知は不要というユーザー指示）"


def test_overlap_expectedフラグは廃止済み():
    """⚠️ 復活させないこと。

    「排他設計のランクの衝突だけ失敗として可視化する」という前提が、
    7T1 / 7T3（7S より上位）と看板穴埋めの追加で成り立たなくなった。
    衝突はどのランクでも失敗ではないので、フラグの役目そのものが無い。
    """
    src = SUBMIT_PY.read_text(encoding="utf-8")
    assert '"overlap_expected"' not in src, "overlap_expected が復活しています"
