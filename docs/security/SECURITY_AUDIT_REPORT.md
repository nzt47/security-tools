# 项目安全审计报告

**报告类型**: 持续维护型安全审计报告（Living Document）
**首次创建**: 2026-07-19
**最近更新**: 2026-07-19
**审计负责人**: nzt47
**审计范围**: `agent/` 目录全量代码 + 部署配置 + 运行时数据保护

---

## 审计目标

1. 识别代码库中所有敏感数据处理路径（API Key / Token / Webhook URL / 用户凭证）
2. 评估敏感数据存储、传输、销毁各阶段的安全性
3. 追踪已发现安全事件的修复状态
4. 沉淀安全最佳实践，避免同类问题再次发生

## 严重级别定义

| 级别 | 含义 | 响应时限 |
|------|------|----------|
| P0 | 严重：敏感数据已泄露或可直接被未授权访问 | 24h 内修复 |
| P1 | 高：存在被利用风险，需立即加固 | 7 天内修复 |
| P2 | 中：潜在风险，可在常规迭代中处理 | 30 天内修复 |
| P3 | 低：信息性提示，无即时风险 | 视情况处理 |

---

## 已审计事件清单

### SEC-2026-07-19-001：OpenAI API Key 明文泄露

| 字段 | 值 |
|------|-----|
| 级别 | P0（已缓解） |
| 发现日期 | 2026-07-19 |
| 状态 | 已缓解（Mitigated） — Git 历史清除待定 |
| 详细文档 | [SECURITY_NOTICE_20260719_api_key_leak.md](file:///c:/Users/Administrator/agent/docs/security/SECURITY_NOTICE_20260719_api_key_leak.md) |

**事件摘要**：
- `agent/data/network_config.json` 曾明文存储 OpenAI API key（`sk-ddf2...45a3`），并已提交到 Git 历史的 12 个 commit 中
- key 已在 OpenAI 控制台完成轮换
- `network_config.json` 中的明文 api_key 和 webhook_url 已清空

**缓解措施**：
1. ✅ OpenAI key 轮换完成
2. ✅ 本地 `network_config.json` 敏感字段清空
3. ✅ 启动"纯 .env 单一数据源架构"重构（见 SEC-2026-07-19-002）
4. ⏳ Git 历史清除（BFG 清理待定，需协调多个分支）

---

### SEC-2026-07-19-002：纯 .env 单一数据源架构重构

| 字段 | 值 |
|------|-----|
| 级别 | P1（架构性加固） |
| 完成日期 | 2026-07-19 |
| 状态 | 已完成 |
| 详细文档 | [CHANGELOG_ENV_SINGLE_SOURCE_20260719.md](file:///c:/Users/Administrator/agent/docs/CHANGELOG_ENV_SINGLE_SOURCE_20260719.md) |
| 关联 commit | `878974c2` / `d4df7036` |

**变更目标**：
- 移除 `SecureConfigManager` 加密存储中间层（双源问题）
- `.env` 文件作为唯一敏感数据存储
- UI 修改 → `.env` → `os.environ` → 代码读取（热重载）

**核心组件**：
- `agent/env_config_manager.py`：`.env` 文件配置管理器（线程安全 + 原子写入）
- `agent/network_config.py`：`_key_to_env_var` 映射规则、`_save_secure` / `_load_secure` 改写
- `agent/server_routes/routes_config.py`：UI POST 端点先持久化到 `.env` 再 `configure_llm`

**安全收益**：
| 维度 | 变更前 | 变更后 |
|------|--------|--------|
| 敏感数据存储 | 加密文件 + .env 双源 | .env 单一来源（可审计） |
| 明文泄露风险 | `network_config.json` 可能含明文 | 永远不含明文 |
| 文件权限依赖 | 加密层兜底 | 依赖文件系统权限（见 SEC-2026-07-19-003） |
| 配置读取性能 | AES-GCM 解密 | `os.getenv()` O(1) |

**遗留任务**（P2，不影响功能）：
- `agent/network/config_manager.py` 旧版未使用，待同步改造或删除
- `agent/network_config.py` `secure_manager` 参数保留向后兼容，待完全移除
- `agent/orchestrator/lifecycle_manager.py:832-833` 仍尝试导入 `_get_secure_manager`，fallback 正常

---

### SEC-2026-07-19-003：.env 文件权限加固（P1）

| 字段 | 值 |
|------|-----|
| 级别 | P1 |
| 完成日期 | 2026-07-19 |
| 状态 | 已完成 |
| 关联 commit | `d5d27482` |
| 测试覆盖 | `tests/unit/test_env_file_permissions.py`（16 用例，13 passed / 3 Unix-only skipped） |

**变更目标**：
- 自动化设置 `.env` 文件权限为 `600`（仅 owner 可读写）
- 防止其他用户（如 `BUILTIN\Administrators`）读取敏感数据
- 跨平台兼容（Unix chmod / Windows icacls）

**实现要点**：
1. `EnvConfigManager._secure_file_permissions()` 方法
2. 在 `_ensure_file_exists()` 和 `_atomic_write()` 末尾调用权限保护
3. 跨平台分支：
   - Unix/Linux：`os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)`（即 0o600）
   - Windows：`icacls /inheritance:r /grant:r "<user>:F" /grant:r "SYSTEM:F"`

**关键设计**：
- **失败降级**：权限设置失败仅 `warning`，不抛异常（写入已成功，不应回滚）
- **中文 Windows 兼容**：`subprocess.run(encoding='utf-8', errors='replace')` 处理 GBK 输出
- **无窗口运行**：`CREATE_NO_WINDOW` 标志避免弹出控制台窗口
- **超时保护**：`timeout=5` 防止 icacls 卡死

**真实环境验证**：

修复前 `.env` ACL（含继承权限）：
```
c:\Users\Administrator\agent\.env NT AUTHORITY\SYSTEM:(I)(F)
                                  BUILTIN\Administrators:(I)(F)
                                  DESKTOP-CN00D5I\AdminWT:(I)(F)
```

修复后 `.env` ACL（仅 owner + SYSTEM，无继承）：
```
c:\Users\Administrator\agent\.env NT AUTHORITY\SYSTEM:(F)
                                  DESKTOP-CN00D5I\AdminWT:(F)
```

**关联修复 Bug**：
- 中文 Windows 下 `icacls` 输出 GBK 编码，`subprocess` 默认 utf-8 解码失败 → 权限设置实际未生效（被降级为 warning）
- 修复方案：添加 `errors='replace'` 容错

---

## 静态扫描结论（2026-07-19）

### ✅ 正确适配纯 .env 架构的代码

| 文件 | 验证结论 |
|------|----------|
| `agent/env_config_manager.py` | 核心组件，原子写入 + 权限保护 |
| `agent/network_config.py` | `_save_secure` / `_load_secure` 通过 `_key_to_env_var` 映射 |
| `agent/server_routes/routes_config.py` | UI POST 端点先持久化到 `.env` 再 `configure_llm` |
| `agent/error_reporting_config.py` | 完整从 `os.environ.get` 读取所有 `ERROR_REPORTING_*` 变量（含 Slack webhook） |
| `agent/orchestrator/lifecycle_manager.py:945-952` | `configure_llm` 从环境变量回退加载 `LLM_API_KEY` |
| `agent/security_utils.py:73` | `os.getenv(key_env_var)` 读取密钥 |
| `agent/server_auth.py:16` | `FLASK_API_TOKEN` 从环境变量读取 |
| `agent/monitoring/loki.py:40` | `LOKI_URL` 从环境变量读取 |
| `agent/utils/perf_monitor.py:45-55` | `AGENT_PERF_*` 从环境变量读取 |

### ⚠️ 遗留但已废弃的代码（P2 任务范围，不影响功能）

| 文件 / 位置 | 说明 | 风险 |
|-------------|------|------|
| `agent/network/config_manager.py` | 旧版 NetworkConfigManager，未被任何代码导入 | 低（死代码） |
| `agent/network_config.py:176,180,184` | `secure_manager` 参数已标记废弃，保留向后兼容 | 低 |
| `agent/orchestrator/lifecycle_manager.py:832-833` | 尝试导入 `_get_secure_manager`，失败时 fallback 到 `NetworkConfigManager()` 无参数构造 | 低（功能正常） |

### ❌ 硬编码假阳性（无需修复）

| 文件 / 位置 | 实际情况 | 风险 |
|-------------|----------|------|
| `agent/env_config_manager.py:48` | docstring 示例 `'sk-xxx'` | 无 |
| `agent/security_utils.py:243` | `__main__` 块的内置测试值 `'sk-test1234567890abcdef'` | 无 |

### ℹ️ 独立子系统（不在本次重构范围）

| 文件 / 位置 | 说明 |
|-------------|------|
| `agent/monitoring/alert_notifier.py:262` | `DingTalkSender.webhook_url` 来自 monitoring config（独立子系统） |
| `agent/monitoring/alert_manager.py:176-178` | 告警系统独立配置，与 `.env` 架构解耦 |

---

## 待办事项（Pending）

| 优先级 | 任务 | 关联事件 | 截止日期 |
|--------|------|----------|----------|
| P1 | Git 历史清除（BFG） | SEC-2026-07-19-001 | 待协调多分支 |
| P2 | 完全删除 `SecureConfigManager` 类定义 | SEC-2026-07-19-002 | 30 天内 |
| P2 | 删除或改造 `agent/network/config_manager.py` | SEC-2026-07-19-002 | 30 天内 |
| P2 | 清理 `lifecycle_manager.py:832-833` 旧版导入 | SEC-2026-07-19-002 | 30 天内 |
| P3 | 配置变更审计日志 | SEC-2026-07-19-002 | 视情况 |

---

## 安全最佳实践清单

### 敏感数据存储
- ✅ `.env` 文件作为唯一敏感数据存储（不加密，依赖文件系统权限）
- ✅ `.env` 文件权限自动化设置 600（Unix）/ ACL 限制（Windows）
- ✅ `network_config.json` 永远不含明文 api_key
- ✅ `.gitignore` 已忽略 `.env` 文件

### 敏感数据传输
- ✅ UI 修改 → `.env` → `os.environ` → 代码读取（全程内存，无网络传输）
- ✅ HTTPS 传输敏感数据（生产环境强制 TLS）

### 敏感数据访问
- ✅ 配置读取通过 `os.getenv()`（O(1) 字典查找）
- ✅ 配置写入通过 `EnvConfigManager.set()`（线程安全 + 原子写入）
- ✅ 失败降级不破坏主流程

### 敏感数据销毁
- ✅ `EnvConfigManager.delete()` 从 `.env` 和 `os.environ` 同步移除
- ⏳ Git 历史中的明文 key 待 BFG 清除

### 配置变更追踪
- ⏳ P3：记录每次 `.env` 修改的 `trace_id` / `user` / `key`（待实施）

---

## 参考文档

- [SECURITY_NOTICE_20260719_api_key_leak.md](file:///c:/Users/Administrator/agent/docs/security/SECURITY_NOTICE_20260719_api_key_leak.md) — API key 泄露事件通知
- [CHANGELOG_ENV_SINGLE_SOURCE_20260719.md](file:///c:/Users/Administrator/agent/docs/CHANGELOG_ENV_SINGLE_SOURCE_20260719.md) — 纯 .env 架构变更日志
- [ENV_ARCH_REFACTOR_SUMMARY.md](file:///c:/Users/Administrator/agent/docs/ENV_ARCH_REFACTOR_SUMMARY.md) — 架构调整完整总结
- [security_coding_checklist.md](file:///c:/Users/Administrator/agent/docs/security/security_coding_checklist.md) — 安全编码检查清单
- [secure_config_guide.md](file:///c:/Users/Administrator/agent/docs/security/secure_config_guide.md) — 安全配置指南
