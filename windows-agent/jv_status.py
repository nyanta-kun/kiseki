import win32com.client
jv = win32com.client.Dispatch("JVDTLab.JVLink")
with open("C:/kiseki/.env") as f:
    env = f.read()
sid = [l.split("=",1)[1].strip() for l in env.splitlines() if l.startswith("JRAVAN_SID")][0]
rc_init = jv.JVInit(sid)
print("JVInit rc:", rc_init)

# option=1で試行（通常モード）
from_time = "20240322000000"
print("\n--- option=1 test ---")
result1 = jv.JVOpen("RACE", from_time, 1, 0, 0, "")
print("JVOpen(option=1) result:", result1)
jv.JVClose()

import time; time.sleep(2)

# option=3で試行
print("\n--- option=3 test ---")
result3 = jv.JVOpen("RACE", from_time, 3, 0, 0, "")
print("JVOpen(option=3) result:", result3)
jv.JVClose()
