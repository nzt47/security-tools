# shell_execute 工具实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为云枢添加 shell 命令执行能力，支持 bash/cmd/PowerShell，含安全检测

**Architecture:** 在 `agent/system_tools.py` 中添加 `execute_shell()` 函数（shell 自动检测+超时控制+输出截断），在 `agent/digital_life.py` 中注册为 `shell_execute` 工具（SafetyGuard 扫描 + PermissionSystem 权限确认）

**Tech Stack:** Python subprocess, json (危险命令规则), re (命令特征匹配)

**参考文档:** `docs/superpowers/specs/2026-06-13-shell-execute-tool-design.md`

---

### Task 1: 在 system_tools.py 中添加 shell 执行核心函数

**Files:**
- Modify: `agent/system_tools.py:1030`（在进程管理段落之前插入新函数）

- [ ] **Step 1: 在 system_tools.py 中进程管理段落之前插入 shell 执行函数**

在 `# ════════════════════════════════════════════════════════════`（第 1030 行，浏览器关闭函数之后）和进程管理段落之间，插入以下代码：

```python
# ════════════════════════════════════════════════════════════
#  Shell 执行 — 云枢执行 shell 命令的能力
# ════════════════════════════════════════════════════════════

# Shell 类型与执行命令的映射
_SHELL_COMMANDS = {
    "bash": ["bash", "-c"],
    "cmd": ["cmd", "/c"],
    "powershell": ["powershell", "-Command"],
}

# Unix 风格特征（检测到这些则倾向使用 bash）
_UNIX_SHELL_PATTERNS = [
    r"\$\(.*\)",      # $() 命令替换
    r"grep\s+",       # grep
    r"ls\s+-[lahr]",  # ls -l/a/h/r
    r"ps\s+(aux|ef)", # ps aux/ef
    r"chmod\s+",      # chmod
    r"chown\s+",      # chown
    r"rm\s+-[rf]",    # rm -r/-f
    r"mv\s+",         # mv
    r"cp\s+",         # cp
    r"cat\s+",        # cat
    r"less\s+",       # less
    r"tail\s+",       # tail
    r"head\s+",       # head
    r"which\s+",      # which
    r"whoami",        # whoami
    r"pwd",           # pwd
]

# PowerShell cmdlet 特征（检测到这些则使用 powershell）
_PS_CMDLET_PATTERNS = [
    r"(Get|Set|Write|Read|Invoke|Remove|New|Add|Select|Where|ForEach)-",
    r"\$Env:",         # PowerShell 环境变量
    r"\$_\s*\.",      # PowerShell 管道变量
    r"\$\w+\s*=",     # PowerShell 变量赋值
    r"\bWrite-(Host|Output|Error|Warning)",
    r"\bGet-(Process|Service|ChildItem|Content|Date|Item)",
    r"\bSet-(ExecutionPolicy|Location|Content)",
    r"\bRemove-Item",
]


def _detect_shell(command: str) -> str:
    """根据命令内容智能检测适合的 shell 类型
    
    Args:
        command: 要执行的命令字符串
        
    Returns:
        str: "bash", "cmd" 或 "powershell"
    """
    # 先检测 PowerShell cmdlet（特征最明显）
    for pattern in _PS_CMDLET_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return "powershell"
    
    # 再检测 Unix 风格特征
    for pattern in _UNIX_SHELL_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return "bash"
    
    # Windows 环境下的 cmd 常见命令
    # dir/type/find/echo 等同时存在于 bash 和 cmd，更精确区分
    if os.name == "nt":
        # 在 Windows 上，只有明确是 cmd 风格才用 cmd
        # 简单检查是否包含 cmd 特有的命令
        cmd_only_patterns = [
            r"\bdir\s+",       # dir 命令（bash 没有同名命令）
            r"\btype\s+",      # type 命令（bash 中 type 是内置，含义不同）
            r"\bfind\s+",      # find（非 POSIX find，Windows find 是字符串搜索）
        ]
        for pattern in cmd_only_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return "cmd"
    
    # 默认使用 bash（云枢运行在 Git Bash 环境）
    return "bash"


def _truncate_output(text: str, max_bytes: int = 102400) -> str:
    """截断过长输出，防止爆内存
    
    Args:
        text: 原始输出文本
        max_bytes: 最大字节数，默认 100KB
        
    Returns:
        str: 截断后的文本（可能附加 truncated 标注）
    """
    if not text:
        return text
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    # 截断到 max_bytes，然后按字符边界切割
    truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
    return truncated + "\n...（输出已截断，共 %d 字节）" % len(encoded)


def execute_shell(command: str, shell: str = "auto", cwd: str = None, timeout: int = 30) -> dict:
    """在 shell 中执行命令并返回结果
    
    Args:
        command: 要执行的命令字符串
        shell: "auto" / "bash" / "cmd" / "powershell"
        cwd: 工作目录，默认使用当前目录
        timeout: 超时秒数（1-120），默认 30
        
    Returns:
        dict: {ok: bool, stdout: str, stderr: str, exit_code: int, shell: str, cwd: str}
    """
    if not command or not command.strip():
        return {"ok": False, "error": "命令不能为空", "exit_code": -1}
    
    # 1. 确定 shell 类型
    shell = _detect_shell(command) if shell == "auto" else shell.lower()
    if shell not in _SHELL_COMMANDS:
        return {"ok": False, "error": f"不支持的 shell 类型: {shell}，可选: auto/bash/cmd/powershell", "exit_code": -1}
    
    # 2. 构建执行命令
    shell_cmd = _SHELL_COMMANDS[shell]
    cmd = shell_cmd + [command]
    
    # 3. 确定工作目录
    work_dir = cwd or os.getcwd()
    
    # 4. 限制超时
    timeout = max(1, min(timeout, 120))
    
    # 5. 执行
    try:
        proc = subprocess.run(
            cmd,
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        
        stdout = _truncate_output(proc.stdout.decode("utf-8", errors="replace"))
        stderr = _truncate_output(proc.stderr.decode("utf-8", errors="replace"))
        
        return {
            "ok": proc.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode,
            "shell": shell,
            "cwd": work_dir,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"命令执行超时（{timeout}秒）",
            "exit_code": -1,
            "shell": shell,
            "cwd": work_dir,
        }
    except FileNotFoundError as e:
        return {
            "ok": False,
            "error": f"找不到 shell 程序: {e}",
            "exit_code": -1,
            "shell": shell,
            "cwd": work_dir,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"执行失败: {e}",
            "exit_code": -1,
            "shell": shell,
            "cwd": work_dir,
        }
```

- [ ] **Step 2: 验证语法正确**

```bash
cd /c/Users/Administrator/agent && python -c "import ast; ast.parse(open('agent/system_tools.py').read()); print('语法 OK')"
```
预期输出：`语法 OK`

- [ ] **Step 3: 提交**

```bash
cd /c/Users/Administrator/agent
git add agent/system_tools.py
git commit -m "feat: 添加 shell 执行核心函数 execute_shell()

在 system_tools.py 中添加：
- _detect_shell(): 智能检测 shell 类型（bash/cmd/powershell）
- _truncate_output(): 输出截断（100KB 上限）
- execute_shell(): shell 命令执行，含超时控制

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: 在 digital_life.py 中注册 shell_execute 工具

**Files:**
- Modify: `agent/digital_life.py:2197-2199`（添加导入）
- Modify: `agent/digital_life.py:2237`（在 stop_process 之后添加新工具注册）

- [ ] **Step 1: 添加导入**

将第 2197-2199 行的导入语句修改为：

```python
        from agent.system_tools import (
            start_process, list_processes, stop_process, execute_shell,
        )
```

- [ ] **Step 2: 在 stop_process 工具注册之后插入 shell_execute 工具**

找到 `stop_process` 注册的结束位置（在 return 语句和下一个段落注释之间），插入新工具注册代码：

`stop_process` 注册的大致结构是（第 2230-2237 行左右）：
```python
        @tools.register("stop_process", ...)
        def _stop_process(**kwargs):
            pid = kwargs.get("pid")
            return stop_process(pid)
```

在这个函数**之后**（即在 `_stop_process` 函数体的 `return` 之后），插入以下代码：

```python
        # ════════════════════════════════════════════════════════════
        #  Shell 工具 — 云枢执行 shell 命令的能力
        # ════════════════════════════════════════════════════════════

        @tools.register("shell_execute", "在本地执行 shell 命令（支持 bash/cmd/PowerShell）。自动检测或手动指定 shell 类型。返回 stdout、stderr 和退出码。注意：危险命令（如 rm -rf）会被安全系统阻止", schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
                "shell": {"type": "string", "description": "shell 类型: auto（自动检测）/ bash / cmd / powershell，默认 auto"},
                "cwd": {"type": "string", "description": "工作目录，默认当前目录"},
                "timeout": {"type": "integer", "description": "超时秒数，默认 30，最大 120"},
            },
            "required": ["command"],
        })
        def _shell_execute(**kwargs):
            command = kwargs.get("command", "")
            shell = kwargs.get("shell", "auto")
            cwd = kwargs.get("cwd")
            timeout = kwargs.get("timeout", 30)
            
            if not command:
                return {"ok": False, "error": "请提供要执行的命令（command）"}
            
            # 第 1 关：SafetyGuard 扫描命令内容
            safety = getattr(self, '_safety_monitor', None)
            if safety:
                try:
                    check = safety.check_text(command)
                    if check.get("level") == "critical":
                        matches = [m.get("description", "") for m in check.get("matches", [])]
                        return {
                            "ok": False,
                            "error": f"危险命令被安全系统阻止: {matches}",
                            "blocked": True,
                            "level": "critical",
                        }
                    elif check.get("level") == "warning":
                        # warning 级别需权限确认
                        desc = "; ".join(m.get("description", "") for m in check.get("matches", []))
                        perm = self._permission.check_action(
                            f"shell_execute:warning:{desc[:100]}",
                            f"执行可能危险的命令: {desc}",
                        )
                        if not perm.allowed:
                            return {
                                "ok": False,
                                "error": f"权限系统拒绝: {perm.reason}",
                                "blocked": True,
                                "level": "warning",
                            }
                except Exception as e:
                    logger.warning("[shell_execute] 安全检查异常: %s", e)
            
            # 执行命令
            return execute_shell(command, shell=shell, cwd=cwd, timeout=timeout)
```

- [ ] **Step 3: 验证语法正确**

```bash
cd /c/Users/Administrator/agent && python -c "import ast; ast.parse(open('agent/digital_life.py').read()); print('语法 OK')"
```
预期输出：`语法 OK`

- [ ] **Step 4: 验证导入和注册正常**

```bash
cd /c/Users/Administrator/agent && python -c "
from agent.system_tools import execute_shell
result = execute_shell('echo 测试', shell='bash')
print('测试结果:', result.get('stdout'))
assert result['ok'] and '测试' in result.get('stdout', '')
print('验证通过: execute_shell 可以正常执行 bash 命令')
"
```
预期输出：`验证通过: execute_shell 可以正常执行 bash 命令`

- [ ] **Step 5: 提交**

```bash
cd /c/Users/Administrator/agent
git add agent/digital_life.py
git commit -m "feat: 注册 shell_execute 工具

在 digital_life.py 中：
- 导入 execute_shell
- 注册 shell_execute 工具供 LLM 调用
- 添加 SafetyGuard 危险命令扫描（critical 直接阻止）
- 添加 PermissionSystem 权限确认（warning 需用户确认）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: 测试验证

**Files:**
- Create: `test_shell_execute.py`（临时测试脚本，运行后删除）

- [ ] **Step 1: 创建并运行集成测试**

```bash
cd /c/Users/Administrator/agent && python -c "
from agent.system_tools import execute_shell

# 测试 1: 基本执行
r = execute_shell('echo hello world', shell='bash')
assert r['ok'], f'基本执行失败: {r}'
assert 'hello world' in r.get('stdout', ''), f'输出不匹配: {r}'
print('[PASS] 基本 bash 执行')

# 测试 2: cmd 执行
r = execute_shell('echo hello', shell='cmd')
assert r['ok'] or r['exit_code'] == 0, f'cmd 执行失败: {r}'
print('[PASS] cmd 执行')

# 测试 3: 错误命令
r = execute_shell('invalid_command_xyz_123', shell='bash')
assert not r['ok'], f'错误命令应该返回失败: {r}'
assert r['exit_code'] != 0, f'应该非零退出码: {r}'
print('[PASS] 错误命令处理')

# 测试 4: 空命令
r = execute_shell('')
assert not r['ok'], f'空命令应该失败: {r}'
print('[PASS] 空命令检测')

# 测试 5: 超时
r = execute_shell('sleep 5', shell='bash', timeout=1)
assert not r['ok'], f'超时应该失败: {r}'
assert '超时' in r.get('error', '')
print('[PASS] 超时控制')

# 测试 6: Shell 自动检测 - unix 风格
r = execute_shell('grep foo bar.txt', shell='auto')
assert r.get('shell') == 'bash', f'shell 检测错误: {r}'
print('[PASS] Shell 自动检测（Unix 风格）')

# 测试 7: Shell 自动检测 - PowerShell
r = execute_shell('Get-Process', shell='auto')
assert r.get('shell') == 'powershell', f'shell 检测错误: {r}'
print('[PASS] Shell 自动检测（PowerShell）')

# 测试 8: 管道命令
r = execute_shell('echo aaa bbb ccc | wc -w', shell='bash')
assert r['ok'], f'管道命令失败: {r}'
assert '3' in r.get('stdout', '') or '3' in r.get('stdout', '').strip()
print('[PASS] 管道命令')

print()
print('=' * 40)
print('全部测试通过!')
print('=' * 40)
"
```

预期输出：全部 8 项测试通过。

- [ ] **Step 2: 提交**

```bash
cd /c/Users/Administrator/agent
git add -A && git commit -m "test: shell_execute 工具测试验证

验证：
- 基本 bash/cmd 执行
- 错误命令处理
- 空命令检测
- 超时控制
- Shell 自动检测（Unix / PowerShell）
- 管道命令

Co-Authored-By: Claude <noreply@anthropic.com>"
```
