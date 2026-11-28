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
    Navigate '/backups' "!!document.querySelector('#cloud-add')&&!!window.viewStatus"
    Wait-Ready "!!window.viewStatus?.cloud"
    if ((Evaluate "!document.querySelector('#backup-start-time')&&!document.querySelector('#backup-max-pause')&&!document.querySelector('#backup-timezone')&&!document.querySelector('#backup-settings-form')&&!document.querySelector('#cloud-now')&&!document.querySelector('#backup-interrupt')&&![...document.querySelectorAll('#backups-page button')].some(b=>/\u7acb\u5373\u5907\u4efd|\u505c\u6b62\u5907\u4efd/.test(b.textContent))&&document.querySelector('#cloud-add').closest('.card-heading')===document.querySelector('#cloud-backup-heading').parentElement") -ne $true) {throw 'Online backup must have no schedule or pause settings'}
    if ((Evaluate "(()=>{const original=window.viewStatus;window.viewStatus={...original,backup_control:{operation:{kind:'cloud',state:'running',phase:'checking'}}};renderCloud({...original.cloud,prepared:true,accounts:(original.cloud.accounts||[]).map(a=>({...a,backup:{...a.backup,state:'running',phase:'checking'}}))},false);const ok=!document.querySelector('#cloud-add').disabled;window.viewStatus=original;renderCloud(original.cloud,!!original.storage_blocked);return ok})()") -ne $true) {throw 'Adding a cloud account was blocked by repository checking'}
    if ((Evaluate "(()=>{const original=window.viewStatus;renderCloud({prepared:true,accounts:[{id:'fixture-warning',name:'Fixture',state:'failed',error:'Connection fixture warning',backup:{}}]},false);const warning=document.querySelector('.cloud-warning button'),ok=!!warning&&warning.getAttribute('aria-label').includes('Connection fixture warning')&&![...document.querySelectorAll('#cloud-accounts button')].some(b=>/\u91cd\u65b0\u9a8c\u8bc1|\u9a8c\u8bc1\u8fde\u63a5|\u91cd\u65b0\u6388\u6743/.test(b.textContent));window.oldOpenCloudAccount=openCloudAccount;window.warningActivated=false;openCloudAccount=account=>{window.warningActivated=account.id==='fixture-warning'};warning.click();openCloudAccount=oldOpenCloudAccount;const clicked=window.warningActivated;renderCloud(original.cloud,!!original.storage_blocked);return ok&&clicked})()") -ne $true) {throw 'Cloud warning icon must replace manual verification and authorization buttons'}
    if ((Evaluate "(()=>{const original=window.viewStatus;renderCloud({prepared:true,accounts:[{id:'fixture-active',name:'Fixture',state:'ready',backup:{state:'running'}}]},false);const button=[...document.querySelectorAll('#cloud-accounts button')].find(b=>b.textContent==='\u5220\u9664');const ok=!!button&&!button.disabled;renderCloud(original.cloud,!!original.storage_blocked);return ok})()") -ne $true) {throw 'Delete button must remain available during backups'}
    # Only validation failures are sent: neither request changes configuration or starts/stops apps.
    if ((Evaluate "fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-NAS-Request':'1'},body:JSON.stringify({action:'backup-settings',time:'25:00',max_pause_minutes:15})}).then(r=>r.status===400)") -ne $true) {throw 'Invalid backup schedule was accepted'}
    if ((Evaluate "fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-NAS-Request':'1'},body:JSON.stringify({action:'backup-cancel',job:'browser-fixture'})}).then(r=>r.status===400)") -ne $true) {throw 'Unknown backup cancellation was accepted'}
    if ((Evaluate "!document.querySelector('.backup-subsection')&&(!document.querySelector('.cloud-account')||getComputedStyle(document.querySelector('.cloud-account')).borderTopWidth==='0px')") -ne $true) {throw 'Unwanted section dividers remain'}
    $styles=@{}
    foreach ($path in @('/apps','/files','/backups')) {
        Navigate $path "!!document.querySelector('.hero.page-header .page-heading h1')"
        $styles[$path]=Evaluate "(()=>{let t=document.querySelector('.hero h1'),p=document.querySelector('.hero .muted'),h=document.querySelector('.hero');return JSON.stringify([getComputedStyle(t).fontSize,getComputedStyle(t).lineHeight,getComputedStyle(t).marginBottom,getComputedStyle(p).fontSize,getComputedStyle(h).marginTop])})()"
    }
    if ($styles['/apps'] -ne $styles['/files'] -or $styles['/apps'] -ne $styles['/backups']) {throw 'Heading styles mismatch'}
    Wait-Ready "!!window.viewStatus&&!!document.querySelector('#backup-history')"
    if ((Evaluate "document.querySelectorAll('#backups-page>section').length===2&&!document.querySelector('#mirror-now')&&!document.querySelector('#backup-choice')&&!document.querySelector('#cloud-list-heading')&&!document.querySelector('#backup-schedule-heading')") -ne $true) {throw 'Backup workspace sections mismatch'}
    if ((Evaluate "(()=>{const original=window.viewStatus.backup_history;const fixture=[{id:'fixture-cloud',type:'cloud',state:'failed',target:{id:'cloud-only',name:'Cloud fixture'}},...Array.from({length:25},(_,i)=>({id:'fixture-disk-'+i,type:'disk',state:i===0?'failed':'success',started:'2026-11-27T00:00:00Z',target:{uid:'fixture-uid',name:'Disk fixture',relative_path:'nas-backup'}}))];renderBackupHistory(fixture);let ok=document.querySelectorAll('#backup-history>tr').length===20&&![...document.querySelectorAll('#backup-history>tr')].some(r=>r.dataset.kind!=='disk')&&!document.querySelector('#history-target').textContent.includes('Cloud fixture')&&!document.querySelector('#history-kind');changeHistoryPage(1);ok=ok&&document.querySelectorAll('#backup-history>tr').length===5;document.querySelector('#history-state').value='failed';filterBackupHistory();ok=ok&&document.querySelectorAll('#backup-history>tr').length===1&&document.querySelector('#backup-history').textContent.includes('fixture-uid');document.querySelector('#history-state').value='';document.querySelector('#history-target').value='';historyPage=1;renderBackupHistory(original);return ok})()") -ne $true) {throw 'Disk-only history filtering or pagination failed'}
    if ((Evaluate "fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-NAS-Request':'1'},body:JSON.stringify({action:'hdd-backup'})}).then(r=>r.status===400)") -ne $true) {throw 'Disk backup must not be callable from web'}
    foreach ($width in @(1440,390)) {
        Invoke-CDP 'Emulation.setDeviceMetricsOverride' @{width=$width;height=950;deviceScaleFactor=1;mobile=($width -eq 390)} | Out-Null
        Invoke-CDP 'Emulation.setFocusEmulationEnabled' @{enabled=$true} | Out-Null
        if ((Evaluate "(()=>{let help=document.querySelector('.backup-help-trigger');help.blur();help.focus();let tip=help.nextElementSibling,rect=tip.getBoundingClientRect(),ok=getComputedStyle(tip).display!=='none'&&rect.left>=0&&rect.right<=innerWidth;window.backupTooltipDebug={display:getComputedStyle(tip).display,left:rect.left,right:rect.right,width:innerWidth,position:getComputedStyle(tip).position,active:document.activeElement===help,focused:help.matches(':focus'),within:help.parentElement.matches(':focus-within'),dismissedBefore:help.parentElement.dataset.dismissed};help.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));window.backupTooltipDebug.dismissed=getComputedStyle(tip).display;ok=ok&&getComputedStyle(tip).display==='none';help.blur();return ok})()") -ne $true) {throw ('Information tooltip: '+(Evaluate 'JSON.stringify(window.backupTooltipDebug)'))}
        if ((Evaluate 'document.documentElement.scrollWidth>innerWidth+1') -eq $true) {throw 'Backup page horizontal overflow'}
        $shot=Invoke-CDP 'Page.captureScreenshot' @{format='png'}
        [IO.File]::WriteAllBytes((Join-Path $taskDir ('backups-'+$width+'.png')),[Convert]::FromBase64String($shot.data))
    }
    Evaluate "window.originalRequest=request;request=async(url,data)=>data?.action==='cloud-authorize'?{session:'browser-fixture',qr_image:'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNDAiIGhlaWdodD0iMjQwIj48cmVjdCB3aWR0aD0iMjQwIiBoZWlnaHQ9IjI0MCIgZmlsbD0id2hpdGUiLz48cGF0aCBkPSJNMjAgMjBoNjB2NjBIMjB6TTE2MCAyMGg2MHY2MGgtNjB6TTIwIDE2MGg2MHY2MEgyMHoiLz48L3N2Zz4=',authorization_url:'https://openapi.alipan.com/oauth/authorize?sid=fixture'}:data?.action==='cloud-authorize-cancel'?{state:'cancelled'}:originalRequest(url,data);openCloudAccount()" | Out-Null

    Wait-Ready "!document.querySelector('#cloud-qr-panel').hidden && document.querySelector('#cloud-qr').complete && document.querySelector('#cloud-qr').naturalWidth>0"
    if ((Evaluate "document.querySelector('#cloud-form').hidden && document.querySelector('#cloud-dialog').open") -ne $true) {throw 'Direct QR opening failed'}
    Evaluate "closeCloudAccount();request=originalRequest" | Out-Null
    Write-Output ('PASS: unified titles, online backup controls, disk-only history filters/pagination, script-only disk actions, tooltips and desktop/mobile layouts, direct QR fixture; '+$taskDir)
} finally {
    if ($socket) {$socket.Dispose()}
    if ($process -and -not $process.HasExited) {Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue}
}
