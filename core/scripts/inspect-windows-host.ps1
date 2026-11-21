# Read-only inventory. Does not start Docker, attach disks, or print container env.
# Keep collecting when a native command reports an unavailable daemon or old WSL.
$ErrorActionPreference = 'Continue'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$directory = Join-Path $root ('system\runtime\host-inventory-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $directory -ErrorAction Stop | Out-Null
$report = [ordered]@{}
$report.Time = (Get-Date).ToString('o')
$os = Get-CimInstance Win32_OperatingSystem
$report.Windows = $os | Select-Object Caption, Version, BuildNumber, OSArchitecture, TotalVisibleMemorySize, FreePhysicalMemory
$report.WSLVersion = ((& wsl.exe --version 2>&1 | Out-String) -replace "`0", '')
$report.WSLDistributions = ((& wsl.exe --list --verbose 2>&1 | Out-String) -replace "`0", '')
$report.DVolume = Get-Volume -DriveLetter D | Select-Object DriveLetter, FileSystem, FileSystemLabel, UniqueId, Size, SizeRemaining
$report.DPhysicalDisk = Get-Partition -DriveLetter D | Get-Disk | Select-Object Number, FriendlyName, SerialNumber, BusType, PartitionStyle, Size, UniqueId
try {
    $report.LegacyStorageLink = Get-Item -LiteralPath C:\nas-storage -Force -ErrorAction Stop | Select-Object FullName, LinkType, Target
} catch { $report.LegacyStorageLink = $_.Exception.Message }
$report.DockerServices = @(Get-Service -Name '*docker*' -ErrorAction SilentlyContinue | Select-Object Name, Status, StartType)
$dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
$docker = if ($dockerCommand) { $dockerCommand.Source } else { $null }
$report.DockerCLI = $docker
if ($docker) {
    $report.DockerContexts = & $docker context ls 2>&1 | Out-String
    $report.DockerVersion = & $docker version 2>&1 | Out-String
    $serverCode = $LASTEXITCODE
    $report.DockerServerReachable = ($serverCode -eq 0)
    $report.ComposeVersion = & $docker compose version 2>&1 | Out-String
    if ($serverCode -eq 0) {
        $report.Engine = & $docker info --format 'Name={{.Name}} OS={{.OperatingSystem}} Root={{.DockerRootDir}}' 2>&1 | Out-String
        $report.Containers = & $docker ps -a --format '{{.ID}} | {{.Names}} | {{.Image}} | {{.Status}}' 2>&1 | Out-String
        $report.Volumes = & $docker volume ls 2>&1 | Out-String
        $ids = @(& $docker ps -aq)
        if ($LASTEXITCODE -ne 0) { throw 'Unable to enumerate containers; inventory incomplete.' }
        $report.ContainerMounts = @(
            foreach ($containerId in $ids) {
                & $docker inspect --format '{{.Name}}:{{range .Mounts}} [{{.Type}} {{.Source}} -> {{.Destination}} RW={{.RW}}]{{end}}' $containerId 2>&1 | Out-String
            }
        )
        $volumeNames = @(& $docker volume ls -q)
        if ($LASTEXITCODE -ne 0) { throw 'Unable to enumerate volumes; inventory incomplete.' }
        $report.VolumePaths = @(
            foreach ($volumeName in $volumeNames) {
                & $docker volume inspect --format '{{.Name}} | Driver={{.Driver}} | Mountpoint={{.Mountpoint}}' $volumeName 2>&1 | Out-String
            }
        )
    }
}
# Only selected Docker Desktop storage settings; never dump the settings file.
$settingsPath = Join-Path $env:APPDATA 'Docker\settings-store.json'
if (-not (Test-Path -LiteralPath $settingsPath)) {
    $settingsPath = Join-Path $env:APPDATA 'Docker\settings.json'
}
if (Test-Path -LiteralPath $settingsPath) {
    try {
        $settings = Get-Content -Raw -LiteralPath $settingsPath -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $report.DesktopStorageSettings = $settings | Select-Object diskImageLocation, dataFolder, wslEngineEnabled
    } catch { $report.DesktopStorageSettings = 'Unable to parse settings; storage location remains unknown.' }
}
$destination = Join-Path $directory 'windows-host.json'
$json = $report | ConvertTo-Json -Depth 8
$json | Set-Content -LiteralPath $destination -Encoding UTF8 -ErrorAction Stop
Write-Output $json
Write-Host "Report retained: $destination"
Write-Host 'Read-only inventory complete. Missing or failed fields remain unverified.'
