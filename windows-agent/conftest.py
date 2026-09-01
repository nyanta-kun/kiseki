"""windows-agent 直下の `test_*.py` を pytest の収集対象から外す。

## なぜ必要か（2026-09-01 実測）

`windows-agent` 直下には `test_1r_odds.py` / `test_odds_jvopen.py` /
`test_jvopen_dm.py` など **25 本**の `test_*.py` があるが、これらは実体が
JV-Link / UmaConn の**手動プローブ**で `def test_` を1つも持たない
（実テストは `tests/` 配下だけ）。

命名が pytest の自動収集パターンと衝突するうえ、一部は import 時に
`win32com` 不在で `sys.exit(1)` を呼ぶため、`pytest .` や
`pytest windows-agent/` と広く指定すると **収集段階で INTERNALERROR になり
1件もテストが走らない**（実測: `no tests collected, 10 errors`）。

`pytest.ini` の `testpaths` は引数なしのときにしか効かないので、
明示的にパスを指定された場合はここで落とす。

⚠️ ファイル名を `probe_*.py` へ改名する案もあったが、CLAUDE.md と
   トラブルシューティング手順が実名で参照しているため見送った
   （参照を壊すだけで実利がない。`exp_*` の一括アーカイブを見送ったのと同じ判断）。
⚠️ 直下に**本物のテスト**を置きたくなったら `tests/` へ置くこと。
   ここへ置いても収集されない。
"""

from pathlib import Path

# conftest.py からの相対パスで指定する。tests/ 配下は対象にならない。
collect_ignore = [p.name for p in Path(__file__).parent.glob("test_*.py")]
