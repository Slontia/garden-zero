/* Browser-only Syncthing enrollment. Local credentials never leave loopback requests. */
class LocalSyncthing {
  constructor(port, key) {
    if (!/^\d{1,5}$/.test(String(port)) || +port < 1 || +port > 65535) throw Error('本机端口无效');
    if (!key || /[\r\n]/.test(key)) throw Error('请填写本机 Syncthing 的 API Key');
    this.base = 'http://127.0.0.1:' + Number(port); this.key = key;
  }
  async api(path, data, method) {
    let response;
    const options={
      method: method || (data === undefined ? 'GET' : 'POST'), mode:'cors', credentials:'omit',
      redirect:'error',cache:'no-store',signal:AbortSignal.timeout(15000),
      headers:{'X-API-Key':this.key,...(data===undefined?{}:{'Content-Type':'application/json'})},
      body:data===undefined?undefined:JSON.stringify(data)
    };
    // Chrome 142-144 used "local" for loopback too; newer releases distinguish it.
    // Probe dictionary support before sending anything, never retry a failed write request.
    if(typeof Request!=='undefined') {
      for(const space of ['loopback','local',null]) {
        if(space)options.targetAddressSpace=space;else delete options.targetAddressSpace;
        try {new Request(this.base+'/rest/'+path,options);break} catch(error) {
          if(!(error instanceof TypeError)||!space)throw error;
        }
      }
    } else options.targetAddressSpace='loopback';
    try { response=await fetch(this.base+'/rest/'+path,options) }
    catch(error) {
      const detail=(String(error.name||'Error')+': '+String(error.message||'')).replaceAll(this.key,'[已隐藏]').slice(0,240);
      if(error.name==='TimeoutError')throw Error('连接本机 Syncthing 超时。请检查本机端口及浏览器网络权限。诊断：'+detail);
      throw Error('无法读取本机 Syncthing 响应。如果控制台提示缺少 Access-Control-Allow-Origin，请先重新复制这台电脑本机 Syncthing 的 API Key；错误的 Key 也会导致该 CORS 提示。若仍失败，请核对本机管理页面的完整地址和端口。诊断（不包含 API Key）：'+detail);
    }
    if (response.status === 401 || response.status === 403) throw Error('本机授权未通过，请从本机 Syncthing 设置中复制 API Key（不是登录密码）。');
    if (!response.ok) throw Error('本机 Syncthing 操作失败（' + response.status + '），已有配置保留。');
    const text = await response.text(); return text ? JSON.parse(text) : null;
  }
}
function syncPath(path, status) {
  if (typeof path !== 'string' || !path.trim() || /[\x00-\x1f]/.test(path)) throw Error('请填写有效的本地存放路径');
  path = path.trim(); const sep = status.pathSeparator || '/';
  if (/^~([\\/]|$)/.test(path)) path = status.tilde + path.slice(1);
  if (sep === '\\') path = path.replaceAll('/', '\\');
  if (sep === '/' ? !path.startsWith('/') : !/^[A-Za-z]:\\/.test(path)) throw Error('请使用本机绝对路径，例如 ' + status.tilde + sep + 'NAS' + sep + '共享');
  const parts = path.split(/[\\/]/);
  if (parts.some(p => p === '.' || p === '..')) throw Error('路径中不能包含 . 或 ..');
  return path.replace(/[\\/]+$/, '') || sep;
}
function comparablePath(path, status) {
  let p = syncPath(path, status).replaceAll('\\', '/');
  return p.normalize('NFC').toLowerCase();
}
function overlaps(a,b) { return a === b || a.startsWith(b + '/') || b.startsWith(a + '/') || a === '/' || b === '/'; }
async function enrollLocal(client, nas, rawPath, register) {
  const status = await client.api('system/status');
  if (status.myID === nas.device_id) throw Error('这是 NAS 主机自身的 Syncthing，请在需要接入的电脑上打开 Meta。');
  const path = syncPath(rawPath, status), target = comparablePath(path,status);
  if (target === comparablePath(status.tilde,status) || target === '/' || /^[a-z]:\/?$/i.test(target) || /^\/Volumes(\/|$)/i.test(target) || /^\/mnt\//.test(target)) throw Error('请选择专用本地目录，不要选择主目录、磁盘根或网络挂载位置。');
  const folders = await client.api('config/folders'), devices = await client.api('config/devices');
  const existing = folders.find(f => f.id === nas.folder_id);
  if (existing && (comparablePath(existing.path,status) !== target || existing.type !== 'sendreceive' || existing.filesystemType !== 'basic' || (existing.paused && client.pendingPath !== target) || existing.ignoreDelete)) throw Error('此共享已配置在其他路径或使用其他同步策略。为保护已有数据，未自动迁移；请使用现有路径或先在 Syncthing 中核实。');
  if(existing && !existing.devices.some(d=>d.deviceID===nas.device_id))throw Error('本机存在同名共享但未连接此 NAS，未自动接管。请先在 Syncthing 中核实。');
  for (const f of folders) if (f.id !== nas.folder_id && overlaps(target,comparablePath(f.path,status))) throw Error('此位置与已有同步目录重叠，请选择新的专用目录。');
  // Browse only enumerates directories, not files: never infer emptiness from an empty result.
  // New enrollment requires a directory that does not already exist.
  if (!existing) {
    const entries = await client.api('system/browse?' + new URLSearchParams({current:path}));
    if (entries.some(p=>comparablePath(p,status)===target)) throw Error('此目录已经存在，请填写一个尚未建立的新子目录，系统会自动创建；不会自动合并已有文件。');
  }
  const peer = devices.find(d => d.deviceID === nas.device_id);
  if (peer && (peer.paused || peer.introducer || peer.autoAcceptFolders)) throw Error('已有 NAS 设备设置需要核实，请先关闭暂停、介绍人和自动接受文件夹后重试。');
  // Local folder remains paused until NAS registration succeeds; a failed/retried action cannot
  // download into an unconfirmed location. Never delete a device/folder as automatic rollback.
  if (!existing) {
    const f = await client.api('config/defaults/folder');
    Object.assign(f,{id:nas.folder_id,label:nas.label,path,filesystemType:'basic',type:'sendreceive',paused:true,ignorePerms:true,ignoreDelete:false,maxConflicts:-1,devices:[{deviceID:status.myID},{deviceID:nas.device_id}]});
    f.versioning = {type:'simple',params:{keep:'20',cleanoutDays:'0'},cleanupIntervalS:3600,fsPath:'',fsType:'basic'};
    // Register device first, then a paused folder. Preserve all unrelated local configuration.
    const d = peer ? {...peer} : await client.api('config/defaults/device');
    Object.assign(d,{deviceID:nas.device_id,name:peer?.name || 'NAS',addresses:[...new Set([nas.address,...(peer?.addresses||[])])],introducer:false,autoAcceptFolders:false});
    await client.api('config/devices',d,'POST');
    await client.api('config/folders',f,'POST');client.pendingPath=target;
  } else {
    const d = peer ? {...peer} : await client.api('config/defaults/device');
    Object.assign(d,{deviceID:nas.device_id,name:peer?.name || 'NAS',addresses:[...new Set([nas.address,...(peer?.addresses||[])])],introducer:false,autoAcceptFolders:false});
    await client.api('config/devices',d,'POST');
  }
  await register({action:'sync-pair',device:status.myID,name:status.hostName || '浏览器接入设备'});
  if (existing) {
    if (!existing.devices.some(d => d.deviceID === nas.device_id)) await client.api('config/folders/'+encodeURIComponent(nas.folder_id),{devices:[...existing.devices,{deviceID:nas.device_id}]},'PATCH');
    if(client.pendingPath===target)await client.api('config/folders/'+encodeURIComponent(nas.folder_id),{paused:false},'PATCH');
  } else await client.api('config/folders/'+encodeURIComponent(nas.folder_id),{paused:false},'PATCH');
  return {path,device:status.myID};
}
async function detachLocal(client,nas,unregister) {
 const status=await client.api('system/status');
 if(status.myID===nas.device_id)throw Error('不能解除 NAS 主机自身的共享。');
 const folders=await client.api('config/folders');const folder=folders.find(f=>f.id===nas.folder_id);
 if(folder && !folder.devices.some(d=>d.deviceID===nas.device_id))throw Error('此共享不属于当前 NAS，未自动修改。');
 if(folder)await client.api('config/folders/'+encodeURIComponent(nas.folder_id),{paused:true},'PATCH');
 await unregister({action:'sync-unpair',device:status.myID});
 if(folder)await client.api('config/folders/'+encodeURIComponent(nas.folder_id),undefined,'DELETE');
 client.pendingPath=null;
 return folder?.path;
}

let connectBusy=false, localClient=null, localStatus=null;
function renderLocalBinding(existing,initializePath=false) {
 document.querySelector('#local-new').hidden=Boolean(existing);
 document.querySelector('#local-bound').hidden=!existing;
 document.querySelector('#local-current-path').textContent=existing?.path||'';
 if(!existing && localStatus && initializePath)document.querySelector('#local-path').value=localStatus.tilde+(localStatus.pathSeparator||'/')+'NAS'+(localStatus.pathSeparator||'/')+'共享';
 document.querySelector('#local-binding').hidden=false;
 document.querySelector('#local-auth').hidden=true;
}
async function refreshLocalBinding(){
 const folders=await localClient.api('config/folders');
 const existing=folders.find(f=>f.id===window.nasConnection?.folder_id);
 renderLocalBinding(existing);return existing;
}
function connectionMessage(text,isError=false) { const message=document.querySelector('#connect-message');message.textContent=text;message.className=isError?'bad':'';message.setAttribute?.('role',isError?'alert':'status');if(isError)message.scrollIntoView?.({block:'nearest',behavior:'smooth'}); }
async function authorizeLocal() {
  if(connectBusy)return; connectBusy=true;
  try {
    if(!window.nasConnection?.folder_id)throw Error('NAS 同步信息尚未加载，请稍后点击重试。');
    connectionMessage('正在连接本机，请允许浏览器访问本机网络…');
    const key=document.querySelector('#local-key');
    const client=new LocalSyncthing(document.querySelector('#local-port').value,key.value.trim());
    const status=await client.api('system/status');
    localClient=client;localStatus=status;key.value='';
    const folders=await client.api('config/folders');const existing=folders.find(f=>f.id===window.nasConnection?.folder_id);
    renderLocalBinding(existing,true);
    connectionMessage(existing?'此设备已绑定。需要更换位置时，请先解除本机绑定；原文件会保留。':'本机已授权，请确认存放位置后点击绑定。');
  } catch(e){localClient=null;localStatus=null;connectionMessage(e.message,true)} finally{connectBusy=false}
}
async function connectThisDevice() {
  if(connectBusy || !localClient)return;connectBusy=true;
  const button=document.querySelector('#local-bind');button.disabled=true;
  try {
    const nas=window.nasConnection;
    if(!nas?.device_id || !nas?.address)throw Error('NAS 同步服务尚未就绪，请稍后重试。');
    connectionMessage('正在配置本机与 NAS，请勿关闭页面…');
    const result=await enrollLocal(localClient,nas,document.querySelector('#local-path').value,data=>request('/api/action',data));
    await refreshLocalBinding();
    connectionMessage('已绑定到 '+result.path+'。Syncthing 会在后台同步，网页可以关闭。');await refresh();
  } catch(e){let existing;try{existing=await refreshLocalBinding()}catch(_){}connectionMessage('绑定未完成：'+e.message+(existing?' 本机已有共享配置，请核实或解绑后重试；原文件保留。':' 输入路径已保留，请修改后重试。'),true)}
  finally{connectBusy=false;button.disabled=false}
}
async function detachThisDevice(){
 if(connectBusy||!localClient)return;
 if(!confirm('解除本机共享绑定？会停止此共享的同步，本地文件和 NAS 数据均保留。未同步的修改仍在本地；换新位置后不会自动迁移这些修改。'))return;
 connectBusy=true;const button=document.querySelector('#local-detach');button.disabled=true;
 try{
  connectionMessage('正在暂停同步并解除本机绑定…');
  await detachLocal(localClient,window.nasConnection,data=>request('/api/action',data));
  await refreshLocalBinding();document.querySelector('#local-path').value='';
  connectionMessage('已解除绑定，原文件均保留。请填写一个新的存放位置再绑定。');await refresh();
 }catch(e){connectionMessage('解绑未完成：'+e.message+' 已暂停的共享保持暂停，请重试；文件不会被删除。',true)}
 finally{connectBusy=false;button.disabled=false}
}
function forgetLocal(){localClient=null;localStatus=null;document.querySelector('#local-key').value='';document.querySelector('#local-auth').hidden=false;document.querySelector('#local-binding').hidden=true;connectionMessage('本页授权已清除，本机后台同步继续运行。')}

function byteLabel(value){let n=Number(value)||0;return n>=1073741824?(n/1073741824).toFixed(2)+' GiB':n>=1048576?(n/1048576).toFixed(1)+' MiB':n>=1024?(n/1024).toFixed(1)+' KiB':n+' B'}
function percentLabel(value,pending){if(value===null||value===undefined||value===''||!Number.isFinite(Number(value)))return '未知';let n=Math.max(0,Math.min(100,Number(value)));if(pending>0)n=Math.min(n,99.9);return n.toFixed(1)+'%'}
function fileLink(path,label){const link=node('a',label);link.href=serviceURL('files')+'browse?path='+encodeURIComponent('shared/'+path);link.target='_blank';link.rel='noopener';return link}
function renderSyncDetails(sync){
 const panel=document.querySelector('#sync-progress');panel.replaceChildren();
 if(sync.mode!=='full_sync'){panel.append(node('p','同步服务不可用，暂无法读取进度。'));return}
 const states={idle:'空闲',syncing:'同步中',scanning:'扫描中','scan-waiting':'等待扫描','sync-waiting':'等待同步',error:'出错'};
 panel.append(node('h3','同步进度'),node('p','NAS：'+(sync.paused?'已暂停':states[sync.sync_state]||sync.sync_state||'未知')));
 if(!sync.paused)panel.append(node('p','NAS 已知进度：'+percentLabel(sync.completion,sync.pending_items)+'；待接收 '+(sync.pending_items||0)+' 项 / '+byteLabel(sync.pending_bytes)),node('p','已保存 '+(sync.local_files||0)+' 个文件 / '+byteLabel(sync.local_bytes)));
 if(sync.errors||sync.sync_error)panel.append(node('p','同步错误：'+(sync.sync_error||sync.errors+' 项，请检查日志'),'bad'));
 for(const peer of sync.peers||[]){
  let text=(peer.name||peer.id.slice(0,7))+'：';
  if(!peer.connected)text+='离线，当前进度未知';
  else if(peer.folder_state!=='valid')text+=({paused:'设备已暂停共享',notSharing:'设备未接受共享',unknown:'已连接，进度尚未知'})[peer.folder_state]||'已连接，进度尚未知';
  else text+='已连接，已知进度 '+percentLabel(peer.completion,peer.pending_items)+'；待接收 '+(peer.pending_items||0)+' 项 / '+byteLabel(peer.pending_bytes);
  panel.append(node('p',text));
 }
 if((sync.peers||[]).some(p=>!p.connected))panel.append(node('p','离线设备可能有尚未上报的修改；NAS 空闲不代表所有设备已经一致。','muted'));
 const conflicts=document.querySelector('#sync-conflicts');conflicts.replaceChildren();
 if(!(sync.conflicts||[]).length)conflicts.append(node('p','当前没有检测到冲突副本。'));
 for(const path of sync.conflicts||[]){let row=node('p');row.append(node('span',path+' '),fileLink(path,'下载冲突副本'));const original=path.replace(/\.sync-conflict-\d{8}-\d{6}-[A-Z0-9]+(?=\.|$)/,'');if(original!==path)row.append(document.createTextNode(' · '),fileLink(original,'查看当前文件'));conflicts.append(row)}
 if((sync.conflict_count||0)>(sync.conflicts||[]).length)conflicts.append(node('p','仅显示前 '+sync.conflicts.length+' 个，完整内容请从本机共享目录查看。'));
}
let historyRestoreBusy=false;
async function loadHistory(){
 const message=document.querySelector('#history-message'),list=document.querySelector('#history-list');message.textContent='正在读取历史版本…';
 try{
  const data=await request('/api/sync/history');list.replaceChildren();
  message.textContent=data.total?'找到 '+data.total+' 个历史版本'+(data.limited?'，仅显示最近 500 个':''):'当前没有可恢复的历史版本；版本只在接收其他设备的覆盖或删除时保留。';
  for(const item of data.entries){const row=node('section','','card');row.append(node('p',item.path),node('p',new Date(item.version).toLocaleString()+' · '+byteLabel(item.size)));const button=node('button','恢复为新副本','secondary');button.onclick=()=>restoreHistory(item,button);row.append(button);list.append(row)}
 }catch(e){message.textContent='读取失败：'+e.message}
}
async function restoreHistory(item,button){
 if(historyRestoreBusy)return;
 if(!confirm('将此历史版本恢复到新的 Recovered 文件夹？当前文件和历史版本都会保留。恢复副本会在同步开启后分发到已绑定设备。'))return;
 historyRestoreBusy=true;button.disabled=true;const message=document.querySelector('#history-message');message.textContent='正在生成恢复副本…';
 try{
  const result=await request('/api/action',{action:'sync-restore-copy',path:item.path,version:item.version});
  message.replaceChildren(node('span','已生成 '+result.restored_path+'。'+(result.warning||(!result.sync_resumed?'共享保持暂停，可按需恢复同步。':''))+' '),fileLink(result.restored_path,'查看恢复副本'));
  await refresh();
 }catch(e){message.textContent=e.message}
 finally{historyRestoreBusy=false;button.disabled=false}
}
if(window.nasSyncStatus)renderSyncDetails(window.nasSyncStatus);
