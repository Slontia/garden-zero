# Isolated browser layout regression; QR opening uses a local response fixture and does not alter account authorization.
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
        throw ('Page readiness timed out: ' + $expression + ' message: ' + (Evaluate "document.querySelector('#message')?.textContent"))
    }
    function Navigate($path,$expression) { Invoke-CDP 'Page.navigate' @{url=('http://127.0.0.1:8080'+$path)} | Out-Null; Wait-Ready $expression }
    $styles=@{}
    foreach ($path in @('/apps','/files','/backups')) {
        Navigate $path "!!document.querySelector('.hero.page-header .page-heading h1')"
        $styles[$path]=Evaluate "(()=>{let t=document.querySelector('.hero h1'),p=document.querySelector('.hero .muted'),h=document.querySelector('.hero');return JSON.stringify([getComputedStyle(t).fontSize,getComputedStyle(t).lineHeight,getComputedStyle(t).marginBottom,getComputedStyle(p).fontSize,getComputedStyle(h).marginTop])})()"
    }
    if ($styles['/apps'] -ne $styles['/files'] -or $styles['/apps'] -ne $styles['/backups']) {throw 'Heading styles mismatch'}
    Wait-Ready "document.querySelector('#disk-history').children.length>0"
    if ((Evaluate "!document.querySelector('#backup_schedule')&&!document.querySelector('#snapshot-details')&&document.querySelectorAll('#disk-history details[open]').length===0&&document.querySelector('#disk-history').textContent.includes('nas-backup')") -ne $true) {throw 'Disk history layout mismatch'}
    foreach ($width in @(1440,390)) {
        Invoke-CDP 'Emulation.setDeviceMetricsOverride' @{width=$width;height=950;deviceScaleFactor=1;mobile=($width -eq 390)} | Out-Null
        if ((Evaluate 'document.documentElement.scrollWidth>innerWidth+1') -eq $true) {throw 'Backup page horizontal overflow'}
        $shot=Invoke-CDP 'Page.captureScreenshot' @{format='png'}
        [IO.File]::WriteAllBytes((Join-Path $taskDir ('backups-'+$width+'.png')),[Convert]::FromBase64String($shot.data))
    }
    Evaluate "window.originalRequest=request;request=async(url,data)=>data?.action==='cloud-authorize'?{session:'browser-fixture',qr_image:'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNDAiIGhlaWdodD0iMjQwIj48cmVjdCB3aWR0aD0iMjQwIiBoZWlnaHQ9IjI0MCIgZmlsbD0id2hpdGUiLz48cGF0aCBkPSJNMjAgMjBoNjB2NjBIMjB6TTE2MCAyMGg2MHY2MGgtNjB6TTIwIDE2MGg2MHY2MEgyMHoiLz48L3N2Zz4=',authorization_url:'https://openapi.alipan.com/oauth/authorize?sid=fixture'}:data?.action==='cloud-authorize-cancel'?{state:'cancelled'}:originalRequest(url,data);document.querySelector('#cloud-add').click()" | Out-Null

    Wait-Ready "!document.querySelector('#cloud-qr-panel').hidden && document.querySelector('#cloud-qr').complete && document.querySelector('#cloud-qr').naturalWidth>0"
    if ((Evaluate "document.querySelector('#cloud-form').hidden && document.querySelector('#cloud-dialog').open") -ne $true) {throw 'Direct QR opening failed'}
    Evaluate "closeCloudAccount();request=originalRequest" | Out-Null
    Write-Output ('PASS: unified titles, disk history and desktop/mobile layouts, direct QR fixture; '+$taskDir)
} finally {
    if ($socket) {$socket.Dispose()}
    if ($process -and -not $process.HasExited) {Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue}
}
