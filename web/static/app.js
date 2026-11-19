// A 股选股系统 · 前端交互（无框架、无外链）

const $ = (id) => document.getElementById(id);
const THS = (code) => `https://stockpage.10jqka.com.cn/${code}/`;
const F10 = (code) => `https://basic.10jqka.com.cn/${code}`;

const ICON_COPY = iconSvg("copy", { size: 13 });

/** 复制到剪贴板：优先 Clipboard API，失败退回 execCommand */
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) { /* 继续走兜底 */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:-999px;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (e) {
    return false;
  }
}

const state = {
  rows: [],          // 当前页结果（服务端分页）
  total: 0,          // 命中总数
  offset: 0,
  sortKey: "drop_pct",
  sortAsc: false,    // 户数降幅默认从大到小
  page: 1,
  pageSize: 50,
  active: null,
  user: null,
  favs: [],
  favSet: new Set(),
  groups: [],
  favFilter: "all",
  conds: [
    { k: "drop_pct", min: 20 },
    { k: "float_mktcap", min: 30, max: 300 },
    { k: "exclude_st", on: true },
  ],
  fields: [],
  industries: [],
  periods: [{ k: "latest", label: "最新一期" }],
  years: [],
  holderYears: [],
  top10Years: [],
  screens: [],
  activeScreen: null,
  lastConds: null,    // 当前表格结果实际使用的条件（运行成功后快照）
  cols: {},           // 列口径切换：{yoy: "np_yoy"}
  site: null,         // 站点配置（公开字段 + 管理员完整字段）
  resultTab: "stocks",   // stocks=个股列表 / screen=筛选结果
  stockRows: [], stockTotal: 0, stockPage: 1, stockPageSize: 50,
  favRows: [], favTotal: 0, favLoaded: false, favMeta: "",
  favSortKey: "", favSortAsc: true,
  stockLoaded: false,
  stockSortKey: "pct", stockSortAsc: false, stockIndustry: "",
  stockDate: null, stockMeta: "",
  screenMeta: "",
};

function effVal(r, key) {
  const alt = state.cols[key];
  return alt ? r[alt] : r[key];
}

// ---------------------------------------------------------------- 条件行
const FALLBACK_LABELS = {
  pe: "市盈率(TTM)", pb: "市净率", dv: "股息率",
  pe_pct: "PE 分位", pb_pct: "PB 分位", dv_pct: "股息分位",
  drop_pct: "户数三季降幅", profit_yi: "近三季净利", roe: "ROE(年化)",
  top10_ratio: "前十大股东持股占比",
  yoy: "净利同比", rev_yoy: "营收同比", gross_margin: "毛利率",
  net_profit: "单期净利", mktcap: "总市值", years: "上市年数", exclude_st: "排除 ST",
};

function fieldOf(k) {
  const hit = state.fields.find((f) => f.k === k);
  if (hit) return hit;
  return { k, label: FALLBACK_LABELS[k] || k, unit: "", cat: "其他", dec: 1,
           bool: k === "exclude_st" };
}

function optionsHtml(selected) {
  const groups = {};
  state.fields.forEach((f) => { (groups[f.cat] = groups[f.cat] || []).push(f); });
  // 分类顺序：K线形态排最前，其余按固定顺序，未知分类追加在后面
  const ORDER = ["K线形态", "估值", "财务", "筹码", "行情", "其他"];
  const cats = Object.keys(groups).sort((a, b) => {
    const ia = ORDER.indexOf(a), ib = ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  if (!cats.length) {
    return `<option value="">（指标加载中…）</option>`;
  }
  return `<option value="">选择指标…</option>` + cats.map((cat) => `
    <optgroup label="${cat}">
      ${groups[cat].map((f) =>
        `<option value="${f.k}" ${f.k === selected ? "selected" : ""}>${f.label}</option>`
      ).join("")}
    </optgroup>`).join("");
}

function condRowHtml(c, i) {
  const f = c.k ? fieldOf(c.k) : null;
  let body = "";
  if (!f) {
    body = "";
  } else if (f.special === "limit_days") {
    const ev = c.ev || "up";
    const days = c.days ?? 10;
    const cnt = c.cnt ?? 2;
    const pct = c.pct ?? 5;
    const evOpts = [
      ["up", "涨停"], ["down", "跌停"], ["any", "涨停或跌停"],
      ["chg_up", "涨幅 ≥"], ["chg_down", "跌幅 ≥"],
    ];
    const pctPart = (ev === "chg_up" || ev === "chg_down")
      ? `<span class="cond-group"><input type="number" class="cond-num cond-num-sm" data-lpct="${i}" value="${pct}" min="0.1" max="30" step="0.1">
         <span class="cond-unit">%</span></span>`
      : "";
    body = `<span class="cond-group"><span class="cond-text">近</span>
        <input type="number" class="cond-num cond-num-sm" data-ldays="${i}" value="${days}" min="1" max="120">
        <span class="cond-unit">天</span></span>
      <span class="cond-group">
        <select class="cond-period" data-lev="${i}">
          ${evOpts.map(([k, label]) =>
            `<option value="${k}" ${ev === k ? "selected" : ""}>${label}</option>`).join("")}
        </select>${pctPart}
      </span>
      <span class="cond-group"><span class="cond-text">至少</span>
        <input type="number" class="cond-num cond-num-sm" data-lcnt="${i}" value="${cnt}" min="1" max="120">
        <span class="cond-unit">次</span></span>
      <label class="cond-check"><input type="checkbox" data-chk="${i}" ${c.on !== false ? "checked" : ""}> 启用</label>`;
  } else if (f.choice) {
    const cur = c.p || "";
    const opts = state.industries.length
      ? state.industries
      : [{ k: cur, label: cur || "（行业加载中…）" }];
    body = `<select class="cond-period" data-op="${i}">
        <option value="in" ${c.op !== "not" ? "selected" : ""}>属于</option>
        <option value="not" ${c.op === "not" ? "selected" : ""}>不属于</option>
      </select>
      <select class="cond-period" data-ind="${i}">
        ${opts.map((o) => `<option value="${o.k}" ${cur === o.k ? "selected" : ""}>${o.label}${o.n ? `（${o.n}）` : ""}</option>`).join("")}
      </select>`;
  } else if (f.bool) {
    let params = "";
    if (f.options && f.options.length) {
      const cur = c.p || f.options[0].k;
      params += `<select class="cond-period" data-per="${i}">
        ${f.options.map((o) => `<option value="${o.k}" ${cur === o.k ? "selected" : ""}>${o.label}</option>`).join("")}
      </select>`;
    }
    if (f.daysOptions && f.daysOptions.length) {
      const cur = c.d || f.daysDefault || f.daysOptions[0].k;
      params += `<select class="cond-period" data-days="${i}">
        ${f.daysOptions.map((o) => `<option value="${o.k}" ${cur === o.k ? "selected" : ""}>${o.label}</option>`).join("")}
      </select>`;
    }
    if (f.posOptions && f.posOptions.length) {
      const cur = c.lp || f.posDefault || f.posOptions[0].k;
      params += `<select class="cond-period" data-pos="${i}">
        ${f.posOptions.map((o) => `<option value="${o.k}" ${cur === o.k ? "selected" : ""}>${o.label}</option>`).join("")}
      </select>`;
    }
    body = `${params}<label class="cond-check"><input type="checkbox" data-chk="${i}" ${c.on ? "checked" : ""}> 启用</label>`;
  } else {
    const opts = f.period ? state.periods : (f.options || null);
    const cur = c.p || (opts && opts.length ? opts[0].k : "");
    const per = opts
      ? `<select class="cond-period" data-per="${i}">
           ${opts.map((p) =>
             `<option value="${p.k}" ${cur === p.k ? "selected" : ""}>${p.label}</option>`).join("")}
         </select>`
      : "";
    const years = f.yearSource === "holder" ? state.holderYears
      : f.yearSource === "top10" ? state.top10Years : state.years;
    const yearSel = ((f.period || f.options) && cur !== "latest" && years.length)
      ? `<select class="cond-period" data-year="${i}" title="报告年份">
           <option value="" ${c.y ? "" : "selected"}>最新</option>
           ${years.map((y) =>
             `<option value="${y}" ${Number(c.y) === y ? "selected" : ""}>${y} 年</option>`).join("")}
         </select>`
      : "";
    body = `${per}${yearSel}
      <input type="number" class="cond-num" data-min="${i}" value="${c.min ?? ""}" placeholder="不限">
      <span class="tilde">~</span>
      <input type="number" class="cond-num" data-max="${i}" value="${c.max ?? ""}" placeholder="不限">
      <span class="cond-unit">${f.unit || ""}</span>`;
  }
  return `<div class="cond-row" data-i="${i}">
    <button class="cond-del" title="删除条件" data-del="${i}">${iconSvg("minus", { size: 14 })}</button>
    <select class="cond-sel" data-sel="${i}">${optionsHtml(c.k)}</select>
    ${body}
  </div>`;
}

function renderConds(focusIndex = -1) {
  $("condRows").innerHTML = state.conds.map(condRowHtml).join("") ||
    '<div class="cond-empty">还没有条件：点下方「添加条件」，或从右侧「快捷查询」载入一组。</div>';

  const rows = $("condRows");
  rows.querySelectorAll("[data-del]").forEach((b) => {
    b.addEventListener("click", () => {
      state.conds.splice(Number(b.dataset.del), 1);
      renderConds();
    });
  });
  rows.querySelectorAll("[data-sel]").forEach((sel) => {
    sel.addEventListener("change", () => {
      const i = Number(sel.dataset.sel);
      const k = sel.value;
      if (!k) { state.conds[i] = { k: "" }; renderConds(); return; }
      // 同一个指标可以加多次（换报告期/区间后就是不同条件），不做去重
      const f = fieldOf(k);
    if (f.special === "limit_days") {
      state.conds[i] = { k, ev: "up", days: 10, cnt: 2, pct: 5, on: true };
    } else if (f.choice) {
      state.conds[i] = { k, p: (state.industries[0] || {}).k || "", op: "in" };
    } else if (f.bool) {
      const c = { k, on: true };
      if (f.options && f.options.length) c.p = f.options[0].k;
      if (f.daysOptions && f.daysOptions.length) c.d = f.daysDefault || f.daysOptions[0].k;
      if (f.posOptions && f.posOptions.length) c.lp = f.posDefault || f.posOptions[0].k;
      state.conds[i] = c;
    } else {
      state.conds[i] = { k };
    }
      renderConds();
    });
  });
  rows.querySelectorAll("[data-per]").forEach((sel) => {
    sel.addEventListener("change", () => {
      const i = Number(sel.dataset.per);
      const c = state.conds[i];
      if (sel.value === "latest") { delete c.p; delete c.y; } else { c.p = sel.value; }
      renderConds();           // 重新渲染以显示/隐藏年份下拉
    });
  });
  rows.querySelectorAll("[data-ind]").forEach((sel) => {
    sel.addEventListener("change", () => { state.conds[Number(sel.dataset.ind)].p = sel.value; });
  });
  rows.querySelectorAll("[data-op]").forEach((sel) => {
    sel.addEventListener("change", () => { state.conds[Number(sel.dataset.op)].op = sel.value; });
  });
  rows.querySelectorAll("[data-pos]").forEach((sel) => {
    sel.addEventListener("change", () => {
      state.conds[Number(sel.dataset.pos)].lp = sel.value;
    });
  });
  rows.querySelectorAll("[data-days]").forEach((sel) => {
    sel.addEventListener("change", () => {
      const c = state.conds[Number(sel.dataset.days)];
      c.d = sel.value;
    });
  });
  rows.querySelectorAll("[data-year]").forEach((sel) => {
    sel.addEventListener("change", () => {
      const c = state.conds[Number(sel.dataset.year)];
      if (sel.value === "") delete c.y; else c.y = Number(sel.value);
    });
  });
  rows.querySelectorAll("[data-min]").forEach((inp) => {
    inp.addEventListener("input", () => {
      const c = state.conds[Number(inp.dataset.min)];
      const v = inp.value.trim();
      if (v === "") delete c.min; else c.min = Number(v);
    });
  });
  rows.querySelectorAll("[data-max]").forEach((inp) => {
    inp.addEventListener("input", () => {
      const c = state.conds[Number(inp.dataset.max)];
      const v = inp.value.trim();
      if (v === "") delete c.max; else c.max = Number(v);
    });
  });
  rows.querySelectorAll("[data-chk]").forEach((box) => {
    box.addEventListener("change", () => {
      state.conds[Number(box.dataset.chk)].on = box.checked;
    });
  });
  rows.querySelectorAll("[data-ldays]").forEach((inp) => {
    inp.addEventListener("input", () => {
      const c = state.conds[Number(inp.dataset.ldays)];
      const v = Math.round(Number(inp.value));
      if (Number.isFinite(v)) c.days = Math.max(1, Math.min(120, v));
    });
  });
  rows.querySelectorAll("[data-lcnt]").forEach((inp) => {
    inp.addEventListener("input", () => {
      const c = state.conds[Number(inp.dataset.lcnt)];
      const v = Math.round(Number(inp.value));
      if (Number.isFinite(v)) c.cnt = Math.max(1, Math.min(120, v));
    });
  });
  rows.querySelectorAll("[data-lpct]").forEach((inp) => {
    inp.addEventListener("input", () => {
      const c = state.conds[Number(inp.dataset.lpct)];
      const v = Number(inp.value);
      if (Number.isFinite(v)) c.pct = Math.max(0.1, Math.min(30, v));
    });
  });
  rows.querySelectorAll("[data-lev]").forEach((sel) => {
    sel.addEventListener("change", () => {
      const c = state.conds[Number(sel.dataset.lev)];
      c.ev = sel.value;
      renderConds();          // 切换「涨幅/跌幅」时显示或隐藏百分比输入
    });
  });

  if (focusIndex >= 0) {
    const sel = rows.querySelector(`[data-sel="${focusIndex}"]`);
    if (sel) sel.focus();
  }
}

function addCondition() {
  state.conds.push({ k: "" });
  renderConds(state.conds.length - 1);
}

// ---------------------------------------------------------------- 保存的查询
/** 把一组条件拆成可读的条目，例如「户数三季降幅 ≥20%」 */
function condParts(conds) {
  const list = (conds || []).filter((c) => c && c.k);
  return list.map((c) => {
    if (c.k === "limit_days") {
      const ev = {
        up: "涨停", down: "跌停", any: "涨停或跌停",
        chg_up: `涨幅≥${c.pct ?? 5}%`, chg_down: `跌幅≥${c.pct ?? 5}%`,
      }[c.ev] || "涨停";
      return `近${c.days ?? 10}天 ${ev} 至少${c.cnt ?? 2}次${c.on === false ? "（未启用）" : ""}`;
    }
    const f = fieldOf(c.k);
    if (f.bool) return `${f.label}${c.on === false ? "（未启用）" : ""}`;
    const unit = f.unit || "";
    let rng = "已设置";
    if (c.min != null && c.max != null) rng = `${c.min}~${c.max}${unit}`;
    else if (c.min != null) rng = `≥${c.min}${unit}`;
    else if (c.max != null) rng = `≤${c.max}${unit}`;
    let period = "";
    if (f.k === "top10_ratio") {
      const opts = f.options || state.periods;
      const p = c.p || "latest";
      const lab = p === "latest" ? "当季"
        : (opts.find((o) => o.k === p)?.label || p);
      period = c.y ? `${c.y}年${lab}` : lab;
    }
    return `${f.label}${period ? `（${period}）` : ""} ${rng}`;
  });
}

/** 把一组条件压成一行摘要，给右侧「快捷查询」列表用 */
function condSummary(conds) {
  const parts = condParts(conds);
  return parts.length ? parts.join(" · ") : "空条件";
}

function renderPresets() {
  const box = $("presetBar");
  if (!state.screens.length) {
    box.innerHTML = `<div class="preset-empty">还没有保存的查询。<br>左边调好条件后点「保存查询」，就会出现在这里。</div>`;
    $("presetMeta").textContent = "点击载入";
    return;
  }
  $("presetMeta").textContent = `${state.screens.length} 组 · 点击载入`;
  box.innerHTML = state.screens.map((s) => `
    <div class="preset-item ${state.activeScreen === s.id ? "on" : ""}" data-load="${s.id}"
         role="button" tabindex="0" title="载入「${escapeHtml(s.name)}」">
      <div class="preset-item-main">
        <span class="preset-name">${escapeHtml(s.name)}</span>
        <span class="preset-desc">${escapeHtml(condSummary(s.conds))}</span>
      </div>
      <button class="preset-x" data-drop="${s.id}" title="删除这组查询">${iconSvg("x", { size: 12 })}</button>
    </div>`).join("");

  const load = (el) => {
    const s = state.screens.find((x) => x.id === Number(el.dataset.load));
    if (!s) return;
    state.conds = JSON.parse(JSON.stringify(s.conds || []));
    state.activeScreen = s.id;
    renderConds();
    renderPresets();
    toast(`已载入「${s.name}」，点击「筛选」运行`);
  };
  box.querySelectorAll("[data-load]").forEach((el) => {
    el.addEventListener("click", (e) => {
      if (e.target.closest("[data-drop]")) return;
      load(el);
    });
    el.addEventListener("keydown", (e) => {
      if (e.target.closest("[data-drop]")) return;
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); load(el); }
    });
  });
  box.querySelectorAll("[data-drop]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const s = state.screens.find((x) => x.id === Number(btn.dataset.drop));
      if (!s || !confirm(`删除查询「${s.name}」？`)) return;
      await postJson("/api/screens/delete", { id: s.id });
      state.screens = state.screens.filter((x) => x.id !== s.id);
      if (state.activeScreen === s.id) state.activeScreen = null;
      renderPresets();
      toast(`已删除「${s.name}」`);
    });
  });
}

async function loadScreens() {
  try {
    state.screens = (await (await fetch("/api/screens", { cache: "no-store" })).json()).screens || [];
  } catch (e) {
    state.screens = [];
  }
  renderPresets();
}

async function saveCurrent() {
  const conds = state.conds.filter((c) => c.k);
  if (!conds.length) { toast("先加一个条件再保存"); return; }
  const name = (prompt("给这组条件起个名字（如：红利股）") || "").trim();
  if (!name) return;
  const d = await postJson("/api/screens", { name, conds });
  if (d.error) { toast("保存失败：" + d.error); return; }
  state.screens = d.screens || [];
  state.activeScreen = d.id;
  renderPresets();
  toast(`已保存「${d.name}」`);
}

// ---------------------------------------------------------------- 请求
function currentParams() {
  const p = new URLSearchParams();
  p.set("conds", JSON.stringify(state.conds.filter((c) => c.k)));
  // 服务端分页：pageSize=0 表示「全部」，仍走一次接口拿全量
  p.set("limit", String(state.pageSize || 0));
  p.set("offset", String(Math.max(0, (state.page - 1) * (state.pageSize || 0))));
  if (state.sortKey) {
    p.set("sort", state.sortKey);
    p.set("dir", state.sortAsc ? "asc" : "desc");
  }
  return p;
}

/** 筛选结果标题下方：展示本次表格结果实际使用的条件 */
function renderResultConds() {
  const box = $("resultConds");
  if (!box) return;
  if (state.lastConds == null) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  const parts = condParts(state.lastConds);
  box.innerHTML = `<span class="rc-label">筛选条件</span>` +
    (parts.length
      ? parts.map((p) => `<span class="rc-chip">${escapeHtml(p)}</span>`).join("")
      : `<span class="rc-empty">无（全部股票）</span>`);
  box.hidden = false;
}

// ---------------------------------------------------------------- 结果标签 / 个股列表
function updateResultTabUI() {
  const isStocks = state.resultTab === "stocks";
  const isFav = state.resultTab === "favorites";
  $("stockTools").hidden = !isStocks;
  $("stockTableWrap").hidden = !isStocks;
  $("favTableWrap").hidden = !isFav;
  $("screenTableWrap").hidden = isStocks || isFav;
  document.querySelectorAll("#resultTabs .result-tab").forEach((b) => {
    const on = b.dataset.rtab === state.resultTab;
    b.classList.toggle("on", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  $("resultConds").hidden = isStocks || isFav || state.lastConds == null;
  $("pageSize").value = String(isStocks ? state.stockPageSize : state.pageSize);
  $("resultMeta").textContent = isStocks
    ? (state.stockMeta || "加载中…")
    : isFav
      ? (state.favMeta || "点击查看我的收藏")
      : (state.screenMeta || "尚未运行，点击「筛选」");
}

function setResultTab(tab) {
  state.resultTab = tab === "screen" ? "screen" : tab === "favorites" ? "favorites" : "stocks";
  updateResultTabUI();
  if (state.resultTab === "stocks") {
    if (!state.stockLoaded) loadStockList();
    else renderStockTable();
  } else if (state.resultTab === "favorites") {
    if (!state.favLoaded) loadFavList();
    else renderFavTable();
  } else {
    renderTable();
    renderResultConds();
  }
}

function renderActiveTable() {
  if (state.resultTab === "stocks") renderStockTable();
  else if (state.resultTab === "favorites") renderFavTable();
  else renderTable();
}

function stockSortLabel() {
  if (state.stockSortKey === "pct") return state.stockSortAsc ? "跌幅榜" : "涨幅榜";
  const names = { volume: "成交量", turnover: "换手率", mktcap: "市值",
    float_mktcap: "流通市值", close: "现价", pe: "PE", pb: "PB",
    symbol: "代码", name: "名称", industry: "行业" };
  return `${names[state.stockSortKey] || state.stockSortKey}${state.stockSortAsc ? "升序" : "降序"}`;
}

function renderStockIndustryOptions() {
  const sel = $("stockIndustry");
  if (!sel) return;
  const cur = state.stockIndustry;
  sel.innerHTML = `<option value="">全部行业</option>` +
    state.industries.map((x) =>
      `<option value="${escapeHtml(x.k)}">${escapeHtml(x.label || x.k)}${x.n ? `（${x.n}）` : ""}</option>`
    ).join("");
  sel.value = cur;
}

function syncStockSortUI() {
  document.querySelectorAll("#stockSort .sort-chip").forEach((chip) => {
    chip.classList.toggle("on",
      chip.dataset.sort === state.stockSortKey &&
      (chip.dataset.dir === "asc") === state.stockSortAsc);
  });
  document.querySelectorAll("#stockTable thead th[data-stock-key]").forEach((th) => {
    const on = th.dataset.stockKey === state.stockSortKey;
    th.classList.toggle("sorted", on);
    th.classList.toggle("asc", on && state.stockSortAsc);
  });
}

let stockReq = 0;

async function loadStockList() {
  const seq = ++stockReq;
  const tb = $("stockTbody");
  tb.innerHTML = '<tr class="empty"><td colspan="11">加载中…</td></tr>';
  if (state.resultTab === "stocks") $("resultMeta").textContent = "加载中…";
  try {
    const p = new URLSearchParams();
    if (state.stockIndustry) p.set("industry", state.stockIndustry);
    p.set("sort", state.stockSortKey);
    p.set("dir", state.stockSortAsc ? "asc" : "desc");
    p.set("limit", String(state.stockPageSize || 0));
    p.set("offset", String((state.stockPage - 1) * (state.stockPageSize || 0)));
    const d = await (await fetch("/api/stock_list?" + p.toString(), { cache: "no-store" })).json();
    if (seq !== stockReq) return;
    if (d.error) throw new Error(d.error);
    state.stockRows = d.rows || [];
    state.stockTotal = d.count || 0;
    state.stockDate = d.date || null;
    state.stockLoaded = true;
    const datePart = d.date ? `${d.date} 收盘` : "最新交易日收盘";
    state.stockMeta = `共 ${d.count} 只 · ${datePart} · ${stockSortLabel()} · ${state.stockIndustry || "全部行业"}`;
    renderStockTable();
    if (state.resultTab === "stocks") $("resultMeta").textContent = state.stockMeta;
  } catch (e) {
    if (seq !== stockReq) return;
    state.stockLoaded = true;
    state.stockRows = [];
    state.stockTotal = 0;
    tb.innerHTML = '<tr class="empty"><td colspan="11">个股列表加载失败，请刷新重试</td></tr>';
    setPager(false, false);
    $("pageInfo").textContent = "—";
    $("pageNum").textContent = "1 / 1";
    if (state.resultTab === "stocks") $("resultMeta").textContent = "加载失败";
    toast("个股列表加载失败：" + e.message);
  }
}

function renderStockTable() {
  const tb = $("stockTbody");
  if (!state.stockLoaded) {
    tb.innerHTML = '<tr class="empty"><td colspan="11">正在加载个股列表…</td></tr>';
    return;
  }
  if (!state.stockRows.length) {
    tb.innerHTML = `<tr class="empty"><td colspan="11">${state.stockIndustry ? "该行业暂无个股数据" : "暂无个股数据"}</td></tr>`;
    $("pageInfo").textContent = "共 0 只";
    $("pageNum").textContent = "1 / 1";
    setPager(false, false);
    return;
  }
  tb.innerHTML = state.stockRows.map((r) => {
    const pct = r.pct;
    const pctText = pct == null ? "—" : `${pct > 0 ? "+" : ""}${fmt(pct, 2)}%`;
    return `<tr data-symbol="${r.symbol}" class="${r.symbol === state.active ? "active" : ""}">
      <td class="fav-col">
        <button class="star-btn ${state.favSet.has(r.symbol) ? "on" : ""}"
                data-fav="${r.symbol}" title="${state.favSet.has(r.symbol) ? "取消收藏" : "加入收藏"}">${iconSvg("star", { size: 15, filled: state.favSet.has(r.symbol) })}</button>
      </td>
      <td class="stock">
        <span class="code">${r.symbol}</span>
        <span class="sname">${escapeHtml(r.name)}</span>
        <button class="copy-btn" data-copy="${r.symbol}" data-copy-name="${escapeHtml(r.name)}"
                title="复制代码到剪贴板">${ICON_COPY}</button>
        <a class="ext" href="${THS(r.symbol)}" target="_blank" rel="noopener"
           title="在同花顺查看 ${escapeHtml(r.name)}">${iconSvg("external", { size: 13 })}</a>
      </td>
      <td class="num">${fmt(r.close, 2)}</td>
      <td class="num ${pct > 0 ? "pos" : pct < 0 ? "neg" : ""}">${pctText}</td>
      <td class="num">${fmt(r.volume, 2)}</td>
      <td class="num">${fmt(r.turnover, 2)}%</td>
      <td class="num">${fmt(r.float_mktcap, 1)}</td>
      <td class="num">${fmt(r.mktcap, 1)}</td>
      <td class="num">${r.pe == null ? "—"
        : r.pe <= 0 ? '<span class="loss">亏损</span>' : fmt(r.pe, 1)}</td>
      <td class="num">${fmt(r.pb, 2)}</td>
      <td class="ind" title="${escapeHtml(r.industry || "")}">${escapeHtml(r.industry || "—")}</td>
    </tr>`;
  }).join("");
  bindRowActions(tb);
  const pages = state.stockPageSize ? Math.max(1, Math.ceil((state.stockTotal || 0) / state.stockPageSize)) : 1;
  state.stockPage = Math.min(Math.max(1, state.stockPage), pages);
  renderPagerFor(state.stockPage, state.stockTotal, state.stockPageSize);
  syncStockSortUI();
}

let favReq = 0;

async function loadFavList() {
  const seq = ++favReq;
  const tb = $("favTbody");
  tb.innerHTML = '<tr class="empty"><td colspan="13">加载中…</td></tr>';
  if (state.resultTab === "favorites") $("resultMeta").textContent = "加载中…";
  try {
    const d = await (await fetch("/api/favorites_list", { cache: "no-store" })).json();
    if (seq !== favReq) return;
    if (d.error) throw new Error(d.error);
    state.favRows = d.rows || [];
    state.favTotal = d.count || 0;
    state.favLoaded = true;
    const datePart = d.date ? `${d.date} 收盘` : "最新交易日收盘";
    state.favMeta = `共 ${d.count} 只收藏 · ${datePart}`;
    renderFavTable();
    if (state.resultTab === "favorites") $("resultMeta").textContent = state.favMeta;
  } catch (e) {
    if (seq !== favReq) return;
    state.favLoaded = true;
    state.favRows = [];
    state.favTotal = 0;
    tb.innerHTML = '<tr class="empty"><td colspan="13">收藏加载失败，请刷新重试</td></tr>';
    $("pageInfo").textContent = "—";
    $("pageNum").textContent = "1 / 1";
    setPager(false, false);
    if (state.resultTab === "favorites") $("resultMeta").textContent = "加载失败";
    toast("收藏加载失败：" + e.message);
  }
}

function favSortedRows() {
  const k = state.favSortKey;
  if (!k) return state.favRows;
  const arr = [...state.favRows].sort((a, b) => {
    const x = a[k], y = b[k];
    const r = (typeof x === "string" || typeof y === "string")
      ? String(x ?? "").localeCompare(String(y ?? ""), "zh")
      : (x ?? -1e9) - (y ?? -1e9);
    return state.favSortAsc ? r : -r;
  });
  return arr;
}

function renderFavTable() {
  const tb = $("favTbody");
  if (!state.favLoaded) {
    tb.innerHTML = '<tr class="empty"><td colspan="13">正在加载收藏…</td></tr>';
    return;
  }
  if (!state.favRows.length) {
    tb.innerHTML = '<tr class="empty"><td colspan="13">还没有收藏。在列表里点 ★ 就能加进来</td></tr>';
    $("pageInfo").textContent = "共 0 只";
    $("pageNum").textContent = "1 / 1";
    setPager(false, false);
    return;
  }
  tb.innerHTML = favSortedRows().map((r) => {
    const pct = r.pct;
    const pctText = pct == null ? "—" : `${pct > 0 ? "+" : ""}${fmt(pct, 2)}%`;
    return `<tr data-symbol="${r.symbol}" class="${r.symbol === state.active ? "active" : ""}">
      <td class="fav-col">
        <button class="star-btn on" data-fav="${r.symbol}" title="取消收藏">${iconSvg("star", { size: 15, filled: true })}</button>
      </td>
      <td class="stock">
        <span class="code">${r.symbol}</span>
        <span class="sname">${escapeHtml(r.name)}</span>
        <button class="copy-btn" data-copy="${r.symbol}" data-copy-name="${escapeHtml(r.name)}"
                title="复制代码到剪贴板">${ICON_COPY}</button>
        <a class="ext" href="${THS(r.symbol)}" target="_blank" rel="noopener"
           title="在同花顺查看 ${escapeHtml(r.name)}">${iconSvg("external", { size: 13 })}</a>
      </td>
      <td class="num">${fmt(r.close, 2)}</td>
      <td class="num ${pct > 0 ? "pos" : pct < 0 ? "neg" : ""}">${pctText}</td>
      <td class="num">${fmt(r.volume, 2)}</td>
      <td class="num">${fmt(r.turnover, 2)}%</td>
      <td class="num">${fmt(r.float_mktcap, 1)}</td>
      <td class="num">${fmt(r.mktcap, 1)}</td>
      <td class="num">${r.pe == null ? "—"
        : r.pe <= 0 ? '<span class="loss">亏损</span>' : fmt(r.pe, 1)}</td>
      <td class="num">${fmt(r.pb, 2)}</td>
      <td class="ind" title="${escapeHtml(r.industry || "")}">${escapeHtml(r.industry || "—")}</td>
      <td class="ind">${escapeHtml(r.group_name || "未分组")}</td>
      <td class="num">${r.notes ? `${r.notes} 条` : "—"}</td>
    </tr>`;
  }).join("");
  bindRowActions(tb);
  $("pageInfo").textContent = `共 ${state.favTotal} 只`;
  $("pageNum").textContent = "1 / 1";
  setPager(false, false);
}

function renderPagerFor(page, total, size = state.pageSize) {
  const pages = size ? Math.max(1, Math.ceil((total || 0) / size)) : 1;
  page = Math.min(Math.max(1, page), pages);
  const from = total ? (page - 1) * (size || total) + 1 : 0;
  const to = size ? Math.min(total, page * size) : total;
  $("pageInfo").textContent = size && total > size
    ? `共 ${total} 只 · 第 ${from}~${to} 条` : `共 ${total} 只`;
  $("pageNum").textContent = `${page} / ${pages}`;
  setPager(page > 1, page < pages);
  return pages;
}

function activePages() {
  const isStocks = state.resultTab === "stocks";
  const total = isStocks ? state.stockTotal : state.total;
  const size = isStocks ? state.stockPageSize : state.pageSize;
  return size ? Math.max(1, Math.ceil((total || 0) / size)) : 1;
}

function bindRowActions(tb) {
  tb.querySelectorAll("tr[data-symbol]").forEach((tr) => {
    tr.addEventListener("click", (e) => {
      if (e.target.closest("a.ext")) return;
      if (e.target.closest(".star-btn")) return;
      if (e.target.closest(".copy-btn")) return;
      selectStock(tr.dataset.symbol);
    });
  });
  tb.querySelectorAll(".copy-btn").forEach((b) => {
    b.addEventListener("click", async (e) => {
      e.stopPropagation();
      const code = b.dataset.copy;
      const ok = await copyText(code);
      if (ok) {
        toast(`已复制 ${code}`);
        b.classList.add("copied");
        setTimeout(() => b.classList.remove("copied"), 900);
      } else {
        toast("复制失败，请手动选中");
      }
    });
  });
  tb.querySelectorAll(".star-btn").forEach((b) => {
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleFav(b.dataset.fav);
    });
  });
}

async function runScreen(keepPage = false) {
  const btn = $("runBtn");
  state.resultTab = "screen";
  updateResultTabUI();
  btn.disabled = true;
  btn.textContent = "筛选中…";
  $("resultMeta").textContent = "查询中…";
  try {
    if (!keepPage) state.page = 1;
    const res = await fetch("/api/screen?" + currentParams().toString());
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    state.rows = data.rows;
    state.total = data.count;
    state.offset = data.offset || 0;
    state.active = null;
    state.lastConds = JSON.parse(JSON.stringify(state.conds.filter((c) => c.k)));
    state.cols = data.cols || {};       // 必须先设口径，再渲染表格
    applyColumnLabels();
    renderTable();
    renderResultConds();
    const perHint = (data.periods || []).map((x) => `${x.label}=${x.ptext}`).join("，");
    state.screenMeta =
      `命中 ${data.count} 只 · 用时 ${data.elapsed}s` + (perHint ? ` · ${perHint}口径` : "");
    if (state.resultTab === "screen") $("resultMeta").textContent = state.screenMeta;
    $("csvBtn").disabled = data.count === 0;
  } catch (e) {
    state.screenMeta = "查询失败";
    if (state.resultTab === "screen") $("resultMeta").textContent = state.screenMeta;
    toast("查询失败：" + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "筛选";
  }
}

// ---------------------------------------------------------------- 表格
const fmt = (n, d = 2) =>
  (n === null || n === undefined || Number.isNaN(n)) ? "—" : Number(n).toFixed(d);

/** 净利那一列跟随条件口径改名 */
function applyColumnLabels() {
  const th = document.querySelector('thead th[data-key="yoy"]');
  if (th) {
    th.textContent = state.cols.yoy === "np_yoy" ? "净利同比(单季)"
      : state.cols.yoy === "np_qoq" ? "净利环比(单季)" : "净利同比";
  }
  const thDrop = document.querySelector('thead th[data-key="drop_pct"]');
  if (thDrop) {
    thDrop.textContent = state.cols.drop_pct === "top10_ratio" ? "十大占比" : "户数降幅";
  }
}

function sortedRows() {
  const k = state.sortKey;
  return [...state.rows].sort((a, b) => {
    const x = effVal(a, k), y = effVal(b, k);
    const r = (typeof x === "string" || typeof y === "string")
      ? String(x).localeCompare(String(y))
      : (x ?? -1e9) - (y ?? -1e9);
    return state.sortAsc ? r : -r;
  });
}

function totalPages() {
  return state.pageSize ? Math.max(1, Math.ceil((state.total || 0) / state.pageSize)) : 1;
}

function renderTable() {
  const tb = $("tbody");
  if (state.lastConds == null) {
    tb.innerHTML = '<tr class="empty"><td colspan="14">设置条件后点击「筛选」</td></tr>';
    $("pageInfo").textContent = "—";
    $("pageNum").textContent = "1 / 1";
    setPager(false, false);
    return;
  }
  const rows = sortedRows();

  if (!rows.length) {
    state.total = 0;
    tb.innerHTML = '<tr class="empty"><td colspan="14">没有符合条件的股票，试试放宽条件</td></tr>';
    $("pageInfo").textContent = "—";
    $("pageNum").textContent = "1 / 1";
    setPager(false, false);
    return;
  }

  const pages = totalPages();
  state.page = Math.min(Math.max(1, state.page), pages);
  const pageRows = rows;      // 服务端已经分好页

  tb.innerHTML = pageRows.map((r) => `
    <tr data-symbol="${r.symbol}" class="${r.symbol === state.active ? "active" : ""}">
      <td class="fav-col">
        <button class="star-btn ${state.favSet.has(r.symbol) ? "on" : ""}"
                data-fav="${r.symbol}" title="${state.favSet.has(r.symbol) ? "取消收藏" : "加入收藏"}">${iconSvg("star", { size: 15, filled: state.favSet.has(r.symbol) })}</button>
      </td>
      <td class="stock">
        <span class="code">${r.symbol}</span>
        <span class="sname">${r.name}</span>
        <button class="copy-btn" data-copy="${r.symbol}" data-copy-name="${escapeHtml(r.name)}"
                title="复制代码到剪贴板">${ICON_COPY}</button>
        <a class="ext" href="${THS(r.symbol)}" target="_blank" rel="noopener"
           title="在同花顺查看 ${r.name}">${iconSvg("external", { size: 13 })}</a>
      </td>
      <td class="num ${state.cols.drop_pct !== "top10_ratio" && r.drop_pct > 0 ? "neg" : state.cols.drop_pct !== "top10_ratio" && r.drop_pct < 0 ? "pos" : ""}">${fmt(effVal(r, "drop_pct"))}%</td>
      <td class="num">${fmt(r.profit_yi, 2)}</td>
      <td class="num">${fmt(r.mktcap, 1)}</td>
      <td class="num muted" title="流通市值">${fmt(r.float_mktcap, 1)}</td>
      <td class="num">${r.pe == null ? "—"
        : r.pe <= 0 ? '<span class="loss">亏损</span>'
        : fmt(r.pe, 1) + (r.pe_pct != null
          ? `<span class="pct" title="近 5 年 PE 分位">${Math.round(r.pe_pct)}%</span>` : "")}</td>
      <td class="num">${fmt(r.pb, 2)}${r.pb_pct != null
        ? `<span class="pct">${Math.round(r.pb_pct)}%</span>` : ""}</td>
      <td class="num">${r.dv == null ? "—" : fmt(r.dv, 2) + "%"}${r.dv_pct != null
        ? `<span class="pct">${Math.round(r.dv_pct)}%</span>` : ""}</td>
      <td class="num">${fmt(r.roe_annual, 1)}%</td>
      <td class="num ${effVal(r, "yoy") > 0 ? "pos" : effVal(r, "yoy") < 0 ? "neg" : ""}">${fmt(effVal(r, "yoy"), 1)}%</td>
      <td class="num ${r.rev_yoy > 0 ? "pos" : r.rev_yoy < 0 ? "neg" : ""}">${fmt(r.rev_yoy, 1)}%</td>
      <td class="num">${fmt(r.gross_margin, 1)}%</td>
      <td class="code">${(r.period || "").slice(0, 7)}</td>
    </tr>`).join("");

  bindRowActions(tb);
  renderPagerFor(state.page, state.total);
}

function setPager(prevOn, nextOn) {
  $("prevBtn").disabled = !prevOn;
  $("firstBtn").disabled = !prevOn;
  $("nextBtn").disabled = !nextOn;
  $("lastBtn").disabled = !nextOn;
}

function activePage() {
  if (state.resultTab === "favorites") return 1;
  return state.resultTab === "stocks" ? state.stockPage : state.page;
}

function scrollResultTop() {
  const wrap = state.resultTab === "stocks" ? $("stockTableWrap")
    : state.resultTab === "favorites" ? $("favTableWrap") : $("screenTableWrap");
  if (wrap) wrap.scrollTop = 0;
}

function goto(p) {
  if (state.resultTab === "favorites") return;      // 收藏列表一页显示完
  const pages = activePages();
  if (p < 1 || p > pages || p === activePage()) return;
  if (state.resultTab === "stocks") {
    state.stockPage = p;
    loadStockList().then(scrollResultTop);
  } else {
    if (state.lastConds == null) return;
    state.page = p;
    runScreen(true).then(scrollResultTop);
  }
}

document.querySelectorAll("thead th[data-key]").forEach((th) => {
  th.addEventListener("click", () => {
    if (state.resultTab !== "screen") return;
    if (state.lastConds == null) { toast("先点击「筛选」生成结果"); return; }
    const k = th.dataset.key;
    if (state.sortKey === k) state.sortAsc = !state.sortAsc;
    else { state.sortKey = k; state.sortAsc = k === "symbol"; }
    state.page = 1;
    runScreen(true);
  });
});

document.querySelectorAll("#favTable thead th[data-fav-key]").forEach((th) => {
  th.addEventListener("click", () => {
    const k = th.dataset.favKey;
    if (state.favSortKey === k) state.favSortAsc = !state.favSortAsc;
    else { state.favSortKey = k; state.favSortAsc = k === "symbol"; }
    renderFavTable();
  });
});

document.querySelectorAll("#stockTable thead th[data-stock-key]").forEach((th) => {
  th.addEventListener("click", () => {
    const k = th.dataset.stockKey;
    if (state.stockSortKey === k) state.stockSortAsc = !state.stockSortAsc;
    else { state.stockSortKey = k; state.stockSortAsc = k === "symbol"; }
    state.stockPage = 1;
    syncStockSortUI();
    loadStockList();
  });
});

document.querySelectorAll("#stockSort .sort-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    state.stockSortKey = chip.dataset.sort;
    state.stockSortAsc = chip.dataset.dir === "asc";
    state.stockPage = 1;
    syncStockSortUI();
    loadStockList();
  });
});

$("stockIndustry").addEventListener("change", (e) => {
  state.stockIndustry = e.target.value;
  state.stockPage = 1;
  loadStockList();
});

document.querySelectorAll("#resultTabs .result-tab").forEach((btn) => {
  btn.addEventListener("click", () => setResultTab(btn.dataset.rtab));
});

$("pageSize").addEventListener("change", (e) => {
  const size = Number(e.target.value);
  if (state.resultTab === "favorites") { updateResultTabUI(); return; }
  if (state.resultTab === "stocks") {
    state.stockPageSize = size;
    state.stockPage = 1;
    loadStockList();
  } else {
    state.pageSize = size;
    state.page = 1;
    if (state.lastConds != null) runScreen(true);
    else updateResultTabUI();
  }
});
$("prevBtn").addEventListener("click", () => goto(activePage() - 1));
$("nextBtn").addEventListener("click", () => goto(activePage() + 1));
$("firstBtn").addEventListener("click", () => goto(1));
$("lastBtn").addEventListener("click", () => goto(activePages()));

// ---------------------------------------------------------------- 个股
async function selectStock(code) {
  state.active = code;
  renderActiveTable();
  // 让走势面板的搜索框始终反映当前正在看的股票
  if ($("stockSearch")) { $("stockSearch").value = code; syncStockClear(); }
  ssClose();
  $("detailTitle").textContent = "加载中…";
  try {
    const res = await fetch(`/api/kline/${code}?n=160`);
    const d = await res.json();
    if (d.error) throw new Error(d.error);
    $("detailTitle").textContent = `${d.name} ${d.symbol}`;
    $("detailMeta").textContent =
      `${d.board} · 总市值 ${d.mktcap ? d.mktcap.toFixed(1) + " 亿" : "—"}` +
      `${d.float_mktcap ? "（流通 " + d.float_mktcap.toFixed(1) + " 亿）" : ""}` +
      ` · PE ${d.pe ?? "—"} · PB ${d.pb ?? "—"} · 股息 ${d.dv != null ? d.dv + "%" : "—"}` +
      (d.val_date && d.bars && d.bars.length && d.val_date !== d.bars[d.bars.length - 1][0]
        ? `（估值截至 ${String(d.val_date).slice(5)}）` : "");
    const link = $("thsLink");
    link.href = THS(code);
    link.style.display = "";
    const f10 = $("f10Link");
    f10.href = F10(code);
    f10.style.display = "";
    state.chartPattern = d.pattern || null;
    drawChart(d.bars, d.pattern);          // 形态标记（旗形区间）
    state.chartBars = d.bars;              // 切换主题时重画用
    $("chartZoom").hidden = false;
  } catch (e) {
    $("detailTitle").textContent = "加载失败";
    toast("K 线加载失败：" + e.message);
  }
}

// 绘制函数已移至 /static/kline-chart.js（选股页/收藏页共用）

/** 右侧小图：固定显示最近 160 根 */
function drawChart(bars, pattern) {
  paintKline($("chart"), bars, { i0: 0, i1: bars.length, mark: patternMark(bars, pattern) });
}

// ---------------------------------------------------------------- 放大弹窗
const cmState = { bars: null, i0: 0, i1: 0, drag: null, hover: null,
                   more: false, loading: false, code: null, total: 0, pattern: null };

function cmVisible() { return cmState.i1 - cmState.i0; }

function cmRender() {
  const cv = $("chartBig");
  if (!cv || !cmState.bars) return;
  paintKline(cv, cmState.bars, {
    i0: cmState.i0, i1: cmState.i1, hover: cmState.hover, macd: true,
    mark: patternMark(cmState.bars, cmState.pattern),
  });
}

function cmReset() {
  const n = cmState.bars ? cmState.bars.length : 0;
  cmState.i1 = n;
  cmState.i0 = Math.max(0, n - Math.min(n, 250));
  cmState.hover = null;
  cmRender();
}

function cmZoomAt(clientX, factor) {
  if (!cmState.bars) return;
  const cv = $("chartBig");
  const rect = cv.getBoundingClientRect();
  const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
  const span = cmVisible();
  const newSpan = Math.max(20, Math.min(cmState.bars.length, Math.round(span * factor)));
  if (newSpan === span) return;
  const ratio = x / Math.max(1, rect.width);
  const anchor = cmState.i0 + span * ratio;
  let i0 = Math.round(anchor - newSpan * ratio);
  i0 = Math.max(0, Math.min(cmState.bars.length - newSpan, i0));
  cmState.i0 = i0;
  cmState.i1 = i0 + newSpan;
  cmState.hover = null;
  cmRender();
}

async function openChartModal() {
  const code = state.active;
  if (!code) return;
  $("cmName").textContent = $("detailTitle").textContent || code;
  $("cmMeta").textContent = $("detailMeta").textContent || "";
  $("cmThs").href = THS(code);
  $("cmF10").href = F10(code);
  $("chartModal").hidden = false;
  document.body.classList.add("modal-open");
  cmState.code = code;
  cmState.more = false;
  cmState.loading = false;
  try {
    const res = await fetch(`/api/kline/${code}?n=500`);
    const d = await res.json();
    if (d.error) throw new Error(d.error);
    cmState.bars = d.bars;
    cmState.pattern = d.pattern || null;
    cmState.more = !!d.more;
    cmState.hover = null;
    cmReset();
    cmUpdateHint();
    cmUpdateNav();
    cmFavRefresh();
  } catch (e) {
    toast("K 线加载失败：" + e.message);
    closeChartModal();
  }
}

/** 同步当前这只票的 K 线，然后重新加载弹窗 */
async function cmSyncBars() {
  const code = cmState.code || state.active;
  if (!code) return;
  const btn = $("cmSync");
  if (btn.dataset.busy === "1") return;
  btn.dataset.busy = "1";
  const oldText = btn.innerHTML;
  btn.innerHTML = "同步中…";
  try {
    const r = await postJson("/api/bars/sync", { symbol: code });
    if (r.error) throw new Error(r.error);
    if (r.skipped) {
      toast(`刚刚已同步：${r.name} ${r.latest || ""}，使用本地最新`);
    } else {
      toast(`已同步 ${r.name}：${r.rows} 根，最新 ${r.latest}` +
        (r.fixed ? "（检测到除权，已整只重建）" : ""));
    }
    const res = await fetch(`/api/kline/${code}?n=500`);
    const d = await res.json();
    if (d.error) throw new Error(d.error);
    cmState.bars = d.bars;
    cmState.pattern = d.pattern || null;
    cmState.more = !!d.more;
    cmState.hover = null;
    cmReset();
    cmUpdateHint();
    cmUpdateNav();
  } catch (e) {
    toast("同步失败：" + e.message);
  } finally {
    btn.dataset.busy = "";
    btn.innerHTML = oldText;
  }
}

/** 当前结果列表（sortedRows 是表格里正在显示的顺序） */
function cmList() {
  try {
    const rows = state.resultTab === "stocks"
      ? (state.stockRows || [])
      : state.resultTab === "favorites"
        ? (state.favRows || [])
        : (typeof sortedRows === "function" ? sortedRows() : state.rows);
    return rows || [];
  } catch (e) {
    return state.rows || [];
  }
}

function cmUpdateNav() {
  const list = cmList();
  const i = list.findIndex((r) => r.symbol === state.active);
  const prev = $("cmPrev"), next = $("cmNext"), counter = $("cmCounter");
  if (!prev) return;
  counter.textContent = i >= 0 ? `${i + 1} / ${list.length}`
    : (list.length ? "不在当前列表" : "—");
  prev.disabled = i <= 0;
  next.disabled = i < 0 || i >= list.length - 1;
}

/** 上下切换结果列表里的股票（dir = -1 / +1） */
let cmSwitching = false;
async function cmNav(dir) {
  if (cmSwitching) return;            // 连点保护
  const list = cmList();
  const i = list.findIndex((r) => r.symbol === state.active);
  if (i < 0) return;
  const j = i + dir;
  if (j < 0 || j >= list.length) return;
  const target = list[j];
  cmSwitching = true;
  $("cmPrev").disabled = true;
  $("cmNext").disabled = true;
  try {
    await selectStock(target.symbol);   // 同步右侧面板 + 表格高亮
    await openChartModal();             // 弹窗换成新股票，视图复位
  } finally {
    cmSwitching = false;
    cmUpdateNav();                      // 恢复按钮状态
  }
}

/** 向左拉到边缘时，继续加载更早的日线 */
async function cmLoadOlder() {
  if (!cmState.more || cmState.loading || !cmState.bars || !cmState.bars.length) return;
  cmState.loading = true;
  cmUpdateHint();
  const before = cmState.bars[0][0];
  try {
    const res = await fetch(`/api/kline/${cmState.code}?n=500&before=${encodeURIComponent(before)}`);
    const d = await res.json();
    if (d.error) throw new Error(d.error);
    if (d.bars.length) {
      cmState.bars = d.bars.concat(cmState.bars);
      cmState.i0 += d.bars.length;      // 视图位置跟着右移，画面不跳
      cmState.i1 += d.bars.length;
    }
    cmState.more = !!d.more;
    cmRender();
    cmUpdateHint();
  } catch (e) {
    toast("加载更早数据失败：" + e.message);
  } finally {
    cmState.loading = false;
  }
}

/** 同步弹窗里收藏按钮的状态 */
function cmFavRefresh() {
  const code = state.active;
  const btn = $("cmFav"), text = $("cmFavText");
  if (!btn) return;
  const on = !!(code && state.favSet && state.favSet.has(code));
  btn.classList.toggle("on", on);
  if (text) text.textContent = on ? "已收藏" : "收藏";
  btn.title = state.user ? (on ? "已收藏，点击可改分类/加笔记" : "收藏（可选分类、写笔记）")
                         : "登录后可收藏";
}

let cmFavGroupId = 0;

/** 画分类 chips（未分组 / 自建分组 / 新建） */
function cmRenderFavChips() {
  const box = $("cfpChips");
  if (!box) return;
  // state.groups 里已含 id=0 的「未分组」，不要再叠加一份
  const extra = (state.groups || []).filter((g) => g.id !== 0);
  const list = [{ id: 0, name: "未分组" }].concat(extra);
  box.innerHTML = list.map((g) =>
    `<button type="button" class="cfp-chip${g.id === cmFavGroupId ? " on" : ""}" data-gid="${g.id}">${g.name}</button>`
  ).join("") + `<button type="button" class="cfp-chip add" data-gid="new">＋ 新建</button>`;
  box.querySelectorAll(".cfp-chip").forEach((el) => {
    el.addEventListener("click", async () => {
      const v = el.dataset.gid;
      if (v !== "new") { cmFavGroupId = Number(v); cmRenderFavChips(); return; }
      const name = (prompt("新分组名称") || "").trim();
      if (!name) return;
      const d = await postJson("/api/group", { action: "create", name });
      if (d.error) return;
      await loadMe();
      cmFavGroupId = d.id || (state.groups.find((g) => g.name === name) || {}).id || 0;
      cmRenderFavChips();
    });
  });
}

/** 打开收藏浮层：选分类 + 写笔记 */
function cmOpenFav() {
  if (!state.user) { toast("请先登录再收藏"); openAuth("login"); return; }
  const code = state.active;
  if (!code) return;
  const cur = (state.favs || []).find((f) => f.symbol === code);
  const row = (state.rows || []).find((r) => r.symbol === code) ||
              (state.stockRows || []).find((r) => r.symbol === code);
  cmFavGroupId = cur ? (cur.group_id || 0) : 0;
  $("cfpName").textContent = (row && row.name) || (cur && cur.name) || code;
  $("cfpCode").textContent = code;
  cmRenderFavChips();
  $("cfpNote").value = "";
  $("cfpCount").textContent = "0";
  $("cfpNote").placeholder = cur
    ? "本次追加一条笔记（留空则只改分类）"
    : "记一笔：为什么关注、打算什么价位买、风险点是什么";
  $("cfpRemove").hidden = !cur;
  $("cfpSave").textContent = cur ? "保存修改" : "添加收藏";
  $("cmFavPop").hidden = false;
  $("cfpNote").focus();
}

function cmCloseFav() { $("cmFavPop").hidden = true; }

/** 保存：未收藏则新增；已收藏则改分类 + 追加笔记 */
async function cmSaveFav() {
  const code = state.active;
  if (!code) return;
  const gid = cmFavGroupId || 0;
  const note = $("cfpNote").value.trim();
  const cur = (state.favs || []).find((f) => f.symbol === code);
  const btn = $("cfpSave");
  btn.disabled = true;
  try {
    if (!cur) {
      const d = await postJson("/api/fav", { symbol: code, group_id: gid || null });
      if (d.error) return;
    } else if ((cur.group_id || 0) !== gid) {
      const d = await postJson("/api/group", { action: "assign", symbol: code, id: gid });
      if (d.error) return;
    }
    if (note) {
      const d = await postJson("/api/note", { symbol: code, note });
      if (d.error) return;
    }
    await loadMe();
    cmFavRefresh();
    cmCloseFav();
    toast(note ? `已收藏 ${code}，笔记已记录`
               : (cur ? `已更新 ${code} 的分类` : `已收藏 ${code}`));
  } finally {
    btn.disabled = false;
  }
}

async function cmRemoveFav() {
  const code = state.active;
  if (!code) return;
  const d = await postJson("/api/fav/delete", { symbol: code });
  if (d.error) return;
  await loadMe();
  cmFavRefresh();
  cmCloseFav();
  toast(`已移除 ${code}`);
}

function cmUpdateHint() {
  const el = $("cmHint");
  if (!el) return;
  if (cmState.loading) { el.textContent = "正在加载更早数据…"; return; }
  el.textContent = cmState.more
    ? "滚轮缩放 · 拖动平移 · 双击复位 · 向左拖动加载更早"
    : "滚轮缩放 · 拖动平移 · 双击复位 · 已是全部历史";
}

function closeChartModal() {
  $("chartModal").hidden = true;
  if ($("cmFavPop")) $("cmFavPop").hidden = true;
  document.body.classList.remove("modal-open");
  cmState.bars = null;
  cmState.drag = null;
  cmState.hover = null;
  cmState.more = false;
  cmState.loading = false;
}

// 交互：移动看十字光标、滚轮缩放、拖动平移、双击复位
function bindBigChart() {
  const cv = $("chartBig");

  cv.addEventListener("mousemove", (e) => {
    if (!cmState.bars) return;
    if (cmState.drag) {
      const rect = cv.getBoundingClientRect();
      const dx = e.clientX - cmState.drag.x;
      const barsPerPx = cmVisible() / Math.max(1, rect.width);
      const shift = Math.round(-dx * barsPerPx);
      const span = cmVisible();
      let i0 = Math.max(0, Math.min(cmState.bars.length - span, cmState.drag.i0 + shift));
      cmState.i0 = i0;
      cmState.i1 = i0 + span;
      cmState.hover = null;
      cmRender();
      if (i0 <= 3) cmLoadOlder();      // 拖到最左边 → 继续往前加载
      return;
    }
    const rect = cv.getBoundingClientRect();
    const padL = 6, padR = 58;
    const plotW = rect.width - padL - padR;
    const step = plotW / Math.max(1, cmVisible());
    const idx = Math.round((e.clientX - rect.left - padL) / Math.max(1, step) - 0.5) + cmState.i0;
    const clamped = Math.max(cmState.i0, Math.min(cmState.i1 - 1, idx));
    if (clamped !== cmState.hover) {
      cmState.hover = clamped;
      cmRender();
    }
  });

  cv.addEventListener("mouseleave", () => {
    cmState.hover = null;
    cmRender();
  });

  cv.addEventListener("mousedown", (e) => {
    cmState.drag = { x: e.clientX, i0: cmState.i0 };
    cv.style.cursor = "grabbing";
  });
  window.addEventListener("mouseup", () => {
    if (cmState.drag) {
      cmState.drag = null;
      cv.style.cursor = "";
    }
  });

  cv.addEventListener("wheel", (e) => {
    e.preventDefault();
    cmZoomAt(e.clientX, e.deltaY > 0 ? 1.18 : 0.85);
  }, { passive: false });

  cv.addEventListener("dblclick", () => cmReset());

  $("cmClose").addEventListener("click", closeChartModal);
  $("cmReset").addEventListener("click", cmReset);
  $("cmPrev").addEventListener("click", () => cmNav(-1));
  $("cmNext").addEventListener("click", () => cmNav(1));
  $("cmFav").addEventListener("click", () => {
    if (!$("cmFavPop").hidden) cmCloseFav(); else cmOpenFav();
  });
  $("cfpCancel").addEventListener("click", cmCloseFav);
  $("cfpX").addEventListener("click", cmCloseFav);
  $("cfpNote").addEventListener("input", () => {
    $("cfpCount").textContent = String($("cfpNote").value.length);
  });
  $("cfpSave").addEventListener("click", cmSaveFav);
  $("cfpRemove").addEventListener("click", cmRemoveFav);
  $("cfpNote").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); cmSaveFav(); }
  });
  document.addEventListener("keydown", (e) => {
    if ($("chartModal").hidden) return;
    if (e.key === "Escape") closeChartModal();
    else if (e.key === "ArrowLeft") { e.preventDefault(); cmNav(-1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); cmNav(1); }
  });
  window.addEventListener("resize", () => { if (!$("chartModal").hidden) cmRender(); });
}

// ---------------------------------------------------------------- 其他
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove("show"), 2400);
}

$("csvBtn").addEventListener("click", async () => {
  if (!state.total) return;
  const top10 = state.lastConds?.some((c) => c.k === "top10_ratio");
  const head = `代码,名称,板块,${top10 ? "十大占比%" : "户数降幅%"},三季净利(亿),总市值(亿),流通市值(亿),PE,PB,股息率%,ROE年化%,净利同比%,营收同比%,毛利率%,报告期`;
  // 导出要全量：绕过分页再取一次（limit=0）
  const q = currentParams();
  q.set("limit", "0");
  q.set("offset", "0");
  let allRows = sortedRows();
  try {
    const d = await (await fetch("/api/screen?" + q.toString())).json();
    if (d.rows && d.rows.length) allRows = d.rows;
  } catch (e) { toast("导出取全量失败，导出当前页"); }
  const body = allRows.map(r =>
    [r.symbol, r.name, r.board, effVal(r, "drop_pct") ?? "", r.profit_yi,
     r.mktcap ?? "", r.float_mktcap ?? "", r.pe ?? "", r.pb ?? "", r.dv ?? "", r.roe_annual ?? "",
     r.yoy ?? "", r.rev_yoy ?? "", r.gross_margin ?? "", r.period].join(",")).join("\n");
  const blob = new Blob(["\ufeff" + head + "\n" + body], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `screen_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
});

// ---------------------------------------------------------------- 个股搜索（右侧走势面板）
// 只负责「看这一只票」：代码 / 名称 / 拼音补全，和左侧筛选条件、结果过滤完全分开。
const ssState = { items: [], idx: -1, timer: null, seq: 0 };

function ssClose() {
  ssState.items = [];
  ssState.idx = -1;
  const box = $("stockSuggest");
  if (!box) return;
  box.hidden = true;
  box.innerHTML = "";
}

function ssRender() {
  const box = $("stockSuggest");
  if (!ssState.items.length) { ssClose(); return; }
  box.innerHTML = ssState.items.map((it, i) => `
    <div class="kw-item${i === ssState.idx ? " on" : ""}" data-i="${i}">
      <span class="kw-code">${it.symbol}</span>
      <span class="kw-name">${escapeHtml(it.name)}</span>
      <span class="kw-board">${it.board || ""}</span>
    </div>`).join("");
  box.hidden = false;
  box.querySelectorAll(".kw-item").forEach((el) => {
    el.addEventListener("mousedown", (e) => {
      e.preventDefault();
      ssPick(Number(el.dataset.i));
    });
  });
}

function ssPick(i) {
  const it = ssState.items[i];
  if (!it) return;
  $("stockSearch").value = it.symbol;
  ssClose();
  selectStock(it.symbol);
}

async function ssSearch(q) {
  const query = (q || "").trim();
  if (!query) { ssClose(); return []; }
  const seq = ++ssState.seq;
  try {
    const d = await (await fetch("/api/suggest?q=" + encodeURIComponent(query))).json();
    if (seq !== ssState.seq) return [];      // 放弃过期结果
    ssState.items = d.items || [];
    ssState.idx = -1;
    ssRender();
    return ssState.items;
  } catch (e) {
    if (seq === ssState.seq) ssClose();
    return [];
  }
}

function ssMove(step) {
  if (!ssState.items.length) return;
  const n = ssState.items.length;
  ssState.idx = (ssState.idx + step + n + (ssState.idx < 0 && step < 0 ? n : 0)) % n;
  ssRender();
}

/** 回车 /「查看」：6 位代码直连；否则取补全第一项；还没补全就先搜一次 */
async function ssSubmit() {
  clearTimeout(ssState.timer);            // 别让防抖再补发一次搜索
  const q = $("stockSearch").value.trim();
  if (!q) { toast("输入代码 / 名称 / 拼音再查"); $("stockSearch").focus(); return; }
  if (/^\d{6}$/.test(q)) {
    ssState.seq++;                        // 作废在途的补全请求
    ssClose();
    selectStock(q);
    return;
  }
  let items = ssState.items;
  if (!items.length) items = await ssSearch(q);
  if (!items.length) { toast(`没有找到「${q}」`); return; }
  ssPick(ssState.idx >= 0 ? ssState.idx : 0);
}

function syncStockClear() {
  const btn = $("stockClear");
  if (btn) btn.hidden = !$("stockSearch").value;
}

/** 一键清空查股输入框（不清掉当前走势，只清查询条件） */
function clearStockSearch(focus = true) {
  ssState.seq++;                  // 作废在途补全请求
  ssClose();
  $("stockSearch").value = "";
  syncStockClear();
  if (focus) $("stockSearch").focus();
}

$("stockSearch").addEventListener("input", (e) => {
  ssState.seq++;                 // 输入变了：作废在途请求，也别拿上一次的补全去回车
  ssClose();
  syncStockClear();
  clearTimeout(ssState.timer);
  ssState.timer = setTimeout(() => ssSearch(e.target.value), 160);
});

$("stockSearch").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); ssMove(1); return; }
  if (e.key === "ArrowUp") { e.preventDefault(); ssMove(-1); return; }
  if (e.key === "Escape") {
    if (!$("stockSuggest").hidden) { ssClose(); return; }
    if ($("stockSearch").value) clearStockSearch();
    return;
  }
  if (e.key === "Enter") { e.preventDefault(); ssSubmit(); }
});

$("stockSearchGo").addEventListener("click", ssSubmit);
$("stockClear").addEventListener("click", () => clearStockSearch());

document.addEventListener("mousedown", (e) => {
  if (!$("stockSuggest").hidden && !e.target.closest(".stock-search")) ssClose();
});

$("runBtn").addEventListener("click", runScreen);
window.addEventListener("resize", () => { if (state.active) selectStock(state.active); });

// ---------------------------------------------------------------- 用户 / 收藏
function renderUser() {
  const u = state.user;
  $("userBtn").textContent = u ? `👤 ${u.nickname || u.username}` : "登录 / 注册";
  $("userBtn").classList.toggle("on", !!u);
  $("favBtn").hidden = !u;
  $("favCount").textContent = u ? String(state.favs.length) : "0";
  $("favSub").textContent = u ? `${state.favs.length} 只` : "";
}

async function loadIndustries() {
  try {
    const d = await (await fetch("/api/industries")).json();
    state.industries = d.industries || [];
    renderConds();
    renderStockIndustryOptions();
  } catch (e) { /* 忽略：行业下拉降级为空白 */ }
}

async function loadMe() {
  try {
    const d = await (await fetch("/api/me")).json();
    state.user = d.user || null;
    state.favs = d.favorites || [];
    state.favSet = new Set(d.symbols || []);
    state.groups = d.groups || [];
    if (state.favFilter !== "all" && !state.groups.some((g) => g.id === state.favFilter)) {
      state.favFilter = "all";
    }
  } catch (e) {
    state.user = null;
  }
  renderUser();
  renderFavList();
  renderActiveTable();
}

async function toggleFav(symbol) {
  if (!state.user) { openAuth("login"); return; }
  const on = state.favSet.has(symbol);
  try {
    const r = on
      ? await fetch("/api/fav/delete", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ symbol }),
        })
      : await fetch("/api/fav", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            symbol,
            group_id: typeof state.favFilter === "number" ? state.favFilter : null,
          }),
        });
    const d = await r.json();
    if (d.error) { toast(d.error); return; }
    toast(on ? `已移除 ${symbol}` : `已收藏 ${symbol}，可在右上角「收藏」里记笔记`);
    await loadMe();
    if (!$("favPop").hidden) renderFavList();
    state.favLoaded = false;                    // 「我的收藏」标签下次切过去重新加载
    if (state.resultTab === "favorites") loadFavList();
  } catch (e) {
    toast("操作失败：" + e.message);
  }
}

function favFiltered() {
  if (state.favFilter === "all") return state.favs;
  return state.favs.filter((f) => (f.group_id || 0) === state.favFilter);
}

function renderFavList() {
  const box = $("favList");
  if (!state.user) {
    box.innerHTML = '<div class="fav-empty">登录后可收藏股票并记录跟踪笔记</div>';
    return;
  }
  if (!state.favs.length) {
    box.innerHTML = '<div class="fav-empty">还没有收藏。在结果表左侧点 ★ 添加。</div>';
    return;
  }

  const chips = [`<button class="chip ${state.favFilter === "all" ? "on" : ""}" data-filter="all">全部 ${state.favs.length}</button>`]
    .concat(state.groups.map((g) => `
      <button class="chip ${state.favFilter === g.id ? "on" : ""}" data-filter="${g.id}"
              ${g.id ? `data-gid="${g.id}" title="双击重命名，× 删除"` : ""}>
        ${g.name} ${g.count}${g.id ? '<span class="chip-x" data-delg="' + g.id + '">×</span>' : ""}
      </button>`))
    .concat(['<button class="chip add" data-newgroup="1">+ 分组</button>']);

  const list = favFiltered();
  box.innerHTML = `
    <div class="chips">${chips.join("")}</div>
    ${list.length ? "" : '<div class="fav-empty">这个分组还没有股票</div>'}
    ${list.map((f) => `
    <div class="fav-item" data-symbol="${f.symbol}">
      <div class="fav-top">
        <span class="code">${f.symbol}</span>
        <span class="sname">${f.name || ""}</span>
        <button class="copy-btn" data-copy="${f.symbol}" title="复制代码到剪贴板">${ICON_COPY}</button>
        <span class="fav-price">${f.close ?? "—"}</span>
        <span class="fav-chg ${f.chg > 0 ? "pos" : f.chg < 0 ? "neg" : ""}">${
          f.chg == null ? "" : (f.chg > 0 ? "+" : "") + f.chg.toFixed(2) + "%"}</span>
        <a class="ext" href="${THS(f.symbol)}" target="_blank" rel="noopener" title="同花顺">${iconSvg("external", { size: 13 })}</a>
        <select class="fav-group" data-gsel="${f.symbol}" title="移动到分组">
          ${state.groups.map((g) => `<option value="${g.id}" ${((f.group_id || 0) === g.id) ? "selected" : ""}>${g.name}</option>`).join("")}
        </select>
        <button class="mini danger" data-del="${f.symbol}">移除</button>
      </div>
      ${f.notes ? `
      <div class="fav-latest">
        <span class="fav-when">${relTime(f.latest_at)}</span>
        <span class="fav-text">${escapeHtml(f.latest_note)}</span>
        <a class="fav-more" data-notes="${f.symbol}">历史 ${f.notes} 条</a>
      </div>
      <div class="fav-timeline" data-tl="${f.symbol}" hidden></div>` : ""}
      <div class="fav-addnote">
        <textarea class="fav-note" data-note="${f.symbol}" rows="2"
          placeholder="追加一条跟进记录：今天为什么看好/看淡，下一步盯什么"></textarea>
        <button class="mini" data-save="${f.symbol}">记录</button>
      </div>
    </div>`).join("")}`;

  // 分组筛选 / 新建 / 删除 / 重命名
  box.querySelectorAll("[data-filter]").forEach((b) => {
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      state.favFilter = b.dataset.filter === "all" ? "all" : Number(b.dataset.filter);
      renderFavList();
    });
  });
  box.querySelectorAll("[data-newgroup]").forEach((b) => {
    b.addEventListener("click", async (e) => {
      e.stopPropagation();
      const name = prompt("新分组名称（1~12 字）");
      if (!name) return;
      await postJson("/api/group", { action: "create", name });
      await loadMe();
    });
  });
  box.querySelectorAll("[data-delg]").forEach((b) => {
    b.addEventListener("click", async (e) => {
      e.stopPropagation();
      const gid = Number(b.dataset.delg);
      const g = state.groups.find((x) => x.id === gid);
      if (!confirm(`删除分组「${g ? g.name : ""}」？组内股票会回到「未分组」。`)) return;
      await postJson("/api/group", { action: "delete", id: gid });
      if (state.favFilter === gid) state.favFilter = "all";
      await loadMe();
    });
  });
  box.querySelectorAll("[data-gid]").forEach((b) => {
    b.addEventListener("dblclick", async (e) => {
      e.stopPropagation();
      const gid = Number(b.dataset.gid);
      const g = state.groups.find((x) => x.id === gid);
      const name = prompt("重命名分组", g ? g.name : "");
      if (!name) return;
      await postJson("/api/group", { action: "rename", id: gid, name });
      await loadMe();
    });
  });

  // 移动到分组
  box.querySelectorAll("[data-gsel]").forEach((sel) => {
    sel.addEventListener("click", (e) => e.stopPropagation());
    sel.addEventListener("change", async (e) => {
      e.stopPropagation();
      const gid = Number(sel.value) || 0;
      await postJson("/api/group", { action: "assign", symbol: sel.dataset.gsel, id: gid });
      await loadMe();
    });
  });

  // 追加笔记
  box.querySelectorAll("[data-save]").forEach((b) => {
    b.addEventListener("click", async (e) => {
      e.stopPropagation();
      const sym = b.dataset.save;
      const ta = box.querySelector(`[data-note="${sym}"]`);
      const note = ta.value.trim();
      if (!note) { toast("先写点什么"); ta.focus(); return; }
      b.disabled = true;
      try {
        const d = await postJson("/api/note", { symbol: sym, note });
        if (d.error) { toast(d.error); return; }
        toast("已记录");
        ta.value = "";
        await loadMe();
        await showTimeline(sym, true);
      } finally { b.disabled = false; }
    });
  });

  box.querySelectorAll("[data-notes]").forEach((a) => {
    a.addEventListener("click", async (e) => {
      e.stopPropagation();
      await showTimeline(a.dataset.notes);
    });
  });

  box.querySelectorAll("[data-del]").forEach((b) => {
    b.addEventListener("click", async (e) => {
      e.stopPropagation();
      await toggleFav(b.dataset.del);
      await loadMe();
    });
  });

  box.querySelectorAll(".fav-item").forEach((el) => {
    el.addEventListener("click", (e) => {
      if (e.target.closest("textarea,button,a,select")) return;
      selectStock(el.dataset.symbol);
      setFavPanel(false);
    });
  });
}

async function showTimeline(symbol, forceOpen = false) {
  const box = $("favList");
  const el = box.querySelector(`[data-tl="${symbol}"]`);
  if (!el) return;
  const open = forceOpen || el.hidden;
  if (!open) { el.hidden = true; return; }
  try {
    const d = await (await fetch(`/api/notes?symbol=${encodeURIComponent(symbol)}`)).json();
    const notes = d.notes || [];
    el.innerHTML = notes.length ? notes.map((n) => `
      <div class="tl-item">
        <span class="tl-time" title="${n.created_at}">${fmtTime(n.created_at)}</span>
        <span class="tl-text">${escapeHtml(n.note)}</span>
        <button class="tl-del" data-delnote="${n.id}" title="删除这条">${iconSvg("x", { size: 13 })}</button>
      </div>`).join("") : '<div class="tl-empty">暂无记录</div>';
    el.hidden = false;
    el.querySelectorAll("[data-delnote]").forEach((b) => {
      b.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm("删除这条记录？")) return;
        await postJson("/api/note/delete", { id: Number(b.dataset.delnote) });
        await showTimeline(symbol, true);
        await loadMe();
      });
    });
  } catch (e) {
    el.textContent = "加载失败";
  }
}

function fmtTime(iso) {
  if (!iso) return "";
  const t = new Date(String(iso).replace(" ", "T"));
  if (Number.isNaN(t.getTime())) return String(iso).slice(0, 16).replace("T", " ");
  const p = (n) => String(n).padStart(2, "0");
  return `${t.getMonth() + 1}-${p(t.getDate())} ${p(t.getHours())}:${p(t.getMinutes())}`;
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function postJson(url, body) {
  try {
    const r = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (d.error) toast(d.error);
    return d;
  } catch (e) {
    toast("请求失败：" + e.message);
    return { error: e.message };
  }
}

function setFavPanel(open) {
  $("favPop").hidden = !open;
  if (open) { renderFavList(); setPanel(false); }
}

// 收藏已迁到独立页 /fav，这里不再弹面板（保留函数以兼容旧调用）
$("favClose").addEventListener("click", (e) => { e.stopPropagation(); setFavPanel(false); });

// ---------------------------------------------------------------- 登录 / 注册
let authMode = "login";

function openAuth(mode) {
  authMode = mode;
  $("authModal").hidden = false;
  $("authTitle").textContent = mode === "login" ? "登录" : "注册";
  $("authTip").textContent = mode === "login" ? "登录后可收藏股票并记录笔记" : "注册后自动登录";
  $("authSwitch").textContent = mode === "login" ? "注册新账号" : "已有账号，去登录";
  $("authSubmit").textContent = mode === "login" ? "登录" : "注册并登录";
  $("authPass2Wrap").hidden = mode === "login";
  $("authErr").textContent = "";
  $("authPass").value = "";
  $("authPass2").value = "";
  setTimeout(() => $("authUser").focus(), 30);
}

function closeAuth() { $("authModal").hidden = true; }

// ---------------------------------------------------------------- 站点配置
async function fetchSiteSettings() {
  try {
    return await (await fetch("/api/settings", { cache: "no-store" })).json();
  } catch (e) {
    return null;
  }
}

function applySiteSettings(data) {
  if (data) state.site = data;
  const allow = !state.site || state.site.allow_register !== false;
  if ($("authSwitch")) $("authSwitch").hidden = !allow;
  if (!allow && authMode === "register") openAuth("login");
}

async function loadSiteSettings() {
  applySiteSettings(await fetchSiteSettings());
}

$("authSwitch").addEventListener("click", () => openAuth(authMode === "login" ? "register" : "login"));
$("authModal").addEventListener("click", (e) => { if (e.target.id === "authModal") closeAuth(); });
$("userBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  if (!state.user) { openAuth("login"); return; }
  location.href = "/account";
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeAuth(); setFavPanel(false); }
});

async function submitAuth() {
  const username = $("authUser").value.trim();
  const password = $("authPass").value;
  const err = $("authErr");
  err.textContent = "";
  if (authMode === "register" && password !== $("authPass2").value) {
    err.textContent = "两次输入的密码不一致";
    return;
  }
  const btn = $("authSubmit");
  btn.disabled = true;
  try {
    const r = await fetch(authMode === "login" ? "/api/login" : "/api/register", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const d = await r.json();
    if (d.error) { err.textContent = d.error; return; }
    closeAuth();
    await loadMe();
    loadDatasets();   // 登录后解锁同步按钮
    toast(authMode === "login" ? `欢迎回来，${d.user.username}` : `注册成功，${d.user.username}`);
  } catch (e) {
    err.textContent = "请求失败：" + e.message;
  } finally {
    btn.disabled = false;
  }
}

$("authSubmit").addEventListener("click", submitAuth);
$("authPass").addEventListener("keydown", (e) => { if (e.key === "Enter") submitAuth(); });
$("authPass2").addEventListener("keydown", (e) => { if (e.key === "Enter") submitAuth(); });
$("authUser").addEventListener("keydown", (e) => { if (e.key === "Enter") $("authPass").focus(); });

// ---------------------------------------------------------------- 数据管理
let DS = null;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function relTime(iso) {
  if (!iso) return "—";
  const t = new Date(String(iso).replace(" ", "T"));
  if (Number.isNaN(t.getTime())) return "—";
  const s = (Date.now() - t.getTime()) / 1000;
  if (s < 0) return "刚刚";
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} 天前`;
  return String(iso).slice(0, 10);
}

function renderDatasets() {
  if (!DS) return;
  $("dpSub").textContent = `本地库 ${DS.db_mb} MB`;

  // 顶栏标签上的复权状态 / 同步中指示灯
  const bars = DS.datasets.find((d) => d.key === "bars");
  const adj = (bars && bars.note || "").includes("前复权") ? "· 前复权" : "· 不复权";
  $("dbAdj").textContent = adj;
  $("dbDot").classList.toggle("busy", DS.datasets.some((d) => d.running));

  const needLogin = !state.user;          // 未登录：只能看，不能同步
  $("dpLoginHint").hidden = !needLogin;
  $("dpRows").classList.toggle("readonly", needLogin);

  $("dpRows").innerHTML = DS.datasets.map((d) => `
    <tr data-kind="${d.key}">
      <td><span class="dp-name">${d.name}</span><span class="dp-note">${d.note || ""}</span></td>
      <td>${d.coverage}</td>
      <td class="dp-time">${d.latest || "—"}</td>
      <td class="dp-time" data-synced="${d.synced_at || ""}" title="${d.synced_at || ""}">${relTime(d.synced_at)}</td>
      <td class="dp-act">
        <button class="mini" data-sync="${d.key}" ${needLogin ? "disabled" : ""}
                title="${needLogin ? "请先登录" : "增量同步"}">同步</button>
        ${d.key === "bars"
          ? `<button class="mini" data-sync="bars_rebuild" ${needLogin ? "disabled" : ""}
                     title="${needLogin ? "请先登录" : "重新拉取全部历史，换成前复权（较慢）"}">重建</button>`
          : ""}
      </td>
    </tr>`).join("");

  document.querySelectorAll("[data-sync]").forEach((b) => {
    b.addEventListener("click", () => {
      if (needLogin) { toast("请先登录再同步数据"); openAuth("login"); return; }
      doSync(b.dataset.sync);
    });
  });
}

async function loadDatasets() {
  try {
    DS = await (await fetch("/api/datasets")).json();
    renderDatasets();
  } catch (e) {
    $("dpRows").innerHTML = '<tr><td colspan="5" class="dp-empty">数据状态读取失败</td></tr>';
  }
}

async function doSync(kind) {
  if (kind === "bars_rebuild" &&
      !confirm("全量重建会重新拉取 5578 只的全部历史并改写日线表，约需 8~12 分钟。继续？")) return;
  const btn = document.querySelector(`[data-sync="${kind}"]`);
  if (btn) { btn.disabled = true; btn.textContent = "同步中"; }
  try {
    const r = await fetch(`/api/sync?kind=${encodeURIComponent(kind)}`, { method: "POST" });
    const d = await r.json();
    if (d.error) { toast(d.error); if (btn) { btn.disabled = false; btn.textContent = "同步"; } return; }
    await poll(d.job, kind);
  } catch (e) {
    toast("同步失败：" + e.message);
    if (btn) { btn.disabled = false; btn.textContent = "同步"; }
  }
}

async function poll(jobId, kind) {
  const rowKind = kind === "bars_rebuild" ? "bars" : kind;
  const row = document.querySelector(`tr[data-kind="${rowKind}"]`);
  const cell = row && row.querySelector("[data-synced]");
  while (true) {
    await sleep(800);
    let d;
    try { d = await (await fetch(`/api/sync/status?id=${jobId}`)).json(); }
    catch { continue; }
    if (d.status === "running") {
      if (cell) {
        const pct = d.total ? Math.round((d.done / d.total) * 100) : 0;
        cell.innerHTML = `${d.msg || "同步中"}<div class="progress"><i style="width:${pct}%"></i></div>`;
      }
      continue;
    }
    if (d.status === "done") {
      const r = d.result || {};
      toast(`${kind} 同步完成${r.updated !== undefined ? "，更新 " + r.updated + " 只" : ""}`);
    } else if (d.status === "error") {
      toast("同步失败：" + (d.msg || "未知错误"));
    }
    await loadDatasets();
    if (state.resultTab === "stocks") loadStockList();
    return;
  }
}

function setPanel(open) {
  $("dbPop").hidden = !open;
  $("dbBtn").setAttribute("aria-expanded", open ? "true" : "false");
  if (open) loadDatasets();
}

$("dpLoginBtn").addEventListener("click", (e) => {
  e.stopPropagation(); setPanel(false); openAuth("login");
});
$("dbBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  setPanel($("dbPop").hidden);
});
$("dpClose").addEventListener("click", (e) => { e.stopPropagation(); setPanel(false); });
document.addEventListener("click", (e) => {
  if (!$("dbPop").hidden && !$("dbPop").contains(e.target) && !$("dbBtn").contains(e.target)) {
    setPanel(false);
  }
  if (!$("favPop").hidden && !$("favPop").contains(e.target) && !$("favBtn").contains(e.target)) {
    setFavPanel(false);
  }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") setPanel(false); });

// 相对时间每 30 秒刷新一次（同步中的行不动）
setInterval(() => {
  document.querySelectorAll("[data-synced]").forEach((td) => {
    if (!td.querySelector(".progress")) td.textContent = relTime(td.dataset.synced);
  });
}, 30000);

// ---------------------------------------------------------------- 初始化
$("addCond").addEventListener("click", addCondition);
$("saveBtn").addEventListener("click", saveCurrent);

// 顶栏固定后的阴影 + 顶栏菜单（选股筛选 / 个股走势）
const topbarEl = document.querySelector(".topbar");
function syncTopbarShadow() {
  if (topbarEl) topbarEl.classList.toggle("scrolled", window.scrollY > 4);
}
/** 顶栏在窄屏会折行，把真实高度写进 CSS 变量，右侧粘性面板和锚点才不会钻到顶栏下面 */
function syncTopbarHeight() {
  if (!topbarEl) return;
  document.documentElement.style.setProperty(
    "--topbar-h", Math.round(topbarEl.getBoundingClientRect().height) + "px");
}
window.addEventListener("scroll", syncTopbarShadow, { passive: true });
window.addEventListener("resize", syncTopbarHeight);
if (window.ResizeObserver) new ResizeObserver(syncTopbarHeight).observe(topbarEl);
syncTopbarShadow();
syncTopbarHeight();

// 顶栏「选股筛选」：展开 / 收起下面的筛选表单（高度做滑动过渡）
const screenCollapse = $("screenCollapse");
const screenToggle = $("screenToggle");
let screenOpen = true;

function setScreenOpen(open, animate = true) {
  if (!screenCollapse || !screenToggle) return;
  screenOpen = open;
  screenToggle.setAttribute("aria-expanded", open ? "true" : "false");
  screenCollapse.inert = !open;               // 收起后里面的控件不再能被 Tab 到
  clearTimeout(screenCollapse._t);
  if (!animate) {
    screenCollapse.style.height = open ? "" : "0px";
    return;
  }
  if (open) {
    const h = screenCollapse.scrollHeight;
    screenCollapse.style.height = "0px";
    void screenCollapse.offsetHeight;         // 强制回流，让 0 -> h 生效
    screenCollapse.style.height = h + "px";
    screenCollapse._t = setTimeout(() => { screenCollapse.style.height = ""; }, 330);
  } else {
    screenCollapse.style.height = screenCollapse.scrollHeight + "px";
    void screenCollapse.offsetHeight;
    screenCollapse.style.height = "0px";
  }
}

screenToggle.addEventListener("click", () => setScreenOpen(!screenOpen));

// 收藏面板里的复制按钮（事件委托，重渲染后依然有效）
$("favList").addEventListener("click", async (e) => {
  const b = e.target.closest(".copy-btn");
  if (!b) return;
  e.stopPropagation();
  const code = b.dataset.copy;
  if (await copyText(code)) {
    toast(`已复制 ${code}`);
    b.classList.add("copied");
    setTimeout(() => b.classList.remove("copied"), 900);
  } else {
    toast("复制失败，请手动选中");
  }
});

async function loadFields() {
  for (let i = 0; i < 3; i++) {
    try {
      const d = await (await fetch("/api/fields", { cache: "no-store" })).json();
      if (d.fields && d.fields.length) {
        state.fields = d.fields;
        if (d.periods && d.periods.length) state.periods = d.periods;
        state.years = d.years || [];
        state.holderYears = d.holderYears || [];
        state.top10Years = d.top10Years || [];
        return;
      }
    } catch (e) { /* retry */ }
    await new Promise((r) => setTimeout(r, 400));
  }
  state.fields = [];
  toast("指标列表加载失败，请刷新页面");
}

// 深链接：?sort=pe&dir=asc&size=20&page=2 直接定位到某一页
function applyPagingParams() {
  const q = new URLSearchParams(location.search);
  const rawSize = q.get("size");
  if (rawSize !== null && rawSize !== "") {
    const size = Number(rawSize);
    if ([0, 20, 50, 100].includes(size)) {
      state.pageSize = size;
      const sel = $("pageSize");
      if (sel) sel.value = String(size);
    }
  }
  const sort = q.get("sort");
  if (sort) {
    state.sortKey = sort;
    state.sortAsc = (q.get("dir") || "desc").toLowerCase() === "asc";
  }
  const page = Number(q.get("page"));
  if (page > 0) state.page = page;
}

(async () => {
  applyPagingParams();
  await loadFields();
  await loadIndustries();
  renderConds();
  loadScreens();
  await loadMe();
  await loadSiteSettings();
  loadDatasets();
  updateResultTabUI();
  await loadStockList();      // 默认进入「个股列表」；筛选只在点击「筛选」后运行
  // 支持 /?login=1 直接打开登录框
  const q = new URLSearchParams(location.search);
  if (q.get("db") === "1") setPanel(true);
  if (q.get("login") === "1" && !state.user) openAuth("login");
  const code = q.get("code") || "";
  if (/^\d{6}$/.test(code)) {                     // /?code=601838 直接看某只票
    await selectStock(code);
    if (q.get("chart") === "1") await openChartModal();   // &chart=1 顺便弹大图
  }
})();

bindBigChart();

// 小图点击 / 右上角按钮 → 弹出大图
$("chartWrap").addEventListener("click", openChartModal);
$("chartZoom").addEventListener("click", (e) => { e.stopPropagation(); openChartModal(); });
$("cmSync").addEventListener("click", (e) => { e.stopPropagation(); cmSyncBars(); });

// 切换亮/暗主题后重画当前 K 线（canvas 需要按新配色重绘）
new MutationObserver(() => {
  if (state.chartBars) drawChart(state.chartBars);
  if (!$("chartModal").hidden) cmRender();
}).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
