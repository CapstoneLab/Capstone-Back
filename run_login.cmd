@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM ========= 1. venv =========
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [run_login] .venv not found. Creating virtualenv...
    python -m venv .venv
    if errorlevel 1 (
        echo [run_login] Failed to create venv. Is Python installed and on PATH?
        exit /b 1
    )
)

REM ========= 2. deps =========
echo [run_login] Installing/upgrading requirements (quiet) ...
"%PY%" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo [run_login] pip install failed.
    exit /b 1
)

REM ========= 3. .env (interactive if missing) =========
if not exist ".env" (
    echo.
    echo [run_login] .env not found. Let's create one for GitHub OAuth.
    echo.
    echo   1^) Register an OAuth App at https://github.com/settings/developers
    echo        Homepage URL              : http://127.0.0.1:8000
    echo        Authorization callback URL: http://127.0.0.1:8000/auth/github/callback
    echo   2^) Copy Client ID and generate a new Client Secret.
    echo.
    set /p "GH_CID=GITHUB_CLIENT_ID: "
    set /p "GH_SEC=GITHUB_CLIENT_SECRET: "

    if "!GH_CID!"=="" (
        echo [run_login] Client ID is empty. Aborting.
        exit /b 1
    )
    if "!GH_SEC!"=="" (
        echo [run_login] Client Secret is empty. Aborting.
        exit /b 1
    )

    > .env echo DATABASE_URL=postgresql+asyncpg://ciuser:cipass@localhost:5432/cidb
    >> .env echo GITHUB_CLIENT_ID=!GH_CID!
    >> .env echo GITHUB_CLIENT_SECRET=!GH_SEC!
    >> .env echo GITHUB_REDIRECT_URI=http://127.0.0.1:8000/auth/github/callback
    >> .env echo FRONTEND_REDIRECT_URL=http://127.0.0.1:8000/auth/success
    >> .env echo JWT_SECRET=dev-secret-change-me-please-%RANDOM%%RANDOM%
    echo [run_login] .env written.
    echo.
)

REM ========= 4. server =========
set HOST=127.0.0.1
set PORT=8000
if not "%~1"=="" set PORT=%~1

REM ----- kill anything holding the port and any orphan uvicorn workers -----
echo [run_login] Cleaning stale processes on port %PORT% ...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\kill_orphans.ps1" -Port %PORT%

REM ----- clear python bytecode cache to avoid stale .pyc -----
if exist "app\__pycache__" rmdir /s /q "app\__pycache__" >nul 2>&1
if exist "app\auth\__pycache__" rmdir /s /q "app\auth\__pycache__" >nul 2>&1
if exist "app\api\__pycache__" rmdir /s /q "app\api\__pycache__" >nul 2>&1

REM ----- remove any stale JWT temp file so the CLI only picks up a fresh one -----
if exist "%TEMP%\cicd_last_jwt.txt" del /q "%TEMP%\cicd_last_jwt.txt" >nul 2>&1

echo [run_login] Server       : http://%HOST%:%PORT%
echo [run_login] Swagger docs : http://%HOST%:%PORT%/docs
echo [run_login] GitHub login : http://%HOST%:%PORT%/auth/github/login
echo.

REM ========= 5. start uvicorn in the BACKGROUND (no extra window) =========
REM   - output goes to server.log so it doesn't clobber the CLI
REM   - --reload OFF in single-window mode (reloader spawns orphan workers on Windows)
if exist "server.log" del /q "server.log" >nul 2>&1
echo [run_login] Starting server in background (logs -^> server.log) ...
start /B "" cmd /c ""%PY%" -m uvicorn app.main:app --host %HOST% --port %PORT% > server.log 2>&1"

REM ========= 6. wait for /health to come up =========
echo [run_login] Waiting for server to become ready ...
set /a TRIES=0
:WAIT_LOOP
set /a TRIES+=1
"%PY%" -c "import urllib.request,sys; urllib.request.urlopen('http://%HOST%:%PORT%/health',timeout=1); sys.exit(0)" >nul 2>&1
if not errorlevel 1 goto SERVER_READY
if %TRIES% GEQ 30 (
    echo [run_login] Server did not become ready in time.
    echo [run_login] ----- last lines of server.log -----
    if exist "server.log" powershell -NoProfile -Command "Get-Content -Path 'server.log' -Tail 30"
    echo [run_login] -------------------------------------
    pause
    exit /b 1
)
"%PY%" -c "import time; time.sleep(0.5)" >nul 2>&1
goto WAIT_LOOP

:SERVER_READY
echo [run_login] Server is up.

REM ========= 7. open browser to /auth/github/login =========
start "" "http://%HOST%:%PORT%/auth/github/login"

REM ========= 8. run interactive repo/branch selector in THIS window =========
echo.
echo [run_login] Launching interactive repo/branch selector ...
echo.
"%PY%" select_repo.py --base "http://%HOST%:%PORT%"
set EXIT_CODE=%ERRORLEVEL%

REM ========= 9. stop the background server =========
echo.
echo [run_login] Stopping background server on port %PORT% ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
echo [run_login] Done. Server log is in server.log
echo.
pause

endlocal & exit /b %EXIT_CODE%
