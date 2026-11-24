' CampusNet Assistant - launch without console window
Option Explicit
Dim fso, sh, base, exe1, exe2
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base
exe1 = base & "\" & ChrW(26657) & ChrW(22253) & ChrW(32593) & ChrW(21161) & ChrW(25163) & ".exe"
exe2 = base & "\dist\" & ChrW(26657) & ChrW(22253) & ChrW(32593) & ChrW(21161) & ChrW(25163) & "\" & ChrW(26657) & ChrW(22253) & ChrW(32593) & ChrW(21161) & ChrW(25163) & ".exe"
On Error Resume Next
If fso.FileExists(exe1) Then
    sh.Run """" & exe1 & """", 0, False
ElseIf fso.FileExists(exe2) Then
    sh.Run """" & exe2 & """", 0, False
Else
    sh.Run "pythonw.exe """ & base & "\tk_app.py""", 0, False
End If
