# Attach only the registered NAS USB device. No elevation, binding or formatting.
param([Parameter(Mandatory=$true)][string]$ConfigFile,[string]$Distribution='Ubuntu')
$ErrorActionPreference='Stop'
$config=Get-Content -LiteralPath $ConfigFile -Raw | ConvertFrom-Json
if($config.data_storage.driver -ne 'btrfs'){return}
$instance=$config.data_storage.usb_instance
if(-not $instance -or $instance -notmatch '^USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}\\[^\\]+$'){throw 'Missing registered NAS USB identity'}
$usbipd=Get-Command usbipd -ErrorAction SilentlyContinue
$exe=if($usbipd){$usbipd.Source}else{Join-Path $env:ProgramFiles 'usbipd-win\usbipd.exe'}
if(-not(Test-Path -LiteralPath $exe)){throw 'USBIPD requires one-time Windows setup; no disk was changed'}
& wsl.exe -d $Distribution --exec true
if($LASTEXITCODE -ne 0){throw 'Target WSL distribution is unavailable'}
$state=(& $exe state | Out-String) | ConvertFrom-Json
if($LASTEXITCODE -ne 0){throw 'Cannot inspect USBIPD state'}
$devices=@($state.Devices | Where-Object {$_.InstanceId -ieq $instance})
if($devices.Count -ne 1 -or -not $devices[0].BusId){throw 'Registered NAS USB device is absent or ambiguous'}
$device=$devices[0]
if(-not $device.PersistedGuid){throw 'NAS USB device needs one-time administrator sharing'}
if(-not $device.ClientIPAddress){
 & $exe attach --wsl --busid $device.BusId
 if($LASTEXITCODE -ne 0){throw 'NAS USB attach failed; no formatting was attempted'}
}
$partition='/dev/disk/by-partuuid/'+$config.data_storage.partuuid
for($attempt=0;$attempt -lt 40;$attempt++){
 & wsl.exe -d $Distribution --exec test -b $partition
 if($LASTEXITCODE -eq 0){return}
 Start-Sleep -Milliseconds 500
}
throw 'Registered NAS partition did not appear in WSL'
