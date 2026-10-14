---
title: 私密笔记
hide:
  - navigation
  - toc
  - footer
---

<div id="vault">
<script>
// 这段必须同步执行、且放在解锁框之前：只要有可用的密钥，就直接不给解锁框出现的机会，
// 否则每次进页面都会先闪一下登录框，看起来像"又要输口令"。
(function () {
  try {
    if (sessionStorage.getItem("vault.skipAuto")) return;
    var saved = JSON.parse(localStorage.getItem("vault.remember") || "null");
    var remembered = saved && saved.k && saved.exp > Date.now();
    if (remembered || sessionStorage.getItem("vault.key")) {
      document.documentElement.classList.add("vault-auto");
    }
  } catch (e) { /* 隐私模式下读不了，退回手动输入 */ }
})();
</script>
<div id="vault-booting">正在用本机保存的密钥解锁…</div>
<div id="vault-gate">
<div class="vault-card">
<h1>私密笔记</h1>
<p>这个专区里的内容在服务器上就是密文，只有输入口令才会在你这台设备的浏览器里解密显示。<br>没有口令的人（包括搜索引擎和爬虫）看到的只是一堆乱码。</p>
<form id="vault-form" autocomplete="off">
<input id="vault-passphrase" type="password" placeholder="输入口令" autocomplete="current-password" spellcheck="false">
<label class="vault-remember"><input id="vault-remember" type="checkbox" checked> 在这台设备上记住半年，期间不再提示</label>
<button type="submit">解锁</button>
</form>
<div id="vault-status"></div>
<div class="vault-note">
口令不会保存在服务器上，也不会出现在仓库里；密钥只用 PBKDF2-SHA256（60 万次）在本机派生。<br>
勾选「记住」后，只有密钥会存在你这台浏览器的本地存储里，半年内不再提示；到期、换设备或清除浏览数据后需要重新输入。<br>
如果忘了口令，密文无法恢复。
</div>
</div>
</div>
<div id="vault-app" hidden>
<div id="vault-progress"></div>
<div class="vault-toolbar">
<div class="vault-search-wrap">
<input id="vault-search" type="search" placeholder="检索题目 / 标题 / 小节（Ctrl+K 或 /）" autocomplete="off">
<div id="vault-results" hidden></div>
</div>
<span id="search-status"></span>
<button id="vault-side-toggle" class="vault-btn" type="button" title="显示 / 隐藏章节目录">目录</button>
<button id="vault-quiz" class="vault-btn" type="button" title="遮住答案，只看问题自测（快捷键 T）">自测</button>
<button id="vault-font" class="vault-btn" type="button" title="切换正文字号（快捷键 + / -）">字号</button>
<button id="vault-forget" class="vault-btn" type="button" title="清除本机保存的密钥，下次需要重新输入口令">忘记本机密钥</button>
<button id="vault-lock" class="vault-btn" type="button">锁定</button>
</div>
<div class="vault-body">
<aside id="vault-side">
<input id="vault-filter" type="search" placeholder="筛选章节…" autocomplete="off">
<nav id="vault-nav"></nav>
</aside>
<main>
<div class="vault-head">
<div id="vault-crumb"></div>
<h1 id="vault-title"></h1>
</div>
<details id="vault-toc-box"><summary>本页目录</summary><nav id="vault-toc"></nav></details>
<article id="vault-article"></article>
<div id="vault-pager"></div>
</main>
</div>
<button id="vault-top" type="button" title="回到顶部" hidden>↑</button>
</div>
</div>

<link rel="stylesheet" href="../assets/private.css">
<script type="module" src="../assets/private.js"></script>
