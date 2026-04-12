param([int]$Port = 8000)

# 1. Kill whoever is listening on $Port
try {
    $owners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
              Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($pid in $owners) {
        Write-Host "[kill_orphans] Killing listener PID $pid on port $Port"
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    }
} catch {}

# 2. Nuke any python.exe running this app or multiprocessing spawned by a dead parent
try {
    Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'multiprocessing\.spawn|uvicorn.*app\.main' } |
        ForEach-Object {
            Write-Host "[kill_orphans] Killing orphan python PID $($_.ProcessId)"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
} catch {}

exit 0
