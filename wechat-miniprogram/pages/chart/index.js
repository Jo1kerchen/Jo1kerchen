const chartSource = require('../../data/moutai_stock_vs_liquor.json');

function zhDate(d) {
  const [y, m, dd] = d.split('-');
  return `${y}年${m}月${dd}日`;
}

function monthLabel(d) {
  const [y, m] = d.split('-');
  return `${y}年${m}月`;
}

Page({
  data: {
    meta: { subtitle: '' },
    tooltip: { show: false },
    emaVisible: { ema20: false, ema55: false, ema100: false, ema200: false }
  },

  onLoad() {
    const payload = chartSource;
    this.series = payload.data || [];
    this.summary = payload.summary || {};
    this.setData({ meta: payload.meta || {} });
  },

  onReady() {
    this.initCanvas();
  },

  initCanvas() {
    const query = wx.createSelectorQuery();
    query.select('#chartCanvas').fields({ node: true, size: true }).exec((res) => {
      const canvas = res[0].node;
      const ctx = canvas.getContext('2d');
      const dpr = wx.getWindowInfo ? wx.getWindowInfo().pixelRatio : 2;
      canvas.width = res[0].width * dpr;
      canvas.height = res[0].height * dpr;
      ctx.scale(dpr, dpr);
      this.canvas = canvas;
      this.ctx = ctx;
      this.w = res[0].width;
      this.h = res[0].height;
      this.drawChart();
    });
  },

  toggleEma(e) {
    const key = e.currentTarget.dataset.key;
    const next = { ...this.data.emaVisible, [key]: !this.data.emaVisible[key] };
    this.setData({ emaVisible: next });
    this.drawChart();
  },

  yScale(v, min, max, top, bottom) {
    if (max === min) return (top + bottom) / 2;
    return bottom - ((v - min) / (max - min)) * (bottom - top);
  },

  drawLine(points, color, width = 2) {
    const { ctx } = this;
    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    points.forEach((p, i) => {
      if (i === 0) ctx.moveTo(p.x, p.y);
      else ctx.lineTo(p.x, p.y);
    });
    ctx.stroke();
  },

  drawChart() {
    if (!this.ctx || !this.series || this.series.length === 0) return;
    const { ctx, w, h } = this;
    ctx.clearRect(0, 0, w, h);

    const ml = 70, mr = 70, mt = 80, mb = 60;
    const pw = w - ml - mr;
    const ph = h - mt - mb;

    const data = this.series;
    const n = data.length;
    const x = (i) => ml + (pw * i) / Math.max(1, n - 1);

    const closes = data.map((d) => d.close);
    const liquors = data.map((d) => d.liquor_price_ref);
    const cmin = Math.min(...closes), cmax = Math.max(...closes);
    const lmin = Math.min(...liquors), lmax = Math.max(...liquors);

    // axes only, no prominent grid
    ctx.strokeStyle = '#444';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(ml, mt);
    ctx.lineTo(ml, mt + ph);
    ctx.lineTo(ml + pw, mt + ph);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(ml + pw, mt);
    ctx.lineTo(ml + pw, mt + ph);
    ctx.stroke();

    // month tick labels
    ctx.fillStyle = '#666';
    ctx.font = '11px sans-serif';
    const step = Math.max(1, Math.floor(n / 6));
    for (let i = 0; i < n; i += step) {
      const xi = x(i);
      ctx.fillText(monthLabel(data[i].date), xi - 28, mt + ph + 18);
    }

    const closePts = data.map((d, i) => ({ x: x(i), y: this.yScale(d.close, cmin, cmax, mt, mt + ph) }));
    const liquorPts = data.map((d, i) => ({ x: x(i), y: this.yScale(d.liquor_price_ref, lmin, lmax, mt, mt + ph) }));

    this.drawLine(closePts, '#1565c0', 2.2);
    this.drawLine(liquorPts, '#c62828', 2.2);

    const emaMap = {
      ema20: { key: 'ema20', color: '#42a5f5' },
      ema55: { key: 'ema55', color: '#26a69a' },
      ema100: { key: 'ema100', color: '#ab47bc' },
      ema200: { key: 'ema200', color: '#8d6e63' }
    };

    Object.keys(this.data.emaVisible).forEach((k) => {
      if (!this.data.emaVisible[k]) return;
      const p = data.map((d, i) => ({ x: x(i), y: this.yScale(d[emaMap[k].key], cmin, cmax, mt, mt + ph) }));
      this.drawLine(p, emaMap[k].color, 1.1);
    });

    // latest labels
    const li = n - 1;
    ctx.fillStyle = '#1565c0';
    ctx.fillText(`最新股价: ${data[li].close.toFixed(2)}`, closePts[li].x - 80, closePts[li].y - 10);
    ctx.fillStyle = '#c62828';
    ctx.fillText(`最新酒价: ${data[li].liquor_price_ref.toFixed(2)}`, liquorPts[li].x - 80, liquorPts[li].y + 14);

    // fixed summary block top-right
    ctx.fillStyle = 'rgba(255,255,255,0.9)';
    ctx.fillRect(w - 230, 18, 210, 52);
    ctx.strokeStyle = 'rgba(0,0,0,0.2)';
    ctx.strokeRect(w - 230, 18, 210, 52);
    ctx.fillStyle = '#333';
    ctx.font = '12px sans-serif';
    ctx.fillText(`最大涨幅：+${(this.summary.max_runup_pct || 0).toFixed(2)}%`, w - 220, 40);
    ctx.fillText(`最大回撤：${(this.summary.max_drawdown_pct || 0).toFixed(2)}%`, w - 220, 60);

    this.chartMetrics = { ml, mt, pw, ph, n };
  },

  onTouch(e) {
    if (!this.chartMetrics || !this.series || !this.series.length) return;
    const touch = e.touches[0];
    const { ml, pw, n } = this.chartMetrics;
    let idx = Math.round(((touch.x - ml) / pw) * (n - 1));
    idx = Math.max(0, Math.min(n - 1, idx));
    const d = this.series[idx];
    this.setData({
      tooltip: {
        show: true,
        dateZh: zhDate(d.date),
        close: d.close.toFixed(2),
        liquor: d.liquor_price_ref.toFixed(2)
      }
    });
  },

  onTouchEnd() {
    // keep tooltip visible for mobile readability
  }
});
