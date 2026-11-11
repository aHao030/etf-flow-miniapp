// Run locally behind a platform-approved HTTPS reverse proxy. No connection secrets on disk.
import http from 'node:http';
import {randomBytes,randomInt,timingSafeEqual} from 'node:crypto';
import {pathToFileURL} from 'node:url';
const paths=/^\/api\/v1\/(?:broad\/(?:meta|calendar|(?:off|index)\/(?:all|\d{6}))|market\/(?:meta|stocks|stock\/\d{6})|prices\/stock\/\d{6}|history\/(?:meta|etf\/\d{6}(?:\/chart)?))$/;
function send(res,status,obj){res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(JSON.stringify(obj));}
export function createOwnerGateway(fetchLocal, clock=Date.now){
  let ticket=null, deadline=0, failures=[],sessions=new Map();
  function issue(){ticket=String(randomInt(100000000)).padStart(8,'0');deadline=clock()+5*60000;return ticket;}
  function revoke(){ticket=null;sessions.clear();}
  const server=http.createServer(async(req,res)=>{
    try{
      const url=new URL(req.url,'http://localhost');
      if(url.pathname==='/api/v1/pair'&&req.method==='POST'){
        failures=failures.filter(t=>clock()-t<60000);
        if(failures.length>=5)return send(res,429,{message:'稍后再试'});
        let size=0, data='';
        for await(const chunk of req){size+=chunk.length;if(size>256)return send(res,413,{message:'请求过大'});data+=chunk.toString('utf8');}
        let code;try{code=JSON.parse(data).code;}catch{}
        if(!ticket||clock()>=deadline||typeof code!=='string'||!/^\d{8}$/.test(code)||!timingSafeEqual(Buffer.from(code),Buffer.from(ticket))){
          failures.push(clock());return send(res,401,{message:'连接码无效'});
        }
        ticket=null;const session=randomBytes(32).toString('base64url');
        for(const [key,expiry] of sessions)if(expiry<=clock())sessions.delete(key);
        sessions.set(session,clock()+8*3600000);
        return send(res,200,{session});
      }
      if(req.method!=='GET'||!paths.test(url.pathname))return send(res,404,{message:'路径无效'});
      const token=(req.headers.authorization||'').replace(/^Bearer /,'');
      const expires=sessions.get(token);if(!expires||expires<=clock()){sessions.delete(token);return send(res,401,{message:'请重新连接'});}
      const upstream=await fetchLocal(url.pathname+url.search);
      if(!String(upstream.headers.get('content-type')||'').includes('application/json'))return send(res,502,{message:'本机接口响应异常'});
      const chunks=[];let size=0;
      for await(const chunk of upstream.body){size+=chunk.length;if(size>16*1024*1024)throw Error('size');chunks.push(chunk);}
      res.writeHead(upstream.status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(Buffer.concat(chunks));
    }catch{if(!res.headersSent)send(res,502,{message:'本机数据暂不可用'});else res.end();}
  });
  server.requestTimeout=30000;
  return {server,issue,revoke};
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  if(!process.stdin.isTTY||!process.stdout.isTTY)throw Error('请在本机交互式终端启动；连接码不可写入日志。');
  const base='http://127.0.0.1:4173';
  const status=await (await fetch(base+'/local/status',{signal:AbortSignal.timeout(4000),redirect:'error'})).json();
  if(status.app_id!=='etf-flow-local')throw Error('请先打开桌面的 ETF 资金观察');
  const gate=createOwnerGateway(p=>fetch(base+p,{signal:AbortSignal.timeout(30000),redirect:'error'}));
  gate.server.listen(4174,'127.0.0.1',()=>{console.log('本机只读入口 127.0.0.1:4174。回车生成一次性连接码，输入 revoke 撤销设备连接。');});
  process.stdin.setEncoding('utf8');process.stdin.on('data',s=>{if(s.trim()==='revoke'){gate.revoke();console.log('连接已撤销。');}else console.log('5 分钟有效，单次使用：'+gate.issue());});
}
