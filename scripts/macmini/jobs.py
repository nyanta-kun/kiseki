#!/usr/bin/env python3
"""Mac mini の常時稼働ジョブ定義 — launchd plist の生成・導入・死活確認

ジョブの一覧・スケジュール・鮮度の閾値はこのファイルの JOBS だけが正本。
plist は手で書かず、ここから生成する（scripts/macmini/launchagents/）。

    python3 scripts/macmini/jobs.py generate          # plist を再生成
    python3 scripts/macmini/jobs.py check             # 生成物が JOBS と一致するか（CI/preflight 用）
    python3 scripts/macmini/jobs.py install           # 導入内容を表示するだけ（dry-run）
    python3 scripts/macmini/jobs.py install --apply   # ~/Library/LaunchAgents へ配置して bootstrap
    python3 scripts/macmini/jobs.py install --apply --skip-tag vm   # VM 依存ジョブを除く
    python3 scripts/macmini/jobs.py uninstall --apply
    python3 scripts/macmini/jobs.py status            # ハートビートの鮮度（異常があれば exit 1）

全ジョブは run_job.sh 経由で動く（PATH 明示・ログ・ハートビート・失敗通知）。
依存は標準ライブラリのみ（どの venv が壊れていても status が見られるように）。
"""

from __future__ import annotations

import argparse
import itertools
import os
import plistlib
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HOME = "/Users/ysuzuki"
KISEKI = f"{HOME}/GitHub/kiseki"
# 自動実行専用の clone（対話開発と混ざらない・netkeirin への入稿権限を持たない）
LAB = f"{HOME}/GitHub/kiseki-dev/keirin-lab"
RUN_JOB = f"{KISEKI}/scripts/macmini/run_job.sh"
LOG_DIR = f"{KISEKI}/logs/jobs"
STATE_DIR = Path(HOME) / ".local/state/jobs"
OUT_DIR = Path(__file__).resolve().parent / "launchagents"
LAUNCH_AGENTS = Path(HOME) / "Library/LaunchAgents"
LABEL_PREFIX = "com.kiseki."

H = 3600
D = 86400


@dataclass
class Job:
    name: str
    cmd: list[str]
    cron: list[str] = field(default_factory=list)  # "分 時 日 月 曜" (曜: 0=日)
    interval: int | None = None  # 秒。cron と排他
    cwd: str = KISEKI
    max_age: int = 26 * H  # これより古い .ok は異常
    tags: tuple[str, ...] = ()  # vm = Parallels / JV-Link 依存
    quiet: bool = False
    note: str = ""


JOBS: list[Job] = [
    # --- keirin ---
    Job(
        "keirin-weekly-retrain",
        [
            "/bin/bash",
            "-c",
            "scripts/weekly_retrain_wt.sh && scripts/sync_models_to_vps.sh",
        ],
        cron=["30 23 * * 0"],
        cwd=f"{KISEKI}/keirin",
        max_age=8 * D,
        note="日曜 23:30。再学習 → VPS へ rsync（昇格ゲートは未導入: 計画 7-3）",
    ),
    Job(
        "keirin-monthly-vintage",
        ["scripts/ensure_monthly_vintage.sh"],
        cron=["5 0 1 * *"],
        cwd=f"{KISEKI}/keirin",
        max_age=32 * D,
        note="月初に不足月の vintage モデルを学習して VPS へ配布",
    ),
    # 🔴 keirin-nightly-triage（毎日 00:12・夜間レポートを Claude に読ませて
    #    「所見」を Discord へ出す）は 2026-09-23 に停止した。半日レビュー
    #    （下記 keirin-lab-review-*）が同じ役割を担い、通知が二重になるため。
    #    ⚠️ VPS cron 00:10 の nightly_review.sh は**止めていない**。あちらは
    #    台帳（type_lab_nightly_ledger.csv）への追記・監査後の前向き観測・
    #    HTML 配布を行っており、半日レビューはそれらを持たない。
    Job(
        "keirin-lab-review-noon",
        ["/bin/bash", "-c",
         "cd keirin && .venv/bin/python scripts/lab_halfday_review.py --label 昼"],
        cron=["0 14 * * *"],
        cwd=LAB,
        max_age=26 * H,
        note="半日レビュー（昼）。予測オッズ誤差・確率較正・当落傾向を review ch へ",
    ),
    Job(
        "keirin-lab-review-night",
        ["/bin/bash", "-c",
         "cd keirin && .venv/bin/python scripts/lab_halfday_review.py --label 夜"],
        cron=["50 23 * * *"],
        cwd=LAB,
        max_age=26 * H,
        note=("半日レビュー（夜）。⚠️ 23:50 はミッドナイトの最終レース"
              "（23:20〜23:30 発走）の確定着順が入る前なので、当日最後の数レースは"
              "翌日の昼回で拾う。累積窓（90日）での解析なので影響は小さい"),
    ),
    # --- JRA（JV-Link / Windows VM 依存） ---
    Job(
        "dm-auto-fetch",
        ["scripts/dm_auto_fetch.sh"],
        cron=["0 12,14,18 * * *", "30 22 * * *", "0 8,11 * * 0,6"],
        max_age=15 * H,
        tags=("vm",),
        note="DM 指数自動収集。ssh windows-vm でパイプラインを叩く",
    ),
    Job(
        "prlctl-watchdog",
        ["scripts/macmini/prlctl_watchdog.sh"],
        interval=30,
        max_age=5 * 60,
        tags=("vm",),
        quiet=True,
        note="90 秒以上ハングした prlctl exec を kill",
    ),
    # --- JRA（スクレイプ・保守） ---
    Job(
        "scrape-projected",
        ["scripts/scrape_projected_entries.sh"],
        cron=["0 19 * * 3"],
        max_age=8 * D,
        note="水曜 19:00（MacBook の plist と照合済み 2026-09-23）。netkeiba の出走想定は水曜 20:00 までに出揃うため、取りこぼしがあれば時刻を見直す",
    ),
    Job(
        "scrape-special-jockeys",
        ["scripts/scrape_special_jockeys.sh"],
        cron=["30 18 * * *"],
        note="TOKU 取得(18:00)の 30 分後（MacBook の plist と照合済み 2026-09-23）",
    ),
    Job(
        "jra-odds-prune",
        ["scripts/prune_odds_history_weekly.sh"],
        cron=["0 5 * * 1"],
        max_age=8 * D,
    ),
    Job(
        "jra-quarterly-rollover",
        [f"{KISEKI}/backend/.venv/bin/python", "scripts/jra_quarterly_rollover.py"],
        cron=["20 3 1 1,4,7,10 *"],
        cwd=f"{KISEKI}/backend",
        max_age=93 * D,
    ),
    Job(
        "feature-health",
        ["scripts/feature_health_weekly.sh"],
        cron=["0 6 * * 1"],
        max_age=8 * D,
    ),
    # --- 地方 ---
    Job(
        "chihou-monthly-rollover",
        [f"{KISEKI}/backend/.venv/bin/python", "scripts/chihou_monthly_rollover.py"],
        cron=["18 3 1 * *"],
        cwd=f"{KISEKI}/backend",
        max_age=32 * D,
    ),
    # --- DB バックアップ ---
    Job(
        "db-backup",
        ["scripts/backup_hrdb.sh"],
        cron=["30 3 * * *"],
        note="VPS hrdb を pg_dump して ~/kiseki-backups へ。将来は VPS 側へ移す（計画 4-2）",
    ),
]


# ---------------------------------------------------------------- cron → launchd

_CRON_KEYS = ("Minute", "Hour", "Day", "Month", "Weekday")
_CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


def _parse_field(expr: str, lo: int, hi: int) -> list[int] | None:
    """cron の 1 フィールドを値の一覧に。'*' は None（launchd では省略＝任意）。"""
    if expr == "*":
        return None
    values: set[int] = set()
    for part in expr.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/")
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-"))
        else:
            a = b = int(part)
        if not (lo <= a <= b <= hi):
            raise ValueError(f"cron フィールドが範囲外: {expr}")
        values.update(range(a, b + 1, step))
    return sorted(values)


def cron_to_intervals(cron: str) -> list[dict[str, int]]:
    fields = cron.split()
    if len(fields) != 5:
        raise ValueError(f"cron は 5 フィールド: {cron!r}")
    parsed = [
        (k, _parse_field(f, *r)) for k, f, r in zip(_CRON_KEYS, fields, _CRON_RANGES)
    ]
    keys = [k for k, v in parsed if v is not None]
    lists = [v for _, v in parsed if v is not None]
    return [dict(zip(keys, combo)) for combo in itertools.product(*lists)]


# ---------------------------------------------------------------- plist


def label(job: Job) -> str:
    return LABEL_PREFIX + job.name


def build_plist(job: Job) -> dict:
    if bool(job.cron) == (job.interval is not None):
        raise ValueError(f"{job.name}: cron と interval はどちらか一方だけ指定する")
    d: dict = {
        "Label": label(job),
        "ProgramArguments": ["/bin/bash", RUN_JOB, job.name, *job.cmd],
        "WorkingDirectory": job.cwd,
        # run_job.sh 自体が起動できなかった場合だけここに出る
        "StandardOutPath": f"{LOG_DIR}/{job.name}.launchd.log",
        "StandardErrorPath": f"{LOG_DIR}/{job.name}.launchd.log",
        "RunAtLoad": False,
    }
    if job.quiet:
        d["EnvironmentVariables"] = {"RUN_JOB_QUIET": "1"}
    if job.interval is not None:
        d["StartInterval"] = job.interval
    else:
        intervals = [i for c in job.cron for i in cron_to_intervals(c)]
        d["StartCalendarInterval"] = intervals[0] if len(intervals) == 1 else intervals
    return d


def plist_bytes(job: Job) -> bytes:
    return plistlib.dumps(build_plist(job), sort_keys=True)


def plist_path(job: Job) -> Path:
    return OUT_DIR / f"{label(job)}.plist"


# ---------------------------------------------------------------- commands


def cmd_generate(_: argparse.Namespace) -> int:
    OUT_DIR.mkdir(exist_ok=True)
    wanted = {plist_path(j) for j in JOBS}
    for j in JOBS:
        plist_path(j).write_bytes(plist_bytes(j))
    for stale in set(OUT_DIR.glob("*.plist")) - wanted:
        stale.unlink()
        print(f"削除: {stale.name}")
    print(f"{len(JOBS)} 件を {OUT_DIR} に生成")
    return 0


def cmd_check(_: argparse.Namespace) -> int:
    bad = [
        j.name
        for j in JOBS
        if not plist_path(j).exists() or plist_path(j).read_bytes() != plist_bytes(j)
    ]
    extra = {p.name for p in OUT_DIR.glob("*.plist")} - {
        plist_path(j).name for j in JOBS
    }
    if bad or extra:
        print(
            "plist が JOBS と一致しません。`jobs.py generate` を実行してください:",
            *bad,
            *extra,
            sep="\n  ",
        )
        return 1
    print(f"OK: {len(JOBS)} 件の plist が JOBS と一致")
    return 0


def _select(args: argparse.Namespace) -> list[Job]:
    jobs = JOBS
    if args.only:
        unknown = set(args.only) - {j.name for j in JOBS}
        if unknown:
            sys.exit(f"不明なジョブ: {', '.join(sorted(unknown))}")
        jobs = [j for j in jobs if j.name in args.only]
    if args.skip_tag:
        jobs = [j for j in jobs if not set(j.tags) & set(args.skip_tag)]
    return jobs


def _run(cmd: list[str], apply: bool) -> None:
    print(("  $ " if apply else "  (dry-run) ") + " ".join(cmd))
    if apply:
        subprocess.run(cmd, check=False)


def cmd_install(args: argparse.Namespace) -> int:
    if cmd_check(args) != 0:
        return 1
    domain = f"gui/{os.getuid()}"
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    for j in _select(args):
        dst = LAUNCH_AGENTS / plist_path(j).name
        print(f"{j.name}:")
        _run(["launchctl", "bootout", f"{domain}/{label(j)}"], args.apply)
        _run(["cp", str(plist_path(j)), str(dst)], args.apply)
        _run(["launchctl", "bootstrap", domain, str(dst)], args.apply)
    if not args.apply:
        print("\n--apply を付けると実行します")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    domain = f"gui/{os.getuid()}"
    for j in _select(args):
        print(f"{j.name}:")
        _run(["launchctl", "bootout", f"{domain}/{label(j)}"], args.apply)
        _run(["rm", "-f", str(LAUNCH_AGENTS / plist_path(j).name)], args.apply)
    if not args.apply:
        print("\n--apply を付けると実行します")
    return 0


def _fmt_age(sec: float) -> str:
    if sec < H:
        return f"{int(sec // 60)}分前"
    if sec < 2 * D:
        return f"{sec / H:.1f}時間前"
    return f"{sec / D:.1f}日前"


def cmd_status(args: argparse.Namespace) -> int:
    now = time.time()
    problems = 0
    print(f"{'ジョブ':<26}{'状態':<8}{'最終成功':<14}詳細")
    for j in _select(args):
        ok, fail = STATE_DIR / f"{j.name}.ok", STATE_DIR / f"{j.name}.fail"
        loaded = (LAUNCH_AGENTS / plist_path(j).name).exists()
        if fail.exists():
            state, detail = "FAIL", fail.read_text().strip().replace("\t", " ")
        elif not ok.exists():
            state, detail = "NEVER", "" if loaded else "(未導入)"
        elif now - ok.stat().st_mtime > j.max_age:
            state, detail = "STALE", f"閾値 {_fmt_age(j.max_age).replace('前', '')}"
        else:
            state, detail = "OK", ok.read_text().strip().split("\t")[-1]
        age = _fmt_age(now - ok.stat().st_mtime) if ok.exists() else "-"
        if state != "OK" and (loaded or state == "FAIL"):
            problems += 1
        print(f"{j.name:<26}{state:<8}{age:<14}{detail}")
    return 1 if problems else 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate").set_defaults(fn=cmd_generate)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    for name, fn in (
        ("install", cmd_install),
        ("uninstall", cmd_uninstall),
        ("status", cmd_status),
    ):
        sp = sub.add_parser(name)
        sp.add_argument("--only", nargs="+", metavar="JOB")
        sp.add_argument("--skip-tag", nargs="+", metavar="TAG")
        if name != "status":
            sp.add_argument("--apply", action="store_true")
        sp.set_defaults(fn=fn)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
