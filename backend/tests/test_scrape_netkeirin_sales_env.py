"""`scripts/scrape_netkeirin_sales.sh` の `.env` の読み方（2026-09-25）。

🔴 2026-09-23 にパスワードを更新した際、値が `"…"` で囲まれた。`cut` だけで読んでいた
   ため引用符ごとパスワードとして送り、9/23〜9/25 の3日間ログインに失敗し続けた
   （入稿側は python-dotenv で読むので無事で、売上の取り込みだけが止まった）。
   さらに失敗が rc=1 としてログに残るだけで、**誰にも通知されていなかった**。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_SHELL = Path(__file__).resolve().parents[2] / "scripts" / "scrape_netkeirin_sales.sh"


def _env_get(tmp_path: Path, line: str, key: str) -> str:
    src = _SHELL.read_text(encoding="utf-8")
    m = re.search(r"^env_get\(\) \{.*?^\}", src, re.DOTALL | re.MULTILINE)
    assert m, "env_get を読めない"
    env = tmp_path / ".env"
    env.write_text(f"OTHER=x\n{line}\n", encoding="utf-8")
    out = subprocess.run(["bash", "-c", f'{m.group(0)}\nenv_get {key} "{env}"'],
                         capture_output=True, text=True, check=True)
    return out.stdout


def test_二重引用符を外す(tmp_path):
    assert _env_get(tmp_path, 'NETKEIRIN_PASSWORD="ab#c$d"', "NETKEIRIN_PASSWORD") == "ab#c$d"


def test_一重引用符を外す(tmp_path):
    assert _env_get(tmp_path, "NETKEIRIN_PASSWORD='ab\"cd'", "NETKEIRIN_PASSWORD") == 'ab"cd'


def test_引用符なしはそのまま(tmp_path):
    assert _env_get(tmp_path, "NETKEIRIN_PASSWORD=abc=d", "NETKEIRIN_PASSWORD") == "abc=d"


def test_途中の引用符は消さない(tmp_path):
    assert _env_get(tmp_path, 'NETKEIRIN_PASSWORD=a"b"c', "NETKEIRIN_PASSWORD") == 'a"b"c'


def test_片側だけの引用符は外さない(tmp_path):
    assert _env_get(tmp_path, 'NETKEIRIN_PASSWORD="abc', "NETKEIRIN_PASSWORD") == '"abc'


def test_資格情報は必ずenv_get経由で読む():
    """`cut` 直読みに戻すと同じ事故が再発する。"""
    src = _SHELL.read_text(encoding="utf-8")
    for key in ("NETKEIRIN_LOGIN_ID", "NETKEIRIN_PASSWORD", "DISCORD_WEBHOOK_URL_NETKEIRIN"):
        assert f"{key}=$(env_get {key} " in src, key
    assert "cut -d= -f2-" not in src.replace(
        re.search(r"^env_get\(\) \{.*?^\}", src, re.DOTALL | re.MULTILINE).group(0), "")


def test_失敗をシステム障害へ通知する():
    src = _SHELL.read_text(encoding="utf-8")
    assert "DISCORD_WEBHOOK_URL_SYSTEM" in src
    tail = src[src.index('log "=== 終了 (rc=$RC) ==="'):]
    assert 'if [ "$RC" -ne 0 ]; then' in tail and "notify_failure" in tail
