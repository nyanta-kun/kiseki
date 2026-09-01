"""柱(pillar)判定の分類漏れを機械的に禁止する。

なぜ必要か（2026-09-01 の実測）:
    `scripts/dev/pillars.sh` の ``pillar_of`` は判定の唯一の情報源だが、
    ``backend/scripts/*`` (118件) と ``frontend/*`` (104件) を拾う分岐が無く、
    追跡ファイル 315 件が ``other`` に落ちていた。
    ``check_ownership.sh`` は ``other`` を柱の集計から除外するため、
    **これらは shared 警告にも複数柱警告にも一切かからなかった**——
    つまり並列開発ガードが最も効いてほしいコード領域で効いていなかった。

    分類漏れは「エラーにならず、ただ警告が出なくなる」形で壊れるので、
    人が気づけない。ここで機械的に固定する。

守る不変条件:
    1. コードとして扱うべき領域のファイルは ``other`` に落ちない。
    2. 代表的なパスの所属が変わらない（特に shared の境界）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PILLARS_SH = REPO_ROOT / "scripts" / "dev" / "pillars.sh"

# other に落ちてはいけない領域。ここに入るのは「編集すると動作が変わるもの」。
# docs/ や inputs/ や引き継ぎメモは衝突の原因にならないので対象外。
CODE_PREFIXES = (
    "backend/src/",
    "backend/scripts/",
    "backend/tests/",
    "backend/alembic/",
    "frontend/src/",
    "keirin/src/",
    "keirin/scripts/",
    "keirin/tests/",
    "windows-agent/",
    "scripts/",
)
CODE_SUFFIXES = (".py", ".ts", ".tsx", ".sh", ".vbs", ".ps1", ".bat", ".sql", ".plist")


def _classify(paths: list[str]) -> dict[str, str]:
    """pillars.sh を1回だけ起動して全パスを分類する（1件1プロセスだと遅い）。"""
    # ⚠️ `read` は末尾に改行が無い最終行を落とす。入力の末尾に必ず改行を足すこと
    #    （足さないと最後の1件が黙って分類されず、テストが偽陰性になる）。
    script = f'source "{PILLARS_SH}"\nwhile IFS= read -r f; do printf "%s\\t%s\\n" "$(pillar_of "$f")" "$f"; done'
    proc = subprocess.run(
        ["bash", "-c", script],
        input="\n".join(paths) + "\n",
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    )
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "\t" in line:
            pillar, path = line.split("\t", 1)
            out[path] = pillar
    return out


def _tracked_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    )
    return [p for p in proc.stdout.split("\0") if p]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # --- shared: 触ると全柱に波及する ---
        ("backend/src/db/models.py", "shared"),
        ("backend/alembic/env.py", "shared"),
        ("backend/src/indices/composite.py", "shared"),
        ("backend/src/realtime/__init__.py", "shared"),
        ("backend/src/bet_types.py", "shared"),
        ("scripts/dev/pillars.sh", "shared"),
        ("scripts/deploy-bluegreen.sh", "shared"),
        ("scripts/backup_hrdb.sh", "shared"),
        ("scripts/setup_schema.py", "shared"),
        (".github/workflows/ci.yml", "shared"),
        # CLAUDE.md と AGENTS.md は対で編集される写し関係。
        # 片方だけ shared だと対の変更が警告をすり抜ける。
        ("CLAUDE.md", "shared"),
        ("AGENTS.md", "shared"),
        # フロントの横断部分（認証・共通ナビ・API 型定義）
        ("frontend/src/lib/api.ts", "shared"),
        ("frontend/src/app/layout.tsx", "shared"),
        ("frontend/src/components/AppNav.tsx", "shared"),
        ("frontend/src/proxy.ts", "shared"),
        # --- 柱ごと ---
        ("backend/src/api/keirin_router.py", "keirin"),
        ("keirin/src/type_lab.py", "keirin"),
        ("frontend/src/app/keirin/page.tsx", "keirin"),
        ("backend/src/api/chihou_races_router.py", "chihou"),
        ("backend/scripts/chihou_monthly_rollover.py", "chihou"),
        ("frontend/src/app/chihou/races/page.tsx", "chihou"),
        ("backend/src/api/races.py", "jra"),
        ("backend/src/indices/confidence.py", "jra"),
        ("backend/scripts/inference_v27.py", "jra"),
        ("windows-agent/jvlink_agent.py", "jra"),
        ("frontend/src/components/RaceCard.tsx", "jra"),
        # --- other は「コード以外」だけ ---
        ("README.md", "other"),
        ("HANDOFF_2026-08-31.md", "other"),
    ],
)
def test_representative_paths_keep_their_pillar(path: str, expected: str) -> None:
    assert _classify([path])[path] == expected, (
        f"{path} の柱判定が {expected} から変わりました。"
        " scripts/dev/pillars.sh の case の順序（shared → キーワード → jra）を確認してください。"
    )


def test_no_code_file_falls_through_to_other() -> None:
    """コード領域のファイルが other に落ちていないこと。

    other は check_ownership.sh の柱集計から除外される＝警告が一切出なくなる。
    ここに落ちたコードは並列開発ガードの外側に出る。
    """
    targets = [
        p
        for p in _tracked_files()
        if p.startswith(CODE_PREFIXES) and p.endswith(CODE_SUFFIXES)
    ]
    assert targets, "対象ファイルが1件も取れていません（git ls-files の失敗を疑う）"

    classified = _classify(targets)
    orphans = sorted(p for p, pillar in classified.items() if pillar == "other")

    assert not orphans, (
        f"柱に分類されないコードが {len(orphans)} 件あります"
        f"（check_ownership.sh の警告が効きません）:\n  "
        + "\n  ".join(orphans[:20])
        + ("\n  ..." if len(orphans) > 20 else "")
    )
