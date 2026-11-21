# Isolated allocation experiment. Does not format or modify existing images.
$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$drive = [IO.Path]::GetPathRoot($root).Substring(0,1)
$receipt = Get-Content 'C:\nas\system\state\prepared-host.json' -Raw | ConvertFrom-Json
$volume = Get-Volume -DriveLetter $drive
if ($volume.FileSystem -ne 'NTFS' -or $receipt.volume_guid -ne ($volume.UniqueId -replace '^.*\{([^}]+)\}.*$', '$1') -or $receipt.drive -ne $drive) { throw 'Volume differs from prepared host; refusing test file creation.' }
if ($volume.SizeRemaining -lt 8GB) { throw 'Less than 8 GiB free on outer volume.' }
$work = Join-Path $root ('system\runtime\storage-probes\sparse-probe-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$image = Join-Path $work 'blank.img'
$stream = $null
try {
    # CreateNew refuses to open an existing file. Mark sparse before extending.
    $stream = [System.IO.File]::Open($image, [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::ReadWrite)
    & fsutil.exe sparse setflag $image
    if ($LASTEXITCODE -ne 0) { throw 'Unable to mark new file sparse.' }
    $stream.SetLength(268435456)
    $stream.Flush($true)
} finally {
    if ($null -ne $stream) { $stream.Dispose() }
}
$lines = @("Artifacts retained: $work", 'Phase: allocation only; blank file, NOT an ext4 filesystem.',
    "Logical bytes: $((Get-Item -LiteralPath $image).Length)")
$lines | Tee-Object -FilePath (Join-Path $work 'allocation.log')
foreach ($operation in @('queryflag', 'queryrange')) {
    $result = & fsutil.exe sparse $operation $image 2>&1
    $code = $LASTEXITCODE
    $result | Tee-Object -FilePath (Join-Path $work 'allocation.log') -Append
    if ($code -ne 0) { throw "fsutil $operation failed with exit code $code" }
}
Write-Host 'Allocation experiment complete. Mount and allocation after ext4 writes are NOT TESTED.'
