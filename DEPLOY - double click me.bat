@echo off
cd /d "%~dp0"
echo Running deploy, please wait...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy_finish.ps1" > "%~dp0deploy_log.txt" 2>&1
echo.
echo ===== RESULT =====
type "%~dp0deploy_log.txt"
echo.
echo Finished. This window stays open so you can read it.
pause
