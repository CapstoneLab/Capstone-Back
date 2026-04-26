@echo off
title CI-CD Backend Server (port 8010)
cd /d "%~dp0"

echo ============================================
echo   CI-CD Backend Server
echo   Port: 8010
echo   Close this window to stop the server
echo ============================================
echo.

.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8010

echo.
echo Server stopped.
timeout /t 2 >nul
