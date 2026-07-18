# 变更日志：纯 .env 单一数据源架构

**日期**: 2026-07-19
**类型**: 架构重构（Architecture Refactor）
**Scope**: `config` / `security`
**破坏性变更**: 否（向后兼容）

---

## 一、背景与目标

### 1.1 触发场景
- 历史遗留：`network_config.json` 曾明文存储 API Key，导致敏感数据泄露风险
- 架构问题：`SecureConfigManager` 加密存储层引入额外复杂度，且与 `.env` 文件存在数据双源问题
- 运维痛点：UI 修改的配置与部署级配置（.env）不同步，难以追踪配置来源

### 1.2 设计目标
- **单一数据源**：所有敏感配置（API Key、Token、Webhook URL）统一存储在 `.env` 文件
- **零加密依赖**：移除 `SecureConfigManager` 加密层，依赖文件系统权限保护
- **热重载**：UI 修改 `.env` 后，环境变量立即同步，`LLMService` 自动重建
- **向后兼容**：外部调用签名不变（`_save_secure` / `_load_secure` / `configure_llm` / `apply_to_app`）

### 1.3 三义决策
| 原则 | 决策 |
|------|------|
| 【不易】 | `.env` 是唯一敏感数据存储；`configure_llm` / `apply_to_app` 接口契约不变 |
| 【变易】 | 移除加密中间层；`_save_secure` / `_load_secure` 内部实现替换为 `EnvConfigManager` |
| 【简易】 | `_key_to_env_var` 统一映射规则；原子写入 + Lock 保护并发；零外部依赖 |

---

## 二、变更范围

### 2.1 新增文件

#### `agent/env_config_manager.py`（224 行）
`.env` 文件配置管理器，替代 `SecureConfigManager`。

**核心 API**：
| 方法 | 职责 |
|------|------|
| `get(key, default)` | 从 `os.environ` 读取 |
| `set(key, value)` | 写 `.env` 文件 + 同步 `os.environ`（热重载） |
| `delete(key)` | 从 `.env` 和 `os.environ` 移除 |
| `reload()` | 重新加载 `.env` 到 `os.environ`（外部修改后手动触发） |

**设计要点**：
- 线程安全：`threading.Lock` 保护并发读写
- 原子写入：`tempfile.mkstemp` → 写入 → `os.rename`（防止写入中断导致文件损坏）
- Windows 兼容：rename 前先 unlink 目标文件（Windows 不支持覆盖式 rename）
- 模块级单例：`get_env_config_manager()` 懒加载

### 2.2 修改文件

#### `agent/network_config.py`
| 位置 | 变更内容 | 影响分析 |
|------|----------|----------|
| 顶部 docstring | 移除"AES-GCM 加密存储"描述，改为".env 单一数据源" | 文档同步 |
| `import` | 新增 `from agent.env_config_manager import get_env_config_manager` | 新依赖 |
| `__init__` | 新增 `self._env_config = get_env_config_manager()`；`secure_manager` 参数保留但标记废弃 | 向后兼容 |
| `_key_to_env_var`（新增） | 统一 key → env_var 映射规则 | 消除散落逻辑 |
| `_save_secure` | 改为调用 `EnvConfigManager.set()` 写 .env + os.environ | **核心变更** |
| `_load_secure` | 简化为 `os.getenv()`，移除 secure_manager 分支 | **核心变更** |
| `_save` | 无条件移除所有 api_key 字段（llm / llm_instances / search_instances / webhook_url） | 防止明文落盘 |
| `_update_llm_instances` | 修复 upsert bug（传入不存在 ID 时 api_key 丢失） | **既有 bug 修复** |
| `_update_search_instances` | 同上 upsert 修复 | **既有 bug 修复** |

#### `agent/server_routes/routes_config.py`
| 位置 | 变更内容 | 影响分析 |
|------|----------|----------|
| `api_config` POST（L100-104） | UI 提交的 api_key 先 `_save_secure` 写入 .env，再 `configure_llm` | 消除旧版端点的配置丢失风险 |

---

## 三、对现有功能的影响

### 3.1 行为变化（向后兼容）

| 功能 | 变更前 | 变更后 | 兼容性 |
|------|--------|--------|--------|
| UI 修改 LLM API Key | 写入加密存储 | 写入 `.env` + `os.environ` 同步 | ✅ 调用点无感知 |
| 读取 LLM API Key | 加密存储 > 环境变量 > 默认值 | 环境变量 > 默认值 | ✅ 优先级简化 |
| `network_config.json` | 可能含明文 api_key | 永远不含明文 api_key | ✅ 安全增强 |
| `get_all()` 返回值 | 脱敏值 | 脱敏值（不变） | ✅ 前端无感知 |
| `get_raw_config()` 返回值 | 解密后的真实值 | 从环境变量读取的真实值 | ✅ 调用方无感知 |
| `apply_to_app()` | 重建 HTTP / 搜索 / LLM | 不变（LLM 热重载链路已存在） | ✅ 行为一致 |

### 3.2 热重载机制（新增能力）

**完整链路**：
```
UI POST /api/network-config
    ↓
ncm.update(data)
    ↓
_save_secure(key, value)
    ↓
EnvConfigManager.set(env_var, value)
    ├─→ 写入 .env 文件（持久化）
    └─→ os.environ[env_var] = value（内存热重载）
    ↓
ncm.apply_to_app(Yunshu)
    ↓
get_raw_config() → _load_secure() → os.getenv() 读到最新值
    ↓
configure_llm(provider, api_key, model, base_url)
    ↓
重建 LLMService（立即生效）
```

**关键保证**：
- 写入 `.env` 与 `os.environ` 在同一个 `threading.Lock` 内完成，避免脏读
- `apply_to_app` 在 `_save_secure` 之后调用，确保读取到最新值
- `configure_llm` 内部已有环境变量回退逻辑（commit c367fa4c），双保险

### 3.3 既有 Bug 修复

#### Bug 1: `_update_llm_instances` upsert 缺失
- **现象**：传入 `instance_id` 但实例不存在时，api_key 不会被保存，实例也不会被创建
- **根因**：`else` 分支只在 `existing` 非空时处理，未覆盖"传入 ID 但不存在"场景
- **修复**：改为 upsert 语义，`existing` 为空时走新增分支
- **影响**：修复了用户通过 UI 新增 LLM 实例（带预设 ID）时配置丢失的问题

#### Bug 2: `_update_search_instances` 同样问题
- 同上修复

### 3.4 性能影响

| 指标 | 变更前 | 变更后 | 评估 |
|------|--------|--------|------|
| 配置读取 | 加密存储解密（AES-GCM） | `os.getenv()`（O(1) 字典查找） | ⬆️ 提升 |
| 配置写入 | 加密存储加密 + 写文件 | 写 `.env` 文件 + `os.environ` 赋值 | ⬇️ 略降（多了 os.environ 同步） |
| 启动加载 | 加密存储初始化 | `.env` 文件读取 | ⬆️ 提升（无加密开销） |

**结论**：整体性能提升，写入开销可忽略（单次 `os.environ[key] = value` 纳秒级）。

### 3.5 安全性影响

| 维度 | 变更前 | 变更后 | 评估 |
|------|--------|--------|------|
| 敏感数据存储 | 加密文件 + .env 双源 | .env 单一来源 | ⬆️ 可审计 |
| 明文泄露风险 | network_config.json 可能含明文 | 永远不含明文 | ⬆️ 增强 |
| 文件权限依赖 | 加密层兜底 | 依赖文件系统权限 | ⚠️ 需确保 .env 权限 |
| Git 提交保护 | .gitignore 已忽略 .env | 不变 | ✅ 保持 |

**⚠️ 部署注意事项**：
- 确保 `.env` 文件权限为 `600`（仅 owner 可读写）
- 生产环境通过容器环境变量或 secret manager 注入，避免宿主机文件泄露
- `.env.example` 已提供完整模板（commit 6f30fa01），不含真实值

---

## 四、回归测试方案

### 4.1 测试矩阵

| 场景 | 验证点 | 状态 |
|------|--------|------|
| 单实例 LLM api_key 修改 | .env 写入 + json 无明文 + os.environ 同步 | ✅ 通过 |
| 多实例 LLM api_key 新增（新 ID） | upsert 正确写入 | ✅ 通过 |
| 多实例 LLM api_key 更新（已存在 ID） | 值正确更新 | ✅ 通过 |
| Webhook URL 修改 | .env 写入 + json 无明文 + 脱敏 | ✅ 通过 |
| Search engine api_key（旧版字典） | 映射规则正确 | ✅ 通过 |
| 脱敏 vs 真实值 | `get_all()` 脱敏、`get_raw_config()` 真实 | ✅ 通过 |
| `/api/config` 端点写入 .env | 旧版端点不再丢失配置 | 🔄 待回归 |
| 并发写入线程安全 | Lock 保护无脏读 | 🔄 待回归 |
| 原子写入（写入中断恢复） | 临时文件清理 | 🔄 待回归 |
| `EnvConfigManager.reload()` | 外部修改 .env 后手动重载 | 🔄 待回归 |

### 4.2 测试脚本
- 单元测试：`tests/unit/test_env_hot_reload.py`（新增）
- 运行命令：`python -m pytest tests/unit/test_env_hot_reload.py -v`

---

## 五、回滚方案

### 5.1 回滚步骤
1. `git revert <commit_hash>` 回滚本次提交
2. 恢复 `SecureConfigManager` 初始化（`__init__` 中传入 `secure_manager` 参数）
3. `.env` 文件中的配置迁移回加密存储（手动或脚本）

### 5.2 回滚风险
- `.env` 文件中已写入的配置不会自动迁移回加密存储，需手动处理
- `network_config.json` 中被移除的 api_key 字段不会自动恢复

### 5.3 回滚决策点
- **建议回滚**：仅当发现 `EnvConfigManager` 在高并发场景下有严重性能问题
- **不建议回滚**：纯架构优化，无功能缺失，安全性增强

---

## 六、后续规划

| 优先级 | 任务 | 说明 |
|--------|------|------|
| P1 | `.env` 文件权限自动化 | 部署脚本自动设置 `chmod 600 .env` |
| P2 | `SecureConfigManager` 完全删除 | 当前仅废弃参数，后续可移除类定义 |
| P2 | `agent/network/config_manager.py` 同步改造 | 旧版未使用，可同步或删除 |
| P3 | 配置变更审计日志 | 记录每次 `.env` 修改的 trace_id / user / key |

---

## 七、参考文档
- 安全通知：`docs/security/SECURITY_NOTICE_20260719_api_key_leak.md`
- 分层配置（前置 commit）：`c367fa4c`
- .env 模板：`.env.example`（commit 6f30fa01）
- 三义哲学：Yi-Jing Coding Agent 规则（不易/变易/简易）
