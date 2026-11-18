// 亮 / 暗主题：存 localStorage，页面加载时立刻套用，避免闪白
(function () {
  const KEY = "astock-theme";
  const root = document.documentElement;

  function apply(theme) {
    root.setAttribute("data-theme", theme);
    document.querySelectorAll("[data-theme-btn]").forEach((el) => {
      el.title = theme === "dark" ? "切换到亮色" : "切换到暗色";
    });
  }

  function current() {
    const q = new URLSearchParams(location.search).get("theme");
    if (q === "dark" || q === "light") {
      localStorage.setItem(KEY, q);
      return q;
    }
    return localStorage.getItem(KEY) === "dark" ? "dark" : "light";
  }

  function toggle() {
    const next = current() === "dark" ? "light" : "dark";
    localStorage.setItem(KEY, next);
    apply(next);
  }

  apply(current());          // 立即生效（脚本在 <head> 里）

  document.addEventListener("DOMContentLoaded", () => {
    apply(current());
    document.querySelectorAll("[data-theme-btn]").forEach((b) => {
      b.addEventListener("click", (e) => { e.preventDefault(); toggle(); });
    });
  });
})();
