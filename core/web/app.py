#!/usr/bin/env python3
"""NAS Meta + read-only files + private Markdown knowledge views. No Docker socket."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import base64,hashlib,hmac,html,http.client,http.cookies,http.server,json,mimetypes,os,re,secrets,signal,socket,stat,sys,time,urllib.parse
from pathlib import Path
MODE=os.environ.get('NAS_MODE','meta')
PUBLIC_ORIGIN=os.environ.get('NAS_PUBLIC_ORIGIN','').rstrip('/')
ROOT=os.environ.get('NAS_DATA','/data')
ADMIN=json.loads(Path('/credentials/admin.json').read_text())
KEY=bytes.fromhex(ADMIN['hash'])
COOKIE='nas_'+MODE+'_session'
FAILURES={}
SESSIONS={}
STYLE='''body{margin:0;background:#f4f6fa;color:#182336;font:15px system-ui,sans-serif}main{max-width:1100px;margin:auto;padding:32px 24px}h1{font-size:30px}header{display:flex;justify-content:space-between;align-items:center}.muted{color:#657287}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(255px,1fr));gap:16px}.card{background:white;padding:22px;border-radius:14px;border:1px solid #e4e8ef;margin:14px 0}button,.button{display:inline-block;border:0;border-radius:8px;padding:9px 13px;background:#235cce;color:white;text-decoration:none;cursor:pointer;margin:4px}button:disabled{opacity:.5;cursor:not-allowed}button.secondary{background:#e9eef7;color:#293b59}input{padding:12px;border:1px solid #c7d0dd;border-radius:8px;max-width:100%;box-sizing:border-box}pre{white-space:pre-wrap;word-break:break-word;background:#101e31;color:#d3e3ff;padding:18px;border-radius:8px}a{color:#235cce}li{margin:12px 0}.state{font-size:13px;color:#56657c}.good{color:#148454}.bad{color:#b54136}article{line-height:1.8}table{border-collapse:collapse;width:100%}td,th{text-align:left;border-bottom:1px solid #e7eaf0;padding:10px}.actions{display:flex;flex-wrap:wrap}#message{white-space:pre-wrap}code{background:#edf0f5;padding:2px 4px}'''
def page(title,body,script='',layout=''):
 return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title><style>'+STYLE+(Path('/app/meta.css').read_text() if layout else '')+'</style><body class="'+layout+'"><main>'+body+'</main>'+('<script>'+script+'</script>' if script else '')+'</body></html>'
class UnixConnection(http.client.HTTPConnection):
 def connect(self):
  self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(300);self.sock.connect('/control/control.sock')
def agent(path,data=None):
 token=Path('/credentials/agent-token').read_text().strip();conn=UnixConnection('localhost',timeout=300)
 try:
  conn.request('POST' if data is not None else 'GET',path,body=json.dumps(data) if data is not None else None,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
  response=conn.getresponse();body=json.loads(response.read())
  if response.status!=200:raise RuntimeError(body.get('error','Control service unavailable'))
  return body
 finally:conn.close()
def safe_open(relative,flags=os.O_RDONLY):
 parts=relative.split('/') if relative else []
 if any(p in ('','.', '..') or '\\' in p or '\x00' in p for p in parts):raise PermissionError('Invalid path')
 fd=os.open(ROOT,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:
  for i,part in enumerate(parts):
   new=os.open(part,(flags if i==len(parts)-1 else os.O_RDONLY|os.O_DIRECTORY)|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=new
  return fd
 except Exception:os.close(fd);raise

def md(text):
 # Small safe Markdown renderer: raw HTML always escaped, no user-supplied scripts.
 lines=[];code=False
 for raw in text.splitlines():
  if raw.startswith('```'):
   lines.append('</code></pre>' if code else '<pre><code>');code=not code;continue
  escaped=html.escape(raw)
  if code:lines.append(escaped+'\n');continue
  m=re.match(r'^(#{1,6})\s+(.+)',escaped)
  if m:lines.append(f'<h{len(m[1])}>{m[2]}</h{len(m[1])}>')
  elif raw.startswith('- '):lines.append('<p>• '+escaped[2:]+'</p>')
  elif raw.strip():lines.append('<p>'+escaped+'</p>')
 if code:lines.append('</code></pre>')
 return ''.join(lines)

JS='''function canonicalApp(id){return ({knowledge:'joplin',syncthing:'shared'})[id]||id}
async function request(path,body){let r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json','X-NAS-Request':'1'},body:body?JSON.stringify(body):undefined});let d=await r.json();if(r.status===401){location='/';throw Error('请重新登录')}if(!r.ok)throw Error(d.error||'操作失败');return d}async function logout(){if(typeof forgetLocal==='function')forgetLocal();await request('/api/logout',{});location='/'}'''
MANAGEMENTJS=Path('/app/management.js').read_text()
METAJS=JS+MANAGEMENTJS+Path('/app/dashboard.js').read_text()

class Handler(http.server.BaseHTTPRequestHandler):
 server_version='NAS'
 def log_message(self,*args):pass
 def send(self,code,body,content='text/html; charset=utf-8',headers=None):
  data=body.encode() if isinstance(body,str) else body;self.send_response(code)
  self.send_header('Content-Type',content);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Frame-Options','DENY')
  self.send_header('Content-Security-Policy',("connect-src 'self' http://127.0.0.1:*; " if MODE=='meta' else '')+"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
  for k,v in (headers or {}).items():self.send_header(k,v)
  self.end_headers();self.wfile.write(data)
 def json(self,code,data):return self.send(code,json.dumps(data,ensure_ascii=False),'application/json')
 def authenticated(self):
  try:
   cookie=http.cookies.SimpleCookie(self.headers.get('Cookie',''));value=cookie[COOKIE].value;payload,signature=value.rsplit('.',1)
   expires,mode,nonce=payload.split(':')
   return SESSIONS.get(nonce,0)==int(expires) and mode==MODE and int(expires)>time.time() and hmac.compare_digest(signature,hmac.new(KEY,payload.encode(),hashlib.sha256).hexdigest())
  except Exception:return False
 def cookie(self,value):
  secure='; Secure' if self.headers.get('X-Forwarded-Proto')=='https' else ''
  return COOKIE+'='+value+'; HttpOnly; SameSite=Strict; Path=/'+secure
 def read_json(self):
  if self.headers.get('X-NAS-Request')!='1' or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise PermissionError('Invalid request headers')
  size=int(self.headers.get('Content-Length','0'))
  if not 0<size<=8192:raise ValueError('Invalid request size')
  return json.loads(self.rfile.read(size))
 def redirect_plain_remote(self):
  if MODE!='meta' or not PUBLIC_ORIGIN:return False
  expected=urllib.parse.urlsplit(PUBLIC_ORIGIN).netloc
  forwarded=self.headers.get('X-Forwarded-Host','')
  # Tailscale HTTP Serve may omit X-Forwarded-Proto; HTTPS always supplies https.
  if forwarded not in (expected,expected+':80') or self.headers.get('X-Forwarded-Proto','').lower()=='https':return False
  path=self.path if self.path.startswith('/') and not self.path.startswith('//') else '/'
  self.send(308,b'',headers={'Location':PUBLIC_ORIGIN+path})
  return True
 def do_POST(self):
  if self.redirect_plain_remote():return
  try:
   data=self.read_json()
   if self.path=='/api/login':
    ip=self.client_address[0];now=time.time();attempts=[t for t in FAILURES.get(ip,[]) if now-t<300]
    if len(attempts)>=10:return self.json(429,{'error':'尝试次数过多，请稍后再试'})
    password=str(data.get('password',''))[:512];username=str(data.get('username',ADMIN['username']))
    digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(ADMIN['salt']),n=16384,r=8,p=1).hex()
    if username!=ADMIN['username'] or not hmac.compare_digest(digest,ADMIN['hash']):
     FAILURES[ip]=attempts+[now];return self.json(401,{'error':'密码不正确'})
    FAILURES.pop(ip,None);nonce=secrets.token_hex(24);expiry=int(now+8*3600);SESSIONS[nonce]=expiry;payload=str(expiry)+':'+MODE+':'+nonce;cookie=payload+'.'+hmac.new(KEY,payload.encode(),hashlib.sha256).hexdigest()
    return self.send(200,'{"ok":true}','application/json',{'Set-Cookie':self.cookie(cookie)})
   if not self.authenticated():return self.json(401,{'error':'请先登录'})
   if self.path=='/api/logout':
    token=http.cookies.SimpleCookie(self.headers.get('Cookie',''))[COOKIE].value
    SESSIONS.pop(token.rsplit('.',1)[0].split(':')[2],None)
    return self.send(200,'{}','application/json',{'Set-Cookie':self.cookie('')+'; Max-Age=0'})
   if MODE=='meta' and self.path=='/api/action':return self.json(200,agent('/action',data))
   return self.json(405,{'error':'此服务不允许写入'})
  except PermissionError as e:self.json(403,{'error':str(e)})
  except Exception as e:self.json(400,{'error':str(e)})
 def do_DELETE(self):self.json(405,{'error':'只读服务'})
 def do_PUT(self):self.json(405,{'error':'只读服务'})
 def do_PATCH(self):self.json(405,{'error':'只读服务'})
 def do_GET(self):
  if self.redirect_plain_remote():return
  try:
   parsed=urllib.parse.urlsplit(self.path);path=parsed.path
   if path=='/health':return self.json(200,{'status':'ready','mode':MODE})
   if not self.authenticated():
    if path.startswith('/api/'):return self.json(401,{'error':'请先登录'})
    identity='<input type="hidden" name="username" value="'+html.escape(ADMIN['username'],quote=True)+'" autocomplete="username">'
    body='<section class="card" style="max-width:400px;margin:12vh auto"><h1>NAS '+html.escape(MODE)+'</h1><p class="muted">私人访问入口</p><form id="login">'+identity+'<p><input name="password" type="password" placeholder="密码" autocomplete="current-password" required></p><button>登录</button></form><p id="message"></p></section>'
    script=JS+"document.querySelector('#login').onsubmit=async e=>{e.preventDefault();try{let f=new FormData(e.target);let r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json','X-NAS-Request':'1'},body:JSON.stringify(Object.fromEntries(f))});let d=await r.json();if(!r.ok)throw Error(d.error);location='/'}catch(e){document.querySelector('#message').textContent=e.message}}"
    if MODE=='meta':body='<section class="login-card"><div class="brand">'+'<span class="brand-mark" aria-hidden="true">▤</span><span><span class="brand-name">零号庭院<span>.</span></span><span class="brand-caption">GARDEN ZERO</span></span>'+'</div><p class="eyebrow">WELCOME HOME</p><h1>欢迎回来。</h1><p class="muted">登录你的私人数据空间</p><form id="login">'+identity+'<label>密码<input name="password" type="password" placeholder="输入管理密码" autocomplete="current-password" required></label><button>进入管理面板 →</button></form><p id="message" role="alert"></p><p class="login-foot">零号庭院 / GARDEN ZERO</p></section>'
    return self.send(200,page('零号庭院 · 登录' if MODE=='meta' else 'NAS 登录',body,script,'login' if MODE=='meta' else ''))
   if MODE=='meta':
    if path=='/connect.js':return self.send(200,Path('/app/connect.js').read_text(),'application/javascript; charset=utf-8')
    if path=='/api/sync/history':return self.json(200,agent('/sync/history'))
    if path=='/api/status':return self.json(200,agent('/status'))
    if path=='/api/system':return self.json(200,agent('/system'))
    for prefix in ('/api/app-logs/','/api/container-logs/'):
     if path.startswith(prefix):return self.json(200,agent(path[4:]))
    if path=='/system':
     self.send_response(302);self.send_header('Location','/containers');self.end_headers();return
    if path.startswith('/api/logs/'):return self.json(200,agent(path[4:]))
    names={'/':('apps','dashboard','应用'),'/apps':('apps','dashboard','应用'),'/backups':('backups','backups','备份'),'/containers':('containers','containers','容器'),'/logs':('logs','logs','日志')}
    if path not in names:return self.json(404,{'error':'not found'})
    active,name,title=names[path];nav=Path('/app/navigation.html').read_text().replace('class="nav-link" href="/'+active+'"','class="nav-link active" href="/'+active+'" aria-current="page"')
    body=Path('/app/'+name+'.html').read_text().replace('<!--NAVIGATION-->',nav)
    script=METAJS if name=='dashboard' else JS+(MANAGEMENTJS if name=='backups' else '')+Path('/app/'+name+'.js').read_text()
    return self.send(200,page('零号庭院 · '+title,body,script,'dashboard')+('<script src="/connect.js"></script>' if name=='dashboard' else ''))
   if MODE=='files':return self.files(parsed)
   if MODE=='knowledge':return self.knowledge(parsed)
   self.json(404,{'error':'not found'})
  except (FileNotFoundError,NotADirectoryError):self.json(404,{'error':'文件不存在'})
  except (PermissionError,OSError):self.json(403,{'error':'路径不可访问'})
  except Exception:self.json(503,{'error':'服务暂不可用，请检查宿主控制服务'})
 def files(self,parsed):
  if parsed.path not in ('/','/browse'):return self.json(404,{'error':'not found'})
  relative=urllib.parse.parse_qs(parsed.query).get('path',[''])[0]
  fd=safe_open(relative)
  try:
   st=os.fstat(fd)
   if stat.S_ISREG(st.st_mode):
    name=relative.rsplit('/',1)[-1]
    self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+urllib.parse.quote(name));self.send_header('Content-Length',str(st.st_size));self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
    with os.fdopen(os.dup(fd),'rb') as f:
     while block:=f.read(1024**2):self.wfile.write(block)
    return
   if not stat.S_ISDIR(st.st_mode):raise PermissionError()
   entries=[]
   for name in sorted(os.listdir(fd),key=str.casefold):
    info=os.stat(name,dir_fd=fd,follow_symlinks=False)
    if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):continue
    child=(relative+'/' if relative else '')+name
    entries.append('<li><a href="/browse?path='+urllib.parse.quote(child,safe='')+'">'+html.escape(name)+('/' if stat.S_ISDIR(info.st_mode) else '')+'</a></li>')
   parent=relative.rsplit('/',1)[0] if '/' in relative else ''
   body='<header><h1>文件浏览</h1><button onclick="logout()">退出</button></header><p class="muted">'+html.escape(relative or '/')+'</p><a href="/browse?path='+urllib.parse.quote(parent,safe='')+'">上一级</a><section class="card"><ul>'+''.join(entries)+'</ul></section>'
   self.send(200,page('文件浏览',body,JS))
  finally:os.close(fd)
 def knowledge(self,parsed):
  params=urllib.parse.parse_qs(parsed.query);article=params.get('article',[''])[0];query=params.get('q',[''])[0].casefold()
  if parsed.path!='/':return self.json(404,{'error':'not found'})
  if article:
   if not article.endswith('.md'):raise PermissionError()
   fd=safe_open('articles/'+article)
   with os.fdopen(fd,'r',encoding='utf-8') as f:text=f.read(2*1024**2)
   body='<a href="/">← 文章列表</a><article class="card">'+md(text)+'</article>'
  else:
   entries=[]
   # No symlink traversal: every article opened through a no-follow dirfd chain.
   for directory,dirs,files in os.walk(Path(ROOT)/'articles',followlinks=False):
    dirs[:]=[d for d in dirs if not (Path(directory)/d).is_symlink()]
    for name in sorted(files):
     if not name.endswith('.md'):continue
     relative=str((Path(directory)/name).relative_to(Path(ROOT)/'articles'))
     try:
      fd=safe_open('articles/'+relative)
      with os.fdopen(fd,encoding='utf-8') as f:text=f.read(2*1024**2)
     except OSError:continue
     if query and query not in (relative+' '+text).casefold():continue
     title=next((x.lstrip('# ').strip() for x in text.splitlines() if x.startswith('# ')),name[:-3])
     category=str(Path(relative).parent)
     entries.append('<li><a href="/?article='+urllib.parse.quote(relative,safe='')+'">'+html.escape(title)+'</a> <span class="muted">'+html.escape(category)+'</span></li>')
   body='<header><h1>私人知识库</h1><button onclick="logout()">退出</button></header><form><input name="q" placeholder="搜索标题与正文" value="'+html.escape(query,quote=True)+'"><button>搜索</button></form><section class="card"><ul>'+''.join(entries)+'</ul></section>'
  self.send(200,page('私人知识库',body,JS))
if __name__=='__main__':
 signal.signal(signal.SIGTERM,lambda *_:sys.exit(0))
 http.server.ThreadingHTTPServer(('0.0.0.0',8080),Handler).serve_forever()
