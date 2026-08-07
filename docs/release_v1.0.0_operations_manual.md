# Release v1.0.0 发布操作手册（新成员）

> 版本：v1.0.0 · 2026-08-07
> 适用对象：首次接手 Release 发布流程的新成员。
> 配套阅读：《Release 自动化测试避坑指南》`docs/release_testing_guide.md`、《发布新手引导》`docs/release_quickstart.md`。

---

## 1. 这份手册解决什么

团队已把「release 发布流程」沉淀为一份可直接取用的工具集 v1.0.0，包含：
- **Shell 函数库**：curl 网络失败自动映射 500、数值安全兜底等 4 个函数
- **WinForms 引导脚本（GUI）**：可视化完成首次发布 5 步，写操作自动弹确认框
- **Docker 镜像配置**：release-sim 模拟环境（git/gh/curl/pwsh 预装）
- **pip 包**：Python 项目直接 `import release_shell_lib`

本手册带你：**拿到归档包 → 解压 → 用 GUI 完成一次发布**。

---

## 2. 获取归档包

归档包已随仓库发布，两个来源任选：

**方式 A：仓库目录（已克隆仓库）**
```
releases/release_workflow_v1.0.0.zip
```

**方式 B：GitHub 网页**
```
https://github.com/nzt47/security-tools/blob/master/releases/release_workflow_v1.0.0.zip
```
点击 Download / Raw 下载。

> 归档包是**文件快照**，内部不包含 `.git` 历史；解压后可直接使用，无需初始化仓库。

---

## 3. 解压归档包

**Windows 资源管理器**：右键 zip → 「全部解压缩」，选目标目录（建议 `D:\tools\release-workflow`）。

**PowerShell**（推荐，保留目录结构）：
```powershell
Expand-Archive -Path release_workflow_v1.0.0.zip -DestinationPath D:\tools\release-workflow
```

解压后目录结构：
```
release-workflow/
├── scripts/
│   ├── release_shell_lib.sh          # Shell 函数库（4 个函数）
│   └── dev/
│       ├── release_first_release.ps1    # 命令行版引导
│       └── release_first_release_gui.ps1# GUI 版引导（本手册核心）
├── docker/release-sim/               # Dockerfile + entrypoint（release-sim 镜像）
├── packages/release_shell_lib/       # pip 包源码（release-shell-lib 0.1.0）
├── .github/workflows/                # release-auto.yml / release-precheck.yml
└── docs/                             # 手册/清单/模板/避坑指南 8 份
```

---

## 4. 用 GUI 脚本完成一次发布（核心流程）

### 4.1 启动

在解压目录下，用 PowerShell 启动（**必须加 `-Sta`**）：

```powershell
pwsh -Sta -File .\scripts\dev\release_first_release_gui.ps1
```

> Windows PowerShell 5.1 用户：`powershell.exe -Sta -File ...` 同样可用。
> 不加 `-Sta` 时脚本会提示「当前线程非 STA」并退出。

### 4.2 界面说明

启动后出现主窗口，从上到下：
1. **版本号输入框**：填写待发布版本（格式 `vX.Y.Z`，如 `v1.2.0`）
2. **「开始发布流程」按钮**：填写后点击
3. **步骤进度区**：5 步状态标签（待执行 / OK 绿 / FAIL 红）
4. **日志区**：实时输出每步检查结果

### 4.3 五步操作逐屏说明

| 步骤 | 做什么 | 需要你操作 | 可能弹确认框 |
|---|---|---|---|
| Step 1 版本号确认 | 校验 `vX.Y.Z` 格式 | 无 | 无 |
| Step 2 工作区与远端同步 | `git status` 检查 + `git fetch` + 对比 HEAD | 无 | 仅本地落后远端时弹 pull 确认框 |
| Step 3 创建 annotated tag | 检查 tag 唯一性 + 打 tag | 点「是」确认打 tag | ✅ 必弹 |
| Step 4 推送 tag 触发发布 | `git push` tag + 远端确认 | 点「是」确认推送 | ✅ 必弹 |
| Step 5 发布后验证引导 | 给出 Actions / Release 页面的核对清单 | 无 | 提示框 |

**操作提示**：
- 每步只做检查的会自动执行；**所有写操作（pull/tag/push）都会先弹确认框**，看清楚描述再点「是/否」。
- 点「否」会中止该步（Step 3/4 中止时发布流程停止，不会产生脏 tag 之外的副作用；Step 3 若已打本地 tag 中止，可用 `git tag -d <版本号>` 删除）。
- 任一步失败：该步标签变红 `[FAIL]`，日志给出原因，流程停止。

### 4.4 完整示例（发布 v1.2.0）

```
1. 启动 GUI（pwsh -Sta -File ...）
2. 输入 v1.2.0 → 点击「开始发布流程」
3. Step 1 变 [OK]
4. Step 2 变 [OK]（若本地落后，弹框选「是」执行 pull --rebase）
5. Step 3 弹「将执行 git tag -a v1.2.0 ...」→ 点「是」
6. Step 4 弹「将执行 git push origin v1.2.0」→ 点「是」
7. Step 5 显示核对清单：Actions 自动发布 + GitHub/Gitee Release 页面
8. 对照 docs/release_checklist.md 完成 D/E 段人工确认
```

---

## 5. 命令行版备选（无 GUI 环境）

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev\release_first_release.ps1 -Version v1.2.0
```
流程与 GUI 完全等价；交互改为命令行输入 `y` 确认 / `n` 中止。

---

## 6. 常见问题速查

| 症状 | 原因与解决 |
|---|---|
| GUI 启动即退出 | 未加 `-Sta`。用 `pwsh -Sta -File ...` 启动 |
| 界面中文乱码 | 脚本被旧编辑器存成非 UTF-8。重新用 UTF-8（带 BOM）保存 |
| Step 3 报「远端已存在 tag」 | 该版本已发布过，改一个新版本号；重复发布会被 409/422 拦截 |
| Step 4 推送后确认失败 | 网络/权限问题；先 `git ls-remote origin refs/tags/<版本>` 自查 |
| 网络失败反复重试 | 属正常：网络失败已映射 HTTP 500 自动重试（见避坑指南） |
| pip 包单测 | `python -m unittest discover -s packages/release_shell_lib/tests -v`（15 项） |

---

## 7. 关联文档

- 避坑指南：`docs/release_testing_guide.md`（自动化测试 8 大坑位）
- 排障总纲：`docs/release_workflow_manual.md`
- 检查清单：`docs/release_checklist.md`
- 团队 Wiki：`docs/wiki/release_workflow_wiki.md`（知识库入口）
