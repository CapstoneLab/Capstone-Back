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

REM ----- kill anything already listening on that port -----
echo [run_login] Checking for stale process on port %PORT% ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    echo [run_login] Killing stale PID %%a on port %PORT%
    taskkill /F /PID %%a >nul 2>&1
)

REM ----- clear python bytecode cache to avoid stale .pyc -----
if exist "app\__pycache__" rmdir /s /q "app\__pycache__" >nul 2>&1
if exist "app\auth\__pycache__" rmdir /s /q "app\auth\__pycache__" >nul 2>&1

echo [run_login] Server       : http://%HOST%:%PORT%
echo [run_login] Swagger docs : http://%HOST%:%PORT%/docs
echo [run_login] GitHub login : http://%HOST%:%PORT%/auth/github/login
echo.
echo [run_login] Browser will open in 5s. Press Ctrl+C to stop the server.
echo.

REM ========= 5. auto-open browser after 5s =========
start "" /min powershell -NoProfile -Command "Start-Sleep -Seconds 5; Start-Process 'http://%HOST%:%PORT%/auth/github/login'"

REM ========= 6. run uvicorn in foreground (logs visible, Ctrl+C stops) =========
"%PY%" -m uvicorn app.main:app --host %HOST% --port %PORT% --reload
set EXIT_CODE=%ERRORLEVEL%

endlocal & exit /b %EXIT_CODE%
