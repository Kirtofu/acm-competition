@echo off
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw.exe "%~dp0contest_agent.pyw"
    exit /b 0
)
start "" python "%~dp0contest_agent.pyw"
