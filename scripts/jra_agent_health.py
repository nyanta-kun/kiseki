#!/usr/bin/env python3
"""Windows エージェントの健康診断（Mac から実行・読み取り専用）。

## なぜ要るか

**スケジュールタスクの rc は全機能で無意味**。全 VBS が `WshShell.Run(..., 0, False)` で
pythonw を非同期起動して即 return するため、rc は Python の成否を一切反映しない。
実際 `kiseki-JVLink-BldnFull` は 6 時間ハングして強制終了された晩も **rc=0** だった。
`bldn_full.log` / `chokyo.log` / `backfill.log` も「started」しか書かない。
その結果、以下が誰にも気づかれず放置されていた（2026-08-23 に発見）:

  - `kiseki-JVLink-BldnFull` が 8/17 から毎晩 6 時間ハングし完走ゼロ
  - `kiseki-Historical` が Disabled のまま 8/8 から停止 → 新規馬の血統が入らず、
    8/23 の出走 468 頭中 54 頭（11.5%）が血統情報ゼロで評価されていた
  - `kiseki-Chokyo-Setup` が存在しない引数 `--setup` で登録されており実行すれば即死
  - `kiseki-DM-Import` はトリガー失効済みなのに機能は別経路で生きている

そこで **rc を信用せず、「データが実際に新しいか」と「プロセス・ログの実態」から**判定する。

## 使い方

    python scripts/jra_agent_health.py            # 表形式で全項目
    python scripts/jra_agent_health.py --json     # 機械可読
    python scripts/jra_agent_health.py --no-vm    # DB のみ（VM に繋がらない環境用）

終了コード: 0=全て OK / 1=警告あり / 2=異常あり
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

SSH_HOST = os.getenv("WINDOWS_VM_SSH", "windows-vm")
PS_PREFIX = "$OutputEncoding=[Console]::OutputEncoding=[Text.Encoding]::UTF8; "

OK, WARN, NG = "OK", "WARN", "NG"


def _ssh(ps: str, timeout: int = 30) -> str:
    """VM で PowerShell を実行して stdout を返す。失敗時は空文字。"""
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", SSH_HOST, f'powershell -Command "{PS_PREFIX}{ps}"'],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def _db_rows(sql: str) -> list[tuple]:
    import psycopg2

    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"),
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# データ鮮度チェック（rc を信用せず、入っているものを見る）
# ---------------------------------------------------------------------------
# (ラベル, SQL, 許容遅延日数, 補足)
FRESHNESS = [
    ("レース結果", "SELECT max(created_at)::date FROM keiba.race_results", 4,
     "realtime 0B12 / RACE"),
    ("払戻", "SELECT max(created_at)::date FROM keiba.race_payouts", 4, "HR"),
    ("坂路調教", "SELECT max(created_at)::date FROM keiba.slope_training", 3, "SLOP option=1"),
    ("ウッド調教", "SELECT max(created_at)::date FROM keiba.wood_training", 3, "WOOD option=1"),
    ("競走馬マスタ", "SELECT max(created_at)::date FROM keiba.horses", 7, "DIFN option=1"),
    ("出馬表(projected)", "SELECT max(created_at)::date FROM keiba.projected_entries", 7, "0B15"),
]


def check_freshness() -> list[dict]:
    out = []
    today = date.today()
    for label, sql, tol, note in FRESHNESS:
        try:
            v = _db_rows(sql)[0][0]
        except Exception as e:  # noqa: BLE001
            out.append({"項目": label, "状態": NG, "値": f"照会失敗: {e}", "備考": note})
            continue
        if v is None:
            out.append({"項目": label, "状態": NG, "値": "データなし", "備考": note})
            continue
        lag = (today - v).days
        st = OK if lag <= tol else (WARN if lag <= tol * 3 else NG)
        out.append({"項目": label, "状態": st, "値": f"{v} ({lag}日前)", "備考": note})
    return out


def check_pedigree_gap() -> dict:
    """直近の出走馬で血統情報が無い割合。静かに劣化する典型なので単独で見る。"""
    sql = """
        SELECT count(DISTINCT re.horse_id),
               count(DISTINCT re.horse_id) FILTER (WHERE p.horse_id IS NULL)
        FROM keiba.races r
        JOIN keiba.race_entries re ON re.race_id = r.id
        LEFT JOIN keiba.pedigrees p ON p.horse_id = re.horse_id
        WHERE r.course <= '10' AND r.date >= to_char(now() - interval '7 days', 'YYYYMMDD')
    """
    try:
        total, missing = _db_rows(sql)[0]
    except Exception as e:  # noqa: BLE001
        return {"項目": "血統の欠落(直近7日)", "状態": NG, "値": f"照会失敗: {e}", "備考": ""}
    if not total:
        return {"項目": "血統の欠落(直近7日)", "状態": WARN, "値": "出走なし", "備考": ""}
    pct = 100.0 * missing / total
    st = OK if pct < 3 else (WARN if pct < 10 else NG)
    return {"項目": "血統の欠落(直近7日)", "状態": st,
            "値": f"{missing}/{total} 頭 ({pct:.1f}%)",
            "備考": "pedigree_index は sire 不明時レース平均へフォールバックする"}


def check_exotic_odds() -> dict:
    """エキゾチックオッズの組番が壊れていないか（11バイトずれの再発検知）。"""
    sql = """
        SELECT count(*),
               count(*) FILTER (
                   WHERE (SELECT bool_and(p::int BETWEEN 1 AND r.registered_count)
                          FROM unnest(string_to_array(oh.combination,'-')) p
                          WHERE p ~ '^[0-9]+$'))
        FROM keiba.odds_history oh
        JOIN keiba.races r ON r.id = oh.race_id
        WHERE oh.bet_type = 'trio' AND r.registered_count IS NOT NULL
          AND r.date >= to_char(now() - interval '30 days', 'YYYYMMDD')
    """
    try:
        total, valid = _db_rows(sql)[0]
    except Exception as e:  # noqa: BLE001
        return {"項目": "三連複オッズの整合", "状態": NG, "値": f"照会失敗: {e}", "備考": ""}
    if not total:
        return {"項目": "三連複オッズの整合", "状態": WARN, "値": "データなし",
                "備考": "確定オッズは施行の約2日後に配信される"}
    pct = 100.0 * valid / total
    st = OK if pct >= 99 else (WARN if pct >= 90 else NG)
    return {"項目": "三連複オッズの整合", "状態": st,
            "値": f"馬番が登録頭数以内 {pct:.2f}%",
            "備考": "低いとパースのオフセットずれ（2026-08-23 の 11バイトずれ再発）"}


# ---------------------------------------------------------------------------
# VM 側チェック
# ---------------------------------------------------------------------------
def check_tasks() -> list[dict]:
    """kiseki-* タスクの状態。⚠️ rc は信用しない（非同期起動のため常に 0）。"""
    raw = _ssh(
        "schtasks /query /fo csv /v | ConvertFrom-Csv | "
        "Where-Object { $_.TaskName -like '*kiseki-*' } | "
        "ForEach-Object { $_.TaskName + '|' + $_.'Scheduled Task State' + '|' + $_.'Last Run Time' }"
    )
    out = []
    seen: set[str] = set()
    for line in raw.splitlines():
        parts = line.strip().split("|")
        if len(parts) < 3:
            continue
        name, state, last = parts[0].split("\\")[-1], parts[1], parts[2]
        # schtasks はトリガーごとに1行返すので、タスク名で重複を潰す
        if name in seen:
            continue
        seen.add(name)
        st = OK
        note = ""
        if state.lower() in ("disabled", "無効"):
            st = NG
            note = "無効化されたまま"
        elif "1999" in last or "N/A" in last:
            st = WARN
            note = "一度も実行されていない"
        out.append({"項目": f"task {name}", "状態": st, "値": f"{state} / 最終 {last}",
                    "備考": note})
    if not out:
        out.append({"項目": "task 一覧", "状態": WARN, "値": "取得できず",
                    "備考": "VM に到達できないか schtasks の出力形式が変わった"})
    return out


def check_processes() -> dict:
    raw = _ssh(
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pythonw.exe' } | "
        "ForEach-Object { $_.CommandLine }"
    )
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return {"項目": "稼働中の pythonw", "状態": OK, "値": "なし", "備考": ""}
    modes = [ln.split("jvlink_agent.py")[-1].strip() if "jvlink_agent.py" in ln else ln[-60:]
             for ln in lines]
    return {"項目": "稼働中の pythonw", "状態": OK, "値": f"{len(lines)} 本: " + " / ".join(modes),
            "備考": "JV-Link は蓄積系(JVOpen)が排他。長時間居座っていないか見ること"}


def check_jvopen_hang() -> dict:
    """JVOpen が長時間返っていないか（ハートビート行の経過秒を見る）。"""
    raw = _ssh(
        r"Get-Content C:\kiseki\windows-agent\jvlink_agent.log -Tail 400 | "
        r"Select-String -Pattern 'JVOpen' | Select-Object -Last 1 | ForEach-Object { $_.Line }"
    )
    if not raw:
        return {"項目": "JVOpen のハング", "状態": WARN, "値": "ログを読めず", "備考": ""}
    import re

    m = re.search(r"=(\d+)", raw)
    if not m:
        return {"項目": "JVOpen のハング", "状態": OK, "値": "待機行なし", "備考": ""}
    sec = int(m.group(1))
    st = OK if sec < 600 else (WARN if sec < 1800 else NG)
    return {"項目": "JVOpen のハング", "状態": st, "値": f"直近の待機 {sec}秒",
            "備考": "600秒超は不可視ダイアログを疑う（jvlink_dialog_guard のログを見る）"}


def main() -> None:
    ap = argparse.ArgumentParser(description="Windows エージェントの健康診断")
    ap.add_argument("--json", action="store_true", help="JSON で出す")
    ap.add_argument("--no-vm", action="store_true", help="VM に繋がず DB だけ見る")
    args = ap.parse_args()

    rows: list[dict] = []
    rows += check_freshness()
    rows.append(check_pedigree_gap())
    rows.append(check_exotic_odds())
    if not args.no_vm:
        rows.append(check_processes())
        rows.append(check_jvopen_hang())
        rows += check_tasks()

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        w1 = max(len(r["項目"]) for r in rows) + 2
        w2 = max(len(str(r["値"])) for r in rows) + 2
        print(f"{'項目':<{w1}} {'状態':<6} {'値':<{w2}} 備考")
        print("-" * (w1 + w2 + 40))
        for r in rows:
            mark = {"OK": "  OK", "WARN": "WARN", "NG": "  NG"}[r["状態"]]
            print(f"{r['項目']:<{w1}} {mark:<6} {str(r['値']):<{w2}} {r['備考']}")

    worst = max((r["状態"] for r in rows), key=lambda s: {"OK": 0, "WARN": 1, "NG": 2}[s])
    sys.exit({"OK": 0, "WARN": 1, "NG": 2}[worst])


if __name__ == "__main__":
    main()
