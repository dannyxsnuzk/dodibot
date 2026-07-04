$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[ERROR] Setup hasn't been run yet. Double-click setup.bat first." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path ".env")) {
    Write-Host "[ERROR] No .env file found. Double-click setup.bat first." -ForegroundColor Red
    exit 1
}

$watchPaths = @("bot.py", ".env", "src")
$lastStamp = $null
$proc = $null

function Get-Stamp {
    $items = foreach ($path in $watchPaths) {
        if (Test-Path $path) {
            Get-ChildItem -Path $path -Recurse -File |
                Where-Object {
                    $_.FullName -notmatch "\\__pycache__\\" -and
                    $_.FullName -notmatch "\\data\\" -and
                    $_.Extension -in @(".py", ".env", ".json")
                }
        }
    }
    if (-not $items) {
        return 0
    }
    return ($items | Measure-Object -Property LastWriteTimeUtc -Maximum).Maximum.Ticks
}

function Start-Bot {
    Write-Host "[dev] Starting bot..." -ForegroundColor Cyan
    return Start-Process `
        -FilePath ".venv\Scripts\python.exe" `
        -ArgumentList "bot.py" `
        -WorkingDirectory $root `
        -NoNewWindow `
        -PassThru
}

function Stop-Bot {
    param($Process)
    if ($null -ne $Process -and -not $Process.HasExited) {
        Write-Host "[dev] Stopping bot..." -ForegroundColor DarkYellow
        Stop-Process -Id $Process.Id -Force
        $Process.WaitForExit()
    }
}

try {
    $lastStamp = Get-Stamp
    $proc = Start-Bot
    Write-Host "[dev] Watching for changes. Press Ctrl+C to stop." -ForegroundColor Green

    while ($true) {
        Start-Sleep -Seconds 1
        if ($proc.HasExited) {
            Write-Host "[dev] Bot exited with code $($proc.ExitCode). Waiting for a file change..." -ForegroundColor Yellow
            while ($true) {
                Start-Sleep -Seconds 1
                $stamp = Get-Stamp
                if ($stamp -ne $lastStamp) {
                    $lastStamp = $stamp
                    $proc = Start-Bot
                    break
                }
            }
        }

        $stamp = Get-Stamp
        if ($stamp -ne $lastStamp) {
            $lastStamp = $stamp
            Stop-Bot $proc
            Start-Sleep -Milliseconds 500
            $proc = Start-Bot
        }
    }
}
finally {
    Stop-Bot $proc
}
