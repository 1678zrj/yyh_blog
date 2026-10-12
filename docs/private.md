---
title: 私密笔记
hide:
  - navigation
  - toc
  - footer
---

<div id="vault">
<div id="vault-gate" hidden>
<div class="vault-card">
<h1>私密笔记</h1>
<p>这个专区里的内容在服务器上就是密文，只有输入口令才会在你这台设备的浏览器里解密显示。<br>没有口令的人（包括搜索引擎和爬虫）看到的只是一堆乱码。</p>
<form id="vault-form" autocomplete="off">
<input id="vault-passphrase" type="password" placeholder="输入口令" autocomplete="current-password" spellcheck="false">
<button type="submit">解锁</button>
</form>
<div id="vault-status"></div>
<div class="vault-note">
口令不会保存在服务器上，也不会出现在仓库里；密钥只用 PBKDF2-SHA256（60 万次）在本机派生。<br>
解锁状态记录在当前标签页内，关掉页面即失效；如果忘了口令，密文无法恢复。
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
<button id="vault-lock" class="vault-btn" type="button">锁定</button>
</div>
<div class="vault-body">
<aside id="vault-side"><nav id="vault-nav"></nav></aside>
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
</div>
</div>

<link rel="stylesheet" href="../assets/private.css">
<script type="module" src="../assets/private.js"></script>
