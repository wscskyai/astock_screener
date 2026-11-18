// 个人中心 · 独立页（左侧菜单 + 右侧工作区）

const $ = (id) => document.getElementById(id);

const state = {
  user: null,
  favs: [],
  site: null,
};

// ---------------------------------------------------------------- 工具
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtTime(s) {
  if (!s) return "—";
  return String(s).replace("T", " ").slice(0, 16);
}

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove("show"), 2400);
}

async function getJson(url) {
  try {
    return await (await fetch(url, { cache: "no-store" })).json();
  } catch (e) {
    return { error: "请求失败：" + e.message };
  }
}

async function postJson(url, body) {
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
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

// ---------------------------------------------------------------- 账号信息
function firstChar(s) {
  const t = String(s || "").trim();
  return t ? t[0].toUpperCase() : "?";
}

function setRoleBadge(el, role) {
  el.textContent = role === "admin" ? "系统管理员" : "普通用户";
  el.className = "role-badge" + (role === "admin" ? " admin" : "");
}

function fillProfile() {
  const u = state.user;
  const nick = u.nickname || u.username;
  $("acctName").textContent = u.username;
  $("acctNick").textContent = nick;
  $("acctAvatar").textContent = firstChar(nick);
  $("sideAvatar").textContent = firstChar(nick);
  $("sideName").textContent = nick;
  setRoleBadge($("acctRole"), u.role);
  setRoleBadge($("sideRole"), u.role);
  $("acctCreated").textContent = fmtTime(u.created_at);
  $("acctLogin").textContent = fmtTime(u.last_login);
  $("acctFavs").textContent = `${state.favs.length} 只`;
  $("nickInput").value = nick;
  $("nickCount").textContent = `${nick.length}/20`;
}

function refreshTopbar() {
  const u = state.user;
  $("userLabel").textContent = u.nickname || u.username;
  $("favCount").textContent = String(state.favs.length);
}

$("nickInput").addEventListener("input", () => {
  $("nickCount").textContent = `${$("nickInput").value.length}/20`;
});

$("nickForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const nickname = $("nickInput").value.trim();
  if (!nickname) { toast("昵称不能为空"); return; }
  if (nickname.length > 20) { toast("昵称最多 20 个字符"); return; }
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  const r = await postJson("/api/profile", { nickname });
  btn.disabled = false;
  if (r.error) return;
  state.user.nickname = r.nickname;
  fillProfile();
  refreshTopbar();
  toast("昵称已更新");
});

$("pwdForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const newPwd = $("pwdNew").value;
  if (!newPwd) { toast("请输入新密码"); return; }
  if (newPwd.length < 6) { toast("新密码至少 6 位"); return; }
  if (newPwd !== $("pwdNew2").value) { toast("两次输入的新密码不一致"); return; }
  const btn = $("pwdSave");
  btn.disabled = true;
  const r = await postJson("/api/password", { old: $("pwdOld").value, new: newPwd });
  btn.disabled = false;
  if (r.error) return;
  $("pwdOld").value = ""; $("pwdNew").value = ""; $("pwdNew2").value = "";
  toast("密码已修改");
});

document.querySelectorAll(".pwd-eye").forEach((btn) => {
  btn.addEventListener("click", () => {
    const inp = $(btn.dataset.eye);
    if (!inp) return;
    const show = inp.type === "password";
    inp.type = show ? "text" : "password";
    btn.classList.toggle("on", show);
  });
});

// ---------------------------------------------------------------- 用户管理
function renderUserRows(users) {
  const me = state.user;
  $("userRows").innerHTML = users.map((u) => {
    const isMe = me && u.id === me.id;
    const isAdmin = u.role === "admin";
    const actions = isMe
      ? '<span class="acct-you">当前账号</span>'
      : `<button class="mini" data-act="reset" data-id="${u.id}"
                  data-name="${escapeHtml(u.username)}">重置密码</button>
         <button class="mini" data-act="role" data-id="${u.id}" data-name="${escapeHtml(u.username)}"
                 data-role="${isAdmin ? "user" : "admin"}">${isAdmin ? "设为普通" : "设为管理员"}</button>
         <button class="mini danger" data-act="del" data-id="${u.id}"
                 data-name="${escapeHtml(u.username)}">删除</button>`;
    return `<tr>
      <td><div class="acct-member">
        <span class="acct-avatar sm">${escapeHtml(firstChar(u.nickname || u.username))}</span>
        <span>
          <b class="acct-member-name">${escapeHtml(u.nickname || u.username)}</b>${
            (u.nickname && u.nickname !== u.username)
              ? `<span class="acct-member-account">@${escapeHtml(u.username)}</span>` : ""}${
            isMe ? '<span class="acct-you">你</span>' : ""}
        </span>
      </div></td>
      <td><span class="role-badge ${isAdmin ? "admin" : ""}">${isAdmin ? "管理员" : "普通用户"}</span></td>
      <td class="num">${u.favorites ?? 0}</td>
      <td>${(u.created_at || "").slice(0, 10) || "—"}</td>
      <td>${fmtTime(u.last_login)}</td>
      <td><div class="acct-actions">${actions}</div></td>
    </tr>`;
  }).join("") || '<tr><td colspan="6" class="acct-empty">还没有用户</td></tr>';
}

async function loadUsers() {
  const d = await getJson("/api/users");
  if (d.error) {
    $("userRows").innerHTML =
      `<tr><td colspan="6" class="acct-empty">${escapeHtml(d.error)}</td></tr>`;
    $("userTotal").textContent = "";
    return;
  }
  const users = d.users || [];
  $("userTotal").textContent = `共 ${users.length} 位会员`;
  renderUserRows(users);
}

$("newUserForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = $("newUserName").value.trim();
  const password = $("newUserPass").value;
  const role = $("newUserRole").value;
  if (!username) { toast("请输入用户名"); return; }
  if (password.length < 6) { toast("初始密码至少 6 位"); return; }
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  const r = await postJson("/api/users/create", { username, password, role });
  btn.disabled = false;
  if (r.error) return;
  toast(`已创建「${r.username}」${r.role === "admin" ? "（管理员）" : ""}`);
  $("newUserName").value = "";
  $("newUserPass").value = "";
  $("newUserRole").value = "user";
  loadUsers();
});

$("userRows").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const id = Number(b.dataset.id);
  const name = b.dataset.name || "";
  const act = b.dataset.act;
  if (act === "reset") {
    const pwd = prompt(`给「${name}」设置新密码（至少 6 位）`, "");
    if (pwd === null) return;
    if (pwd.length < 6) { toast("新密码至少 6 位"); return; }
    b.disabled = true;
    const r = await postJson("/api/users/reset_password", { id, password: pwd });
    b.disabled = false;
    if (r.error) return;
    toast(`已重置「${name}」的密码，该用户需重新登录`);
    loadUsers();
  } else if (act === "role") {
    const toAdmin = b.dataset.role === "admin";
    if (!confirm(`把「${name}」${toAdmin ? "设为管理员" : "改为普通用户"}？`)) return;
    b.disabled = true;
    const r = await postJson("/api/users/role", { id, role: b.dataset.role });
    b.disabled = false;
    if (r.error) return;
    toast(toAdmin ? `「${name}」已设为管理员` : `「${name}」已改为普通用户`);
    loadUsers();
  } else if (act === "del") {
    if (!confirm(`删除用户「${name}」？其收藏、笔记、分组和保存的查询会一并删除，不可恢复。`)) return;
    b.disabled = true;
    const r = await postJson("/api/users/delete", { id });
    b.disabled = false;
    if (r.error) return;
    toast(`已删除用户「${name}」`);
    loadUsers();
  }
});

// ---------------------------------------------------------------- 系统配置
function fillSiteForm(d) {
  $("cfgTitle").value = d.site_title || "";
  $("cfgIcp").value = d.icp || "";
  $("cfgAnalytics").value = d.analytics || "";
  $("cfgAllowRegister").checked = d.allow_register !== false;
  $("cfgAutoSync").checked = !!d.auto_sync;
  $("cfgSyncTime").value = d.sync_time || "17:30";
  $("cfgLastSync").textContent = "上次自动同步：" +
    (d.auto_sync_last ? String(d.auto_sync_last).replace("T", " ") : "—");
}

async function loadSiteSettings() {
  const d = await getJson("/api/settings");
  if (d.error || !d.is_admin) {
    toast(d.error || "需要管理员权限");
    return;
  }
  state.site = d;
  fillSiteForm(d);
}

$("siteForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    site_title: $("cfgTitle").value.trim(),
    icp: $("cfgIcp").value.trim(),
    analytics: $("cfgAnalytics").value,
    allow_register: $("cfgAllowRegister").checked,
    auto_sync: $("cfgAutoSync").checked,
    sync_time: $("cfgSyncTime").value || "17:30",
  };
  const btn = $("siteSave");
  btn.disabled = true;
  const r = await postJson("/api/settings", body);
  btn.disabled = false;
  if (r.error) return;
  r.is_admin = true;
  state.site = r;
  fillSiteForm(r);
  toast("配置已保存；网站标题 / 备案 / 统计代码刷新后生效");
});

// ---------------------------------------------------------------- 菜单 / 初始化
const PANEL_IDS = ["profile", "password", "users", "settings"];

function showPanel(id, push = true) {
  if (!PANEL_IDS.includes(id)) id = "profile";
  if ((id === "users" || id === "settings") && state.user.role !== "admin") id = "profile";
  document.querySelectorAll(".acct-menu-item[data-panel]").forEach((b) => {
    b.classList.toggle("on", b.dataset.panel === id);
  });
  document.querySelectorAll(".acct-panel").forEach((p) => {
    p.hidden = p.dataset.panelView !== id;
  });
  if (push) history.replaceState(null, "", "/account#" + id);
  if (id === "profile") fillProfile();
  if (id === "users") loadUsers();
  if (id === "settings") loadSiteSettings();
}

document.querySelectorAll(".acct-menu-item[data-panel]").forEach((b) => {
  b.addEventListener("click", () => showPanel(b.dataset.panel));
});
window.addEventListener("hashchange", () => {
  showPanel(location.hash.replace("#", ""), false);
});

$("acctLogout").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  location.href = "/";
});

// 顶栏在窄屏折行时同步真实高度，左侧粘性菜单才不会钻到顶栏下面
function syncTopbarHeight() {
  const bar = document.querySelector(".topbar");
  if (!bar) return;
  document.documentElement.style.setProperty(
    "--topbar-h", Math.round(bar.getBoundingClientRect().height) + "px");
}
window.addEventListener("resize", syncTopbarHeight);
if (window.ResizeObserver) {
  const bar = document.querySelector(".topbar");
  if (bar) new ResizeObserver(syncTopbarHeight).observe(bar);
}
syncTopbarHeight();

(async () => {
  const d = await getJson("/api/me");
  if (!d.user) { location.replace("/?login=1"); return; }
  state.user = d.user;
  state.favs = d.favorites || [];
  refreshTopbar();
  fillProfile();
  const admin = state.user.role === "admin";
  document.querySelectorAll("[data-admin]").forEach((el) => { el.hidden = !admin; });
  const initial = location.hash.replace("#", "") || "profile";
  showPanel(initial, false);
})();
