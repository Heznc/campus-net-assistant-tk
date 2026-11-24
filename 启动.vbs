' CampusNet Assistant - launch without console window
Option Explicit
Dim fso, sh, base, exe
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base
exe = base & "\" & ChrW(26657) & ChrW(22253) & ChrW(32593) & ChrW(21161) & ChrW(25163) & ".exe"
On Error Resume Next
If fso.FileExists(exe) Then
    sh.Run """" & exe & """", 0, False
Else
    sh.Run "pythonw.exe """ & base & "\tk_app.py""", 0, False
End If
