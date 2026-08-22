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
#
# 🔴 2026-08-23: JV-Link 5.0.0 で option=3/4（セットアップ）中に
#    class=#32770 / title='セットアップ' / visible=0 のダイアログが観測された。
#    接頭辞が JRA-VAN / JV-Link のどちらでもないため対象外として捨てられ、
#    JVOpen が 6 時間ブロックし続けていた（毎晩・8/17〜）。
TITLE_PREFIXES: tuple[str, ...] = ("JRA-VAN", "JV-Link", "セットアップ", "Setup")


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


# JV-Link 5.0.0 のセットアップ選択ダイアログを見分けるための語
_SETUP_NO_KIT = "持っていない"
_SETUP_KIT_MARK = "スタートキット"


def _handle_setup_dialog(
    log: logging.Logger, buttons: dict[str, int], *, dry_run: bool
) -> bool:
    """JV-Link 5.0.0 の「セットアップ」選択ダイアログに応答する。

    「スタートキット(CD/DVD-ROM)を持っていない」を選んでから OK を押す。
    CD が無い環境なので全ダウンロードで進めるのが正しい。

    Returns:
        このダイアログとして処理したら True。
    """
    no_kit = next(
        (label for label in buttons if _SETUP_KIT_MARK in label and _SETUP_NO_KIT in label),
        None,
    )
    ok = next((label for label in buttons if normalize_label(label) in ("OK", "ＯＫ")), None)
    if no_kit is None or ok is None:
        return False

    if dry_run:
        log.warning(f"[dry-run] セットアップダイアログ: 「{no_kit}」→「{ok}」を押すところ")
        return True

    # ctypes / user32 は dismiss() 内でローカルに作られるので、ここでも取り直す
    # （このモジュールは Windows 以外でも import されるため、トップレベルで
    #   ctypes.windll を触ってはいけない）
    import ctypes  # noqa: PLC0415

    user32 = ctypes.windll.user32
    BM_CLICK = 0x00F5
    BM_GETCHECK = 0x00F0
    BST_CHECKED = 0x0001
    if user32.SendMessageW(buttons[no_kit], BM_GETCHECK, 0, 0) != BST_CHECKED:
        user32.SendMessageW(buttons[no_kit], BM_CLICK, 0, 0)
    user32.SendMessageW(buttons[ok], BM_CLICK, 0, 0)
    log.warning(
        f"セットアップダイアログに自動応答: 「{no_kit}」を選んで「{ok}」を押した"
        "（スタートキット無し＝全ダウンロード）"
    )
    return True


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

        # 🔴 タイトルで弾く前に必ず記録する。
        #    以前はここが `if not is_target_title(title): continue` だったため、
        #    接頭辞に一致しないダイアログを**一行も残さず**捨てていた。
        #    その結果「ログに title= が0件だからダイアログは無い」と誤読でき、
        #    JV-Link 5.0.0 の 'セットアップ' ダイアログを 6 夜見逃した。
        #    docstring が約束している「未知のダイアログでも文面を必ず残す」を守る。
        target_title = is_target_title(title)

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
            f"モーダルを検出: title={title!r} target={target_title} "
            f"buttons={sorted(buttons)} text={' / '.join(texts)!r}"
        )
        if not target_title:
            # 対象外でも記録だけは残す（押しはしない）
            continue

        # --- JV-Link 5.0.0 の「セットアップ」ダイアログ ---------------------
        # option=4 は仕様上「ダイアログ無しセットアップ」だが、5.0.0 では
        # option=3/4 とも下記の選択ダイアログを **不可視で** 出すようになった
        # （2026-08-23 に TOKU/option=4 で再現。BLDN 固有ではない）。
        # 誰も押せないので JVOpen が永久にブロックする。
        #   title='セットアップ'
        #   buttons=['OK','キャンセル',
        #            'スタートキット(CD/DVD-ROM)を持っている（推奨）',
        #            'スタートキット(CD/DVD-ROM)を持っていない']
        # CD は無いので「持っていない」を選んで OK ＝ 全ダウンロードで進める。
        # ⚠️ SAFE_BUTTONS のままだと「キャンセル」が先に当たってセットアップが
        #    中止される。ここで先に処理して return する必要がある。
        if _handle_setup_dialog(log, buttons, dry_run=dry_run):
            closed += 1
            continue

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
