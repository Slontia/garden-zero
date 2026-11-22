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
    $script:headingStyles = @{}
    function Check-Navigation($path) {
        if ($path -eq '/apps') {
            if ((Evaluate "[...document.querySelectorAll('.app-title-line')].length===6&&[...document.querySelectorAll('.app-title-line')].every(line=>{let title=line.querySelector('h3').getBoundingClientRect(),identity=line.querySelector('.app-identity').getBoundingClientRect();return Math.abs((title.top+title.bottom-identity.top-identity.bottom)/2)<2&&identity.left>=title.right&&line.nextElementSibling.getBoundingClientRect().top>=line.getBoundingClientRect().bottom})") -ne $true) { throw 'Application title, ID and icons are not on the same line' }
        }
        $heading = Evaluate "(()=>{let hero=document.querySelector('.hero.page-header'),title=hero?.querySelector('.page-heading > h1'),description=hero?.querySelector('.page-heading > p');if(!title||!description||document.querySelectorAll('h1').length!==1||hero.querySelector('.eyebrow,h1 span'))throw Error('Heading structure mismatch');let a=getComputedStyle(hero),b=getComputedStyle(title),c=getComputedStyle(description);return {viewport:innerWidth,styles:JSON.stringify([a.marginTop,a.marginBottom,b.fontSize,b.fontWeight,b.lineHeight,b.marginBottom,c.fontSize,c.lineHeight]),left:title.getBoundingClientRect().left}})()"
        $key = [string]$heading.viewport
        if ($script:headingStyles.ContainsKey($key) -and $script:headingStyles[$key] -ne $heading.styles) { throw ('Inconsistent page heading style: ' + $path) }
        $script:headingStyles[$key] = $heading.styles
        if ((Evaluate "JSON.stringify([...document.querySelectorAll('.side-nav a')].map(a=>new URL(a.href).pathname))===JSON.stringify(['/apps','/backups','/containers','/logs']) && document.querySelectorAll('.side-nav [aria-current=page]').length===1 && new URL(document.querySelector('.side-nav [aria-current=page]').href).pathname==='$path'") -ne $true) { throw 'Four-page navigation mismatch' }
    }
    function Screenshot($name) { $shot=Invoke-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}; [IO.File]::WriteAllBytes("$taskDir\$name.png",[Convert]::FromBase64String($shot.data)) }
    Wait-Ready "document.querySelectorAll('#modules .app-row').length===6 && !document.querySelector('#message').textContent"
    Check-Navigation '/apps'
    if ((Evaluate "/^\d{2,}:\d{2}:\d{2}$/.test(document.querySelector('#resource-uptime').textContent) && !document.querySelector('.compact-resources svg') && document.querySelector('.compact-resources').getBoundingClientRect().height<260 && !document.querySelector('#data-permissions,#permission-path,#backup-now,#observation')") -ne $true) { throw 'Compact resource or removed panels mismatch' }
    $before = Evaluate "document.querySelector('#resource-uptime').textContent"
    Start-Sleep -Milliseconds 2200
    if ((Evaluate "document.querySelector('#resource-uptime').textContent") -eq $before) { throw 'Uptime does not tick' }
    if ((Evaluate "(()=>{let ids=['resource-cpu','resource-memory','resource-nas-disk','resource-local-disk'],r=ids.map(id=>document.getElementById(id).getBoundingClientRect());return r.every(x=>Math.abs(x.top-r[0].top)<2)&&!document.querySelector('#resource-disks')&&document.querySelectorAll('.composite-meter').length===2})()") -ne $true) { throw 'Four-column composite resource layout failed' }
    $identityOk = Evaluate "(()=>{for(let [id,types]of Object.entries({immich:['data','database'],files:['data'],shared:['data'],joplin:['data','database'],git:['data'],platform:[]})){let card=document.querySelector('#app-'+id),summary=card?.querySelector('summary');if(!card||card.open||summary.querySelector('.app-id')?.textContent!==id||summary.querySelectorAll('.storage-indicator').length!==types.length||summary.querySelector('.write-path'))return false;for(let type of types)if(!summary.querySelector('[data-storage='+type+'] .storage-icon'))return false}return true})()"
    if (-not $identityOk) { throw 'Application ID or storage attribution mismatch' }
    $position = Evaluate "(()=>{let b=document.querySelector('#app-immich [data-storage=data] .storage-icon');let r=b.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2}})()"
    Invoke-CDP 'Input.dispatchMouseEvent' @{type='mouseMoved';x=$position.x;y=$position.y} | Out-Null
    if ((Evaluate "getComputedStyle(document.querySelector('#app-immich [data-storage=data] .storage-tooltip')).display!=='none'") -ne $true) { throw 'Storage capability tooltip does not appear on hover' }
    Evaluate "document.querySelector('#app-immich [data-storage=data] .storage-icon').click()" | Out-Null
    if ((Evaluate "document.querySelector('#app-immich').open") -eq $true) { throw 'Purpose icon unexpectedly expands app' }
    Invoke-CDP 'Input.dispatchMouseEvent' @{type='mouseMoved';x=10;y=10} | Out-Null
    Evaluate "document.querySelector('#app-git [data-storage=data] .storage-icon').focus({preventScroll:true})" | Out-Null
    if ((Evaluate "getComputedStyle(document.querySelector('#app-git [data-storage=data] .storage-tooltip')).display!=='none'") -ne $true) { throw 'Storage capability tooltip missing for keyboard focus' }
    Invoke-CDP 'Input.dispatchKeyEvent' @{type='keyDown';key='Escape';code='Escape';windowsVirtualKeyCode=27} | Out-Null
    if ((Evaluate "getComputedStyle(document.querySelector('#app-git [data-storage=data] .storage-tooltip')).display==='none'") -ne $true) { throw 'Purpose tooltip Escape dismissal failed' }
    Evaluate "document.activeElement.blur()" | Out-Null
    Evaluate "document.querySelector('#app-immich [data-storage=database] .storage-icon').focus({preventScroll:true})" | Out-Null
    if ((Evaluate "(()=>{let tip=document.querySelector('#app-immich [data-storage=database] .storage-tooltip');return getComputedStyle(tip).display!=='none'&&tip.textContent==='\u5df2\u4f7f\u7528\u6570\u636e\u5e93'})()") -ne $true) { throw 'Database tooltip missing or incorrect' }
    Evaluate "document.activeElement.blur()" | Out-Null
    Screenshot 'apps-desktop'
    Evaluate "appViews.git.card.open=true;document.querySelector('#app-git').scrollIntoView({block:'center'})" | Out-Null
    if ((Evaluate "document.querySelector('#git-details').textContent.includes('SSH \u516c\u94a5') && ![...document.querySelectorAll('.permission-table th')].some(x=>x.textContent==='\u53ea\u8bfb\u8bbf\u95ee')") -ne $true) { throw 'Git info or write ownership details mismatch' }
    Screenshot 'git-details'
    $draft = Evaluate "(async()=>{appViews.shared.card.open=true;document.querySelector('#device-connect').open=true;document.querySelector('#local-path').value='~/NAS/draft';await refresh();return document.querySelector('#local-path').value==='~/NAS/draft'&&document.querySelector('#device-connect').open&&appViews.shared.card.open})()"
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
    if ((Evaluate "document.querySelectorAll('#system-rows tr').length===3") -ne $true) { throw 'Container application filter failed' }
    Screenshot 'containers-desktop'
    Navigate '/logs' "document.querySelector('#log-output')?.dataset.loaded==='true'"
    Check-Navigation '/logs'
    Evaluate "selectLogApp('git')" | Out-Null
    Wait-Ready "document.querySelector('#log-output').dataset.loaded==='true' && document.querySelector('#log-output').dataset.app==='git'"
    if ((Evaluate "document.querySelectorAll('#log-tabs [role=tab]').length===2") -ne $true) { throw 'Log sources were not preserved' }
    Screenshot 'logs-desktop'
    Navigate '/logs?app=knowledge' "document.querySelector('#log-output')?.dataset.loaded==='true'&&document.querySelector('#log-output').dataset.app==='joplin'"
    Navigate '/logs?app=syncthing' "document.querySelector('#log-output')?.dataset.loaded==='true'&&document.querySelector('#log-output').dataset.app==='shared'"
    Invoke-CDP 'Emulation.setDeviceMetricsOverride' @{width=390;height=844;deviceScaleFactor=1;mobile=$true} | Out-Null
    foreach ($entry in @(@('/apps',"document.querySelectorAll('#modules .app-row').length===6"),@('/backups',"document.querySelector('#mirror-details')?.children.length>0"),@('/containers',"document.querySelectorAll('#system-rows tr').length===11"),@('/logs',"document.querySelector('#log-output')?.dataset.loaded==='true'"))) {
        Navigate $entry[0] $entry[1]
        Check-Navigation $entry[0]
        if ((Evaluate "document.documentElement.scrollWidth<=innerWidth") -ne $true) { throw ('Mobile overflow '+$entry[0]) }
        if ($entry[0] -eq '/apps') {
            if ((Evaluate "(()=>{let r=['resource-cpu','resource-memory','resource-nas-disk','resource-local-disk'].map(id=>document.getElementById(id).getBoundingClientRect());return Math.abs(r[0].top-r[1].top)<2&&Math.abs(r[2].top-r[3].top)<2&&r[2].top>r[0].bottom&&r[1].left>r[0].left})()") -ne $true) { throw 'Mobile resources are not a 2x2 grid' }
        }
        Screenshot ($entry[0].Substring(1)+'-mobile')
    }
    Evaluate "fetch('/api/logout',{method:'POST',headers:{'Content-Type':'application/json','X-NAS-Request':'1'},body:'{}'}).then(r=>r.ok)" | Out-Null
    @{state='PASS';checks=@('consistent heading typography and spacing on all four pages at desktop/mobile widths','four independent routes and navigation','compact resources and ticking uptime','application IDs and storage capability tooltips','legacy app log links','Git details','sync draft survives refresh','dedicated backup and explicit retention choices','container filter','real logs from stopped app','desktop/mobile no page overflow');screenshots=$taskDir;no_application_state_changed=$true} | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 "$taskDir\result.json"
    Write-Output ('PASS: four-page Chrome acceptance. Screenshots: '+$taskDir)
    Invoke-CDP 'Browser.close' @{} | Out-Null
} finally {
    if ($socket) { $socket.Dispose() }
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -ErrorAction SilentlyContinue }
}
