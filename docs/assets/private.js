/* ---------------------------------------------------------------------------
 * 私密笔记专区：解锁、目录、阅读、检索
 *
 * 安全模型：
 *   仓库里只有密文（pages/*.bin、index.bin、search.bin）。
 *   口令不进代码、不进仓库；PBKDF2-SHA256(60 万次) 派生 AES-256-GCM 密钥在浏览器本地完成，
 *   明文只存在于当前页面的内存里。
 *   密钥（不是口令）可以临时存进 sessionStorage，刷新页面免重输；关掉标签页即失效。
 * ------------------------------------------------------------------------- */

const BASE = new URL("../assets/private/", import.meta.url);
const SESSION_KEY = "vault.key";          // 当前标签页内有效
const REMEMBER_KEY = "vault.remember";    // 本机记住（含过期时间）
const SKIP_AUTO_KEY = "vault.skipAuto";   // 主动锁定后，本次会话不再自动解锁
const LAST_PAGE_KEY = "vault.last";
const REMEMBER_DAYS = 180;   // 记住半年

const state = {
  key: null,
  build: "",
  structure: [],
  search: null,
  current: null,
  order: [],
};

const $ = (selector) => document.querySelector(selector);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
};

/* ---------- 加解密 ---------------------------------------------------------- */

const te = new TextEncoder();
const td = new TextDecoder();

function b64ToBytes(value) {
  const raw = atob(value);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

async function deriveKey(passphrase, salt, iterations) {
  const material = await crypto.subtle.importKey(
    "raw", te.encode(passphrase), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", salt, iterations, hash: "SHA-256" },
    material,
    { name: "AES-GCM", length: 256 },
    true,               // 允许导出，便于刷新后复用
    ["decrypt"]);
}

async function decryptBytes(key, buffer) {
  const bytes = new Uint8Array(buffer);
  const plain = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: bytes.subarray(0, 12) }, key, bytes.subarray(12));
  return td.decode(plain);
}

async function fetchDecrypt(key, relative) {
  const url = new URL(relative, BASE);
  // 带上构建号：否则浏览器缓存可能给到"上一次加密"的密文，和新的 salt 对不上，
  // 表现就是"明明记住过口令，却提示密钥已失效"。
  if (state.build) url.searchParams.set("b", state.build);
  const response = await fetch(url);
  if (!response.ok) throw new Error(`加载失败：${relative}（HTTP ${response.status}）`);
  return decryptBytes(key, await response.arrayBuffer());
}

/** 读取构建配置。config.json 体积很小，强制不走缓存，用它来给其它资源定版本。 */
async function loadConfig() {
  const response = await fetch(new URL("config.json", BASE), { cache: "no-store" });
  if (!response.ok) throw new Error(`读取配置失败（HTTP ${response.status}）`);
  const config = await response.json();
  state.build = config.build || "";
  return config;
}

async function exportKey(key) {
  const raw = await crypto.subtle.exportKey("raw", key);
  return btoa(String.fromCharCode(...new Uint8Array(raw)));
}

async function importKey(encoded) {
  const raw = Uint8Array.from(atob(encoded), (char) => char.charCodeAt(0));
  return crypto.subtle.importKey("raw", raw, { name: "AES-GCM" }, true, ["decrypt"]);
}

/* ---------- 本机「记住半年」 ------------------------------------------------ */

function rememberKey(encoded) {
  try {
    localStorage.setItem(REMEMBER_KEY, JSON.stringify({
      k: encoded,
      exp: Date.now() + REMEMBER_DAYS * 24 * 3600 * 1000,
    }));
  } catch { /* 隐私模式下可能写不进去，忽略即可 */ }
}

async function loadRememberedKey() {
  try {
    const saved = JSON.parse(localStorage.getItem(REMEMBER_KEY) || "null");
    if (!saved || !saved.k || !saved.exp) return null;
    if (Date.now() > saved.exp) { localStorage.removeItem(REMEMBER_KEY); return null; }
    return await importKey(saved.k);
  } catch {
    localStorage.removeItem(REMEMBER_KEY);
    return null;
  }
}

function forgetRememberedKey() {
  localStorage.removeItem(REMEMBER_KEY);
  sessionStorage.removeItem(SESSION_KEY);
  sessionStorage.removeItem(SKIP_AUTO_KEY);
}

/* ---------- 解锁界面 -------------------------------------------------------- */

function renderGate(message) {
  // 自动解锁失败/被锁定时，必须把「不显示登录框」的类去掉，否则用户看不到输入框
  document.documentElement.classList.remove("vault-auto");
  const gate = $("#vault-gate");
  gate.hidden = false;
  $("#vault-app").hidden = true;
  $("#vault-status").textContent = message || "";
  $("#vault-passphrase").focus();
}

async function unlock(passphrase) {
  const status = $("#vault-status");
  status.textContent = "正在派生密钥（PBKDF2，60 万次）…";
  const config = await loadConfig();

  const key = await deriveKey(passphrase, b64ToBytes(config.salt), config.iterations);
  status.textContent = "正在校验口令…";
  let structure;
  try {
    structure = JSON.parse(await fetchDecrypt(key, "index.bin"));
  } catch {
    return renderGate("口令不对，或者文件损坏了。");
  }

  state.key = key;
  state.structure = structure;
  const encoded = await exportKey(key);
  sessionStorage.setItem(SESSION_KEY, encoded);
  sessionStorage.removeItem(SKIP_AUTO_KEY);
  if ($("#vault-remember")?.checked) rememberKey(encoded);
  await enterApp();
}

async function enterApp() {
  document.documentElement.classList.remove("vault-auto");
  $("#vault-booting").hidden = true;
  $("#vault-gate").hidden = true;
  $("#vault-app").hidden = false;

  state.order = [];
  state.structure.forEach((chapter) => {
    chapter.pages.forEach((page) => {
      state.order.push({ id: page.id, title: page.title, chapter: chapter.chapter,
                         group: chapter.group, questions: page.questions });
    });
  });

  renderSidebar();
  bindSearch();

  const hash = location.hash.match(/^#p=([0-9a-f]+)$/);
  const remembered = localStorage.getItem(LAST_PAGE_KEY);
  const first = hash ? hash[1] : (remembered && state.order.some((p) => p.id === remembered)
    ? remembered : state.order[0]?.id);
  if (first) await openPage(first, { keepScroll: false });
}

/* ---------- 侧边栏 ---------------------------------------------------------- */

function renderSidebar() {
  const nav = $("#vault-nav");
  nav.textContent = "";
  const groups = new Map();
  state.structure.forEach((chapter) => {
    if (!groups.has(chapter.group)) groups.set(chapter.group, []);
    groups.get(chapter.group).push(chapter);
  });

  groups.forEach((chapters, groupName) => {
    const group = el("div", "vault-group");
    const pages = chapters.reduce((sum, chapter) => sum + chapter.pages.length, 0);
    const title = el("div", "vault-group__title");
    title.appendChild(document.createTextNode(groupName));
    title.appendChild(el("span", "vault-group__count", `${pages} 页`));
    title.addEventListener("click", () => group.classList.toggle("is-collapsed"));
    group.appendChild(title);

    const list = el("div", "vault-group__body");
    chapters.forEach((chapter) => {
      const chapterNode = el("div", "vault-chapter");
      if (chapter.pages.length > 1) {
        chapterNode.appendChild(el("div", "vault-chapter__title",
          `${chapter.chapter}（${chapter.pages.length} 页）`));
      }
      chapter.pages.forEach((page) => {
        // 页面标题形如「01-xxx（1/7）正文标题…」，前缀和章节名重复，侧边栏里去掉它
        const link = el("a", "vault-link");
        link.appendChild(el("span", "vault-link__text", shortLabel(chapter.chapter, page.title)));
        link.title = page.title;
        link.href = `#p=${page.id}`;
        link.dataset.pageId = page.id;
        link.addEventListener("click", (event) => {
          event.preventDefault();
          openPage(page.id);
        });
        chapterNode.appendChild(link);
      });
      list.appendChild(chapterNode);
    });
    group.appendChild(list);
    nav.appendChild(group);
  });
}

/** 侧边栏条目用的短标题：剥掉与章节名重复的前缀，保留「1/7」这类页码。 */
function shortLabel(chapterName, title) {
  const escaped = chapterName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = title.match(new RegExp(`^${escaped}\\s*[（(](\\d+/\\d+)[）)]\\s*(.*)$`));
  if (match) return `${match[1]} · ${match[2] || chapterName}`;
  if (chapterName && title.startsWith(chapterName)) {
    const rest = title.slice(chapterName.length).replace(/^[·—\-：:\s]+/, "");
    return rest || title;
  }
  return title;
}

function markActive(id) {
  document.querySelectorAll(".vault-link").forEach((link) => {
    link.classList.toggle("is-active", link.dataset.pageId === id);
  });
  const active = document.querySelector(".vault-link.is-active");
  if (!active) return;
  const activeGroup = active.closest(".vault-group");
  // 只展开当前所在的组，其余收起：120 个页面全平铺根本没法扫
  document.querySelectorAll("#vault-nav .vault-group").forEach((group) => {
    group.classList.toggle("is-collapsed", group !== activeGroup);
  });
  active.scrollIntoView({ block: "nearest" });
}

/* ---------- 打开页面 -------------------------------------------------------- */

async function openPage(id, options = {}) {
  const meta = state.order.find((page) => page.id === id);
  if (!meta) return;
  state.current = id;
  localStorage.setItem(LAST_PAGE_KEY, id);

  const article = $("#vault-article");
  article.innerHTML = '<p class="vault-loading">正在解密…</p>';
  let html;
  try {
    html = await fetchDecrypt(state.key, `pages/${id}.bin`);
  } catch (error) {
    article.innerHTML = `<p class="vault-error">解密失败：${error.message}</p>`;
    return;
  }

  article.innerHTML = html;
  article.querySelectorAll('a[href^="#p="]').forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      openPage(link.getAttribute("href").slice(3));
    });
  });
  markQuestions(article);
  markLabels(article);
  addCopyButtons(article);
  buildToc(article);
  renderPrevNext(meta);
  markActive(id);
  updateProgress();

  $("#vault-title").textContent = meta.title;
  $("#vault-crumb").textContent = `${meta.group} · ${meta.chapter}`;
  history.replaceState(null, "", `#p=${id}`);
  if (options.keepScroll !== true) window.scrollTo({ top: 0, behavior: "auto" });
  closeSearch();
}

function renderPrevNext(meta) {
  const index = state.order.findIndex((page) => page.id === meta.id);
  const prev = state.order[index - 1];
  const next = state.order[index + 1];
  const box = $("#vault-pager");
  box.textContent = "";
  const make = (page, label) => {
    if (!page) return el("span");
    const link = el("a", "vault-pager__item", `${label} ${page.title}`);
    link.href = `#p=${page.id}`;
    link.addEventListener("click", (event) => {
      event.preventDefault();
      openPage(page.id);
    });
    return link;
  };
  box.appendChild(make(prev, "←"));
  box.appendChild(make(next, "→"));
}

function addCopyButtons(article) {
  article.querySelectorAll("pre > code").forEach((code) => {
    const button = el("button", "vault-copy", "复制");
    button.addEventListener("click", async () => {
      await navigator.clipboard.writeText(code.textContent);
      button.textContent = "已复制";
      setTimeout(() => { button.textContent = "复制"; }, 1200);
    });
    (code.parentElement).appendChild(button);
  });
}

function buildToc(article) {
  const toc = $("#vault-toc");
  toc.textContent = "";
  article.querySelectorAll("h2, h3").forEach((heading, index) => {
    if (!heading.id) heading.id = `vault-h-${index}`;
    const link = el("a", `vault-toc__item vault-toc__item--h${heading.tagName[1]}`,
      heading.textContent.replace(/¶/g, "").trim());
    link.href = `#${heading.id}`;
    link.addEventListener("click", (event) => {
      event.preventDefault();
      heading.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    toc.appendChild(link);
  });
  $("#vault-toc-box").hidden = toc.childElementCount === 0;
}

/* ---------- 检索 ------------------------------------------------------------ */

async function ensureSearch() {
  if (state.search) return state.search;
  $("#search-status").textContent = "首次检索需要解密索引…";
  state.search = JSON.parse(await fetchDecrypt(state.key, "search.bin"));
  $("#search-status").textContent = "";
  return state.search;
}

function score(page, needle) {
  let best = 0;
  const hit = (texts, weight) => {
    for (const text of texts) {
      const position = (text || "").toLowerCase().indexOf(needle);
      if (position >= 0) best = Math.max(best, weight - Math.min(position / 100, 5));
    }
  };
  hit(page.q, 100);
  hit([page.t], 60);
  hit(page.h, 40);
  hit([page.c], 20);
  return best;
}

async function runSearch(query) {
  const needle = query.trim().toLowerCase();
  const box = $("#vault-results");
  box.textContent = "";
  if (needle.length < 1) { box.hidden = true; return; }

  const index = await ensureSearch();
  const hits = index
    .map((page) => ({ page, value: score(page, needle) }))
    .filter((item) => item.value > 0)
    .sort((a, b) => b.value - a.value)
    .slice(0, 40);

  if (!hits.length) {
    box.appendChild(el("div", "vault-result vault-result--empty", "没有匹配"));
    box.hidden = false;
    return;
  }

  hits.forEach(({ page }) => {
    const item = el("a", "vault-result");
    item.href = `#p=${page.id}`;
    item.appendChild(el("div", "vault-result__title", page.t));
    const question = page.q.find((text) => text.toLowerCase().includes(needle));
    item.appendChild(el("div", "vault-result__meta",
      question ? question.slice(0, 90) : `${page.g} · ${page.c}`));
    item.addEventListener("click", (event) => {
      event.preventDefault();
      openPage(page.id);
      $("#vault-search").value = "";
      box.hidden = true;
    });
    box.appendChild(item);
  });
  box.hidden = false;
}

function bindSearch() {
  const input = $("#vault-search");
  if (input.dataset.bound) return;
  input.dataset.bound = "1";

  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => runSearch(input.value), 120);
  });
  input.addEventListener("keydown", (event) => {
    const results = [...document.querySelectorAll(".vault-result:not(.vault-result--empty)")];
    if (!results.length) return;
    const active = results.findIndex((node) => node.classList.contains("is-active"));
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const next = event.key === "ArrowDown"
        ? Math.min(active + 1, results.length - 1)
        : Math.max(active - 1, 0);
      results.forEach((node) => node.classList.remove("is-active"));
      results[next].classList.add("is-active");
      results[next].scrollIntoView({ block: "nearest" });
    } else if (event.key === "Enter") {
      event.preventDefault();
      (results[active >= 0 ? active : 0]).click();
    } else if (event.key === "Escape") {
      closeSearch();
    }
  });
}

function closeSearch() {
  $("#vault-results").hidden = true;
  $("#vault-search").value = "";
}

/* ---------- 阅读体验：题目标记、自测模式、字号、筛选 ------------------------ */

const QUIZ_KEY = "vault.quiz";
const FONT_KEY = "vault.font";
const QUESTION_HEAD = /^Q\d+\s*[.．、:：]/;

// 这些粗体标签会被渲染成色块，扫读时一眼能找到每一节的分界
const LABELS = new Set([
  "一句话回答", "一句话结论", "展开", "原理", "项目落地", "常见追问", "来源", "参考",
  "经典资料怎么讲", "我们的差距", "面试怎么答", "容易答错的边界",
  "边界条件", "取舍", "延伸", "怎么答",
]);

/** 把「### Qn. 题面」和它后面的正文分别包成 .vault-question / .vault-answer。
 *
 * 为什么要包：自测模式要把答案整块藏起来，只留题目；不先分组就只能按行藏，很难看。
 * 同时给题目加 #q-序号 锚点，方便直接跳到某道题。
 */
function markQuestions(article) {
  [...article.children].forEach((node) => {
    if (node.tagName !== "H3") return;
    const text = node.textContent.trim();
    if (!QUESTION_HEAD.test(text)) return;

    node.classList.add("vault-question");
    const number = text.match(/^Q(\d+)/);
    if (number) node.id = `q-${number[1]}`;

    const following = [];
    let cursor = node.nextElementSibling;
    while (cursor && cursor.tagName !== "H3" && cursor.tagName !== "H2") {
      following.push(cursor);
      cursor = cursor.nextElementSibling;
    }
    if (!following.length) return;

    const body = document.createElement("div");
    body.className = "vault-answer";
    node.after(body);
    following.forEach((item) => body.appendChild(item));

    node.addEventListener("click", () => {
      if (!document.documentElement.classList.contains("vault-quiz")) return;
      const open = body.classList.toggle("is-open");
      node.classList.toggle("is-open", open);
    });
  });
}

function markLabels(article) {
  article.querySelectorAll("p > strong:first-child, li > strong:first-child").forEach((strong) => {
    const text = strong.textContent.trim().replace(/[:：]\s*$/, "");
    if (LABELS.has(text)) strong.classList.add("vault-label");
  });
}

function applyQuiz(on) {
  document.documentElement.classList.toggle("vault-quiz", on);
  localStorage.setItem(QUIZ_KEY, on ? "1" : "0");
  const button = $("#vault-quiz");
  if (!button) return;
  button.classList.toggle("is-on", on);
  button.textContent = on ? "自测中" : "自测";
}

function applyFont(level) {
  const safe = [1, 2, 3].includes(level) ? level : 2;
  document.documentElement.classList.remove("vault-fs-1", "vault-fs-2", "vault-fs-3");
  document.documentElement.classList.add(`vault-fs-${safe}`);
  localStorage.setItem(FONT_KEY, String(safe));
  const button = $("#vault-font");
  if (!button) return;
  button.textContent = `字号 ${["小", "中", "大"][safe - 1]}`;
  button.classList.toggle("is-on", safe !== 2);
}

function cycleFont(step) {
  const current = Number(localStorage.getItem(FONT_KEY)) || 2;
  const next = Math.min(3, Math.max(1, current + step));
  applyFont(next);
}

function bindReadingUI() {
  applyQuiz(localStorage.getItem(QUIZ_KEY) === "1");
  applyFont(Number(localStorage.getItem(FONT_KEY)) || 2);

  $("#vault-quiz")?.addEventListener("click", () => {
    applyQuiz(!document.documentElement.classList.contains("vault-quiz"));
  });
  $("#vault-font")?.addEventListener("click", () => {
    const current = Number(localStorage.getItem(FONT_KEY)) || 2;
    applyFont(current >= 3 ? 1 : current + 1);
  });
  $("#vault-side-toggle")?.addEventListener("click", () => {
    document.documentElement.classList.toggle("vault-side-open");
  });

  // 回到顶部
  const top = $("#vault-top");
  if (top) {
    top.hidden = false;
    top.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
    window.addEventListener("scroll", () => {
      top.classList.toggle("is-shown", window.scrollY > 500);
    }, { passive: true });
  }

  // 侧边栏筛选：命中就留着，没命中的组整组隐藏
  $("#vault-filter")?.addEventListener("input", (event) => {
    const needle = event.target.value.trim().toLowerCase();
    document.querySelectorAll("#vault-nav .vault-group").forEach((group) => {
      let visible = 0;
      group.querySelectorAll(".vault-link").forEach((link) => {
        const hit = !needle || link.textContent.toLowerCase().includes(needle);
        link.hidden = !hit;
        if (hit) visible += 1;
      });
      group.querySelectorAll(".vault-chapter").forEach((chapter) => {
        chapter.hidden = ![...chapter.querySelectorAll(".vault-link")].some((l) => !l.hidden);
      });
      group.classList.toggle("is-hidden", visible === 0);
      if (needle) group.classList.remove("is-collapsed");
    });
  });
}

/* ---------- 阅读进度 & 快捷键 ---------------------------------------------- */

function updateProgress() {
  const max = document.documentElement.scrollHeight - window.innerHeight;
  const ratio = max > 0 ? window.scrollY / max : 0;
  $("#vault-progress").style.transform = `scaleX(${Math.min(Math.max(ratio, 0), 1)})`;
}

function bindKeys() {
  document.addEventListener("keydown", (event) => {
    const typing = /^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName);
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      $("#vault-search").focus();
      return;
    }
    if (typing) return;
    if (event.key === "/") {
      event.preventDefault();
      $("#vault-search").focus();
    } else if (event.key === "[") {
      document.querySelectorAll("#vault-pager a")[0]?.click();
    } else if (event.key === "]") {
      document.querySelectorAll("#vault-pager a")[1]?.click();
    } else if (event.key.toLowerCase() === "t") {
      applyQuiz(!document.documentElement.classList.contains("vault-quiz"));
    } else if (event.key === "+" || event.key === "=") {
      cycleFont(1);
    } else if (event.key === "-" || event.key === "_") {
      cycleFont(-1);
    } else if (event.key === "Escape") {
      closeSearch();
    }
  });
}

/* ---------- 启动 ------------------------------------------------------------ */

/**
 * 初始化专区。
 *
 * 必须是"可重复调用、且对同一份 DOM 只生效一次"：
 * mkdocs-material 开了 navigation.instant，点导航进来时不会重新执行页面里的 module 脚本，
 * 所以由全局的 extra.js 在检测到 #vault 出现时调用本函数（见 docs/assets/extra.js）。
 * DOM 被换掉后 dataset 标记自然消失，于是下次进入会重新初始化。
 */
export async function bootVault() {
  const vault = document.getElementById("vault");
  if (!vault || vault.dataset.booted === "1") return;
  vault.dataset.booted = "1";

  state.key = null;
  state.structure = [];
  state.search = null;
  state.current = null;

  $("#vault-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = $("#vault-passphrase").value.trim();
    if (!value) return;
    try {
      await unlock(value);
    } catch (error) {
      renderGate(`出错了：${error.message}`);
    }
  });

  $("#vault-lock").addEventListener("click", () => {
    // 临时锁定：清掉本标签页的密钥，并且本次会话不再自动解锁
    sessionStorage.removeItem(SESSION_KEY);
    sessionStorage.setItem(SKIP_AUTO_KEY, "1");
    location.reload();
  });

  $("#vault-forget").addEventListener("click", () => {
    forgetRememberedKey();
    location.reload();
  });

  window.addEventListener("scroll", updateProgress, { passive: true });
  window.addEventListener("hashchange", () => {
    const match = location.hash.match(/^#p=([0-9a-f]+)$/);
    if (match && match[1] !== state.current) openPage(match[1]);
  });
  bindKeys();
  bindReadingUI();

  if (sessionStorage.getItem(SKIP_AUTO_KEY)) {
    return renderGate("已锁定：本次会话不再自动解锁，输入口令即可重新进入。");
  }

  // 按「本标签页的密钥 → 本机记住的密钥」的顺序尝试自动解锁
  const enter = async (key) => {
    state.key = key;
    await loadConfig();          // 先拿到本次构建号，再解密，避免缓存错配
    state.structure = JSON.parse(await fetchDecrypt(key, "index.bin"));
    await enterApp();
  };

  const sessionKey = sessionStorage.getItem(SESSION_KEY);
  if (sessionKey) {
    try {
      return await enter(await importKey(sessionKey));
    } catch {
      sessionStorage.removeItem(SESSION_KEY);
    }
  }

  const hadRemembered = !!localStorage.getItem(REMEMBER_KEY);
  try {
    const remembered = await loadRememberedKey();
    if (remembered) {
      $("#vault-status").textContent = "已使用本机保存的密钥解锁…";
      return await enter(remembered);
    }
  } catch {
    localStorage.removeItem(REMEMBER_KEY);
  }

  renderGate(hadRemembered
    ? "本机保存的密钥已失效（可能是清过浏览数据），请重新输入口令。"
    : "请输入口令；勾选「记住半年」后，这台设备上就不会再提示了。");
}

bootVault();
