param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8787
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Virtual environment not found. Run: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e ."
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    $owners = ($listener | Select-Object -ExpandProperty OwningProcess -Unique) -join ", "
    throw "Port $Port is already in use by PID: $owners"
}

Write-Host "Local AI Gateway: http://127.0.0.1:$Port"
& $python -m uvicorn app.main:app --host 127.0.0.1 --port $Port --app-dir $projectRoot
exit $LASTEXITCODE
