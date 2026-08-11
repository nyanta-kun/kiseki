"""jvlink_dialog_guard の Windows API 部分を実機で検証する自己テスト。

``tests/test_jvlink_dialog_guard.py`` は判断部分（どのボタンを選ぶか）しか見ない。
CI が Linux で回るため EnumWindows / EnumChildWindows / BM_CLICK は検査できず、
そこが壊れていても**本番でしか分からない**（今回の障害と同じ型）。

本物の JV-Link ダイアログは JVLinkAgent 起動後の初回 JVOpen でしか出ず再現性が無いので、
**同じ形の MessageBox を自分で出して**押されることを確かめる。

    ssh windows-vm 'powershell -NoProfile -Command "Set-Content -Path
      \\"C:\\kiseki\\windows-agent\\adhoc_cmd.txt\\" -Value \\"selftest_dialog_guard.py\\" -Encoding ASCII"'
    ssh windows-vm 'schtasks /run /tn kiseki-RunAdhoc'

出力: C:\\kiseki\\windows-agent\\selftest_dialog_guard.log
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import jvlink_dialog_guard as guard  # noqa: E402

log_path = Path(__file__).resolve().parent / "selftest_dialog_guard.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(str(log_path), encoding="utf-8", mode="w")],
)
log = logging.getLogger("selftest")

MB_YESNO = 0x00000004
IDYES = 6
IDNO = 7

_answer: dict[str, int] = {}


def _show(title: str, text: str) -> None:
    """モーダルを出して、押された結果を _answer に入れる。"""
    _answer[title] = ctypes.windll.user32.MessageBoxW(0, text, title, MB_YESNO)


WM_COMMAND = 0x0111


def _force_close(title: str) -> None:
    """残っているダイアログを自分で閉じる。

    ⚠️ **後始末を省いてはいけない。** 初版はこれが無く、ケース①で閉じ損ねた
    JRA-VAN ダイアログが残ったままケース②を走らせていた。dismiss() は
    その残骸を見て「触らなかった」と報告するので、**ケース②が常に成功に見える**
    （2026-08-12 に実際にそうなっていた）。
    """
    hwnd = ctypes.windll.user32.FindWindowW(None, title)
    if hwnd:
        ctypes.windll.user32.PostMessageW(hwnd, WM_COMMAND, IDNO, 0)


def _case(title: str, text: str, *, expect_dismissed: bool) -> bool:
    _answer.pop(title, None)
    th = threading.Thread(target=_show, args=(title, text), daemon=True)
    th.start()
    time.sleep(1.5)  # ダイアログが生成されるのを待つ

    closed = guard.dismiss(log)
    th.join(timeout=5)
    got = _answer.get(title)

    if expect_dismissed:
        ok = closed == 1 and got == IDNO
        expect = f"closed=1 / IDNO={IDNO}"
    else:
        ok = closed == 0 and got is None
        expect = "触らない"
    log.info(f"[{title}] closed={closed} answer={got} (期待: {expect}) -> {'OK' if ok else 'NG'}")

    # 成否によらず必ず後始末する。残すと次のケースの判定が汚れる
    _force_close(title)
    th.join(timeout=5)
    if title in _answer and not expect_dismissed:
        log.info(f"[{title}] 後始末で閉じた (answer={_answer[title]})")
    return ok


def main() -> None:
    if not sys.platform.startswith("win"):
        log.error("Windows でのみ実行できる")
        sys.exit(1)

    results = []
    # ① 本物と同じ文面・同じボタン構成 → 「いいえ」が押されること
    results.append(
        _case(
            "JRA-VAN DataLab.",
            "現在のバージョンより新しいバージョン(5.0.0)のJV-Linkが存在します。\r\n"
            "新しいバージョンをダウンロードしますか？",
            expect_dismissed=True,
        )
    )
    # ② 無関係なダイアログ（UmaConn のリーク通知など）は触らないこと
    results.append(_case("UmaConn", "Unexpected Memory Leak", expect_dismissed=False))

    ok = all(results)
    log.info(f"===== 総合: {'OK' if ok else 'NG'} ({sum(results)}/{len(results)}) =====")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
