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
    [IO.File]::WriteAllText('C:\nas\system\runtime\cloud-account-actions-phase.log',([string]$script:phase+' '+$method))
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
    if ($result.exceptionDetails) { if($script:phase -and $script:phase -ne 'login'){throw ('Browser evaluation failed at '+$script:phase+': '+$result.exceptionDetails.exception.description)};throw 'Browser evaluation failed; credentials withheld from output' }
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
    $fixtureScript=@'
(()=>{const original=window.fetch.bind(window);const reply=data=>new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});window.fetch=async(url,options={})=>{const path=new URL(url,location.origin).pathname;let data={};try{data=JSON.parse(options.body||'{}')}catch{}if(path==='/api/action'){if(data.action==='cloud-authorize')return reply({ok:true,session:'fixture',authorization_url:'https://openapi.alipan.com/oauth/authorize?sid=fixture',qr_image:"data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNDAiIGhlaWdodD0iMjQwIj48cmVjdCB3aWR0aD0iMjQwIiBoZWlnaHQ9IjI0MCIgZmlsbD0id2hpdGUiLz48cGF0aCBkPSJNMjAgMjBoNjB2NjBIMjB6Ii8+PC9zdmc+"});if(data.action==='cloud-authorize-poll'){sessionStorage.cloudState='verifying';return reply({ok:true,state:'authorized',account:{id:'fixture'}})}if(data.action==='cloud-authorize-cancel')return reply({ok:true});if(data.action==='cloud-auto'){sessionStorage.cloudAuto=data.enabled?'on':'off';return reply({ok:true,auto_backup:data.enabled})}if(data.action==='cloud-backup'){sessionStorage.lastBackupAccount=data.account||'all';sessionStorage.cloudBackup='running';return reply({ok:true,state:'running'})}if(data.action==='cloud-delete'){if(!data.confirmed)throw Error('Missing confirmation');sessionStorage.cloudRemoved='yes';sessionStorage.deleteCalls=String(Number(sessionStorage.deleteCalls||0)+1);return reply({ok:true})}if(data.action==='cloud-verify'){sessionStorage.cloudState='verifying';return reply({ok:true,state:'verifying'})}}const response=await original(url,options);if(path==='/api/status'&&response.ok){const result=await response.json();const state=sessionStorage.cloudState||'ready';result.cloud={prepared:true,state:state==='ready'?'configured':'login_required',accounts:sessionStorage.cloudRemoved==='yes'?[]:[{id:'fixture',name:'默认备注',state,auto_backup:sessionStorage.cloudAuto!=='off',backup:{state:sessionStorage.cloudBackup||'success',last_success:'2026-11-25T01:02:03+08:00'},profile:{nick_name:'演示昵称',user_name:'demo-user',user_id:'demo-id'}}]};return reply(result)}return response}})();
'@
    Invoke-CDP 'Page.addScriptToEvaluateOnNewDocument' @{source=$fixtureScript} | Out-Null
    $script:phase='flow'
    Navigate '/backups' "document.querySelector('#cloud-accounts').textContent.includes('演示昵称')"
    if ((Evaluate "document.querySelector('#backups-page').firstElementChild.classList.contains('cloud-backup-card')&&!document.querySelector('#cloud-auth-link')") -ne $true) {throw 'Order or removed link mismatch'}
    Evaluate "document.querySelector('#cloud-add').click()" | Out-Null
    Wait-Ready "!document.querySelector('#cloud-dialog').open && document.querySelector('.cloud-verification-control[aria-busy=true] button')?.disabled"
    if ((Evaluate "!!document.querySelector('.cloud-verification-control .operation-spinner')&&!document.querySelector('#cloud-dialog-status').textContent.includes('等待扫码')") -ne $true) {throw 'Automatic verification loading mismatch'}
    Invoke-CDP 'Page.reload' @{} | Out-Null
    Wait-Ready "document.querySelector('.cloud-verification-control[aria-busy=true] button')?.disabled===true"
    if ((Evaluate "document.querySelector('#cloud-accounts').textContent.includes('demo-user') && document.querySelector('#cloud-accounts').textContent.includes('demo-id')") -ne $true) {throw 'Cloud profile missing'}
    foreach ($width in @(1440,390)) {
        Invoke-CDP 'Emulation.setDeviceMetricsOverride' @{width=$width;height=950;deviceScaleFactor=1;mobile=($width -eq 390)} | Out-Null
        if ((Evaluate 'document.documentElement.scrollWidth>innerWidth+1') -eq $true) {throw 'Cloud verification overflow'}
        $shot=Invoke-CDP 'Page.captureScreenshot' @{format='png'}
        [IO.File]::WriteAllBytes((Join-Path $taskDir ('cloud-verifying-'+$width+'.png')),[Convert]::FromBase64String($shot.data))
    }
    $script:phase='ready'
    Evaluate "sessionStorage.cloudState='ready';refresh()" | Out-Null
    Wait-Ready "!document.querySelector('.cloud-verification-control button').disabled && !document.querySelector('.cloud-verification-control .operation-spinner')"
    $script:phase='timestamp'
    if ((Evaluate "document.querySelector('.cloud-last-backup').textContent.includes('01:02:03')") -ne $true) {throw 'Per-account second precision time missing'}
    $script:phase='toggle'
    Evaluate "document.querySelector('.cloud-auto-status').click()" | Out-Null
    Wait-Ready "document.querySelector('.cloud-auto-status').textContent==='自动备份已关闭'"
    Invoke-CDP 'Page.reload' @{} | Out-Null
    Wait-Ready "document.querySelector('.cloud-auto-status')?.getAttribute('aria-pressed')==='false'"
    $script:phase='bulk'
    Evaluate "document.querySelector('#cloud-now').click()" | Out-Null
    Wait-Ready "sessionStorage.lastBackupAccount==='all' && document.querySelector('#cloud-now').disabled"
    Evaluate "sessionStorage.cloudBackup='success';refresh()" | Out-Null
    Wait-Ready "!document.querySelector('#cloud-now').disabled"
    $script:phase='single'
    Evaluate "[...document.querySelectorAll('.cloud-account-actions button')].find(b=>b.textContent==='立即备份').click()" | Out-Null
    Wait-Ready "sessionStorage.lastBackupAccount==='fixture'"
    Evaluate "sessionStorage.cloudBackup='success';refresh()" | Out-Null
    Wait-Ready "!document.querySelector('#cloud-now').disabled"
    $script:phase='delete'
    Evaluate "[...document.querySelectorAll('.cloud-account-actions button')].find(b=>b.textContent==='删除').click()" | Out-Null
    if ((Evaluate "document.querySelector('#cloud-delete-dialog').open&&!sessionStorage.deleteCalls") -ne $true) {throw 'Delete skipped confirmation'}
    Evaluate "cancelCloudDelete()" | Out-Null
    if ((Evaluate "!document.querySelector('#cloud-delete-dialog').open&&!sessionStorage.deleteCalls") -ne $true) {throw 'Delete cancel sent request'}
    $script:phase='delete'
    Evaluate "[...document.querySelectorAll('.cloud-account-actions button')].find(b=>b.textContent==='删除').click();confirmCloudDelete()" | Out-Null
    Wait-Ready "sessionStorage.deleteCalls==='1' && !document.querySelector('#cloud-delete-dialog').open && !document.querySelector('.cloud-account')"
    Write-Output ('PASS: cloud flow, persisted states, per-account controls, bulk selection, second precision and delete confirmation; '+$taskDir)
} finally {
    if ($socket) {$socket.Dispose()}
    if ($process -and -not $process.HasExited) {Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue}
}
