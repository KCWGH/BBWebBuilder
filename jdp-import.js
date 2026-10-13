/* Read only the files referenced by a locally selected JDP. */
(function(root){
  function path(value){
    const normalized=value.replace(/\\/g,'/');
    if(!normalized||/[\x00-\x1f:]/.test(normalized)||normalized.split('/').some(p=>!p||p==='..')||new TextEncoder().encode(normalized).length>500)throw Error(`지원하지 않는 JDP 파일 경로: ${value}`);
    const relative=normalized.split('/').filter(p=>p!=='.').join('/');
    if(!relative)throw Error(`지원하지 않는 JDP 파일 경로: ${value}`);
    return relative;
  }
  function references(text){
    let section='';const files=new Map();
    for(const line of text.replace(/^\uFEFF/,'').split(/\r?\n/)){
      const clean=line.trim();if(!clean)continue;
      if(clean===']'){section='';continue}
      if(clean.startsWith('[')){section=clean.startsWith('[Files')?'files':clean.startsWith('[Icons')?'icons':'';continue}
      if(section&&clean.includes('.')){const name=path(clean);files.set(name.toLowerCase(),name)}
    }
    const result=[...files.values()];
    if(!result.some(p=>p.toLowerCase().endsWith('.java')))throw Error('JDP에 Java 소스가 없습니다.');
    return result;
  }
  async function prepare(file){
    const text=await file.text();return {file,text,paths:references(text)};
  }
  async function collect(project,read){
    const jdp=await read(project.file.name);
    if(!jdp||await jdp.text()!==project.text)throw Error(`선택한 폴더에 동일한 ${project.file.name} 파일이 없습니다. JDP가 있는 프로젝트 폴더를 선택하세요.`);
    const files=[project.file],paths=[project.file.name],missing=[];
    for(const relative of project.paths){
      const file=await read(relative);
      if(file){files.push(file);paths.push(relative)}else missing.push(relative);
    }
    if(missing.length)throw Error(`JDP가 참조하는 파일이 없습니다 (${missing.length}개):\n${missing.slice(0,30).join('\n')}${missing.length>30?'\n…':''}`);
    if(files.reduce((sum,file)=>sum+file.size,0)>128*1024*1024)throw Error('업로드 한도는 128 MiB입니다.');
    return {files,paths};
  }
  async function fromDirectory(project,directory){
    return collect(project,async relative=>{
      const parts=relative.split('/');let folder=directory;
      try{
        for(const segment of parts.slice(0,-1))folder=await folder.getDirectoryHandle(segment);
        return await (await folder.getFileHandle(parts.at(-1))).getFile();
      }catch(error){if(error.name==='NotFoundError'||error.name==='TypeMismatchError')return null;throw error}
    });
  }
  async function fromFiles(project,selection){
    const files=new Map();
    for(const file of selection){
      const relative=file.webkitRelativePath.split('/').slice(1).join('/').toLowerCase();
      if(files.has(relative))throw Error(`대소문자만 다른 파일 경로가 있습니다: ${relative}`);
      files.set(relative,file);
    }
    return collect(project,relative=>files.get(relative.toLowerCase()));
  }
  const api={references,prepare,fromDirectory,fromFiles};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.JdpImport=api;
})(globalThis);
