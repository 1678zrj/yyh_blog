/**
 * 跨实现校验：用 Node 的 WebCrypto 解密 Python 加密出来的密文。
 *
 * 浏览器里用的是同一套 WebCrypto API，所以这个脚本通过 = 浏览器也能解开。
 *
 * 用法（仓库根目录）：
 *     node interview/tools/verify_private.mjs
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { webcrypto } from "node:crypto";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..");
const outDir = join(root, "docs", "assets", "private");

const config = JSON.parse(readFileSync(join(outDir, "config.json"), "utf8"));
const passphrase = readFileSync(join(root, "interview", ".passphrase"), "utf8").trim();

const subtle = webcrypto.subtle;
const te = new TextEncoder();
const td = new TextDecoder();

async function deriveKey(passphrase, salt, iterations) {
  const material = await subtle.importKey("raw", te.encode(passphrase), "PBKDF2", false, [
    "deriveKey",
  ]);
  return subtle.deriveKey(
    { name: "PBKDF2", salt, iterations, hash: "SHA-256" },
    material,
    { name: "AES-GCM", length: 256 },
    false,
    ["decrypt"],
  );
}

async function decrypt(key, bytes) {
  const iv = bytes.subarray(0, 12);
  const data = bytes.subarray(12);
  const plain = await subtle.decrypt({ name: "AES-GCM", iv }, key, data);
  return td.decode(plain);
}

const salt = Buffer.from(config.salt, "base64");
console.log(`KDF: ${config.kdf} / ${config.iterations} 次 / salt ${salt.length} 字节`);
console.log(`加密时登记的页数: ${config.pages}`);

const key = await deriveKey(passphrase, salt, config.iterations);

// 1) 目录
const index = JSON.parse(await decrypt(key, readFileSync(join(outDir, "index.bin"))));
const pageCount = index.reduce((sum, chapter) => sum + chapter.pages.length, 0);
console.log(`\n目录解密成功：${index.length} 个专题 / ${pageCount} 页`);
for (const chapter of index.slice(0, 4)) {
  console.log(`  [${chapter.group}] ${chapter.chapter} —— ${chapter.pages.length} 页`);
}

// 2) 检索索引
const search = JSON.parse(await decrypt(key, readFileSync(join(outDir, "search.bin"))));
const questionCount = search.reduce((sum, page) => sum + page.q.length, 0);
console.log(`\n检索索引解密成功：${search.length} 页 / ${questionCount} 个问题`);

// 3) 抽查三个页面
console.log("\n页面抽查：");
for (const id of [search[0].id, search[Math.floor(search.length / 2)].id, search.at(-1).id]) {
  const html = await decrypt(key, readFileSync(join(outDir, "pages", `${id}.bin`)));
  const title =
    (/<h[12][^>]*>(.*?)<\/h[12]>/s.exec(html)?.[1] ?? "(无标题)")
      .replace(/<[^>]+>/g, "")
      .trim();
  console.log(`  ${id}  ${String(html.length).padStart(7)} 字节  ${title.slice(0, 40)}`);
}

// 4) 内部跳转链接改写是否生效
const html = await decrypt(key, readFileSync(join(outDir, "pages", `${search[0].id}.bin`)));
const internal = (html.match(/#p=[0-9a-f]{16}/g) ?? []).length;
const leftover = (html.match(/href="[^"]*\.md[^"]*"/g) ?? []).length;
console.log(`\n内部跳转：#p= 链接 ${internal} 个，残留 .md 链接 ${leftover} 个`);

// 5) 错误口令必须解不开
let rejected = false;
try {
  const wrong = await deriveKey("definitely-not-the-passphrase", salt, config.iterations);
  await decrypt(wrong, readFileSync(join(outDir, "index.bin")));
} catch {
  rejected = true;
}
console.log(`错误口令被拒绝: ${rejected ? "是 ✅" : "否 ❌（有严重问题）"}`);

const ok = rejected && pageCount === config.pages && pageCount > 0;
console.log(`\n结论：${ok ? "✅ 浏览器端可以正常解密" : "❌ 校验未通过"}`);
process.exit(ok ? 0 : 1);
