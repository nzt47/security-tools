# 云枢工具系统增强计划

## 阶段 1：功能增强

### 任务 1.1：多引擎搜索结果聚合去重（6h）
**问题**：web_search 单次只调用一个引擎，结果质量受限于单一来源。

**要求**：
1. 在 `agent/search_aggregator.py` 中实现聚合搜索模块
2. 同时调用 2-3 个搜索引擎（主引擎+备选引擎）
3. 对结果进行去重（基于 URL 归一化）、评分（来源权重+关键词匹配度）
4. 按评分排序后返回 Top N 条
5. 添加 `aggregate` 参数（bool）让 LLM 选择是否启用聚合模式
6. 聚合搜索的超时控制：单个引擎超时不阻塞整体
7. 编写测试（包含在 3.7）

### 任务 1.2：文件压缩/解压工具（4h）
在 `agent/compression_tools.py` 添加：
- `compress` — 压缩文件/目录为 zip 或 tar.gz。参数：`source_path`, `output_path`, `format`（zip/tar.gz）
- `decompress` — 解压压缩文件。参数：`file_path`, `output_dir`

**要求**：
- 基于 zipfile 和 tarfile（标准库）
- 支持大文件流式处理
- 路径安全检查（防止 zip slip 攻击）
- 进度回调支持（为后续异步框架预留）

### 任务 1.3：JSON/YAML 数据处理工具（4h）
在 `agent/data_process_tools.py` 添加：
- `json_query` — 使用 JSONPath 查询 JSON 数据
- `json_to_yaml` — JSON → YAML 转换
- `yaml_to_json` — YAML → JSON 转换
- `json_validate` — 验证 JSON 格式正确性
- `data_format_detect` — 自动检测数据格式（JSON/YAML/CSV/XML）

### 任务 1.4：文件比较工具（3h）
在 `agent/diff_tools.py` 添加：
- `diff_files` — 比较两个文件的差异。参数：`path1`, `path2`, `context_lines`（上下文行数）
- 基于 difflib 实现
- 返回统一格式的差异行（类似 git diff 格式）

### 任务 1.5：定时调度工具（8h）
新文件：`agent/scheduling.py` + API 路由 `agent/server_routes/routes_scheduling.py`

**工具：**
- `schedule_task` — 创建定时任务。参数：`name`, `cron_expr`（cron 表达式）或 `interval_minutes`, `action`（要执行的操作描述）, `params`
- `list_scheduled_tasks` — 列出所有定时任务
- `cancel_scheduled_task` — 取消指定任务
- `pause_scheduled_task` / `resume_scheduled_task` — 暂停/恢复

**要求：**
- 使用 `schedule` 库（需安装 `pip install schedule`）
- 任务持久化到 `data/schedules.json`
- 任务执行结果记录到 `data/schedule_history.jsonl`
- 服务器重启后自动恢复定时任务
- 注意线程安全（schedule 在主线程或独立线程运行）

**API 端点（用于前端管理）：**
- `GET /api/schedules` — 获取所有定时任务
- `POST /api/schedules` — 创建定时任务
- `DELETE /api/schedules/:id` — 删除定时任务
- `GET /api/schedules/history` — 获取执行历史

### 任务 1.6：异步工具执行框架（12h）
新文件：`agent/async_executor.py`

**核心机制：**
1. 任务提交 + 轮询模式：`submit_task`（返回 task_id）→ `get_task_status`（轮询）→ `get_task_result`（获取结果）
2. 支持长时间运行的工具（大文件下载、批量搜索等）
3. 任务状态：pending → running → completed / failed
4. 任务结果缓存（TTL 配置化）
5. 可选 WebSocket 推送进度

**要求：**
- 基于 `threading` 或 `concurrent.futures.ThreadPoolExecutor`（不引入 asyncio）
- 最大并发数可配置（默认 3）
- 任务超时控制
- 数据库/文件系统持久化任务记录

### 任务 1.7：为新增工具编写测试（6h）
- 为 1.1-1.6 的所有新工具编写 pytest 测试
- 测试覆盖正常路径、异常路径、边界条件
- 集成到现有测试套件

### 任务 1.8：API 文档更新（4h）
- 更新所有新增工具的 Schema 和描述
- 确保 LLM 能正确理解和使用新工具

## 阶段 2：架构优化

### 任务 2.1：web_search 引擎初始化重构（4h）
**问题**：当前在 `_register_builtin_tools` 中一次性初始化所有搜索引擎，如果有引擎不可用则整体降级。

**要求**：
1. 改为延迟初始化：在第一次搜索时再按需初始化各个引擎
2. 失败引擎的自动隔离：单个引擎初始化失败不影响其他引擎
3. 定期重试失败的引擎（后台任务，间隔 5 分钟）
4. 在 `get_status` 中加入引擎健康状态

### 任务 2.2：扩展管理器单例化（3h）
**问题**：`_make_ext_mgr()` 在 `ext_install` 中被重复调用，每次创建新实例。

**要求**：
1. 将扩展管理器实例保存为 `self._ext_manager` 实例变量
2. 使用懒加载模式（首次访问时创建）
3. 移除 `_make_ext_mgr()` 函数，改为 `self._get_ext_manager()`
4. 确保线程安全（`threading.Lock`）

### 任务 2.3：工具调用限流器（5h）
新文件：`agent/rate_limiter.py`

**实现令牌桶算法：**
- 每个工具有独立的速率限制配置（`tools/__init__.py` 中注册时指定）
- 默认限制：普通工具 10次/秒，网络工具 5次/秒，Shell 工具 2次/秒
- 超出限制时返回 `{"ok": False, "error": "调用频率过高，请稍后重试", "retry_after": 0.5}`

### 任务 2.4：异步框架进度回调（6h）
在任务 1.6 的基础上，添加 WebSocket 通知和 REST API 端点：
- `GET /api/tasks/:id` — 获取任务状态
- `GET /api/tasks` — 获取所有任务列表
- `GET /api/tasks/:id/cancel` — 取消任务
- WebSocket 端点 `/ws/tasks/:id` — 实时推送任务进度

### 任务 2.5：工具注册代码模块化拆分（8h）
**问题**：`digital_life.py` 中的 `_register_builtin_tools()` 超过 1200 行，所有工具注册在一个方法中。

**要求**：
1. 将工具按类别拆分为独立模块：
   - `agent/tools/core_tools.py` — get_status, search_memory, remember 等
   - `agent/tools/file_tools.py` — read/write/list/search/get_file_info
   - `agent/tools/web_tools.py` — web_get/post/search/xpath/css/batch/download
   - `agent/tools/ext_tools.py` — ext_install/uninstall/list/toggle/discover/configure/send_channel
   - `agent/tools/pdf_tools.py` — read/merge/split/get_pdf_info
   - `agent/tools/software_tools.py` — search/install/list/uninstall
   - `agent/tools/system_tools.py` — shell_execute, run_program, processes, weather
   - `agent/tools/code_tools.py` — code_review, arch_diagram, humanize_zh
2. 每个模块导出 `register_all(digital_life_instance)` 函数
3. `_register_builtin_tools()` 简化为依次调用各模块的 register_all

## 阶段 3：稳定性提升

### 任务 3.1：工具调用链路追踪（4h）
**要求**：
1. 在 `agent/tools/__init__.py` 的 `call()` 函数中生成唯一的 `trace_id`
2. trace_id 通过日志上下文传递（使用 `logging.LoggerAdapter` 或 `contextvars`）
3. 在每条日志中输出 `trace_id`，便于问题排查
4. 关键工具（web_search, shell_execute）添加额外追踪信息

### 任务 3.2：压力测试（4h）
**要求**：
1. 编写压力测试脚本 `agent/tests/stress_test_tools.py`
2. 模拟 50 个并发工具调用（使用 `concurrent.futures.ThreadPoolExecutor`）
3. 测试场景：文件操作并发、网络请求并发、混合负载
4. 记录成功率、平均响应时间、P95/P99 延迟
5. 输出压力测试报告

### 任务 3.3：优化注册表查找性能（2h）
**要求**：
1. `_registry` 当前是普通 dict，查找 O(1) 已足够
2. 添加 `@lru_cache` 装饰 `get_tool_defs()` 和 `list_tools()`（需要手动失效机制）
3. 或者添加缓存版本号，工具注册/注销时递增

### 任务 3.4：工具健康检查端点（3h）
**要求**：
1. 添加 `GET /api/tools/health` 端点
2. 返回每个工具的最后调用状态（成功/失败/耗时）
3. 返回引擎健康状态（搜索引擎各引擎是否可用）
4. 返回整体健康评分

### 任务 3.5：最终回归和合入（4h）
1. 运行完整测试套件：`pytest agent/tests/ -v --tb=short`
2. 修复所有失败测试
3. 代码审查：检查新代码的质量和一致性
4. 创建合并 PR 到 master
5. 更新进度文档

## 验收标准
1. 所有测试通过，核心工具覆盖率 >= 75%
2. 新增工具注册到系统并能被 LLM 调用
3. 异步执行框架可正确运行长时间任务
4. 工具调用限流器生效
5. 架构模块化拆分完成，digital_life.py 工具注册部分 <= 200 行
6. 压力测试报告显示成功率 >= 99%，P95 延迟 <= 3s
