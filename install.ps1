$ErrorActionPreference = "Stop"
$installer = Join-Path $PSScriptRoot "install_skill.py"
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $python) { throw "需要 Python 3.10+" }

& $python.Source $installer @args
exit $LASTEXITCODE
