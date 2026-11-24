@echo off
chcp 65001 >nul
cd /d "%~dp0"
pythonw.exe "tk_app.py" --hidden
exit /b
