# Isolated Windows Chrome profile: real browser CORS/API reads only; no sync configuration changes.
$ErrorActionPreference = 'Stop'
$browser = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$taskDir = 'C:\nas\system\runtime\connect-browser-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
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
    for ($attempt=0; $attempt -lt 120; $attempt++) {
        Start-Sleep -Milliseconds 200
        if ((Evaluate "typeof LocalSyncthing==='function' && !!window.nasConnection?.folder_id") -eq $true) { break }
    }
    if ((Evaluate "!!window.nasConnection?.folder_id") -ne $true) { throw 'Dashboard connection metadata did not become ready' }
    Evaluate "jumpToConnect();document.documentElement.style.scrollBehavior='auto'" | Out-Null
    [xml]$config = Get-Content 'C:\nas\system\state\syncthing\config.xml' -Raw
    $keyLiteral = ([string]$config.configuration.gui.apikey) | ConvertTo-Json -Compress
    $result = Evaluate "new LocalSyncthing(8384,$keyLiteral).api('system/status').then(s=>({ok:!!s.myID,form:!!document.querySelector('#local-key')})).catch(e=>({ok:false,error:e.message}))"
    if (-not $result.ok -or -not $result.form) { throw ('Browser localhost API check failed: '+$result.error) }
    $bound = Evaluate "document.querySelector('#local-key').value=$keyLiteral; authorizeLocal().then(()=>({readonly:document.querySelector('#local-new').hidden && !document.querySelector('#local-bound').hidden,path:document.querySelector('#local-current-path').textContent,message:document.querySelector('#connect-message').textContent}))"
    if (-not $bound.readonly -or -not $bound.path) { throw ('Existing binding UI check failed: '+$bound.message) }
    $history = Evaluate "loadHistory().then(()=>request('/api/sync/history')).then(d=>({total:d.total,rows:document.querySelector('#history-list').children.length,progress:document.querySelector('#sync-progress').textContent.length>0,conflicts:document.querySelector('#sync-conflicts').textContent.length>0}))"
    if ($history.rows -ne [Math]::Min(500,$history.total) -or -not $history.progress -or -not $history.conflicts) { throw 'History/progress panels did not render correctly' }
    Evaluate "document.querySelector('#app-syncthing').scrollIntoView({behavior:'instant'});document.querySelector('#device-connect').open=true" | Out-Null
    Start-Sleep -Milliseconds 200
    $shot = Invoke-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
    [IO.File]::WriteAllBytes("$taskDir\sync.png",[Convert]::FromBase64String($shot.data))
    Evaluate "document.documentElement.style.scrollBehavior='auto';window.scrollTo({top:0,behavior:'instant'})" | Out-Null
    Start-Sleep -Milliseconds 250
    $shot = Invoke-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
    [IO.File]::WriteAllBytes("$taskDir\desktop.png",[Convert]::FromBase64String($shot.data))
    $apps = Evaluate "(()=>{let original=submitAction,seen;submitAction=async(data)=>{seen=data};try{let row=document.querySelector('#app-knowledge');row.open=false;document.querySelector('[data-app-toggle=knowledge]').click();if(seen?.action!=='app-power'||seen?.app!=='knowledge'||seen?.enabled!==false||row.open)throw Error('Application toggle mismatch');let data=structuredClone(window.viewStatus.apps.knowledge);data.running=false;data.state='stopped';appPill(data,window.viewStatus).click();if(seen?.enabled!==true)throw Error('Stopped app toggle mismatch');let cards=[...document.querySelectorAll('.app-row')],width=document.querySelector('#modules').getBoundingClientRect().width;return {groups:cards.length,fullWidth:cards.every(x=>Math.abs(x.getBoundingClientRect().width-width)<2),autostart:document.querySelectorAll('input[data-module]').length,locked:document.querySelector('[data-app-toggle=platform]').disabled,noBottomLogs:!document.querySelector('#logs'),merged:document.querySelector('#app-syncthing').contains(document.querySelector('#sync-panel'))}}finally{submitAction=original}})()"
    if ($apps.groups -ne 6 -or -not $apps.fullWidth -or $apps.autostart -ne 0 -or -not $apps.locked -or -not $apps.merged -or -not $apps.noBottomLogs) { throw 'Application row layout mismatch' }
    $polish = Evaluate "(()=>{let rows=[...document.querySelectorAll('.app-row')],x=rows.map(r=>r.querySelector('.app-status-slot').getBoundingClientRect().x),tools=rows.map(r=>r.querySelector('.row-tools').getBoundingClientRect().x);return {aligned:Math.max(...x)-Math.min(...x)<2&&Math.max(...tools)-Math.min(...tools)<2,slots:rows.every(r=>r.querySelector('.row-tools').children.length===2),disabled:['syncthing','git','platform'].every(id=>document.querySelector('#app-'+id+' [data-action=open]').disabled),oneShared:document.querySelectorAll('#app-syncthing .permission-table tbody tr').length===1,noPrefix:![...document.querySelectorAll('.permission-table')].some(t=>t.textContent.includes('data/')),noInbox:!document.querySelector('#data-permissions').textContent.includes('inbox'),noDuplicates:!document.querySelector('.details-intro,.app-lifecycle,.system-detail-link'),logNav:!![...document.querySelectorAll('.side-nav a')].find(a=>a.getAttribute('href')==='/logs')}})()"
    if (-not $polish.aligned -or -not $polish.slots -or -not $polish.disabled -or -not $polish.oneShared -or -not $polish.noPrefix -or -not $polish.noInbox -or -not $polish.noDuplicates -or -not $polish.logNav) { throw 'Alignment / relative paths / consolidated permissions mismatch' }
    $hover = Evaluate "(()=>{let r=document.querySelector('[data-app-toggle=immich]').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()"
    Invoke-CDP 'Input.dispatchMouseEvent' @{type='mouseMoved';x=$hover.x;y=$hover.y} | Out-Null
    $tip = Evaluate "getComputedStyle(document.querySelector('#app-help-immich')).display==='block'&&document.querySelector('#app-help-immich').textContent===window.viewStatus.apps.immich.lifecycle"
    if (-not $tip) { throw 'Lifecycle hover hint missing' }
    $resources = Evaluate "(()=>{let d=window.viewStatus,cpu=document.querySelector('#resource-cpu .resource-gauge'),mem=document.querySelector('#resource-memory .resource-gauge'),db=document.querySelector('#immich-storage');return {cards:document.querySelectorAll('.resource-card').length,graphics:document.querySelectorAll('.resource-overview [role=img]').length,liveCpu:Math.abs(Number(cpu.dataset.percent)-d.resources.cpu.used_percent)<.01,liveMemory:Math.abs(Number(mem.dataset.percent)-d.resources.memory.used_percent)<.02,disks:document.querySelector('#resource-disks').children.length,databaseScoped:db.closest('.app-row').id==='app-immich'&&db.querySelectorAll('.app-storage-card').length===2&&!document.querySelector('#disk').textContent.includes('\u6570\u636e\u5e93'),renamed:d.apps.syncthing.name==='\u5171\u4eab\u540c\u6b65\u76d8',runtime:d.resources.scope==='nas_runtime'&&d.resources.uptime_seconds>0}})()"
    if ($resources.cards -ne 4 -or $resources.graphics -lt 6 -or -not $resources.liveCpu -or -not $resources.liveMemory -or $resources.disks -ne 2 -or -not $resources.databaseScoped -or -not $resources.renamed -or -not $resources.runtime) { throw 'NAS resource graphs or application storage scope mismatch' }
    $unknown = Evaluate "(()=>{let d=structuredClone(window.viewStatus);d.resources.cpu={state:'unavailable'};d.resources.memory={state:'unavailable'};d.disk.ssd={state:'unavailable'};dashboardStats(d);let ok=document.querySelector('#resource-cpu .resource-gauge').dataset.percent==='unknown'&&document.querySelector('#resource-memory .resource-gauge').dataset.percent==='unknown'&&document.querySelector('#resource-disks .resource-bar').dataset.percent==='unknown';dashboardStats(window.viewStatus);return ok})()"
    if (-not $unknown) { throw 'Missing resource sample fabricated zero usage' }
    $stable = Evaluate "(async()=>{let panel=document.querySelector('#sync-panel'),path=document.querySelector('#local-path');path.value='fixture-draft';await refresh();return panel===document.querySelector('#sync-panel')&&path===document.querySelector('#local-path')&&path.value==='fixture-draft'&&document.querySelector('#app-syncthing').open})()"
    if (-not $stable) { throw 'Refresh discarded expanded synchronization form' }
    $feature = Evaluate "(()=>{let original=window.viewStatus,fake=structuredClone(original);fake.apps.immich.state='stopped';fake.apps.immich.running=false;fake.apps.immich.features[0].available=false;renderApps(fake);let blocked=document.querySelector('[data-feature=intelligence]').disabled;renderApps(original);document.querySelector('#app-immich').open=true;document.querySelector('#app-immich').scrollIntoView({behavior:'instant'});return blocked})()"
    if (-not $feature) { throw 'Album feature enabled with stopped parent' }
    $shot = Invoke-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
    [IO.File]::WriteAllBytes("$taskDir\details.png",[Convert]::FromBase64String($shot.data))
    Evaluate "document.querySelector('#app-immich').open=false;document.querySelector('#app-syncthing').open=false;window.scrollTo({top:0,behavior:'instant'})" | Out-Null
    $shot = Invoke-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
    [IO.File]::WriteAllBytes("$taskDir\desktop.png",[Convert]::FromBase64String($shot.data))
    $permission = Evaluate "inspectPath('shared/new/nested/file.txt');document.querySelector('#path-permission').textContent.includes('Syncthing')"
    if (-not $permission) { throw 'New nested path ownership inheritance missing' }
    Invoke-CDP 'Emulation.setDeviceMetricsOverride' @{width=390;height=844;deviceScaleFactor=1;mobile=$true} | Out-Null
    Start-Sleep -Milliseconds 300
    $layout = Evaluate "({width:innerWidth,content:document.documentElement.scrollWidth,modules:document.querySelector('#modules').children.length})"
    if ($layout.content -gt $layout.width -or $layout.modules -lt 1) { throw 'Mobile layout overflow or missing modules' }
    $shot = Invoke-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
    [IO.File]::WriteAllBytes("$taskDir\mobile.png",[Convert]::FromBase64String($shot.data))
    Evaluate "document.querySelector('#app-files').open=true;document.querySelector('#app-files').scrollIntoView({behavior:'instant'})" | Out-Null
    $shot = Invoke-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
    [IO.File]::WriteAllBytes("$taskDir\mobile-details.png",[Convert]::FromBase64String($shot.data))
    Evaluate "document.querySelector('#app-files').open=false;document.querySelector('#storage').scrollIntoView({behavior:'instant'})" | Out-Null
    Start-Sleep -Milliseconds 200
    if ((Evaluate "document.documentElement.scrollWidth>innerWidth") -eq $true) { throw 'Storage ownership panel causes mobile overflow' }
    $shot = Invoke-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
    [IO.File]::WriteAllBytes("$taskDir\mobile-storage.png",[Convert]::FromBase64String($shot.data))
    Invoke-CDP 'Emulation.setDeviceMetricsOverride' @{width=1440;height=1000;deviceScaleFactor=1;mobile=$false} | Out-Null
    Evaluate "document.querySelector('#app-immich .row-tools').lastElementChild.click()" | Out-Null
    $popup = $null
    for ($attempt=0; $attempt -lt 50; $attempt++) {
        Start-Sleep -Milliseconds 200
        $popupPages = Invoke-RestMethod "http://127.0.0.1:$debugPort/json/list"
        $popup = $popupPages | Where-Object { $_.url -like '*logs?app=immich*' } | Select-Object -First 1
        if ($popup) { break }
    }
    if (-not $popup) { throw 'Log action did not open a separate window' }
    $parentSocket = $socket
    $socket = [Net.WebSockets.ClientWebSocket]::new()
    $socket.ConnectAsync([Uri]$popup.webSocketDebuggerUrl, [Threading.CancellationToken]::None).GetAwaiter().GetResult() | Out-Null
    for ($attempt=0; $attempt -lt 80; $attempt++) { Start-Sleep -Milliseconds 200; if ((Evaluate "document.querySelector('#log-output')?.dataset.loaded==='true'") -eq $true) { break } }
    if ((Evaluate "document.querySelector('#log-output')?.dataset.loaded==='true'") -ne $true) { throw 'Popup logs failed to load' }
    $logUI = Evaluate "({apps:document.querySelector('#log-app').options.length,tabs:document.querySelectorAll('[role=tab]').length,nav:!!document.querySelector('.side-nav')})"
    if ($logUI.apps -ne 6 -or $logUI.tabs -ne 5 -or -not $logUI.nav) { throw 'Log workspace selector or source tabs missing' }
    Evaluate "selectLogSource('immich-machine-learning')" | Out-Null
    for ($attempt=0; $attempt -lt 100; $attempt++) { Start-Sleep -Milliseconds 100; if ((Evaluate "document.querySelector('#log-output').dataset.loaded==='true'&&document.querySelector('#log-output').dataset.source==='immich-machine-learning'") -eq $true) { break } }
    if ((Evaluate "document.querySelector('#log-output').dataset.source==='immich-machine-learning'") -ne $true) { throw 'Intelligence log tab did not load' }
    if ((Evaluate "document.querySelector('#log-output').textContent.includes(String.fromCharCode(27))") -eq $true) { throw 'Terminal escape sequences polluted log display' }
    $shot = Invoke-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
    [IO.File]::WriteAllBytes("$taskDir\logs.png",[Convert]::FromBase64String($shot.data))
    Evaluate "selectLogSource('immich-server');selectLogApp('git')" | Out-Null
    for ($attempt=0; $attempt -lt 100; $attempt++) { Start-Sleep -Milliseconds 100; if ((Evaluate "document.querySelector('#log-output').dataset.loaded==='true'&&document.querySelector('#log-output').dataset.app==='git'") -eq $true) { break } }
    if ((Evaluate "document.querySelector('#log-output').dataset.app==='git'&&document.querySelectorAll('[role=tab]').length===2") -ne $true) { throw 'Application log switch displayed stale results' }
    Invoke-CDP 'Page.close' @{} | Out-Null
    $socket.Dispose()
    $socket = $parentSocket
    Invoke-CDP 'Page.navigate' @{url='http://127.0.0.1:8080/system?app=immich'} | Out-Null
    for ($attempt=0; $attempt -lt 120; $attempt++) { Start-Sleep -Milliseconds 200; if ((Evaluate "typeof systemData!=='undefined' && systemData?.containers.length===11 && document.querySelectorAll('#system-rows tr').length===4") -eq $true) { break } }
    $system = Evaluate "({rows:document.querySelectorAll('#system-rows tr').length,sampled:systemData?.containers.every(r=>r.state!=='running'||(r.cpu_percent!==null&&r.memory_bytes!==null&&r.uptime_seconds>=0)),mounts:systemData?.containers.some(r=>r.mounts.length>0)})"
    if ($system.rows -ne 4 -or -not $system.sampled -or -not $system.mounts) { throw 'System table missing real metrics or application filtering' }
    Evaluate "document.querySelector('#system-filter').value='';renderSystem()" | Out-Null
    if ((Evaluate "document.querySelectorAll('#system-rows tr').length") -ne 11) { throw 'System all-application filter mismatch' }
    $shot = Invoke-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
    [IO.File]::WriteAllBytes("$taskDir\system.png",[Convert]::FromBase64String($shot.data))
    Invoke-CDP 'Emulation.setDeviceMetricsOverride' @{width=390;height=844;deviceScaleFactor=1;mobile=$true} | Out-Null
    Start-Sleep -Milliseconds 250
    if ((Evaluate "document.documentElement.scrollWidth>innerWidth") -eq $true) { throw 'System table causes mobile page overflow' }
    $shot = Invoke-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
    [IO.File]::WriteAllBytes("$taskDir\mobile-system.png",[Convert]::FromBase64String($shot.data))
    Invoke-CDP 'Page.navigate' @{url='http://127.0.0.1:8080/logs'} | Out-Null
    for ($attempt=0; $attempt -lt 100; $attempt++) { Start-Sleep -Milliseconds 150; if ((Evaluate "document.querySelector('#log-output')?.dataset.loaded==='true'") -eq $true) { break } }
    if ((Evaluate "document.querySelector('#log-app').value==='immich'&&document.documentElement.scrollWidth<=innerWidth") -ne $true) { throw 'Log navigation default selection or mobile layout failed' }
    $shot = Invoke-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
    [IO.File]::WriteAllBytes("$taskDir\mobile-logs.png",[Convert]::FromBase64String($shot.data))
    Invoke-CDP 'Emulation.setDeviceMetricsOverride' @{width=1440;height=1000;deviceScaleFactor=1;mobile=$false} | Out-Null
    Evaluate "window.scrollTo({top:0,behavior:'instant'})" | Out-Null
    Start-Sleep -Milliseconds 200
    $shot = Invoke-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
    [IO.File]::WriteAllBytes("$taskDir\logs-page.png",[Convert]::FromBase64String($shot.data))
    Write-Output ("Screenshots: " + $taskDir)
    Evaluate "fetch('/api/logout',{method:'POST',headers:{'Content-Type':'application/json','X-NAS-Request':'1'},body:'{}'}).then(r=>r.ok)" | Out-Null
    $report = @{state='PASS'; checks=@('localhost Syncthing authorization and existing binding protection','single full-width application rows','logical app power and parent-constrained feature controls','expanded sync form and draft survive refresh','sync history and conflict panels retained inside sync application','independent real log window loads authenticated logs','system page displays eleven containers with actual CPU memory uptime mounts','application filter displays four Immich containers','desktop/mobile screenshots with no page overflow','aligned action columns with disabled placeholders','relative paths and one inherited shared rule','four real NAS resource panels with accessible graphs','unavailable samples stay unknown','database capacity scoped to Immich details','lifecycle hover hint replaces duplicate details','log application selector and service tabs with stale response protection'); limitations=@('Windows Chrome; physical Mac layout not exercised'); profile=$taskDir}
    $report | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 "$taskDir\result.json"
    Write-Output 'PASS: isolated Chrome cross-origin localhost authorization; no sync configuration changed'
    Invoke-CDP 'Browser.close' @{} | Out-Null
} finally {
    if ($socket) { $socket.Dispose() }
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -ErrorAction SilentlyContinue }
}
