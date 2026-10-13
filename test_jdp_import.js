const test=require('node:test');
const assert=require('node:assert/strict');
const {references,prepare,fromDirectory,fromFiles}=require('./jdp-import.js');
const text='\uFEFF[Files\r\nsrc\\App.java\r\nres\\sound.mp3\r\n]\r\n[Icons\r\nres\\icon.png\r\n]\r\n[Imports\r\nother.jar\r\n]\r\n';
const file=(name,content,relative)=>{
  const value=new File([content],name);
  Object.defineProperty(value,'webkitRelativePath',{value:relative||'Project/'+name});
  return value;
};
const jdp=file('App.JDP',text);

test('JDP references preserve relative paths, deduplicate icons, and ignore other sections',()=>{
  assert.deepEqual(references(text),['src/App.java','res/sound.mp3','res/icon.png']);
  assert.deepEqual(references('[Files\na.java\nicon.png\n]\n[Icons\nicon.png\n]'),['a.java','icon.png']);
});
test('absolute and parent paths are rejected before requesting files',()=>{
  for(const path of ['../App.java','src/../../App.java','C:\\App.java','/App.java','src//App.java'])assert.throws(()=>references(`[Files\n${path}\n]`),/파일 경로/);
  assert.throws(()=>references('[Files\nimage.png\n]'),/Java 소스/);
});
test('folder input returns exactly JDP, source, resources and icons',async()=>{
  const selection=[jdp,file('App.java','source','Project/src/App.java'),file('sound.mp3','sound','Project/res/sound.mp3'),file('icon.png','icon','Project/res/icon.png'),file('.env','secret'),file('unused.java','unused','Project/src/unused.java')];
  const result=await fromFiles(await prepare(jdp),selection);
  assert.deepEqual(result.paths,['App.JDP','src/App.java','res/sound.mp3','res/icon.png']);
  assert.equal(result.files.length,4);
  assert.ok(!result.files.some(f=>f.name==='.env'||f.name==='unused.java'));
});
test('native directory access reads only referenced files without enumerating the folder',async()=>{
  const reads=[];
  const files=new Map([['App.JDP',jdp],['src/App.java',file('App.java','source')],['res/sound.mp3',file('sound.mp3','sound')],['res/icon.png',file('icon.png','icon')]]);
  const directory=(prefix='')=>({
    async getDirectoryHandle(name){return directory(prefix+name+'/')},
    async getFileHandle(name){const path=prefix+name;reads.push(path);if(!files.has(path))throw Object.assign(Error('missing'),{name:'NotFoundError'});return {getFile:async()=>files.get(path)}}
  });
  const result=await fromDirectory(await prepare(jdp),directory());
  assert.deepEqual(reads,result.paths);
});
test('wrong folder and missing files fail without returning a partial upload',async()=>{
  const project=await prepare(jdp);
  await assert.rejects(fromFiles(project,[file('App.JDP','another project')]),/동일한/);
  await assert.rejects(fromFiles(project,[jdp]),/src\/App.java[\s\S]*res\/sound.mp3[\s\S]*res\/icon.png/);
});
test('directory permission errors remain actionable',async()=>{
  await assert.rejects(fromDirectory(await prepare(jdp),{async getFileHandle(){throw Object.assign(Error('Permission denied'),{name:'NotAllowedError'})}}),/Permission denied/);
});
