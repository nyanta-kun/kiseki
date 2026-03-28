"""0B11で取得できるレコード種別を全件確認するテスト。"""
import win32com.client
import os
import sys
from collections import Counter

try:
    from dotenv import load_dotenv
    load_dotenv(r"C:\kiseki\.env")
except Exception:
    pass

sid = os.getenv("JRAVAN_SID", "kiseki")
today = "20260328"

jv = win32com.client.Dispatch("JVDTLab.JVLink.1")
try:
    jv.JVSetUIProperties(False, False)
except Exception:
    pass
rc = jv.JVInit(sid)
print("JVInit: rc={}".format(rc))

# 0B11 (単複オッズ) を全件読む
rc = jv.JVRTOpen("0B11", today)
print("JVRTOpen(0B11): rc={}".format(rc))

if rc < 0:
    print("アクセス不可")
    sys.exit(1)

records = []
counter = Counter()
while True:
    r = jv.JVRead("", 256000, "")
    ret_code = r[0]
    if ret_code == 0:
        break
    elif ret_code == -1:
        continue
    elif ret_code < -1:
        print("JVRead error: rc={}".format(ret_code))
        break
    else:
        buff = r[1]
        rec_id = buff[:2] if buff else "??"
        counter[rec_id] += 1
        records.append((rec_id, buff))

jv.JVClose()

print("取得レコード件数: {}".format(len(records)))
print("種別内訳: {}".format(dict(counter)))

# O1/O2の最初の1件を表示
for rec_id, buff in records:
    if rec_id in ("O1", "O2"):
        print("{} サンプル(先頭100byte): {}".format(rec_id, repr(buff[:100])))
        break
else:
    print("O1/O2 レコードなし")
    if records:
        rec_id, buff = records[0]
        print("最初のレコード: {} = {}".format(rec_id, repr(buff[:80])))
