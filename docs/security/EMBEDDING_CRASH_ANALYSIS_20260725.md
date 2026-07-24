# Embedding 检索 0xC0000005 崩溃技术排查报告

> **报告编号**：EMB-CRASH-001  
> **生成日期**：2026-07-25  
> **严重级别**：P1（功能阻断）  
> **影响范围**：Windows CPU 环境下 Embedding 语义检索不可用  
> **临时方案**：`AGENT_HYBRID_EMBEDDING=0` 禁用 Embedding，降级为纯 BM25 + Reranker

---

## 一、问题概述

### 1.1 现象描述

在 Windows CPU 环境下，调用 `tool_router_hybrid.py` 的 Embedding 语义检索时，主进程触发原生崩溃：

- **崩溃码**：`0xC0000005`（ACCESS_VIOLATION，内存访问违规）
- **退出码**：`-1073741819`（即 `0xC0000005` 的有符号表示）
- **崩溃位置**：[agent/tool_router_hybrid.py:610-620](file:///c:/Users/Administrator/agent/agent/tool_router_hybrid.py#L610-L620)
- **崩溃模块**：`torch` / `SentenceTransformer` 加载模型时

### 1.2 影响分析

| 影响项 | 说明 |
|--------|------|
| 功能影响 | Embedding 语义检索完全不可用 |
| 降级方案 | 纯 BM25 + Reranker（检索质量略降） |
| 环境限制 | 仅 Windows CPU 环境，Linux/GPU 环境正常 |
| 触发条件 | `AGENT_HYBRID_EMBEDDING` 未设为 `0` 时自动触发 |

---

## 二、崩溃定位

### 2.1 崩溃点代码

**文件**：[agent/tool_router_hybrid.py](file:///c:/Users/Administrator/agent/agent/tool_router_hybrid.py)  
**行号**：L610-L620

```python
# EmbeddingIndex._encode_via_worker 方法
try:
    line = self._proc.stdout.readline()  # ← 崩溃点：子进程已异常退出
except OSError as e:
    logger.warning(json.dumps({
        "module_name": "tool_router_hybrid",
        "action": "embedding.encode.read_failed",
        "error": str(e),
    }, ensure_ascii=False))
    self._init_failed = True
    return None
```

### 2.2 调用链分析

```
hybrid_select_tools()                          # 入口
  └── get_hybrid_retriever()                   # 获取单例
        └── HybridRetriever.query()            # 查询
              └── EmbeddingIndex.search()       # L712-823
                    ├── _ensure_worker()        # L470-572 启动子进程
                    │     └── _WORKER_SCRIPT_EMBEDDING  # 加载模型
                    └── _encode_via_worker()    # L573-660 ← 崩溃
                          └── self._proc.stdout.readline()  # L610
                                ↑
                          子进程因 0xC0000005 崩溃退出
                          readline() 返回空/抛出 OSError
```

### 2.3 子进程启动流程

**文件**：[agent/tool_router_hybrid.py:470-572](file:///c:/Users/Administrator/agent/agent/tool_router_hybrid.py#L470-L572)

```python
# EmbeddingIndex._ensure_worker 方法
def _ensure_worker(self) -> bool:
    # 1. 启动子进程（multiprocessing.Process）
    # 2. 子进程执行 _WORKER_SCRIPT_EMBEDDING
    # 3. 子进程加载 SentenceTransformer 模型
    # 4. 等待 "ready" 信号
    # 5. 如果超时/失败，设置 _init_failed = True
```

---

## 三、根因分析

### 3.1 直接原因

**PyTorch / SentenceTransformer 在 Windows CPU 环境下加载模型时触发原生崩溃**。

- `0xC0000005` 是 Windows 的 ACCESS_VIOLATION（内存访问违规）
- 这是 C/C++ 层面的崩溃，Python 的 `try/except` **无法捕获**
- 崩溃发生在 `torch` 的 C 扩展中，可能是：
  - 模型加载时的内存映射（mmap）冲突
  - CPU 指令集不兼容（如 AVX 指令缺失）
  - 线程安全问题

### 3.2 间接原因

子进程隔离机制**不完善**：

| 问题点 | 说明 |
|--------|------|
| 崩溃检测缺失 | `_ensure_worker()` 未检测子进程 `poll()` 退出码 |
| 错误恢复不足 | `_encode_via_worker()` 读取失败后未重启子进程 |
| 崩溃诊断缺失 | 未调用 `_diagnose_crash()` 分析退出码 |
| 日志不完整 | 崩溃时无法记录子进程 stderr 输出 |

### 3.3 与 Cross-Encoder 对比

**Cross-Encoder（已修复）**：[agent/tool_router_reranker.py:220-310](file:///c:/Users/Administrator/agent/agent/tool_router_reranker.py#L220-L310)

| 特性 | Cross-Encoder (Reranker) | Embedding (Hybrid) |
|------|--------------------------|---------------------|
| 子进程隔离 | ✅ 已实现 | ⚠️ 部分实现 |
| 崩溃码检测 | ✅ `_diagnose_crash()` | ❌ 缺失 |
| 崩溃码常量 | ✅ 5 个常量定义 | ❌ 无 |
| 自动重试 | ✅ 子进程重启 | ❌ 仅设 `_init_failed` |
| 退出码检查 | ✅ `poll()` 检测 | ❌ 缺失 |
| stderr 捕获 | ✅ 完整捕获 | ⚠️ 部分 |

**Cross-Encoder 的 5 个崩溃码常量**（tool_router_reranker.py）：

```python
# 0xC0000005 及类似崩溃码需在 tool_router_reranker.py 中通过 try/except 捕获
CRASH_CODE_ACCESS_VIOLATION = 0xC0000005      # 内存访问违规
CRASH_CODE_STACK_OVERFLOW = 0xC00000FD         # 栈溢出
CRASH_CODE_ILLEGAL_INSTRUCTION = 0xC000001D    # 非法指令
```

---

## 四、临时方案（已实施）

### 4.1 禁用 Embedding

**环境变量**：`.env` 中设置

```bash
# 禁用 Embedding，降级为纯 BM25 + Reranker
AGENT_HYBRID_EMBEDDING=0
```

### 4.2 降级检索流程

```
正常流程: BM25 → Embedding（语义增强）→ Reranker（精排）
降级流程: BM25 → Reranker（精排）
```

### 4.3 影响评估

| 指标 | 正常流程 | 降级流程 | 差异 |
|------|---------|---------|------|
| 检索准确率 | 高 | 中 | 略降（无语义匹配） |
| 响应时间 | 慢（Embedding 开销） | 快 | 降级更快 |
| 召回率 | 高 | 中 | 语义相似项可能遗漏 |

---

## 五、修复方案

### 5.1 方案 A：完善子进程隔离（推荐）

**修改文件**：[agent/tool_router_hybrid.py](file:///c:/Users/Administrator/agent/agent/tool_router_hybrid.py)

**修改点 1**：`_ensure_worker()` 增加崩溃检测

```python
def _ensure_worker(self) -> bool:
    if self._init_failed:
        return False
    if self._proc and self._proc.is_alive():
        return True

    # 启动子进程...
    self._proc = multiprocessing.Process(target=...)

    # 等待 ready 信号时检测崩溃
    line = self._proc.stdout.readline()
    if not line:
        # 【新增】检查子进程退出码
        exit_code = self._proc.poll()
        if exit_code is not None:
            crash_info = self._diagnose_crash(exit_code)
            logger.error(json.dumps({
                "module_name": "tool_router_hybrid",
                "action": "embedding.worker.crashed",
                "exit_code": exit_code,
                "crash_info": crash_info,
            }, ensure_ascii=False))
            self._init_failed = True
            return False
```

**修改点 2**：新增 `_diagnose_crash()` 方法

```python
# 崩溃码常量（与 tool_router_reranker.py 对齐）
CRASH_CODES = {
    0xC0000005: "ACCESS_VIOLATION",
    0xC00000FD: "STACK_OVERFLOW",
    0xC000001D: "ILLEGAL_INSTRUCTION",
}

def _diagnose_crash(self, exit_code: int) -> dict:
    """诊断子进程崩溃原因"""
    # Windows 退出码为负数（-1073741819 = 0xC0000005）
    unsigned_code = exit_code & 0xFFFFFFFF
    crash_name = CRASH_CODES.get(unsigned_code, "UNKNOWN")
    return {
        "exit_code": exit_code,
        "unsigned_code": f"0x{unsigned_code:08X}",
        "crash_type": crash_name,
        "platform": sys.platform,
    }
```

**修改点 3**：`_encode_via_worker()` 增加自动重试

```python
def _encode_via_worker(self, texts: list) -> list:
    for attempt in range(2):  # 最多重试 1 次
        try:
            # 检查子进程是否存活
            if self._proc and not self._proc.is_alive():
                exit_code = self._proc.poll()
                if exit_code is not None:
                    crash_info = self._diagnose_crash(exit_code)
                    logger.warning(...)
                    if attempt == 0:
                        self._init_failed = False
                        self._proc = None
                        if self._ensure_worker():
                            continue  # 重试
                    self._init_failed = True
                    return None

            # 正常编码流程
            self._proc.stdin.write(...)
            line = self._proc.stdout.readline()
            ...
        except OSError as e:
            ...
```

### 5.2 方案 B：使用子进程探测（已有）

**文件**：[agent/tool_router_hybrid.py:145-185](file:///c:/Users/Administrator/agent/agent/tool_router_hybrid.py#L145-L185)

```python
# _run_embedding_probe 函数
# 在启用 Embedding 前先探测模型加载是否安全
# 如果探测失败，自动设为 AGENT_HYBRID_EMBEDDING=0
```

**当前状态**：已实现，但可能未在所有路径上生效。

### 5.3 方案 C：改用 ONNX Runtime（长期）

将 SentenceTransformer 模型转为 ONNX 格式，使用 `onnxruntime` 推理：
- 避免 PyTorch 的 C 扩展崩溃
- 更好的跨平台兼容性
- 更低的内存占用

---

## 六、测试验证方案

### 6.1 崩溃复现

```bash
# 1. 设置环境（启用 Embedding）
set AGENT_HYBRID_EMBEDDING=1

# 2. 运行检索（触发崩溃）
python -c "from agent.tool_router_hybrid import hybrid_select_tools; hybrid_select_tools('test query')"

# 3. 观察崩溃
# 预期：进程退出码 -1073741819 (0xC0000005)
```

### 6.2 修复验证

```bash
# 1. 应用方案 A 修复
# 2. 设置环境（启用 Embedding）
set AGENT_HYBRID_EMBEDDING=1

# 3. 运行检索（应优雅降级）
python -c "from agent.tool_router_hybrid import hybrid_select_tools; hybrid_select_tools('test query')"

# 4. 预期结果：
# - 不崩溃
# - 日志输出: embedding.worker.crashed + crash_info
# - 自动降级为 BM25 + Reranker
# - 返回检索结果
```

### 6.3 回归测试

```bash
# 确保降级模式正常
set AGENT_HYBRID_EMBEDDING=0
python -m pytest tests/test_tool_router_hybrid.py -v
```

---

## 七、时间线

| 日期 | 事件 |
|------|------|
| 2026-07-21 | 发现 Embedding 检索在 Windows CPU 环境崩溃 |
| 2026-07-21 | 实施临时方案 `AGENT_HYBRID_EMBEDDING=0` |
| 2026-07-21 | Cross-Encoder 子进程隔离验证通过 |
| 2026-07-25 | 生成崩溃排查报告（本文档） |
| 待定 | 实施方案 A 修复（完善子进程隔离） |
| 待定 | 方案 C 评估（ONNX Runtime 迁移） |

---

## 八、结论

### 8.1 根因

PyTorch / SentenceTransformer 在 Windows CPU 环境下加载模型时触发 `0xC0000005` 原生崩溃，现有子进程隔离机制不完善，未正确检测和处理子进程崩溃退出码。

### 8.2 当前状态

- **临时方案**：`AGENT_HYBRID_EMBEDDING=0` 已生效，检索降级为 BM25 + Reranker
- **功能影响**：Embedding 语义检索不可用，但基础检索功能正常
- **风险评估**：低（降级方案可用，不影响核心功能）

### 8.3 修复优先级

| 优先级 | 方案 | 工作量 | 风险 |
|--------|------|--------|------|
| P1 | 方案 A：完善子进程隔离 | 2-3 小时 | 低 |
| P2 | 方案 B：确保探测全覆盖 | 1 小时 | 低 |
| P3 | 方案 C：ONNX Runtime 迁移 | 2-3 天 | 中 |

**建议**：先实施方案 A（完善子进程隔离 + 崩溃检测 + 自动重试），再评估方案 C 的长期可行性。

---

*报告生成人：Yi-Jing Coding Agent*  
*报告生成时间：2026-07-25*
