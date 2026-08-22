"""JVOpen(RACE)で本日のオッズ(O1/O2)を確認するテスト。"""
import win32com.client
import os
from collections import Counter

try:
    from dotenv import load_dotenv
    load_dotenv(r"C:\kiseki\.env")
except Exception:
    pass

sid = os.getenv("JRAVAN_SID", "kiseki")

jv = win32com.client.Dispatch("JVDTLab.JVLink.1")
try:
    # ⚠️ jv.JVSetUIProperties(False, False) をここで呼んでいたが削除した。
    #    このメソッドは仕様上**引数0個**で、しかも「設定変更（ダイアログ版）」＝
    #    モーダル設定ダイアログを開く。2引数呼び出しは pywin32 が TypeError を投げ、
    #    素の except で握り潰されていたので **一度も効いていなかった**。
    #    「引数が多すぎるバグ」と見て 0 引数に直すと、非対話プロセスでダイアログが
    #    開いて永久ブロックする。UI 抑止が要るなら JVSetSaveFlag / JVSetPayFlag を使う。
    pass  # noqa: PIE790
except Exception:
    pass
rc_init = jv.JVInit(sid)
print("JVInit: rc={}".format(rc_init))

# 昨日00:00からの差分 (option=2) で今日のデータも含まれるはず
from_time = "20260327000000"
for opt in (2, 4, 1):
    result = jv.JVOpen("RACE", from_time, opt, 0, 0, "")
    if isinstance(result, tuple):
        rc = result[0]
        file_count = result[1] if len(result) > 1 else "?"
    else:
        rc, file_count = result, "?"
    print("JVOpen(RACE, option={}, from={}): rc={}, files={}".format(opt, from_time, rc, file_count))
    if rc < 0:
        try:
            jv.JVClose()
        except Exception:
            pass
        continue

    counter = Counter()
    total = 0
    o1_sample = None

    while total < 3000:
        r = jv.JVRead("", 256000, "")
        ret_code = r[0]
        if ret_code == 0:
            break
        elif ret_code == -1:
            continue
        elif ret_code < 0:
            print("  JVRead error: rc={}".format(ret_code))
            break
        else:
            buff = r[1]
            rec_id = buff[:2] if buff else "??"
            counter[rec_id] += 1
            total += 1
            if rec_id == "O1" and o1_sample is None:
                o1_sample = buff

    jv.JVClose()
    print("  取得: {}件, 種別: {}".format(total, dict(counter)))
    if o1_sample:
        print("  O1 sample: {}".format(repr(o1_sample[:60])))
    break
