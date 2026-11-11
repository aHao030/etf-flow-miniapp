import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {pathToFileURL,fileURLToPath} from 'node:url';

// Run the same read-only API against the immutable versions used for this package.
export async function referenceApi(meta){
  const web=fileURLToPath(new URL('../../web-trial/',import.meta.url));
  const worker=(await import(pathToFileURL(path.join(web,'dist/server/index.js')))).default;
  const archives=new Map(), cache=new Map();
  for(const name of ['market','history','broad']){
    const version=meta[name].version;
    if(!/^\d{14}-[\da-f]{32}$/.test(version))throw Error('Invalid source version');
    const dir=path.join(web,'.local',name+'-snapshots',version);
    const manifest=JSON.parse(fs.readFileSync(path.join(dir,'manifest.json'),'utf8'));
    if(manifest.version!==version)throw Error('Snapshot identity mismatch');
    archives.set(name,{dir,manifest});
  }
  const env={BUCKET:{async get(key){
    const latest=/^(market|history|broad)-latest\.json$/.exec(key);
    if(latest)return {text:async()=>JSON.stringify(archives.get(latest[1]).manifest)};
    const parts=/^(market|history|broad)\/([\w-]+)\/([\w-]+)\.json$/.exec(key);
    if(!parts)return null;
    const {dir,manifest}=archives.get(parts[1]),file=parts[3];
    if(parts[2]!==manifest.version||!manifest.files.includes(file))return null;
    if(!cache.has(key)){
      const data=fs.readFileSync(path.join(dir,file+'.json'));
      if(createHash('sha256').update(data).digest('hex')!==manifest.hashes[file])throw Error('Invalid source digest');
      cache.set(key,data.toString('utf8'));
    }
    return {text:async()=>cache.get(key)};
  }}};
  return (url)=>worker.fetch(new Request(url),env);
}
