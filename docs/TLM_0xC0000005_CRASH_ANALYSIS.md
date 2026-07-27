# 0xC0000005 崩溃分析 — torch 线程数 vs GPU 卸载

> 基于 project_memory 记录和实际代码分析，评估 torch 线程数调整和 GPU 卸载能否解决 Windows 下的 0xC0000005 崩溃。

## 崩溃现象

| 测试文件 | 退出码 | 错误 | 根因 |
|---------|--------|------|------|
| test_memory_vector_store.py | 3221225477 (0xC0000005) | ACCESS_VIOLATION | torch C 扩展内存访问违规 |
| test_vector_store_sqlite_vec.py | 异常退出 | `AttributeError: '_thread.RLock' object has no attribute '_recursion_count'` | multiprocess ResourceTracker bug（非 0xC0000005） |

**关键区分**：这是两个不同的错误，不能混为一谈。

---

## 根因分析

### 0xC0000005 (ACCESS_VIOLATION)

`tool_router_hybrid.py:61` 已定义崩溃码并实现 `_diagnose_crash()` 诊断函数：

```python
_WIN_ACCESS_VIOLATION = -1073741819   # 0xC0000005
```

诊断结论：**"原生内存访问违规，常见于 PyTorch C 扩展或 SentenceTransformer 加载大模型"**

可能原因（按可能性排序）：

1. **DLL 加载冲突**（最可能）：torch 的 C 扩展（`torch_cpu.dll`、`torch_python.dll`）与 sqlite-vec 的 C 扩展在同一进程内加载，DLL 的内存布局冲突导致访问违规
2. **内存损坏**：torch 模型加载时分配大块内存，与 Python 堆内存冲突
3. **OpenMP 线程竞争**：torch 使用的 OpenMP 库与 Python 线程冲突（可能性低，通常表现为死锁而非崩溃）
4. **页面文件不足**：project_memory 记录 "OSError 1455 (page file too small)"，但这是模型加载失败，不是 0xC0000005

### ResourceTracker `_recursion_count` 错误

这是 **Python multiprocess 的已知 bug**，与 torch 无关：
- `multiprocess/resource_tracker.py` 的 `__del__` 方法访问 `_thread.RLock._recursion_count`
- Python 3.12 的 `RLock` 实现变更导致此属性不存在
- 影响：子进程清理时打印异常，但不影响主进程

---

## 方案评估

### 方案 1：调整 torch 线程数

| 配置 | 作用 | 对 0xC0000005 效果 | 对 ResourceTracker 效果 |
|------|------|-------------------|----------------------|
| `OMP_NUM_THREADS=4` | 限制 OpenMP 线程数 | ⚠️ 有限（若崩溃由线程竞争导致可缓解，DLL 冲突无效） | ❌ 无效 |
| `MKL_NUM_THREADS=4` | 限制 MKL 线程数 | ⚠️ 同上 | ❌ 无效 |
| `torch.set_num_threads(4)` | 运行时限制 torch 线程 | ⚠️ 同上 | ❌ 无效 |
| `torch.set_num_interop_threads(2)` | 限制 interop 线程 | ⚠️ 同上 | ❌ 无效 |

**结论**：**建议设置（作为预防措施），但不能根本解决 0xC0000005**。

- 如果崩溃根因是线程竞争 → 可能有效
- 如果崩溃根因是 DLL 冲突 → 无效
- 实际项目中 `Dockerfile.linux-test` 已设置 `OMP_NUM_THREADS=4`（Linux 环境预防）

### 方案 2：GPU 卸载

| 维度 | 评估 |
|------|------|
| 原理 | GPU torch 使用 CUDA 后端，不依赖 CPU C 扩展（MKL/OpenMP），可能避开 DLL 冲突 |
| 对 0xC0000005 效果 | ⚠️ 可能缓解（若根因是 CPU C 扩展冲突），但不保证 |
| 硬件要求 | 需要 NVIDIA GPU + CUDA 工具包 + CUDA 版 torch |
| 项目现状 | `diagnose_env.py:19` 已检查 `torch.cuda.is_available()`；`finetune_reranker.py:310` 根据 CUDA 选择设备 |
| 风险 | CUDA 版 torch 依赖更重（~2GB+），可能引入新的兼容性问题 |

**结论**：**不推荐作为 0xC0000005 的解决方案**。

- 生产环境通常无 GPU
- GPU torch 依赖更重，部署复杂度高
- 不保证解决崩溃（根因可能是内存损坏而非 C 扩展冲突）
- 如果已有 GPU 环境，可以用于性能优化，但崩溃问题应通过子进程隔离解决

---

## 推荐方案（已实施）

### 方案 3：子进程隔离（项目已实施，最可靠）

project_memory 明确记录：
> "子进程隔离是保障稳定性的必要措施：Cross-Encoder 和 Embedding 检索均已实现"

`skills_mgmt/reranker.py:25-28` 注释：
> Windows 崩溃防护（守【不易】）:
> Embedding 检索在 Windows CPU 环境下无隔离时会导致主进程 0xC0000005 崩溃
> Reranker 同样需要子进程隔离（multiprocessing.Process + terminate）

**原理**：将 torch 模型加载和推理放到子进程中，子进程崩溃不影响主进程。

**项目已有实现**：
- `tool_router_hybrid.py` — 崩溃诊断（`_diagnose_crash`）
- `skills_mgmt/reranker.py` — Reranker 子进程隔离
- `tool_router_reranker.py` — 路由器子进程隔离
- `system_tools.py:155` — `multiprocessing.Process + terminate()`

**为什么子进程隔离有效**：
- 子进程有独立的内存空间，DLL 冲突不会影响主进程
- 子进程崩溃后 `terminate()` 清理，主进程继续运行
- `_diagnose_crash()` 记录崩溃原因，便于排查

### 方案 4：Linux Docker 测试环境（刚创建）

`docker-compose.linux-test.yml` 已创建，包含：
- 完整的 torch + sentence-transformers + sqlite-vec 依赖
- `OMP_NUM_THREADS=4` 预防性配置
- HuggingFace 模型缓存卷（避免重复下载）
- 5 个测试服务（predownload-models / test / test-integration / test-all / test-sqlite-vec）

**Linux 优势**：
- 不存在 Windows 的 DLL 加载问题（Linux 用 .so，加载机制不同）
- torch CPU 版在 Linux 上长期稳定
- 不需要子进程隔离（但保留也无害）

---

## 行动建议

### 必须做（守【不易】）

1. **确保所有 embedding/reranker 调用都经过子进程隔离**
   - 已实施：SkillReranker、tool_router_hybrid
   - 待检查：test_memory_vector_store.py 的测试是否绕过了子进程隔离直接加载模型

2. **测试环境迁移到 Linux Docker**
   - 使用 `docker-compose.linux-test.yml` 运行涉及模型的测试
   - Windows 本地只运行不涉及模型的测试

### 建议做（【变易】）

3. **添加 OMP_NUM_THREADS 配置**（预防性，低风险）
   - 在 `.env` 文件中添加 `OMP_NUM_THREADS=4`
   - 在应用启动脚本中 export
   - 已在 `Dockerfile.linux-test` 中设置

4. **升级 Python 版本**（解决 ResourceTracker bug）
   - Python 3.12.x 的 `RLock._recursion_count` bug 在 3.12.4+ 已修复
   - 检查当前版本：`python --version`

### 不建议做（违【简易】）

5. **不要添加 GPU 卸载逻辑**
   - 增加部署复杂度（CUDA 依赖）
   - 不保证解决 0xC0000005
   - 生产环境通常无 GPU
   - 子进程隔离已足够

6. **不要在 Windows 本地运行涉及模型的测试**
   - 已知会崩溃
   - 使用 Linux Docker 替代

---

## 验证清单

- [ ] 确认 `test_memory_vector_store.py` 是否直接加载模型（绕过子进程隔离）
- [ ] 在 Linux Docker 中运行 `test-sqlite-vec` 服务，验证全部通过
- [ ] 检查 Python 版本是否 ≥ 3.12.4（解决 ResourceTracker bug）
- [ ] 在 `.env` 中添加 `OMP_NUM_THREADS=4`（预防性）
- [ ] 确认生产环境的 embedding 调用都经过子进程隔离
