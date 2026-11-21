# Isolated Windows Chrome profile: real browser CORS/API reads only; no sync configuration changes.
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
    Invoke-CDP 'Page.navigate' @{url='http://127.0.0.1:8080/'} | Out-Null
    for ($attempt=0; $attempt -lt 120; $attempt++) {
        Start-Sleep -Milliseconds 200
        if ((Evaluate "location.origin==='http://127.0.0.1:8080' && document.readyState==='complete'") -eq $true) { break }
    }
    $password = (Get-Content 'C:\nas\system\secrets\admin-password.txt' -Raw).Trim()
    $payload = @{username='admin'; password=$password} | ConvertTo-Json -Compress
    $literal = $payload | ConvertTo-Json -Compress
    $login = Evaluate "fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json','X-NAS-Request':'1'},body:$literal}).then(r=>r.json())"
    if (-not $login.ok) { throw 'Test browser login failed' }
    Invoke-CDP 'Page.reload' @{} | Out-Null
    function Wait-Ready($expression) {
        for ($i=0; $i -lt 120; $i++) { Start-Sleep -Milliseconds 150; if ((Evaluate $expression) -eq $true) { return } }
        throw ('Page readiness timed out: ' + $expression)
    }
    function Navigate($path,$expression) { Invoke-CDP 'Page.navigate' @{url=('http://127.0.0.1:8080'+$path)} | Out-Null; Wait-Ready $expression }
    function Check-Navigation($path) {
        if ((Evaluate "JSON.stringify([...document.querySelectorAll('.side-nav a')].map(a=>new URL(a.href).pathname))===JSON.stringify(['/apps','/backups','/containers','/logs']) && document.querySelectorAll('.side-nav [aria-current=page]').length===1 && new URL(document.querySelector('.side-nav [aria-current=page]').href).pathname==='$path'") -ne $true) { throw 'Four-page navigation mismatch' }
    }
    function Screenshot($name) { $shot=Invoke-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}; [IO.File]::WriteAllBytes("$taskDir\$name.png",[Convert]::FromBase64String($shot.data)) }
    Wait-Ready "document.querySelectorAll('#modules .app-row').length===6 && !document.querySelector('#message').textContent"
    Check-Navigation '/apps'
    if ((Evaluate "/^\d{2,}:\d{2}:\d{2}$/.test(document.querySelector('#resource-uptime').textContent) && !document.querySelector('.compact-resources svg') && document.querySelector('.compact-resources').getBoundingClientRect().height<260 && !document.querySelector('#data-permissions,#permission-path,#backup-now,#observation')") -ne $true) { throw 'Compact resource or removed panels mismatch' }
    $before = Evaluate "document.querySelector('#resource-uptime').textContent"
    Start-Sleep -Milliseconds 2200
    if ((Evaluate "document.querySelector('#resource-uptime').textContent") -eq $before) { throw 'Uptime does not tick' }
    Screenshot 'apps-desktop'
    Evaluate "appViews.git.card.open=true;document.querySelector('#app-git').scrollIntoView({block:'center'})" | Out-Null
    if ((Evaluate "document.querySelector('#git-details').textContent.includes('SSH \u516c\u94a5') && ![...document.querySelectorAll('.permission-table th')].some(x=>x.textContent==='\u53ea\u8bfb\u8bbf\u95ee')") -ne $true) { throw 'Git info or write ownership details mismatch' }
    Screenshot 'git-details'
    $draft = Evaluate "(async()=>{appViews.syncthing.card.open=true;document.querySelector('#device-connect').open=true;document.querySelector('#local-path').value='~/NAS/draft';await refresh();return document.querySelector('#local-path').value==='~/NAS/draft'&&document.querySelector('#device-connect').open&&appViews.syncthing.card.open})()"
    if (-not $draft) { throw 'Sync binding draft or expanded state lost on refresh' }
    Navigate '/backups' "document.querySelector('#mirror-details')?.children.length>0"
    Check-Navigation '/backups'
    if ((Evaluate "!document.querySelector('.resource-overview,#disk,#data-permissions') && document.querySelector('#mirror-details').textContent.includes('\u5b8c\u6574\u5386\u53f2')") -ne $true) { throw 'Backup page contains redundant panels or missing history' }
    Screenshot 'backups-desktop'
    $choice = Evaluate "(async()=>{window.choiceCalls=[];submitAction=async d=>choiceCalls.push(d);await act('backup');let dialog=document.querySelector('#backup-choice');if(!dialog.open||choiceCalls.length)return false;cancelBackup();if(choiceCalls.length)return false;await act('backup');await confirmBackup(false);await act('hdd-backup');await confirmBackup(true);return choiceCalls.length===2&&choiceCalls[0].purge_history===false&&choiceCalls[1].purge_history===true})()"
    if (-not $choice) { throw 'Explicit history choices failed' }
    Navigate '/containers' "document.querySelectorAll('#system-rows tr').length===11"
    Check-Navigation '/containers'
    Evaluate "document.querySelector('#system-filter').value='immich';renderSystem()" | Out-Null
    if ((Evaluate "document.querySelectorAll('#system-rows tr').length===4") -ne $true) { throw 'Container application filter failed' }
    Screenshot 'containers-desktop'
    Navigate '/logs' "document.querySelector('#log-output')?.dataset.loaded==='true'"
    Check-Navigation '/logs'
    Evaluate "selectLogApp('git')" | Out-Null
    Wait-Ready "document.querySelector('#log-output').dataset.loaded==='true' && document.querySelector('#log-output').dataset.app==='git'"
    if ((Evaluate "document.querySelectorAll('#log-tabs [role=tab]').length===2") -ne $true) { throw 'Log sources were not preserved' }
    Screenshot 'logs-desktop'
    Invoke-CDP 'Emulation.setDeviceMetricsOverride' @{width=390;height=844;deviceScaleFactor=1;mobile=$true} | Out-Null
    foreach ($entry in @(@('/apps',"document.querySelectorAll('#modules .app-row').length===6"),@('/backups',"document.querySelector('#mirror-details')?.children.length>0"),@('/containers',"document.querySelectorAll('#system-rows tr').length===11"),@('/logs',"document.querySelector('#log-output')?.dataset.loaded==='true'"))) {
        Navigate $entry[0] $entry[1]
        Check-Navigation $entry[0]
        if ((Evaluate "document.documentElement.scrollWidth<=innerWidth") -ne $true) { throw ('Mobile overflow '+$entry[0]) }
        Screenshot ($entry[0].Substring(1)+'-mobile')
    }
    Evaluate "fetch('/api/logout',{method:'POST',headers:{'Content-Type':'application/json','X-NAS-Request':'1'},body:'{}'}).then(r=>r.ok)" | Out-Null
    @{state='PASS';checks=@('four independent routes and navigation','compact resources and ticking uptime','Git details and write-only ownership','sync draft survives refresh','dedicated backup and explicit retention choices','container filter','real logs from stopped app','desktop/mobile no page overflow');screenshots=$taskDir;no_application_state_changed=$true} | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 "$taskDir\result.json"
    Write-Output ('PASS: four-page Chrome acceptance. Screenshots: '+$taskDir)
    Invoke-CDP 'Browser.close' @{} | Out-Null
} finally {
    if ($socket) { $socket.Dispose() }
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -ErrorAction SilentlyContinue }
}
