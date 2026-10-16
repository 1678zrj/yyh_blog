/**
 * 私密笔记专区的端到端测试：用真实的 Chrome 打开页面、自动填口令解锁，
 * 然后检查「侧边栏 / 正文解密 / 检索 / 内部跳转 / 目录」是否都正常。
 *
 * 做法：先按构建产物生成一个临时测试页（把口令注入进去），
 * 再用 CDP（DevTools 协议）驱动无头 Chrome，等页面自己跑完再读结果。
 *
 * 用法：
 *   1. 先起静态服务： .venv\\Scripts\\python.exe -m http.server 8099 --directory site --bind 127.0.0.1
 *   2. 再跑测试：     node interview/tools/e2e_private.mjs [baseUrl]
 */

import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const baseUrl = (process.argv[2] || "http://127.0.0.1:8099").replace(/\/$/, "");
const pageUrl = `${baseUrl}/private/_test.html`;
const chromePath =
  process.env.CHROME || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const port = 9333 + Math.floor(Math.random() * 200);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/* ---------- 1. 生成临时测试页 ---------- */

const siteHtml = join(root, "site", "private", "index.html");
if (!existsSync(siteHtml)) {
  console.error("找不到 site/private/index.html，请先执行 mkdocs build");
  process.exit(1);
}
const passphrase = readFileSync(join(root, "interview", ".passphrase"), "utf8").trim();

const injected = `
const PASS = ${JSON.stringify(passphrase)};
let tries = 0;
let typed = 0;
let gateVisible = 0;
let searching = false;
function report(text) {
  let box = document.getElementById("test-result");
  if (!box) { box = document.createElement("div"); box.id = "test-result"; document.body.appendChild(box); }
  box.textContent = text;
}
window.addEventListener("error", (e) => report("FAIL:js-error|" + (e.message || "")));

// 解锁框是否真的呈现给了用户（display:none 也算没显示）
function gateShown() {
  const gate = document.getElementById("vault-gate");
  if (!gate || gate.hidden) return false;
  return getComputedStyle(gate).display !== "none";
}

setInterval(() => {
  tries += 1;
  const gate = document.getElementById("vault-gate");
  const input = document.getElementById("vault-passphrase");
  if (!gate || !input) { if (tries > 200) report("FAIL:no-gate"); return; }

  if (gateShown()) {
    gateVisible = 1;
    // 前 1.5 秒先别输：留时间给「本机记住的密钥」自动解锁，否则测不出效果
    if (tries < 5) return;
    typed += 1;
    input.value = PASS;
    const box = document.getElementById("vault-remember");
    if (box) box.checked = true;
    document.getElementById("vault-form")
      .dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
    return;
  }

  const article = document.getElementById("vault-article");
  const nav = document.getElementById("vault-nav");
  const chars = article ? article.textContent.trim().length : -1;
  const links = nav ? nav.querySelectorAll("a").length : 0;
  if (chars < 500 || links === 0) {
    if (tries > 200) report("FAIL:empty|chars=" + chars + "|links=" + links);
    return;
  }

  if (!searching) {
    searching = true;
    const box = document.getElementById("vault-search");
    box.value = "锁";
    box.dispatchEvent(new Event("input", { bubbles: true }));
    // 轮询等结果，而不是固定等 3 秒：首次打开时检索索引还要解密，
    // 固定等待会误报"检索命中 0 条"（其实只是还没解完）
    let waited = 0;
    const timer = setInterval(() => {
      waited += 1;
      const results = document.getElementById("vault-results");
      const ready = results && results.querySelectorAll(".vault-result").length > 0;
      if (!ready && waited < 20) return;
      clearInterval(timer);
      const hits = document.querySelectorAll(".vault-result:not(.vault-result--empty)").length;
      const internal = article.querySelectorAll('a[href^="#p="]').length;
      const toc = document.querySelectorAll("#vault-toc a").length;
      const copies = article.querySelectorAll(".vault-copy").length;
      const code = article.querySelectorAll("pre code").length;
      const remembered = !!localStorage.getItem("vault.remember");
      // 静态资源是否真的带上了构建号（?b=xxx）：这是避免缓存错配的关键
      const tagged = performance.getEntriesByType("resource")
        .filter((entry) => /[?&]b=[0-9a-f]+/.test(entry.name)).length;
      report("PASS|chars=" + chars + "|navLinks=" + links + "|searchHits=" + hits +
             "|internalLinks=" + internal + "|toc=" + toc + "|copyButtons=" + copies +
             "|codeBlocks=" + code + "|typed=" + typed + "|gateVisible=" + gateVisible +
             "|remembered=" + remembered + "|buildTag=" + tagged);
    }, 500);
  }
}, 300);
`;

writeFileSync(join(root, "site", "private", "_inject.js"), injected, "utf8");
const html = readFileSync(siteHtml, "utf8").replace(
  "</body>",
  '<script type="module" src="./_inject.js"></script></body>',
);
writeFileSync(join(root, "site", "private", "_test.html"), html, "utf8");

// 第二份测试页：故意去掉页面自带的 module 脚本，模拟 mkdocs-material「无刷新换页」
// 进入专区的情况——此时只能靠全局 extra.js 兜底启动。
const noScript = readFileSync(siteHtml, "utf8")
  .replace('<script type="module" src="../assets/private.js"></script>', "")
  .replace("</body>", '<script type="module" src="./_inject.js"></script></body>');
writeFileSync(join(root, "site", "private", "_test2.html"), noScript, "utf8");

/* ---------- 2. 启动无头 Chrome ---------- */

const profile = mkdtempSync(join(tmpdir(), "vault-e2e-"));
const chrome = spawn(
  chromePath,
  [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    "--window-size=1400,1000",
    pageUrl,
  ],
  { stdio: "ignore" },
);

let socket;
let messageId = 0;
const waiting = new Map();

function cleanup(code) {
  try { socket?.close(); } catch {}
  try { chrome.kill(); } catch {}
  try { rmSync(profile, { recursive: true, force: true }); } catch {}
  try { rmSync(join(root, "site", "private", "_test.html"), { force: true }); } catch {}
  try { rmSync(join(root, "site", "private", "_test2.html"), { force: true }); } catch {}
  try { rmSync(join(root, "site", "private", "_inject.js"), { force: true }); } catch {}
  process.exit(code);
}

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = (messageId += 1);
    waiting.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function main() {
  // 等 DevTools 端口起来
  let target = null;
  for (let i = 0; i < 60 && !target; i += 1) {
    await sleep(500);
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      target = list.find((item) => item.type === "page" && item.url.includes("_test.html"));
    } catch {}
  }
  if (!target) throw new Error("连不上 Chrome 的调试端口");

  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve);
    socket.addEventListener("error", reject);
  });
  socket.addEventListener("message", (event) => {
    const data = JSON.parse(event.data);
    const entry = waiting.get(data.id);
    if (!entry) return;
    waiting.delete(data.id);
    data.error ? entry.reject(new Error(data.error.message)) : entry.resolve(data.result);
  });

  await send("Runtime.enable");

  // 轮询测试结果
  let result = "";
  for (let i = 0; i < 90; i += 1) {
    await sleep(1000);
    const response = await send("Runtime.evaluate", {
      expression: "document.getElementById('test-result')?.textContent || ''",
      returnByValue: true,
    });
    result = response.result?.value || "";
    if (result.startsWith("PASS") || result.startsWith("FAIL")) break;
  }

  if (!result) {
    console.log("❌ 超时：页面没有给出结果（可能卡在派生密钥或解密）");
    return cleanup(1);
  }

  const parts = Object.fromEntries(
    result.split("|").slice(1).map((item) => {
      const [key, value] = item.split("=");
      return [key, value];
    }),
  );

  if (result.startsWith("PASS")) {
    console.log("✅ 第一次解锁（手动输入口令）");
    console.log(`   解密正文 ${parts.chars} 字 · 侧边栏 ${parts.navLinks} 个入口`);
    console.log(`   检索命中 ${parts.searchHits} 条 · 站内跳转 ${parts.internalLinks} 个 · 本页目录 ${parts.toc} 项`);
    console.log(`   代码块 ${parts.codeBlocks} 个（复制按钮 ${parts.copyButtons} 个）`);
    console.log(`   本机记住密钥：${parts.remembered === "true" ? "已保存" : "未保存"}`);
    console.log(`   带构建号的加密资源：${parts.buildTag ?? 0} 个${Number(parts.buildTag) > 0 ? "" : "  ❌ 缓存错配防护没生效"}`);

    // 第二次：刷新页面，应当自动解锁，不需要再输口令
    await send("Page.enable");
    await send("Page.reload", { ignoreCache: true });
    let second = "";
    for (let i = 0; i < 60; i += 1) {
      await sleep(1000);
      const response = await send("Runtime.evaluate", {
        expression: "document.getElementById('test-result')?.textContent || ''",
        returnByValue: true,
      });
      second = response.result?.value || "";
      if (second.startsWith("PASS") || second.startsWith("FAIL")) break;
    }
    const secondParts = Object.fromEntries(
      second.split("|").slice(1).map((item) => {
        const [key, value] = item.split("=");
        return [key, value];
      }),
    );
    if (second.startsWith("PASS") && secondParts.typed === "0" && secondParts.gateVisible === "0") {
      console.log("✅ 刷新后自动解锁：解锁框全程没有出现（不再有「又要输口令」的错觉）");
    } else {
      console.log(`❌ 刷新后仍显示解锁框（typed=${secondParts.typed ?? "?"}, gateVisible=${secondParts.gateVisible ?? "?"}）：${second.slice(0, 80)}`);
      return cleanup(1);
    }

    // 第三次：不带页面自带脚本的版本，模拟无刷新换页进入
    await send("Page.navigate", { url: `${baseUrl}/private/_test2.html` });
    let third = "";
    for (let i = 0; i < 60; i += 1) {
      await sleep(1000);
      const response = await send("Runtime.evaluate", {
        expression: "document.getElementById('test-result')?.textContent || ''",
        returnByValue: true,
      });
      third = response.result?.value || "";
      if (third.startsWith("PASS") || third.startsWith("FAIL")) break;
    }
    const thirdParts = Object.fromEntries(
      third.split("|").slice(1).map((item) => {
        const [key, value] = item.split("=");
        return [key, value];
      }),
    );
    if (third.startsWith("PASS") && thirdParts.typed === "0" && thirdParts.gateVisible === "0") {
      console.log("✅ 无刷换页场景（无页面脚本，靠全局脚本启动）也自动解锁、不显示解锁框");
      return cleanup(0);
    }
    console.log(`❌ 无刷换页场景失败（typed=${thirdParts.typed ?? "?"}, gateVisible=${thirdParts.gateVisible ?? "?"}）：${third.slice(0, 80)}`);
    return cleanup(1);
  }
  console.log(`❌ 测试失败：${result}`);
  return cleanup(1);
}

main().catch((error) => {
  console.log(`❌ 测试异常：${error.message}`);
  cleanup(1);
});
