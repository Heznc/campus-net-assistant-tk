' CampusNet Assistant - launch without console window
Option Explicit
Dim fso, sh, base, py
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base
py = "pythonw.exe"
On Error Resume Next
sh.Run py & " """ & base & "\tk_app.py""", 0, False
If Err.Number <> 0 Then
    Err.Clear
    sh.Run "python.exe """ & base & "\tk_app.py""", 0, False
End If
