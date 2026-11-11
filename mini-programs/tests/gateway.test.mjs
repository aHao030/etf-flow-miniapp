import test from 'node:test';
import assert from 'node:assert/strict';
import {once} from 'node:events';
import {createOwnerGateway} from '../tools/owner-gateway.mjs';
test('owner pairing is one-time; only authenticated read routes pass; expiry and revoke work',async()=>{
 let calls=[],now=0;
 const gate=createOwnerGateway(async route=>{calls.push(route);return Response.json({code:0,data:{}});},()=>now);
 gate.server.listen(0,'127.0.0.1');await once(gate.server,'listening');
 const base='http://127.0.0.1:'+gate.server.address().port;
 const pair=code=>fetch(base+'/api/v1/pair',{method:'POST',body:JSON.stringify({code})});
 try{
  const resource='/api/v1/broad/meta',code=gate.issue();
  assert.equal((await fetch(base+resource)).status,401);
  const issued=await pair(code);assert.equal(issued.status,200);const {session}=await issued.json();
  assert.equal((await pair(code)).status,401);
  const headers={Authorization:'Bearer '+session};
  assert.equal((await fetch(base+resource,{headers})).status,200);
  assert.equal((await fetch(base+'/internal/history/commit',{method:'POST',headers})).status,404);
  assert.deepEqual(calls,[resource]);
  now+=8*3600000+1;assert.equal((await fetch(base+resource,{headers})).status,401);
  const expired=gate.issue();now+=5*60000;assert.equal((await pair(expired)).status,401);
  const next=await (await pair(gate.issue())).json();gate.revoke();
  assert.equal((await fetch(base+resource,{headers:{Authorization:'Bearer '+next.session}})).status,401);
 }finally{gate.server.close();gate.server.closeAllConnections?.();}
});
