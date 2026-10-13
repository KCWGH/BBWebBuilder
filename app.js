
const $=id=>document.getElementById(id);let pid='',project=null,jid='',config={},pollTimer=null,busy=false;
let treeModel=null;
let localProject=null;
const folderOpen=new Map();
const folderControls=[];

function showError(message){$('error').textContent=message;$('error').hidden=false}
function clearError(){$('error').hidden=true}
function lockInputs(value){
  busy=value;
  for(const id of ['open','folderButton','zipButton','selectProjectFolder','cancelLocalProject','jdps','build','all','none','title','name','vendor','version','icon'])$(id).disabled=value;
  if(project)renderTree();
}
function makeTree(files){
  const root={children:new Map(),files:[]};
  for(const file of files){
    const parts=file.path.split('/');let node=root;node.files.push(file);
    for(let i=0;i<parts.length-1;i++){
      const name=parts[i],path=parts.slice(0,i+1).join('/');
      if(!node.children.has(name))node.children.set(name,{name,path,children:new Map(),files:[]});
      node=node.children.get(name);node.files.push(file);
    }
    node.children.set(parts[parts.length-1],{name:parts[parts.length-1],file});
  }
  return root;
}
function renderProject(data){
  project=data;jid='';$('fileDetails').open=false;folderOpen.clear();treeModel=makeTree(data.files);$('search').value='';
  $('resultArea').hidden=true;$('empty').hidden=true;$('projectArea').hidden=false;
  $('projectLabel').textContent=data.jdp;
  for(const key of ['title','name','vendor','version'])$(key).value=data[key];
  $('icon').replaceChildren(new Option('없음',''),...data.files.filter(f=>/\.(png|gif|jpe?g)$/i.test(f.path)&&f.exists).map(f=>new Option(f.path,f.path)));
  $('icon').value=data.icon;$('status').textContent='준비됨';$('logs').textContent='';renderTree();
}
function renderTree(){
  const search=$('search').value.toLowerCase();folderControls.length=0;
  const list=document.createElement('ul');
  function append(parent,node){
    if(node.file){
      const file=node.file;if(search&&!file.path.toLowerCase().includes(search))return;
      const item=document.createElement('li'),label=document.createElement('label');
      label.className='file'+(file.exists?'':' missing');label.title=file.path;
      const box=document.createElement('input');box.type='checkbox';box.checked=file.selected;box.disabled=!file.exists||busy;
      box.setAttribute('aria-label',file.path+' 선택');
      box.onchange=()=>{file.selected=box.checked;refreshFolderChecks();updateCount()};
      const name=document.createElement('span');name.className='file-name';name.textContent=node.name+(file.exists?'':' (파일 없음)');
      label.append(box,name);item.append(label);parent.append(item);return;
    }
    if(search&&!node.files.some(f=>f.path.toLowerCase().includes(search)))return;
    const item=document.createElement('li'),details=document.createElement('details'),summary=document.createElement('summary');
    details.dataset.path=node.path;
    details.open=!!search||(folderOpen.has(node.path)?folderOpen.get(node.path):false);
    const content=document.createElement('span');content.className='folder-content';
    const box=document.createElement('input');box.type='checkbox';box.setAttribute('aria-label',node.path+' 폴더 선택');
    box.onclick=event=>event.stopPropagation();box.onkeydown=event=>event.stopPropagation();
    box.onchange=()=>{for(const file of node.files)file.selected=file.exists&&box.checked;renderTree()};
    const name=document.createElement('span');name.className='folder-name';name.textContent=node.name;
    const count=document.createElement('span');count.className='folder-count';
    content.append(box,name,count);summary.append(content);
    const children=document.createElement('ul');children.className='children';appendChildren(children,node);
    details.append(summary,children);item.append(details);parent.append(item);
    folderControls.push({node,box,count});
    details.ontoggle=()=>{if(details.isConnected&&!search)folderOpen.set(node.path,details.open);updateExpandButton()};
  }
  function appendChildren(parent,node){
    const sorted=[...node.children.values()].sort((a,b)=>Number(!!a.file)-Number(!!b.file)||a.name.localeCompare(b.name,'en',{numeric:true,sensitivity:'base'}));
    for(const child of sorted)append(parent,child);
  }
  appendChildren(list,treeModel);$('tree').replaceChildren(list);
  if(!list.children.length){const empty=document.createElement('p');empty.textContent='검색 결과 없음';$('tree').append(empty)}
  refreshFolderChecks();updateCount();updateExpandButton();
}
function refreshFolderChecks(){
  for(const {node,box,count} of folderControls){
    const selected=node.files.filter(f=>f.selected).length;
    box.checked=selected===node.files.length;box.indeterminate=selected>0&&selected<node.files.length;
    box.disabled=busy||!node.files.some(f=>f.exists);count.textContent=`${selected}/${node.files.length}`;
  }
}
function updateExpandButton(){
  const folders=[...$('tree').querySelectorAll('details')];
  $('expand').textContent=folders.length&&folders.every(f=>f.open)?'전체 접기':'전체 펼치기';
  $('expand').disabled=!folders.length||!!$('search').value;
}
$('expand').onclick=()=>{
  const folders=[...$('tree').querySelectorAll('details')],open=!folders.every(f=>f.open);
  for(const folder of folders){folderOpen.set(folder.dataset.path,open);folder.open=open}updateExpandButton();
};
$('theme').value=document.documentElement.dataset.theme||'system';
$('theme').onchange=()=>{document.documentElement.dataset.theme=$('theme').value;try{localStorage.setItem('bb-theme',$('theme').value)}catch{}};
$('open').onclick=action(async()=>{
  if(!config.native_picker){$('jdpFile').click();return}
  lockInputs(true);$('toolStatus').textContent='파일 선택 창에서 JDP를 선택하세요.';
  try{
    const data=await api('/api/open-dialog',{method:'POST'});
    if(!data.cancelled){setProject(data);$('location').textContent=data.location;$('location').hidden=false}
  }finally{lockInputs(false);$('toolStatus').textContent=config.tools_ready?'Java6 / JDE 7.1':'Java6 / JDE 경로를 확인하세요'}
});
function renderResults(job){
  $('resultArea').hidden=false;$('moduleCount').textContent=`COD ${job.modules}개`;$('results').replaceChildren();
  const order=file=>file.name.endsWith('.zip')?0:file.name.endsWith('.jad')?1:2;
  const metadata=job.files.filter(f=>!f.name.endsWith('.cod')).sort((a,b)=>order(a)-order(b)||a.name.localeCompare(b.name));
  const moduleNumber=file=>Number(file.name.match(/-(\d+)\.cod$/)?.[1]||0);
  const modules=job.files.filter(f=>f.name.endsWith('.cod')).sort((a,b)=>moduleNumber(a)-moduleNumber(b)||a.name.localeCompare(b.name,'en',{numeric:true}));
  function appendFile(file){
    const row=document.createElement('tr'),name=document.createElement('td'),size=document.createElement('td'),link=document.createElement('a');
    link.href=`releases/${jid}/${encodeURIComponent(file.name)}`;link.textContent=file.name;link.download=file.name;
    name.append(link);size.textContent=`${(file.size/1024).toFixed(1)} KiB`;row.append(name,size);$('results').append(row);
  }
  let expanded=false;
  function renderDownloads(){
    $('results').replaceChildren();metadata.forEach(appendFile);
    if(modules.length<=8){modules.forEach(appendFile);return}
    modules.slice(0,3).forEach(appendFile);
    const row=document.createElement('tr'),cell=document.createElement('td'),toggle=document.createElement('button');
    row.className='cod-toggle';cell.colSpan=2;
    toggle.textContent=expanded?'중간 COD 접기':`… COD ${modules.length-6}개 생략 · 펼치기`;
    toggle.setAttribute('aria-expanded',String(expanded));
    toggle.onclick=()=>{expanded=!expanded;renderDownloads();$('results').querySelector('.cod-toggle button').focus()};
    cell.append(toggle);row.append(cell);$('results').append(row);
    if(expanded)modules.slice(3,-3).forEach(appendFile);
    modules.slice(-3).forEach(appendFile);
  }
  renderDownloads();
  $('otaNotice').textContent='OTA 설치: ZIP의 JAD와 모든 COD를 같은 서버 폴더에 올리고 JAD 링크를 여세요.';
  updateUploadState();
  $('uploadResult').hidden=true;
}
function updateUploadState(){
  $('uploadHint').textContent=config.package_upload_ready?'':'업로드하려면 .env에 BB_UPLOAD_TOKEN을 설정하고 저장하세요.';
}
setInterval(async()=>{
  if(document.hidden||busy||$('resultArea').hidden)return;
  try{config=await api('/api/config');updateUploadState()}catch{}
},5000);
(async()=>{try{config=await api('/api/config');$('logout').hidden=!config.authentication_required;$('toolStatus').textContent=config.tools_ready?'Java6 / JDE 7.1':'Java6 / JDE 경로를 확인하세요'}catch(e){showError(e.message)}})();
async function api(url,options={}){const response=await fetch(url.replace(/^\//,''),options);if(!response.ok){const data=await response.json().catch(()=>({detail:response.statusText}));if(response.status===401&&data.detail==='로그인이 필요합니다.')location.replace('login');throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail))}return response.json()}
$('logout').onclick=action(async()=>{await api('/api/auth/logout',{method:'POST'});location.replace('login')});
function json(body){return {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}}
function action(fn){return async()=>{clearError();try{await fn()}catch(e){showError(e.message)}}}
function setProject(data){localProject=null;$('localProject').hidden=true;pid=data.id;$('jdps').replaceChildren(...data.jdps.map(p=>new Option(p,p)));$('jdpPicker').hidden=data.jdps.length<2;$('location').hidden=!data.location;renderProject(data.project)}
function updateCount(){$('count').textContent=`${project.files.filter(f=>f.selected).length}개 선택 / ${project.files.length}개 파일`}
async function importFiles(files,folder,paths){lockInputs(true);$('toolStatus').textContent='프로젝트 가져오는 중…';try{const form=new FormData();for(const file of files)form.append('files',file);form.append('paths',JSON.stringify(paths||[...files].map(f=>folder?f.webkitRelativePath:f.name)));setProject(await api('/api/import',{method:'POST',body:form}))}finally{lockInputs(false);$('toolStatus').textContent=config.tools_ready?'Java6 / JDE 7.1':'Java6 / JDE 경로를 확인하세요'}}
$('jdpFile').onchange=action(async()=>{
  const file=$('jdpFile').files[0];$('jdpFile').value='';if(!file)return;
  localProject=null;$('localProject').hidden=true;
  if(!file.name.toLowerCase().endsWith('.jdp'))throw Error('JDP 파일을 선택하세요.');
  localProject=await JdpImport.prepare(file);
  $('localProjectHint').textContent=`${file.name} · 참조 파일 ${localProject.paths.length}개. JDP가 있는 폴더를 선택하세요.`;
  $('localProject').hidden=false;
});
$('cancelLocalProject').onclick=()=>{localProject=null;$('localProject').hidden=true;clearError()};
async function importLocalProject(read){
  if(!localProject||busy)return;
  lockInputs(true);$('toolStatus').textContent='JDP 참조 파일 확인 중…';
  try{const selection=await read(localProject);await importFiles(selection.files,false,selection.paths)}
  finally{lockInputs(false);$('toolStatus').textContent=config.tools_ready?'Java6 / JDE 7.1':'Java6 / JDE 경로를 확인하세요'}
}
$('selectProjectFolder').onclick=()=>{
  if(!localProject||busy)return;
  clearError();$('jdpFolder').click();
};
$('jdpFolder').onchange=action(async()=>{
  const files=[...$('jdpFolder').files];$('jdpFolder').value='';if(files.length)await importLocalProject(p=>JdpImport.fromFiles(p,files));
});
$('folderButton').onclick=()=>$('folder').click();$('zipButton').onclick=()=>$('zip').click();$('folder').onchange=action(async()=>{if($('folder').files.length)await importFiles($('folder').files,true);$('folder').value=''});$('zip').onchange=action(async()=>{if($('zip').files.length)await importFiles($('zip').files,false);$('zip').value=''});
$('jdps').onchange=action(async()=>renderProject(await api(`/api/projects/${pid}?jdp=${encodeURIComponent($('jdps').value)}`)));
$('search').oninput=()=>project&&renderTree();$('all').onclick=()=>{project.files.forEach(f=>f.selected=f.exists);renderTree()};$('none').onclick=()=>{project.files.forEach(f=>f.selected=false);renderTree()};
$('build').onclick=action(async()=>{if(busy)return;const body={project_id:pid,jdp:project.jdp,files:project.files.filter(f=>f.selected).map(f=>f.path)};for(const key of ['title','name','vendor','version','icon'])body[key]=$(key).value;lockInputs(true);$('resultArea').hidden=true;$('status').textContent='빌드 요청 중…';try{const job=await api('/api/build',json(body));jid=job.id;await poll()}catch(e){lockInputs(false);$('status').textContent='실패';throw e}});
async function poll(){try{const job=await api(`/api/jobs/${jid}`);$('status').textContent=({queued:'대기 중',building:'빌드 중',complete:'완료',failed:'실패'})[job.status];$('logs').textContent=job.logs.join('\n')||'빌드 대기 중…';$('logs').scrollTop=$('logs').scrollHeight;if(job.status==='queued'||job.status==='building'){pollTimer=setTimeout(poll,1000)}else{lockInputs(false);if(job.status==='complete'){config=await api('/api/config');renderResults(job);if(window.confirm('빌드가 완료되었습니다. 앱스토어에 업로드하시겠습니까?'))await uploadRelease();else{$('uploadResult').hidden=false;$('uploadResult').textContent='업로드하지 않았습니다.'}}}}catch(e){lockInputs(false);showError(e.message)}}
async function uploadRelease(){
  lockInputs(true);
  try{
    $('uploadResult').hidden=false;$('uploadResult').textContent='업로드 중…';
    const result=await api(`/api/jobs/${jid}/upload`,{method:'POST'});
    $('uploadResult').textContent=result.response;
    if($('storeDetails').open)await refreshStore();
  }catch(e){$('uploadResult').textContent=e.message;showError(e.message)}
  finally{lockInputs(false)}
}

let storeBusy=false,storeLoaded=false;
function lockStore(value){storeBusy=value;$('storeRefresh').disabled=value;for(const button of $('storeApps').querySelectorAll('button'))button.disabled=value||button.dataset.invalid==='true'}
async function refreshStore(){
  if(storeBusy)return;
  lockStore(true);$('storeStatus').textContent='조회 중…';
  try{
    const data=await api('/api/store/apps');$('storeApps').replaceChildren();
    for(const app of data.apps){
      const row=document.createElement('tr');
      for(const value of [app.app_name,app.version,app.status]){const cell=document.createElement('td');cell.textContent=value||'';row.append(cell)}
      const cell=document.createElement('td'),button=document.createElement('button');button.textContent='삭제';button.dataset.invalid=String(!app.app_id);button.disabled=!app.app_id;
      button.onclick=action(async()=>{
        if(storeBusy||!window.confirm(`“${app.app_name}” 앱과 모든 버전을 앱스토어에서 삭제하시겠습니까?`))return;
        lockStore(true);$('storeStatus').textContent='삭제 중…';
        try{const result=await api(`/api/store/apps/${encodeURIComponent(app.app_id)}`,{method:'DELETE'});row.remove();$('storeStatus').textContent=`${result.app_name} 삭제 완료 · ${result.deleted_versions}개 버전`}
        catch(e){$('storeStatus').textContent='삭제 결과를 확인하지 못했습니다. 새로고침하세요.';throw e}
        finally{lockStore(false)}
      });cell.append(button);row.append(cell);$('storeApps').append(row);
    }
    storeLoaded=true;$('storeStatus').textContent=`${data.apps.length}개 앱`;
  }catch(e){$('storeStatus').textContent=e.message;throw e}finally{lockStore(false)}
}
$('storeRefresh').onclick=action(refreshStore);
$('storeDetails').ontoggle=()=>{if($('storeDetails').open&&!storeLoaded)refreshStore().catch(()=>{})};
if($('storeDetails').open)refreshStore().catch(()=>{});
