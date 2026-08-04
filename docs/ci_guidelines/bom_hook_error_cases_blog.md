# 一次 Git Hook 与 BOM 的「字节级」排障实录

> **分享对象**：团队内部 | **难度**：中级 | **阅读时长**：10 分钟
> **关联工具**：[避坑指南](git_hook_bom_guide.md) | [上手指南](new_member_onboarding.md) | [总结报告](bom_repair_summary_20260805.md)

---

## 引言

「文件看起来是新的，跑起来却是旧的」「报错指向注释里的一行中文」——
这不是玄学，而是 UTF-8 BOM 在 PowerShell 5.1 中文系统上的经典陷阱。

过去两天我们在 pre-commit 钩子项目上连续踩中 6 类坑，其中 4 类与 BOM 直接相关，
最离谱的一次是同一个文件在 24 小时内被「意外叠加 BOM」了两次。
这篇博客把每个案例的**现象 → 根因 → 解法**原样记录，供团队复用。

---

## 案例一：叠加 BOM 破坏块注释（复发性最高）

### 现象

运行 `git_precommit_check.ps1` 直接报语法错误，指向**注释里的中文**：

```text
ParserError: ... : Missing expression after unary operator '-'.
    + CategoryInfo : InvalidOperation
```

第 8-9 行是 `.SYNOPSIS` 注释，却被打断成代码。

### 根因

文件头被写入了**两个** BOM（`EF BB BF EF BB BF`）。PS 5.1 只把第一个 BOM 当文件标记，
第二个被解码成 U+FEFF 字符，导致 `<#` 不再位于行首、块注释解析失败：

```text
正常：EF BB BF  3C 23 0D 0A   .SYNOPSIS ...
      [BOM]    <#   CRLF     （行首成立）
叠加：EF BB BF  EF BB BF  3C 23 0D 0A  .SYNOPSIS ...
      [BOM]    [BOM]     <#    ← 行首标记失效
```

### 解法

```powershell
# 一键修复（去叠加 BOM，保留恰好 1 个）
python scripts/fix_ps_bom.py --apply
```

### 一句话教训

> 批量写入 `.ps1`/`.psm1` 的工具如果按「追加 BOM」方式落盘，会把 BOM 叠上去。
> **PS 文件必须恰好 1 个 BOM**——多一个少一个都会炸。

---

## 案例二：无 BOM 导致的 PS 5.1 中文乱码

### 现象

42 个历史遗留 `.ps1` 文件**没有 BOM**。在 PS 5.1 中文系统上，这些文件的
中文字符串被按 GBK 解码成乱码，注释变 `鎷?`，字符串比较静默失败。

### 根因

PS 5.1 对**无 BOM** 的 `.ps1` 默认按系统 ANSI 代码页（中文系统 = GBK）解码；
PS 7+ 默认 UTF-8，行为不一致。**本地能跑 ≠ 部署环境能跑**。

### 解法

```powershell
# 补全所有缺 BOM 文件（dry-run 先预览）
python scripts/fix_ps_bom.py --fill-missing
python scripts/fix_ps_bom.py --fill-missing --apply
```

### 一句话教训

> 新写 PS 文件**保存为「UTF-8 with BOM」**，并让 hook 在提交时帮你盯住：
> `check_ps1_encoding.py` 会把关键文件缺 BOM 判为 BLOCK。

---

## 案例三：hook 模板「静默失败」——磁盘是新的，运行时是旧的

### 现象

`sync_precommit_hook.ps1` 显示 `DONE`，但提交行为毫无变化，`Import-Module` 报解析错误。

### 根因

`hook_fail_safe.psm1` 模板文件本身被叠加 BOM（同案例一），`Import-Module` 解析失败后
**静默降级**，用旧模板生成了 hook。磁盘内容、运行时行为两个层面脱节。

### 解法

```powershell
# 验证三连：模块可加载 + 生成内容含关键段 + 部署态一致
Import-Module scripts/dev/hook_fail_safe.psm1 -Force; $Error.Count          # 期望 0
(Get-HookContent -SourceRepo '<repo>') | Select-String 'git_precommit_check' # 期望命中
Select-String 'ENCODING_CHECK' .git/hooks/pre-commit                         # 期望命中
```

### 一句话教训

> 部署工具显示成功**不代表**产物正确。验「运行时行为」，不验「磁盘状态」。

---

## 案例四：`git commit -- <paths>` 会还原你的工作区

### 现象

用 `git commit -- 文件A 文件B` 提交后，文件被还原到旧状态、暂存区被清空，
出现「no changes added to commit」。

### 根因

pre-commit 钩子运行期间，`commit -- <paths>` 这种部分提交会临时替换暂存区，
钩子里的写操作（如自检脚本）触碰工作区后，git 还原了这些改动。

### 解法

```powershell
# 先 add，再普通 commit（不带 -- <paths>）
git add 文件A 文件B
git commit -m "msg"
```

### 一句话教训

> 在带 pre-commit 的仓库里，**永远先 `git add` 再普通 `git commit`**。

---

## 案例五：一行命令里的三个 PS 陷阱

### 现象

`git stash pop` 报冲突失败、`stash@{0}` 报 "Too many revisions"、
WSL bash 里取不到 `TLM_HOOK_SOURCE_REPO`。

### 根因与解法

| 陷阱 | 根因 | 解法 |
|------|------|------|
| `stash@{0}` 解析失败 | PS 把 `{0}` 当子表达式 | 单引号包裹：`'stash@{0}'` |
| stash pop 假失败 | 非冲突文件已 apply 且 stash 已 drop | `git stash list` + `git status` 双确认 |
| WSL 不继承环境变量 | WSL bash 与 Windows 环境隔离 | 测试 hook 用 git for windows 原生触发 |

---

## 案例六：工具与运行时的「隐性契约」

### 现象

IDE 编辑 `.psm1` 后 BOM 被剥掉；PS 5.1 的 `-File` 调用不绑定 `-Verbose`。

### 根因与解法

| 契约 | 说明 |
|------|------|
| **Edit/IDE 会剥 BOM** | 编辑含中文的 `.ps1`/`.psm1` 后必须字节级复查 `EF BB BF` |
| **`-File` 不绑定 `-Verbose`** | `-Verbose` 是保留参数名，自定义开关用 `-BomDiag` 实现 |
| **PS5.1 vs PS7 解码不同** | 本地 hook 强制用 `powershell.exe`（5.1）验证编码契约 |

---

## 诊断工具链（按使用频率）

```bash
# 1. 字节级调试（拦截失败时看字节证据：BOM 状态 + head hex）
powershell -File scripts/dev/git_precommit_check.ps1 -TargetRepo . -BomDiag

# 2. 编码契约门禁（BLOCK/WARN 分级）
python scripts/check_ps1_encoding.py --repo-root . --quiet

# 3. 一键批量修复（dry-run → apply）
python scripts/fix_ps_bom.py --fill-missing          # 预览
python scripts/fix_ps_bom.py --fill-missing --apply  # 修复

# 4. 防无痕回滚（12 项不变量）
python scripts/verify_core_invariants.py --repo-root . --quiet
```

---

## 总结：三条核心经验

1. **字节即真相**：遇到「看起来对的却跑不对」，先读字节（`head hex` / `-BomDiag`），
   别猜编码、别信显示。
2. **契约要机器盯**：`恰好 1 个 BOM`、`hook 模板可加载`、`不变量 12 项`——
   人记不住，脚本记得住。让 pre-commit 在提交前自动校验。
3. **静默失败最危险**：所有「可选增强」段都遵守「脚本缺失静默跳过」，
   但跳过时要打印 `[INFO]`，否则故障时无从下手。

> 演示：PR 阶段 `-BomDiag` 拦截叠加 BOM + 失效链接的真实过程，
> 见 `docs/ci_guidelines/assets/bomdiag_pr_demo.gif`。
