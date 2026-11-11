'use strict';

const fundDirections = [
  {key:'all',label:'全部'}, {key:'in',label:'流入'}, {key:'out',label:'流出'}, {key:'missing',label:'暂无估算'}
];

function fundMatcher(query,direction) {
  const text=String(query||'').trim().toLowerCase();
  return (code,name,amount)=>{
    if(text && !String(code).includes(text) && !String(name||'').toLowerCase().includes(text))return false;
    if(direction==='all')return true;
    if(!Number.isFinite(amount))return direction==='missing';
    return direction==='in' ? amount>0 : direction==='out' && amount<0;
  };
}

module.exports={fundDirections,fundMatcher};
