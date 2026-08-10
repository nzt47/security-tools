# pre_commit_ci_guard 部署操作手册（同事版）

> 版本：v1.2（2026-08-10）— 新增一键安装脚本（install_guard.sh）与 pre-commit 框架链式集成
> 适用对象：仓库所有开发者（含并行会话/临时 worktree）
> 配套文档：[使用指南](pre_commit_ci_guard_使用指南_20260810.md)（WARN 排查指南全文）、发布包 `release/pre_commit_ci_guard_20260810.zip`

---

## 0. 这份手册帮你做什么

| 场景 | 直接跳到 |
|---|---|
| 我刚拿到代码，要启用提交前检查 | §2 安装（一键脚本 / 发布包二选一） |
| 安装后第一次提交，弹出一堆提示 | §3 首次提交会发生什么 |
| 提交被 `[FAIL] 新增 WARN` 拦住了 | §4 提交被阻断怎么办 |
| 我觉得检查误报了 | §5 常见误报处理 |
| 提交时显示"未部署，跳过" | §6.1 |
| 想卸载 / 换别的 hook 工具 | §7 |

---

## 1. 环境要求

- **git**：Windows 用户必须使用 Git for Windows（自带 `sh.exe`，hook 依赖它执行）
- **Python ≥ 3.8**：guard 脚本只用标准库，无需安装任何第三方包
- **目标目录是 git 仓库**：`git rev-parse --is-inside-work-tree` 返回 true

---

## 2. 安装（二选一）

### 方式 A：仓库内一键脚本（推荐，克隆了本仓库的同事）

一条命令完成「部署 guard 脚本 + 安装 hook + 端到端验证」：

```bash
# 在仓库根目录执行（Linux / macOS / Git Bash）：
bash scripts/install_guard.sh

# Windows PowerShell：
& "C:\Program Files\Git\bin\bash.exe" scripts\install_guard.sh
```

脚本自动完成：
1. 定位仓库根（`git rev-parse`）；
2. guard 脚本缺失时，自动从 `release/pre_commit_ci_guard/` 恢复；
3. 调 `python scripts/pre_commit_ci_guard.py --install-hook` 写入 hook；
4. 运行 `--static-only --strict` 验证（有 FAIL 或基线外新增 WARN 会提示，不阻断脚本本身）；
5. 检测到 pre-commit 框架时提示链式集成信息。

### 方式 B：发布包安装器（从 release/pre_commit_ci_guard_20260810.zip 解压的同事）

```bash
# 在仓库根目录（git 仓库）执行：
python install.py
# 若安装到指定仓库：
python install.py --repo D:\work\security-tools
```

预期输出：

```
[ok] guard 脚本已部署：D:\work\security-tools\scripts\pre_commit_ci_guard.py
[ok] pre-commit hook 已安装（存在性容错 + 增量阻断版）：...
[info] 验证安装结果：
[PASS] guard 脚本已部署：...
[PASS] hook 为容错版：...
[INFO] guard 静态检查：rc=0 ...
```

### 验证（两种方式通用）

```bash
python scripts/pre_commit_ci_guard.py --install-hook --check   # 或 python install.py --check
```

两条 `[PASS]` + `rc=0` 即安装成功。以后每次 `git commit` 自动检查。

> ⚠️ 安装器会覆盖 `.git/hooks/pre-commit`。若该文件原属于其他工具，安装器会自动备份为 `pre-commit.bak`（见 §7 回滚）。

### 链式集成 pre-commit 框架（.pre-commit-config.yaml）

仓库已有被 git 跟踪的 `.pre-commit-config.yaml`（5 个框架 hook：kwarg 冲突扫描、工具索引同步、敏感信息检测、知识 CLI 校验）。guard hook 会**链式调用**框架：

- **guard 失败 → 硬阻断**（exit 1，提交中止）；
- **pre-commit 框架失败 → 仅警告放行**（本次提交继续，控制台提示尽快处理）；
- 未安装 pre-commit 命令时自动跳过框架，不影响提交。

> 背景：此前框架的 5 个检查从未真正注册生效（缓存无 db）。链式集成后框架在干净工作区正常拦截；脏工作区（大量未暂存改动）下框架恢复会报 `patch does not apply`，此时仅警告、不卡提交。

---

## 3. 首次提交会发生什么

第一次 `git commit` 时：

```
[info] 首次运行：已自动生成基线 C:\...\.guard_baseline.json，本次放行；后续新增 WARN 将阻断提交
=== 汇总：FAIL=0 WARN=3 PASS/SKIP=5 ===
```

若本机装有 pre-commit 框架，guard 通过后还会链式运行框架 hook（输出框架日志）；框架失败不会卡本次提交，但控制台会提示尽快处理。

- 系统自动把**当前仓库所有存量 WARN** 记录到 `.guard_baseline.json`（基线 = 豁免清单）。
- 本次提交**放行**，不卡历史债务。
- **请把 `.guard_baseline.json` 提交到 git**（与代码一起提交），团队豁免口径一致。

从第二次提交起：基线外**新增**的 WARN 会以 `[FAIL]` 阻断；存量 WARN 继续豁免，输出形如：

```
=== 汇总：FAIL=0 WARN=3（基线内豁免 54，新增阻断 0） PASS/SKIP=5 ===
```

---

## 4. 提交被阻断怎么办

阻断原因只有两类：

### 4.1 `[FAIL]` 确定性缺陷（检查清单①）

例如"测试断言 is_registered(...) 但实现无对应注册"——这是**必须修复**的缺陷，按错误提示修改代码后重新提交。

### 4.2 `[FAIL] 新增 WARN（基线外）`

例如：

```
  [FAIL] 新增 WARN（基线外，须处理后方可提交）: import_degraded:my_new_module.py:23
=== 汇总：FAIL=1 WARN=3（基线内豁免 54，新增阻断 1） PASS/SKIP=5 ===
```

含义：你本次改动新引入了一个避坑指南禁止的模式（如新的 `except ImportError` 降级无告警、新的模块顶层副作用、新的性能测试混入并行矩阵等）。

处理路径（按优先级）：

1. **修复**（正确做法）：按 [使用指南 §6](pre_commit_ci_guard_使用指南_20260810.md) 的排查步骤修好对应位置，重新提交；
2. **确认是存量搬家**：如果该 WARN 只是把存量代码移动位置导致"看起来新增"，先修好再提交；
3. **临时跳过本次**（仅应急，勿常态化）：`git commit --no-verify -m "..."`——会跳过**所有** hook；
4. **确认误报**：走 §5。

---

## 5. 常见误报处理

guard 已内置消噪，以下场景**不会**告警：

| 场景 | 排除规则 |
|---|---|
| 测试自行注册的桩名 | 集成断言期望名减去测试内 `register_singleton` 注册的桩名 |
| 方法调用 `barrier.is_registered(...)` | `(?<!\.)is_registered` 负向断言只统计模块级调用 |
| 幂等配置初始化 `os.environ.setdefault` | 顶层副作用排除 setdefault / getenv |
| 未启用 SingletonManager 的仓库 | 相关检查项 SKIP（不计入 WARN/FAIL） |

### 仍觉得误报时，按此流程：

1. **复核命中行**：对照 [使用指南 §6](pre_commit_ci_guard_使用指南_20260810.md) 的判定逻辑，确认不是真问题（例如 `except ImportError` 分支确实无告警输出）；
2. **临时放行**（仅本次）：`git commit --no-verify`；
3. **反馈维护者**：把命中示例（文件:行号 + 代码片段）发给维护者，说明为何是误报。guard 的排除规则集中在 `pre_commit_ci_guard.py` 各检查函数内（注释已标注"排除"），由维护者更新后重新发布。

> 原则：**先假定是真问题，再怀疑误报**。本项目历史上"静默降级"和"顶层副作用"都造成过 CI 集体失败（见使用指南 §6 风险说明）。

---

## 6. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 提交输出 `[pre-commit-guard] 未部署 ... 本次跳过` | 当前 worktree 没有 `scripts/pre_commit_ci_guard.py` | 运行 `python install.py --repo <该仓库>`；或把基线/脚本随代码提交共享 |
| hook 输出 `sh: 未找到命令` | git 不是 Git for Windows | 安装 Git for Windows 后重跑 `python install.py` |
| hook 被其他工具/会话覆盖 | `.git/hooks/` 对所有 worktree 共享 | 重跑 `python install.py`，并确认团队只有一份 hook 维护入口 |
| `--run-serial` 很慢 | 它跑完整串行 singleton 测试 | 仅 CI 排查用，日常提交不需要 |
| 修改了 guard 规则但不生效 | hook 调用的是 `<仓库>/scripts/` 下脚本 | 把新脚本复制到 scripts/ 后重跑 `python install.py` |
| 提交输出 `[pre-commit-guard] 注意：pre-commit 框架 hook 未全部通过` | 框架 hook 失败（脏工作区常见 `patch does not apply`） | 属**警告放行**设计，提交继续；工作区 clean 后重跑 `pre-commit run --all-files` 排查框架问题 |

---

## 7. 卸载 / 回滚

```bash
python install.py --uninstall    # 移除 hook（保留 guard 脚本；非本工具 hook 不误删）
```

- 安装时若覆盖了旧 hook，备份在 `<仓库>/.git/hooks/pre-commit.bak`，改回 `pre-commit` 即恢复。
- 基线文件 `.guard_baseline.json` 与 guard 脚本由 `--uninstall` 保留，手动删除即可彻底移除。

---

## 8. 团队约定（建议）

1. **基线随代码提交**：`.guard_baseline.json` 纳入 git，review 时留意豁免范围是否合理；
2. **存量清零即收紧**：每修复一批存量 WARN，运行 `python scripts/pre_commit_ci_guard.py --update-baseline` 刷新基线，防止"存量永存"；
3. **误报走反馈通道**：不自行改动本地 guard 规则（否则同事环境不一致）；
4. **并行会话**：临时 worktree 不需要重复安装——hooks 共享，脚本未部署时自动跳过；需要启用时再执行 `python install.py`。

---

## 9. 关联

- 使用指南（WARN 排查指南全文）：`docs/troubleshooting/pre_commit_ci_guard_使用指南_20260810.md`
- 发布包：`release/pre_commit_ci_guard_20260810.zip`
- 避坑指南（检查清单来源）：`docs/zh/知识库重构计划/Singleton与覆盖率并行测试_避坑指南_20260809.md`
