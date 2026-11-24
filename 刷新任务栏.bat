@echo off
chcp 65001 >nul
echo 正在重启资源管理器以刷新任务栏图标缓存...
taskkill /f /im explorer.exe >nul 2>nul
timeout /t 2 /nobreak >nul
start explorer.exe
echo 完成。请查看任务栏和右下角托盘。
timeout /t 3 /nobreak >nul
