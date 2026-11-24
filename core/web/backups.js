function backupTime(value){if(!value)return '尚无成功记录';if(/^\d{8}-\d{6}$/.test(value))return value.replace(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/,'$1-$2-$3 $4:$5:$6');let date=new Date(value);return Number.isNaN(date.getTime())?value:date.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).replaceAll('/','-')}
function detailList(selector,items){let root=document.querySelector(selector);root.replaceChildren();for(let [label,value] of items)root.append(node('dt',label),node('dd',value??'—'))}
function backupPath(path){return path?.replace(/^\/mnt\/([a-z])\//,(_,drive)=>drive.toUpperCase()+':\\').replace(/\//g,'\\')||'尚未配置'}
function outcome(state){return ({success:'成功',interrupted:'中断',failed:'失败',running:'进行中',success_with_resume_error:'失败'})[state]||'尚未备份'}
function renderDisk(h,blocked){
 const target=h.disk||{};document.querySelector('#mirror-now').disabled=blocked||!h.path||h.last?.state==='running';
 document.querySelector('#disk-target').textContent=(target.name||'备份磁盘')+' · '+(target.relative_path||'未配置目录');
 const root=document.querySelector('#disk-history'),open=new Set([...root.querySelectorAll('details[open]')].map(x=>x.dataset.key));root.replaceChildren();
 const rows=h.history||[];document.querySelector('#disk-empty').hidden=rows.length>0;
 for(const [index,row] of rows.entries()){
  const disk=row.disk||target,tr=node('tr');for(const value of [backupTime(row.started||row.time||row.last_success),disk.name||'未知',disk.uid||'未知',disk.relative_path||'—'])tr.append(node('td',value));
  const status=node('td');status.append(node('span',outcome(row.state),'backup-outcome outcome-'+row.state));tr.append(status);
  const cell=node('td'),details=node('details');details.dataset.key=row.started||row.time||String(index);details.open=open.has(details.dataset.key);details.append(node('summary','展开'));
  details.append(node('pre',row.error||row.resume_error||(row.state==='success'?'备份已完整提交，保留策略已生效。':row.state==='running'?'正在准备或传输数据，请勿拔出磁盘。':'任务未完整完成，上一次完整备份保留。')));cell.append(details);tr.append(cell);root.append(tr);
 }
}
async function refresh(){if(actionPending)return;try{let d=await request('/api/status'),blocked=Boolean(d.storage_blocked);window.viewStatus=d;document.querySelector('#storage-alert').hidden=!blocked;renderDisk(d.hdd||{},blocked);renderCloud(d.cloud,blocked);pageNotice(null);let badge=document.querySelector('#connection-badge');badge.className='connection-badge online';badge.textContent='管理服务已连接'}catch(e){pageNotice(e.message);let badge=document.querySelector('#connection-badge');badge.className='connection-badge warning';badge.textContent='连接暂时中断'}}
decorateIcons();refresh();setInterval(refresh,3000);

let cloudSession=null,cloudTimer=null,cloudBusy=false,cloudReauthorize=null,cloudGeneration=0;
let pendingCloudActions=new Map(),bulkCloud=false,deletingCloud=null;
function renderCloud(cloud={},blocked=false){
 const accounts=cloud.accounts||[],root=document.querySelector('#cloud-accounts');root.replaceChildren();
 const anyBackup=accounts.some(a=>a.backup?.state==='running');
 for(const account of accounts){
  const verifying=account.state==='verifying'||pendingCloud.has(account.id),backing=account.backup?.state==='running'||pendingCloudActions.get(account.id)==='backup';
  const pending=pendingCloudActions.has(account.id),row=node('div','','cloud-account');row.dataset.account=account.id;
  const info=node('div','','cloud-account-info'),profile=account.profile||{},display=profile.nick_name||profile.name||profile.user_name||account.name;
  const heading=node('div','','cloud-account-heading'),automatic=account.auto_backup!==false;
  const auto=node('button',automatic?'自动备份已开启':'自动备份已关闭','cloud-auto-status '+(automatic?'enabled':'disabled'));auto.disabled=pending||verifying||blocked;auto.setAttribute('aria-pressed',String(automatic));auto.onclick=()=>toggleCloudAuto(account.id,!automatic);
  heading.append(node('strong',display),auto);info.append(heading,node('p',backing?'阿里云盘 · 正在备份':verifying?'阿里云盘 · 正在验证连接':account.state==='ready'?'阿里云盘 · 连接已验证':'阿里云盘 · 待验证连接','muted'));
  if(profile.user_name&&profile.user_name!==display)info.append(node('p','用户名：'+profile.user_name,'muted'));
  if(profile.user_id)info.append(node('p','用户 ID：'+profile.user_id,'muted'));
  info.append(node('p','上次成功备份：'+backupTime(account.backup?.last_success),'muted cloud-last-backup'));
  if(account.error)info.append(node('p',account.error,'bad'));
  if(account.backup?.state==='failed'||account.backup?.state==='interrupted')info.append(node('p',account.backup.error||'上次备份中断，请重试','bad'));
  const actions=node('div','','actions cloud-account-actions');
  const now=node('button',backing?'备份中':'立即备份');now.disabled=blocked||pending||verifying||anyBackup||account.state!=='ready';now.onclick=()=>backupCloud(account.id);
  const backupControl=node('span','','cloud-verification-control');backupControl.append(now);if(backing)backupControl.append(spinner());actions.append(backupControl);
  const button=node('button',verifying?'验证中':account.state==='ready'?'重新验证':'验证连接','secondary');button.disabled=verifying||backing||pending||blocked;button.onclick=()=>verifyCloud(account.id);
  const verificationControl=node('span','','cloud-verification-control');verificationControl.append(button);if(verifying){verificationControl.append(spinner());verificationControl.setAttribute('aria-busy','true')}actions.append(verificationControl);
  const reauthorize=node('button','重新授权','secondary');reauthorize.disabled=verifying||backing||pending||blocked;reauthorize.onclick=()=>openCloudAccount(account);actions.append(reauthorize);
  const remove=node('button','删除','secondary danger');remove.disabled=verifying||backing||pending||blocked;remove.onclick=()=>askCloudDelete(account);actions.append(remove);
  row.append(info,actions);root.append(row);
 }
 document.querySelector('#cloud-state').textContent=accounts.length?'已连接 '+accounts.length+' 个账号':'尚未连接网盘账号';
 document.querySelector('#cloud-add').disabled=blocked||!cloud.prepared||bulkCloud||anyBackup;
 const all=document.querySelector('#cloud-now');all.disabled=blocked||!accounts.length||accounts.some(a=>a.state!=='ready')||anyBackup||bulkCloud;all.replaceChildren(document.createTextNode(anyBackup||bulkCloud?'正在备份':'立即备份到所有云盘'));if(anyBackup||bulkCloud)all.append(spinner());
}

function openCloudAccount(account=null){cloudReauthorize=account;document.querySelector('#cloud-name').value=account?.name||'我的阿里云盘';document.querySelector('#cloud-dialog-title').textContent=account?'更新阿里云盘授权':'连接阿里云盘';document.querySelector('#cloud-dialog').showModal();document.querySelector('#cloud-form').hidden=true;startCloudAuthorization()}
async function startCloudAuthorization(){
 const generation=++cloudGeneration;const button=document.querySelector('#cloud-authorize'),status=document.querySelector('#cloud-dialog-status');button.disabled=true;status.textContent='正在生成二维码…';
 try{const result=await request('/api/action',{action:'cloud-authorize',provider:'aliyun',name:document.querySelector('#cloud-name').value,account:cloudReauthorize?.id});if(generation!==cloudGeneration||!document.querySelector('#cloud-dialog').open){request('/api/action',{action:'cloud-authorize-cancel',session:result.session}).catch(()=>{});return}cloudSession=result.session;document.querySelector('#cloud-qr').src=result.qr_image;document.querySelector('#cloud-form').hidden=true;document.querySelector('#cloud-qr-panel').hidden=false;status.textContent='';cloudTimer=setTimeout(pollCloudAuthorization,2500)}catch(error){if(generation!==cloudGeneration)return;status.textContent=error.message;document.querySelector('#cloud-form').hidden=false;button.disabled=false}
}
async function pollCloudAuthorization(){
 const session=cloudSession;if(!session)return;
 try{const result=await request('/api/action',{action:'cloud-authorize-poll',session});if(session!==cloudSession)return;
 if(result.state==='authorized'){cloudSession=null;closeCloudAccount();await refresh();return}
 document.querySelector('#cloud-dialog-status').textContent=result.state==='scanned'?'请在手机确认授权':'';cloudTimer=setTimeout(pollCloudAuthorization,3000);
 }catch(error){if(session!==cloudSession)return;cloudSession=null;document.querySelector('#cloud-dialog-status').textContent=error.message;document.querySelector('#cloud-form').hidden=false;document.querySelector('#cloud-authorize').disabled=false;document.querySelector('#cloud-qr-panel').hidden=true}
}
function closeCloudAccount(){
 cloudGeneration++;clearTimeout(cloudTimer);const session=cloudSession;cloudSession=null;if(session)request('/api/action',{action:'cloud-authorize-cancel',session}).catch(()=>{});
 document.querySelector('#cloud-dialog').close();document.querySelector('#cloud-form').hidden=false;document.querySelector('#cloud-authorize').disabled=false;document.querySelector('#cloud-qr-panel').hidden=true;document.querySelector('#cloud-qr').removeAttribute('src');document.querySelector('#cloud-dialog-status').textContent='';
}
document.querySelector('#cloud-dialog').addEventListener('cancel',event=>{event.preventDefault();closeCloudAccount()});
const pendingCloud=new Set();
async function verifyCloud(account){pendingCloud.add(account);renderCloud(window.viewStatus?.cloud,Boolean(window.viewStatus?.storage_blocked));try{await request('/api/action',{action:'cloud-verify',account})}catch(error){pageNotice(error.message)}finally{pendingCloud.delete(account);await refresh()}}

function repaintCloud(){renderCloud(window.viewStatus?.cloud,Boolean(window.viewStatus?.storage_blocked))}
async function backupCloud(account){if(account)pendingCloudActions.set(account,'backup');else bulkCloud=true;repaintCloud();try{await request('/api/action',{action:'cloud-backup',...(account?{account}:{})})}catch(error){pageNotice(error.message)}finally{if(account)pendingCloudActions.delete(account);else bulkCloud=false;await refresh()}}
async function toggleCloudAuto(account,enabled){pendingCloudActions.set(account,'auto');repaintCloud();try{await request('/api/action',{action:'cloud-auto',account,enabled})}catch(error){pageNotice(error.message)}finally{pendingCloudActions.delete(account);await refresh()}}
function askCloudDelete(account){deletingCloud=account.id;document.querySelector('#cloud-delete-name').textContent=account.profile?.nick_name||account.profile?.name||account.name;document.querySelector('#cloud-delete-error').textContent='';document.querySelector('#cloud-delete-dialog').showModal()}
function cancelCloudDelete(){deletingCloud=null;document.querySelector('#cloud-delete-dialog').close()}
async function confirmCloudDelete(){const account=deletingCloud;if(!account)return;const button=document.querySelector('#cloud-delete-confirm');button.disabled=true;try{await request('/api/action',{action:'cloud-delete',account,confirmed:true});cancelCloudDelete();await refresh()}catch(error){document.querySelector('#cloud-delete-error').textContent=error.message}finally{button.disabled=false}}
document.querySelector('#cloud-delete-dialog').addEventListener('cancel',()=>{deletingCloud=null});
