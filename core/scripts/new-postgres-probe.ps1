# Host-specific, isolated 2 GiB sparse file. Never opens existing files.
$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$drive = [IO.Path]::GetPathRoot($root).Substring(0,1)
$receipt = Get-Content 'C:\nas\system\state\prepared-host.json' -Raw | ConvertFrom-Json
$volume = Get-Volume -DriveLetter $drive
if ($volume.FileSystem -ne 'NTFS' -or $receipt.volume_guid -ne ($volume.UniqueId -replace '^.*\{([^}]+)\}.*$', '$1') -or $receipt.drive -ne $drive) { throw 'Volume differs from prepared host; refusing test file creation.' }
if ($volume.SizeRemaining -lt 8GB) { throw 'Less than 8 GiB free on outer volume.' }
$work = Join-Path $root ('system\runtime\storage-probes\postgres-probe-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$image = Join-Path $work 'database.img'
$stream = [IO.File]::Open($image, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
try {
    & fsutil.exe sparse setflag $image
    if ($LASTEXITCODE -ne 0) { throw 'Cannot set sparse attribute.' }
    $stream.SetLength(2GB)
    $stream.Flush($true)
} finally { $stream.Dispose() }
foreach ($operation in @('queryflag', 'queryrange')) {
    & fsutil.exe sparse $operation $image
    if ($LASTEXITCODE -ne 0) { throw "fsutil $operation failed." }
}
Write-Output "New blank database test image: $image"
