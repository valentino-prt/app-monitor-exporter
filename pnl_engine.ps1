# ===== LOAD GLOBAL ENV =====
. "C:\quant_apps\config\env.ps1"

$appName = "pnl_engine"
$appDir  = "$BASE_DIR\$appName"

New-Item -ItemType Directory -Force -Path "$appDir" | Out-Null

Write-Host "================================="
Write-Host "Starting $appName"
Write-Host "Time: $(Get-Date)"
Write-Host "================================="

# Move to app directory
Set-Location $appDir

# Run Python script (console only)
& $PYTHON pnl_engine.py

Write-Host ""
Write-Host "$appName stopped."
Read-Host "Press Enter to exit"