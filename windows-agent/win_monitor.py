"""ウィンドウ表示イベントを90秒間監視して記録するスクリプト。"""
import ctypes
import ctypes.wintypes
import time
import sys

WINEVENT_OUTOFCONTEXT = 0
EVENT_OBJECT_SHOW = 0x8002

WinEventProc = ctypes.CFUNCTYPE(
    None,
    ctypes.wintypes.HANDLE,
    ctypes.wintypes.DWORD,
    ctypes.wintypes.HWND,
    ctypes.wintypes.LONG,
    ctypes.wintypes.LONG,
    ctypes.wintypes.DWORD,
    ctypes.wintypes.DWORD,
)


def callback(hWinEventHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
    if not hwnd or idObject != 0:
        return
    try:
        buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value
        if not title:
            return
        # WindowsTerminal は除外（prlctl exec によるノイズ）
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        hProc = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, pid.value)
        pname = ""
        if hProc:
            pbuf = ctypes.create_unicode_buffer(260)
            size = ctypes.wintypes.DWORD(260)
            ctypes.windll.kernel32.QueryFullProcessImageNameW(hProc, 0, pbuf, ctypes.byref(size))
            pname = pbuf.value.split("\\")[-1] if pbuf.value else ""
            ctypes.windll.kernel32.CloseHandle(hProc)
        if "WindowsTerminal" in pname or "Terminal" in pname:
            return
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] pid={pid.value} proc={pname!r} title={title!r}", flush=True)
    except Exception:
        pass


proc_ref = WinEventProc(callback)
hook = ctypes.windll.user32.SetWinEventHook(
    EVENT_OBJECT_SHOW, EVENT_OBJECT_SHOW, None, proc_ref, 0, 0, WINEVENT_OUTOFCONTEXT
)
if not hook:
    print("SetWinEventHook failed", flush=True)
    sys.exit(1)

print("Monitoring for 90 seconds...", flush=True)
msg = ctypes.wintypes.MSG()
start = time.time()
while time.time() - start < 90:
    while ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
        ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
        ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
    time.sleep(0.005)

ctypes.windll.user32.UnhookWinEvent(hook)
print("Done.", flush=True)
