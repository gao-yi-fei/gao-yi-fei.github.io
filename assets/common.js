window.SCPPER = (() => {
  const fmt = new Intl.NumberFormat("zh-CN");
  let cacheToken = new URLSearchParams(location.search).get("v") || "";
  const jsonCache = new Map();
  const nav = [
    ["/", "主页", "home"],
    ["/pages.html", "页面索引", "pages"],
    ["/users.html", "用户索引", "users"],
    ["/forum.html", "讨论区", "forum"],
    ["/recent.html", "最近", "recent"],
  ];
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "\"":"&quot;", "'":"&#39;" }[c]));
  }
  function dataUrl(path) {
    return cacheToken ? `${path}${path.includes("?") ? "&" : "?"}v=${encodeURIComponent(cacheToken)}` : path;
  }
  async function loadJson(path, compressed = true) {
    const url = dataUrl(path);
    const key = `${compressed ? "gz" : "plain"}:${url}`;
    if (jsonCache.has(key)) return jsonCache.get(key);
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    let parsed;
    if (!compressed) parsed = await res.json();
    else {
      if (!res.body || !("DecompressionStream" in window)) throw new Error("当前浏览器不支持 gzip 数据解压");
      parsed = await new Response(res.body.pipeThrough(new DecompressionStream("gzip"))).json();
    }
    jsonCache.set(key, parsed);
    return parsed;
  }
  function idle(task) {
    if ("requestIdleCallback" in window) {
      requestIdleCallback(task, { timeout: 1800 });
    } else {
      setTimeout(task, 60);
    }
  }
  function pageHref(pageName) {
    return `/pages.html?page=${encodeURIComponent(pageName || "")}`;
  }
  function forumHref(categoryId, threadId, postId) {
    const params = new URLSearchParams();
    if (categoryId) params.set("category", categoryId);
    if (threadId) params.set("thread", threadId);
    if (postId) params.set("post", postId);
    return `/forum.html?${params.toString()}`;
  }
  function registerServiceWorker() {
    if (!("serviceWorker" in navigator) || location.protocol === "file:") return;
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
  function refreshData() {
    cacheToken = String(Date.now());
    const next = `${location.pathname}?v=${cacheToken}`;
    if (location.search) {
      location.replace(next);
    } else {
      location.href = next;
    }
  }
  function renderNav(current) {
    return nav.map(([href, label, key]) => `<a href="${href}" ${key === current ? 'aria-current="page"' : ""}>${label}</a>`).join("") +
      `<button type="button" id="refreshData">刷新数据</button>`;
  }
  function wireRefresh() {
    document.getElementById("refreshData")?.addEventListener("click", refreshData);
  }
  function ratingLabel(value) {
    return value == null ? "n/a" : `${value > 0 ? "+" : ""}${value}`;
  }
  function ratingClass(value) {
    return value > 0 ? "rating positive" : value < 0 ? "rating negative" : "rating";
  }
  function normalize(value) {
    return String(value ?? "").trim().toLowerCase();
  }
  function pager(kind, total, page, size) {
    const pages = Math.max(1, Math.ceil(total / size));
    const current = Math.min(Math.max(1, page), pages);
    return `<div class="pager ${kind.endsWith("bottom") ? "bottom" : ""}" data-pages="${pages}">
      <button type="button" data-page-kind="${escapeHtml(kind)}" data-page="${current - 1}" ${current <= 1 ? "disabled" : ""}>上一页</button>
      <span class="pager-summary">第 ${fmt.format(current)} / ${fmt.format(pages)} 页，共 ${fmt.format(total)} 条</span>
      <span class="pager-jump">
        <label>跳至 <input type="number" min="1" max="${pages}" value="${current}" inputmode="numeric" data-page-input="${escapeHtml(kind)}" aria-label="跳转页码"> 页</label>
        <button type="button" data-page-go="${escapeHtml(kind)}">跳转</button>
      </span>
      <button type="button" data-page-kind="${escapeHtml(kind)}" data-page="${current + 1}" ${current >= pages ? "disabled" : ""}>下一页</button>
    </div>`;
  }
  function pagerAction(event) {
    let pagerEl = null, rawKind = "", rawPage = NaN;
    if (event.type === "click") {
      const button = event.target.closest("button[data-page-kind], button[data-page-go]");
      if (!button || button.disabled) return null;
      pagerEl = button.closest(".pager");
      if (button.dataset.pageKind) {
        rawKind = button.dataset.pageKind;
        rawPage = Number(button.dataset.page);
      } else {
        rawKind = button.dataset.pageGo;
        rawPage = Number(pagerEl?.querySelector("input[data-page-input]")?.value);
      }
    } else if (event.type === "keydown" && event.key === "Enter") {
      const input = event.target.closest("input[data-page-input]");
      if (!input) return null;
      event.preventDefault();
      pagerEl = input.closest(".pager");
      rawKind = input.dataset.pageInput;
      rawPage = Number(input.value);
    } else if (event.type === "change") {
      const input = event.target.closest("input[data-page-input]");
      if (!input) return null;
      pagerEl = input.closest(".pager");
      rawKind = input.dataset.pageInput;
      rawPage = Number(input.value);
    } else {
      return null;
    }
    if (!rawKind) return null;
    const pages = Math.max(1, Number(pagerEl?.dataset.pages) || 1);
    const page = Math.min(Math.max(1, Math.trunc(rawPage || 1)), pages);
    const input = pagerEl?.querySelector("input[data-page-input]");
    if (input) input.value = String(page);
    return { kind: rawKind.replace("-bottom", ""), page };
  }
  function pageSlice(items, page, size) {
    const pages = Math.max(1, Math.ceil(items.length / size));
    const current = Math.min(Math.max(1, page), pages);
    return { page: current, size, items: items.slice((current - 1) * size, current * size) };
  }
  const kindLabels = { original: "原创", translation: "翻译", fragment: "段落", other: "其他", forum: "讨论" };
  function kindBadge(kind) {
    const key = kind || "other";
    return `<span class="kind-badge kind-${escapeHtml(key)}">${escapeHtml(kindLabels[key] || "其他")}</span>`;
  }
  function formatBeijing(value, explicit) {
    if (explicit) return explicit;
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(date).replace(/\//g, "/");
  }
  function tickBeijing(id) {
    const node = document.getElementById(id);
    if (!node) return;
    const render = () => {
      const now = new Date();
      const text = new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }).format(now).replace(/\//g, "/");
      node.textContent = text;
    };
    render();
    setInterval(render, 1000);
  }
  function scrollMobile(id) {
    if (window.matchMedia("(max-width: 980px)").matches) {
      document.getElementById(id)?.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  }
  idle(registerServiceWorker);
  return { fmt, escapeHtml, loadJson, idle, pageHref, forumHref, refreshData, renderNav, wireRefresh, ratingLabel, ratingClass, normalize, pager, pagerAction, pageSlice, kindBadge, formatBeijing, tickBeijing, scrollMobile };
})();
