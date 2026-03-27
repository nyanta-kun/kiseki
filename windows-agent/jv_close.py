import win32com.client
jv = win32com.client.Dispatch("JVDTLab.JVLink")
with open("C:/kiseki/.env") as f:
    env = f.read()
sid = [l.split("=",1)[1].strip() for l in env.splitlines() if l.startswith("JRAVAN_SID")][0]
print("JVInit:", jv.JVInit(sid))
print("JVClose:", jv.JVClose())
print("Done")
