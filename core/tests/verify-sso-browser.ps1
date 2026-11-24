# Isolated Windows Chrome profile: real browser CORS/API reads only; no sync configuration changes.
$ErrorActionPreference = 'Stop'
$browser = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$taskDir = 'C:\nas\system\runtime\sso-browser-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Path $taskDir | Out-Null
$remote=Get-Content 'C:\nas\system\state\remote.json' -Raw | ConvertFrom-Json
$origin=$remote.meta
$publicHost=([Uri]$origin).Host
$resolver='"--host-resolver-rules=MAP '+$publicHost+' 127.0.0.1"'
$debugPort = 19224
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
    if ($result.exceptionDetails) { throw ('Browser evaluation failed at '+$script:phase+'; credentials withheld from output') }
    return $result.result.value
}
try {
    try { Invoke-RestMethod "http://127.0.0.1:$debugPort/json/version" -TimeoutSec 1 | Out-Null; throw 'Debug port occupied' }
    catch { if ($_.Exception.Message -eq 'Debug port occupied') { throw } }
    $process = Start-Process -FilePath $browser -ArgumentList @('--headless=new','--no-proxy-server','--no-first-run','--no-default-browser-check',$resolver,"--remote-debugging-port=$debugPort", "--user-data-dir=$taskDir\profile",'about:blank') -PassThru
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

    function Wait-Ready($expression) {
        for ($i=0; $i -lt 160; $i++) { Start-Sleep -Milliseconds 200; if ((Evaluate $expression) -eq $true) { return } }
        $location = Evaluate 'location.origin+location.pathname'
    $shot=Invoke-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false};[IO.File]::WriteAllBytes((Join-Path $taskDir 'failure.png'),[Convert]::FromBase64String($shot.data));throw ('Readiness timed out at '+$location+'; evidence '+$taskDir)
    }
    function Navigate($url,$expression) { Invoke-CDP 'Page.navigate' @{url=$url} | Out-Null; Wait-Ready $expression }
    $deep = "function nodes(root=document){return [...root.querySelectorAll('*')].flatMap(e=>[e,...(e.shadowRoot?nodes(e.shadowRoot):[])])}"
    Navigate $origin ($deep+";nodes().some(e=>e.tagName==='INPUT'&&e.type==='password')")
    $counts = Evaluate ($deep+";JSON.stringify({password:nodes().filter(e=>e.tagName==='INPUT'&&e.type==='password').length,username:nodes().filter(e=>e.tagName==='INPUT'&&e.type!=='hidden'&&e.getClientRects().length>0&&['username','uid_field','email'].includes(e.name)).length})") | ConvertFrom-Json
    if ($counts.password -ne 1 -or $counts.username -ne 0) { throw 'Owner login must only ask for the password' }
    $password = (Get-Content 'C:\nas\system\secrets\admin-password.txt' -Raw).Trim() | ConvertTo-Json -Compress
    Evaluate ($deep+";(()=>{let input=nodes().find(e=>e.tagName==='INPUT'&&e.type==='password');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,$password);input.dispatchEvent(new Event('input',{bubbles:true,composed:true}));input.dispatchEvent(new Event('change',{bubbles:true,composed:true}));let submit=nodes().find(e=>e.tagName==='BUTTON'&&e.type==='submit');if(!submit)throw Error('No submit');submit.click();return true})()") | Out-Null
    Wait-Ready "['/','/apps'].includes(location.pathname) && !!document.querySelector('.app-row')"
    $meta = Evaluate "fetch('/api/status').then(r=>r.json()).then(s=>Object.keys(s.apps).length)"
    if ($meta -ne 6) { throw 'Authenticated Meta status unavailable' }
    Navigate ($origin+'/files') "location.pathname==='/files' && document.readyState==='complete' && !!document.querySelector('#file-list')"
    Write-Output 'PASS: one password; Meta and read-only files share browser identity'
    Navigate ($origin+':8445') "location.port==='8445' && (location.pathname.startsWith('/photos')||location.pathname==='/auth/onboarding')"
     $immich=Evaluate "fetch('/api/users/me').then(r=>r.json()).then(u=>u.id)"
    $accounts=Get-Content 'C:\nas\system\state\sso-accounts.json' -Raw | ConvertFrom-Json
    if ($immich -ne $accounts.immich_user_id) { throw 'SSO must preserve the existing Immich user ID' }
    Write-Output 'PASS: existing Immich account reached through OIDC without a second password'
    Navigate ($origin+':8444/login') "location.port==='8444' && document.readyState==='complete'"
    Invoke-CDP 'Page.navigate' @{url=($origin+':8444/login/sso-saml')} | Out-Null
    Wait-Ready "location.port==='8444' && document.readyState==='complete' && (document.body.innerText.includes('Dashboard') || document.body.innerText.includes('Sign out') || document.body.innerText.includes('Log out') || document.body.innerText.includes('Logout'))"
    Write-Output 'PASS: Joplin SAML browser login completed without a second password'
     $script:phase='native-code-page'
    Navigate ($origin+':8444/login/sso-saml-app') "location.port==='8444' && location.pathname==='/api/saml' && document.readyState==='complete' && /\b\d{3}-\d{3}-\d{3}\b/.test(document.body.innerText)"
    $script:phase='native-code-read'
    $code=Evaluate "document.body?.innerText.match(/\b\d{3}-\d{3}-\d{3}\b/)?.[0]?.replaceAll('-','')"
    if (-not $code) { throw 'Native authorization page lost its code' }
    $headers=@{Host=([Uri]$remote.knowledge).Authority}
    $session=Invoke-RestMethod -Uri ('http://127.0.0.1:8082/api/login_with_code/'+$code) -Headers $headers
    if (-not $session.id -or -not $session.user_id) { throw 'SSO client code exchange failed' }
    try { Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:8082/api/login_with_code/'+$code) -Headers $headers | Out-Null; throw 'SSO code reused' }
    catch { if ($_.Exception.Message -eq 'SSO code reused' -or [int]$_.Exception.Response.StatusCode -ne 400) { throw } }
    [IO.File]::WriteAllText('C:\nas\system\secrets\joplin-sso-test-session.json',($session|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
    Write-Output 'PASS: native-client SAML code exchanged once; replay rejected'
    Navigate ($origin+':8444/home') "location.pathname==='/home'"
    $shot=Invoke-CDP 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
    [IO.File]::WriteAllBytes((Join-Path $taskDir 'joplin-sso.png'),[Convert]::FromBase64String($shot.data))
    Navigate $origin "!!document.querySelector('.app-row')"
    $script:phase='logout'
    $logout=Evaluate "fetch('/api/logout',{method:'POST',headers:{'Content-Type':'application/json','X-NAS-Request':'1'},body:'{}'}).then(r=>r.json())"
    if ((Evaluate "fetch('/api/status').then(r=>r.status)") -ne 401) { throw 'Local session not revoked' }
    Invoke-CDP 'Page.navigate' @{url=$logout.logout_url} | Out-Null
    Wait-Ready ($deep+";location.pathname==='/logged-out'||nodes().some(e=>e.tagName==='INPUT'&&e.type==='password')")
    Navigate $origin ($deep+";nodes().some(e=>e.tagName==='INPUT'&&e.type==='password')")
    Write-Output 'PASS: logout revokes the Meta session and clears this browser identity session'
    Write-Output ('SSO browser evidence: '+$taskDir)
} finally {
    if ($socket) { $socket.Dispose() }
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
}
