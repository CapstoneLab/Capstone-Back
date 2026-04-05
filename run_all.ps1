param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PromptArgs
)

$ErrorActionPreference = 'Stop'

Set-Location -Path $PSScriptRoot

$pythonCmd = $null
if (Test-Path '.venv/Scripts/python.exe') {
    $pythonCmd = (Resolve-Path '.venv/Scripts/python.exe').Path
} elseif (Test-Path '.venv/bin/python') {
    $pythonCmd = (Resolve-Path '.venv/bin/python').Path
} else {
    Write-Host 'Error: Python in virtual environment not found. (.venv/Scripts/python.exe or .venv/bin/python)'
    exit 1
}

if (-not $env:UBUNTU_SSH_HOST) { $env:UBUNTU_SSH_HOST = '192.168.100.142' }
if (-not $env:UBUNTU_SSH_PORT) { $env:UBUNTU_SSH_PORT = '22' }
if (-not $env:UBUNTU_SSH_USER) { $env:UBUNTU_SSH_USER = 'capstone' }
if (-not $env:UBUNTU_SSH_PASSWORD) { $env:UBUNTU_SSH_PASSWORD = '1111' }
if (-not $env:UBUNTU_RUNNER_PATH) { $env:UBUNTU_RUNNER_PATH = '/home/capstone/CI-CD-pipiline/main.py' }
if (-not $env:UBUNTU_RUNNER_REPO_ARG) { $env:UBUNTU_RUNNER_REPO_ARG = 'repo' }
if (-not $env:UBUNTU_WORKING_DIR) { $env:UBUNTU_WORKING_DIR = '/home/capstone/CI-CD-pipiline' }
if (-not $env:UBUNTU_PYTHON_COMMAND) { $env:UBUNTU_PYTHON_COMMAND = 'python3' }
if (-not $env:UBUNTU_SHELL_PRELUDE) {
    $env:UBUNTU_SHELL_PRELUDE = 'export PATH="$HOME/.local/bin:$PATH" && export NVM_DIR="$HOME/.nvm" && source "$NVM_DIR/nvm.sh"'
}
if (-not $env:WINDOWS_CALLBACK_BASE_URL) { $env:WINDOWS_CALLBACK_BASE_URL = 'http://192.168.100.1:8000' }

$backendStartedByScript = $false
$backendProcess = $null
$backendLog = Join-Path $env:TEMP 'ci_cd_back_uvicorn.log'

try {
    $healthy = $false
    try {
        $health = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 3
        if ($health.status -eq 'ok') {
            $healthy = $true
        }
    } catch {
        $healthy = $false
    }

    if (-not $healthy) {
        Write-Host 'Starting backend server...'
        $backendProcess = Start-Process -FilePath $pythonCmd -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000') -PassThru -WindowStyle Hidden -RedirectStandardOutput $backendLog -RedirectStandardError $backendLog
        $backendStartedByScript = $true

        $started = $false
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 1
            try {
                $health = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 3
                if ($health.status -eq 'ok') {
                    $started = $true
                    break
                }
            } catch {
            }
        }

        if (-not $started) {
            Write-Host "Error: Backend server failed to start. Log: $backendLog"
            exit 1
        }
    } else {
        Write-Host 'Backend server is already running.'
    }

    $lastExitCode = 0
    $continueLoop = $true

    while ($continueLoop) {
        Write-Host ''
        Write-Host 'Launching repository input prompt.'
        & $pythonCmd 'run_pipeline_prompt.py' @PromptArgs
        $lastExitCode = $LASTEXITCODE

        Write-Host ''
        Write-Host 'Run again from the beginning?'
        Write-Host '1) Enter repository again'
        Write-Host '2) Exit'
        $choice = Read-Host 'Select (1/2)'

        if ($choice -eq '1') {
            # 다음 실행부터는 프롬프트 입력 방식으로 동작
            $PromptArgs = @()
            continue
        }

        $continueLoop = $false
    }

    exit $lastExitCode
} finally {
    if ($backendStartedByScript -and $backendProcess -and -not $backendProcess.HasExited) {
        Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue
    }
}
