param([string]$Module='business',[string]$Distribution='Ubuntu')
$ErrorActionPreference='Stop'
$linuxPath=(& wsl.exe -d $Distribution -- wslpath -u $PSScriptRoot).Trim()
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
& wsl.exe -d $Distribution -- bash "$linuxPath/stop.sh" $Module
exit $LASTEXITCODE
