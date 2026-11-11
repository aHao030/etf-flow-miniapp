import test from 'node:test';
import assert from 'node:assert/strict';
import {prepareObservations,prepareTradingObservations,exactAmount,flowAmount,chartDomain,canConnectDates,renderTimeChart} from '../public/charts.mjs';

test('only independent lifetime prices may preserve explicitly verified historical weekend sessions',()=>{
 const rows=[{date:'1991-04-05',value:10},{date:'1991-04-06',value:11}],options={start:'1991-04-05',end:'1991-04-06',tradingDates:rows.map(row=>row.date)};
 assert.equal(prepareTradingObservations(rows,options).length,1,'existing fund chart behavior stays unchanged');
 assert.deepEqual(prepareTradingObservations(rows,{...options,preserveHistoricalSessions:true}).map(row=>row.value),[10,11]);
 assert.equal(prepareTradingObservations(rows,{start:options.start,end:options.end,preserveHistoricalSessions:true}).length,1,'the option never guesses a weekend is open without an explicit calendar');
});
test('exact currency retains the amount and date rather than an aggregate bucket',()=>{
 const result=prepareObservations([{date:'2026-11-09',value:86070800.32},{date:'2026-11-06',value:0}],{start:'2026-11-06',end:'2026-11-09'});
 assert.deepEqual(result.map(x=>[x.date,x.value]),[['2026-11-06',0],['2026-11-09',86070800.32]]);
 assert.equal(exactAmount(result[1].value),'+86,070,800.32 元');
 assert.equal(flowAmount(result[1].value),'+8,607.080032 万元');
 assert.equal(flowAmount(0.01),'+0.000001 万元');assert.equal(flowAmount(-1),'-0.0001 万元');
});
test('missing observations stay missing even in cumulative mode',()=>{
 const rows=[{date:'2026-11-02',value:10},{date:'2026-11-03',value:null},{date:'2026-11-04',value:-4},{date:'2026-02-30',value:900}];
 assert.deepEqual(prepareObservations(rows,{cumulative:true}).map(x=>x.value),[10,null,6]);
 assert.equal(exactAmount(null),'暂无有效记录');
});
test('zero and negative-only charts have finite non-degenerate axes',()=>{
 for(const values of [[0],[-5,-2],[3],[null]]){const domain=chartDomain(values.map(value=>({value})));assert(domain.max>domain.min);assert(domain.min<=0);assert(domain.max>=0);}
});
test('missing weekday gaps break lines while normal weekends may connect',()=>{
 assert.equal(canConnectDates('2026-11-02','2026-11-04'),false);
 assert.equal(canConnectDates('2026-11-06','2026-11-09'),true);
 assert.equal(canConnectDates('2026-11-05','2026-11-09'),false);
 assert.equal(exactAmount(1.12345678,'元',false,8),'1.12345678 元');
});
test('five years preserve every real day without lossy amount binning',()=>{
 const rows=Array.from({length:1300},(_,i)=>({date:new Date(Date.UTC(2021,0,1)+i*86400000).toISOString().slice(0,10),value:i===52?null:i/100}));
 const points=prepareObservations(rows);assert.equal(points.length,1300);assert.equal(points[52].value,null);assert.equal(points[1299].value,12.99);
});
test('trading axis removes closures but preserves a missing trading day and independent index values',()=>{
 const rows=[{date:'2026-11-06',value:10},{date:'2026-11-07',value:999},{date:'2026-11-09',value:888},{date:'2026-11-11',value:-2}];
 const result=prepareTradingObservations(rows,{start:'2026-11-06',end:'2026-11-11',tradingDates:['2026-11-06','2026-11-10','2026-11-11'],cumulative:true,overlay:{rows:[{date:'2026-11-06',value:3000},{date:'2026-11-10',value:3010}]}});
 assert.deepEqual(result.map(p=>[p.date,p.value,p.overlay]),[['2026-11-06',10,3000],['2026-11-10',null,3010],['2026-11-11',8,null]]);
 assert.deepEqual(result.map(p=>p.index),[0,1,2]);
});
test('fallback removes weekends without deleting a missing weekday',()=>{
 const result=prepareTradingObservations([{date:'2026-11-06',value:2},{date:'2026-11-10',value:4}]);
 assert.deepEqual(result.map(p=>[p.date,p.value]),[['2026-11-06',2],['2026-11-09',null],['2026-11-10',4]]);
});
test('index-only early history remains visible with missing ETF amounts and separate point readout',()=>{
 class Node{constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.style={};this.listeners={};this.clientWidth=800;}append(...n){this.children.push(...n);}replaceChildren(...n){this.children=n;}setAttribute(k,v){this.attributes[k]=String(v);}addEventListener(k,v){this.listeners[k]=v;}removeEventListener(){} }
 globalThis.document={createElement:tag=>new Node(tag),createElementNS:(_,tag)=>new Node(tag)};
 const box=new Node('div');
 renderTimeChart(box,[],{start:'1990-12-19',end:'1990-12-20',tradingDates:['1990-12-19','1990-12-20'],overlay:{label:'上证综指',unit:'点',rows:[{date:'1990-12-19',value:99.98},{date:'1990-12-20',value:104.39}]}});
 const frame=box.children[0],readout=frame.children[0],svg=frame.children.find(n=>n.tag==='svg'),slider=frame.children.find(n=>n.tag==='input');
 assert.equal(readout.children[1].textContent,'暂无有效记录');assert.match(readout.children[2].textContent,/104.39 点/);
 assert(svg.children.some(n=>n.attributes['data-series']==='index'));assert.equal(svg.children.filter(n=>n.tag==='rect').length,0);
 slider.value='0';slider.listeners.input();assert.match(slider.attributes['aria-valuetext'],/1990-12-19.*99.98 点/);
});

function installChartDOM(t) {
 class Element {
   constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.style={};this.listeners={};this.clientWidth=390;}
   append(...nodes){this.children.push(...nodes);}
   replaceChildren(...nodes){this.children=nodes;}
   setAttribute(key,value){this.attributes[key]=String(value);}
   addEventListener(key,handler){this.listeners[key]=handler;}
   removeEventListener(){}
 }
 const original=globalThis.document;
 t.after(()=>{globalThis.document=original;});
 globalThis.document={createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag)};
 return new Element('div');
}

test('date inspection distinguishes a closed session, a missing amount, and an observed zero',t=>{
   const box=installChartDOM(t);
   const dates=['2026-11-06','2026-11-09','2026-11-10'];
   renderTimeChart(box,[{date:dates[0],value:0},{date:dates[2],value:-20000}],{tradingDates:dates});
   const frame=box.children[0], amount=frame.children[0].children[1];
   const controls=frame.children.find(node=>node.className==='chart-day-controls');
   const [prev,label,next]=controls.children,picker=label.children[0];
   const status=frame.children.find(node=>node.className==='chart-day-status');
   assert.equal(picker.value,dates[2]);assert.equal(next.disabled,true);
   prev.listeners.click();assert.equal(picker.value,dates[1]);assert.equal(amount.textContent,'暂无有效记录');
   picker.value='2026-11-08';picker.listeners.change();assert.equal(status.hidden,false);
   assert.match(status.textContent,/2026-11-09/);assert.equal(picker.attributes['aria-invalid'],'true');
   assert.equal(amount.textContent,'暂无有效记录');
   picker.value=dates[0];picker.listeners.change();
   assert.equal(prev.disabled,true);assert.equal(status.hidden,true);assert.match(amount.textContent,/^0\.00 /);
   next.listeners.click();assert.equal(picker.value,dates[1]);
});

test('a display-only redraw retains the inspected trading day, including a missing observation',t=>{
 const box=installChartDOM(t),dates=['2026-11-02','2026-11-03','2026-11-04'];
 const rows=[{date:dates[0],value:150000000},{date:dates[2],value:-10000}];
 function render(extra={}) {
   renderTimeChart(box,rows,{instrument:'index:000300',start:dates[0],end:dates[2],tradingDates:dates,...extra});
   const frame=box.children[0],controls=frame.children.find(node=>node.className==='chart-day-controls');
   return {picker:controls.children[1].children[0],amount:frame.children[0].children[1],overlay:frame.children[0].children[2]};
 }
 function select(result,date){result.picker.value=date;result.picker.listeners.change();}
 let view=render({moneyUnit:'yi'});
 select(view,dates[0]);assert.equal(view.amount.textContent,'+1.50 亿元');
 view=render({moneyUnit:'wan'});assert.equal(view.picker.value,dates[0]);assert.equal(view.amount.textContent,'+15,000.00 万元');
 select(view,dates[1]);
 view=render({overlay:{label:'指数',unit:'点',rows:[{date:dates[1],value:3000}]}});
 assert.equal(view.picker.value,dates[1]);assert.equal(view.amount.textContent,'暂无有效记录');
 assert.match(view.overlay.textContent,/3,000 点/);
 assert.equal(render({instrument:'index:000905'}).picker.value,dates[2]);
 select(render(),dates[0]);assert.equal(render({start:dates[1]}).picker.value,dates[2]);
});
