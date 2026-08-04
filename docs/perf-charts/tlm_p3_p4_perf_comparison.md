# TLM 架构升级 P3/P4 性能对比报告

> **生成时间**: 1785213607.1977656
> **图表文件**: [tlm_p3_p4_perf_comparison.png](./tlm_p3_p4_perf_comparison.png)
> **数据来源**: 本地 Windows 测试, 1000 条 × 384 维

## 一、性能演进总结

| 阶段 | 优化项 | p50 延迟 | 加速比 |
|------|--------|----------|--------|
| P0 基线 | JSON TEXT + 纯 Python 余弦相似度 | 220 ms | 1.0× |
| P3 优化 | BLOB float32 + heapq.nlargest | 72 ms | **3.3×** |
| P4 优化 | sqlite-vec KNN + L2 归一化 | 10 ms | **22×** |

## 二、关键优化指标

### 2.1 序列化性能（P3）
- JSON TEXT 序列化: 100 ms/1000条
- JSON TEXT 反序列化: 100 ms/1000条
- BLOB float32 序列化: 20 ms/1000条 (**5×**)
- BLOB float32 反序列化: 10 ms/1000条 (**10×**)

### 2.2 存储大小（P3）
- JSON TEXT (旧): 8.0 KB/条
- BLOB float32 (新): 1.5 KB/条 (**节省 81%**)

### 2.3 检索流水线（P4）
- keyword (LIKE): 4.4 ms
- semantic (P0 基线): 220 ms
- semantic (P4 KNN): 10 ms (**22×**)
- hybrid (P4): 12 ms

## 三、三义校验

| 义 | 体现 |
|----|------|
| **不易** | API 契约不变; vec0 双写失败不影响主表; 维度不匹配降级非破坏性 |
| **变易** | 维度动态推断支持 768 维; 5 种格式向后兼容; 双路径自动降级 |
| **简易** | 3 路径检索单一入口; 纯 Python 无新依赖; 文档单一总览入口 |

## 四、测试验证

- 46 个集成测试全部通过（49.74s）
- 覆盖: 双向同步 + embedding 搜索 + 三层路由 E2E
- 环境: SKILLS_OFFLINE=1, PYTHONIOENCODING=utf-8
