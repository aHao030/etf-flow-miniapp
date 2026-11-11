// Run against generated packages: node tests/host-compile.mjs [--report <json-file>]
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const miniRoot=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const toolRoot=path.resolve(miniRoot,'../work/miniapp-validation');
const req=createRequire(path.join(toolRoot,'package.json'));
const {Parser}=req('htmlparser2');
const babel=req('@babel/parser');
const json=p=>JSON.parse(fs.readFileSync(p,'utf8'));
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const baseFile=(name,ext)=>name.slice(0,-ext.length);

function listFiles(root,dir='') {
  const result=[];
  for(const item of fs.readdirSync(path.join(root,dir),{withFileTypes:true})){
    const rel=path.posix.join(dir,item.name);
    if(item.isDirectory())result.push(...listFiles(root,rel));
    else if(item.isFile())result.push(rel);
  }
  return result.sort();
}
function constructorMethods(source,file) {
  const ast=babel.parse(source,{sourceType:'unambiguous',sourceFilename:file});
  const call=ast.program.body.map(stmt=>stmt.expression).find(expr=>expr?.type==='CallExpression'&&['Page','Component'].includes(expr.callee?.name));
  assert.ok(call && call.arguments[0]?.type==='ObjectExpression',file+' needs a Page or Component definition');
  const fields=call.arguments[0].properties;
  const methods=call.callee.name==='Page'?fields:fields.find(field=>field.key?.name==='methods')?.value?.properties||[];
  return new Set(methods.filter(prop=>prop.type==='ObjectMethod'||['FunctionExpression','ArrowFunctionExpression'].includes(prop.value?.type)).map(prop=>prop.key?.name||prop.key?.value));
}
function componentBase(file,ref) {
  return ref.startsWith('/')?ref.slice(1):path.posix.normalize(path.posix.join(path.posix.dirname(file),ref));
}
function inventory(root,platform){
  const files=listFiles(root),fileSet=new Set(files);
  const wx=platform==='wechat',ext=wx?'.wxml':'.xhsml',styleExt=wx?'.wxss':'.css',prefix=wx?'wx:':'xhs:';
  const templates=files.filter(f=>f.endsWith(ext)),styles=files.filter(f=>f.endsWith(styleExt));
  const app=json(path.join(root,'app.json'));
  assert.ok(templates.length && styles.length && app.pages?.length,'package has no registered pages, templates or styles');
  const requiredParts=base=>{for(const suffix of ['.js','.json',ext,styleExt])assert.ok(fileSet.has(base+suffix),'missing '+base+suffix);};
  for(const page of app.pages)requiredParts(page);
  for(const tab of app.tabBar?.list||[])for(const key of ['iconPath','selectedIconPath'])if(tab[key])assert.ok(fileSet.has(tab[key]),'missing tab icon '+tab[key]);
  let scripts=0,bindings=0,directives=0;
  for(const file of files.filter(f=>f.endsWith('.js'))){babel.parse(fs.readFileSync(path.join(root,file),'utf8'),{sourceType:'unambiguous',sourceFilename:file});scripts++;}
  const perTemplate=[],tags=new Set();
  for(const template of templates){
    const base=baseFile(template,ext),page=json(path.join(root,base+'.json'));
    const aliases={...app.usingComponents,...page.usingComponents};
    for(const ref of Object.values(aliases))requiredParts(componentBase(template,ref));
    const methods=constructorMethods(fs.readFileSync(path.join(root,base+'.js'),'utf8'),base+'.js');
    const handlers=[],controls=[],nativeTags=[];
    const parser=new Parser({onopentag(tag,attrs){
      tags.add(tag);nativeTags.push(tag);
      if(tag.includes('-') && !['scroll-view','swiper-item','checkbox-group','radio-group'].includes(tag))assert.ok(aliases[tag],template+' undeclared component '+tag);
      for(const [key,value] of Object.entries(attrs)){
        if(/^(bind|catch):?[a-z]/.test(key)){
          assert.ok(methods.has(value),template+' '+key+' has no JS handler '+value);
          handlers.push(tag+'.'+key+'='+value);bindings++;
        }
        if(key.startsWith('wx:')||key.startsWith('xhs:')){
          assert.ok(key.startsWith(prefix),template+' has the wrong directive '+key);
          controls.push(key.slice(prefix.length)+'='+value);directives++;
        }
      }
    }},{xmlMode:true,lowerCaseTags:false,lowerCaseAttributeNames:false,decodeEntities:false});
    parser.end(fs.readFileSync(path.join(root,template),'utf8'));
    perTemplate.push({template,base,aliases:Object.keys(aliases),handlers,controls,nativeTags});
  }
  const digest=createHash('sha256');for(const file of files)digest.update(file+'\0'+sha(fs.readFileSync(path.join(root,file)))+'\n');
  return {root,templates,styles,perTemplate,summary:{packageSha256:digest.digest('hex'),pageCount:app.pages.length,templateCount:templates.length,styleCount:styles.length,scriptCount:scripts,eventBindingCount:bindings,directiveCount:directives,tags:[...tags].sort()}};
}
function runBinary(exe,args,cwd){
  const result=spawnSync(exe,args,{cwd,encoding:'utf8',windowsHide:true,maxBuffer:16*1024*1024,timeout:30000});
  if(result.error)throw result.error;
  if(result.status!==0)throw Error(path.basename(exe)+' exited '+result.status+' '+(result.stderr||result.stdout||'').slice(0,2000));
  return result.stdout;
}
function collectNodes(node,result=[]){
  if(node && typeof node==='object'){
    result.push(node);
    for(const child of node.children||[])collectNodes(child,result);
  }
  return result;
}
function compileWechat(source){
  const {root,templates,styles,perTemplate}=source;
  const compilerJson=req.resolve('miniprogram-compiler/package.json'),compiler=json(compilerJson);
  const binDir={win32:'windows',linux:'linux',darwin:'mac'}[process.platform];
  if(!binDir)throw Error('Unsupported WeChat compiler OS '+process.platform);
  const binary=name=>path.join(path.dirname(compilerJson),'bin',binDir,name+(process.platform==='win32'?'.exe':''));
  const componentArgs=perTemplate.map(entry=>[entry.template,entry.aliases.length,...entry.aliases].join(' '));
  const templateJs=runBinary(binary('wcc'),['-d','-cc',[componentArgs.length,...componentArgs].join(' '),...templates,'-gn','$gwx'],root);
  const context=vm.createContext({window:{},global:{},console});
  new vm.Script(templateJs,{filename:'wcc-render.js'}).runInContext(context,{timeout:5000});
  // Create input data inside the renderer realm, as the bundled compiler checks Array identity.
  const render=(file,data)=>{
    context.file=file;context.payload=JSON.stringify(data);
    return new vm.Script('$gwx(file)(JSON.parse(payload))').runInContext(context,{timeout:3000});
  };
  for(const file of templates)assert.equal(render(file,{}).tag,'wx-page','wcc render tree '+file);
  const probe={indexNames:['沪深300'],indexName:'沪深300',period:'3',periods:[{id:'1',name:'1个月'},{id:'3',name:'3个月'}],busy:true,ready:false};
  const file='pages/broad/index.wxml';
  const loadingNodes=collectNodes(render(file,probe));
  const buttons=loadingNodes.filter(node=>node.tag==='wx-button'&&node.attr?.bindtap==='changePeriod');
  assert.deepEqual(buttons.map(button=>button.attr['data-id']),['1','3'],'compiled iteration and event dataset');
  assert.ok(buttons[1].attr.class.includes('b-on')&&!buttons[0].attr.class.includes('b-on'),'compiled selected period');
  assert.ok(!loadingNodes.some(node=>node.tag==='wx-flow-chart'),'conditional graph hidden before data');
  const readyNodes=collectNodes(render(file,{...probe,ready:true,busy:false,section:'chart',summary:{},quote:{},chart:{points:[]}}));
  assert.ok(readyNodes.some(node=>node.tag==='wx-flow-chart'),'conditional graph visible with data');

  const decoded=req('unescape-js');
  const styleText=runBinary(binary('wcsc'),['-db','-pc',String(styles.length),...styles],root);
  const parts=styleText.split('='),entries=new Map();
  for(let i=0;i+1<parts.length;i+=2)entries.set(parts[i],parts[i+1]);
  for(const file of styles){
    const output=entries.get(file);assert.ok(output,file+' missing wcsc output');
    new vm.Script(decoded(output),{filename:file+'.compiled.js'});
  }
  return {compiler:compiler.name+'@'+compiler.version,wcc:context.window.__wcc_version__,wcsc:entries.get('version'),templateJsBytes:Buffer.byteLength(templateJs),compiledStyleBytes:Buffer.byteLength(styleText),renderedTemplates:templates.length,conditionalAndLoopProbe:true};
}
function compareMarkup(wechat,xhs){
  for(const entry of wechat.perTemplate){
    const counterpart=xhs.perTemplate.find(t=>t.base===entry.base);
    assert.ok(counterpart,entry.base+' missing XHS counterpart');
    for(const field of ['handlers','controls','nativeTags'])assert.deepEqual(counterpart[field],entry[field],entry.base+' '+field+' differ');
  }
  assert.equal(xhs.perTemplate.length,wechat.perTemplate.length);
}
function buildXhsService(source){
  const compilerDist=path.dirname(req.resolve('xhs-mp-compiler-cli'));
  const entry=path.join(compilerDist,'bin/xhs-mp-cli-build.js');
  const tempParent=fs.realpathSync(os.tmpdir());
  const scratch=fs.mkdtempSync(path.join(tempParent,'codex-etf-xhs-build-'));
  try{
    const project=path.join(scratch,'project');
    fs.cpSync(source.root,project,{recursive:true});
    const result=spawnSync(process.execPath,[entry,'-d',project,'-p','xhs','--enableV1','true','--enableV2','false','--enableVDom','false','--compressJs','false'],
      {cwd:scratch,windowsHide:true,encoding:'utf8',timeout:60000,maxBuffer:8*1024*1024});
    if(result.error)throw result.error;
    const messages=(result.stdout||'')+'\n'+(result.stderr||'');
    if(result.status!==0 || /^ERROR\b/im.test(messages) || /Module build failed|Module not found: Error:/i.test(messages))throw Error('XHS offline packer failed: '+messages.slice(-2000));
    const output=path.join(scratch,'dist','v1'),all=listFiles(output);
    const app=json(path.join(source.root,'app.json'));
    const required=['app.json','app.css','service.js','components.js',...app.pages.map(page=>page+'.render.js')];
    const artifacts=[];
    for(const file of all){
      const bytes=fs.readFileSync(path.join(output,file));
      if(file.endsWith('.js'))babel.parse(bytes.toString('utf8'),{sourceType:'unambiguous',sourceFilename:file});
      artifacts.push({file,bytes:bytes.length,sha256:sha(bytes)});
    }
    for(const file of required)assert.ok(artifacts.some(a=>a.file===file && a.bytes>0),'XHS full build missing '+file);
    return {tool:'xhs-mp-compiler-cli/dist/bin/xhs-mp-cli-build.js',packageFormat:'v1',minified:false,artifactCount:artifacts.length,artifacts,
      warnings:messages.split(/\r?\n/).filter(line=>/^WARNING in /.test(line)).map(line=>line.trim()),
      webpackDeprecationNotice:messages.includes('DEP_WEBPACK_COMPILATION_ASSETS')};
  } finally{
    const resolved=fs.realpathSync(scratch);
    assert.equal(path.dirname(resolved),tempParent);
    assert.ok(path.basename(resolved).startsWith('codex-etf-xhs-build-'));
    fs.rmSync(resolved,{recursive:true,force:true});
  }
}

const dist=process.env.ETF_MINI_DIST?path.resolve(process.env.ETF_MINI_DIST):path.join(miniRoot,'dist');
const wx=inventory(path.join(dist,'wechat'),'wechat'),xhs=inventory(path.join(dist,'xiaohongshu'),'xiaohongshu');
compareMarkup(wx,xhs);
const wechatResult=compileWechat(wx);
const {validateXhs}=createRequire(import.meta.url)('./host-xhs-helper.cjs');
const xhsResult=await validateXhs({projectRoot:xhs.root,toolRoot,templates:xhs.templates,styles:xhs.styles});
const fullBuild=buildXhsService(xhs);
const report={verifiedAt:new Date().toISOString(),scope:'Generated packages, WeChat wcc/wcsc, XHS production template/style loaders and full offline v1 Webpack build, event handlers and directive parity.',
  wechat:{...wx.summary,...wechatResult},xiaohongshu:{...xhs.summary,...xhsResult,fullBuild},crossPlatformBindingsAndDirectivesMatch:true,
  remotePreview:false,deviceRun:false,uploaded:false};
const output=JSON.stringify(report,null,2)+'\n';
const flag=process.argv.indexOf('--report');
if(flag>=0){assert.ok(process.argv[flag+1],'--report needs a filename');fs.writeFileSync(path.resolve(process.argv[flag+1]),output);}
process.stdout.write(output);
