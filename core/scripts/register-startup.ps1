param([ValidatePattern('^[A-Za-z0-9._-]+$')][string]$Distribution='Ubuntu',[switch]$CheckOnly,[switch]$AllowUpdate)
$ErrorActionPreference='Stop'
$script='C:\nas\system\installed\start-nas.ps1'
$content=@"
`$ErrorActionPreference='Stop'
if(Test-Path 'C:\nas\system\state\deployment-blocked'){throw 'NAS storage incident unresolved; startup blocked.'}
& wsl.exe -d $Distribution -u root -- systemctl start nas-session.service
exit `$LASTEXITCODE
"@
$identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments='-NoProfile -NonInteractive -File "C:\nas\system\installed\start-nas.ps1"'
$legacy='-NoProfile -NonInteractive -File "C:\nas\tools\start-nas.ps1"'
$task=Get-ScheduledTask -TaskName 'NAS-Login-Start' -ErrorAction SilentlyContinue
if($task){$sid=$task.Principal.UserId;if($sid -notlike 'S-1-*'){$sid=(New-Object Security.Principal.NTAccount($sid)).Translate([Security.Principal.SecurityIdentifier]).Value};if($sid -ne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value){throw 'Existing task belongs to another principal.'};if(@($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ne 'powershell.exe' -or $task.Actions[0].Arguments -notin @($arguments,$legacy)){throw 'Unexpected existing startup action; refusing takeover.'}}
$matches=(Test-Path $script) -and ((Get-Content -Raw $script).Replace("`r`n","`n").Trim() -eq $content.Replace("`r`n","`n").Trim())
$actionMatches=$task -and $task.Actions[0].Arguments -eq $arguments
if($CheckOnly){if(-not $matches -or -not $actionMatches){throw 'Startup script or task needs reviewed update.'};Write-Output 'Current user startup task and installed script verified.';exit 0}
if(($task -or (Test-Path $script)) -and (-not $matches -or -not $actionMatches) -and -not $AllowUpdate){throw 'Existing startup differs; explicit --AllowUpdate required.'}
if(-not $matches){[IO.File]::WriteAllText($script,$content+"`n",(New-Object Text.UTF8Encoding($false)))}
$action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
if($task){if(-not $actionMatches){Set-ScheduledTask -TaskName 'NAS-Login-Start' -Action $action|Out-Null}}
else{$trigger=New-ScheduledTaskTrigger -AtLogOn -User $identity;$principal=New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited;$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries;Register-ScheduledTask -TaskName 'NAS-Login-Start' -Action $action -Trigger $trigger -Principal $principal -Settings $settings|Out-Null}
Write-Output 'NAS login startup registered or updated; no reboot performed.'
