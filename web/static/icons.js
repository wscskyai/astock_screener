// 图标：Feather / Lucide 风格的线性图标（24 格 viewBox，描边 2px）
// 用法：<span data-icon="moon"></span> 自动填充；或 JS 里 iconSvg("copy")

const ICONS = {
  sun: '<circle cx="12" cy="12" r="4"/>' +
       '<path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41' +
       'M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
  moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
  external: '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>' +
            '<path d="M15 3h6v6"/><path d="M10 14 21 3"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/>' +
        '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  star: '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 ' +
        '5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  user: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  database: '<ellipse cx="12" cy="5" rx="9" ry="3"/>' +
            '<path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>' +
            '<path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
  arrowLeft: '<path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/>',
  trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>' +
         '<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>' +
         '<path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3"/>' +
           '<path d="M1 14h6M9 8h6M17 16h6"/>',
  eye: '<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/>',
  eyeOff: '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/>' +
          '<path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>' +
          '<path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/><path d="m1 1 22 22"/>',
  bookmark: '<path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>',
  chevronLeft: '<path d="m15 18-6-6 6-6"/>',
  chevronRight: '<path d="m9 18 6-6-6-6"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  maximize: '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M16 3h3a2 2 0 0 1 2 2v3"/>' +
            '<path d="M16 21h3a2 2 0 0 0 2-2v-3"/><path d="M8 21H5a2 2 0 0 1-2-2v-3"/>',
  home: '<path d="m3 10 9-7 9 7v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>' +
        '<path d="M9 22V12h6v10"/>',
  fileText: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>' +
            '<path d="M14 2v6h6"/><path d="M8 13h8M8 17h5"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
};

function iconSvg(name, opts) {
  const o = opts || {};
  const body = ICONS[name];
  if (!body) return "";
  const size = o.size || 15;
  const cls = o.cls || "icon";
  const fill = o.filled ? "currentColor" : "none";
  return `<svg class="${cls}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="${fill}" ` +
    `stroke="currentColor" stroke-width="${o.stroke || 2}" stroke-linecap="round" ` +
    `stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

function paintIcons(root) {
  (root || document).querySelectorAll("[data-icon]").forEach((el) => {
    const name = el.dataset.icon;
    el.innerHTML = iconSvg(name, {
      size: Number(el.dataset.iconSize) || 15,
      filled: el.dataset.iconFilled === "1",
    });
  });
}

document.addEventListener("DOMContentLoaded", () => paintIcons());
