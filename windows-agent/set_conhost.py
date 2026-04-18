"""Windows 11 のデフォルトターミナルを ConHost に切り替える。

prlctl exec --current-user でコマンドを実行するたびに
Windows Terminal ウィンドウが出現する問題を修正する。
"""
import winreg

KEY_PATH = r"Console\%Startup"

try:
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, KEY_PATH,
        0, winreg.KEY_SET_VALUE | winreg.KEY_CREATE_SUB_KEY,
    )
except FileNotFoundError:
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY_PATH)

winreg.SetValueEx(key, "DelegateFocusToConsoleHost", 0, winreg.REG_DWORD, 1)
winreg.CloseKey(key)
print("DelegateFocusToConsoleHost=1 set → ConHost is now default terminal", flush=True)
