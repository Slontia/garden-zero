param([string]$Module='all',[string]$Distribution='Ubuntu')
$ErrorActionPreference='Stop'
$config=Join-Path (Split-Path -Parent $PSScriptRoot) 'system\config\config.json'
if(Test-Path -LiteralPath $config){
 & (Join-Path $PSScriptRoot 'scripts\attach-native-storage.ps1') -ConfigFile $config -Distribution $Distribution
}
$linuxPath=(& wsl.exe -d $Distribution -- wslpath -u $PSScriptRoot).Trim()
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
& wsl.exe -d $Distribution -- bash "$linuxPath/start.sh" $Module
exit $LASTEXITCODE
