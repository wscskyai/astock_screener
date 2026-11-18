// 我的收藏 · 独立页（左侧卡片 / 右侧 K 线 + 跟进记录）

const $ = (id) => document.getElementById(id);
const THS = (code) => `https://stockpage.10jqka.com.cn/${code}/`;
const F10 = (code) => `https://basic.10jqka.com.cn/${code}`;

const ICON_COPY = iconSvg("copy", { size: 13 });

const state = {
  user: null,
  favs: [],
  groups: [],
  tab: "all",
  keyword: "",
  sort: "updated",
  selected: null,          // 当前选中的收藏
  detailSym: null,         // 右侧已经加载的股票
  pendingDelete: null,     // 待确认移除的股票
  notesSeq: 0,
  chart: { seq: 0, symbol: null, bars: [], pattern: null, hover: -1 },
};

// ---------------------------------------------------------------- 工具
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const fmt = (n, d = 2) =>
  (n === null || n === undefined || Number.isNaN(n)) ? "—" : Number(n).toFixed(d);

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove("show"), 2200);
}

function relTime(s) {
  if (!s) return "—";
  const t = new Date(String(s).replace(" ", "T"));
  if (Number.isNaN(t.getTime())) return s;
  const mins = Math.floor((Date.now() - t.getTime()) / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  if (mins < 60 * 24) return `${Math.floor(mins / 60)} 小时前`;
  if (mins < 60 * 24 * 30) return `${Math.floor(mins / 60 / 24)} 天前`;
  return String(s).slice(0, 10);
}

async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) { /* fallthrough */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;top:-999px;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (e) { return false; }
}

async function getJson(url) {
  const r = await fetch(url, { cache: "no-store" });
  return r.json();
}

/** 收藏了多久：天数，超过一年按月 / 年显示 */
function holdText(ts) {
  if (!ts) return "";
  const t = new Date(String(ts).replace(" ", "T"));
  if (Number.isNaN(t.getTime())) return "";
  const days = Math.max(0, Math.floor((Date.now() - t.getTime()) / 86400000));
  if (days === 0) return "今天收藏";
  if (days < 31) return `已收藏 ${days} 天`;
  if (days < 365) return `已收藏 ${Math.floor(days / 30)} 个月`;
  return `已收藏 ${(days / 365).toFixed(1)} 年`;
}

async function postJson(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

// ---------------------------------------------------------------- 卡片
function metric(label, value, sub, cls = "") {
  return `<div class="m-cell ${cls}">
    <label>${label}</label>
    <b>${value}${sub ? `<i>${sub}</i>` : ""}</b>
  </div>`;
}

function cardHtml(f) {
  const chgCls = f.chg > 0 ? "pos" : f.chg < 0 ? "neg" : "";
  const peCell = f.pe == null ? "—"
    : f.pe <= 0 ? '<span class="loss">亏损</span>'
    : fmt(f.pe, 1) + (f.pe_pct != null ? `<i>${Math.round(f.pe_pct)}%</i>` : "");
  const pbCell = fmt(f.pb, 2) + (f.pb_pct != null ? `<i>${Math.round(f.pb_pct)}%</i>` : "");
  const dvCell = f.dv == null ? "—"
    : fmt(f.dv, 2) + "%" + (f.dv_pct != null ? `<i>${Math.round(f.dv_pct)}%</i>` : "");

  return `<article class="fav-card ${f.symbol === state.selected ? "selected" : ""}"
                   data-symbol="${f.symbol}" tabindex="0">
    <header class="fc-head">
      <span class="code">${f.symbol}</span>
      <span class="sname">${escapeHtml(f.name)}</span>
      <button class="fc-sync" data-sync="${f.symbol}" title="同步这只票的最新 K 线" aria-label="同步">${iconSvg("refresh", { size: 13 })}</button>
      <button class="copy-btn" data-copy="${f.symbol}" title="复制代码">${ICON_COPY}</button>
      <a class="ext" href="${THS(f.symbol)}" target="_blank" rel="noopener" title="同花顺">${iconSvg("external", { size: 13 })}</a>
      <span class="spacer"></span>
      <button class="fc-del" data-del="${f.symbol}" title="移除收藏" aria-label="移除收藏">${iconSvg("trash", { size: 13 })}</button>
    </header>

    <div class="fc-price">
      <span class="price ${chgCls}">${f.close == null ? "—" : f.close.toFixed(2)}</span>
      <span class="chg ${chgCls}">${f.chg == null ? "" : (f.chg > 0 ? "+" : "") + f.chg.toFixed(2) + "%"}</span>
      <span class="fc-date">${f.date || ""}</span>
      ${f.industry ? `<span class="fc-ind">${escapeHtml(f.industry)}</span>` : ""}
    </div>

    <div class="metric-grid">
      ${metric("市值", f.mktcap == null ? "—" : fmt(f.mktcap, 1) + "亿")}
      ${metric("PE", peCell)}
      ${metric("PB", pbCell)}
      ${metric("股息率", dvCell)}
      ${metric("ROE", f.roe == null ? "—" : fmt(f.roe, 1) + "%")}
      ${metric("净利同比", f.yoy == null ? "—" : fmt(f.yoy, 1) + "%",
    "", f.yoy > 0 ? "pos" : f.yoy < 0 ? "neg" : "")}
      ${metric("户数降幅", f.drop_pct == null ? "—" : fmt(f.drop_pct, 1) + "%")}
      ${metric("营收同比", f.rev_yoy == null ? "—" : fmt(f.rev_yoy, 1) + "%",
    "", f.rev_yoy > 0 ? "pos" : f.rev_yoy < 0 ? "neg" : "")}
      ${metric("报告期", (f.period || "").slice(0, 7) || "—")}
    </div>

    <div class="fc-foot">
      <select class="fav-group" data-gsel="${f.symbol}" title="移动到分组">
        ${state.groups.map((g) => `<option value="${g.id}" ${
          (f.group_id || 0) === g.id ? "selected" : ""}>${escapeHtml(g.name)}</option>`).join("")}
      </select>
      ${f.created_at
        ? `<span class="fc-age" title="加入自选：${f.created_at}">${holdText(f.created_at)}</span>`
        : ""}
      <span class="fc-note-count" data-note-count="${f.symbol}" title="跟进记录条数">跟进 ${f.notes || 0} 条</span>
    </div>
  </article>`;
}

// ---------------------------------------------------------------- 列表渲染
function visibleFavs() {
  let list = state.favs.slice();
  if (state.tab === "ungrouped") list = list.filter((f) => !f.group_id);
  else if (state.tab !== "all") list = list.filter((f) => String(f.group_id) === state.tab);
  if (state.keyword) {
    const kw = state.keyword.toUpperCase();
    list = list.filter((f) => f.symbol.includes(kw) || (f.name || "").toUpperCase().includes(kw));
  }
  const num = (v) => (v == null ? -1e9 : v);
  const sorters = {
    updated: () => 0,
    chg: (a, b) => num(b.chg) - num(a.chg),
    dv: (a, b) => num(b.dv) - num(a.dv),
    pe: (a, b) => (a.pe == null || a.pe <= 0 ? 1e9 : a.pe) - (b.pe == null || b.pe <= 0 ? 1e9 : b.pe),
    pb_pct: (a, b) => num(a.pb_pct) - num(b.pb_pct),
    drop_pct: (a, b) => num(b.drop_pct) - num(a.drop_pct),
  };
  const fn = sorters[state.sort];
  if (fn && state.sort !== "updated") list.sort(fn);
  return list;
}

function renderTabs() {
  const total = state.favs.length;
  const ungrouped = state.favs.filter((f) => !f.group_id).length;
  const tabs = [`<button class="fav-tab ${state.tab === "all" ? "on" : ""}" data-tab="all">
      全部 <em>${total}</em></button>`];
  if (ungrouped) {
    tabs.push(`<button class="fav-tab ${state.tab === "ungrouped" ? "on" : ""}"
      data-tab="ungrouped">未分组 <em>${ungrouped}</em></button>`);
  }
  state.groups.filter((g) => g.id !== 0).forEach((g) => {
    const n = state.favs.filter((f) => f.group_id === g.id).length;
    tabs.push(`<button class="fav-tab ${state.tab === String(g.id) ? "on" : ""}"
      data-tab="${g.id}">${escapeHtml(g.name)} <em>${n}</em></button>`);
  });
  tabs.push(`<button class="fav-tab add" data-newgroup="1" title="新建分组">＋ 分组</button>`);
  $("favTabs").innerHTML = tabs.join("");

  $("favTabs").querySelectorAll("[data-tab]").forEach((b) => {
    b.addEventListener("click", () => { state.tab = b.dataset.tab; render(); });
  });
  const add = $("favTabs").querySelector("[data-newgroup]");
  if (add) add.addEventListener("click", async () => {
    const name = (prompt("新分组名称（如：红利股）") || "").trim();
    if (!name) return;
    const r = await postJson("/api/group", { action: "create", name });
    if (r.error) { toast(r.error); return; }
    await loadFavs();
    toast(`已创建分组「${name}」`);
  });
}

function updateCardSelection() {
  document.querySelectorAll("#favGrid .fav-card").forEach((c) => {
    c.classList.toggle("selected", c.dataset.symbol === state.selected);
  });
}

function render() {
  const list = visibleFavs();
  renderTopbar();
  $("loadingState").hidden = true;
  $("loginHint").hidden = true;
  const hasFavs = state.favs.length > 0;
  $("favLayout").hidden = !hasFavs;
  $("emptyState").hidden = hasFavs;
  renderTabs();
  if (!hasFavs) {
    $("favGrid").innerHTML = "";
    clearDetail();
    return;
  }

  $("favGrid").innerHTML = list.map(cardHtml).join("") ||
    '<div class="fav-empty fav-list-empty"><h3>这个分组还没有股票</h3><p>换个分组看看。</p></div>';

  if (!list.some((f) => f.symbol === state.selected)) {
    state.selected = list.length ? list[0].symbol : null;
  }
  bindCards();
  updateCardSelection();

  if (state.selected) {
    if (state.detailSym !== state.selected) loadDetail(state.selected);
  } else {
    clearDetail();
  }
}

// ---------------------------------------------------------------- 选中 / 右侧详情
function selectFav(sym) {
  if (!sym) return;
  if (state.selected === sym && state.detailSym === sym) return;
  state.selected = sym;
  updateCardSelection();
  loadDetail(sym);
}

function updateDetailHeader(f) {
  $("fdName").textContent = f.name || f.symbol;
  $("fdCode").textContent = f.symbol;
  $("fdPrice").textContent = f.close == null ? "—" : Number(f.close).toFixed(2);
  const cls = f.chg > 0 ? "pos" : f.chg < 0 ? "neg" : "";
  $("fdPrice").className = cls;
  $("fdChg").textContent = f.chg == null ? "" : `${f.chg > 0 ? "+" : ""}${f.chg.toFixed(2)}%`;
  $("fdChg").className = cls;
  $("fdDate").textContent = f.date || "";
  $("fdNotesName").textContent = f.name || "";
  $("fdNotesCount").textContent = `共 ${f.notes || 0} 条`;
  $("fdF10").href = F10(f.symbol);
  $("fdThs").href = THS(f.symbol);
  $("fdLinks").hidden = false;
}

function loadDetail(sym) {
  const f = state.favs.find((x) => x.symbol === sym);
  if (!f) { clearDetail(); return; }
  state.detailSym = sym;
  updateDetailHeader(f);
  $("fdChartInfo").textContent = "近 250 个交易日";
  $("favNoteInput").value = "";
  $("favNoteInput").disabled = false;
  $("favNoteSave").disabled = false;
  loadFavChart(sym);
  loadSelectedNotes(sym);
}

function clearDetail() {
  state.selected = null;
  state.detailSym = null;
  state.chart.seq++;
  state.chart.symbol = null;
  state.chart.bars = [];
  state.chart.pattern = null;
  state.chart.hover = -1;
  updateCardSelection();
  $("fdName").textContent = "—";
  $("fdCode").textContent = "";
  $("fdPrice").textContent = "—";
  $("fdPrice").className = "";
  $("fdChg").textContent = "";
  $("fdChg").className = "";
  $("fdDate").textContent = "";
  $("fdNotesName").textContent = "";
  $("fdNotesCount").textContent = "—";
  $("fdLinks").hidden = true;
  $("fdF10").href = "#";
  $("fdThs").href = "#";
  $("fdChartInfo").textContent = "近 250 个交易日";
  $("favNotes").innerHTML = '<div class="tl-empty">选择左侧收藏查看记录</div>';
  $("favNoteInput").value = "";
  $("favNoteInput").disabled = true;
  $("favNoteSave").disabled = true;
  $("favChartLoading").hidden = true;
  $("favChartEmpty").hidden = true;
  closeFavChartModal();
  const cv = $("favChart");
  if (cv) {
    const ctx = cv.getContext("2d");
    ctx.clearRect(0, 0, cv.width, cv.height);
  }
}

// ---------------------------------------------------------------- 卡片交互
function bindCards() {
  const grid = $("favGrid");

  grid.querySelectorAll(".fav-card").forEach((card) => {
    const sym = card.dataset.symbol;
    card.addEventListener("click", (e) => {
      if (e.target.closest("button, a, select, input, textarea")) return;
      selectFav(sym);
    });
    card.addEventListener("keydown", (e) => {
      if (e.target.closest("button, a, select, input, textarea")) return;
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        selectFav(sym);
      }
    });
  });

  grid.querySelectorAll(".copy-btn").forEach((b) => {
    b.addEventListener("click", async (e) => {
      e.stopPropagation();
      const code = b.dataset.copy;
      if (await copyText(code)) {
        toast(`已复制 ${code}`);
        b.classList.add("copied");
        setTimeout(() => b.classList.remove("copied"), 900);
      } else { toast("复制失败，请手动选中"); }
    });
  });

  grid.querySelectorAll("[data-sync]").forEach((b) => {
    b.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (b.dataset.busy === "1") return;
      const sym = b.dataset.sync;
      b.dataset.busy = "1";
      b.disabled = true;
      b.classList.add("syncing");
      const oldTitle = b.title;
      b.title = "同步中…";
      try {
        const r = await postJson("/api/bars/sync", { symbol: sym });
        if (r.error) return;
        let close = null, chg = null, date = r.latest || "";
        try {
          const k = await getJson(`/api/kline/${encodeURIComponent(sym)}?n=2`);
          const bars = k.bars || [];
          if (bars.length) {
            const last = bars[bars.length - 1];
            close = last[4];
            date = last[0] || date;
            if (bars.length > 1) {
              const prev = bars[bars.length - 2][4];
              if (prev) chg = (close / prev - 1) * 100;
            }
          }
        } catch (err) { /* 同步成功但取最新价失败：保留日期 */ }
        updateFavPrice(sym, close, chg, date);
        if (r.skipped) toast(`刚刚已同步：${r.name} ${r.latest || ""}，使用本地最新`);
        else toast(`已同步 ${r.name}：${r.rows} 根，最新 ${r.latest}`);
      } catch (err) {
        toast("同步失败：" + err.message);
      } finally {
        b.dataset.busy = "";
        b.disabled = false;
        b.classList.remove("syncing");
        b.title = oldTitle;
      }
    });
  });

  grid.querySelectorAll("[data-del]").forEach((b) => {
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      openDeleteConfirm(b.dataset.del);
    });
  });

  grid.querySelectorAll("[data-gsel]").forEach((sel) => {
    sel.addEventListener("change", async () => {
      const sym = sel.dataset.gsel;
      const r = await postJson("/api/group", { action: "assign", symbol: sym, id: Number(sel.value) });
      if (r.error) { toast(r.error); return; }
      const f = state.favs.find((x) => x.symbol === sym);
      if (f) {
        const g = state.groups.find((x) => String(x.id) === sel.value);
        f.group_id = g ? g.id : 0;
        f.group_name = g ? g.name : "未分组";
      }
      renderTabs();
      if ((state.tab === "ungrouped" && f && f.group_id) ||
          (state.tab !== "all" && state.tab !== "ungrouped" && f &&
           String(f.group_id) !== state.tab)) {
        render();
      }
      toast("已移动分组");
    });
  });
}

// ---------------------------------------------------------------- 移除收藏确认
function openDeleteConfirm(sym) {
  const f = state.favs.find((x) => x.symbol === sym);
  if (!f) return;
  state.pendingDelete = sym;
  $("deleteCode").textContent = f.symbol;
  $("deleteName").textContent = f.name || "";
  const n = Number(f.notes) || 0;
  const notes = $("deleteNotes");
  if (n > 0) {
    notes.className = "confirm-notes warn";
    notes.textContent = `将同时删除 ${n} 条跟进记录，删除后不可恢复。`;
  } else {
    notes.className = "confirm-notes";
    notes.textContent = "该股票没有跟进记录，直接移除即可。";
  }
  const ok = $("deleteOk");
  ok.disabled = false;
  ok.textContent = "确认移除";
  $("deleteModal").hidden = false;
  requestAnimationFrame(() => $("deleteCancel").focus());
}

function closeDeleteConfirm() {
  if ($("deleteModal").hidden) return;
  $("deleteModal").hidden = true;
  state.pendingDelete = null;
  const ok = $("deleteOk");
  ok.disabled = false;
  ok.textContent = "确认移除";
}

async function confirmDelete() {
  const sym = state.pendingDelete;
  if (!sym) return;
  const f = state.favs.find((x) => x.symbol === sym);
  const fallbackNotes = Number(f && f.notes) || 0;
  const btn = $("deleteOk");
  btn.disabled = true;
  btn.textContent = "移除中…";
  const r = await postJson("/api/fav/delete", {
    symbol: sym,
    delete_notes: true,
  });
  if (r.error) {
    btn.disabled = false;
    btn.textContent = "确认移除";
    toast(r.error);
    return;
  }
  const removed = Number.isFinite(Number(r.removed_notes))
    ? Number(r.removed_notes) : fallbackNotes;
  closeDeleteConfirm();
  state.favs = state.favs.filter((x) => x.symbol !== sym);
  render();
  toast(removed
    ? `已移除 ${f ? f.name : sym}，同时删除 ${removed} 条跟进记录`
    : `已移除 ${f ? f.name : sym}`);
}

/** 同步完成后只改当前卡片的价格/涨跌幅/日期，不重绘整页 */
function updateFavPrice(sym, close, chg, date) {
  const f = state.favs.find((x) => x.symbol === sym);
  if (f) {
    if (close != null) { f.close = close; f.chg = chg; }
    if (date) f.date = date;
  }
  const card = $("favGrid").querySelector(`.fav-card[data-symbol="${sym}"]`);
  if (card && close != null) {
    const cls = chg == null ? "" : chg > 0 ? "pos" : chg < 0 ? "neg" : "";
    const price = card.querySelector(".fc-price .price");
    const change = card.querySelector(".fc-price .chg");
    if (price) {
      price.textContent = Number(close).toFixed(2);
      price.className = "price " + cls;
    }
    if (change) {
      change.textContent = chg == null ? "" : `${chg > 0 ? "+" : ""}${chg.toFixed(2)}%`;
      change.className = "chg " + cls;
    }
  }
  if (card && date) {
    const day = card.querySelector(".fc-date");
    if (day) day.textContent = date;
  }
  if (state.selected === sym && f) {
    updateDetailHeader(f);
    loadFavChart(sym);
  }
}

// ---------------------------------------------------------------- 右侧跟进记录
function updateCardNoteCount(sym, n) {
  const f = state.favs.find((x) => x.symbol === sym);
  if (f) f.notes = n;
  const el = $("favGrid").querySelector(`.fav-card[data-symbol="${sym}"] [data-note-count]`);
  if (el) el.textContent = `跟进 ${n} 条`;
}

function renderNotes(sym, notes) {
  const box = $("favNotes");
  box.innerHTML = notes.length ? notes.map((n) => `
    <div class="tl-item">
      <div class="tl-head">
        <span class="tl-time" title="${n.created_at}">${relTime(n.created_at)}</span>
        <button class="tl-del" data-delnote="${n.id}" data-sym="${sym}" title="删除这条">${iconSvg("x", { size: 12 })}</button>
      </div>
      <div class="tl-text">${escapeHtml(n.note)}</div>
    </div>`).join("")
    : '<div class="tl-empty">暂无记录，写一条吧</div>';
  $("fdNotesCount").textContent = `共 ${notes.length} 条`;
  updateCardNoteCount(sym, notes.length);
  box.querySelectorAll("[data-delnote]").forEach((b) => {
    b.addEventListener("click", async (e) => {
      e.stopPropagation();
      await postJson("/api/note/delete", { id: Number(b.dataset.delnote) });
      loadSelectedNotes(b.dataset.sym || sym);
      toast("已删除");
    });
  });
}

async function loadSelectedNotes(sym) {
  const seq = ++state.notesSeq;
  const box = $("favNotes");
  box.innerHTML = '<div class="tl-empty">加载中…</div>';
  let d;
  try {
    d = await getJson(`/api/notes?symbol=${encodeURIComponent(sym)}`);
  } catch (e) {
    if (seq !== state.notesSeq) return;
    box.innerHTML = '<div class="tl-empty">加载失败，切换一下再试</div>';
    return;
  }
  if (seq !== state.notesSeq || state.selected !== sym) return;
  if (d.error) {
    box.innerHTML = `<div class="tl-empty">${escapeHtml(d.error)}</div>`;
    return;
  }
  renderNotes(sym, d.notes || []);
}

async function saveSelectedNote() {
  const sym = state.selected;
  if (!sym) return;
  const ta = $("favNoteInput");
  const note = (ta.value || "").trim();
  if (!note) { toast("写点什么再记录"); ta.focus(); return; }
  const r = await postJson("/api/note", { symbol: sym, note });
  if (r.error) { toast(r.error); return; }
  ta.value = "";
  toast("已记录");
  loadSelectedNotes(sym);
}

// ---------------------------------------------------------------- 右侧 K 线
const favBigChart = new KlineChart($("favChartBig"), { macd: true });

function loadFavChart(sym) {
  const seq = ++state.chart.seq;
  state.chart.symbol = sym;
  state.chart.bars = [];
  state.chart.pattern = null;
  state.chart.hover = -1;
  $("favChartLoading").hidden = false;
  $("favChartEmpty").hidden = true;
  const cv = $("favChart");
  if (cv) {
    const ctx = cv.getContext("2d");
    ctx.clearRect(0, 0, cv.width, cv.height);
  }
  getJson(`/api/kline/${encodeURIComponent(sym)}?n=500`).then((d) => {
    if (seq !== state.chart.seq || state.selected !== sym) return;
    if (d.error) {
      $("favChartLoading").hidden = true;
      $("favChartEmpty").hidden = false;
      return;
    }
    state.chart.bars = d.bars || [];
    state.chart.pattern = d.pattern || null;
    $("favChartLoading").hidden = true;
    $("favChartEmpty").hidden = state.chart.bars.length > 0;
    if (state.chart.bars.length) {
      $("fdChartInfo").textContent = "近 250 个交易日";
      drawFavChart();
    }
  }).catch(() => {
    if (seq !== state.chart.seq) return;
    $("favChartLoading").hidden = true;
    $("favChartEmpty").hidden = false;
  });
}

function drawFavChart() {
  const bars = state.chart.bars;
  if (!bars.length) return;
  const i0 = Math.max(0, bars.length - 250);
  paintKline($("favChart"), bars, {
    i0, i1: bars.length, hover: state.chart.hover, macd: false,
    mark: patternMark(bars, state.chart.pattern),
  });
}

function bindChartHover() {
  const cv = $("favChart");
  if (!cv) return;
  cv.addEventListener("mousemove", (e) => {
    const bars = state.chart.bars;
    if (!bars.length) return;
    const rect = cv.getBoundingClientRect();
    const padL = 6, padR = 58;
    const plotW = rect.width - padL - padR;
    if (plotW <= 0) return;
    const i0 = Math.max(0, bars.length - 250);
    const visible = Math.max(1, bars.length - i0);
    const step = plotW / visible;
    const i = Math.round((e.clientX - rect.left - padL) / Math.max(1, step) - 0.5) + i0;
    const clamped = Math.max(i0, Math.min(bars.length - 1, i));
    state.chart.hover = clamped;
    const b = bars[clamped];
    const prev = clamped > 0 ? bars[clamped - 1][4] : b[4];
    const chg = prev ? (b[4] / prev - 1) * 100 : 0;
    $("fdChartInfo").textContent =
      `${b[0]} 开${fmt(b[1])} 高${fmt(b[2])} 低${fmt(b[3])} 收${fmt(b[4])} ${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%`;
    drawFavChart();
  });
  cv.addEventListener("mouseleave", () => {
    state.chart.hover = -1;
    if (state.chart.bars.length) $("fdChartInfo").textContent = "近 250 个交易日";
    drawFavChart();
  });
}

function openFavChartModal() {
  const f = state.favs.find((x) => x.symbol === state.selected);
  if (!f) return;
  if (state.chart.symbol !== state.selected || !state.chart.bars.length) {
    toast("K 线还在加载，稍等一下");
    return;
  }
  $("fcmName").textContent = f.name || f.symbol;
  $("fcmCode").textContent = f.symbol;
  $("fcmPrice").textContent = f.close == null ? "—" : Number(f.close).toFixed(2);
  const cls = f.chg > 0 ? "pos" : f.chg < 0 ? "neg" : "";
  $("fcmPrice").className = cls;
  $("fcmChg").textContent = f.chg == null ? "" : `${f.chg > 0 ? "+" : ""}${f.chg.toFixed(2)}%`;
  $("fcmChg").className = cls;
  $("fcmDate").textContent = f.date || "";
  $("fcmF10").href = F10(f.symbol);
  $("fcmThs").href = THS(f.symbol);
  $("fcmInfo").textContent = "滚轮缩放 · 拖动平移 · 双击复位";
  $("favChartBigEmpty").hidden = true;
  $("favChartModal").hidden = false;
  requestAnimationFrame(() => {
    favBigChart.setData(state.chart.bars, {
      mark: patternMark(state.chart.bars, state.chart.pattern),
      more: false,
    });
    $("favChartClose").focus();
  });
}

function closeFavChartModal() {
  if ($("favChartModal").hidden) return;
  $("favChartModal").hidden = true;
}

// ---------------------------------------------------------------- 顶栏 / 数据
/** 顶栏高度包含分类工具栏；右侧吸顶位置跟着它走，避免两层 sticky 抖动 */
function syncFavTopH() {
  const bar = document.querySelector(".fav-topbar");
  if (!bar) return;
  const h = bar.offsetHeight;
  document.documentElement.style.setProperty("--fav-bar-h", h + "px");
  document.documentElement.style.setProperty("--fav-top-h", (h + 8) + "px");
}

function renderTopbar() {
  const u = state.user;
  $("favCount").textContent = u ? String(state.favs.length) : "";
  $("userLabel").textContent = u ? (u.nickname || u.username) : "登录 / 注册";
  const btn = $("userBtn");
  btn.classList.toggle("on", !!u);
  btn.href = u ? "/account" : "/screen?login=1";
  btn.title = u ? "打开个人中心" : "去登录";
}

async function loadFavs() {
  $("loadingState").hidden = false;
  $("emptyState").hidden = true;
  $("loginHint").hidden = true;
  $("favLayout").hidden = true;
  let d;
  try {
    d = await getJson("/api/favs");
  } catch (e) {
    $("loadingState").hidden = true;
    $("emptyState").hidden = false;
    $("emptyState").innerHTML =
      '<h3>数据加载失败</h3><p>网络或服务异常，请刷新重试。</p>';
    return;
  }
  if (d.error) {
    $("loadingState").hidden = true;
    $("loginHint").hidden = false;
    $("favLayout").hidden = true;
    $("emptyState").hidden = true;
    $("favCount").textContent = "";
    return;
  }
  state.favs = d.favorites || [];
  render();
}

async function boot() {
  $("loadingState").hidden = false;
  const me = await getJson("/api/me");
  state.user = me.user;
  state.groups = me.groups || [];
  renderTopbar();
  if (!state.user) {
    $("loadingState").hidden = true;
    $("loginHint").hidden = false;
    $("favTabs").innerHTML = "";
    document.querySelector(".fav-toolbar").hidden = true;
    return;
  }
  await loadFavs();
}

$("favSearch").addEventListener("input", (e) => { state.keyword = e.target.value.trim(); render(); });
$("favSort").addEventListener("change", (e) => { state.sort = e.target.value; render(); });
$("favNoteSave").addEventListener("click", saveSelectedNote);
$("favNoteInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    saveSelectedNote();
  }
});
$("deleteCancel").addEventListener("click", closeDeleteConfirm);
$("deleteOk").addEventListener("click", confirmDelete);
$("deleteModal").addEventListener("click", (e) => {
  if (e.target === $("deleteModal")) closeDeleteConfirm();
});
$("favChartZoom").addEventListener("click", (e) => {
  e.stopPropagation();
  openFavChartModal();
});
$("favChart").addEventListener("click", openFavChartModal);
$("favChartClose").addEventListener("click", closeFavChartModal);
$("favChartReset").addEventListener("click", () => favBigChart.reset());
$("favChartModal").addEventListener("click", (e) => {
  if (e.target === $("favChartModal")) closeFavChartModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("favChartModal").hidden) { closeFavChartModal(); return; }
  if (!$("deleteModal").hidden) closeDeleteConfirm();
});
window.addEventListener("resize", () => {
  if (state.chart.bars.length) {
    drawFavChart();
    if (!$("favChartModal").hidden) favBigChart.render();
  }
});
window.addEventListener("resize", syncFavTopH);
if (window.ResizeObserver) {
  const bar = document.querySelector(".fav-topbar");
  if (bar) new ResizeObserver(syncFavTopH).observe(bar);
}
syncFavTopH();
new MutationObserver(() => {
  if (state.chart.bars.length) {
    drawFavChart();
    if (!$("favChartModal").hidden) favBigChart.render();
  }
}).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

bindChartHover();
boot();
