"""JV-Link が JVOpen 中に出すモーダルダイアログを自動で閉じる。

## なぜ要るか（2026-08-12 に6日間データが止まった実障害）

2026-08-06 18:00 を境に JVOpen(蓄積系) が DataSpec を問わず**永久にブロック**した。
切り分けの実測は「通信ゼロ・CPUゼロ・JVLinkAgent へ接続すらしない・再起動しても再発」で、
到達性・FW・認証・容量・競合・デスクトップセッションはすべて否定された。

実体は **JRA-VAN が JV-Link 5.0.0 をリリースし、JVOpen が毎回

    「現在のバージョンより新しいバージョン(5.0.0)のJV-Linkが存在します。
      新しいバージョンをダウンロードしますか？」  [はい] [いいえ]

というモーダルを出すようになった**こと。``pythonw.exe`` には押す者がいないので
JVOpen が返らない。「いいえ」を押した瞬間 rc=0 で 11 秒で返ることを実機で確認した。

⚠️ **ダイアログの「…お知らせは今後表示しない。」チェックは当てにできない**。
実機で入れても次回また出た。恒久対策はこのモジュールで**毎回押す**こと。

## 検出が難しかった理由（同じ轍を踏まないために）

- `Process.MainWindowHandle` では見つからない。このダイアログは非表示扱いで、
  以前の切り分けは「ウィンドウを持つプロセス0件」と**誤って否定**していた
- SSH 経由の PowerShell は**別ウィンドウステーション**なので対話セッションの
  ウィンドウがそもそも見えない。**ブロックしているプロセス自身から** EnumWindows する

## 安全側の設計

- 「はい」系（Yes/はい）は**絶対に押さない**。押すとバージョンアップの
  ダウンロードが始まってしまう
- 押すのは否定・中立系（いいえ / No / キャンセル / Cancel / OK）だけ
- **未知のダイアログでも文面を必ずログに残す**。今回の障害で6日間失われたのは
  まさにこの1行だった
"""

from __future__ import annotations

import logging
import re
import sys

# 押してよいボタン。左から順に探す。「はい」を含めてはいけない。
SAFE_BUTTONS: tuple[str, ...] = ("いいえ", "No", "キャンセル", "Cancel", "OK")

# 押してはいけないボタン（検査用に明示しておく）
UNSAFE_BUTTONS: tuple[str, ...] = ("はい", "Yes")

# Windows のダイアログボックスのクラス名
DIALOG_CLASS = "#32770"

# 対象にするダイアログのタイトル接頭辞
TITLE_PREFIXES: tuple[str, ...] = ("JRA-VAN", "JV-Link")


# 「いいえ(&N)」「&Yes」のようなアクセラレータ表記
_ACCELERATOR = re.compile(r"\(&.\)")


def normalize_label(label: str) -> str:
    """ボタンのキャプションを比較用に正規化する。

    ⚠️ **完全一致で比較してはいけない。** 標準の MessageBox はボタンを
    ``いいえ(&N)`` / ``はい(&Y)`` というキャプションで作る。2026-08-12 の実機
    自己テストで、完全一致のままだと「安全に押せるボタンが無い」と判断して
    **本来押せるダイアログを押せない**ことが分かった（＝ハングしたままになる）。
    """
    return _ACCELERATOR.sub("", label).replace("&", "").strip()


def choose_button(labels: list[str]) -> str | None:
    """ボタン一覧から押すべきものを選ぶ。

    Args:
        labels: ダイアログ上のボタンのキャプション一覧（生のまま渡してよい）

    Returns:
        押すべきキャプション（**入力どおりの生の文字列**）。安全なものが無ければ None
    """
    for safe in SAFE_BUTTONS:
        for label in labels:
            if normalize_label(label) == safe:
                return label
    return None


def is_unsafe(label: str) -> bool:
    """「はい」系（押すとバージョンアップDLが始まる）か。"""
    return normalize_label(label) in UNSAFE_BUTTONS


def is_target_title(title: str) -> bool:
    """このダイアログを JV-Link のものとして扱ってよいか。"""
    return any(title.startswith(p) for p in TITLE_PREFIXES)


def dismiss(log: logging.Logger, *, dry_run: bool = False) -> int:
    """自プロセスが出している JV-Link のモーダルを閉じる。

    JVOpen をブロックしているのは**呼び出しスレッド**なので、この関数は
    別スレッド（``BlockingCallGuard`` のウォッチャー）から呼ぶこと。

    Args:
        log: ログ出力先
        dry_run: True なら文面のログだけ出してボタンを押さない

    Returns:
        閉じたダイアログの数
    """
    if not sys.platform.startswith("win"):
        return 0

    import ctypes
    import os
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    BM_CLICK = 0x00F5
    BM_GETCHECK = 0x00F0
    BST_CHECKED = 1

    self_pid = os.getpid()
    dialogs: list[int] = []

    def find_dialog(hwnd, _lparam):  # noqa: ANN001
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != self_pid:
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value == DIALOG_CLASS:
            dialogs.append(hwnd)
        return True

    try:
        user32.EnumWindows(proc(find_dialog), 0)
    except Exception as e:  # noqa: BLE001 - 監視は本処理を壊してはいけない
        log.warning(f"ダイアログ走査に失敗: {e!r}")
        return 0

    closed = 0
    for hwnd in dialogs:
        title_buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title_buf, 512)
        title = title_buf.value
        if not is_target_title(title):
            continue

        texts: list[str] = []
        buttons: dict[str, int] = {}
        suppress_boxes: list[int] = []

        def collect(child, _lparam):  # noqa: ANN001
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(child, cls, 256)
            txt = ctypes.create_unicode_buffer(2048)
            user32.GetWindowTextW(child, txt, 2048)
            value = txt.value
            if cls.value == "Button":
                if "表示しない" in value:
                    suppress_boxes.append(child)
                else:
                    buttons[value] = child
            elif value:
                texts.append(value)
            return True

        user32.EnumChildWindows(hwnd, proc(collect), 0)

        # 未知のダイアログでも必ず文面を残す。今回の障害で失われたのはこの1行。
        log.warning(
            f"JV-Link がモーダルを出している: title={title!r} "
            f"buttons={sorted(buttons)} text={' / '.join(texts)!r}"
        )

        target = choose_button(list(buttons))
        if target is None:
            log.error(
                "安全に押せるボタンが無い（「はい」系は押さない方針）。"
                "BlockingCallGuard の上限まで待って強制終了に委ねる"
            )
            continue
        if dry_run:
            continue

        # ⚠️ チェックボックスに BM_SETCHECK してから BM_CLICK を送ってはいけない。
        # BM_CLICK はトグルなので入れたチェックが外れる（2026-08-12 に実際に踏んだ）。
        for box in suppress_boxes:
            if user32.SendMessageW(box, BM_GETCHECK, 0, 0) != BST_CHECKED:
                user32.SendMessageW(box, BM_CLICK, 0, 0)

        user32.SendMessageW(buttons[target], BM_CLICK, 0, 0)
        log.warning(f"「{target}」を自動で押した（JVOpen の再開を試みる）")
        closed += 1

    return closed
