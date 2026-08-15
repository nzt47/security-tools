# Changelog — v1.2.0-rc3

**日期**: 2026-08-14
**标签**: `v1.2.0-rc3`（annotated，已推送 origin）
**指向提交**: `08dffffd`（fix(test/ci): A类基线遗留项3根因修复闭环 + CI回归守卫接入）
**对比基线**: `v1.2.0-rc2`
**范围**: 3 个提交（398bb32e / ed06e481 / 08dffffd）

---

## 一、变更总览

| 系列 | 提交 | 说明 |
|---|---|---|
| 测试可信度修复（A 类闭环） | `08dffffd` | A 类基线遗留 5 项 → 3 根因全部修复 + 双 BOM 污染修复 |
| CI 回归守卫接入 | `08dffffd` | env-health-guard.yml 并发治理 + 脏工作区阻断 + 分块回归入口 |
| 测试冷启动治理 | `398bb32e` | vector_store_sqlite_vec 收集期禁用重型真实 import |
| 规划可观测性 | `ed06e481` | wire 分支 wire_trace_id 全链路追踪 |
| 规划/总结文档 | `08dffffd` | 6 份规划总结 + 基线清单 + pytest.ini seed 说明 |

### 提交清单（rc2 → rc3）

| 提交 | 说明 | 变更量 |
|---|---|---|
| `398bb32e` | fix(test): test_vector_store_sqlite_vec 收集期禁用重型真实 import，消除冷启动卡死 | 1 file, +26 −3 |
| `ed06e481` | feat(planning): wire 分支补 wire_trace_id 全链路追踪 | 1 file, +12 −9 |
| `08dffffd` | fix(test/ci): A类基线遗留项3根因修复闭环 + CI回归守卫接入 | 16 files, +1328 −226 |

---

## 二、测试可信度修复（A 类 5 项 → 3 根因闭环）

### R1 本地 pre-commit hook 未同步 TLM 体系（A-2/A-3）
- **现象**: 旧版 pre-commit 框架 hook 报 "No .pre-commit-config.yaml file was found"；D1 不变量校验 BLOCK
- **修复**:
  - 环境治本: 重新部署 TLM hook（`sync_precommit_hook.ps1`），旧 hook 备份
  - 测试加固: `_install_local_hook` 校验本地 hook 含 `TLM_HOOK_SOURCE_REPO` marker，缺则回退 psm1 生成
- **验证**: 5 passed

### R2 os.symlink 失败残留未清理（A-1）
- **现象**: WinError 2 后残留无效链接文件，rglob 收集 2 个（期望 1）
- **修复**: symlink 失败后 `unlink()` 清理残留文件，保证"降级仅收集真实文件"断言
- **验证**: `test_collect_test_files_symlink_resolution` 通过

### R3 symlink 权限异常未捕获（A-4/A-5）
- **现象**: `FileNotFoundError [WinError 2]` 直接崩溃
- **修复**: `os.symlink` 包 try/except（OSError/NotImplementedError）→ `pytest.skip("符号链接不可用（Windows 需管理员/开发者模式）")`
- **验证**: 2 skipped（正确跳过，非崩溃）

### 附带: protect_source_files.ps1 双 BOM 污染
- 被 hook 编码检查拦截（head `EF BB BF EF BB BF`），`check_ps1_encoding.py --fix` 去叠加 BOM x2 → x1

---

## 三、CI 回归守卫接入

- **`.github/workflows/env-health-guard.yml`**: concurrency group 对齐 ci.yml（`ci-${{ github.ref }}`），双 job（env-health `--ci` 5min + workspace-guard `--strict` 3min）
- **`scripts/guard_workspace_clean.py`**: 提示/严格双模式 + `REGRESSION_REQUIRE_CLEAN=1` 强制
- **`scripts/run_full_pytest.py`**: 分块回归入口（4×1 串行）前置 guard 调用
- **`scripts/env_health_check.py`**: `--ci` 模式 + REQUIRED/OPTIONAL DEPS 分离 + venv WARN 分支

---

## 四、测试冷启动治理（398bb32e）

`importlib.util.find_spec()` 替代模块顶层真实 import（sentence_transformers），只查注册不触发加载。pytest 全新子进程冷启动卡死（>7min，thread 超时无法中断系统调用）→ 秒级完成收集。

---

## 五、规划可观测性（ed06e481）

wire 分支补 `wire_trace_id` 全链路追踪: 入口绑定一次（优先复用 process trace_id），9 处出口日志统一携带（ingress / call.start / planning 成功 / 三路回退 detail+warning），`grep wire_trace_id` 可还原完整 wire 调用链，便于定位规划回退根因。

---

## 六、文档与基线

- 规划: `P0_测试可信度修复_执行任务清单_20260813.md` / `P1_性能优化方案_20260814.md` / `下一轮迭代规划_20260813.md`
- 待办: `剩余基线遗留项待办清单_20260814.md`（78 项 A/B/C/D 类）/ `A类基线遗留项修复方案待办清单_20260814.md`
- 复盘: `A类基线遗留项修复总结_20260814.md`（3 根因复现/修复/验证）
- 基线: `failures_baseline.txt`（78 项 T-0 固化）+ `pytest.ini` 固定 seed 验证说明（T-4）

---

## 七、验证结果

| 验证项 | 结果 |
|---|---|
| A 类定向（3 文件） | **454 passed / 11 skipped / 0 failed**（65.85s） |
| 全量回归 chunk_2（4×1 串行） | rc=0，**3119 passed 全绿**（含 A-1/A-2） |
| 全量回归 chunk_3 | rc=0（含 A-3 ci_guard、A-4/5 system_tools） |
| pre-commit hook | 4 项检查全过（关键字冲突/工具索引同步/敏感信息/知识卡片 CLI） |

> 环境性说明: chunk_0 `test_abort_chat_when_tool_calling_active` 0xC0000005（ACCESS_VIOLATION）与 chunk_1 Timeout 为 Windows 环境性问题，与本次修复无关（参见 `剩余基线遗留项待办清单` C/D 类）。

---

## 八、已知问题与遗留

- **B 类 24 项**: 基线遗留但本次未复现，需 T-4 固定 seed 全量验证确认（计划见 `B类遗留项修复执行计划_20260814.md`）
- **C 类 3 项**: 环境伪失败（skill_index_cache 性能波动 / memory_optimized async）
- **D 类**: 环境性慢测试，并入 P1 方案 A3（@pytest.mark.slow 分流）
