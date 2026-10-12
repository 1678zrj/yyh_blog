/* ---------------------------------------------------------------------------
 * 页面动效脚本
 *   1. 元素进入视口时淡入上移（滚动到哪，动到哪）
 *   2. 文章页顶部阅读进度条
 *
 * mkdocs-material 开启了 navigation.instant 后是「无刷新换页」，
 * 所以用 MutationObserver 监听内容变化，而不是只监听 DOMContentLoaded。
 * 关掉 JS 也能正常阅读：.reveal 只有在 JS 运行时才会被加上。
 * ------------------------------------------------------------------------- */

(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- 1. 滚动淡入 ---- */

  var REVEAL_SELECTORS = [
    ".md-post--excerpt",
    ".post-card",
    ".tag-listing__item",
    ".chip--category",
    ".home-stat"
  ];

  var observer = null;

  function initReveal() {
    if (reduceMotion || !("IntersectionObserver" in window)) return;

    if (!observer) {
      observer = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry, index) {
            if (!entry.isIntersecting) return;
            var element = entry.target;
            window.setTimeout(function () {
              element.classList.add("is-revealed");
            }, Math.min(index * 45, 260));
            observer.unobserve(element);
          });
        },
        { rootMargin: "0px 0px -6% 0px", threshold: 0.05 }
      );
    }

    var nodes = document.querySelectorAll(REVEAL_SELECTORS.join(","));
    Array.prototype.forEach.call(nodes, function (element, index) {
      if (element.classList.contains("reveal")) return;
      element.classList.add("reveal");
      // 首屏元素直接显示，避免出现「空白等动画」
      if (element.getBoundingClientRect().top < window.innerHeight * 0.9) {
        element.classList.add("is-revealed");
      } else {
        observer.observe(element);
      }
    });
  }

  /* ---- 2. 阅读进度条 ---- */

  var progress = null;

  function updateProgress() {
    // 文章页的正文容器是 .md-content--post 里的 .md-content__inner
    var article = document.querySelector(".md-content--post .md-content__inner");
    if (!article) {
      if (progress) progress.style.transform = "scaleX(0)";
      return;
    }
    if (!progress) {
      progress = document.createElement("div");
      progress.className = "blog-progress";
      document.body.appendChild(progress);
    }
    var start = article.offsetTop;
    var total = article.offsetHeight - window.innerHeight * 0.6;
    var ratio = total > 0 ? (window.scrollY - start + window.innerHeight * 0.6) / total : 0;
    progress.style.transform = "scaleX(" + Math.min(Math.max(ratio, 0), 1) + ")";
  }

  /* ---- 3. 私密笔记专区的启动兜底 ---- */

  // mkdocs-material 开了 navigation.instant：点导航进入专区时不会重新执行页面里的
  // module 脚本。这里由全局脚本在检测到 #vault 时把它拉起来（bootVault 自身幂等）。
  function bootVaultIfNeeded() {
    var vault = document.getElementById("vault");
    if (!vault || vault.dataset.booted === "1") return;
    import("./private.js")
      .then(function (mod) { mod.bootVault(); })
      .catch(function () { /* 不是专区页面或加载失败，忽略 */ });
  }

  /* ---- 4. 页面初始化与无刷新换页 ---- */

  function refresh() {
    initReveal();
    updateProgress();
    bootVaultIfNeeded();
  }

  var scheduled = false;
  function scheduleRefresh() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(function () {
      scheduled = false;
      refresh();
    });
  }

  if (document.readyState !== "loading") {
    refresh();
  } else {
    document.addEventListener("DOMContentLoaded", refresh);
  }

  window.addEventListener("scroll", function () {
    if (progress) updateProgress();
    else scheduleRefresh();
  }, { passive: true });

  window.addEventListener("resize", scheduleRefresh, { passive: true });

  new MutationObserver(scheduleRefresh).observe(document.body, {
    childList: true,
    subtree: true
  });
})();
