// 通用 K 线画布：选股页小图/大图、收藏页大图共用
// 依赖：无（纯 canvas）
// ---------------------------------------------------------------- K 线绘制
function ma(arr, n) {
  const out = new Array(arr.length).fill(null);
  let sum = 0;
  for (let i = 0; i < arr.length; i++) {
    sum += arr[i];
    if (i >= n) sum -= arr[i - n];
    if (i >= n - 1) out[i] = sum / n;
  }
  return out;
}

/** 画布配色：跟随亮 / 暗主题 */
function chartColors() {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  return dark
    ? {
        grid: "rgba(255,255,255,0.055)", text: "#7d8798", label: "#8a95a8",
        up: "#e8737a", down: "#4fc98a",
        upVol: "#e8737a", downVol: "#4fc98a",
        ma5: "#f0a94a", ma20: "#6d8cff",
        tipBg: "rgba(20,26,35,.94)", tipBorder: "rgba(255,255,255,.14)", tipText: "#e6ecf5",
      }
    : {
        grid: "#eef0f4", text: "#8a93a3", label: "#8a93a3",
        up: "#e5484d", down: "#2f9e44",
        upVol: "#e5484d", downVol: "#2f9e44",
        ma5: "#f59f00", ma20: "#2b5cff",
        tipBg: "rgba(255,255,255,.97)", tipBorder: "rgba(16,24,40,.14)", tipText: "#1f2937",
      };
}

/** EMA 序列（用于 MACD） */
function emaArr(arr, n) {
  const k = 2 / (n + 1);
  const out = new Array(arr.length).fill(null);
  let prev = null;
  for (let i = 0; i < arr.length; i++) {
    prev = prev == null ? arr[i] : arr[i] * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

/** MACD(12,26,9)：返回 { dif, dea, bar } */
function macdSeries(closes) {
  const e12 = emaArr(closes, 12), e26 = emaArr(closes, 26);
  const dif = closes.map((_, i) => (e12[i] == null || e26[i] == null) ? null : e12[i] - e26[i]);
  const dea = emaArr(dif.map((v) => (v == null ? 0 : v)), 9);
  const bar = dif.map((v, i) => (v == null ? null : 2 * (v - dea[i])));
  return { dif, dea, bar };
}

/** 通用 K 线绘制：小面板和大弹窗共用
 *  canvas  目标画布
 *  bars    [date, open, high, low, close, volume]
 *  view    { i0, i1, hover, macd }  可见区间 [i0,i1)、光标下标、是否画 MACD
 */
function paintKline(canvas, bars, view) {
  view = view || {};
  const C = chartColors();
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth || 680;
  const H = canvas.clientHeight || 360;
  canvas.width = Math.round(W * dpr);
  canvas.height = Math.round(H * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);

  if (!bars || bars.length < 2) {
    ctx.fillStyle = C.text;
    ctx.font = "13px sans-serif";
    ctx.fillText("暂无数据", 20, 40);
    return;
  }

  const closes = bars.map((b) => b[4]);
  const m5 = ma(closes, 5), m20 = ma(closes, 20);
  const showMacd = !!view.macd;
  const macd = showMacd ? macdSeries(closes) : null;

  let i0 = Math.max(0, Math.floor(view.i0 ?? 0));
  let i1 = Math.min(bars.length, Math.floor(view.i1 ?? bars.length));
  if (i1 - i0 < 8) i0 = Math.max(0, i1 - 8);
  const n = i1 - i0;

  const padL = 6, padR = 58, padT = 16, gap = 14, dateH = 20;
  const volH = showMacd ? Math.min(80, Math.max(38, H * 0.13)) : Math.min(96, Math.max(44, H * 0.2));
  const macdH = showMacd ? Math.min(110, Math.max(58, H * 0.2)) : 0;
  const priceH = H - padT - gap - volH - dateH - (showMacd ? macdH + gap : 0);
  const plotW = W - padL - padR;
  const step = plotW / n;
  const cw = Math.max(1, Math.min(16, step * 0.62));

  let lo = Infinity, hi = -Infinity;
  for (let i = i0; i < i1; i++) {
    lo = Math.min(lo, bars[i][3]);
    hi = Math.max(hi, bars[i][2]);
  }
  for (const arr of [m5, m20]) {
    for (let i = i0; i < i1; i++) {
      if (arr[i] == null) continue;
      lo = Math.min(lo, arr[i]);
      hi = Math.max(hi, arr[i]);
    }
  }
  const margin = (hi - lo) * 0.06 || 0.1;
  lo -= margin; hi += margin;

  const yP = (v) => padT + priceH * (1 - (v - lo) / (hi - lo));
  let vsMax = 1;
  for (let i = i0; i < i1; i++) vsMax = Math.max(vsMax, bars[i][5]);
  const volTop = padT + priceH + gap;
  const yV = (v) => volTop + volH - (v / vsMax) * volH;
  const macdTop = volTop + volH + gap;
  const xC = (i) => padL + step * (i - i0 + 0.5);

  // MACD 量纲
  let mMax = 0.0001, mMin = -0.0001;
  if (showMacd) {
    for (let i = i0; i < i1; i++) {
      for (const v of [macd.dif[i], macd.dea[i], macd.bar[i]]) {
        if (v == null) continue;
        mMax = Math.max(mMax, v);
        mMin = Math.min(mMin, v);
      }
    }
    const pad = Math.max(Math.abs(mMax), Math.abs(mMin)) * 0.12;
    mMax += pad; mMin -= pad;
  }
  const yM = (v) => macdTop + macdH * (1 - (v - mMin) / (mMax - mMin));

  // 横向网格 + 右侧价格
  ctx.font = "11px ui-monospace, Consolas, monospace";
  ctx.textBaseline = "middle";
  for (let g = 0; g <= 4; g++) {
    const y = padT + (priceH / 4) * g;
    const val = hi - ((hi - lo) / 4) * g;
    ctx.strokeStyle = C.grid; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + plotW, y); ctx.stroke();
    ctx.fillStyle = C.text;
    ctx.fillText(val.toFixed(2), padL + plotW + 6, y);
  }
  // 成交量 / MACD 区域的分隔线
  ctx.strokeStyle = C.grid;
  ctx.beginPath(); ctx.moveTo(padL, volTop); ctx.lineTo(padL + plotW, volTop); ctx.stroke();
  if (showMacd) {
    ctx.beginPath(); ctx.moveTo(padL, macdTop); ctx.lineTo(padL + plotW, macdTop); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(padL, yM(0)); ctx.lineTo(padL + plotW, yM(0)); ctx.stroke();
  }

  // 形态标记（旗形/箱体），先取出来供下面各段使用
  const mark = view.mark;

  // W 底：颈线 + 两个底
  if (mark && mark.w) {
    const i1 = bars.findIndex((b) => b[0] === mark.w.l1_date);
    const i2 = bars.findIndex((b) => b[0] === mark.w.l2_date);
    const yN = yP(mark.w.neck);
    const xa = i1 >= 0 ? xC(i1) : padL;
    const xb = i2 >= 0 ? xC(i2) : padL + plotW;
    ctx.strokeStyle = "rgba(95,140,255,.85)";
    ctx.setLineDash([6, 4]);
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(xa, yN); ctx.lineTo(padL + plotW, yN); ctx.stroke();
    ctx.setLineDash([]);
    ctx.font = "11px sans-serif";
    ctx.fillStyle = "rgba(95,140,255,.95)";
    ctx.textBaseline = "bottom";
    ctx.fillText(`W底颈线 ${mark.w.neck}`, xa + 4, yN - 3);
    ctx.textBaseline = "middle";
    for (const [x, y, txt] of [[xa, yP(mark.w.l1), "L1"], [xb, yP(mark.w.l2), "L2"]]) {
      ctx.fillStyle = "rgba(95,140,255,.95)";
      ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill();
      ctx.fillText(txt, x + 5, y + 10);
    }
    if (mark.w.brk_date) {
      const ib = bars.findIndex((b) => b[0] === mark.w.brk_date);
      if (ib >= 0) {
        ctx.strokeStyle = "rgba(95,140,255,.55)";
        ctx.setLineDash([3, 3]);
        ctx.beginPath(); ctx.moveTo(xC(ib), padT); ctx.lineTo(xC(ib), volTop); ctx.stroke();
        ctx.setLineDash([]);
      }
    }
    ctx.font = "11px ui-monospace, Consolas, monospace";
  }

  // 启动信号：放量大阳线那一根标一条竖虚线与标签
  if (mark && mark.ignite) {
    const gi = bars.findIndex((b) => b[0] === mark.ignite.date);
    if (gi >= i0 && gi < i1) {
      const gx = xC(gi);
      ctx.strokeStyle = "rgba(240,169,74,.75)";
      ctx.setLineDash([4, 3]);
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(gx, padT); ctx.lineTo(gx, volTop + volH); ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = "11px sans-serif";
      ctx.fillStyle = "rgba(240,169,74,.95)";
      ctx.textBaseline = "top";
      const txt = `启动 +${(mark.ignite.chg ?? 0).toFixed(1)}% · 量${(mark.ignite.vol ?? 0).toFixed(1)}×`;
      const tw = ctx.measureText(txt).width;
      let tx = gx + 6;
      if (tx + tw > padL + plotW - 4) tx = Math.max(padL + 2, gx - tw - 6);
      ctx.fillText(txt, tx, padT + 18);
      ctx.textBaseline = "middle";
      ctx.font = "11px ui-monospace, Consolas, monospace";
    }
  }

  // 箱体：上下沿两条虚线 + 区间淡色底
  if (mark && mark.box) {
    const yTop = yP(mark.box.high);
    const yBot = yP(mark.box.low);
    const boxLeft = padL + step * Math.max(0, (i1 - i0) - (mark.box.days || 90));
    ctx.fillStyle = "rgba(120,180,255,.07)";
    ctx.fillRect(boxLeft, Math.min(yTop, yBot), padL + plotW - boxLeft, Math.abs(yBot - yTop));
    ctx.strokeStyle = "rgba(120,180,255,.5)";
    ctx.setLineDash([5, 4]);
    ctx.lineWidth = 1;
    for (const y of [yTop, yBot]) {
      ctx.beginPath();
      ctx.moveTo(boxLeft, y);
      ctx.lineTo(padL + plotW, y);
      ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.font = "11px sans-serif";
    ctx.textBaseline = "middle";
    ctx.fillStyle = "rgba(120,180,255,.95)";
    const posTxt = mark.box.pos == null ? ""
      : ` · ${mark.box.pos <= 0.3 ? "底部" : mark.box.pos >= 0.7 ? "顶部" : "中部"}`;
    ctx.fillText(`箱体 ${mark.box.days} 天${posTxt}`, boxLeft + 6, Math.min(yTop, yBot) + 10);
    ctx.font = "11px ui-monospace, Consolas, monospace";
  }

  // 旗形：旗面区间阴影 + 旗杆竖线
  if (mark && mark.i0 >= i0 && mark.i0 < i1) {
    const x0 = padL + step * (mark.i0 - i0);
    const x1 = padL + plotW;
    ctx.fillStyle = "rgba(240,169,74,.10)";
    ctx.fillRect(x0, padT, x1 - x0, priceH);
    ctx.strokeStyle = "rgba(240,169,74,.55)";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(x0 + step / 2, padT); ctx.lineTo(x0 + step / 2, padT + priceH); ctx.stroke();
    ctx.setLineDash([]);
    if (mark.label) {
      const text = mark.label + (mark.chg != null ? `（+${mark.chg.toFixed(1)}%）` : "");
      ctx.font = "11px sans-serif";
      const tw = ctx.measureText(text).width;
      let tx = x0 + step + 6;
      if (tx + tw > padL + plotW - 4) tx = Math.max(padL + 2, x0 - tw - 6);
      ctx.fillStyle = "rgba(240,169,74,.95)";
      ctx.textBaseline = "top";
      ctx.fillText(text, tx, padT + 4);
      ctx.textBaseline = "middle";
      ctx.font = "11px ui-monospace, Consolas, monospace";
    }
  }

  // K 线
  for (let i = i0; i < i1; i++) {
    const o = bars[i][1], h = bars[i][2], l = bars[i][3], c = bars[i][4];
    const rising = c >= o;
    ctx.strokeStyle = ctx.fillStyle = rising ? C.up : C.down;
    const x = xC(i);
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, yP(h)); ctx.lineTo(x, yP(l)); ctx.stroke();
    const y1 = yP(Math.max(o, c)), y2 = yP(Math.min(o, c));
    ctx.fillRect(x - cw / 2, y1, cw, Math.max(1, y2 - y1));
  }

  // 均线
  const line = (arr, color, width) => {
    ctx.strokeStyle = color; ctx.lineWidth = width || 1.2; ctx.beginPath();
    let started = false;
    for (let i = i0; i < i1; i++) {
      if (arr[i] == null) continue;
      const x = xC(i), y = yP(arr[i]);
      if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
    }
    ctx.stroke();
  };
  line(m5, C.ma5);
  line(m20, C.ma20, 1.4);

  // 成交量
  for (let i = i0; i < i1; i++) {
    const o = bars[i][1], c = bars[i][4];
    ctx.fillStyle = c >= o ? C.upVol : C.downVol;
    const y = yV(bars[i][5]);
    ctx.fillRect(xC(i) - cw / 2, y, cw, volTop + volH - y);
  }
  ctx.fillStyle = C.text;
  ctx.fillText("VOL", padL + 2, volTop + 10);

  // MACD 副图
  if (showMacd) {
    const bw = Math.max(1, cw * 0.7);
    for (let i = i0; i < i1; i++) {
      const v = macd.bar[i];
      if (v == null) continue;
      const x = xC(i);
      const y0 = yM(0), y1 = yM(v);
      ctx.fillStyle = v >= 0 ? C.upVol : C.downVol;
      ctx.fillRect(x - bw / 2, Math.min(y0, y1), bw, Math.max(1, Math.abs(y1 - y0)));
    }
    const mline = (arr, color) => {
      ctx.strokeStyle = color; ctx.lineWidth = 1.2; ctx.beginPath();
      let started = false;
      for (let i = i0; i < i1; i++) {
        if (arr[i] == null) continue;
        const x = xC(i), y = yM(arr[i]);
        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
      }
      ctx.stroke();
    };
    mline(macd.dif, C.ma5);
    mline(macd.dea, C.ma20);
    ctx.fillStyle = C.text;
    ctx.fillText("MACD(12,26,9)", padL + 2, macdTop + 10);
    ctx.fillStyle = C.ma5; ctx.fillRect(padL + 88, macdTop + 6, 10, 2);
    ctx.fillStyle = C.label; ctx.fillText("DIF", padL + 102, macdTop + 7);
    ctx.fillStyle = C.ma20; ctx.fillRect(padL + 134, macdTop + 6, 10, 2);
    ctx.fillStyle = C.label; ctx.fillText("DEA", padL + 148, macdTop + 7);
  }

  // 日期刻度
  const axisY = showMacd ? macdTop + macdH : volTop + volH;
  ctx.textBaseline = "top";
  ctx.fillStyle = C.text;
  const ticks = [i0, i0 + Math.floor(n / 3), i0 + Math.floor((2 * n) / 3), i1 - 1];
  ticks.forEach((i) => {
    const label = bars[i][0].slice(5);
    const tx = Math.min(xC(i) - 14, padL + plotW - 30);
    ctx.fillText(label, Math.max(padL, tx), axisY + 4);
  });

  // 做小做底角标
  if (mark && mark.baseLabel) {
    ctx.font = "11px sans-serif";
    ctx.fillStyle = "rgba(93,168,255,.95)";
    ctx.textBaseline = "middle";
    ctx.fillText(mark.baseLabel, padL + 108, padT - 10);
    ctx.font = "11px ui-monospace, Consolas, monospace";
  }

  // 均线图例
  ctx.textBaseline = "middle";
  ctx.fillStyle = C.ma5; ctx.fillRect(padL + 2, padT - 10, 10, 2);
  ctx.fillStyle = C.label; ctx.fillText("MA5", padL + 16, padT - 10);
  ctx.fillStyle = C.ma20; ctx.fillRect(padL + 56, padT - 10, 10, 2);
  ctx.fillStyle = C.label; ctx.fillText("MA20", padL + 70, padT - 10);

  // 十字光标 + 信息框
  if (view.hover != null && view.hover >= i0 && view.hover < i1 && view.hover >= 0) {
    const h = view.hover;
    const b = bars[h];
    const x = xC(h);
    const y = yP(b[4]);
    ctx.save();
    ctx.strokeStyle = C.label;
    ctx.globalAlpha = 0.55;
    ctx.setLineDash([3, 3]);
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, axisY); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + plotW, y); ctx.stroke();
    ctx.restore();

    // 右侧价格气泡
    const label = b[4].toFixed(2);
    ctx.fillStyle = C.label;
    ctx.fillRect(padL + plotW + 2, y - 9, padR - 6, 18);
    ctx.fillStyle = "#fff";
    ctx.fillText(label, padL + plotW + 8, y);

    // 底部日期气泡
    const dw = 62;
    const dx = Math.min(Math.max(x - dw / 2, padL), padL + plotW - dw);
    ctx.fillStyle = C.label;
    ctx.fillRect(dx, axisY + 2, dw, 16);
    ctx.fillStyle = "#fff";
    ctx.textAlign = "center";
    ctx.fillText(b[0], dx + dw / 2, axisY + 10);
    ctx.textAlign = "left";

    // 信息框
    const prev = h > 0 ? bars[h - 1][4] : null;
    const chg = prev ? ((b[4] / prev - 1) * 100) : null;
    const rows = [
      [b[0], C.label],
      [`开 ${b[1].toFixed(2)}  高 ${b[2].toFixed(2)}`, ""],
      [`低 ${b[3].toFixed(2)}  收 ${b[4].toFixed(2)}`, ""],
      chg == null ? null : [`涨跌 ${chg > 0 ? "+" : ""}${chg.toFixed(2)}%`,
                            chg > 0 ? C.up : C.down],
      [`量 ${fmtVol(b[5])}`, ""],
      m5[h] == null ? null : [`MA5 ${m5[h].toFixed(2)}`, C.ma5],
      m20[h] == null ? null : [`MA20 ${m20[h].toFixed(2)}`, C.ma20],
      (showMacd && macd.dif[h] != null)
        ? [`DIF ${macd.dif[h].toFixed(3)}  DEA ${macd.dea[h].toFixed(3)}`, C.label] : null,
      (showMacd && macd.bar[h] != null)
        ? [`MACD ${macd.bar[h].toFixed(3)}`, macd.bar[h] >= 0 ? C.up : C.down] : null,
    ].filter(Boolean);

    ctx.font = "12px ui-monospace, Consolas, monospace";
    const boxW = 208, lineH = 17;
    const boxH = rows.length * lineH + 14;
    let bx = x + 16;
    if (bx + boxW > padL + plotW) bx = x - boxW - 16;
    const by = Math.min(Math.max(padT + 4, y - boxH / 2), axisY - boxH - 4);
    ctx.fillStyle = C.tipBg;
    ctx.strokeStyle = C.tipBorder;
    ctx.beginPath();
    ctx.roundRect(bx, by, boxW, boxH, 8);
    ctx.fill(); ctx.stroke();
    ctx.textBaseline = "top";
    rows.forEach(([text, color], idx) => {
      ctx.fillStyle = color || C.tipText;
      ctx.fillText(text, bx + 10, by + 8 + idx * lineH);
    });
    ctx.textBaseline = "middle";
  }
}

function fmtVol(v) {
  if (v == null) return "—";
  if (v >= 1e8) return (v / 1e8).toFixed(2) + " 亿";
  if (v >= 1e4) return (v / 1e4).toFixed(1) + " 万";
  return String(Math.round(v));
}

/** 把形态信息换算成画布区间：旗杆那一根 + 旗面到最后一根 */
function patternMark(bars, pattern) {
  if (!pattern) return null;
  const out = { i0: -1, i1: -1, label: null, chg: null, baseLabel: null, box: null,
                ignite: null };
  if (pattern.box) out.box = pattern.box;
  if (pattern.ignite && pattern.ignite.date) out.ignite = pattern.ignite;
  if (pattern.w) out.w = pattern.w;
  if (pattern.base_ok) {
    out.baseLabel = `做小做底 · 2年振幅 ${((pattern.base_amp ?? 0) * 100).toFixed(0)}%`;
  }
  if (pattern.pole_date) {
    const i = bars.findIndex((b) => b[0] === pattern.pole_date);
    if (i >= 0) {
      out.i0 = i;
      out.i1 = bars.length - 1;
      out.label = `旗形 · ${pattern.flag_days || 0} 天整理`;
      out.chg = pattern.pole_chg;
    }
  }
  return (out.baseLabel || out.label || out.box || out.ignite || out.w) ? out : null;
}



/**
 * 可缩放大 K 线视图（大弹窗用）
 * 滚轮缩放、按住拖动平移、双击复位、悬停十字光标。
 */
class KlineChart {
  constructor(canvas, opts) {
    this.canvas = canvas;
    const o = opts || {};
    this.macd = o.macd !== false;
    this.onNeedOlder = o.onNeedOlder || null;
    this.onHover = o.onHover || null;
    this.bars = [];
    this.mark = null;
    this.more = false;
    this.loading = false;
    this.i0 = 0;
    this.i1 = 0;
    this.hover = null;
    this.drag = null;
    if (canvas) this._bind();
  }

  setData(bars, opts) {
    const o = opts || {};
    this.bars = bars || [];
    this.mark = o.mark !== undefined ? o.mark
      : (o.pattern ? patternMark(this.bars, o.pattern) : null);
    this.more = !!o.more;
    this.loading = false;
    this.reset();
  }

  reset() {
    const n = this.bars.length;
    this.i1 = n;
    this.i0 = Math.max(0, n - Math.min(n, 250));
    this.hover = null;
    this.render();
  }

  visible() {
    return Math.max(1, this.i1 - this.i0);
  }

  render() {
    if (!this.canvas || !this.bars.length) {
      if (this.canvas) {
        const ctx = this.canvas.getContext("2d");
        ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
      }
      return;
    }
    paintKline(this.canvas, this.bars, {
      i0: this.i0, i1: this.i1, hover: this.hover,
      macd: this.macd, mark: this.mark,
    });
  }

  zoomAt(clientX, factor) {
    if (!this.bars.length) return;
    const rect = this.canvas.getBoundingClientRect();
    const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
    const span = this.visible();
    const newSpan = Math.max(20, Math.min(this.bars.length, Math.round(span * factor)));
    if (newSpan === span) return;
    const ratio = x / Math.max(1, rect.width);
    const anchor = this.i0 + span * ratio;
    let i0 = Math.round(anchor - newSpan * ratio);
    i0 = Math.max(0, Math.min(this.bars.length - newSpan, i0));
    this.i0 = i0;
    this.i1 = i0 + newSpan;
    this.hover = null;
    this.render();
  }

  prepend(older, more) {
    const add = older.length;
    if (!add) return;
    this.bars = older.concat(this.bars);
    this.i0 += add;
    this.i1 += add;
    this.more = more;
    this.render();
  }

  _bind() {
    const cv = this.canvas;
    cv.addEventListener("mousemove", (e) => {
      if (!this.bars.length) return;
      if (this.drag) {
        const rect = cv.getBoundingClientRect();
        const dx = e.clientX - this.drag.x;
        const span = this.visible();
        const barsPerPx = span / Math.max(1, rect.width);
        const shift = Math.round(-dx * barsPerPx);
        let i0 = Math.max(0, Math.min(this.bars.length - span, this.drag.i0 + shift));
        this.i0 = i0;
        this.i1 = i0 + span;
        this.hover = null;
        this.render();
        if (i0 <= 3 && this.onNeedOlder) this.onNeedOlder();
        return;
      }
      const rect = cv.getBoundingClientRect();
      const padL = 6, padR = 58;
      const plotW = rect.width - padL - padR;
      const step = plotW / this.visible();
      const idx = Math.round((e.clientX - rect.left - padL) / Math.max(1, step) - 0.5) + this.i0;
      const clamped = Math.max(this.i0, Math.min(this.i1 - 1, idx));
      if (clamped !== this.hover) {
        this.hover = clamped;
        this.render();
        if (this.onHover) this.onHover(this.hover);
      }
    });

    cv.addEventListener("mouseleave", () => {
      this.hover = null;
      this.render();
      if (this.onHover) this.onHover(null);
    });

    cv.addEventListener("mousedown", (e) => {
      if (!this.bars.length) return;
      this.drag = { x: e.clientX, i0: this.i0 };
      cv.style.cursor = "grabbing";
      e.preventDefault();
    });
    window.addEventListener("mouseup", () => {
      if (this.drag) {
        this.drag = null;
        cv.style.cursor = "";
      }
    });

    cv.addEventListener("wheel", (e) => {
      e.preventDefault();
      this.zoomAt(e.clientX, e.deltaY > 0 ? 1.18 : 0.85);
    }, { passive: false });

    cv.addEventListener("dblclick", () => this.reset());
    window.addEventListener("resize", () => this.render());
  }
}
