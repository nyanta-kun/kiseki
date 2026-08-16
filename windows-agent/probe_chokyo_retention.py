r"""調教（SLOP/WOOD）を何年前まで遡って取得できるかを実測する診断スクリプト。

## なぜ要るか

`keiba.slope_training` / `wood_training` は **2025-05-27 以降しかない**。
調教はキャリア0-2走（出走の 27.7%）で唯一生きている入力で、そこに効果が集中する
（memory: jra_thin_career_needs_chokyo_2026_08_16）。だが評価窓が 4 四半期しか
取れず、+1.31pt [+0.15, +2.46] を確定できない。

2023-05 まで遡れれば **データ 2.67倍 / 評価窓 3.0倍**、信頼区間はおよそ 0.58倍に縮む。

## 何が分かっていないか

- 過去に「3年全期間 backfill」を 2 回試して両方失敗している
  （`JVOpen option=4` セットアップが数日ハング・docs/jra_new_index_results.md 4-B）
- 一方 **仕様上 SLOP/WOOD は option=1 で `from_time` が効く**
  （docs/JV-Link_Interface_Spec.md の「読み出し終了ポイント時刻を指定できない
  データ種別」に SLOP/WOOD は入っていない）
- 残る未知は **JRA-VAN が通常データ(option=1)の差分をどこまで遡って保持しているか**

これは JVOpen の戻り値（読込ファイル数・DL数）だけで分かる。**JVRead を回さない**ので
ダウンロードも DB 反映も発生しない。

## 使い方

    ssh windows-vm 'powershell -NoProfile -Command "Set-Content -Path \"C:\kiseki\windows-agent\adhoc_cmd.txt\" -Value \"probe_chokyo_retention.py\" -Encoding ASCII"'
    ssh windows-vm 'schtasks /run /tn kiseki-RunAdhoc'

出力: C:\kiseki\windows-agent\probe_chokyo_retention.log

🔴 **開催中に実行しないこと。** JVOpen は数十分ブロックしうる。realtime が
走っていれば既定で拒否する（`--force` で上書き可）。UmaConn の夜間 backfill と
同じ 23:50〜08:30 の窓が安全。
"""

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path

from jvlink_agent import init_jvlink
from link_common import BlockingCallGuard

# ⚠️ `logging.basicConfig()` を使ってはいけない。`jvlink_agent` を import した時点で
# root logger にハンドラが付くため basicConfig は **no-op** になり、出力が
# jvlink_agent.log へ流れる（2026-08-16 の初回実行で実際にそうなり、
# 自分のログファイルは 0 バイトのまま作られた）。専用ロガーを直接組む。
_log_path = Path(__file__).resolve().parent / "probe_chokyo_retention.log"
logger = logging.getLogger("probe_chokyo_retention")
logger.setLevel(logging.INFO)
logger.propagate = False
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
for _h in (logging.FileHandler(str(_log_path), encoding="utf-8", mode="w"),
           logging.StreamHandler()):
    _h.setFormatter(_fmt)
    logger.addHandler(_h)

# 遡り候補。粗く刻んで「どこで 0 件になるか」を挟み込む。
DEFAULT_FROMS = [
    "20260701000000",  # 直近（対照。必ず取れるはず）
    "20260101000000",
    "20250527000000",  # 現在の DB 開始日
    "20250101000000",
    "20240101000000",
    "20230506000000",  # 学習データの開始日＝目標
]


def _realtime_running() -> bool:
    """同一 PC で realtime が動いているか（COM を奪わないための保険）。"""
    try:
        import subprocess
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pythonw.exe' -and "
             "$_.CommandLine -match 'mode realtime' } | Measure-Object).Count"],
            capture_output=True, text=True, timeout=60,
        )
        return int((out.stdout or "0").strip() or 0) > 0
    except Exception as e:  # noqa: BLE001 - 判定できないときは安全側（動いている扱い）
        logger.warning(f"realtime 判定に失敗: {e} → 動いている扱いにします")
        return True


def probe_once(jv, dataspec: str, from_time: str, option: int, timeout: int) -> dict:
    """JVOpen を1回だけ呼び、戻り値を記録して即 JVClose する。JVRead は回さない。

    COM 呼び出しは中断できないので、ワーカースレッドに投げて本体はタイムアウトで
    見切る（回収はプロセス終了に委ねる）。
    """
    box: dict = {}

    def worker() -> None:
        t0 = time.monotonic()
        try:
            # 本番の取得経路と同じガードに載せる。ハングの正体が
            # 「JV-Link のモーダルダイアログ待ち」であることが実際にあり、
            # そのときは殺すのではなく押せば通る（jvlink_dialog_guard 参照）。
            # timeout=0 にして強制終了はこの関数側の join に任せる。
            with BlockingCallGuard(f"JVOpen({dataspec},opt={option})", 0, logger):
                box["result"] = jv.JVOpen(dataspec, from_time, option, 0, 0, "")
        except Exception as e:  # noqa: BLE001 - COM 例外もそのまま記録する
            box["error"] = repr(e)
        box["elapsed"] = time.monotonic() - t0

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    th.join(timeout)

    rec = {"dataspec": dataspec, "from": from_time, "option": option}
    if th.is_alive():
        rec["verdict"] = f"TIMEOUT(>{timeout}s)"
        return rec
    rec["elapsed"] = round(box.get("elapsed", 0.0), 1)
    if "error" in box:
        rec["verdict"] = f"EXCEPTION {box['error']}"
        return rec
    r = box["result"]
    if isinstance(r, tuple):
        rec["rc"] = r[0]
        rec["files"] = r[1] if len(r) > 1 else None
        rec["dl"] = r[2] if len(r) > 2 else None
        rec["last_ts"] = r[3] if len(r) > 3 else None
    else:
        rec["rc"] = r
    try:
        jv.JVClose()
    except Exception:  # noqa: BLE001 - 既に閉じている場合がある
        pass
    return rec


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataspecs", default="SLOP,WOOD")
    p.add_argument("--froms", default=",".join(DEFAULT_FROMS))
    p.add_argument("--option", type=int, default=1,
                   help="1=通常データ（既定・from_time が効く）/ 4=セットアップ")
    p.add_argument("--timeout", type=int, default=300,
                   help="1回あたりの JVOpen 待ち上限(秒)")
    p.add_argument("--force", action="store_true",
                   help="realtime が動いていても実行する")
    args = p.parse_args()

    if not args.force and _realtime_running():
        logger.error(
            "realtime が動いています。JVOpen は数十分ブロックしうるため中止します。"
            "開催終了後（23:50〜08:30 が安全）に実行するか --force を付けてください。"
        )
        sys.exit(2)

    logger.info(f"=== 調教リテンション調査 option={args.option} ===")
    jv = init_jvlink()
    rows = []
    try:
        for ds in args.dataspecs.split(","):
            for ft in args.froms.split(","):
                rec = probe_once(jv, ds.strip(), ft.strip(), args.option, args.timeout)
                rows.append(rec)
                logger.info(f"  {rec}")
                if rec.get("verdict", "").startswith("TIMEOUT"):
                    # COM 呼び出しは中断できない。ワーカーを抱えたまま生き残ると
                    # JV-Link を掴んだままの pythonw が残り、次の realtime を壊す。
                    # ここは通常終了に頼らず強制終了する（CLAUDE.md の居座り事例と同型）。
                    for r in rows:
                        logger.error(f"  (timeout前の結果) {r}")
                    logger.error("タイムアウト。JV-Link を掴んだまま残らないよう強制終了します")
                    for h in logger.handlers:
                        h.flush()
                    os._exit(1)
    finally:
        try:
            jv.JVClose()
        except Exception:  # noqa: BLE001
            pass

    logger.info("=== まとめ ===")
    for r in rows:
        logger.info(
            f"  {r['dataspec']:5s} from={r['from']} rc={r.get('rc')} "
            f"files={r.get('files')} dl={r.get('dl')} "
            f"{r.get('verdict', '')} ({r.get('elapsed', '?')}s)"
        )
    logger.info(
        "読み方: rc>=0 かつ files>0 なら**その日付まで遡れる**。"
        "rc=-1 は該当データなし（保持期間外）。"
    )


if __name__ == "__main__":
    main()
