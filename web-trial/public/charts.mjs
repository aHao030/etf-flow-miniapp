// Shared chart module: exact observations, responsive axes and accessible inspection.
import {resolveMoneyUnit} from './logic.mjs';
const DAY = 86400000, NS = 'http://www.w3.org/2000/svg';
const owners = new WeakMap();
const inspectedDates = new WeakMap();
let chartId=0;
const INFLOW='#c63536', OUTFLOW='#167451';
// Weekends are known closures. Any unobserved weekday breaks a line conservatively.
export function canConnectDates(previous, next) {
  const from=timestamp(previous),to=timestamp(next);
  if(!Number.isFinite(from)||!Number.isFinite(to)||to<=from||to-from>3*DAY)return false;
  for(let t=from+DAY;t<to;t+=DAY){const weekday=new Date(t).getUTCDay();if(weekday!==0&&weekday!==6)return false;}
  return true;
}
const timestamp = date => {
  if (typeof date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return NaN;
  const value = Date.parse(date + 'T00:00:00Z');
  return Number.isFinite(value) && new Date(value).toISOString().slice(0, 10) === date ? value : NaN;
};
export function prepareObservations(rows, {start, end, cumulative = false} = {}) {
  let total = 0;
  return (Array.isArray(rows) ? rows : []).filter(r => r && Number.isFinite(timestamp(r.date)) && (!start || r.date >= start) && (!end || r.date <= end))
    .slice().sort((a, b) => a.date.localeCompare(b.date)).map(r => {
      const finite = r.value !== null && r.value !== undefined && r.value !== '' && Number.isFinite(Number(r.value));
      if (finite) total += Number(r.value);
      return {date:r.date, time:timestamp(r.date), value:finite ? (cumulative ? total : Number(r.value)) : null, original:finite ? Number(r.value) : null};
    });
}
export function exactAmount(value, unit = '元', signed = true, decimals = 2) {
  if (value == null || value === '' || !Number.isFinite(Number(value))) return '暂无有效记录';
  const n = Number(value);
  return (signed && n > 0 && unit === '元' ? '+' : '') + new Intl.NumberFormat('zh-CN', {minimumFractionDigits:unit === '元'||unit === '元/股' ? Math.min(2,decimals) : 0, maximumFractionDigits:decimals}).format(n) + ' ' + unit;
}
export function flowAmount(value,signed=true,unit){
  if(value==null||value===''||!Number.isFinite(Number(value)))return '暂无有效记录';
  const scale=resolveMoneyUnit(value,unit),n=Number(value)/scale.divisor;
  return (signed&&n>0?'+':'')+new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:scale.unit==='yi'?10:6}).format(n)+' '+scale.label;
}
export function chartDomain(points, kind = 'bar') {
  const values = points.filter(p => p.value !== null).map(p => p.value);
  if (!values.length) return {min:-1,max:1};
  let min = Math.min(...values), max = Math.max(...values);
  if (kind === 'bar') { min = Math.min(0,min); max = Math.max(0,max); }
  if(min === 0 && max === 0)return {min:-1,max:1};
  const padding = (max - min || Math.abs(max) || 1) * 0.12;
  return {min:min - (min < 0 || kind === 'line' ? padding : 0), max:max + (max > 0 || kind === 'line' ? padding : 0)};
}
function el(tag, text, cls) { const n=document.createElement(tag); if(text!=null)n.textContent=text; if(cls)n.className=cls; return n; }
function svgEl(tag, attrs, text) { const n=document.createElementNS(NS,tag); for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v)); if(text!=null)n.textContent=text; return n; }
export function prepareTradingObservations(rows, options = {}) {
  const base=prepareObservations(rows,{...options,cumulative:false}), overlay=prepareObservations(options.overlay?.rows||[],{...options,cumulative:false});
  const all=[...base,...overlay];
  const start=options.start||all.map(p=>p.date).sort()[0], end=options.end||all.map(p=>p.date).sort().at(-1);
  if(!Number.isFinite(timestamp(start))||!Number.isFinite(timestamp(end))||start>end)return [];
  let dates;
  if(Array.isArray(options.tradingDates)) dates=[...new Set(options.tradingDates)].filter(d=>Number.isFinite(timestamp(d))&&d>=start&&d<=end).sort();
  else {
    dates=[];
    for(let t=timestamp(start);t<=timestamp(end)&&dates.length<30000;t+=DAY){const weekday=new Date(t).getUTCDay();if(weekday!==0&&weekday!==6)dates.push(new Date(t).toISOString().slice(0,10));}
  }
  // A separate lifetime price chart may retain verified historical weekend sessions.
  if(!(options.preserveHistoricalSessions===true&&Array.isArray(options.tradingDates)))dates=dates.filter(d=>{const weekday=new Date(timestamp(d)).getUTCDay();return weekday!==0&&weekday!==6;});
  const values=new Map(base.map(p=>[p.date,p])), overlayValues=new Map(overlay.map(p=>[p.date,p.value]));
  let total=0;
  return dates.map((date,index)=>{const value=values.get(date)?.value??null;if(value!==null)total+=value;return {date,time:timestamp(date),index,original:value,value:value===null?null:options.cumulative?total:value,overlay:overlayValues.get(date)??null};});
}
export function renderTimeChart(container, rows, options = {}) {
  owners.get(container)?.(); container.replaceChildren();
  const {label='资金变化', unit='元', kind='bar', note='', cumulative=false}=options;
  const isFlow=unit==='元'&&options.signed!==false;
  const directional=options.directional??(kind==='bar'||cumulative), clipId='negative-chart-'+(++chartId);
  const points=prepareTradingObservations(rows,options), valid=points.filter(p=>p.value!==null);
  const scope=options.instrument ? JSON.stringify([options.instrument,options.start??points[0]?.date,options.end??points.at(-1)?.date]) : null;
  const previous=inspectedDates.get(container);
  if(previous && previous.scope!==scope)inspectedDates.delete(container);
  const moneyScale=resolveMoneyUnit(valid.reduce((maximum,p)=>Math.max(maximum,Math.abs(p.value)),0),options.moneyUnit);
  const displayAmount=value=>isFlow?flowAmount(value,true,moneyScale.unit):exactAmount(value,unit,options.signed!==false,options.decimals??2);
  const hasOverlay=points.some(p=>p.overlay!==null), overlayDomain=chartDomain(points.map(p=>({value:p.overlay})),'line');
  const overlayUnit=options.overlay?.unit||'点',overlayDecimals=options.overlay?.decimals??2;
  if(['点','元/股','元'].includes(overlayUnit)&&points.every(p=>p.overlay===null||p.overlay>=0))overlayDomain.min=Math.max(0,overlayDomain.min);
  if (!valid.length&&!hasOverlay) { container.append(el('div','所选范围暂无可绘制的有效记录，缺失不代表零。','viz-empty')); return ()=>{}; }
  const frame=el('div',null,'viz-frame'), readout=el('div',null,'viz-readout');
  const dateText=el('span',null,'viz-date'), amountText=el('strong',null,'viz-value'), overlayText=el('strong',null,'viz-overlay-value'), pointNote=el('span',null,'viz-point-note');
  readout.setAttribute('aria-live','polite'); readout.setAttribute('aria-atomic','true'); readout.append(dateText,amountText);
  if(options.overlay)readout.append(overlayText);if(options.pointNotes)readout.append(pointNote);
  const help=el('p',note || '点图或选择交易日查看读数。空白表示暂无记录。','viz-help');
  const svg=svgEl('svg',{class:'viz-svg',role:'img','aria-label':label+'，有日期与金额刻度，可通过下方滑块逐日查看'});
  const slider=el('input',null,'viz-navigator'); slider.type='range'; slider.min='0'; slider.max=String(points.length-1); slider.step='1'; slider.setAttribute('aria-label',label+'：选择日期');
  const controls=el('div',null,'chart-day-controls');
  controls.setAttribute('role','group');controls.setAttribute('aria-label',label+'逐日读数');
  const previousDay=el('button','上一日','chart-step'),nextDay=el('button','下一日','chart-step');
  previousDay.type=nextDay.type='button';
  previousDay.setAttribute('aria-label','上一个交易日');nextDay.setAttribute('aria-label','下一个交易日');
  const dateLabel=el('label','定位交易日','chart-day-label'), datePicker=el('input',null,'chart-day-picker');
  datePicker.type='date';datePicker.min=points[0].date;datePicker.max=points.at(-1).date;
  dateLabel.append(datePicker);
  const dateStatus=el('p',null,'chart-day-status');dateStatus.hidden=true;
  dateStatus.id=clipId+'-date-status';dateStatus.setAttribute('role','status');
  datePicker.setAttribute('aria-describedby',dateStatus.id);
  controls.append(previousDay,dateLabel,nextDay);
  frame.append(readout,svg,controls,dateStatus,slider,help);container.append(frame);
  const savedIndex=scope && previous?.scope===scope ? points.findIndex(p=>p.date===previous.date) : -1;
  let selected=savedIndex>=0 ? savedIndex : points.findLastIndex(p=>p.value!==null||p.overlay!==null), geometry, cross, dot,overlayDot;
  const span=Math.max(points.at(-1).time-points[0].time,DAY), domain=chartDomain(points,kind);
  if(unit==='元/股'&&valid.length&&valid.every(p=>p.value>=0))domain.min=Math.max(0,domain.min);
  function inspect(index) {
    selected=Math.max(0,Math.min(points.length-1,index));const p=points[selected];slider.value=String(selected);
    datePicker.value=p.date;datePicker.setAttribute('aria-invalid','false');
    dateStatus.hidden=true;dateStatus.textContent='';
    previousDay.disabled=selected===0;nextDay.disabled=selected===points.length-1;
    dateText.textContent=p.date+' · '+label;amountText.textContent=displayAmount(p.value);
    amountText.style.color=directional&&p.value!==null?(p.value>0?INFLOW:p.value<0?OUTFLOW:'#1d1d1f'):'#1d1d1f';
    if(options.overlay)overlayText.textContent=options.overlay.label+' '+exactAmount(p.overlay,overlayUnit,false,overlayDecimals);
    pointNote.textContent=options.pointNotes?.[p.date]||'';
    slider.setAttribute('aria-valuetext',p.date+'，'+displayAmount(p.value)+(options.overlay?'，'+overlayText.textContent:''));
    if(!geometry)return;
    const x=geometry.x(selected);cross.setAttribute('x1',x);cross.setAttribute('x2',x);
    dot.setAttribute('cx',x);dot.setAttribute('cy',p.value==null?geometry.bottom:geometry.y(p.value));dot.setAttribute('visibility',p.value==null?'hidden':'visible');dot.setAttribute('fill',directional?(p.value>=0?INFLOW:OUTFLOW):'#1d1d1f');
    if(overlayDot){overlayDot.setAttribute('cx',x);overlayDot.setAttribute('cy',p.overlay==null?geometry.bottom:geometry.oy(p.overlay));overlayDot.setAttribute('visibility',p.overlay==null?'hidden':'visible');}
  }
  function draw() {
    const width=Math.max(320,container.clientWidth || 800), height=290, left=isFlow?Math.max(62,Math.min(108,new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2}).format(Math.max(Math.abs(domain.min),Math.abs(domain.max))/moneyScale.divisor).length*6+16)):62, right=hasOverlay?76:18, top=26, bottom=246;
    const plot=width-left-right,x=i=>left+(points.length===1?0.5:i/(points.length-1))*plot,y=v=>bottom-(v-domain.min)/(domain.max-domain.min)*(bottom-top),oy=v=>bottom-(v-overlayDomain.min)/(overlayDomain.max-overlayDomain.min)*(bottom-top);
    geometry={x,y,oy,bottom,plot,left};svg.replaceChildren();svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
    svg.append(svgEl('title',{},label+'；'+points[0].date+' 至 '+points.at(-1).date));
    const magnitude=Math.max(Math.abs(domain.min),Math.abs(domain.max)),divisor=isFlow?moneyScale.divisor:unit==='元/股'?1:magnitude>=1e8?1e8:magnitude>=1e4?1e4:1;
    const scaleUnit=isFlow?moneyScale.label:(divisor===1e8?'亿':divisor===1e4?'万':'')+unit;
    svg.append(svgEl('text',{x:left,y:14,fill:'#626269','font-size':11},scaleUnit));
    if(hasOverlay)svg.append(svgEl('text',{x:width-right,y:14,fill:'#1d1d1f','font-size':11},options.overlay.unit||'点'));
    for(let i=0;i<=4;i++) {
      const value=domain.min+(domain.max-domain.min)*i/4, yy=y(value);
      svg.append(svgEl('line',{x1:left,x2:width-right,y1:yy,y2:yy,stroke:'#e6e6e8','stroke-dasharray':'3 4'}));
      svg.append(svgEl('text',{x:left-10,y:yy+4,'text-anchor':'end',fill:'#626269','font-size':11},new Intl.NumberFormat('zh-CN',{maximumFractionDigits:isFlow?2:divisor===1&&magnitude<10?3:1}).format(value/divisor)));
      if(hasOverlay){const ov=overlayDomain.min+(overlayDomain.max-overlayDomain.min)*i/4;svg.append(svgEl('text',{x:width-right+8,y:yy+4,'text-anchor':'start',fill:'#1d1d1f','font-size':11},new Intl.NumberFormat('zh-CN',{maximumFractionDigits:options.overlay.axisDecimals??(overlayUnit==='点'?0:2)}).format(ov)));}
    }
    const ticks=Math.min(points.length,width<500?3:5);
    for(let i=0;i<ticks;i++) {
      const position=ticks===1?0:Math.round((points.length-1)*i/(ticks-1)),date=points[position].date;
      const text=span>380*DAY?date.slice(0,7):date.slice(5);
      svg.append(svgEl('text',{x:x(position),y:272,'text-anchor':ticks===1?'middle':i===0?'start':i===ticks-1?'end':'middle',fill:'#626269','font-size':11},text));
    }
    if(domain.min<=0&&domain.max>=0)svg.append(svgEl('line',{x1:left,x2:width-right,y1:y(0),y2:y(0),stroke:'#99999f'}));
    if(kind==='line') {
      let path='', previous=null;
      points.forEach(p=>{if(p.value===null){previous=null;return;}path+=(previous?' L':' M')+x(p.index)+' '+y(p.value);previous=p;});
      svg.append(svgEl('path',{d:path,fill:'none',stroke:directional?INFLOW:hasOverlay?'#737379':'#1d1d1f','stroke-width':2,'stroke-linejoin':'round','stroke-linecap':'round',...(hasOverlay&&!directional?{'stroke-dasharray':'5 3'}:{})}));
      if(directional){const clip=svgEl('clipPath',{id:clipId}),cy=Math.max(top,Math.min(bottom,y(0)));clip.append(svgEl('rect',{x:left,y:cy,width:plot,height:bottom-cy}));svg.append(clip,svgEl('path',{d:path,fill:'none',stroke:OUTFLOW,'stroke-width':2,'clip-path':'url(#'+clipId+')'}));}
      points.forEach((p,i)=>{if(p.value!==null&&(!points[i-1]||points[i-1].value===null)&&(!points[i+1]||points[i+1].value===null))svg.append(svgEl('circle',{cx:x(i),cy:y(p.value),r:2,fill:directional?(p.value>=0?INFLOW:OUTFLOW):'#1d1d1f'}));});
    } else {
      const barWidth=Math.max(0.02,Math.min(22,plot/points.length*0.65));
      points.forEach(p=>{if(p.value===null)return;const yy=y(p.value),zero=y(0),bar=svgEl('rect',{x:x(p.index)-barWidth/2,y:Math.min(yy,zero),width:barWidth,height:Math.max(1,Math.abs(zero-yy)),fill:p.value>0?INFLOW:p.value<0?OUTFLOW:'#737379',rx:Math.min(2,barWidth/3)});svg.append(bar);});
    }
    if(hasOverlay){let path='',previous=false;points.forEach(p=>{if(p.overlay===null){previous=false;return;}path+=(previous?' L':' M')+x(p.index)+' '+oy(p.overlay);previous=true;});svg.append(svgEl('path',{d:path,fill:'none',stroke:'#1d1d1f','stroke-width':1.8,'stroke-linejoin':'round','stroke-linecap':'round','data-series':overlayUnit==='点'?'index':'price'}));points.forEach((p,i)=>{if(p.overlay!==null&&points[i-1]?.overlay==null&&points[i+1]?.overlay==null)svg.append(svgEl('circle',{cx:x(i),cy:oy(p.overlay),r:2,fill:'#1d1d1f'}));});}
    cross=svgEl('line',{x1:0,x2:0,y1:top,y2:bottom,stroke:'#737379','stroke-dasharray':'3 3','pointer-events':'none'});
    dot=svgEl('circle',{r:4,fill:'#1d1d1f',stroke:'#fff','stroke-width':2,'pointer-events':'none'});svg.append(cross,dot);overlayDot=null;if(hasOverlay){overlayDot=svgEl('circle',{r:3,fill:'#1d1d1f',stroke:'#fff','stroke-width':1,'pointer-events':'none'});svg.append(overlayDot);}inspect(selected);
  }
  function pointer(event) {
    const rect=svg.getBoundingClientRect(),px=(event.clientX-rect.left)/rect.width*Math.max(320,container.clientWidth||800);
    choose(Math.round((px-geometry.left)/geometry.plot*(points.length-1)));
  }
  // An explicit reading survives formatting changes and late price overlays.
  // A chart the reader has never inspected continues to follow the latest data.
  function choose(index) {
    inspect(index);
    if(scope)inspectedDates.set(container,{scope,date:points[selected].date});
  }
  previousDay.addEventListener('click',()=>choose(selected-1));
  nextDay.addEventListener('click',()=>choose(selected+1));
  datePicker.addEventListener('change',()=>{
    const index=points.findIndex(point=>point.date===datePicker.value);
    if(index>=0){choose(index);return;}
    datePicker.setAttribute('aria-invalid','true');
    dateStatus.textContent='本图可选 '+points[0].date+' 至 '+points.at(-1).date+' 内的交易日。当前读数仍为 '+points[selected].date+'。';
    dateStatus.hidden=false;
  });
  svg.addEventListener('pointermove',pointer);svg.addEventListener('click',pointer);slider.addEventListener('input',()=>choose(Number(slider.value)));
  draw();let previousWidth=container.clientWidth;
  const observer=typeof ResizeObserver==='undefined'?null:new ResizeObserver(()=>{if(container.clientWidth>0&&Math.abs(container.clientWidth-previousWidth)>2){previousWidth=container.clientWidth;draw();}});
  observer?.observe(container);
  const cleanup=()=>{observer?.disconnect();svg.removeEventListener('pointermove',pointer);svg.removeEventListener('click',pointer);};owners.set(container,cleanup);return cleanup;
}
export function renderContributions(container, items, options = {}) {
  container.replaceChildren();const valid=(items||[]).filter(x=>x.flow!=null&&Number.isFinite(Number(x.flow))).sort((a,b)=>Math.abs(b.flow)-Math.abs(a.flow));
  const shown=valid.slice(0,10);
  if(items?.length && options.onShowAll){
    const summary=el('div',null,'contribution-summary');
    summary.append(el('span','按贡献绝对值排序，展示 '+shown.length+' / '+valid.length+' 只有金额记录的 ETF。','note'));
    const open=el('button','查看全部 '+items.length+' 只 ETF 明细','text-button');open.type='button';
    open.addEventListener('click',options.onShowAll);summary.append(open);container.append(summary);
  }
  if(!valid.length){container.append(el('p','暂无已核验的 ETF 贡献可绘图。','viz-empty'));return;}
  const maximum=Math.max(Math.abs(valid[0].flow),1);
  const scale=resolveMoneyUnit(maximum,options.moneyUnit);
  shown.forEach(item=>{const row=el('div',null,'contribution-row'),label=el('span',(item.etf_name||item.etf_code)+' '+item.etf_code),track=el('div',null,'contribution-track'),bar=el('span',null,'contribution-bar'),amount=el('strong',flowAmount(item.flow,true,scale.unit));bar.style.width=(Math.abs(item.flow)/maximum*100)+'%';bar.style.background=item.flow>=0?INFLOW:OUTFLOW;amount.style.color=item.flow>=0?INFLOW:OUTFLOW;track.append(bar);row.append(label,track,amount);container.append(row);});
}
export function renderCoverage(container, valid, expected) {
  container.replaceChildren();if(!Number.isFinite(valid)||!Number.isFinite(expected)||expected<=0)return;
  const percent=Math.min(100,Math.max(0,valid/expected*100)), meter=el('div',null,'coverage-meter'),fill=el('span',null,'coverage-fill');
  fill.style.width=percent+'%';meter.append(fill);meter.setAttribute('role','meter');meter.setAttribute('aria-label','有效交易日覆盖');meter.setAttribute('aria-valuemin','0');meter.setAttribute('aria-valuemax',String(expected));meter.setAttribute('aria-valuenow',String(Math.min(valid,expected)));
  container.append(el('span',`有效交易日覆盖 ${percent.toFixed(1)}% · ${valid} / ${expected} 日`),meter);
}
