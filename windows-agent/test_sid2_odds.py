"""SID1とSID2で0B11オッズへのアクセス可否を比較テストする。"""
import win32com.client
import os
from collections import Counter

try:
    from dotenv import load_dotenv
    load_dotenv(r"C:\kiseki\.env")
except Exception:
    pass

today = "20260328"
sid1 = os.getenv("JRAVAN_SID", "")
sid2 = os.getenv("JRAVAN_SID2", "")

specs = [
    ("0B11", "単複オッズ"),
    ("0B31", "全オッズ"),
    ("0B15", "出走取消"),
]

for label, sid in [("SID1", sid1), ("SID2", sid2)]:
    print("\n=== {} ===".format(label))
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
    rc = jv.JVInit(sid)
    print("JVInit: rc={}".format(rc))
    if rc != 0:
        print("初期化失敗")
        continue

    for spec, name in specs:
        rc = jv.JVRTOpen(spec, today)
        print("  JVRTOpen({} {}): rc={}".format(spec, name, rc))
        if rc >= 0:
            counter = Counter()
            total = 0
            while total < 200:
                r = jv.JVRead("", 256000, "")
                if r[0] == 0:
                    break
                elif r[0] == -1:
                    continue
                elif r[0] < 0:
                    break
                rec_id = r[1][:2] if r[1] else "??"
                counter[rec_id] += 1
                total += 1
            jv.JVClose()
            print("    取得: {}件 {}".format(total, dict(counter)))
        else:
            try:
                jv.JVClose()
            except Exception:
                pass

print("\nDone")
