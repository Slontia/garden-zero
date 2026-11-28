function backupTime(value){if(!value)return '尚无成功记录';if(/^\d{8}-\d{6}$/.test(value))return value.replace(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/,'$1-$2-$3 $4:$5:$6');let date=new Date(value);return Number.isNaN(date.getTime())?value:date.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).replaceAll('/','-')}
function detailList(selector,items){let root=document.querySelector(selector);root.replaceChildren();for(let [label,value] of items)root.append(node('dt',label),node('dd',value??'—'))}
function backupPath(path){return path?.replace(/^\/mnt\/([a-z])\//,(_,drive)=>drive.toUpperCase()+':\\').replace(/\//g,'\\')||'尚未配置'}
function outcome(state){return ({success:'成功',interrupted:'中断',failed:'失败',running:'进行中',success_with_resume_error:'失败'})[state]||'尚未备份'}
let historyRows=[],historyPage=1;const historyPageSize=20;
function historyTarget(row){return row.type+':'+(row.target?.id||row.target?.uid||row.target?.name||'unknown')}
function historyState(row){return row.state==='success_with_resume_error'?'failed':row.state}
function renderBackupHistory(rows){
 rows=rows.filter(row=>row.type==='disk');historyRows=rows;const select=document.querySelector('#history-target'),selected=select.value;
 const targets=new Map(rows.map(row=>[historyTarget(row),(row.target?.name||'未知目标')]));
 const signature=JSON.stringify([...targets]);
 if(select.dataset.options!==signature){select.replaceChildren(new Option('全部目标',''));for(const [id,name] of targets)select.add(new Option(name,id));select.value=targets.has(selected)?selected:'';select.dataset.options=signature}
 drawBackupHistory();
}
function filterBackupHistory(){historyPage=1;drawBackupHistory()}
function changeHistoryPage(delta){historyPage+=delta;drawBackupHistory()}
function drawBackupHistory(){
 const state=document.querySelector('#history-state').value,target=document.querySelector('#history-target').value;
 const rows=historyRows.filter(row=>(!state||historyState(row)===state)&&(!target||historyTarget(row)===target));
 const pages=Math.max(1,Math.ceil(rows.length/historyPageSize));historyPage=Math.max(1,Math.min(historyPage,pages));
 const root=document.querySelector('#backup-history'),open=new Set([...root.querySelectorAll('details[open]')].map(item=>item.dataset.key));root.replaceChildren();
 for(const row of rows.slice((historyPage-1)*historyPageSize,historyPage*historyPageSize)){
  const tr=node('tr');tr.dataset.kind=row.type;tr.dataset.state=historyState(row);const stamp=row.started||row.finished||row.time;
  for(const value of [stamp?backupTime(stamp):'未记录',row.target?.name||'未知目标'])tr.append(node('td',value));
  const result=node('td');result.append(node('span',outcome(row.state),'backup-outcome outcome-'+historyState(row)));tr.append(result);
  const cell=node('td'),details=node('details');details.dataset.key=row.id;details.open=open.has(row.id);details.append(node('summary','展开'));
  const fields=[['开始',row.started?backupTime(row.started):null],['结束',row.finished||row.time?backupTime(row.finished||row.time):null],['UID',row.target?.uid],['目标内路径',row.target?.relative_path],['恢复点',row.source],['快照 ID',row.snapshot_id],['备份模式',row.consistency==='eventual'?'在线版本备份（最终一致性）':row.consistency==='application_consistent'?'停写一致性备份':'历史记录（模式未记录）'],['错误',row.error],['应用恢复错误',row.resume_error],['记录说明',row.legacy?'早期记录，部分信息未记录':null]];
  const list=node('dl','','backup-record-detail');for(const [label,value] of fields)if(value){list.append(node('dt',label),node('dd',String(value)))}
  if(!list.children.length)list.append(node('dd',row.state==='running'?'任务进行中':'没有更多信息'));details.append(list);cell.append(details);tr.append(cell);root.append(tr);
 }
 document.querySelector('#history-count').textContent=rows.length+' 条';document.querySelector('#history-empty').hidden=rows.length>0;
 document.querySelector('#history-prev').disabled=historyPage<=1;document.querySelector('#history-next').disabled=historyPage>=pages;
 document.querySelector('#history-page').textContent=historyPage+' / '+pages;document.querySelector('.backup-pagination').hidden=pages<=1;
}
function wireBackupHelp(root=document){
 for(const help of root.querySelectorAll('.backup-help[data-help]')){
  const text=help.dataset.help;delete help.dataset.help;const button=node('button',help.classList.contains('cloud-warning')?'⚠':'ⓘ','backup-help-trigger'),tip=node('span',text,'backup-help-tooltip');button.type='button';if(help.helpAction)button.onclick=help.helpAction;button.setAttribute('aria-label',text);tip.setAttribute('role','tooltip');
  button.onkeydown=event=>{if(event.key==='Escape'){help.dataset.dismissed='true';event.stopPropagation()}};button.onfocus=button.onmouseenter=()=>{delete help.dataset.dismissed;const rect=button.getBoundingClientRect(),width=Math.min(300,innerWidth-32);tip.style.left=Math.max(16,Math.min(rect.left,innerWidth-width-16))+'px';tip.style.top=Math.max(12,Math.min(rect.bottom+8,innerHeight-tip.getBoundingClientRect().height-12))+'px'};button.onblur=()=>{delete help.dataset.dismissed};help.append(button,tip);
 }
}
async function refresh(){try{let d=await request('/api/status'),blocked=Boolean(d.storage_blocked);window.viewStatus=d;document.querySelector('#storage-alert').hidden=!blocked;renderBackupHistory(d.backup_history||[]);renderBackupControl(d.backup_control||{});renderCloud(d.cloud,blocked);pageNotice(null);let badge=document.querySelector('#connection-badge');badge.className='connection-badge online';badge.textContent='管理服务已连接'}catch(e){pageNotice(e.message);let badge=document.querySelector('#connection-badge');badge.className='connection-badge warning';badge.textContent='连接暂时中断'}}
decorateIcons();wireBackupHelp();refresh();setInterval(refresh,3000);

let cloudSession=null,cloudTimer=null,cloudBusy=false,cloudReauthorize=null,cloudGeneration=0;
let pendingCloudActions=new Map(),deletingCloud=null;
function renderCloud(cloud={},blocked=false){
 const accounts=cloud.accounts||[],root=document.querySelector('#cloud-accounts');root.replaceChildren();
 for(const account of accounts){
  const verifying=account.state==='verifying',backing=account.backup?.state==='running';
  const pending=pendingCloudActions.has(account.id),row=node('div','','cloud-account');row.dataset.account=account.id;
  const info=node('div','','cloud-account-info'),profile=account.profile||{},display=profile.nick_name||profile.name||profile.user_name||account.name;
  const heading=node('div','','cloud-account-heading'),automatic=account.auto_backup!==false;
  const auto=node('button',automatic?'自动备份已开启':'自动备份已关闭','cloud-auto-status '+(automatic?'enabled':'disabled'));auto.disabled=pending||verifying||blocked;auto.setAttribute('aria-pressed',String(automatic));auto.onclick=()=>toggleCloudAuto(account.id,!automatic);
  heading.append(node('strong',display));
  const warning=account.health?.message||account.error||account.backup?.last_error||(account.backup?.state==='failed'?account.backup.error:null);
  if(warning){const help=node('span','','backup-help cloud-warning');help.dataset.help=warning+(verifying?'；后台正在检查，请稍后重新授权':'；点击重新授权');help.helpAction=()=>verifying?pageNotice('后台正在检查，请稍后重新授权'):openCloudAccount(account);heading.append(help)}
  if(verifying){const loading=spinner();loading.setAttribute('aria-label','后台检查中');heading.append(loading)}
  heading.append(auto);info.append(heading);
  if(backing||verifying)info.append(node('p',verifying?'后台检查中':'备份中','muted'));
  if(profile.user_name||profile.user_id){const help=node('span','','backup-help');help.dataset.help=[profile.user_name&&('用户名：'+profile.user_name),profile.user_id&&('用户 ID：'+profile.user_id)].filter(Boolean).join('；');heading.append(help)}
  info.append(node('p','上次成功备份：'+backupTime(account.backup?.last_success),'muted cloud-last-backup'));
  const actions=node('div','','actions cloud-account-actions');
  const remove=node('button','删除','secondary danger');remove.disabled=verifying||pending||blocked;remove.onclick=()=>askCloudDelete(account);actions.append(remove);
  row.append(info,actions);root.append(row);wireBackupHelp(row);
 }
 document.querySelector('#cloud-state').textContent='尚未连接网盘账号';document.querySelector('#cloud-state').hidden=accounts.length>0;
 document.querySelector('#cloud-add').disabled=blocked||!cloud.prepared;
}

function openCloudAccount(account=null){cloudReauthorize=account;document.querySelector('#cloud-name').value=account?.name||'我的阿里云盘';document.querySelector('#cloud-dialog-title').textContent=account?'更新阿里云盘授权':'连接阿里云盘';document.querySelector('#cloud-dialog').showModal();document.querySelector('#cloud-form').hidden=true;startCloudAuthorization()}
async function startCloudAuthorization(){
 const generation=++cloudGeneration;const button=document.querySelector('#cloud-authorize'),status=document.querySelector('#cloud-dialog-status');button.disabled=true;status.textContent='正在生成二维码…';
 try{const result=await request('/api/action',{action:'cloud-authorize',provider:'aliyun',name:document.querySelector('#cloud-name').value,account:cloudReauthorize?.id});if(generation!==cloudGeneration||!document.querySelector('#cloud-dialog').open){request('/api/action',{action:'cloud-authorize-cancel',session:result.session}).catch(()=>{});return}if(result.state==='waiting_for_backup'){status.textContent='正在安全结束当前备份，随后显示授权二维码…';cloudTimer=setTimeout(startCloudAuthorization,1500);return}cloudSession=result.session;document.querySelector('#cloud-qr').src=result.qr_image;document.querySelector('#cloud-form').hidden=true;document.querySelector('#cloud-qr-panel').hidden=false;status.textContent='';cloudTimer=setTimeout(pollCloudAuthorization,2500)}catch(error){if(generation!==cloudGeneration)return;status.textContent=error.message;document.querySelector('#cloud-form').hidden=false;button.disabled=false}
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
function repaintCloud(){renderCloud(window.viewStatus?.cloud,Boolean(window.viewStatus?.storage_blocked))}
async function toggleCloudAuto(account,enabled){pendingCloudActions.set(account,'auto');repaintCloud();try{await request('/api/action',{action:'cloud-auto',account,enabled})}catch(error){pageNotice(error.message)}finally{pendingCloudActions.delete(account);await refresh()}}
function askCloudDelete(account){deletingCloud=account.id;document.querySelector('#cloud-delete-name').textContent=account.profile?.nick_name||account.profile?.name||account.name;document.querySelector('#cloud-delete-error').textContent='';document.querySelector('#cloud-delete-dialog').showModal()}
function cancelCloudDelete(){deletingCloud=null;document.querySelector('#cloud-delete-dialog').close()}
async function confirmCloudDelete(){
 const account=deletingCloud;if(!account)return;const button=document.querySelector('#cloud-delete-confirm'),cancel=document.querySelector('#cloud-delete-dialog .secondary');button.disabled=true;cancel.disabled=true;pendingCloudActions.set(account,'delete');repaintCloud();
 try{
  for(let attempt=0;attempt<120;attempt++){
   const result=await request('/api/action',{action:'cloud-delete',account,confirmed:true});
   if(result.state!=='waiting_for_backup'){deletingCloud=null;document.querySelector('#cloud-delete-dialog').close();await refresh();return}
   document.querySelector('#cloud-delete-error').textContent='正在安全结束当前备份，随后解除连接…';await new Promise(resolve=>setTimeout(resolve,1500));
  }
  throw new Error('备份尚未安全退出，连接已保留，请稍后重试删除');
 }catch(error){document.querySelector('#cloud-delete-error').textContent=error.message}
 finally{pendingCloudActions.delete(account);button.disabled=false;cancel.disabled=false;await refresh()}
}
document.querySelector('#cloud-delete-dialog').addEventListener('cancel',event=>{if(pendingCloudActions.get(deletingCloud)==='delete'){event.preventDefault();return}deletingCloud=null});

function renderBackupControl(control){
 const job=control.operation||{};
 const active=job.kind==='cloud'&&['running','cancelling'].includes(job.state);
 const phases={preparing:'正在准备在线备份',exporting:'正在准备备份',uploading:'正在备份',checking:'正在检查备份'};
 document.querySelector('#backup-operation-status').textContent=active?(job.state==='cancelling'?'已请求停止，等待当前任务安全退出':phases[job.phase]||'正在准备备份'):(job.state==='interrupted'?'上次备份已中断，上一份完整备份保留':job.state==='failed'?'上次备份失败：'+(job.error||'请查看日志'):job.state==='success'?'上次任务已完成':'当前没有备份任务');
 document.querySelector('#backup-operation-row').hidden=!active;
}
