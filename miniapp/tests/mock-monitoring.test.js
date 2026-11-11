const assert = require('assert');
const mock = require('../mock/index');
const search = mock.route('/stocks', {q:'宁德',page:1,page_size:1}).data;
assert.equal(search.items[0].stock_code, '300750');
const uncovered = mock.route('/stock/000000', {period:'5d'}).data;
assert.equal(uncovered.data_status, 'no_data');
assert.equal(uncovered.movement.net_flow, null);
for (const direction of ['in', 'out']) {
  const rank = mock.route('/rank', {direction,period:'1d'}).data;
  assert(rank.items.length > 0);
  assert(rank.items.every(item => direction === 'in' ? item.flow > 0 : item.flow < 0));
}
for (const period of ['1d','5d','20d']) {
  const detail = mock.route('/stock/300750', {period}).data;
  assert.equal(detail.movement.period, period);
  assert.equal(detail.movement.net_flow, detail['flow_' + period]);
  assert(detail.etf_contributions.length > 0);
  assert(Math.abs(detail.movement.inflow + detail.movement.outflow - detail.movement.net_flow) < 0.1);
}
assert.equal(mock.route('/meta', {}).data.is_mock, true);
console.log('PASS: mock search, coverage, directions and periods');
