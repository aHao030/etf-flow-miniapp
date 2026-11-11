const assert = require('assert');
const path = require('path');
const apiPath = require.resolve('../services/api');
const requests = [];
const api = {
  getRank: p => new Promise((resolve, reject) => requests.push({kind:'rank', p, resolve, reject})),
  getStocks: p => new Promise((resolve, reject) => requests.push({kind:'stocks', p, resolve, reject})),
  getStock: (code, p) => new Promise((resolve, reject) => requests.push({kind:'stock', p, resolve, reject}))
};
require.cache[apiPath] = { id: apiPath, filename: apiPath, loaded: true, exports: api };
let definition; global.Page = p => definition = p; global.wx = { navigateTo(){}, setNavigationBarTitle(){}, stopPullDownRefresh(){} }; global.getApp = () => ({});
function page(file) { delete require.cache[require.resolve(file)]; require(file); const instance = {...definition, data: JSON.parse(JSON.stringify(definition.data))}; instance.setData = values => Object.assign(instance.data, values); return instance; }
async function run() {
 const home=page('../pages/index/index.js'); const first=home.refresh(); home.setData({direction:'out'}); const second=home.refresh();
 requests[1].resolve({items:[{stock_code:'new'}],total:1,trade_date:'today',has_more:true}); await second;
 requests[0].resolve({items:[{stock_code:'old'}],total:1,trade_date:'old',has_more:true}); await first;
 assert.equal(home.data.items[0].stock_code,'new');
 const failed=home.loadMore(); assert.equal(requests[2].p.page,2); requests[2].reject({message:'fail'}); await failed; assert.equal(home.data.page,1);
 const retry=home.loadMore(); assert.equal(requests[3].p.page,2); requests[3].resolve({items:[{stock_code:'next'}],total:2,trade_date:'today',has_more:false}); await retry; assert.equal(home.data.items.length,2);
 home.setData({query:'abc'}); const a=home.search(true); home.setData({query:'xyz'}); const b=home.search(true);
 requests[5].resolve({items:[{stock_code:'new'}],has_more:false}); await b; requests[4].resolve({items:[{stock_code:'old'}],has_more:false}); await a; assert.equal(home.data.searchItems[0].stock_code,'new');
 const detail=page('../package-detail/pages/stock-detail/stock-detail.js'); detail.setData({code:'600000'}); const d1=detail.fetch();detail.setData({period:'5d'});const d5=detail.fetch();
 const result=(name)=>({stock_name:name,stock_code:'600000',valid_days:{d1:'1/1',d5:'5/5',d20:'0/20'},quality:{},movement:{net_flow:null},etf_contributions:[],data_status:'no_data'});
 requests[7].resolve(result('new'));await d5;requests[6].resolve(result('old'));await d1;assert.equal(detail.data.name,'new');assert.equal(detail.data.flow,null);assert.equal(requests[7].p.period,'5d');
 console.log('PASS: stale responses, pagination retry, search and period detail');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
