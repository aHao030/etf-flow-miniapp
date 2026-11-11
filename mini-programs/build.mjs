import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash,randomUUID} from 'node:crypto';
import {Script} from 'node:vm';
import {readSelectedSnapshot} from './scripts/snapshot-store.mjs';
import {resolveNativeTheme} from './scripts/native-theme.mjs';

const ROOT=path.dirname(fileURLToPath(import.meta.url));
const PLATFORMS=['wechat','xiaohongshu'];
const PAGES=['broad','stock','fund','about'].map(name=>'pages/'+name+'/index');
const COMPONENTS=['flow-chart','source-note'].map(name=>'components/'+name+'/index');
const LIBS=['data','offline','format','fund-list','chart','platform','updates','vendor/tiny-inflate',
  ...['meta','indices','funds','calendar','off','s300750','s600519'].map(name=>'snapshot/'+name)];
const ICONS=['broad','stock','about'].flatMap(name=>['assets/'+name+'.png','assets/'+name+'-active.png']);
const SRC=['app.js','app.json','app.wxss',...ICONS,...LIBS.map(n=>'lib/'+n+'.js'),
  ...[...PAGES,...COMPONENTS].flatMap(stem=>['js','json','wxml','wxss'].map(ext=>stem+'.'+ext))];
const LICENSES=['lib/vendor/LICENSE','lib/vendor/LICENSE.txt','lib/vendor/tiny-inflate.LICENSE','lib/vendor/tiny-inflate.LICENSE.txt'];
const hash=b=>createHash('sha256').update(b).digest('hex');
const json=x=>Buffer.from(JSON.stringify(x,null,2)+'\n');
const text=b=>b.toString('utf8').replace(/^\uFEFF/,'');
const inside=(base,full)=>{const rel=path.relative(base,full);return rel!==''&&!rel.startsWith('..'+path.sep)&&rel!=='..'&&!path.isAbsolute(rel);};

export function options(argv=[]) {
  const parsed={}, allowed=new Set(['--mode','--base-url','--wechat-appid','--xhs-appid']);
  for(let i=0;i<argv.length;i+=2){const key=argv[i], value=argv[i+1];
    if(!allowed.has(key)||Object.hasOwn(parsed,key)||!value||value.startsWith('--'))throw Error('构建参数无效，参见 --help');
    parsed[key]=value;
  }
  const mode=parsed['--mode']||'snapshot';if(!['snapshot','local','live'].includes(mode))throw Error('mode 仅支持 snapshot、local、live');
  let baseUrl=mode==='local'?'http://127.0.0.1:4173/api/v1':'';
  if(mode==='live'){
    let url;try{url=new URL(parsed['--base-url']);}catch{throw Error('live 需要 HTTPS API 根地址');}
    if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash||!/^\/api\/v1\/?$/.test(url.pathname))
      throw Error('live 地址须为 https://域名/api/v1，不能包含认证、查询参数或片段');
    if(url.hostname==='etf-stock-flow-observer-1031.sweetowlet132.chatgpt.site')throw Error('请使用为小程序配置的正式 API 域名；当前私有 Sites 地址需要站点所有者登录');
    baseUrl=url.origin+'/api/v1';
  }else if(parsed['--base-url'])throw Error('--base-url 仅适用于 live 模式');
  const wechatId=parsed['--wechat-appid'],xhsId=parsed['--xhs-appid'];
  if(wechatId&&wechatId!=='touristappid'&&!/^wx[0-9a-f]{16}$/i.test(wechatId))throw Error('微信 AppID 格式无效');
  if(xhsId&&!/^[A-Za-z0-9_-]{1,80}$/.test(xhsId))throw Error('小红书 AppID 格式无效');
  return {mode,baseUrl,wechatId,xhsId};
}

// Check every existing ancestor too, including Windows directory junctions.
function safePath(root,relative){
  const full=path.resolve(root,relative);if(!inside(root,full))throw Error('工程路径越界');
  let current=root;
  for(const part of path.relative(root,full).split(path.sep)){
    current=path.join(current,part);
    try{if(fs.lstatSync(current).isSymbolicLink())throw Error('工程目录含链接或联接点，已停止写入');}catch(e){if(e.code!=='ENOENT')throw e;}
  }
  return full;
}
function atomicWrite(root,relative,bytes){
  const dest=safePath(root,relative),tmp=dest+'.'+randomUUID()+'.tmp';fs.mkdirSync(path.dirname(dest),{recursive:true});
  fs.writeFileSync(tmp,bytes,{flag:'wx'});
  try{fs.renameSync(tmp,dest);}finally{if(fs.existsSync(tmp))fs.unlinkSync(tmp);}
}
function readSource(root,mode){
  const offline=mode==='snapshot',selected=offline?readSelectedSnapshot(root):null;
  const names=offline?[...SRC,...LICENSES.filter(name=>fs.existsSync(safePath(root,'src/'+name)))]:
    SRC.filter(name=>!name.startsWith('lib/snapshot/')&&!name.startsWith('lib/vendor/'));
  if(offline&&!names.some(name=>LICENSES.includes(name)))throw Error('缺少 tiny-inflate 的 LICENSE');
  const files=new Map();
  for(const name of names){
    if(!offline&&name==='lib/offline.js'){
      files.set(name,Buffer.from("module.exports={meta:{},query(){throw Error('请连接数据服务');}};\n"));continue;
    }
    const file=selected&&name.startsWith('lib/snapshot/')?path.join(selected.dir,path.basename(name)):safePath(root,'src/'+name);
    if(!fs.existsSync(file)||!fs.statSync(file).isFile())throw Error('缺少小程序源文件 '+name);
    const bytes=fs.readFileSync(file);
    const themed=name==='app.json'||name.endsWith('.wxss')||name.endsWith('.wxml');
    files.set(name,themed?Buffer.from(resolveNativeTheme(text(bytes))):bytes);
  }
  return files;
}
function projectConfig(platform,opts,previous){
  const specified=platform==='wechat'?opts.wechatId:opts.xhsId;
  const appid=specified??previous?.appid??(platform==='wechat'?'touristappid':'');
  return {...previous,appid,projectname:previous?.projectname||'etf-flow-observer',compileType:'miniprogram',miniprogramRoot:'./',
    setting:{es6:true,minified:false,...previous?.setting,urlCheck:opts.mode!=='local'}};
}
function convert(src,platform,opts,previousConfig,appVersion){
  const result=new Map(),xhs=platform==='xiaohongshu';
  for(const [name,buffer] of src){let out=name,value=buffer;
    if(xhs&&name.endsWith('.wxml')){out=name.slice(0,-5)+'.xhsml';value=Buffer.from(text(buffer).replace(/\bwx:(?=[\w-]+)/g,'xhs:'));}
    if(xhs&&name.endsWith('.wxss'))out=name.slice(0,-5)+'.css';
    if(name==='lib/platform.js'){
      const original=text(buffer);if(!original.includes('const host = wx;')||!original.includes("const platformName = 'wechat';"))throw Error('平台桥声明与构建契约不一致');
      if(xhs)value=Buffer.from(original.replace('const host = wx;','const host = xhs;').replace("const platformName = 'wechat';","const platformName = 'xiaohongshu';"));
    }
    result.set(out,value);
  }
  result.set('project.config.json',json(projectConfig(platform,opts,previousConfig)));
  const input=createHash('sha256').update(JSON.stringify([platform,appVersion,opts.mode,opts.baseUrl]));
  for(const name of [...result.keys()].sort())input.update(name+'\0'+hash(result.get(name))+'\n');
  const buildId=input.digest('hex').slice(0,12);
  result.set('lib/config.js',Buffer.from("'use strict';\nmodule.exports = "+JSON.stringify({mode:opts.mode,baseUrl:opts.baseUrl,appVersion,buildId})+';\n'));
  return result;
}

function resolveModule(current,reference){
  if(reference.startsWith('/'))return reference.slice(1);
  return path.posix.normalize(path.posix.join(path.posix.dirname(current),reference));
}
export function validate(files,platform){
  const xhs=platform==='xiaohongshu',markup=xhs?'.xhsml':'.wxml',style=xhs?'.css':'.wxss';
  function content(name){if(!files.has(name))throw Error('工程缺少依赖 '+name);return text(files.get(name));}
  const app=JSON.parse(content('app.json'));
  if(app.pages.length!==PAGES.length||new Set(app.pages).size!==PAGES.length||app.pages.some(p=>!PAGES.includes(p)))throw Error('页面登记不完整');
  for(const item of app.tabBar.list){if(!app.pages.includes(item.pagePath))throw Error('tabBar 页面不存在');
    for(const icon of [item.iconPath,item.selectedIconPath]){const b=files.get(icon);if(!b||b.subarray(0,8).toString('hex')!=='89504e470d0a1a0a')throw Error('tab 图标缺失或无效 '+icon);}
  }
  const checkComponents=(cfg,current)=>{for(const target of Object.values(cfg.usingComponents||{})){
    const resolved=resolveModule(current,target);if(JSON.parse(content(resolved+'.json')).component!==true)throw Error('组件未声明 '+resolved);
  }};
  checkComponents(app,'app.json');content('app'+style);content('app.js');
  if(!content('lib/platform.js').includes('const host = '+(xhs?'xhs':'wx')+';'))throw Error('平台 API 桥错误 '+platform);
  for(const base of [...PAGES,...COMPONENTS]){
    const source=content(base+'.js'),template=content(base+markup);content(base+style);checkComponents(JSON.parse(content(base+'.json')),base+'.json');
    if((xhs?/\bwx:[\w-]+/:/\bxhs:[\w-]+/).test(template))throw Error('模板指令平台错误 '+base);
    for(const m of template.matchAll(/\b(?:bind|catch):?[\w-]+\s*=\s*["']([A-Za-z_$][\w$]*)["']/g)){
      if(!new RegExp('\\b'+m[1]+'\\s*(?:\\(|:)').test(source))throw Error('模板事件未实现 '+base+' '+m[1]);
    }
  }
  let bytes=0;
  for(const [name,buffer] of files){bytes+=buffer.length;
    if(name.endsWith('.json'))JSON.parse(text(buffer));
    if(name.endsWith('.js')){const code=text(buffer);new Script(code,{filename:name});
      if((xhs?/\bwx\s*\./:/\bxhs\s*\./).test(code))throw Error('存在另一宿主的直接 API 调用 '+name);
      for(const m of code.matchAll(/\brequire\(\s*['"]([^'"]+)['"]\s*\)/g)){
        if(!m[1].startsWith('.'))throw Error('原生包不可使用外部模块 '+name);
        const target=resolveModule(name,m[1]);if(!files.has(target)&&!files.has(target+'.js')&&!files.has(target+'.json'))throw Error('模块无法解析 '+name+' → '+target);
      }
    }
  }
  if(bytes>2*1024*1024)throw Error('主包超过本项目的 2 MiB 目标 '+platform+' '+bytes+' bytes');
  return {platform,files:files.size,bytes,pages:app.pages.length,components:COMPONENTS.length};
}
function loadReceipt(root,platform){
  const r=safePath(root,'dist/.generated-'+platform+'.json');if(!fs.existsSync(r))return {};
  const data=JSON.parse(fs.readFileSync(r,'utf8'));if(data.schema!==1||!data.files||typeof data.files!=='object')throw Error('构建记录无法读取');return data.files;
}
export function build(sourceRoot,opts=options(),destination=sourceRoot,knownFiles=null){
  sourceRoot=fs.realpathSync(sourceRoot);
  const root=fs.realpathSync(destination),src=readSource(sourceRoot,opts.mode),plans=[];
  const {appVersion}=JSON.parse(fs.readFileSync(safePath(sourceRoot,'version.json'),'utf8'));
  if(!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(appVersion))throw Error('version.json 需要有效的程序版本号');
  // Preflight both trees before changing either one. No directory clean or recursive removal.
  for(const platform of PLATFORMS){
    const previous=loadReceipt(root,platform),prefix='dist/'+platform+'/',config=safePath(root,prefix+'project.config.json');
    const configExists=fs.existsSync(config),previousConfig=configExists&&previous['project.config.json']?JSON.parse(fs.readFileSync(config,'utf8')):null;
    const files=convert(src,platform,opts,previousConfig,appVersion),summary=validate(files,platform),changed=[];
    for(const [name,bytes] of files){const dest=safePath(root,prefix+name);
      if(fs.existsSync(dest)){const existingHash=hash(fs.readFileSync(dest));if(existingHash===hash(bytes))continue;
        if(!(name==='project.config.json'&&previousConfig)&&previous[name]!==existingHash)throw Error('保留本地已修改文件，未覆盖 '+prefix+name);
      }
      changed.push(name);
    }
    const obsolete=[];
    for(const name of Object.keys(previous).filter(n=>!files.has(n))){
      if(!/^(?:lib\/snapshot\/[a-z0-9-]+\.js|lib\/vendor\/[\w.-]+)$/.test(name))throw Error('请检查不再生成的文件 '+name);
      const file=safePath(root,prefix+name);
      if(fs.existsSync(file)){
        if(hash(fs.readFileSync(file))!==previous[name])throw Error('保留已修改文件 '+name);
        obsolete.push(file);
      }
    }
    plans.push({platform,prefix,files,summary,changed,obsolete});
  }
  if(knownFiles&&plans.every(({platform,files})=>{
    const expected=knownFiles[platform]||{};
    return Object.keys(expected).length===files.size&&[...files].every(([name,bytes])=>expected[name]===hash(bytes));
  }))return plans.map(p=>({...p.summary,mode:opts.mode,reused:true}));
  for(const plan of plans){
    for(const name of plan.changed)atomicWrite(root,plan.prefix+name,plan.files.get(name));
    for(const file of plan.obsolete)fs.unlinkSync(file);
    const hashes={};for(const [name,wanted] of plan.files){const actual=fs.readFileSync(safePath(root,plan.prefix+name));
      if(hash(actual)!==hash(wanted))throw Error('生成文件校验失败 '+name);hashes[name]=hash(actual);
    }
    atomicWrite(root,'dist/.generated-'+plan.platform+'.json',json({schema:1,files:hashes}));
  }
  return plans.map(p=>({...p.summary,mode:opts.mode,changed:p.changed.length,folder:path.join(root,p.prefix)}));
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  try{
    if(process.argv.slice(2).includes('--help'))console.log('node build.mjs [--mode snapshot|local|live] [--base-url https://example.com/api/v1] [--wechat-appid wx…] [--xhs-appid …]');
    else console.log(JSON.stringify(build(ROOT,options(process.argv.slice(2))),null,2));
  }catch(error){console.error(error.message);process.exitCode=1;}
}
