$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Venv = Join-Path $Root ".venv-win"
$Py = Join-Path $Venv "Scripts\python.exe"

Write-Host "creating virtualenv" -ForegroundColor Cyan
if (-Not (Test-Path $Py)) {
    & python -m venv $Venv
}

Write-Host "installing dependencies" -ForegroundColor Cyan
& $Py -m pip install --quiet --upgrade pip
& $Py -m pip install --quiet -r requirements-dev.txt

Write-Host "running tests" -ForegroundColor Cyan
& $Py tests\test_gaswatch.py
if ($LASTEXITCODE -ne 0) { throw "tests failed" }

New-Item -ItemType Directory -Force -Path "build" | Out-Null
New-Item -ItemType Directory -Force -Path "dist" | Out-Null

Write-Host "generating icon" -ForegroundColor Cyan
& $Py scripts\make_icon.py build\GasWatch.png
& $Py scripts\verify_icon.py build\GasWatch.png

$IconArgs = @()
$PyInstallerArgs = @()

if (Test-Path "build\GasWatch.ico") {
    $IconArgs = @("--icon", "build\GasWatch.ico")
} else {
    $PyInstallerArgs = @("--icon", "build\GasWatch.png")
}

Write-Host "building executable" -ForegroundColor Cyan
if (Test-Path "dist\GasWatch.exe") { Remove-Item -Force "dist\GasWatch.exe" }
& (Join-Path $Venv "Scripts\pyinstaller.exe") run_gui.py `
    --name GasWatch `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --log-level WARN `
    --paths src `
    --hidden-import web3 `
    --hidden-import web3.providers.rpc `
    --hidden-import web3.eth `
    --hidden-import requests `
    --hidden-import tkinter `
    --hidden-import tkinter.ttk `
    --hidden-import tkinter.messagebox `
    --hidden-import dotenv `
    --hidden-import eth_account `
    --hidden-import eth_abi `
    --hidden-import eth_utils `
    --hidden-import eth_typing `
    --exclude-module telegram `
    --exclude-module matplotlib `
    --exclude-module numpy `
    --exclude-module pandas `
    --exclude-module scipy `
    --exclude-module pytest `
    @IconArgs `
    @PyInstallerArgs

if (-Not (Test-Path "dist\GasWatch.exe")) { throw "build failed: dist\GasWatch.exe not found" }

Write-Host "smoke testing the built executable" -ForegroundColor Cyan
$env:GASWATCH_CONFIG_DIR = Join-Path $Root "build\winconfig"
$proc = Start-Process -FilePath "dist\GasWatch.exe" -ArgumentList "--help" -NoNewWindow -PassThru -Wait
Write-Host "exit code: $($proc.ExitCode)"

$Release = Join-Path $Root "release\windows-x64"
New-Item -ItemType Directory -Force -Path $Release | Out-Null
Copy-Item -Recurse -Force "dist\GasWatch" $Release
Copy-Item -Force "dist\GasWatch.exe" $Release
Copy-Item -Force "build\GasWatch.png" (Join-Path $Release "GasWatch-icon.png")

@"
GasWatch 1.0.0 for Windows (x64)

1. Unzip this folder somewhere permanent, for example C:\Tools\GasWatch.
2. Run GasWatch.exe.
3. Windows SmartScreen may warn about an unsigned app: click More info,
   then Run anyway. Or right click the zip, choose Properties, tick
   Unblock at the bottom left, then apply.

Settings, RPC endpoints and thresholds are stored in:
  %USERPROFILE%\.gaswatch\config.json
"@ | Set-Content -Path (Join-Path $Release "How-to-install.txt") -Encoding UTF8

Write-Host "done: $Release" -ForegroundColor Green