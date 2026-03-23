# ===== GLOBAL ENV CONFIG =====

# Python
$GLOBAL:PYTHON = "C:\Python311\python.exe"

# Base folder
$GLOBAL:BASE_DIR = "C:\quant_apps"

# Logs
$GLOBAL:LOG_DIR = "$BASE_DIR\logs"
New-Item -ItemType Directory -Force -Path $LOG_DIR | Out-Null

# Data folders
$GLOBAL:DATA_DIR = "D:\quant_data"
$GLOBAL:CSV_DIR  = "$DATA_DIR\csv"
$GLOBAL:METRICS_DIR = "$DATA_DIR\metrics"

# Ports
$GLOBAL:PROM_EXPORTER_PORT = 9105
$GLOBAL:FASTAPI_PORT = 8000

# Timezone
$GLOBAL:TZ = "Europe/Paris"

# Misc
$GLOBAL:ENV = "prod"

Write-Host "Environment loaded"


