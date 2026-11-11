import test from 'node:test';
import assert from 'node:assert/strict';
import {getMoneyUnit,setMoneyUnit,onMoneyUnitChange,resolveMoneyUnit,money} from '../public/logic.mjs';
import {flowAmount,exactAmount,renderTimeChart,renderContributions} from '../public/charts.mjs';

test('automatic currency switches at one hundred million yuan for both directions',()=>{
 setMoneyUnit('auto');
 assert.equal(money(99999999),'+10,000.00 万元');
 assert.equal(money(100000000),'+1.00 亿元');
 assert.equal(money(-100000000),'-1.00 亿元');
 assert.equal(flowAmount(1439684100),'+14.396841 亿元');
 assert.equal(money(null),'—');assert.equal(flowAmount(''),'暂无有效记录');
 assert.equal(resolveMoneyUnit(-1e8).unit,'yi');
});

test('manual units are remembered, notify once, and keep small nonzero amounts visible',async()=>{
 const saved=new Map();globalThis.localStorage={getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v)};
 const changes=[],stop=onMoneyUnitChange(unit=>changes.push(unit));
 try{
  setMoneyUnit('wan');setMoneyUnit('wan');assert.equal(money(1e8),'+10,000.00 万元');
  setMoneyUnit('yi');assert.equal(money(1),'+0.00000001 亿元');assert.equal(flowAmount(.01),'+0.0000000001 亿元');
  assert.equal(exactAmount(2.5,'元/股',false), '2.50 元/股');assert.equal(exactAmount(123,'份',false),'123 份');
  assert.equal(saved.get('etf-flow-money-unit-v1'),'yi');
  const reloaded=await import('../public/logic.mjs?saved-unit-test');assert.equal(reloaded.getMoneyUnit(),'yi');
  assert.throws(()=>setMoneyUnit('invalid'),TypeError);assert.equal(getMoneyUnit(),'yi');
  assert.deepEqual(changes,['wan','yi']);stop();setMoneyUnit('auto');assert.deepEqual(changes,['wan','yi']);
 }finally{stop();setMoneyUnit('auto');delete globalThis.localStorage;}
});

class Node{
 constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.style={};this.listeners={};this.clientWidth=800;}
 append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}
 setAttribute(k,v){this.attributes[k]=String(v);}addEventListener(k,v){this.listeners[k]=v;}removeEventListener(){}
}
function chartNodes(box){const frame=box.children[0];return {frame,readout:frame.children[0],svg:frame.children.find(n=>n.tag==='svg'),slider:frame.children.find(n=>n.tag==='input')};}
function useDom(){globalThis.document={createElement:tag=>new Node(tag),createElementNS:(_,tag)=>new Node(tag)};}

test('one chart keeps a single automatic money scale while price stays yuan per share and gaps stay open',()=>{
 setMoneyUnit('auto');useDom();const box=new Node('div');
 const rows=[{date:'2026-11-02',value:150000000},{date:'2026-11-03',value:10000},{date:'2026-11-04',value:-1000}];
 const dates=rows.map(r=>r.date);
 renderTimeChart(box,rows,{tradingDates:dates,overlay:{label:'收盘价（不复权）',unit:'元/股',rows:[{date:dates[0],value:12.34},{date:dates[2],value:12.56}]}});
 const {readout,svg,slider}=chartNodes(box);
 assert.equal(readout.children[1].textContent,'-0.00001 亿元');assert.match(readout.children[2].textContent,/12.56 元\/股/);
 assert(svg.children.some(n=>n.tag==='text'&&n.textContent==='亿元'));
 assert(svg.children.some(n=>n.tag==='text'&&n.textContent==='元/股'));
 const pricePath=svg.children.find(n=>n.attributes['data-series']==='price');
 assert.equal((pricePath.attributes.d.match(/ M/g)||[]).length,2);assert(!pricePath.attributes.d.includes(' L'),'missing price must break, not interpolate, the line');
 slider.value='1';slider.listeners.input();assert.equal(readout.children[1].textContent,'+0.0001 亿元');
 assert.match(readout.children[2].textContent,/暂无有效记录/);assert.match(slider.attributes['aria-valuetext'],/2026-11-03/);
 const contributions=new Node('div');renderContributions(contributions,[{etf_code:'510300',flow:10000}],{moneyUnit:'yi'});
 assert.equal(contributions.children[0].children[2].textContent,'+0.0001 亿元');
});

test('automatic chart threshold uses observations rather than axis padding; manual units preserve geometry and price values',()=>{
 setMoneyUnit('auto');useDom();const box=new Node('div'),rows=[{date:'2026-11-09',value:95000000}];
 const options={tradingDates:['2026-11-09'],overlay:{label:'收盘价',unit:'元/股',rows:[{date:'2026-11-09',value:.01}]}};
 renderTimeChart(box,rows,options);const before=chartNodes(box),bar=before.svg.children.find(n=>n.tag==='rect');
 assert(before.svg.children.some(n=>n.tag==='text'&&n.textContent==='万元'));
 setMoneyUnit('yi');renderTimeChart(box,rows,options);const after=chartNodes(box),nextBar=after.svg.children.find(n=>n.tag==='rect');
 assert.equal(after.readout.children[1].textContent,'+0.95 亿元');assert.equal(before.readout.children[2].textContent,after.readout.children[2].textContent);
 assert.equal(bar.attributes.height,nextBar.attributes.height);assert.equal(bar.attributes.y,nextBar.attributes.y);
 const rightAxis=after.svg.children.filter(n=>n.tag==='text'&&n.attributes['text-anchor']==='start'&&Number(n.attributes.y)>14&&Number(n.attributes.y)<250);
 assert(rightAxis.every(n=>Number(n.textContent.replaceAll(',',''))>=0),'stock price axis must not imply a negative price');
 setMoneyUnit('auto');
});
