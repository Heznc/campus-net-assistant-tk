@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "data" mkdir "data"
where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw.exe "tk_app.py"
) else (
    start "" python.exe "tk_app.py"
)
exit /b
