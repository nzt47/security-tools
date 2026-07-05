# 云枢 (Yunshu) — 数字生命体

一个拥有完整**感知-认知-行动闭环**的数字生命体。

## 架构

```
用户 ──► 感知层 (BodySensor) ──► 认知层 (PromptInjector) ──► 行动层 (DigitalLife) ──► 响应
              │                          │                          │
              ▼                          ▼                          ▼
       CPU/内存/电池/磁盘       拟人化翻译 + 模板注入       行为降级 + 权限检查 + LLM
              │                          │                          │
              └──────────────────────┬──────────────────────────────┘
                                     ▼
                              记忆层 (MemoryManager)
                         滚动摘要 + 黑匣子日志 + 后台压缩
```

### 模块

| 模块 | 目录 | 职责 |
|------|------|------|
| 感知层 | `sensor/` | CPU/GPU/内存/电池/磁盘/网络等物理感知 |
| 认知层 | `cognitive/` | 传感器数据拟人化翻译、提示词管理 |
| 记忆层 | `memory/` | 对话历史、滚动摘要、黑匣子日志、后台压缩 |
| 行动层 | `agent/` | 行为控制、权限管理、MCP 工具、主循环编排 |

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 启动交互模式
python main.py

# 单次对话
python main.py --chat "你好，云枢！"

# 查看状态
python main.py --status
```

### 配置 LLM（可选）

设置环境变量以获得完整对话能力：

```bash
set LLM_PROVIDER=openai
set LLM_API_KEY=sk-xxx
set LLM_MODEL=gpt-4

# 或使用 Anthropic
set LLM_PROVIDER=anthropic
set LLM_API_KEY=sk-ant-xxx
set LLM_MODEL=claude-sonnet-4-20250514
```

不配置 LLM 时，云枢运行在离线模式，提供基础回复。

## 行为模式

| 模式 | 触发条件 | 表现 |
|------|----------|------|
| 🟢 正常 | 一切正常 | 全能力运行 |
| 🔴 安全 | CPU > 85°C | 拒绝高耗能任务 |
| 🟡 省电 | 电池 < 15% | 降低推理频率 |
| 🟠 整理 | 内存 > 90% | 触发记忆压缩 |
| ⚫ 离线 | 网络超时 | 仅本地逻辑 |
| 🟤 预警 | 磁盘 < 10% | 提示清理空间 |

## 权限系统

云枢内置危险操作防护：
- **黑名单操作**：直接禁止（如格式化系统盘）
- **危险操作**：需要二次确认（如删除文件、修改系统配置）
- **敏感路径**：涉及系统关键目录时自动告警
- **自动备份**：执行修改前备份文件

## 安全配置

### 敏感信息加密

云枢使用 AES-GCM 算法加密存储敏感配置：

```bash
# 配置文件优先级（从高到低）
1. 环境变量
2. 加密配置文件 (.secure_config.json)
3. 默认值
```

### 配置方法

**方式一：环境变量（推荐用于生产环境）**

```bash
# Windows PowerShell
$env:LLM_API_KEY = "sk-your-api-key"
$env:LLM_PROVIDER = "openai"

# Linux/macOS
export LLM_API_KEY="sk-your-api-key"
export LLM_PROVIDER="openai"
```

**方式二：加密配置文件**

系统首次启动时会自动生成加密密钥文件 `.encryption_key`（权限 0o600）。

### 日志脱敏

系统自动检测并脱敏日志中的敏感信息：
- API Key（`sk-xxx`, `pk-xxx`）
- JWT Token
- 密码字段（`password=xxx`, `secret=xxx`）
- URL 参数中的敏感信息

### 审计日志

所有安全相关操作记录到 `logs/audit.log`：
- 配置访问和修改
- 安全配置访问
- 用户认证尝试
- 敏感操作执行

### 安全最佳实践

- ✅ 使用环境变量管理敏感配置
- ✅ 定期备份加密密钥文件
- ✅ 密钥文件权限保持为 `0o600`
- ✅ 不要将密钥文件提交到版本控制系统

### 安全文档

详细的安全配置说明请参考：
- [安全配置使用说明](docs/security/secure_config_guide.md)
- [安全测试报告](docs/test_reports/security_test_report.md)

## 项目结构

```
云枢/
├── agent/               # 行动层
│   ├── digital_life.py     # 主类
│   ├── behavior_controller.py  # 行为降级
│   ├── permission_system.py    # 权限系统
│   └── tools/               # MCP 工具
├── sensor/              # 感知层
├── cognitive/           # 认知层
├── memory/              # 记忆层
├── config.py            # 全局配置
├── main.py              # 入口
├── requirements.txt     # 依赖
└── README.md            # 本文档
```

## 测试

### 测试运行

```bash
# 安装测试依赖
pip install pytest pytest-cov

# 运行所有单元测试
$env:PYTHONIOENCODING="utf-8"  # Windows 必需，避免 GBK 编码错误
python -m pytest tests/unit/ -p no:cacheprovider --no-header

# 运行单个文件测试
python -m pytest tests/unit/test_system_tools.py -v

# 生成覆盖率报告
python -m pytest tests/unit/ --cov=agent --cov-report=html -p no:cacheprovider
```

### 三大核心模块 80%+ 覆盖率

| 模块 | 覆盖率 | 目标 | 状态 | 测试文件 |
|------|--------|------|------|----------|
| `agent/error_handler.py` | **89%** | 80% | ✅ 达成 | `test_error_handler*.py` (6 个) |
| `agent/system_tools.py` | **93%** | 80% | ✅ 达成 | `test_system_tools*.py` (8 个) |
| `agent/task_scheduler.py` | **100%** | 80% | ✅ 达成 | `test_task_scheduler*.py` (5 个) |

> **2026-06-10 复验说明**: 合并运行 5 个 task_scheduler 测试文件后覆盖率从 85% 提升至 **100%** (124/124 行)；
> 同时发现 `test_task_scheduler_complete.py` 中 5 个用例存在 Mock 路径错误，已全部修复，84 个测试全部通过。

#### 测量命令

```bash
# 测量 error_handler.py 覆盖率
$env:PYTHONIOENCODING="utf-8"
python -m pytest tests/unit/test_error_handler.py tests/unit/test_error_handler_final.py tests/unit/test_error_handler_final_coverage.py tests/unit/test_error_handler_last.py tests/unit/test_error_handler_remaining.py tests/unit/test_error_handler_supplement.py --cov=agent.error_handler --cov-report=term -p no:cacheprovider --no-header

# 测量 system_tools.py 覆盖率
$env:PYTHONIOENCODING="utf-8"
python -m pytest tests/unit/test_system_tools.py tests/unit/test_system_tools_supplement.py tests/unit/test_system_tools_final.py tests/unit/test_system_tools_ultimate.py tests/unit/test_system_tools_final_complete.py tests/unit/test_system_tools_ultimate_2.py tests/unit/test_system_tools_sandbox_browser_ultimate.py tests/unit/test_system_tools_extreme_edge_cases.py --cov=agent.system_tools --cov-report=term -p no:cacheprovider --no-header

# 测量 task_scheduler.py 覆盖率
$env:PYTHONIOENCODING="utf-8"
python -m pytest tests/unit/test_task_scheduler.py tests/unit/test_task_scheduler_complete.py tests/unit/test_task_scheduler_simple.py tests/unit/test_task_scheduler_supplement.py tests/unit/test_task_scheduler_final.py --cov=agent.task_scheduler --cov-report=term -p no:cacheprovider --no-header --override-ini="addopts=" --cov-fail-under=0
```

#### system_tools.py 关键测试场景

`tests/unit/test_system_tools_ultimate_2.py` (85 个测试用例) 覆盖了：

- **路径安全** (10 个测试): Unix 受保护目录、Windows 允许子目录、路径遍历防护
- **文件操作** (15 个测试): 二进制/文本读写、编码回退、权限错误、备份机制
- **目录与搜索** (8 个测试): 隐藏文件、最大条目、错误大小写处理
- **沙盒执行** (3 个测试): 超时、异常捕获、stdout 捕获
- **浏览器控制** (8 个测试): 无效协议、内网拦截、截图、关闭、异常
- **进程管理** (8 个测试): 白名单检查、启动/停止/列出、权限错误
- **剪贴板** (2 个测试): 内容长度限制、pyperclip 回退
- **定时任务 CRUD** (8 个测试): 白名单命令、启用/禁用、删除不存在
- **工作区** (10 个测试): 路径遍历防护、删除保护、目录递归
- **辅助函数** (13 个测试): MIME 猜测、文件元信息、链接处理

`tests/unit/test_system_tools_sandbox_browser_ultimate.py` (60 个测试用例) 深入覆盖：

- **沙盒执行**: 15+ 被禁模式、空代码、Unicode 字符串、daemon 线程、并发、stdout/stderr 截断、_SAFE_BUILTINS 白名单
- **浏览器启动**: 单例缓存、ImportError、Options 异常、Chrome 启动失败、set_page_load_timeout 失败、window_handles 异常
- **浏览器导航**: 协议大小写、内网 IP 段、URL 查询参数拦截、find_element 失败、title/current_url 异常
- **浏览器截图**: base64 截断到 500000 字符
- **浏览器关闭**: falsy 值处理、quit 异常、AttributeError

`tests/unit/test_system_tools_extreme_edge_cases.py` (37 个测试用例) 覆盖 Bug 修复与极端分支：

- **🐛 Bug 修复验证** (5 个测试): `_browser_instance` 状态泄漏修复（set_page_load_timeout 失败时的清理）
- **`_cleanup_browser_instance` 辅助函数** (4 个测试): 各种清理场景
- **start_process 异常分支** (6 个测试): Popen OSError/FileNotFoundError/PermissionError、args、cwd
- **list_processes 异常分支** (4 个测试): proc.info 异常、None 名称、非白名单过滤
- **stop_process 异常分支** (5 个测试): AccessDenied、ZombieProcess、TimeoutExpired、None 名称
- **get_clipboard pyperclip 回退** (5 个测试): PowerShell 调用、超时、命令不存在、内容截断
- **set_clipboard pyperclip 回退** (7 个测试): PowerShell 调用、内容截断到 5000 字符、超时

#### task_scheduler.py 关键测试场景

`tests/unit/test_task_scheduler_final.py` (13 个测试用例) 覆盖了：

- **start() 主循环**: 子线程 + sleep 副作用避免无限循环
- **异常恢复**: tick 异常、键盘中断
- **时间判断**: Cron 时间不匹配、小时/分钟分支
- **日志清理**: 实际文件系统 + 旧文件删除（mock stat/cutoff 失败路径）
- **单例模式**: 多次调用返回同一实例
- **`__main__` 块**: `runpy.run_module` 在当前进程执行，coverage 可统计

合并运行 5 个测试文件（`test_task_scheduler.py` + `test_task_scheduler_complete.py` + `test_task_scheduler_simple.py` + `test_task_scheduler_supplement.py` + `test_task_scheduler_final.py`）共 **84 个测试用例全部通过**，
覆盖率 **100%** (124/124 行已覆盖)。

#### 本次复验修复 (2026-06-10)

`test_task_scheduler_complete.py` 中修复的 5 个 Mock 路径错误用例：

| 测试用例 | 修复方法 |
|----------|----------|
| `test_init_logging` | 改用 `call_args_list` 子串匹配（避免 mock 中 Unicode 编码） |
| `test_should_run_interval_task_ready` | 重构 mock_datetime.now() 单一返回，明确 `__sub__` 总秒数 |
| `test_generate_weekly_report_import_error` | 改用 `sys.modules['agent.weekly_report_generator'] = None` 触发 ImportError |
| `test_cleanup_old_logs_success` | 移除错误 shutil patch，断言 glob 不被调用 |
| `test_cleanup_old_logs_with_files` | 改用真实临时目录 + `os.utime` 模拟旧/新文件 |

### 最难覆盖的边界条件

| 边界条件 | 难度 | 测试策略 |
|----------|------|----------|
| `start()` 主循环 | ⭐⭐⭐⭐ | 子线程 + `time.sleep` 副作用 + `scheduler.stop()` |
| 键盘中断 | ⭐⭐⭐ | `patch('time.sleep', side_effect=KeyboardInterrupt())` |
| 时间不匹配 | ⭐⭐ | `patch('datetime')` 构造假时间 |
| 沙盒 timeout | ⭐⭐⭐ | `timeout_sec=0` 让 `thread.join` 立即超时 |
| 浏览器启动 | ⭐⭐⭐⭐⭐ | mock `webdriver.Chrome` 抛异常，验证回退 |
| Windows 权限错误 | ⭐⭐⭐⭐ | `pytest.skip` 跳过 Windows 不稳定测试 |
| Unicode 解码失败 | ⭐⭐⭐ | 写入 `b'\xff\xfe\xfd'` 触发回退 |
| `__main__` 块 | ⭐⭐⭐⭐ | `subprocess.run` + `PYTHONIOENCODING=utf-8` |

### Bug 修复记录

#### Bug: `_browser_instance` 状态泄漏（已修复）

**问题描述**:
当 `webdriver.Chrome(options=opts)` 成功但后续的 `set_page_load_timeout()` 抛异常时，`_browser_instance` 已被赋值为部分初始化的实例，但 `get_browser()` 返回 `None`。下次调用 `get_browser()` 时，由于 `if _browser_instance is None` 为 False，会直接返回这个可能无效的实例。

**修复方案** ([system_tools.py](file:///c:/Users/Administrator/agent/agent/system_tools.py#L789-L849)):

1. 引入新的辅助函数 `_cleanup_browser_instance()`，用于安全清理：
   - 调用 `quit()` 释放浏览器资源
   - 将 `_browser_instance` 设为 `None`

2. 在 `get_browser()` 中包装 `set_page_load_timeout` 调用为内部 `try/except`：
   - 失败时调用 `_cleanup_browser_instance()`
   - 返回 `None`

3. 在最外层 `except Exception` 分支也调用 `_cleanup_browser_instance()`，作为防御性清理。

4. 内部 `window_handles` 访问的 `try/except` 保持不变（不影响返回值）。

**修复后行为**:
- `set_page_load_timeout` 失败 → `_browser_instance` 被清理为 `None`，下次调用会重新创建
- `quit()` 自身也失败 → 不会阻止清理流程
- 任何启动异常都会清理 `_browser_instance`，避免状态泄漏

**验证测试**:
`tests/unit/test_system_tools_extreme_edge_cases.py` 中 `TestBrowserInstanceStateLeakFix` 类的 5 个测试用例验证了此修复。

### 详细报告

- [最终覆盖率验收报告 (80%+ 达成)](final_coverage_acceptance_report.md)
- [三大核心模块覆盖率详情](coverage_report_three_modules_80plus.md)
- [Bug 修复总结：浏览器状态泄漏](docs/browser_state_leak_bugfix_summary.md)
- HTML 报告: `htmlcov_final_80plus/index.html`

## 运维工具

### P0 安全验证 Workflow 重建脚本

**脚本路径**: `scripts/rebuild_p0_workflow.py`
**单元测试**: `tests/unit/test_rebuild_p0_workflow.py`（44 个用例）
**完整手册**: [docs/security/p0_workflow_rebuild_runbook.md](docs/security/p0_workflow_rebuild_runbook.md)
**快速摘要**: [docs/security/p0_workflow_rebuild_summary.md](docs/security/p0_workflow_rebuild_summary.md)

#### 使用场景

当 P0 安全验证 CI 的"P0 回归测试"Job 持续因 "Set up job" 失败，且以下策略均无效时使用：
- `rerun-failed-jobs` API 重跑
- `workflow_dispatch` 触发新运行
- 修改 Job 名称 / job_id

诊断为 GitHub Actions 平台对 workflow 文件的持续性缓存故障（48 小时内 16/17 次失败），需通过删除旧 workflow 文件并用新文件名创建，强制生成新 workflow ID 绕过平台缓存。

#### 调用方式

```bash
# 模拟运行（推荐先执行，验证流程无误）
python scripts/rebuild_p0_workflow.py --dry-run

# 交互模式（每次操作前确认）
python scripts/rebuild_p0_workflow.py

# 跳过确认提示（自动化场景）
python scripts/rebuild_p0_workflow.py --yes
```

#### 操作流程

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | 前置检查 | 验证分支、工作目录状态、文件存在性 |
| 2 | 备份 | 旧文件复制到 `docs/security/archive/p0-security.yml.backup_<timestamp>` |
| 3 | 创建新文件 | `.github/workflows/p0-security-v2.yml`（内容与旧文件相同） |
| 4 | 删除旧文件 | `git rm .github/workflows/p0-security.yml` |
| 5 | 提交并推送 | 自动 commit + push 到 `phase2-visibility-convergence` |
| 6 | 等待新 workflow | 轮询 GitHub API 确认新 workflow 出现 |
| 7 | 等待首次运行 | 轮询确认首次 CI 运行已触发 |
| 8 | 轮询验证 | 跟踪运行结果，判断 P0 回归测试 Job 是否通过 |

#### 前置条件与权限要求

| 权限项 | 要求 | 验证方法 |
|--------|------|----------|
| 仓库写权限 | 对 `nzt47/security-tools` 有 push 权限 | `git push --dry-run origin phase2-visibility-convergence` |
| GitHub Token | `~/.git-credentials` 中有有效 token（`gho_` 前缀） | `git ls-remote https://github.com/nzt47/security-tools.git` |
| Actions 查看权限 | 能访问仓库的 Actions 页面 | 浏览器打开 `https://github.com/nzt47/security-tools/actions` |
| 本地分支 | 当前在 `phase2-visibility-convergence` 分支 | `git rev-parse --abbrev-ref HEAD` |
| 工作目录状态 | workflow 文件无未提交变更 | `git status --porcelain .github/workflows/p0-security.yml` |

#### 回滚步骤

重建后如出现问题，根据 [完整回滚决策树](docs/security/p0_workflow_rebuild_runbook.md#44-回滚决策树) 选择对应场景：

| 场景 | 触发条件 | 操作 |
|------|----------|------|
| A | P0 仍因 Set up job 失败（平台未恢复） | 记录失败 + 联系 GitHub 支持 |
| B | 新 workflow 有配置问题 | 从备份或 git 历史恢复旧文件 |
| C | 整个操作需撤销 | `git revert` 回退到重建前 commit |
| D | 备份丢失且 git 历史不可用 | 从验证报告手工重建 workflow 文件 |

**完整回滚决策树**（覆盖 12 种失败场景）见 [操作手册第 4.4 节](docs/security/p0_workflow_rebuild_runbook.md#44-回滚决策树)。

#### 注意事项

- 操作不可逆（旧 workflow 的运行历史保留在 GitHub Actions UI 中）
- 新 workflow 会有全新的 workflow ID，所有 Job 缓存会被清除
- 推送后会自动触发首次 CI 运行
- `--dry-run` 模式不产生任何文件系统副作用（备份、创建、删除、推送均跳过）
- 删除逻辑不使用 `git rm -f`，遇到未提交修改会自动拒绝（安全机制）

#### 相关文档

- [完整操作手册与验证报告](docs/security/p0_workflow_rebuild_runbook.md) — 含风险评估、dry-run 验证、12 种回滚场景
- [快速摘要](docs/security/p0_workflow_rebuild_summary.md) — 团队快速阅读版（~3 分钟）
- [P0 最终验证报告](docs/security/p0_final_verification_report.md) — 完整诊断过程和 17 次运行记录
- [P0 安全修复归档](docs/security/p0_security_fix_archive_20260703.md)
- [Release Notes](docs/security/RELEASE_NOTES_P0_SECURITY_20260703.md)

