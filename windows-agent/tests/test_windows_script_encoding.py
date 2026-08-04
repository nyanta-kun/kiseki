"""Windows スクリプト (.vbs / .ps1 / .bat) の行終端を検査する。

cscript と PowerShell 5.1 は BOM 無し UTF-8 を CP932(ANSI) として読む。
日本語コメント行の末尾文字の最終バイトが CP932 の先行バイト範囲に当たると、
その文字が次の1バイトを飲み込む。LF 単独だと改行そのものが消え、次の行が
コメントに吸収されてスクリプトが壊れる（2026-08-04 に run_eod_cleanup.vbs で発生）。

CRLF なら飲み込まれるのは CR だけで LF が生き残るため、この事故は起きない。
詳細は windows-agent/.gitattributes を参照。

**CRLF が守るのは改行だけ**であることに注意。行の途中は CP932 として誤読された
ままなので、**文字列リテラルの中に日本語を書いてはいけない**。誤読の結果として
`"` が飲み込まれると文字列が閉じず、そこで構文エラーになる
（2026-08-04 にこの検査用のテストスクリプト自身で踏んだ）。
コメントは行末まで読み飛ばされるだけなので日本語で問題ない。

    python3 -m pytest windows-agent/tests/test_windows_script_encoding.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

WINDOWS_AGENT = Path(__file__).resolve().parent.parent
PATTERNS = ("*.vbs", "*.ps1", "*.bat")


def _scripts() -> list[Path]:
    found: list[Path] = []
    for pattern in PATTERNS:
        found.extend(sorted(WINDOWS_AGENT.glob(pattern)))
    return found


def _is_cp932_lead(b: int) -> bool:
    """CP932 の先行バイト範囲。ここに当たる末尾バイトが次の1バイトを飲み込む。"""
    return 0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFC


def test_scripts_are_discovered() -> None:
    """glob が空だと以下のテストが無条件に通ってしまうので先に守る。"""
    assert _scripts(), f"{WINDOWS_AGENT} に Windows スクリプトが見つかりません"


@pytest.mark.parametrize("path", _scripts(), ids=lambda p: p.name)
def test_uses_crlf_line_endings(path: Path) -> None:
    """全行が CRLF で終わること。.gitattributes の eol=crlf が効いているかの検査。"""
    raw = path.read_bytes()
    lone_lf = [
        i + 1
        for i, line in enumerate(raw.split(b"\n")[:-1])
        if not line.endswith(b"\r")
    ]
    assert not lone_lf, (
        f"{path.name}: CRLF でない行があります (行 {lone_lf[:10]})。"
        " .gitattributes の eol=crlf が作業ツリーに適用されていません。"
        " eol=crlf の導入前に作られた worktree / clone では必ず起きる"
        "（フィルタはチェックアウト時に走るので、既にあるファイルは LF のまま残る）。"
        " 作業ツリーを作り直してください:\n"
        "    rm -f windows-agent/*.vbs windows-agent/*.ps1 windows-agent/*.bat\n"
        "    git checkout -- windows-agent/\n"
        " `git add --renormalize` では直らない（index を直すだけで作業ツリーは LF のまま。"
        " Windows への配備は作業ツリーから scp するため、それでは配備物が壊れたままになる）。"
    )


@pytest.mark.parametrize("path", _scripts(), ids=lambda p: p.name)
def test_no_line_ends_with_cp932_lead_byte_before_lone_lf(path: Path) -> None:
    """CRLF が失われた配置経路でも壊れないことを二重に確認する。

    CRLF さえ守れていれば理論上この検査は不要だが、scp / エディタ / CI が
    CR を落とす経路は現実に存在する。壊れ方が「無言の exit 1」なので二重に張る。
    """
    broken: list[int] = []
    for i, line in enumerate(path.read_bytes().split(b"\n"), 1):
        if line.endswith(b"\r"):
            continue  # CRLF は安全（先行バイトは CR を食うだけ）
        if line and _is_cp932_lead(line[-1]):
            broken.append(i)
    assert not broken, (
        f"{path.name}: LF 終端かつ末尾が CP932 先行バイトの行があります (行 {broken[:10]})。"
        " cscript / PowerShell が次行を飲み込みます。"
    )


def _string_literal_spans(line: str) -> list[str]:
    """VBScript / PowerShell の 1 行から二重引用符文字列の中身を取り出す。

    VBScript は文字列中の `""` で引用符自身を表すため、閉じ引用符の直後がまた
    引用符ならエスケープとみなして継続する。行コメント (`'` / `#`) の外だけを見る。
    """
    spans: list[str] = []
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch in ("'", "#"):
            break  # ここから行末まではコメント
        if ch != '"':
            i += 1
            continue
        i += 1
        buf: list[str] = []
        while i < n:
            if line[i] == '"':
                if i + 1 < n and line[i + 1] == '"':
                    buf.append('"')
                    i += 2
                    continue
                i += 1
                break
            buf.append(line[i])
            i += 1
        spans.append("".join(buf))
    return spans


UTF8_BOM = b"\xef\xbb\xbf"


@pytest.mark.parametrize("path", _scripts(), ids=lambda p: p.name)
def test_no_non_ascii_inside_string_literals(path: Path) -> None:
    """文字列リテラルに非 ASCII を入れないこと。

    CRLF は改行を守るだけで、行の途中は CP932 として誤読されたまま。誤読で `"` が
    飲み込まれると文字列が閉じず構文エラーになる。コメントは行末まで読み飛ばされる
    ので日本語で構わない。

    例外: **BOM 付き** の .ps1 / .bat。PowerShell 5.1 は BOM があれば UTF-8 として
    正しく読むため誤読が起きない。
    .vbs には逃げ道が無い（cscript は .vbs の UTF-8 BOM を認識しないことを実機で確認済み。
    有効なのは CP932 と UTF-16LE のみで、どちらも git で扱いづらい）。
    """
    raw = path.read_bytes()
    if path.suffix.lower() in (".ps1", ".bat") and raw.startswith(UTF8_BOM):
        pytest.skip("BOM 付きなので PowerShell が UTF-8 として正しく読む")

    bad: list[tuple[int, str]] = []
    text = raw.decode("utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), 1):
        for span in _string_literal_spans(line):
            if any(ord(c) > 0x7F for c in span):
                bad.append((lineno, span))
    hint = (
        " .ps1 なら UTF-8 BOM を付ければ解決する（CLAUDE.md 記載の規約）。"
        if path.suffix.lower() in (".ps1", ".bat")
        else " .vbs は BOM が効かないので ASCII で書くしかない。"
    )
    assert not bad, (
        f"{path.name}: 文字列リテラルに非 ASCII があります {bad[:5]}。"
        " cscript / PowerShell 5.1 は CP932 として読むため、引用符が飲み込まれて"
        " 構文エラーになりうる。日本語で説明したい場合はコメント行に書くこと。"
        + hint
    )
