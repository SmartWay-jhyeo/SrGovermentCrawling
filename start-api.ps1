$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
& "$PSScriptRoot/.venv/Scripts/python.exe" -m bidloc.api --host 127.0.0.1 --port 8600
