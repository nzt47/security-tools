# shell_execute 工具设计文档

## 概述

为云枢（Yunshu）添加 shell 命令执行能力，使其能在本地执行 bash/cmd/PowerShell 命令，
并将执行结果返回给 LLM 进行进一步处理。

## 背景

云枢已有 `run_program` 工具（基于进程白名单启动特定程序），但其能力有限：
- 只能启动白名单中的单独程序，不能执行管道、重定向、变量等 shell 特性
- 不捕获 stdout/stderr 输出内容
- 没有命令级别的安全检测

需要一个新的 `shell_execute` 工具来填补这个空缺。

## 架构设计

```
LLM (云枢大脑)
  │ 调用 shell_execute(command="...")
  ▼
digital_life.py ── 注册层
  │  1. SafetyGuard 扫描命令内容
  │  2. PermissionSystem 权限确认
  │  3. 委托给 system_tools.execute_shell()
  ▼
system_tools.py ── 执行层
  │  1. Shell 自动检测/选择
  │  2. subprocess 执行（timeout 控制）
  │  3. 输出截断
  │  4. 返回结构化结果
  ▼
操作系统 shell（bash/cmd/powershell）
```

## 工具定义

### 名称

`shell_execute`

### 描述

在本地执行 shell 命令（支持 bash/cmd/PowerShell）。自动检测 shell 类型或手动指定。
返回命令的 stdout、stderr 和退出码。

### 参数 Schema

```json
{
  "type": "object",
  "properties": {
    "command": {
      "type": "string",
      "description": "要执行的 shell 命令"
    },
    "shell": {
      "type": "string",
      "description": "shell 类型：auto（自动检测）/ bash / cmd / powershell，默认 auto",
      "enum": ["auto", "bash", "cmd", "powershell"]
    },
    "cwd": {
      "type": "string",
      "description": "工作目录，默认工作区根目录"
    },
    "timeout": {
      "type": "integer",
      "description": "超时秒数，默认 30，最大 120",
      "default": 30,
      "minimum": 1,
      "maximum": 120
    }
  },
  "required": ["command"]
}
```

## Shell 自动检测逻辑

当 `shell="auto"` 时，按以下规则推断 shell 类型：

| 检测条件 | 推断结果 |
|----------|----------|
| 包含 `Get-` / `Set-` / `Write-` / `Where-Object` 等 PowerShell cmdlet | `powershell` |
| 包含 `$()` / `$(pwd)` / `grep` / `ls -l` / `ps aux` / `chmod` 等 Unix 风格 | `bash` |
| Windows 环境且包含 `dir` / `type` / `echo` 等 | `cmd` |
| 其他 / 默认 | `bash`（当前运行环境为 Git Bash） |

优先级：显式指定 > 特征匹配 > 默认 bash。

## 安全模型（混合模式）

### 第 1 关：SafetyGuard 命令扫描

复用 `data/dangerous_commands.json` 中的规则，对 `command` 字符串进行正则匹配：

- **critical 命中** → 直接拒绝，返回 `{blocked: true, error: "危险命令被阻止"}`
  - 示例：`rm -rf /`、`format C:`、`shutdown`、`del /f /s *.sys`
- **warning 命中** → 标记为危险，进入第 2 关
  - 示例：`kill -9`、`git push --force`、`docker rm -f`

### 第 2 关：PermissionSystem 权限确认

- critical 命令已被拒绝，不会到达此关
- warning 命令调用 `self._permission.check_action("shell:危险命令描述")` 请求用户确认
- 用户拒绝 → 返回 `{blocked: true, reason: "用户拒绝"}`
- 用户允许 → 执行命令

### 运行时安全

- **超时控制**：默认 30s，硬上限 120s，超时返回错误
- **输出截断**：stdout 和 stderr 各截断到 100KB，末尾标注 `...(truncated)`
- **无交互输入**：不传递 stdin，防止命令等待输入
- **CREATE_NO_WINDOW**：Windows 下不显示控制台窗口

## 返回值格式

```json
{
  "ok": true,
  "stdout": "命令的标准输出内容（最多 100KB）",
  "stderr": "命令的错误输出内容（最多 100KB）",
  "exit_code": 0,
  "shell": "bash",
  "cwd": "/path/to/workspace"
}
```

错误时：
```json
{
  "ok": false,
  "error": "错误描述",
  "blocked": false,
  "exit_code": -1
}
```

## 文件变更清单

### 1. `agent/system_tools.py`

新增函数：
- `_detect_shell(command: str) -> str` — 根据命令内容智能检测 shell 类型
- `_truncate_output(text: str, max_bytes: int = 102400) -> str` — 截断过长输出
- `execute_shell(command, shell="auto", cwd=None, timeout=30) -> dict` — 核心执行函数

### 2. `agent/digital_life.py`

在 `_register_builtin_tools()` 中：
- 导入 `execute_shell`（与 `start_process` 等放在一起）
- 注册 `shell_execute` 工具，添加 SafetyGuard 和 PermissionSystem 检查

### 3. `data/tools_config.json`

无需手动修改，新工具默认启用（opt-out 模式）。

## 测试要点

1. 基本执行：`echo hello` → 验证 stdout = "hello\n"
2. Shell 自动检测：PowerShell 命令 → 使用 powershell 执行
3. 危险命令阻止：`rm -rf /` → blocked
4. 超时控制：`sleep 100` → 超时错误
5. 输出截断：生成大量输出 → 截断到 100KB
6. 权限确认：warning 级别命令 → 调用 PermissionSystem
7. 错误命令：`invalid_command_xyz` → exit_code != 0

## 与现有工具的对比

| 工具 | 用途 | 输出捕获 | Shell 特性 | 安全模型 |
|------|------|---------|-----------|---------|
| `run_program` | 启动白名单程序 | 否（仅 PID） | 不支持 | 进程白名单 |
| `shell_execute` | 执行 shell 命令 | stdout+stderr+exit_code | 管道/变量/重定向 | 命令黑名单 + 权限确认 |
