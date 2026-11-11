import http from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const source=await readFile(path.join(root,'dist/server/index.js'),'utf8');
const worker=(await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'))).default;
const snapshot=process.argv[2];
const manifest=snapshot?JSON.parse(await readFile(path.join(snapshot,'manifest.json'),'utf8')):null;
const meta=snapshot?JSON.parse(await readFile(path.join(snapshot,'meta.json'),'utf8')):null;
const historyDir=process.argv[3];
const historyManifest=historyDir?JSON.parse(await readFile(path.join(historyDir,'manifest.json'),'utf8')):null;
const bucket={async get(key){
  let body;
  if(key==='history-latest.json') {if(!historyManifest)return null;body=JSON.stringify(historyManifest);}
  else if(key.startsWith('history/')&&historyManifest){
    const prefix='history/'+historyManifest.version+'/';if(!key.startsWith(prefix))return null;
    const name=key.slice(prefix.length);if(!historyManifest.files.includes(name.replace(/\.json$/,'')))return null;
    body=await readFile(path.join(historyDir,name),'utf8');
  }
  else if(!snapshot)return null;
  else if(key==='latest.json')body=JSON.stringify({...manifest,generated_at:meta.generated_at});
  else {const prefix='snapshots/'+manifest.version+'/'; if(!key.startsWith(prefix))return null;
    const name=key.slice(prefix.length); if(!manifest.files.includes(name.replace(/\.json$/,'')))return null;
    body=await readFile(path.join(snapshot,name),'utf8');}
  return {text:async()=>body};
}};
const server=http.createServer(async(req,res)=>{
  try{const result=await worker.fetch(new Request('http://127.0.0.1:4173'+req.url,{method:req.method,headers:req.headers}),{BUCKET:bucket});
    res.writeHead(result.status,Object.fromEntries(result.headers));res.end(Buffer.from(await result.arrayBuffer()));
  }catch{res.writeHead(500);res.end('Preview unavailable');}
});
const port=snapshot?4173:4175;
server.listen(port,'127.0.0.1',()=>console.log('Local: http://127.0.0.1:'+port+(snapshot?'':' (UI preview without market snapshots)')));
