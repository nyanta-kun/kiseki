On Error Resume Next
Dim fso, f, args
Set fso = CreateObject("Scripting.FileSystemObject")
Set f = fso.OpenTextFile("C:\kiseki\windows-agent\adhoc_cmd.txt", 1)
args = Trim(f.ReadLine)
f.Close
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\kiseki\windows-agent"
WshShell.Run "C:\Python312-32\pythonw.exe " & args, 0, False
