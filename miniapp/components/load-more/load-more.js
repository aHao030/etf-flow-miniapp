// components/load-more —— 列表底部加载状态条
Component({
  properties: {
    status: { type: String, value: 'idle' }, // idle | loading | nomore | error
    text: { type: String, value: '' }
  }
});
