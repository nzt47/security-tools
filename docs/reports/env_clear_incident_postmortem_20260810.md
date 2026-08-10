# .env 被清空事故故障复盘报告

**事故日期**: 2026-08-10 23:38 - 23:50
**影响对象**: 仓库根 `.env`（项目唯一敏感配置源，约 779 行）
**严重级别**: 高（核心配置丢失，可恢复）
**关联会话**: 主会话（知识库任务）与并行会话（P1 安全加固 .env 权限）

---

## 一、事故概述

并行会话运行 pytest 期间，仓库根 `.env` 被反复覆盖为测试专用内容，导致：

1. 23:38-23:46：`.env` 被覆盖为 `SEARCH_<uuid>_API_KEY=sk-real-key-original`（测试单 KEY 内容）
2. 23:49:05-09：`.env` 从 779 行被清空为 0 字节（写入瞬间为空）
3. 主会话的 `notify_group.ps1` 模拟测试期间误触发写入，将空文件写出为单行

## 二、时间线

| 时间 | 事件 | 证据 |
|------|------|------|
| 08-09 23:23-23:41 | 并行会话测试首次写入 `SEARCH_<uuid>_API_KEY` / `LLM_<uuid>_API_KEY` 到仓库根 `.env` | 审计日志 `logs/config_audit.jsonl`（pid 18312） |
| 08-10 23:38-23:46 | pytest（`-k planning`）运行，`.env` 被覆盖为测试值 | 现场检查 `.env` 内容 |
| 08-10 23:49:05 | 主会话前置检查 `.env` = 779 行正常 | 终端日志 |
| 08-10 23:49:09 | 并行进程清空 `.env`（0 行），主会话脚本写入单行 | 时间戳快照 |
| 08-10 23:50:40 | 从备份恢复 `.env`（779 行 + 补 `AGENT_HYBRID_ALPHA=0.5`） | 恢复后行数校验 |

## 三、根因分析（证据链）

### 排除项

- **`scripts/scan_sensitive_data.py`：排除**。纯只读脚本（仅 `filepath.read_text()`），全文件无任何写操作，不可能清空 `.env`。

### 确认根因

1. **`agent/env_config_manager.py` 无参实例化默认写仓库根 `.env`**
   - `EnvConfigManager()` 默认 `env_file_path = 项目根/.env`（L67-70）
   - `set()` → `_update_env_file()` 重写整个文件（读全量 → 追加/更新 → 原子写回）
   - **证据**：审计日志 08-09 23:23-23:41 存在 `SEARCH_<uuid>_API_KEY` / `LLM_<uuid>_API_KEY` 的 `set` 记录（pid 18312），key 格式与事故现场 `.env` 内容完全一致
2. **并行会话测试保存网络配置直接操作 `.env`**
   - `tests/unit/test_network_config_save_regression.py` 使用特征值 `sk-real-key-original` 并断言 `os.getenv(...)`（L303/319/491/499/508）——与事故现场 `.env` 内容逐字符一致
   - 测试保存 Tavily 等网络配置 → 配置管理器写入 `.env`
3. **今日（08-10）写入无审计日志**：说明当日写 `.env` 的路径可能绕过 `EnvConfigManager.set()`（直接文件写入），**未能完全确认**，作为遗留疑点
4. **并发竞态放大**：主会话脚本与并行进程同时操作 `.env`，读-改-写（read-modify-write）无跨进程互斥，低概率互相覆盖

## 四、影响评估

| 影响项 | 状态 |
|--------|------|
| `.env` 原 779 行配置丢失 | ✅ 已从 `.env.backups/env.bak.20260729115143` 恢复 |
| 08-08 固化配置 `AGENT_HYBRID_ALPHA=0.5` | ✅ 已补充（备份早于该配置） |
| 事故现场保留 | ✅ `.env.corrupt_backup_20260810`（91 字节测试值） |
| 敏感信息泄露 | 无（测试值 `sk-real-key-original` 为 mock，非真实密钥） |
| 并行测试结果 | 未受影响（测试目标与 `.env` 无关） |

## 五、修复措施（已实施）

### 5.1 `agent/env_config_manager.py` 三重防护（CHG-2026-0810）

| 防护 | 实现 | 作用 |
|------|------|------|
| 写前自动备份 | `_backup_env_file()` 写前复制当前 `.env` → `.env.backups/env.bak.<时间戳>`，保留最近 50 份 | 防误清空兜底：即使被误写也能一键恢复 |
| 跨进程文件锁 | `_acquire_process_lock()` / `_release_process_lock()`（Windows `msvcrt.locking`，Unix `fcntl.flock`，锁文件 `.env.lock`） | 防多进程并发写撕裂（与既有进程内线程锁互补） |
| 空内容守卫 | `_update_env_file()` 检测"文件非空但内容为空"→ warning 日志 | 疑似误清空时告警 |

验证：模拟"误清空"场景（临时文件）→ 备份生成、原内容可恢复、锁文件正常；既有 91 个单元测试全部通过（3 跳过为 Unix 专属）。

### 5.2 `.env` 恢复

- 恢复源：`.env.backups/env.bak.20260729115143`（777 行）
- 补充：`AGENT_HYBRID_ALPHA=0.5`（08-08 生产固化）+ 说明注释
- 恢复后验证：779 行、关键配置抽查通过、8 秒时间戳快照稳定（无并发写入）

## 六、预防措施（转告并行会话）

1. **测试禁止操作仓库根 `.env`**：涉及 `.env` 的测试必须传显式路径（pytest `tmp_path`），禁止无参 `EnvConfigManager()`
2. **修复 `test_network_config_save_regression.py`**：保存网络配置的测试应隔离到临时目录，不触碰真实 `.env`
3. **写入入口统一走 `EnvConfigManager`**：避免绕过审计日志直接写 `.env` 文件（本次"无审计日志写入"疑点）
4. **提交新功能后检查 `.env.backups/` 是否被测试污染**：如出现异常备份，说明有测试在写真实 `.env`

## 七、遗留疑点

1. 08-10 当日写 `.env` 的进程未 100% 定位（无审计日志），疑似直接文件写入路径
2. `.env.lock` 文件为运行产物，建议加入 `.gitignore`（若尚未忽略）

## 八、恢复指南（若再次发生）

```powershell
# 1) 从最近备份恢复
Copy-Item .env.backups\env.bak.<最近时间戳> .env -Force
# 2) 补充 08-08 固化配置（如缺失）
#    AGENT_HYBRID_ALPHA=0.5
# 3) 检查是否还有进程在写 .env
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select ProcessId, CommandLine
```
