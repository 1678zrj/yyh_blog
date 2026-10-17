# 面试八股工具链

这里是从「笔记 + 网络语料 → 加密专区」的完整流水线。**内容本身不在这里**（`interview/` 下除 `tools/` 外都被 `.gitignore` 排除，只有工具进版本库）。

## 一、日常最常用的四个命令

```powershell
# 1) 合并小节 → 专题文档，并生成总索引与自测清单
.venv\Scripts\python.exe interview\tools\build_interview.py

# 2) 加密成密文（docs/assets/private/）
.venv\Scripts\python.exe interview\tools\build_private.py

# 3) 构建站点
.venv\Scripts\python.exe -m mkdocs build

# 4) 端到端验证（需先起静态服务，见文件末尾）
node interview\tools\e2e_private.mjs http://127.0.0.1:8099
```

## 二、内容生产：抓语料

| 脚本 | 作用 |
| --- | --- |
| `crawl_sources.py` | 从 GitHub 仓库抓八股语料（部分克隆 + **raw/API/tarball 三条通道**兜底，见下） |
| `crawl_sites.py` | 按 sitemap 抓静态站点（1 请求/秒，增量跳过已抓） |
| `diff_market.py` | 把「市面题库清单」与现有题库做差集（导出 `_owned/` 供代理逐题核对） |
| `market_report.py` | 生成覆盖率报告与题目地图 |

`crawl_sources.py` 的三条通道为什么要三条（都是踩过的坑）：

1. **git 通道**：`--filter=blob:none` + 稀疏检出，快；但 git 传输层偶发连不上（`Failed to connect to github.com port 443`），且仓库里只要有一个 Windows 非法文件名（如 `README.md ` 带尾部空格），整次 checkout 就 exit 128
2. **API + raw 通道**：走 GitHub API 列路径 + `raw.githubusercontent.com` 取文件，绕开 git；但未认证 API 只有 60 次/小时
3. **tarball 通道**：`codeload.github.com` 流式解压只取 markdown，不依赖 git 也不耗 API，代价是要整包下载

## 三、内容生产：改写与校验（重要）

改写一律**先写暂存文件**，再用闸门校验，通过了才替换：

```powershell
# 1) 代理只允许写 interview/.staging/<文件名>
# 2) 预览哪些能替换
.venv\Scripts\python.exe interview\tools\apply_staged.py --dry-run
# 3) 通过的全部替换（先自动快照到 interview/.snapshots/）
.venv\Scripts\python.exe interview\tools\apply_staged.py
```

| 脚本 | 作用 |
| --- | --- |
| `verify_rewrite.py` | **闸门**：条目数一致、核心标签齐全且顺序正确、字数不截断（<50%）、列表前有空行 |
| `apply_staged.py` | 逐份校验 → 快照正本 → 替换；不通过的原样保留 |
| `restore_from_vault.py` | **从加密专区把明文还原回 markdown**（曾靠它救回被代理清空的 16 题） |
| `check_shrink.py` | 判断「字数缩水」是真丢内容还是重组压缩（用地道术语做覆盖对比） |
| `normalize_lists.py` | 补「列表前缺空行」——Python-Markdown 的硬要求，否则渲染成一大段 |
| `md_fix.py` | 上面那条规则的库版本，已被 `build_interview.py` / `build_private.py` 内联调用 |
| `normalize_quotes.py` / `check_quotes.py` | 引号规范化与检查（中文文案里混用直引号会出渲染问题） |

**闸门支持的三种章节体裁**（`verify_rewrite.py` 自动识别）：

| 体裁 | 识别特征 | 必含标签 |
| --- | --- | --- |
| `qa` | `### Qn.` | 一句话回答 → 展开 → 常见追问 → 容易答错的边界 |
| `real`（大厂真题） | 有 `**核实与纠错**` | 一句话回答 → 为什么这么做 → 核实与纠错 → 常见追问 → 容易答错的边界 |
| `point`（经典加强） | `### 1.` 编号 | 经典资料怎么讲 → 我们的差距 → 面试怎么答 → 容易答错的边界 |

## 四、验证

| 脚本 | 作用 |
| --- | --- |
| `verify_private.mjs` | 跨实现解密：用 Node 的 WebCrypto 解 Python 加密的密文（能解开说明格式与密钥派生一致），并验证错误口令被拒 |
| `e2e_private.mjs` | **真实 Chrome 端到端**：注入临时页自动解锁 → 检查侧边栏/正文/检索/目录/自动解锁，三个阶段（手输、刷新、无刷换页） |
| `shot_private.mjs` | 截图工具（桌面/移动/深色/自测模式），用来**肉眼评估阅读体验**，别靠猜 CSS |
| `probe_search.mjs` | 专区的检索探针：打字、等结果、把页面 JS 错误抓出来 |
| `vault_status.py` | 解密目录，打印「线上到底有哪些章节、多少页、多少题」——防止"构建成功但内容没上去" |

起静态服务（`e2e_private.mjs` 与 `shot_private.mjs` 都依赖它）：

```powershell
.venv\Scripts\python.exe -m http.server 8099 --directory site --bind 127.0.0.1
```

## 五、写作规范（改内容前必读）

- `prompts/answer-depth.md` — **答案深度规范 v3**：原理驱动（第一性约束 → 推导 → 反事实）、硬核干货的五种形态、形象类比的三个硬标准、禁止四字口号
- `prompts/gap-research.md` — 联网补题的研究规范（不设总字数上限，按每题密度要求，**禁止为卡字数重写**）

## 六、几条踩坑记录（避免重复犯）

1. **代理直接改原文件**曾把 65 KB 的章节边长边清空到 12.9 KB → 此后一律走 `.staging/` + 闸门
2. **`interview/` 没有 git 版本**，所以改写前必须快照（`.snapshots/`）；真要丢了，用 `restore_from_vault.py` 从密文恢复
3. **Python-Markdown 要求列表前有空行**，否则 `- 要点` 会被并进上一段，页面上就是一大坨密排文字（全库曾有 1801 处）
4. **CSS 的 ID 选择器优先级高于 `hidden` 属性**：`#vault-gate { display:flex }` 会让 `hidden` 失效，解锁后登录卡片仍占满屏（已用 `#vault [hidden] { display:none !important }` 修掉）
5. **加密资源的缓存错配**：`config.json`（含 salt）与密文可能一新一旧 → 现在 `config.json` 走 `no-store`，其余资源带 `?b=<构建号>`
6. **KDF 的 salt 必须固定**：每次构建都换 salt 会让用户"记住的密钥"在每次内容更新后失效
