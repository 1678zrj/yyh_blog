/** 专用探针：在真实浏览器里测专区的检索交互，并把页面上的 JS 错误抓出来。 */
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const baseUrl = (process.argv[2] || "http://127.0.0.1:8099").replace(/\/$/, "");
const chromePath = process.env.CHROME || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const port = 9700 + Math.floor(Math.random() * 200);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const passphrase = readFileSync(join(root, "interview", ".passphrase"), "utf8").trim();
const source = join(root, "site", "private", "index.html");
if (!existsSync(source)) { console.error("先执行 mkdocs build"); process.exit(1); }

const html = readFileSync(source, "utf8").replace("</body>", `<script>
window.__errors = [];
window.addEventListener("error", (e) => window.__errors.push(String(e.message)));
const PASS = ${JSON.stringify(passphrase)};
let tries = 0;
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
    document.getElementById("vault-form").dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
    return;
  }
  const app = document.getElementById("vault-app");
  if (app && !app.hidden) document.documentElement.dataset.ready = "1";
}, 400);
</script></body>`);
writeFileSync(join(root, "site", "private", "_probe.html"), html, "utf8");

const profile = mkdtempSync(join(tmpdir(), "vault-probe-"));
const chrome = spawn(chromePath, ["--headless=new", "--disable-gpu", "--no-first-run",
  "--no-default-browser-check", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  "--window-size=1440,900", `${baseUrl}/private/_probe.html`], { stdio: "ignore" });

let socket, id = 0;
const waiting = new Map();
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const current = (id += 1);
  waiting.set(current, { resolve, reject });
  socket.send(JSON.stringify({ id: current, method, params }));
});
const evaluate = async (expression) =>
  (await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true })).result?.value;

function cleanup(code) {
  try { socket?.close(); } catch {}
  try { chrome.kill(); } catch {}
  try { rmSync(profile, { recursive: true, force: true }); } catch {}
  try { rmSync(join(root, "site", "private", "_probe.html"), { force: true }); } catch {}
  process.exit(code);
}

try {
  let target = null;
  for (let i = 0; i < 60 && !target; i += 1) {
    await sleep(500);
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      target = list.find((x) => x.type === "page");
    } catch {}
  }
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { socket.addEventListener("open", res); socket.addEventListener("error", rej); });
  socket.addEventListener("message", (e) => {
    const data = JSON.parse(e.data);
    const entry = waiting.get(data.id);
    if (!entry) return;
    waiting.delete(data.id);
    data.error ? entry.reject(new Error(data.error.message)) : entry.resolve(data.result);
  });
  await send("Page.enable"); await send("Runtime.enable");

  for (let i = 0; i < 60; i += 1) {
    await sleep(500);
    if (await evaluate("document.documentElement.dataset.ready === '1'")) break;
  }

  console.log("已解锁，开始测检索…");
  const report = await evaluate(`(async () => {
    const box = document.getElementById("vault-search");
    box.value = "锁";
    box.dispatchEvent(new Event("input", { bubbles: true }));
    await new Promise(r => setTimeout(r, 2500));
    const results = document.getElementById("vault-results");
    return JSON.stringify({
      errorCount: window.__errors.length,
      errors: window.__errors.slice(0, 3),
      value: box.value,
      hidden: results.hidden,
      childCount: results.children.length,
      itemCount: results.querySelectorAll(".vault-result").length,
      realCount: results.querySelectorAll(".vault-result:not(.vault-result--empty)").length,
      firstText: results.textContent.slice(0, 60),
      status: document.getElementById("search-status").textContent,
      filterExists: !!document.getElementById("vault-filter"),
    });
  })()`);
  console.log(report);
  cleanup(0);
} catch (error) {
  console.error("探针失败：", error.message);
  cleanup(1);
}
