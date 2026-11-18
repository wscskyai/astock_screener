/* 指数首页：横向指数条 + 当日数据条 + 分时/日周月 K（数据实时抓取，不入库） */
(function () {
  "use strict";

  // 兼容老链接：/?code=600000、/?db=1、/?login=1 依旧打开选股页
  const qs = location.search;
  if (/[?&](code|db|login)=/.test(qs)) {
    location.replace("/screen" + qs);
    return;
  }

  const qp = new URLSearchParams(qs);
  const urlMode = qp.get("mode");
  const urlIdx = (qp.get("idx") || "").toUpperCase();
  const urlDays = Number(qp.get("days")) === 5 ? 5 : 1;

  // 非开盘时间默认看日K，开盘时间（工作日 9:30-11:30 / 13:00-15:00）才默认分时
  function isTrading() {
    const now = new Date();
    const day = now.getDay();
    if (day === 0 || day === 6) return false;
    const hm = now.getHours() * 100 + now.getMinutes();
    return (hm >= 930 && hm <= 1130) || (hm >= 1300 && hm <= 1500);
  }

  const state = {
    code: urlIdx || "000001",
    mode: ["min", "day", "week", "month"].includes(urlMode)
      ? urlMode
      : (isTrading() ? "min" : "day"),
    days: urlDays, quotes: [], ths: {}, quote: null,
    data: null, bars: null, loading: false, hover: -1, mouse: null,
  };

  const $ = (id) => document.getElementById(id);

  function fmt(v, dec) {
    if (v == null || Number.isNaN(v)) return "—";
    return Number(v).toFixed(dec == null ? 2 : dec);
  }

  /* ---------------- 顶栏登录态（与选股页 / 收藏页 / 个人中心联动） */
  async function loadMe() {
    try {
      const d = await (await fetch("/api/me", { cache: "no-store" })).json();
      const u = d.user;
      const btn = $("userBtn");
      const label = $("userLabel");
      const fav = $("favBtn");
      const count = $("favCount");
      if (!btn || !label) return;
      if (u) {
        label.textContent = u.nickname || u.username;
        btn.href = "/account";
        btn.title = "个人中心";
        const n = (d.favorites || []).length;
        if (count) { count.textContent = String(n); count.hidden = false; }
        if (fav) fav.title = `我的收藏（${n} 只）`;
      } else {
        label.textContent = "登录 / 注册";
        btn.href = "/screen?login=1";
        btn.title = "登录 / 注册";
        if (count) { count.textContent = ""; count.hidden = true; }
        if (fav) fav.title = "我的收藏";
      }
    } catch (e) { /* 保留静态登录入口 */ }
  }

  function cls(v) { return v == null ? "" : v >= 0 ? "up" : "down"; }

  function chartColors() {
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    return dark
      ? { grid: "rgba(255,255,255,.055)", text: "#7d8798", label: "#8a95a8",
          up: "#e8737a", down: "#4fc98a", pre: "rgba(255,255,255,.28)",
          price: "#6d8cff", ma5: "#f0a94a", ma20: "#6d8cff",
          tipBg: "rgba(20,26,35,.94)", tipBorder: "rgba(255,255,255,.14)", tipText: "#e6ecf5",
          fill: "rgba(109,140,255,.10)" }
      : { grid: "#eef0f4", text: "#8a93a3", label: "#8a93a3",
          up: "#e5484d", down: "#2f9e44", pre: "rgba(16,24,40,.25)",
          price: "#2b5cff", ma5: "#f59f00", ma20: "#2b5cff",
          tipBg: "rgba(255,255,255,.97)", tipBorder: "rgba(16,24,40,.14)", tipText: "#1f2937",
          fill: "rgba(43,92,255,.08)" };
  }

  /* ---------------- 指数条 + 当日数据 */
  async function loadQuotes() {
    try {
      const d = await (await fetch("/api/index/quotes")).json();
      if (d.error) throw new Error(d.error);
      state.quotes = d.quotes || [];
      state.ths = d.ths || {};
      renderStrip();
      const q = state.quotes.find((x) => x.code === state.code);
      if (q) renderBar(q);
      renderThs();
    } catch (e) {
      $("idxStrip").innerHTML = `<div class="idx-cell loading">指数行情加载失败：${e.message}</div>`;
    }
  }

  /** 把当前指数/模式/天数写回地址栏，刷新或转发后还能打开同一视图 */
  function syncUrl() {
    const u = new URL(location.href);
    u.searchParams.set("idx", state.code);
    u.searchParams.set("mode", state.mode);
    if (state.mode === "min" && state.days === 5) u.searchParams.set("days", "5");
    else u.searchParams.delete("days");
    history.replaceState(null, "", u.toString());
  }

  function renderStrip() {
    $("idxStrip").innerHTML = state.quotes.map((q) => `
      <div class="idx-cell${q.code === state.code ? " on" : ""}" data-code="${q.code}">
        <span class="ic-name">${q.name}</span>
        <span class="ic-pct ${cls(q.pct)}">${q.pct == null ? "—"
          : (q.pct >= 0 ? "+" : "") + fmt(q.pct) + "%"}</span>
      </div>`).join("");
    $("idxStrip").querySelectorAll(".idx-cell").forEach((el) => {
      el.addEventListener("click", () => {
        state.code = el.dataset.code;
        syncUrl();
        $("idxStrip").querySelectorAll(".idx-cell")
          .forEach((x) => x.classList.toggle("on", x === el));
        const q = state.quotes.find((x) => x.code === state.code);
        if (q) renderBar(q);
        renderThs();
        state.hover = -1;
        load();
      });
    });
  }

  function renderBar(q) {
    state.quote = q;
    $("ibName").textContent = q.name;
    $("ibPrice").textContent = fmt(q.price);
    $("ibPrice").className = "ib-price " + cls(q.change);
    $("ibChg").className = "ib-chg " + cls(q.change);
    $("ibChg").textContent = q.change == null ? "—"
      : `${q.change >= 0 ? "+" : ""}${fmt(q.change)}  ${q.change >= 0 ? "+" : ""}${fmt(q.pct)}%`;
    $("ibLast").textContent = fmt(q.price);
    $("ibHigh").textContent = fmt(q.high);
    $("ibLow").textContent = fmt(q.low);
    $("ibAmount").textContent = q.amount == null ? "—" : `${fmt(q.amount, 0)} 亿`;
  }

  function renderThs() {
    const url = state.ths[state.code] || "https://stockpage.10jqka.com.cn/1A0001/";
    $("idxThs").href = url;
  }

  /* ---------------- 图表数据 */
  async function load() {
    if (state.loading) return;
    $("idxTip").hidden = true;
    state.hover = -1;
    state.loading = true;
    $("idxLoading").hidden = false;
    $("idxError").hidden = true;
    try {
      if (state.mode === "min") {
        const r = await fetch(`/api/index/minute?code=${state.code}&days=${state.days}`);
        const d = await r.json();
        if (d.error) throw new Error(d.error);
        state.data = d;
        state.bars = null;
        const q = d.quote || {};
        const q2 = state.quotes.find((x) => x.code === state.code) || {};
        if (q2.name) q.name = q2.name;
        renderBar(Object.assign({}, q2, {
          name: q2.name || q.name, price: q.price, change: q.change, pct: q.pct,
          high: q.high, low: q.low, amount: q.amount, volume: q.volume,
        }));
        $("ibVol").textContent = q.volume == null ? "—" : `${fmt(q.volume, 0)} 万手`;
        const last = (d.points || []).slice(-1)[0];
        $("ibTime").textContent = last ? last[0] : "—";
      } else {
        const r = await fetch(`/api/index/kline?code=${state.code}&period=${state.mode}&n=300`);
        const d = await r.json();
        if (d.error) throw new Error(d.error);
        state.bars = d.bars || [];
        state.data = null;
        renderKlineBar(d.bars || d);
      }
      draw();
    } catch (e) {
      state.data = null;
      state.bars = null;
      $("idxError").hidden = false;
      $("idxError").textContent = "数据加载失败：" + e.message;
      draw();
    } finally {
      state.loading = false;
      $("idxLoading").hidden = true;
    }
  }

  function renderKlineBar(bars) {
    if (!bars.length) return;
    const last = bars[bars.length - 1];
    const prev = bars.length > 1 ? bars[bars.length - 2] : last;
    const close = last[4], pre = prev[4];
    const chg = close - pre, pct = (close / pre - 1) * 100;
    const q2 = state.quotes.find((x) => x.code === state.code) || {};
    $("ibName").textContent = q2.name || "";
    $("ibPrice").textContent = fmt(close);
    $("ibPrice").className = "ib-price " + cls(chg);
    $("ibChg").className = "ib-chg " + cls(chg);
    $("ibChg").textContent = `${chg >= 0 ? "+" : ""}${fmt(chg)}  ${chg >= 0 ? "+" : ""}${fmt(pct)}%`;
    $("ibLast").textContent = fmt(close);
    $("ibHigh").textContent = fmt(last[2]);
    $("ibLow").textContent = fmt(last[3]);
    $("ibAmount").textContent = last[6] ? `${fmt(last[6] / 1e8, 0)} 亿` : "—";
    $("ibVol").textContent = `${fmt(last[5] / 1e4, 0)} 万手`;
    $("ibTime").textContent = last[0];
  }

  /* ---------------- 指标 */
  // ---------------- 分时横轴：按全天交易时段定位
  const SESSIONS = {
    HK: [["09:30", "12:00"], ["13:00", "16:00"]],   // 港股：中午休市 1 小时
    A: [["09:30", "11:30"], ["13:00", "15:00"]],    // A 股：11:30~13:00 休市
  };

  // 横轴刻度取好看的整点/半点
  const AXIS_MARKS = {
    HK: ["09:30", "11:00", "12:00", "14:00", "16:00"],
    A: ["09:30", "10:30", "11:30", "14:00", "15:00"],
  };

  function sessionOf(code) {
    return String(code).toUpperCase() === "HSI" ? SESSIONS.HK : SESSIONS.A;
  }

  function axisMarks(code) {
    return String(code).toUpperCase() === "HSI" ? AXIS_MARKS.HK : AXIS_MARKS.A;
  }

  function hhmm(s) {
    const [h, m] = String(s).split(":").map(Number);
    return h * 60 + m;
  }

  /** 全天一共多少个分钟槽位（含首尾），上证 241、恒生 331 */
  function daySlots(code) {
    let n = 0;
    for (const [a, b] of sessionOf(code)) n += hhmm(b) - hhmm(a);
    return n + 1;
  }

  /** "HH:MM" → 第几个分钟槽位（跳过午休） */
  function slotOf(code, t) {
    const m = hhmm(t);
    let acc = 0;
    for (const [a, b] of sessionOf(code)) {
      const s0 = hhmm(a), s1 = hhmm(b);
      if (m <= s0) return acc;
      if (m <= s1) return acc + (m - s0);
      acc += s1 - s0;
    }
    return acc;
  }

  /** 槽位 → "HH:MM"（画刻度用） */
  function timeOfSlot(code, slot) {
    let acc = 0;
    for (const [a, b] of sessionOf(code)) {
      const len = hhmm(b) - hhmm(a);
      if (slot <= acc + len) {
        const m = hhmm(a) + (slot - acc);
        return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
      }
      acc += len;
    }
    return "";
  }

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

  function ema(arr, n) {
    const k = 2 / (n + 1), out = new Array(arr.length).fill(null);
    let prev = null;
    for (let i = 0; i < arr.length; i++) {
      prev = prev == null ? arr[i] : arr[i] * k + prev * (1 - k);
      out[i] = prev;
    }
    return out;
  }

  function macd(closes) {
    const e12 = ema(closes, 12), e26 = ema(closes, 26);
    const dif = closes.map((_, i) => e12[i] - e26[i]);
    const dea = ema(dif, 9);
    const bar = dif.map((v, i) => 2 * (v - dea[i]));
    return { dif, dea, bar };
  }

  /* ---------------- 画图 */
  function layout(W, H) {
    const padL = 8, padR = 104, padT = 12, gap = 12, dateH = 20;
    const volH = Math.min(80, Math.max(40, H * 0.14));
    const macdH = Math.min(100, Math.max(52, H * 0.18));
    const priceH = H - padT - gap * 2 - volH - macdH - dateH;
    const volTop = padT + priceH + gap;
    const macdTop = volTop + volH + gap;
    return { padL, padR, padT, gap, dateH, volH, macdH, priceH, volTop, macdTop,
             plotW: W - padL - padR, axisY: macdTop + macdH };
  }

  function drawMacd(ctx, C, L, m, i0, i1, xAt, forcedW, minSpan) {
    const { padL, plotW, macdTop, macdH } = L;
    let hi = 0.0001, lo = -0.0001;
    for (let i = i0; i < i1; i++) {
      for (const v of [m.dif[i], m.dea[i], m.bar[i]]) {
        if (v == null) continue;
        hi = Math.max(hi, v); lo = Math.min(lo, v);
      }
    }
    const pad = Math.max(hi, -lo) * 0.12;
    hi += pad; lo -= pad;
    if (minSpan && hi - lo < minSpan) {          // 开盘几分钟 MACD 几乎为 0，别把噪声放大
      const mid = (hi + lo) / 2;
      hi = mid + minSpan / 2;
      lo = mid - minSpan / 2;
    }
    const yM = (v) => macdTop + macdH * (1 - (v - lo) / (hi - lo));

    ctx.strokeStyle = C.grid;
    ctx.beginPath(); ctx.moveTo(padL, macdTop); ctx.lineTo(padL + plotW, macdTop); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(padL, yM(0)); ctx.lineTo(padL + plotW, yM(0)); ctx.stroke();

    const bw = forcedW || Math.max(1, (plotW / (i1 - i0)) * 0.62);
    for (let i = i0; i < i1; i++) {
      const v = m.bar[i];
      if (v == null) continue;
      const x = xAt(i), y0 = yM(0), y1 = yM(v);
      ctx.fillStyle = v >= 0 ? C.up : C.down;
      ctx.fillRect(x - bw / 2, Math.min(y0, y1), bw, Math.max(1, Math.abs(y1 - y0)));
    }
    const line = (arr, color) => {
      ctx.strokeStyle = color; ctx.lineWidth = 1.1; ctx.beginPath();
      let started = false;
      for (let i = i0; i < i1; i++) {
        if (arr[i] == null) continue;
        const x = xAt(i), y = yM(arr[i]);
        started ? ctx.lineTo(x, y) : (ctx.moveTo(x, y), started = true);
      }
      ctx.stroke();
    };
    line(m.dif, C.ma5);
    line(m.dea, C.ma20);
    ctx.fillStyle = C.text;
    ctx.font = "11px sans-serif";
    ctx.textBaseline = "top";
    ctx.fillText("MACD(12,26,9)", padL + 2, macdTop + 4);
  }

  function drawVol(ctx, C, L, vols, colors, xAt, i0, i1, forcedW) {
    const { padL, plotW, volTop, volH } = L;
    let vmax = 1;
    for (let i = i0; i < i1; i++) vmax = Math.max(vmax, vols[i]);
    ctx.strokeStyle = C.grid;
    ctx.beginPath(); ctx.moveTo(padL, volTop); ctx.lineTo(padL + plotW, volTop); ctx.stroke();
    const bw = forcedW || Math.max(1, (plotW / (i1 - i0)) * 0.62);
    for (let i = i0; i < i1; i++) {
      const y = volTop + volH - (vols[i] / vmax) * volH;
      ctx.fillStyle = colors[i];
      ctx.fillRect(xAt(i) - bw / 2, y, bw, volTop + volH - y);
    }
    ctx.fillStyle = C.text;
    ctx.font = "11px sans-serif";
    ctx.textBaseline = "top";
    ctx.fillText("VOL", padL + 2, volTop + 3);
  }

  function draw() {
    const cv = $("idxChart");
    const dpr = window.devicePixelRatio || 1;
    const W = cv.clientWidth || 900;
    const H = cv.clientHeight || 560;
    cv.width = Math.round(W * dpr);
    cv.height = Math.round(H * dpr);
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const C = chartColors();
    ctx.clearRect(0, 0, W, H);

    const isMin = state.mode === "min";
    const pts = isMin ? (state.data && state.data.points) : (state.bars || []);
    if (!pts || pts.length < 2) {
      $("idxTip").hidden = true;
      ctx.fillStyle = C.text;
      ctx.font = "13px sans-serif";
      ctx.fillText(state.loading ? "加载中…" : "暂无数据", 20, 40);
      return;
    }
    isMin ? drawMinute(ctx, W, H, C, pts) : drawKline(ctx, W, H, C, pts);
  }

  function drawMinute(ctx, W, H, C, pts) {
    const L = layout(W, H);
    const { padL, padR, padT, priceH, plotW, axisY } = L;
    const pre = state.data.preClose || pts[0][1];
    // 横轴固定为「全天」：当前只走了 10 分钟，就只占 10/240 的宽度
    const perDay = daySlots(state.code);
    const slots = pts.map((p) => slotOf(state.code, p[0].slice(-5)));
    let dayIdx = pts.map(() => 0);
    let dayCount = 1;
    if (state.days > 1) {
      const dates = [];
      dayIdx = pts.map((p) => {
        const d = p[0].slice(0, 5);
        let i = dates.indexOf(d);
        if (i < 0) { dates.push(d); i = dates.length - 1; }
        return i;
      });
      dayCount = Math.max(1, dates.length);
    }
    const span = Math.max(1, dayCount * perDay - 1);
    const xAt = (i) => padL + (plotW * (dayIdx[i] * perDay + slots[i])) / span;
    const barW = Math.max(1, Math.min(6, (plotW / span) * 0.7));

    let maxDev = pre * 0.004;
    for (const p of pts) maxDev = Math.max(maxDev, Math.abs(p[1] - pre));
    maxDev *= 1.15;
    const hi = pre + maxDev, lo = pre - maxDev;
    const yP = (v) => padT + priceH * (1 - (v - lo) / (hi - lo));

    ctx.font = "11px ui-monospace, Consolas, monospace";
    ctx.textBaseline = "middle";
    for (let g = 0; g <= 4; g++) {
      const v = hi - ((hi - lo) / 4) * g;
      const y = yP(v);
      ctx.strokeStyle = g === 2 ? C.pre : C.grid;
      ctx.setLineDash(g === 2 ? [5, 4] : []);
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + plotW, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = v === pre ? C.text : (v > pre ? C.up : C.down);
      ctx.fillText(fmt(v), padL + plotW + 6, y);
      const pct = (v / pre - 1) * 100;
      ctx.textAlign = "right";
      ctx.fillText(`${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`, padL + plotW + padR - 6, y);
      ctx.textAlign = "left";
    }

    ctx.textBaseline = "top";
    ctx.fillStyle = C.text;
    if (state.days > 1) {
      for (let d = 0; d < dayCount; d++) {
        const x = padL + (plotW * (d * perDay)) / span;
        const label = (pts[dayIdx.indexOf(d)] || pts[0])[0].slice(0, 5);
        if (x > padL + plotW - 30) continue;
        ctx.fillText(label, Math.max(padL, x + 2), axisY + 6);
      }
    } else {
      for (const label of axisMarks(state.code)) {
        const slot = slotOf(state.code, label);
        const x = padL + (plotW * slot) / span;
        const tx = Math.min(Math.max(x - 16, padL), padL + plotW - 42);
        ctx.fillText(label, tx, axisY + 6);
      }
    }

    ctx.beginPath();
    ctx.moveTo(xAt(0), yP(pts[0][1]));
    for (let i = 1; i < pts.length; i++) ctx.lineTo(xAt(i), yP(pts[i][1]));
    ctx.lineTo(xAt(pts.length - 1), padT + priceH);
    ctx.lineTo(xAt(0), padT + priceH);
    ctx.closePath();
    ctx.fillStyle = C.fill;
    ctx.fill();

    ctx.strokeStyle = C.price; ctx.lineWidth = 1.6; ctx.beginPath();
    for (let i = 0; i < pts.length; i++) {
      const x = xAt(i), y = yP(pts[i][1]);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    ctx.stroke();

    const vols = pts.map((p) => p[2] || 0);
    const cols = pts.map((p, i) => (p[1] >= (i ? pts[i - 1][1] : pre) ? C.up : C.down));
    drawVol(ctx, C, L, vols, cols, xAt, 0, pts.length, barW);
    drawMacd(ctx, C, L, macd(pts.map((p) => p[1])), 0, pts.length, xAt, barW,
             Math.max(pre * 0.001, 0.02));

    if (state.hover >= 0 && state.hover < pts.length) {
      const i = state.hover, p = pts[i], x = xAt(i);
      ctx.strokeStyle = C.label; ctx.globalAlpha = 0.5;
      ctx.setLineDash([3, 3]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, axisY); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(padL, yP(p[1])); ctx.lineTo(padL + plotW, yP(p[1])); ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha = 1;
      const pct = (p[1] / pre - 1) * 100;
      showTip(W, x, `<div class="tip-date">${p[0]}</div>
        <div class="tip-row"><span>价格</span><b>${fmt(p[1])}</b></div>
        <div class="tip-row"><span>涨跌幅</span><b class="${pct >= 0 ? "up" : "down"}">${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%</b></div>`,
              padT + priceH, plotW, padL);
    } else {
      $("idxTip").hidden = true;
    }
  }

  function drawKline(ctx, W, H, C, bars) {
    const L = layout(W, H);
    const { padL, padT, priceH, plotW, axisY } = L;
    const n = bars.length;
    const xAt = (i) => padL + (plotW * (i + 0.5)) / n;
    const step = plotW / n;
    const cw = Math.max(1, Math.min(16, step * 0.62));

    const closes = bars.map((b) => b[4]);
    const m5 = ma(closes, 5), m20 = ma(closes, 20);

    let hi = -Infinity, lo = Infinity;
    for (const b of bars) { hi = Math.max(hi, b[2]); lo = Math.min(lo, b[3]); }
    for (const arr of [m5, m20]) {
      for (const v of arr) { if (v == null) continue; hi = Math.max(hi, v); lo = Math.min(lo, v); }
    }
    const pad = (hi - lo) * 0.06 || 0.1;
    hi += pad; lo -= pad;
    const yP = (v) => padT + priceH * (1 - (v - lo) / (hi - lo));

    ctx.font = "11px ui-monospace, Consolas, monospace";
    ctx.textBaseline = "middle";
    for (let g = 0; g <= 4; g++) {
      const v = hi - ((hi - lo) / 4) * g;
      const y = yP(v);
      ctx.strokeStyle = C.grid;
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + plotW, y); ctx.stroke();
      ctx.fillStyle = C.text;
      ctx.fillText(fmt(v), padL + plotW + 6, y);
    }

    for (let i = 0; i < n; i++) {
      const [, o, h, l, c2] = bars[i];
      const rising = c2 >= o;
      ctx.strokeStyle = ctx.fillStyle = rising ? C.up : C.down;
      const x = xAt(i);
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, yP(h)); ctx.lineTo(x, yP(l)); ctx.stroke();
      const y1 = yP(Math.max(o, c2)), y2 = yP(Math.min(o, c2));
      ctx.fillRect(x - cw / 2, y1, cw, Math.max(1, y2 - y1));
    }

    const line = (arr, color) => {
      ctx.strokeStyle = color; ctx.lineWidth = 1.1; ctx.beginPath();
      let started = false;
      for (let i = 0; i < n; i++) {
        if (arr[i] == null) continue;
        const x = xAt(i), y = yP(arr[i]);
        started ? ctx.lineTo(x, y) : (ctx.moveTo(x, y), started = true);
      }
      ctx.stroke();
    };
    line(m5, C.ma5);
    line(m20, C.ma20);

    ctx.textBaseline = "top";
    ctx.fillStyle = C.text;
    for (const i of [0, Math.floor(n / 3), Math.floor((2 * n) / 3), n - 1]) {
      const label = bars[i][0].slice(2);
      const tx = Math.min(Math.max(xAt(i) - 24, padL), padL + plotW - 56);
      ctx.fillText(label, tx, axisY + 6);
    }

    drawVol(ctx, C, L, bars.map((b) => b[5]),
            bars.map((b) => (b[4] >= b[1] ? C.up : C.down)), xAt, 0, n);
    drawMacd(ctx, C, L, macd(closes), 0, n, xAt);

    if (state.hover >= 0 && state.hover < n) {
      const i = state.hover, b = bars[i], x = xAt(i);
      ctx.strokeStyle = C.label; ctx.globalAlpha = 0.5;
      ctx.setLineDash([3, 3]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, axisY); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(padL, yP(b[4])); ctx.lineTo(padL + plotW, yP(b[4])); ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha = 1;
      const chg = i > 0 ? (b[4] / bars[i - 1][4] - 1) * 100 : 0;
      showTip(W, x, `<div class="tip-date">${b[0]}</div>
        <div class="tip-row"><span>开盘</span><b>${fmt(b[1])}</b></div>
        <div class="tip-row"><span>最高</span><b>${fmt(b[2])}</b></div>
        <div class="tip-row"><span>最低</span><b>${fmt(b[3])}</b></div>
        <div class="tip-row"><span>收盘</span><b>${fmt(b[4])}</b></div>
        <div class="tip-row"><span>涨跌幅</span><b class="${chg >= 0 ? "up" : "down"}">${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%</b></div>
        <div class="tip-row"><span>成交量</span><b>${b[5] ? fmt(b[5] / 1e4, 0) + " 万手" : "—"}</b></div>
        <div class="tip-row"><span>成交额</span><b>${b[6] ? fmt(b[6] / 1e8, 0) + " 亿" : "—"}</b></div>`,
              padT + priceH, plotW, padL);
    } else {
      $("idxTip").hidden = true;
    }
  }

  function showTip(W, x, html, top, plotW, padL) {
    const tip = $("idxTip");
    tip.innerHTML = html;
    tip.hidden = false;
    const cv = $("idxChart");
    const card = cv.closest(".idx-chart-card");
    if (!card) return;
    const cr = cv.getBoundingClientRect();
    const pr = card.getBoundingClientRect();
    const tw = tip.offsetWidth || 140;
    const th = tip.offsetHeight || 80;
    const offX = cr.left - pr.left;
    const offY = cr.top - pr.top;
    const mx = state.mouse ? state.mouse.x : x;
    const my = state.mouse ? state.mouse.y : Math.max(0, top - offY);
    let left = offX + mx + 16;
    let topPx = offY + my + 16;
    if (left + tw > pr.width - 8) left = offX + mx - tw - 16;
    if (topPx + th > pr.height - 8) topPx = offY + my - th - 16;
    tip.style.left = Math.max(8, Math.round(left)) + "px";
    tip.style.top = Math.max(8, Math.round(topPx)) + "px";
  }

  /* ---------------- 交互 */
  function bind() {
    $("idxMode").querySelectorAll("button").forEach((b) => {
      b.addEventListener("click", () => {
        state.mode = b.dataset.mode;
        syncUrl();
        $("idxMode").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
        $("idxDays").hidden = state.mode !== "min";
        $("idxLegend").innerHTML = state.mode === "min"
          ? '<i class="lg-price"></i>价格'
          : '<i class="lg-ma5"></i>MA5<i class="lg-ma20"></i>MA20';
        state.hover = -1;
        load();
      });
    });

    $("idxDays").querySelectorAll("button").forEach((b) => {
      b.addEventListener("click", () => {
        state.days = Number(b.dataset.days);
        syncUrl();
        $("idxDays").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
        load();
      });
    });

    $("idxRefresh").addEventListener("click", () => { loadQuotes(); load(); });

    const cv = $("idxChart");
    cv.addEventListener("mousemove", (e) => {
      const isMin = state.mode === "min";
      const n = isMin ? ((state.data && state.data.points) || []).length
                      : (state.bars || []).length;
      if (!n) return;
      const rect = cv.getBoundingClientRect();
      state.mouse = { x: e.clientX - rect.left, y: e.clientY - rect.top };
      const padL = 8, padR = 104;
      const plotW = rect.width - padL - padR;
      const i = isMin
        ? Math.round(((e.clientX - rect.left - padL) / plotW) * (n - 1))
        : Math.floor(((e.clientX - rect.left - padL) / plotW) * n);
      state.hover = Math.max(0, Math.min(n - 1, i));
      draw();
    });
    cv.addEventListener("mouseleave", () => { state.hover = -1; state.mouse = null; draw(); });

    window.addEventListener("resize", () => draw());
    new MutationObserver(() => draw())
      .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  }

  bind();
  loadMe();
  window.addEventListener("focus", loadMe);
  window.addEventListener("pageshow", (e) => { if (e.persisted) loadMe(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) loadMe(); });
  if (state.mode !== "min") {
    $("idxMode").querySelectorAll("button").forEach((b) =>
      b.classList.toggle("on", b.dataset.mode === state.mode));
    $("idxDays").hidden = true;
    $("idxLegend").innerHTML = '<i class="lg-ma5"></i>MA5<i class="lg-ma20"></i>MA20';
  }
  if (state.days === 5) {
    $("idxDays").querySelectorAll("button").forEach((b) =>
      b.classList.toggle("on", b.dataset.days === "5"));
  }
  syncUrl();
  loadQuotes();
  load();
  setInterval(() => { if (isTrading()) { loadQuotes(); if (state.mode === "min") load(); } }, 30000);
})();
