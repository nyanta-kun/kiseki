"""JVRTOpen各データ種別のアクセス可否テスト。"""
import win32com.client
import os
import sys

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
if rc < 0:
    print("JVInit failed")
    sys.exit(1)

specs = [
    ("0B11", "単複オッズ"),
    ("0B12", "出馬表速報"),
    ("0B14", "馬体重"),
    ("0B15", "出走取消"),
    ("0B20", "騎手変更"),
    ("0B30", "成績速報"),
    ("0B31", "全オッズ"),
    ("0B32", "払戻速報"),
]

for spec, name in specs:
    rc = jv.JVRTOpen(spec, today)
    print("  JVRTOpen({} {}): rc={}".format(spec, name, rc))
    if rc >= 0:
        r = jv.JVRead("", 4096, "")
        rec_id = r[1][:2] if r[1] else ""
        print("    JVRead: ret={}, rec_id={}".format(r[0], rec_id))
        jv.JVClose()
    else:
        try:
            jv.JVClose()
        except Exception:
            pass

print("Done")
