# Compatibility entrypoint for the consolidated read-only Meta browser suite.
# Local Syncthing protocol behavior is covered separately by verify-connect-js.py.
$ErrorActionPreference='Stop'
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'verify-meta-browser.ps1')
exit $LASTEXITCODE
