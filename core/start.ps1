param([string]$Module='all',[string]$Distribution='Ubuntu')
$ErrorActionPreference='Stop'
$linuxPath=(& wsl.exe -d $Distribution -- wslpath -u $PSScriptRoot).Trim()
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
& wsl.exe -d $Distribution -- bash "$linuxPath/start.sh" $Module
exit $LASTEXITCODE
