"""JVOpen(蓄積系) の無限ブロックが「どこで」起きているかを実測する診断スクリプト。

2026-08-06 18:00 の rc=-413 以降、JVOpen が DataSpec を問わず返らない。
到達性・FW・ダイアログ・認証・容量・競合はすべて否定済み（memory
jvopen_dead_diagnosis_ruled_out_2026_08_08）。残る切り分けは
「JVLinkAgent がそもそも外へ出ていないのか / 出た先で待たされているのか」。

JVOpen をワーカースレッドで呼び、メインスレッドが 5 秒ごとに

  - 経過秒
  - 自プロセス → localhost:6531 (JVLinkAgent) のコネクション状態
  - JVLinkAgent → 外部 (datalab / authlab) のコネクション状態
  - JVLinkAgent の累積 CPU 秒

を記録する。これで以下が区別できる。

  A) 自プロセスが 6531 へ繋がっていない        → COM/エージェント間で詰まっている
  B) 6531 は繋がるが agent が外へ出ない        → agent 内部で詰まっている
  C) agent が外へ出たまま戻らない              → サーバー側 / 経路
  D) CPU が回り続けている                      → 無応答ではなくループ

実行:
  ssh windows-vm 'powershell -Command "Set-Content -Path \"C:\\kiseki\\windows-agent\\adhoc_cmd.txt\" -Value \"probe_jvopen_block.py\" -Encoding ASCII"'
  ssh windows-vm 'schtasks /run /tn kiseki-RunAdhoc'

出力: C:\\kiseki\\windows-agent\\probe_jvopen_block.log

⚠️ JV-Link は同時1接続。realtime が動いている時間帯には実行しないこと。
"""

import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pythoncom
import win32com.client
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

log_path = Path(__file__).resolve().parent / "probe_jvopen_block.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(log_path), encoding="utf-8", mode="w"),
    ],
)
log = logging.getLogger("probe")

JRAVAN_SID = os.getenv("JRAVAN_SID", "kiseki")

# 観測の上限。realtime の起動(9:00)を邪魔しないよう短く切る。
LIMIT_SEC = 180
POLL_SEC = 5

# server_info レジストリの実測値（2026-08-12）
DATA_HOST = "datalab.cdn.jra-van.ne.jp"
AUTH_HOST = "authlab.jra-van.ne.jp"

_result: dict[str, object] = {}

# JVOpen が出すダイアログを自動で押すか（コマンドライン第4引数で切り替え）
DISMISS = False
DISMISS_BUTTON = "いいえ"
SUPPRESS = False


def _agent_pid() -> int | None:
    """JVLinkAgent.exe の PID を返す。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq JVLinkAgent.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower().startswith("jvlinkagent"):
            try:
                return int(parts[1])
            except ValueError:
                return None
    return None


def _cpu_seconds(pid: int) -> float | None:
    """指定 PID の累積 CPU 秒。"""
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).CPU",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout.strip()
        return float(out) if out else None
    except Exception:
        return None


def _connections(pids: set[int]) -> list[str]:
    """netstat -ano から対象 PID の TCP 行だけ抜く。"""
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            timeout=25,
        ).stdout
    except Exception as e:
        return [f"netstat failed: {e}"]
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "TCP":
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid in pids:
            rows.append(f"  pid={pid} local={parts[1]} remote={parts[2]} state={parts[3]}")
    return rows


def _top_level_windows() -> list[str]:
    """自分と同じデスクトップのトップレベルウィンドウを全部列挙する。

    以前の切り分けは `Process.MainWindowHandle` で「ダイアログ0件」と結論したが、
    それは非表示ウィンドウや別スレッド所有のダイアログを取りこぼす。また SSH 経由の
    PowerShell は別ウィンドウステーションになるため対話セッションのウィンドウが
    そもそも見えない。**このプロセス自身から** EnumWindows する必要がある。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    rows: list[str] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lparam):  # noqa: ANN001
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        rows.append(
            f"  hwnd=0x{hwnd:X} pid={pid.value} visible={bool(user32.IsWindowVisible(hwnd))} "
            f"class={cls.value!r} title={title.value!r}"
        )
        return True

    user32.EnumWindows(proc(cb), 0)
    return rows


def _dump_dialogs(self_pid: int) -> list[str]:
    """自プロセスが出しているダイアログ(#32770)の中身を読む。

    JVOpen はモーダルダイアログを出して押されるのを待つことがある。pythonw には
    閉じる者がいないので永久にブロックする。文面が分からないと対処できないので、
    子ウィンドウ（Static / Button）のテキストを全部拾う。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    out: list[str] = []
    targets: list[int] = []

    def find_dialog(hwnd, _lparam):  # noqa: ANN001
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != self_pid:
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value == "#32770":
            targets.append(hwnd)
        return True

    user32.EnumWindows(proc(find_dialog), 0)

    for hwnd in targets:
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        out.append(f"  ダイアログ hwnd=0x{hwnd:X} title={title.value!r}")

        def dump_child(child, _lparam):  # noqa: ANN001
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(child, cls, 256)
            txt = ctypes.create_unicode_buffer(2048)
            user32.GetWindowTextW(child, txt, 2048)
            out.append(f"    child class={cls.value!r} text={txt.value!r}")
            return True

        user32.EnumChildWindows(hwnd, proc(dump_child), 0)
    return out


def _dismiss_dialog(self_pid: int, button_text: str, check_suppress: bool) -> list[str]:
    """自プロセスの JVOpen ダイアログを押して閉じる。

    2026-08-06 の JV-Link 5.0.0 リリース以降、JVOpen は毎回
    「新しいバージョン(5.0.0)が存在します。ダウンロードしますか？」を出す。
    pythonw には押す者がいないので JVOpen が永久にブロックする。

    check_suppress=True なら「…お知らせは今後表示しない。」のチェックも入れてから押す。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    BM_CLICK = 0x00F5
    BM_GETCHECK = 0x00F0
    BST_CHECKED = 1

    acted: list[str] = []
    dialogs: list[int] = []

    def find_dialog(hwnd, _lparam):  # noqa: ANN001
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != self_pid:
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value == "#32770":
            dialogs.append(hwnd)
        return True

    user32.EnumWindows(proc(find_dialog), 0)

    for dlg in dialogs:
        target = {"button": None, "suppress": None}

        def pick(child, _lparam):  # noqa: ANN001
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(child, cls, 256)
            if cls.value != "Button":
                return True
            txt = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(child, txt, 512)
            if txt.value == button_text:
                target["button"] = child
            elif "表示しない" in txt.value:
                target["suppress"] = child
            return True

        user32.EnumChildWindows(dlg, proc(pick), 0)

        if check_suppress and target["suppress"]:
            # ⚠️ BM_SETCHECK の後に BM_CLICK を送ってはいけない。BM_CLICK は「クリック」
            # なのでチェック状態をトグルする＝SETCHECK で入れたチェックが外れる
            # （2026-08-12 に実際にこれで抑止が効かなかった）。BM_CLICK 単発で入れる。
            before = user32.SendMessageW(target["suppress"], BM_GETCHECK, 0, 0)
            if before != BST_CHECKED:
                user32.SendMessageW(target["suppress"], BM_CLICK, 0, 0)
            after = user32.SendMessageW(target["suppress"], BM_GETCHECK, 0, 0)
            acted.append(f"  「今後表示しない」 check {before} -> {after}")
        if target["button"]:
            user32.SendMessageW(target["button"], BM_CLICK, 0, 0)
            acted.append(f"  「{button_text}」を押した")
        else:
            acted.append(f"  「{button_text}」ボタンが見つからない")
    return acted


def _resolve(host: str) -> set[str]:
    """host の A レコード集合。netstat の remote と突き合わせるため。"""
    import socket

    try:
        return {ai[4][0] for ai in socket.getaddrinfo(host, 80, proto=socket.IPPROTO_TCP)}
    except Exception:
        return set()


def _worker(dataspec: str, from_time: str, option: int) -> None:
    """JVOpen をここで呼ぶ。返ってきたら _result に記録する。"""
    pythoncom.CoInitialize()
    try:
        t0 = time.monotonic()
        jv = win32com.client.Dispatch("JVDTLab.JVLink.1")
        _result["dispatch_sec"] = round(time.monotonic() - t0, 2)

        t1 = time.monotonic()
        rc = jv.JVInit(JRAVAN_SID)
        _result["init_rc"] = rc
        _result["init_sec"] = round(time.monotonic() - t1, 2)
        log.info(f"[worker] JVInit rc={rc} ({_result['init_sec']}s)")
        if rc != 0:
            _result["done"] = True
            return

        log.info(f"[worker] JVOpen({dataspec}, {from_time}, option={option}) 呼び出し開始")
        t2 = time.monotonic()
        res = jv.JVOpen(dataspec, from_time, option, 0, 0, "")
        _result["open_rc"] = res[0] if isinstance(res, tuple) else res
        _result["open_files"] = res[1] if isinstance(res, tuple) else None
        _result["open_sec"] = round(time.monotonic() - t2, 2)
        log.info(f"[worker] JVOpen 戻り値 {_result['open_rc']} ({_result['open_sec']}s)")
        try:
            jv.JVClose()
        except Exception:
            pass
    except Exception as e:  # noqa: BLE001
        _result["error"] = repr(e)
        log.error(f"[worker] 例外: {e!r}")
    finally:
        _result["done"] = True
        pythoncom.CoUninitialize()


def main() -> None:
    global DISMISS, DISMISS_BUTTON, SUPPRESS

    dataspec = sys.argv[1] if len(sys.argv) > 1 else "TOKU"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    option = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    # 4番目以降: dismiss / dismiss-suppress
    mode = sys.argv[4] if len(sys.argv) > 4 else ""
    DISMISS = mode.startswith("dismiss")
    SUPPRESS = mode == "dismiss-suppress"
    from_time = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d") + "000000"

    self_pid = os.getpid()
    agent_pid = _agent_pid()
    data_ips = _resolve(DATA_HOST)
    auth_ips = _resolve(AUTH_HOST)

    log.info("=" * 70)
    log.info(f"probe_jvopen_block: dataspec={dataspec} from={from_time} option={option}")
    log.info(f"self_pid={self_pid} JVLinkAgent_pid={agent_pid} limit={LIMIT_SEC}s")
    log.info(f"{DATA_HOST} -> {sorted(data_ips)}")
    log.info(f"{AUTH_HOST} -> {sorted(auth_ips)}")
    log.info("=" * 70)

    if agent_pid is None:
        log.error("JVLinkAgent.exe が見つからない。サービスが停止している可能性")

    pids = {self_pid} | ({agent_pid} if agent_pid else set())
    cpu0 = _cpu_seconds(agent_pid) if agent_pid else None

    th = threading.Thread(target=_worker, args=(dataspec, from_time, option), daemon=True)
    th.start()

    t0 = time.monotonic()
    while not _result.get("done"):
        elapsed = time.monotonic() - t0
        if elapsed > LIMIT_SEC:
            log.error(f"上限 {LIMIT_SEC}s に到達。JVOpen は返らなかった")
            break
        time.sleep(POLL_SEC)
        elapsed = round(time.monotonic() - t0, 1)
        rows = _connections(pids)
        cpu = _cpu_seconds(agent_pid) if agent_pid else None
        dcnt = sum(1 for r in rows if any(ip in r for ip in data_ips))
        acnt = sum(1 for r in rows if any(ip in r for ip in auth_ips))
        agent_cpu = f"{cpu:.2f}" if cpu is not None else "?"
        delta = f"{cpu - cpu0:+.2f}" if (cpu is not None and cpu0 is not None) else "?"
        log.info(
            f"[{elapsed:6.1f}s] conn={len(rows)} datalab={dcnt} authlab={acnt} "
            f"agentCPU={agent_cpu}s (Δ{delta})"
        )
        for r in rows:
            log.info(r)
        dlgs = _dump_dialogs(self_pid)
        if dlgs:
            log.warning("★ 自プロセスがダイアログを出している（JVOpen はこれを待っている）")
            for d in dlgs:
                log.warning(d)
            if DISMISS:
                for a in _dismiss_dialog(self_pid, DISMISS_BUTTON, SUPPRESS):
                    log.warning(a)
        elif elapsed < 20:
            wins = _top_level_windows()
            log.info(f"  トップレベルウィンドウ {len(wins)}件（ダイアログなし）")

    log.info("-" * 70)
    log.info(f"結果: {_result}")
    if not _result.get("done"):
        log.error("JVOpen がブロックしたままプロセスを落とす（COM を掴んだままなので強制終了）")
        logging.shutdown()
        os._exit(2)
    log.info("正常終了")


if __name__ == "__main__":
    main()
