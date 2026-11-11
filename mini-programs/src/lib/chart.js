'use strict';
const {money,number} = require('./format');
const WIDTH=580, HEIGHT=184, MARGIN=8, MAX_GROUPS=52;
const valid=n=>typeof n==='number' && Number.isFinite(n);
function domain(values, includeZero) {
  if (!values.length) return [0,1];
  let low=Math.min.apply(null,values), high=Math.max.apply(null,values);
  if(includeZero){low=Math.min(0,low);high=Math.max(0,high);}
  if(low===high){const pad=Math.abs(low)*.03||1;low-=pad;high+=pad;}
  return [low,high];
}
function yFor(value,d){return MARGIN+(d[1]-value)/(d[1]-d[0])*(HEIGHT-2*MARGIN);}
function segment(x1,y1,x2,y2){
  const dx=x2-x1,dy=y2-y1;
  return 'left:'+x1+'rpx;top:'+y1+'rpx;width:'+Math.hypot(dx,dy)+'rpx;transform:rotate('+Math.atan2(dy,dx)+'rad)';
}
function chartModel(rows, opts = {}, registerDayReader) {
  const input=Array.isArray(rows)?rows:[], bar=opts.kind!=='line', size=Math.max(1,Math.ceil(input.length/MAX_GROUPS));
  const groups=[];
  for(let i=0;i<input.length;i+=size){
    const part=input.slice(i,i+size), values=part.filter(r=>valid(r.value)), overlays=part.filter(r=>valid(r.overlay));
    const last=values[values.length-1], over=overlays[overlays.length-1];
    groups.push({key:part[0].date,start:part[0].date,end:part[part.length-1].date,days:part.length,
      value:last?(bar?values.reduce((s,r)=>s+r.value,0):last.value):null, valueDate:last?last.date:'',
      overlay:over?over.overlay:null, overlayDate:over?over.date:'', count:values.length,overCount:overlays.length,
      starts:valid(part[0].value),ends:valid(part[part.length-1].value),overStarts:valid(part[0].overlay),overEnds:valid(part[part.length-1].overlay)});
  }
  const ys=groups.map(g=>g.value).filter(valid), os=groups.map(g=>g.overlay).filter(valid);
  const mainRange=domain(ys,bar), overRange=domain(os,false),zero=yFor(0,mainRange);
  const largest=ys.reduce((n,v)=>Math.max(n,Math.abs(v)),0);
  const unit=opts.moneyUnit==='wan'||opts.moneyUnit==='yi'?opts.moneyUnit:(largest>=1e8?'yi':'wan');
  const valueText=v=>!valid(v)?'暂无记录':opts.currency?money(v,unit):number(v,opts.unit==='元'?4:2)+' '+(opts.unit||'');
  const overlayText=v=>valid(v)?number(v,2)+' '+(opts.overlayUnit||'点'):'暂无记录';
  const valueTone=v=>bar&&opts.currency?(v>0?'in':v<0?'out':'neutral'):'neutral';
  // Keep raw years of daily observations on the page; send only the requested day across the native bridge.
  if(registerDayReader)registerDayReader(date=>{
    if(!input.length||typeof date!=='string'||date<input[0].date||date>input[input.length-1].date)return null;
    let lo=0,hi=input.length;
    while(lo<hi){const mid=Math.floor((lo+hi)/2);if(input[mid].date<date)lo=mid+1;else hi=mid;}
    const row=input[lo]?.date===date?input[lo]:null;
    const group=row?Math.floor(lo/size):-1;
    return {date,listed:!!row,group,x:row?x(group):null,prev:input[lo-1]?.date||'',
      next:input[row?lo+1:lo]?.date||'',amount:valueText(row?.value),tone:valueTone(row?.value),overlayText:overlayText(row?.overlay)};
  });
  const x=i=>MARGIN+(i+.5)*(WIDTH-2*MARGIN)/Math.max(groups.length,1);
  const segments=[], points=[];
  groups.forEach((g,i)=>{
    const cx=x(i), cy=valid(g.value)?yFor(g.value,mainRange):0, oy=valid(g.overlay)?yFor(g.overlay,overRange):0;
    const prev=groups[i-1],col=(WIDTH-2*MARGIN)/groups.length;
    const addLine=(from,to,kind)=>segments.push({key:kind+i,kind,style:segment(x(i-1),from,cx,to)});
    if(!bar&&valid(g.value)&&prev&&valid(prev.value)&&prev.ends&&g.starts&&prev.count===prev.days&&g.count===g.days)addLine(yFor(prev.value,mainRange),cy,'primary');
    if(valid(g.overlay)&&prev&&valid(prev.overlay)&&prev.overEnds&&g.overStarts&&prev.overCount===prev.days&&g.overCount===g.days)addLine(yFor(prev.overlay,overRange),oy,'overlay');
    const bw=Math.min(18,Math.max(2,col*.61));const rect='left:'+(cx-bw/2)+'rpx;top:'+Math.min(cy,zero)+'rpx;width:'+bw+'rpx;height:'+Math.max(2,Math.abs(zero-cy))+'rpx';
    points.push({key:g.key,i,x:cx,bar:bar&&valid(g.value),dot:!bar&&valid(g.value),overDot:valid(g.overlay),tone:valueTone(g.value),
      end:g.end,prev:input[i*size+g.days-2]?.date||'',next:input[i*size+g.days]?.date||'',
      rect,dotStyle:'left:'+cx+'rpx;top:'+cy+'rpx',overStyle:'left:'+cx+'rpx;top:'+oy+'rpx',
      date:g.days===1?g.start:g.start+' 至 '+g.end,amount:valueText(g.value),overlayText:overlayText(g.overlay),
      coverage:size>1?(bar?'组内有值 '+g.count+' / '+g.days+' 日':('组内末次有效日期 '+(g.valueDate||'—'))):'',overlayDate:g.overlayDate});
  });
  const label=opts.label||'数值';
  return {points,segments,hasData:ys.length+os.length>0,dailyLabel:label,label:size===1?label:(bar?'组内合计 · ':'组内末次 · ')+label.replace(/^(当日|单日|每日)\s*/,''),overlayLabel:opts.overlayLabel||'',
    mainScale:ys.length?valueText(Math.min.apply(null,ys))+' 至 '+valueText(Math.max.apply(null,ys)):'',
    overlayScale:os.length?number(Math.min.apply(null,os),2)+' 至 '+number(Math.max.apply(null,os),2)+' '+(opts.overlayUnit||'点'):'',
    first:input.length?input[0].date:'',last:input.length?input[input.length-1].date:'',
    action:size===1?'滑动查看交易日':bar?'滑动查看分组，每组最多 '+size+' 个交易日，资金按有效记录合计':'滑动查看分组，每组最多 '+size+' 个交易日，点位取组内末次记录',
    zero:bar?zero:null,max:Math.max(0,points.length-1),grouped:size>1};
}
module.exports={chartModel};
