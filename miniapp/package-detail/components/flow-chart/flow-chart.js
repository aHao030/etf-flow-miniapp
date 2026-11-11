// package-detail/components/flow-chart —— 序列图组件（echarts-for-weixin 版）
//
// 对外 props 与骨架阶段占位实现完全一致，页面零改动：
//   series     [{trade_date, ...}] 序列（stock.series / etf.share_series / etf.scale_series）
//   seriesKey  取值字段名（'flow' | 'share_change' | 'scale'）
//   chartType  'bar'（正负红涨绿跌 + 5日均值线）| 'line'（单色折线）
//   unitText   y 轴刻度单位文案；unitDiv 显示除数
//   footnote   图表下方说明文字
//
// 说明：
// - 图表数据仅经 chart.setOption 进入 canvas，不进 setData（m3-草案 §3）；
// - echarts 采用官方精简构建（echarts.simple.min.js 重命名为 echarts.js，~454KB，含
//   bar/line/grid/tooltip/legend/title），放在本分包 components/ec-canvas/ 下；
//   本环境无打包器无法做在线按需裁剪，用官方 simple 构建替代，后续可换自定义裁剪构建（同名替换即可）；
// - ec-canvas 官方组件（ecomfe/echarts-for-weixin master），触控经 zrender 转发，真机 tooltip 可用；
// - 序列点 ≤120（契约 ≤60，双序列冗余），空值跳过，有效点 <2 时显示空态。
const echarts = require('../ec-canvas/echarts');

const UP_COLOR = '#d43c33';
const DOWN_COLOR = '#0a9b4b';
const LINE_COLOR = '#2b5cd9';
const MA_COLOR = '#e6a23c';

Component({
  properties: {
    series: { type: Array, value: [] },
    seriesKey: { type: String, value: 'flow' },
    chartType: { type: String, value: 'bar' },
    seriesName: { type: String, value: '' },
    unitText: { type: String, value: '万元' },
    unitDiv: { type: Number, value: 10000 },
    footnote: { type: String, value: '' }
  },

  data: {
    ec: { lazyLoad: true },
    empty: true,
    heightPx: 220
  },

  observers: {
    'series, seriesKey, chartType': function () {
      if (this._ready) this.render();
    }
  },

  lifetimes: {
    ready() {
      this._ready = true;
      try {
        const info = wx.getSystemInfoSync();
        this.setData({ heightPx: Math.round((info.windowWidth * 400) / 750) });
      } catch (e) {
        /* 保留默认 220px */
      }
      this.render();
    },
    detached() {
      if (this.chart) {
        try {
          this.chart.dispose();
        } catch (e) {
          /* ignore */
        }
        this.chart = null;
      }
    }
  },

  methods: {
    values() {
      const key = this.data.seriesKey;
      return (this.data.series || []).map(function (s) {
        const v = s[key];
        return v === null || v === undefined || isNaN(Number(v)) ? null : Number(v);
      });
    },

    render() {
      const series = this.data.series || [];
      const values = this.values();
      const validCount = values.filter(function (v) {
        return v !== null;
      }).length;
      if (!series.length || validCount < 2) {
        if (!this.data.empty) this.setData({ empty: true });
        return;
      }
      if (this.data.empty) this.setData({ empty: false });
      this.ensureChart(0);
    },

    // ec-canvas 节点在 empty=false 后异步渲染，轮询确保拿到组件后初始化
    ensureChart(attempt) {
      if (this.chart) {
        this.chart.setOption(this.buildOption(), true);
        return;
      }
      const comp = this.selectComponent('#mychart');
      if (!comp) {
        if (attempt < 12) {
          setTimeout(() => this.ensureChart(attempt + 1), 60);
        }
        return;
      }
      comp.init((canvas, width, height, dpr) => {
        const chart = echarts.init(canvas, null, { width, height, devicePixelRatio: dpr });
        this.chart = chart;
        chart.setOption(this.buildOption(), true);
        return chart;
      });
    },

    buildOption() {
      const series = this.data.series || [];
      const values = this.values();
      const isBar = this.data.chartType === 'bar';
      const unitDiv = this.data.unitDiv || 10000;
      const dates = series.map(function (s) {
        return (s.trade_date || '').slice(5);
      });

      let maxAbs = 0;
      values.forEach(function (v) {
        if (v !== null && Math.abs(v) > maxAbs) maxAbs = Math.abs(v);
      });
      if (maxAbs === 0) maxAbs = 1;

      const fmtVal = (v) => {
        if (v === null || v === undefined) return '—';
        const sign = v > 0 ? '+' : '';
        return sign + (v / unitDiv).toFixed(2);
      };

      const yAxis = isBar
        ? {
            type: 'value',
            min: -maxAbs,
            max: maxAbs,
            splitLine: { lineStyle: { color: '#f2f3f5' } },
            axisLabel: { fontSize: 10, color: '#8a919f', formatter: fmtVal }
          }
        : {
            type: 'value',
            splitLine: { lineStyle: { color: '#f2f3f5' } },
            axisLabel: { fontSize: 10, color: '#8a919f', formatter: fmtVal }
          };

      const seriesArr = [];
      const sName =
        this.data.seriesName || (this.data.unitText || '').replace(/[万亿]/g, '') || '数值';
      if (isBar) {
        seriesArr.push({
          name: sName,
          type: 'bar',
          data: values,
          barMaxWidth: 8,
          itemStyle: function (params) {
            return { color: params.value < 0 ? DOWN_COLOR : UP_COLOR };
          }
        });
        // 5 日均值折线（前端计算）
        const ma = [];
        for (let i = 0; i < values.length; i++) {
          let sum = 0;
          let cnt = 0;
          for (let j = Math.max(0, i - 4); j <= i; j++) {
            if (values[j] !== null) {
              sum += values[j];
              cnt++;
            }
          }
          ma.push(cnt ? Math.round((sum / cnt) * 100) / 100 : null);
        }
        seriesArr.push({
          name: '5日均值',
          type: 'line',
          data: ma,
          lineStyle: { color: MA_COLOR, width: 1.2 },
          itemStyle: { color: MA_COLOR },
          symbol: 'none',
          smooth: false,
          connectNulls: false,
          z: 3
        });
      } else {
        seriesArr.push({
          name: sName,
          type: 'line',
          data: values,
          lineStyle: { color: LINE_COLOR, width: 1.5 },
          itemStyle: { color: LINE_COLOR },
          symbol: 'circle',
          symbolSize: 4,
          showSymbol: false,
          smooth: false,
          connectNulls: false
        });
      }

      return {
        grid: { left: 52, right: 10, top: 26, bottom: 24 },
        xAxis: {
          type: 'category',
          data: dates,
          boundaryGap: isBar,
          axisLine: { lineStyle: { color: '#c9cdd4' } },
          axisTick: { show: false },
          axisLabel: {
            fontSize: 10,
            color: '#8a919f',
            interval: Math.max(0, Math.floor(dates.length / 4))
          }
        },
        yAxis: yAxis,
        tooltip: {
          trigger: 'axis',
          confine: true,
          axisPointer: { type: 'line', lineStyle: { color: '#646a73', type: 'dashed' } },
          formatter: (params) => {
            if (!params || !params.length) return '';
            const lines = [params[0].axisValueLabel];
            params.forEach(function (p) {
              if (p.value === null || p.value === undefined || p.value === '-') return;
              const sign = p.value > 0 ? '+' : '';
              lines.push(p.marker + ' ' + p.seriesName + '：' + sign + (p.value / unitDiv).toFixed(2));
            });
            return lines.join('\n');
          }
        },
        series: seriesArr,
        animation: false
      };
    }
  }
});
