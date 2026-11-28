# Isolated VSS capability probe. Does not stop applications or change backup policy.
[CmdletBinding()]
param(
    [string]$NasRoot = 'D:\nas',
    [string]$RuntimeRoot = 'C:\nas\system\runtime',
    [string]$WslDistribution = 'Ubuntu'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this probe in an Administrator PowerShell. WSL sudo does not elevate Windows.'
}
$NasRoot = [IO.Path]::GetFullPath($NasRoot).TrimEnd('\')
$volumeRoot = [IO.Path]::GetPathRoot($NasRoot)
$volume = Get-CimInstance Win32_Volume -Filter ("DriveLetter='{0}'" -f $volumeRoot.TrimEnd('\'))
if (-not $volume -or $volume.FileSystem -ne 'NTFS') { throw 'Expected an existing NTFS NAS volume.' }
if (-not (Test-Path -LiteralPath $NasRoot -PathType Container)) { throw 'NAS root is missing.' }
if ((Get-Item -LiteralPath $NasRoot).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'NAS root must not be a reparse point.' }
if ($volume.FreeSpace -lt 2GB) { throw 'Insufficient free space for an isolated snapshot probe.' }
$tag = 'vss-probe-' + [Guid]::NewGuid().ToString('N')
$run = Join-Path $RuntimeRoot $tag
$fixture = Join-Path $NasRoot ('.snapshot-validation-' + $tag)
$view = Join-Path $run 'view'
$reportPath = Join-Path $run 'result.json'
New-Item -ItemType Directory -Path $run | Out-Null
$report = [ordered]@{ state='running'; scope='isolated file snapshot only; not application/database consistency'; volume=$volume.DeviceID; creation_seconds=$null; windows_old_version=$false; wsl_old_version=$false; cleanup=$false }
$shadow = $null
$watch = [Diagnostics.Stopwatch]::new()
try {
    New-Item -ItemType Directory -Path $fixture | Out-Null
    [IO.File]::WriteAllText((Join-Path $fixture 'before.txt'), 'garden-zero-before')
    [IO.File]::WriteAllText((Join-Path $fixture 'deleted.txt'), 'garden-zero-retained')
    $watch.Start()
    $created = Invoke-CimMethod -ClassName Win32_ShadowCopy -MethodName Create -Arguments @{ Volume=$volumeRoot; Context='ClientAccessible' }
    $watch.Stop()
    $report.creation_seconds = [Math]::Round($watch.Elapsed.TotalSeconds, 3)
    $report.return_code = $created.ReturnValue
    if ($created.ReturnValue -ne 0) { throw ('VSS Create failed with code ' + $created.ReturnValue) }
    $report.shadow_id = $created.ShadowID
    # This is the only shadow copy the probe is allowed to delete.
    $shadow = Get-CimInstance Win32_ShadowCopy | Where-Object ID -eq $created.ShadowID
    if (-not $shadow -or $shadow.VolumeName -ne $volume.DeviceID) { throw 'Created shadow identity mismatch.' }
    [IO.File]::WriteAllText((Join-Path $fixture 'before.txt'), 'garden-zero-after')
    [IO.File]::Delete((Join-Path $fixture 'deleted.txt'))
    [IO.File]::WriteAllText((Join-Path $fixture 'new.txt'), 'garden-zero-new')
    $relative = $fixture.Substring($volumeRoot.Length)
    $snapshotFixture = $shadow.DeviceObject.TrimEnd('\') + '\' + $relative
    if ([IO.File]::ReadAllText($snapshotFixture + '\before.txt') -ne 'garden-zero-before') { throw 'Snapshot changed with the live source.' }
    if ([IO.File]::ReadAllText($snapshotFixture + '\deleted.txt') -ne 'garden-zero-retained') { throw 'Snapshot lost a deleted file.' }
    if ([IO.File]::Exists($snapshotFixture + '\new.txt')) { throw 'Snapshot contains a later addition.' }
    $report.windows_old_version = $true
    $target = $shadow.DeviceObject.TrimEnd('\') + '\'
    & cmd.exe /d /c "mklink /d `"$view`" `"$target`"" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not expose the shadow as an isolated directory link.' }
    $windowsPath = $view + '\' + $relative
    $linuxPath = '/mnt/' + $windowsPath.Substring(0,1).ToLowerInvariant() + $windowsPath.Substring(2).Replace('\','/')
    $python = 'import pathlib,sys; p=pathlib.Path(sys.argv[1]); assert (p/"before.txt").read_text()=="garden-zero-before"; assert (p/"deleted.txt").read_text()=="garden-zero-retained"; assert not (p/"new.txt").exists(); print("WSL_SNAPSHOT_PASS")'
    $output = & wsl.exe --distribution $WslDistribution --exec python3 -B -c $python $linuxPath 2>&1
    if ($LASTEXITCODE -ne 0 -or (($output | Out-String) -notmatch 'WSL_SNAPSHOT_PASS')) { throw ('WSL snapshot read failed: ' + ($output | Out-String)) }
    $report.wsl_old_version = $true
    $report.state = 'PASS'
} catch {
    $report.state = 'FAIL'
    $report.error = $_.Exception.Message
} finally {
    $cleanupErrors = @()
    try {
        if (Test-Path -LiteralPath $view) {
            if (-not ((Get-Item -LiteralPath $view -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Refusing to delete a non-link snapshot view.' }
            [IO.Directory]::Delete($view)
        }
    } catch { $cleanupErrors += $_.Exception.Message }
    try {
        if ($report.shadow_id) {
            $owned = Get-CimInstance Win32_ShadowCopy | Where-Object ID -eq $report.shadow_id
            if ($owned) { $owned | Remove-CimInstance }
            if (Get-CimInstance Win32_ShadowCopy | Where-Object ID -eq $report.shadow_id) { throw 'Probe shadow still exists.' }
        }
    } catch { $cleanupErrors += $_.Exception.Message }
    try {
        # Delete only named fixture files, never recurse into an unexpected tree.
        foreach ($name in @('before.txt','deleted.txt','new.txt')) { [IO.File]::Delete((Join-Path $fixture $name)) }
        if (Test-Path -LiteralPath $fixture) { [IO.Directory]::Delete($fixture) }
    } catch { $cleanupErrors += $_.Exception.Message }
    $report.cleanup = $cleanupErrors.Count -eq 0
    if (-not $report.cleanup) { $report.cleanup_errors=$cleanupErrors; $report.state='FAIL' }
    $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    Write-Host ('Result: ' + $report.state + '; snapshot creation seconds: ' + $report.creation_seconds)
    Write-Host ('Report: ' + $reportPath)
}
if ($report.state -ne 'PASS') { throw 'Snapshot probe did not pass; inspect result.json. Existing backups and applications were not changed.' }
