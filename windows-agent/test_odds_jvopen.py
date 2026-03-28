"""JVOpen(RACE)で本日のO1/O2オッズが取れるか確認するテスト。"""
import win32com.client
import os
from collections import Counter

try:
    from dotenv import load_dotenv
    load_dotenv(r"C:\kiseki\.env")
except Exception:
    pass

sid = os.getenv("JRAVAN_SID", "kiseki")
today = "20260328000000"

jv = win32com.client.Dispatch("JVDTLab.JVLink.1")
try:
    jv.JVSetUIProperties(False, False)
except Exception:
    pass
rc_init = jv.JVInit(sid)
print("JVInit: rc={}".format(rc_init))

# option=4: 本日更新分のみ
result = jv.JVOpen("RACE", today, 4, 0, 0, "")
if isinstance(result, tuple):
    rc = result[0]
    file_count = result[1] if len(result) > 1 else "?"
    print("JVOpen(RACE, option=4): rc={}, files={}".format(rc, file_count))
else:
    rc = result
    print("JVOpen(RACE, option=4): rc={}".format(rc))

if rc < 0:
    print("アクセス不可")
else:
    counter = Counter()
    total = 0
    while True:
        r = jv.JVRead("", 256000, "")
        ret_code = r[0]
        if ret_code == 0:
            break
        elif ret_code == -1:
            continue
        elif ret_code < 0:
            print("JVRead error: rc={}".format(ret_code))
            break
        else:
            buff = r[1]
            rec_id = buff[:2] if buff else "??"
            counter[rec_id] += 1
            total += 1

    jv.JVClose()
    print("取得件数: {}, 種別: {}".format(total, dict(counter)))
    if counter.get("O1") or counter.get("O2"):
        print("-> O1/O2あり！")
    else:
        print("-> O1/O2なし")
