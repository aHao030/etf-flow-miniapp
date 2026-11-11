// components/stock-row —— 通用股票/ETF 行（ETF 来源拆解、ETF 影响榜、自选列表复用）
// item: {code, name, sub, flow, intensity_bp, extra}；extra 可选（如 weight 权重文本）
Component({
  properties: {
    item: { type: Object, value: {} },
    showArrow: { type: Boolean, value: true }
  },
  methods: {
    onTap() {
      this.triggerEvent('tap', { code: this.data.item.code, type: this.data.item.type });
    }
  }
});
