On Error Resume Next
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /c cd /d C:\kiseki\windows-agent && python umaconn_agent.py --mode realtime >> umaconn_agent.log 2>&1", 0, False
