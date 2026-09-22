# Web 工作台启动前置：必须先跑 `npm run build:flask`

> **结论（2026-09-22 核实）**：新克隆的仓库**直接** `python app_server.py` 再访问
> `http://127.0.0.1:5678/chat`，**一定会白屏**。
> 启动前必须先构建前端：`cd yunshu-ui && npm ci && npm run build:flask`。
>
> 根因是**仓库卫生问题**：`npm run build:flask` 的两个产物里，一个（`templates/yunshu.html`）入库、
> 另一个（`static/assets/`）被忽略 —— 这是**清理不彻底**，而非「有意让人先踩一次白屏」（详见 §4）。
> 本文只如实记录，**不改 `.gitignore`、不把构建产物纳入版本控制、不改路由行为**。

---

## 1. 症状与根因

### 1.1 最小复现

```bash
git clone <repo> && cd <repo>
pip install -r requirements.txt
python app_server.py          # 后端在 5678 起来，/api/health 正常
# 浏览器打开 http://127.0.0.1:5678/chat  → 白屏（HTML 200，但脚本 404）
```

### 1.2 机制（证据链）

| # | 事实 | 证据 |
|---|------|------|
| 1 | `templates/yunshu.html` **被 git 跟踪**，且内容是构建产物（引用带哈希的 chunk） | `git ls-files templates/yunshu.html` 有输出；HEAD 版本引用 `/static/assets/index-ddH7RVB9.js` |
| 2 | `static/assets/` **零个文件被跟踪**，且被忽略 | `git ls-files static/assets/ \| Measure-Object -Line` → `0`；`.gitignore` 的 `static/assets/` 条目 |
| 3 | 同一命令 `npm run build:flask` **同时**产出/覆盖这两者：`dist/assets/*` → `static/assets/`、`dist/index.html` → `templates/yunshu.html` | `yunshu-ui/package.json:12`（`build:flask` 脚本体内 `fs.cpSync(...'yunshu.html')` 与 `static/assets`） |
| 4 | 于是新克隆里：HTML 在、它引用的 chunk 不在 | 见下方 §1.3 实测 |
| 5 | `/chat` **只回 HTML、不校验资源**，且没有降级页 | `app_server.py:1500-1526`：直接读盘 `templates/yunshu.html` 原样返回 200 |
| 6 | 缺失的 `/static/*` 返回 **404**，且**刻意不回退** | `app_server.py:1535-1543`：`if os.path.isfile(...)` 否则 `abort(404)`（注释：`spa.html` 已删除，不再回退） |

⇒ 浏览器的实际结果：**200 的 HTML + 404 的入口脚本 = 空白页**（无报错页、无提示）。

### 1.3 实测（`git archive HEAD` 等效新克隆）

```powershell
git archive --format=tar -o $env:TEMP\yunshu_probe.tar HEAD templates static
tar -xf $env:TEMP\yunshu_probe.tar -C $env:TEMP\yunshu_freshclone_probe
```

输出：

```
=== 新克隆里 templates/yunshu.html 存在? ===
True
=== 它引用的资源 ===
<script type="module" crossorigin src="/static/assets/index-ddH7RVB9.js"></script>
<link rel="modulepreload" crossorigin href="/static/assets/vendor-react-DdwHQMIG.js">
...
<link rel="stylesheet" crossorigin href="/static/assets/index-BoOLzuYp.css">
=== 新克隆里 static/assets 存在? ===
ABSENT —— 目录不存在
=== 被引用的 index-ddH7RVB9.js 在新克隆里? ===
NOT FOUND ⇒ 404
```

---

## 2. 谁受影响、谁不受影响

| 场景 | 是否需要 `build:flask` | 说明 |
|------|------------------------|------|
| **`python app_server.py` + 访问 `/chat`（Flask 托管 SPA）** | **需要** | 就是本文描述的白屏场景 |
| `start_yunshu.bat`（一键启动） | 不需要 | 它起的是 **Vite dev server**（5173）并打开 `http://localhost:5173/static/#/workbench`，前端走源码而非构建产物（`start_yunshu.bat:35,55`） |
| 改前端后想让 `/chat` 生效 | **需要重跑** | 且**不必重启后端**：`/chat` 每次请求读盘 + `no-store`（`app_server.py:1508-1526`） |
| 只跑 Python 单测 / CLI（`python main.py`） | 不需要 | 与前端资源无关 |

> **注意**：`start_yunshu.bat` 走 dev server 会**掩盖**本问题 —— 用它验证过的机器改成直接访问 5678 时会突然白屏，
> 这不是"构建坏了"，而是"从来没构建过"。

---

## 3. 修复动作（每次拿到新克隆 / 改了前端之后）

```bash
cd yunshu-ui
npm ci                 # 或 npm install
npm run build:flask    # = npm run build + 把 dist 同步到 static/assets 与 templates/yunshu.html
cd ..
python app_server.py
# 打开 http://127.0.0.1:5678/chat
```

`build:flask` 会**先清空 `static/assets`** 再复制（避免陈旧 chunk 残留），
所以它同时也是"chunk 404 白屏"的解法。

### 自检：我这份 checkout 到底构建过没有？

```powershell
# 1) templates/yunshu.html 里引用的 chunk
$refs = (Select-String -Path templates/yunshu.html -Pattern '/static/assets/([\w.\-]+)' -AllMatches).Matches |
        ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
# 2) 逐个确认存在
foreach ($f in $refs) { "{0,-40} {1}" -f $f, (Test-Path (Join-Path 'static/assets' $f)) }
```

**只要出现 `False`，`/chat` 就是白屏**（HTML 引用了不存在的 chunk）。

---

## 4. 为什么不"把产物提交进库"了事

- Vite 产物是**内容哈希命名**的，每次构建都产生新文件名 ⇒ 入库等于每次构建都制造一次
  "旧 chunk 永久留在历史里"的噪音，且 `templates/yunshu.html` 会与 `static/assets/` 各写各的哈希。
- 仓库对 `static/assets/` 的既定口径已经写死在 `.gitignore`（「前端构建产物与恢复副本」一节）：
  > `static/assets/` 为 `npm run build:flask` 从 `yunshu-ui/dist` 复制的 Vite 哈希构建产物
  > （历史误跟踪的旧产物已 `git rm --cached`；部署/开发时执行 `build:flask` 重新生成）
- ⇒ **本次收口选择"把前置条件写进文档"，而不是改忽略规则或提交产物**。

### 遗留的仓库卫生问题（**未修，仅供决策**）

`templates/yunshu.html` 与 `static/assets/` 是**同一条命令、同一份产物集**，
但只有后者被 `git rm --cached`；前者仍被跟踪，于是：

- 新克隆拿到一个"指向不存在资源的入口页"（本文的白屏）；
- 每次 `build:flask` 都会把 `templates/yunshu.html` 变成工作区改动（`git status` 显示 ` M templates/yunshu.html`）。

它被跟踪**看起来是有意的**：UI 相关的功能提交里一直带着它
（例：`git log --oneline -- templates/yunshu.html` 的 `e98098a6 ... + 构建戳`、
`540cc3d9 工作台体验：...`）。**但它的后果（新克隆白屏）此前没有任何文档写过**
（全文检索 `build:flask` 只命中交付/验收类报告，无启动说明），故本文补上。

> 是否进一步把 `templates/yunshu.html` 也 `git rm --cached` + 让 `/chat` 在缺失时给出明确提示，
> 属于**路由/版本控制行为变更**，超出本次"文档收口"范围，需另行决策 —— 本文不做。

---

## 5. CI 现状（核实结果）

- **没有任何 workflow 执行 `build:flask`**，也没有任何 workflow 引用 `yunshu.html` / `static/assets`。
- `.github/workflows/yunshui-ui-tests.yml:171` 只跑 `cd yunshu-ui && npm run build`，
  并把 `yunshu-ui/dist/` 作为 artifact 上传 —— **不会**同步到 `static/` 与 `templates/`。
- `.github/workflows/ci.yml:1321` 会 `python app_server.py &` 跑在线 E2E，但打的是 `/api/*`，不校验 `/chat` 的资源可达性。
- `docker-compose.yml` 用的是 `Dockerfile`（`Dockerfile:3` 自述为 CI 用的轻量预检镜像，只 `COPY agent/` 与 `tests/`），也不构建前端。

⇒ **CI 跑绿不代表 `/chat` 能打开**；该漂移不会被现有流水线拦住。

---

## 6. 相关位置

| 位置 | 作用 |
|------|------|
| `yunshu-ui/package.json` | `build`（`tsc -b && vite build`）、`build:flask`（+ 同步到 Flask） |
| `app_server.py:1500-1526` | `/chat` 路由（每请求读盘 + `no-store`，无资源校验） |
| `app_server.py:1535-1543` | `/static/<path>`（未命中即 404，刻意不回退） |
| `.gitignore`（「前端构建产物与恢复副本」一节） | `static/assets/` 的忽略口径与理由 |
| `templates/yunshu.html` | 被跟踪的构建产物（SPA 入口页） |
| `start_yunshu.bat:35,55` | 一键启动走 Vite dev server（不经构建产物） |

---

**文档状态**：新增（2026-09-22）
**本次范围**：只改文档；未改 `.gitignore` / 未提交构建产物 / 未改路由
