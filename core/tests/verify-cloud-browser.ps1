# Opt-in isolated Chrome test: creates/cancels a QR authorization session, without scanning or binding a real account.
# Run when no user authorization is in progress; the server currently has one pending QR session.
$ErrorActionPreference = 'Stop'
$browser = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$taskDir = 'C:\nas\system\runtime\meta-browser-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Path $taskDir | Out-Null
$debugPort = 19223
$process = $null
$socket = $null
$script:sequence = 0
function Invoke-CDP($method, $parameters) {
    $script:sequence++
    $id = $script:sequence
    $bytes = [Text.Encoding]::UTF8.GetBytes((@{id=$id; method=$method; params=$parameters} | ConvertTo-Json -Depth 20 -Compress))
    $socket.SendAsync([ArraySegment[byte]]::new($bytes), [Net.WebSockets.WebSocketMessageType]::Text, $true, [Threading.CancellationToken]::None).GetAwaiter().GetResult() | Out-Null
    do {
        $stream = [IO.MemoryStream]::new()
        do {
            $buffer = New-Object byte[] 65536
            $received = $socket.ReceiveAsync([ArraySegment[byte]]::new($buffer), [Threading.CancellationToken]::None).GetAwaiter().GetResult()
            $stream.Write($buffer, 0, $received.Count)
        } while (-not $received.EndOfMessage)
        $message = [Text.Encoding]::UTF8.GetString($stream.ToArray()) | ConvertFrom-Json
        $stream.Dispose()
    } while ($message.id -ne $id)
    if ($message.error) { throw ('Browser protocol refused ' + $method) }
    return $message.result
}
function Evaluate($expression) {
    $result = Invoke-CDP 'Runtime.evaluate' @{expression=$expression; awaitPromise=$true; returnByValue=$true; userGesture=$true}
    if ($result.exceptionDetails) { throw 'Browser evaluation failed; credentials withheld from output' }
    return $result.result.value
}
try {
    try { Invoke-RestMethod "http://127.0.0.1:$debugPort/json/version" -TimeoutSec 1 | Out-Null; throw 'Debug port occupied' }
    catch { if ($_.Exception.Message -eq 'Debug port occupied') { throw } }
    $process = Start-Process -FilePath $browser -ArgumentList @('--headless=new','--no-first-run','--no-default-browser-check',"--remote-debugging-port=$debugPort", "--user-data-dir=$taskDir\profile",'about:blank') -PassThru
    $pages = $null
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        try { $pages = Invoke-RestMethod "http://127.0.0.1:$debugPort/json/list" -TimeoutSec 2; break } catch { Start-Sleep -Milliseconds 300 }
    }
    if (-not $pages) { throw 'Isolated browser did not start' }
    $page = $pages | Where-Object { $_.type -eq 'page' } | Select-Object -First 1
    $socket = [Net.WebSockets.ClientWebSocket]::new()
    $socket.ConnectAsync([Uri]$page.webSocketDebuggerUrl, [Threading.CancellationToken]::None).GetAwaiter().GetResult() | Out-Null
    Invoke-CDP 'Page.enable' @{} | Out-Null
    Invoke-CDP 'Emulation.setDeviceMetricsOverride' @{width=1440;height=1000;deviceScaleFactor=1;mobile=$false} | Out-Null
    Invoke-CDP 'Page.navigate' @{url='http://127.0.0.1:8080/login/local'} | Out-Null
    for ($attempt=0; $attempt -lt 120; $attempt++) {
        Start-Sleep -Milliseconds 200
        if ((Evaluate "location.origin==='http://127.0.0.1:8080' && document.readyState==='complete'") -eq $true) { break }
    }
    if ((Evaluate "(()=>{let inputs=[...document.querySelectorAll('#login input')].filter(input=>input.type!=='hidden');return inputs.length===1&&inputs[0].name==='password'&&inputs[0].type==='password'})()") -ne $true) { throw 'Single-user login must only display the password field' }
    $password = (Get-Content 'C:\nas\system\secrets\admin-password.txt' -Raw).Trim()
    $payload = @{password=$password} | ConvertTo-Json -Compress
    $literal = $payload | ConvertTo-Json -Compress
    $login = Evaluate "fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json','X-NAS-Request':'1'},body:$literal}).then(r=>r.json())"
    if (-not $login.ok) { throw 'Test browser login failed' }
    Invoke-CDP 'Page.navigate' @{url='http://127.0.0.1:8080/'} | Out-Null
    function Wait-Ready($expression) {
        for ($i=0; $i -lt 120; $i++) { Start-Sleep -Milliseconds 150; if ((Evaluate $expression) -eq $true) { return } }
        throw ('Page readiness timed out: ' + $expression)
    }
    function Navigate($path,$expression) { Invoke-CDP 'Page.navigate' @{url=('http://127.0.0.1:8080'+$path)} | Out-Null; Wait-Ready $expression }
    Navigate '/backups' "document.querySelector('#cloud-add') && !document.querySelector('#cloud-add').disabled"
    if ((Evaluate "!!document.querySelector('#disk-history') && !document.querySelector('#backup_schedule') && !document.querySelector('#snapshot-details')") -ne $true) {throw 'Backup layout mismatch'}
    foreach ($width in @(1440,390)) {
        Invoke-CDP 'Emulation.setDeviceMetricsOverride' @{width=$width;height=950;deviceScaleFactor=1;mobile=($width -eq 390)} | Out-Null
        if ((Evaluate 'document.documentElement.scrollWidth>innerWidth+1') -eq $true) {throw 'Backup page horizontal overflow'}
        $shot=Invoke-CDP 'Page.captureScreenshot' @{format='png'}
        [IO.File]::WriteAllBytes((Join-Path $taskDir ('cloud-'+$width+'.png')),[Convert]::FromBase64String($shot.data))
    }
    Evaluate "document.querySelector('#cloud-add').click()" | Out-Null
    Wait-Ready "document.querySelector('#cloud-qr').complete && document.querySelector('#cloud-qr').naturalWidth>0"
    if ((Evaluate "document.querySelector('#cloud-dialog').open && !document.querySelector('#cloud-qr-panel').hidden && document.documentElement.scrollWidth<=innerWidth+1") -ne $true) {throw 'QR dialog rendering mismatch'}
    if ((Evaluate "!document.querySelector('#cloud-auth-link') && document.querySelector('#cloud-qr').naturalWidth>0 && document.querySelector('#cloud-dialog').textContent.includes('OpenList')") -ne $true) {throw 'Authorization origin/disclosure mismatch'}
    Evaluate "closeCloudAccount()" | Out-Null
    Wait-Ready "!document.querySelector('#cloud-dialog').open && !document.querySelector('#cloud-qr').hasAttribute('src')"

    Write-Output ('PASS: backup desktop/mobile and real QR authorization/cancel; '+$taskDir)
} finally {
    if ($socket) {$socket.Dispose()}
    if ($process -and -not $process.HasExited) {Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue}
}
