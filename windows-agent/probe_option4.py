"""JV-Link 5.0.0 で option=3/4（セットアップ）が返らない件の切り分けプローブ。

2026-08-17 以降、`JVOpen(BLDN, option=4)` が返らない（6時間ブロック → 強制終了）。
option=1 は正常。5.0.0 導入は 2026-08-12、最後の option=4 成功は 2026-04-20。

このプローブは:
  1. 指定した dataspec / option で JVOpen を呼ぶ
  2. **必ず --timeout 秒で os._exit する**（6時間ブロックを繰り返さない）
  3. ブロック中は 5 秒ごとに「自プロセスが持つ #32770 を全部」記録する
     （タイトルで弾かず、所有 PID・可視状態・ボタン・本文まで残す）

使い方（RunAdhoc 経由。SSH からの直接起動はデスクトップが取れないので不可）:
    probe_option4.py --dataspec TOKU --option 4 --timeout 180
    probe_option4.py --dataspec BLDN --option 4 --timeout 300
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

BASE_DIR = Path(__file__).parent
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BASE_DIR / "probe_option4.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("probe_option4")

user32 = ctypes.WinDLL("user32", use_last_error=True)
DIALOG_CLASS = "#32770"


def snapshot_dialogs(only_self: bool) -> None:
    """トップレベルの #32770 を、タイトルで弾かずに全部記録する。"""
    self_pid = os.getpid()
    found: list[int] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lp):  # noqa: ANN001
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value == DIALOG_CLASS:
            found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    if not found:
        return

    for hwnd in found:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if only_self and pid.value != self_pid:
            continue
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        visible = bool(user32.IsWindowVisible(hwnd))
        buttons: list[str] = []
        texts: list[str] = []

        def collect(child, _lp):  # noqa: ANN001
            c = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(child, c, 256)
            t = ctypes.create_unicode_buffer(2048)
            user32.GetWindowTextW(child, t, 2048)
            if t.value:
                (buttons if c.value == "Button" else texts).append(t.value)
            return True

        user32.EnumChildWindows(hwnd, WNDENUMPROC(collect), 0)
        log.warning(
            f"[dialog] pid={pid.value} self={self_pid} visible={visible} "
            f"title={title.value!r} buttons={buttons} text={' / '.join(texts)!r}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="JVOpen option=3/4 ブロックの切り分け")
    ap.add_argument("--dataspec", default="TOKU", help="TOKU が最小。BLDN は本番相当")
    ap.add_argument("--option", type=int, default=4, choices=[1, 2, 3, 4])
    ap.add_argument("--fromtime", default="20260101000000")
    ap.add_argument("--timeout", type=int, default=180, help="この秒数で必ず打ち切る")
    ap.add_argument("--all-dialogs", action="store_true",
                    help="自プロセス以外の #32770 も記録する")
    ap.add_argument("--dismiss", action="store_true",
                    help="jvlink_dialog_guard.dismiss() でダイアログに自動応答する")
    args = ap.parse_args()

    import win32com.client

    sid = ""
    for env in (BASE_DIR / ".env", BASE_DIR.parent / ".env"):
        if not env.exists():
            continue
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("JRAVAN_SID") and "=" in line:
                sid = line.split("=", 1)[1].strip()
        if sid:
            break

    jv = win32com.client.Dispatch("JVDTLab.JVLink")
    rc = jv.JVInit(sid or "UNKNOWN")
    log.info(f"JVInit rc={rc} version={getattr(jv, 'm_JVLinkVersion', '?')} "
             f"savepath={getattr(jv, 'm_savepath', '?')!r} payflag={getattr(jv, 'm_payflag', '?')}")
    if rc != 0:
        log.error("JVInit 失敗。中止")
        return

    done = threading.Event()

    def watcher() -> None:
        start = time.time()
        while not done.wait(timeout=5):
            elapsed = int(time.time() - start)
            snapshot_dialogs(only_self=not args.all_dialogs)
            if args.dismiss:
                try:
                    import jvlink_dialog_guard  # noqa: PLC0415

                    jvlink_dialog_guard.dismiss(log)
                except Exception as e:  # noqa: BLE001
                    log.warning(f"dismiss 失敗: {e!r}")
            if elapsed % 30 == 0:
                log.info(f"  JVOpen 待機中... 経過={elapsed}秒")
            if elapsed >= args.timeout:
                log.error(
                    f"[TIMEOUT] JVOpen が {elapsed}秒 返りません "
                    f"(dataspec={args.dataspec} option={args.option})。強制終了します"
                )
                snapshot_dialogs(only_self=False)  # 最後は他プロセスのも記録
                os._exit(2)

    threading.Thread(target=watcher, daemon=True).start()

    log.info(f"JVOpen({args.dataspec}, {args.fromtime}, option={args.option}) 呼び出し...")
    t0 = time.time()
    result = jv.JVOpen(args.dataspec, args.fromtime, args.option, 0, 0, "")
    done.set()
    took = time.time() - t0

    if isinstance(result, tuple):
        log.info(f"[OK] rc={result[0]} readcount={result[1]} downloadcount={result[2]} "
                 f"lastfiletimestamp={result[3] if len(result) > 3 else '?'} 所要={took:.1f}秒")
    else:
        log.info(f"[OK] rc={result} 所要={took:.1f}秒")

    try:
        jv.JVClose()
    except Exception:
        pass
    log.info("done")


if __name__ == "__main__":
    main()
