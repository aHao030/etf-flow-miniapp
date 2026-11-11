import { readFile, writeFile, mkdir, readdir, copyFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import {registerHooks} from 'node:module';
import {readFileSync} from 'node:fs';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const assets = {};
const types = {'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.mjs':'text/javascript; charset=utf-8'};
for (const name of await readdir(path.join(root,'public'))) {
  if (!types[path.extname(name)]) throw new Error('Unexpected public file: '+name);
  assets['/'+name] = {body:await readFile(path.join(root,'public',name),'utf8'),contentType:types[path.extname(name)]};
}
const styles=['@astryxdesign/core/reset.css','@astryxdesign/core/astryx.css'];
const stylesheets=await Promise.all(styles.map(specifier=>readFile(new URL(import.meta.resolve(specifier)),'utf8')));
// The CLI's generated theme registry imports CSS. Collect its production stylesheet
// into the same-origin asset, and use local system fonts for the private dashboard.
const themeStyle=import.meta.resolve('@astryxdesign/theme-neutral/theme.css');
const remoteFonts=import.meta.resolve('@astryxdesign/theme-neutral/fonts.css');
const loader=registerHooks({load(url,context,nextLoad){
  if(url!==themeStyle && url!==remoteFonts)return nextLoad(url,context);
  if(url===themeStyle)stylesheets.push(readFileSync(new URL(url),'utf8'));
  return {format:'module',source:'',shortCircuit:true};
}});
try {
  const {renderDashboard}=await import('../ui/dashboard.mjs');
  assets['/index.html'].body=renderDashboard(assets['/index.html'].body);
} finally {
  loader.deregister();
}
assets['/astryx.css']={body:stylesheets.join('\n'),contentType:types['.css']};
await mkdir(path.join(root,'dist/server'),{recursive:true});
await mkdir(path.join(root,'dist/.openai'),{recursive:true});
const worker = await readFile(path.join(root,'worker/index.js'),'utf8');
const history = await readFile(path.join(root,'worker/history.js'),'utf8');
const broad = await readFile(path.join(root,'worker/broad.js'),'utf8');
const market = await readFile(path.join(root,'worker/market.js'),'utf8');
const prices = await readFile(path.join(root,'worker/prices.js'),'utf8');
const output = 'const ASSETS = '+JSON.stringify(assets)+';\n'+history+'\n'+broad+'\n'+market+'\n'+prices+'\n'+worker;
await writeFile(path.join(root,'dist/server/index.js'),output);
// Local source checkouts do not include the owner's private hosting binding.
try {
  await copyFile(path.join(root,'.openai/hosting.json'),path.join(root,'dist/.openai/hosting.json'));
} catch (error) {
  if(error.code!=='ENOENT')throw error;
}
const mod = await import('data:text/javascript;base64,'+Buffer.from(output).toString('base64'));
if (typeof mod.default?.fetch !== 'function') throw new Error('Worker entry missing');
console.log('Built and validated Worker with '+Object.keys(assets).length+' local assets.');
