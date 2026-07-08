# 测试修复变更摘要（第二轮）

## 修复目标

修复覆盖率 job 中 3 个测试文件的失败：
1. test_tracing_coverage.py — 17 处 API 签名不匹配（已在上轮修复）
2. test_system_tools_core.py — 30+ 处 Mock 路径错误 + SemLock + pyperclip
3. test_memory_vector_store.py — 缺少 @pytest.fixture

## 修复内容

### 1. 源码 bug 修复（3 处）

#### SemLock fork/spawn 冲突（agent/system_tools.py:145）
- **根因**：`multiprocessing.Queue()` 用默认 fork 上下文，`ctx.Process` 用 spawn 上下文，跨上下文共享 SemLock 报 RuntimeError
- **影响**：19 处测试失败
- **修复**：`result_queue = ctx.Queue()` 与 Process 同上下文

#### pyperclip 防御缺口（agent/system_tools.py:196-233）
- **根因**：`get_clipboard`/`set_clipboard` 仅捕 `ImportError`，Linux CI 无 xclip/xsel 时抛 `PyperclipException`（非 ImportError）
- **影响**：2 处测试失败
- **修复**：`except Exception` 覆盖所有异常

#### chromadb 用法错误（memory/vector_store/vector_store.py:329-361）
- **根因**：`chromadb.Client(Settings(persist_directory=...))` 创建 ephemeral 客户端，非持久化；ephemeral client 有单例缓存
- **影响**：持久化测试失败（assert 0 == 3, assert 0 == 1）
- **修复**：改用 `chromadb.PersistentClient(path=..., settings=...)`
- **fallback 修复**：except 分支加 `_load_from_file()` + 重建倒排索引

### 2. 测试代码 bug 修复

#### test_memory_vector_store.py:39
- **根因**：`vector_store` 方法缺少 `@pytest.fixture` 装饰器
- **影响**：2 处 setup ERROR
- **修复**：补 `@pytest.fixture`

#### test_system_tools_core.py（30+ 处）
- **Mock 路径修复**：`patch('agent.system_tools.X')` → `patch('agent.tools.<子模块>.X')`
  - file_tools: safe_resolve_path, is_binary_content, _get_single_file_info
  - workspace_tools: WORKSPACE_DIR, shutil
  - task_tools: _load_tasks, _save_tasks, SCHEDULED_TASKS_FILE, os, open
  - browser_tools: _browser_instance, get_browser, logger
  - process_tools: subprocess, os.name, list_processes, stop_process, psutil
- **断言文本修复**：`"不是文件"` → `"目录而非文件"`（2 处）
- **Windows 路径 skipif**：3 处加 `@pytest.mark.skipif(os.name != "nt", ...)`

## 验证

- py_compile 4 个文件全部通过
- CI Run #85 确认 tracing 测试全部通过（可观测性单元测试 3.10/3.11/3.12 success）
- 本轮推送触发新 CI 验证覆盖率 job
