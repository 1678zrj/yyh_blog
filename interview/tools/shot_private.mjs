/**
 * 给私密专区截图，用来**肉眼评估阅读体验**（不靠猜 CSS）。
 *
 * 做法与 e2e_private.mjs 相同：生成一个注入口令的临时页 → 无头 Chrome 打开 → 自动解锁
 * → 用 CDP 截多张图（桌面/移动、目录页/正文页、浅色/深色）→ 存到 .build-cache/shots/。
 *
 * 用法：
 *   1) .venv\Scripts\python.exe -m http.server 8099 --directory site --bind 127.0.0.1
 *   2) node interview/tools/shot_private.mjs [baseUrl] [输出目录]
 */

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const baseUrl = (process.argv[2] || "http://127.0.0.1:8099").replace(/\/$/, "");
const outDir = process.argv[3] || join(root, ".build-cache", "shots");
const chromePath =
  process.env.CHROME || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const port = 9500 + Math.floor(Math.random() * 300);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

mkdirSync(outDir, { recursive: true });

/* ---------- 1. 生成注入口令的临时页 ---------- */
const siteHtml = join(root, "site", "private", "index.html");
if (!existsSync(siteHtml)) {
  console.error("找不到 site/private/index.html，请先执行 mkdocs build");
  process.exit(1);
}
const passphrase = readFileSync(join(root, "interview", ".passphrase"), "utf8").trim();

const html = readFileSync(siteHtml, "utf8").replace(
  "</body>",
  `<script>
const PASS = ${JSON.stringify(passphrase)};
let tries = 0;
// 顺便测一下「自测模式」是否生效：把答案折叠起来，看正文是不是真的只剩问题
const params = new URLSearchParams(location.search);
setInterval(() => {
  tries += 1;
  const gate = document.getElementById("vault-gate");
  const input = document.getElementById("vault-passphrase");
  if (!gate || !input) return;
  const shown = !gate.hidden && getComputedStyle(gate).display !== "none";
  if (shown && tries > 4) {
    input.value = PASS;
    const box = document.getElementById("vault-remember");
    if (box) box.checked = true;
    document.getElementById("vault-form").dispatchEvent(
      new Event("submit", { cancelable: true, bubbles: true }));
    return;
  }
  const app = document.getElementById("vault-app");
  if (app && !app.hidden) {
    // 解锁成功后：可以按 ?p=<页面id> 直接开某页，或按 ?text=<章节关键词> 点侧边栏
    const want = params.get("p");
    if (want && location.hash !== "#p=" + want) { location.hash = "#p=" + want; return; }
    const text = params.get("text");
    if (text && !window.__picked) {
      // 侧边栏显示的是短标题，全名在 title 属性里，两个都匹配
      const link = [...document.querySelectorAll(".vault-link")]
        .find((item) => (item.textContent + " " + (item.title || "")).includes(text));
      if (link) { window.__picked = 1; link.click(); return; }
    }
    if (params.get("quiz") === "1" && !document.documentElement.classList.contains("vault-quiz")) {
      document.getElementById("vault-quiz")?.click();
    }
    document.documentElement.dataset.shotReady = "1";
  }
}, 400);
</script></body>`,
);
writeFileSync(join(root, "site", "private", "_shot.html"), html, "utf8");

const pageUrl = `${baseUrl}/private/_shot.html` + (process.argv[4] ? `?${process.argv[4]}` : "");
const profile = mkdtempSync(join(tmpdir(), "vault-shot-"));
const chrome = spawn(
  chromePath,
  ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
   "--hide-scrollbars", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
   "--window-size=1440,980", pageUrl],
  { stdio: "ignore" },
);

let socket;
let messageId = 0;
const waiting = new Map();
const send = (method, params = {}) =>
  new Promise((resolve, reject) => {
    const id = (messageId += 1);
    waiting.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });

async function shot(name) {
  const { data } = await send("Page.captureScreenshot", { format: "png" });
  const path = join(outDir, `${name}.png`);
  writeFileSync(path, Buffer.from(data, "base64"));
  console.log(`  已截图 ${name}.png`);
  return path;
}

function cleanup(code) {
  try { socket?.close(); } catch {}
  try { chrome.kill(); } catch {}
  try { rmSync(profile, { recursive: true, force: true }); } catch {}
  try { rmSync(join(root, "site", "private", "_shot.html"), { force: true }); } catch {}
  process.exit(code);
}

async function main() {
  let target = null;
  for (let i = 0; i < 60 && !target; i += 1) {
    await sleep(500);
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      target = list.find((item) => item.type === "page");
    } catch {}
  }
  if (!target) throw new Error("连不上 Chrome 调试端口");

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

  await send("Page.enable");
  await send("Runtime.enable");

  // 等解锁完成
  for (let i = 0; i < 60; i += 1) {
    await sleep(500);
    const r = await send("Runtime.evaluate", {
      expression: "document.documentElement.dataset.shotReady === '1'", returnByValue: true });
    if (r.result?.value) break;
  }
  await sleep(1200);

  const metrics = (width, height) =>
    send("Emulation.setDeviceMetricsOverride",
         { width, height, deviceScaleFactor: 1, mobile: width < 700 });

  console.log("桌面 1440×980：");
  await metrics(1440, 980);
  await sleep(400);
  await shot("desktop-content");

  // 滚到正文中段，看长文排版
  await send("Runtime.evaluate", { expression: "window.scrollTo(0, 1600)" });
  await sleep(500);
  await shot("desktop-content-scrolled");

  // 回到顶部，看侧边栏 + 目录
  await send("Runtime.evaluate", { expression: "window.scrollTo(0, 0)" });
  await sleep(300);

  // 深色模式：Material 靠 <body data-md-color-scheme="slate"> 切换，不是媒体查询
  await send("Runtime.evaluate", {
    expression: "document.body.setAttribute('data-md-color-scheme','slate')" });
  await sleep(700);
  await shot("desktop-dark");
  await send("Runtime.evaluate", {
    expression: "document.body.removeAttribute('data-md-color-scheme')" });
  await sleep(300);

  console.log("移动 390×844：");
  await metrics(390, 844);
  await sleep(600);
  await shot("mobile-content");

  cleanup(0);
}

main().catch((error) => {
  console.error("截图失败：", error.message);
  cleanup(1);
});
