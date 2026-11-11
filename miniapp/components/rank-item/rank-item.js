// components/rank-item —— 榜单列表项
// item 字段对齐契约 §2.3：rank/stock_code/stock_name/industry_name/flow/intensity_bp/valid_days/flags
const fmt = require('../../utils/format');

Component({
  properties: {
    item: { type: Object, value: {} }
  },
  data: {
    flagLabels: []
  },
  observers: {
    item: function (item) {
      this.setData({
        flagLabels: (item.flags || []).map(function (f) {
          return fmt.flagLabel(f);
        })
      });
    }
  },
  methods: {
    onTap() {
      this.triggerEvent('tap', { code: this.data.item.stock_code });
    }
  }
});
