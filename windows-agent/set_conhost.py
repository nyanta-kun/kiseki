"""Windows 11 のデフォルトターミナルを ConHost に切り替える。

prlctl exec --current-user でコマンドを実行するたびに
Windows Terminal ウィンドウが出現する問題を修正する。

Windows Terminal 1.18+ では DelegateFocusToConsoleHost レジストリだけでは不十分。
settings.json の defaultTerminal を "Windows Console Host" に設定する必要がある。
"""
import json
import os
import winreg
from pathlib import Path

# ① HKCU\Console\%Startup\DelegateFocusToConsoleHost = 1（旧方式、念のため維持）
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
print("DelegateFocusToConsoleHost=1 set", flush=True)

# ② Windows Terminal settings.json の defaultTerminal を ConHost に設定（1.18+ 必須）
settings_path = Path(os.environ["LOCALAPPDATA"]) / \
    "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / \
    "LocalState" / "settings.json"

if settings_path.exists():
    with settings_path.open(encoding="utf-8") as f:
        settings = json.load(f)
    settings["defaultTerminal"] = "Windows Console Host"
    with settings_path.open("w", encoding="utf-8") as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)
    print(f"settings.json updated: defaultTerminal=Windows Console Host", flush=True)
else:
    print("Windows Terminal not installed, skipping settings.json", flush=True)

print("ConHost is now default terminal", flush=True)
