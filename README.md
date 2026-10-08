# yyh 的技术笔记

个人技术博客，基于 [MkDocs](https://www.mkdocs.org/) + [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) 搭建，
发布在 GitHub Pages 上。

**线上地址：<https://1678zrj.github.io/yyh_blog/>**

## 它是怎么工作的

```
record/            原始笔记（你只需要往这里写，唯一内容源）
  ├─ FastAPI/xxx.md
  ├─ Redis/xxx.md
  └─ 业务上的疑问（经验类）/...
typeora/           另一个笔记目录，同样会被发布（.md 和 .txt 都算笔记）
  ├─ python/xxx.md
  ├─ pytorch/xxx.md
  └─ 前后端/前端/vue/xxx.md
        │
        │  mkdocs build 时由 scripts/gen_blog.py 自动转换
        ▼
docs/blog/posts/<分类>/<标题>.md    带 front matter 的博客文章（自动生成，不进 git）
        │
        ▼
site/                              最终静态网页（构建产物，不进 git）
```

- 两个笔记目录**合并到同一套分类**里：分类名取各自目录下的第一层目录名，
  大小写不同视为同一个分类（`FastAPI` 与 `fastapi` 会合并成一个）。
- 笔记目录保持原样，脚本只读不写。
- **文章日期 = 笔记文件的修改日期**，首次同步时记录到 `note-dates.json` 并固定下来，
  以后修改老笔记不会把发布日期改掉。
- 代码块没写语言时，脚本会自动识别并补上，让代码有语法高亮。
- 笔记里用本地绝对路径引用的图片，脚本会复制到 `docs/assets/images/notes/` 并改写链接。
- 正文前两个段落之后会插入 `<!-- more -->`，列表页只展示摘要，不会把整篇文章铺在列表上。

## 日常使用

### 本地预览

```powershell
# 第一次：创建虚拟环境并安装依赖
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 每次预览（改笔记会自动刷新浏览器）
.\.venv\Scripts\python.exe -m mkdocs serve
```

浏览器打开 <http://127.0.0.1:8000/yyh_blog/> 即可。

关于本地预览的几点说明：

- 首次启动要先完整构建一次，**大约需要 70~80 秒**，之后才会开始服务。
- 改了 `record/` 或 `typeora/` 里的笔记（或直接改 `docs/`），浏览器会自动刷新。
- 生成器只重写**内容真的变了**的文件，所以不会出现「自己触发自己」的无限重建；
  改一篇笔记会触发两次构建（第二次内容无变化，随即稳定下来），耐心等一下即可。

### 写一篇新笔记

1. 在 `record/` 或 `typeora/` 下新建 `.md`（或 `.txt`）文件，子目录名会成为文章分类。
2. 运行 `mkdocs serve` 预览，或直接提交。
3. 提交并推送到 GitHub：

```powershell
git add -A
git commit -m "新增：xxx 笔记"
git push
```

推送后 GitHub Actions 会自动重新构建并发布，一两分钟后线上就能看到。

### 常用改动

| 想改什么 | 改哪里 |
| --- | --- |
| 站点标题、简介、作者、社交链接 | `mkdocs.yml` 顶部的 `site_name` / `site_description` / `extra.social` |
| 「关于我」页面 | `docs/about.md` |
| 配色、深浅色 | `mkdocs.yml` 的 `theme.palette` |
| **排版与视觉动效** | `docs/assets/extra.css`（样式）、`docs/assets/extra.js`（滚动淡入、阅读进度条） |
| 首页文案（英雄区那段话） | `scripts/gen_blog.py` 里的 `write_home()` |
| 标签页每条记录的展示内容 | `overrides/fragments/tags/default/listing.html` |
| 再加一个笔记目录 | `mkdocs.yml` 的 `extra.note_sync.notes_dirs` 里加一行 |
| 隐藏某些目录不发布 | `mkdocs.yml` 的 `extra.note_sync.exclude`，例如 `["record/私密/*"]` |
| 列表页每页文章数 | `mkdocs.yml` 的 `plugins.blog.pagination_per_page` |
| 站点域名/仓库名变了 | `mkdocs.yml` 的 `site_url`、`repo_url`；`scripts/check_links.py` 会自动读取 `site_url` |

### 关于视觉与动效

- 首页：渐变标题 + 缓慢漂浮的光斑 + 数据统计 + 卡片网格 + 分类/年度胶囊。
- 列表页：两列卡片网格（窄屏自动变一列），鼠标悬停上浮并显示顶部渐变条。
- 滚动动效：元素进入视口时淡入上移，错峰出现；系统开启「减少动态效果」时自动关闭。
- 文章页：顶部阅读进度条、左侧日期/分类/阅读时长元数据。
- 全部动效均为纯 CSS + 少量原生 JS，没有引入任何第三方库。

> 说明：`overrides/fragments/tags/default/listing.html` 覆盖了主题自带的标签列表模板，
> 升级 `mkdocs-material` 之后建议对比一下原模板是否有变化。

## 自动执行的处理

`scripts/gen_blog.py` 在每次构建前做这些事：

| 处理 | 说明 |
| --- | --- |
| 跳过空文件 | 0 字节的笔记不会发布 |
| 跳过重复内容 | 内容完全相同的笔记只保留第一篇，跨笔记目录也会去重（会打印跳过了哪些） |
| 标题层级统一 | 正文最高级标题统一成 `##`，避免和文章标题抢层级 |
| 插入摘要标记 | 前两个段落后插入 `<!-- more -->`，列表页只显示摘要 |
| 估算阅读时长 | 按正文字符数估算，显示在列表页和文章页元数据里 |
| 代码语言识别 | 未标注语言的代码块自动补上语言，结果缓存在 `.build-cache/` |
| 图片本地化 | 本地绝对路径的图片复制进 `docs/assets/images/notes/` 并改写链接 |
| 生成首页 | 统计数字、最新笔记、分类与年度入口 |

## 首次部署（已完成的部分跳过）

1. 在 GitHub 上创建仓库 `yyh_blog`（public）。
2. 本地推送代码：

   ```powershell
   git remote add origin https://github.com/1678zrj/yyh_blog.git
   git branch -M main
   git push -u origin main
   ```

3. 打开仓库 **Settings → Pages**，把 **Source** 设为 **GitHub Actions**。
4. 回到 **Actions** 页签，等 `构建并部署博客` 跑完，访问 <https://1678zrj.github.io/yyh_blog/>。

> 如果希望网址更短（`https://1678zrj.github.io/`），把仓库名改成 `1678zrj.github.io`，
> 并同步修改 `mkdocs.yml` 里的 `site_url` 和 `repo_url`。

## 开发者自检

```powershell
# 构建 + 检查站内死链
.\.venv\Scripts\python.exe -m mkdocs build
.\.venv\Scripts\python.exe scripts\check_links.py
```
