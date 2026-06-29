# DigitalLife V1 到 V2 迁移指南

## 概述

本文档详细说明了云枢 DigitalLife 从 V1 版本迁移到 V2 版本的兼容性策略、注意事项和测试验证方法。

---

## 一、V1 与 V2 功能对比

| 功能模块 | V1 版本 | V2 版本 | 兼容性状态 |
|---------|---------|---------|-----------|
| **核心对话引擎** | 单线程同步处理 | 支持异步处理 | ✅ 向后兼容 |
| **记忆管理** | 基础记忆存储 | 增强向量记忆 + LifeTrace | ✅ 可选启用 |
| **人格系统** | 静态人格配置 | 动态人格注入 + Distillation | ✅ 可选启用 |
| **规划引擎** | 简单任务规划 | ReAct 循环 + 多步推理 | ✅ 可选启用 |
| **权限系统** | 基础权限检查 | 细粒度权限控制 | ✅ 向后兼容 |
| **工具调用** | 基础工具调用 | 增强工具调用服务 | ✅ 向后兼容 |
| **懒加载机制** | 无 | 模块延迟加载 | ✅ 透明兼容 |

---

## 二、V2 新功能开关

V2 功能通过配置文件中的 `features` 字段进行控制：

```yaml
features:
  v2_lifetrace: false      # LifeTrace 记忆系统
  v2_persona: false        # 动态人格注入
  v2_distillation: false   # 人格蒸馏（从对话学习偏好）
```

### 功能启用建议

| 功能 | 适用场景 | 性能影响 | 推荐设置 |
|------|---------|---------|---------|
| `v2_lifetrace` | 需要长期记忆、上下文理解 | 中等（增加内存占用） | 生产环境启用 |
| `v2_persona` | 需要个性化响应、人格一致性 | 低 | 生产环境启用 |
| `v2_distillation` | 需要从用户交互学习偏好 | 低（后台任务） | 可选启用 |

---

## 三、迁移注意事项

### 3.1 配置文件迁移

**V1 配置格式：**
```yaml
memory:
  max_tokens: 8000
behavior:
  default_mode: NORMAL
planning:
  enabled: false
```

**V2 配置格式（向后兼容）：**
```yaml
memory:
  max_tokens: 8000
behavior:
  default_mode: NORMAL
planning:
  enabled: false
features:
  v2_lifetrace: true
  v2_persona: true
  v2_distillation: false
```

> **注意**：V1 配置格式在 V2 中完全兼容，无需修改现有配置即可升级。

### 3.2 模块可用性检查

V2 新功能依赖可选模块，运行时会自动检测：

```python
# 模块可用性检查流程
1. 启动时检测 lifetrace、persona、planning 等模块
2. 如果模块不可用，自动禁用对应功能
3. 记录警告日志，但不影响启动
4. 通过 `_LIFETRACE_AVAILABLE` 等标志判断状态
```

### 3.3 懒加载机制

V2 引入了模块懒加载机制，主要特性：

- **按需加载**：模块仅在首次使用时加载
- **优雅降级**：加载失败不影响核心功能
- **性能优化**：减少启动时间和内存占用

懒加载触发时机：
| 模块 | 触发时机 |
|------|---------|
| LifeTrace | 首次调用 `_ensure_lifetrace()` |
| Persona | 首次调用 `_ensure_persona()` |
| Distillation | 首次调用 `_ensure_distillation()` |

### 3.4 API 兼容性

所有 V1 API 在 V2 中保持兼容：

| V1 API | V2 状态 | 说明 |
|--------|---------|------|
| `chat()` | ✅ 兼容 | 核心对话接口 |
| `process()` | ✅ 兼容 | 处理对话请求 |
| `start()` / `stop()` | ✅ 兼容 | 生命周期管理 |
| `reset()` | ✅ 兼容 | 重置状态 |

---

## 四、兼容性测试策略

### 4.1 测试覆盖范围

| 测试类别 | 测试内容 | 优先级 |
|---------|---------|-------|
| **模块安全导入** | 可选模块缺失时的优雅降级 | P0 |
| **配置兼容性** | V1 配置在 V2 中的行为 | P0 |
| **行为闭环** | 用户输入→响应的完整路径 | P0 |
| **权限系统** | 安全/危险操作的权限检查 | P1 |
| **状态持久化** | 会话信息的保存与恢复 | P1 |
| **V2 功能集成** | LifeTrace/Persona/Distillation | P2 |
| **懒加载机制** | 模块延迟初始化行为 | P2 |

### 4.2 测试执行命令

```bash
# 运行所有集成测试
python -m pytest tests/integration/test_digital_life_integration.py -v

# 仅运行 V2 兼容性测试
python -m pytest tests/integration/test_digital_life_integration.py::TestV2Compatibility -v

# 仅运行懒加载测试
python -m pytest tests/integration/test_digital_life_integration.py::TestLazyLoaderCompatibility -v
```

### 4.3 测试验证矩阵

| 测试场景 | 预期行为 |
|---------|---------|
| 禁用所有 V2 功能 | 行为与 V1 完全一致 |
| 仅启用部分 V2 功能 | 启用的功能生效，未启用的保持 V1 行为 |
| 模块不可用但配置启用 | 功能自动禁用，记录警告日志 |
| V1 遗留配置 | 正确解析并应用 |

---

## 五、迁移步骤

### 步骤 1：备份配置文件
```bash
cp config.yaml config.yaml.v1.bak
```

### 步骤 2：升级代码
```bash
git pull origin main
```

### 步骤 3：验证基础功能
```bash
python -m pytest tests/integration/test_digital_life_integration.py::TestConstructionAndConfiguration -v
```

### 步骤 4：逐步启用 V2 功能
```yaml
# 第一步：启用 LifeTrace
features:
  v2_lifetrace: true
  v2_persona: false
  v2_distillation: false

# 第二步：启用 Persona
features:
  v2_lifetrace: true
  v2_persona: true
  v2_distillation: false

# 第三步：启用 Distillation（可选）
features:
  v2_lifetrace: true
  v2_persona: true
  v2_distillation: true
```

### 步骤 5：运行完整测试套件
```bash
python -m pytest tests/integration/test_digital_life_integration.py -v
```

---

## 六、故障排查指南

### 6.1 懒加载失败排查

**症状**：V2 功能未生效

**排查步骤**：
1. 检查日志中是否有模块导入失败警告
2. 确认对应模块已安装
3. 检查 `_LIFETRACE_AVAILABLE` 等标志状态
4. 运行带详细日志的测试：

```bash
python -m pytest tests/integration/test_digital_life_integration.py::TestV2PersonaIntegration -v -s
```

### 6.2 常见错误及解决方案

| 错误 | 原因 | 解决方案 |
|------|------|---------|
| `ModuleNotFoundError: No module named 'lifetrace'` | lifetrace 模块未安装 | `pip install lifetrace` |
| `_v2_lifetrace` 为 True 但功能未生效 | 模块加载失败被静默降级 | 检查日志中的警告信息 |
| 配置未生效 | 配置文件路径错误 | 确认配置文件路径正确 |

---

## 七、版本兼容性矩阵

| 云枢版本 | Python 版本 | 推荐配置 |
|---------|------------|---------|
| V1.x | 3.8 - 3.10 | 不启用 V2 功能 |
| V2.x | 3.10 - 3.12 | 可启用 V2 功能 |

---

## 八、总结

V2 版本采用**渐进式增强**策略：
- ✅ **完全向后兼容**：V1 配置和 API 无需修改
- ✅ **可选启用**：V2 功能通过配置开关控制
- ✅ **优雅降级**：模块缺失不影响核心功能
- ✅ **懒加载**：按需加载，优化启动性能

建议迁移策略：
1. 先升级代码，保持 V1 配置运行
2. 验证基础功能正常
3. 逐步启用 V2 功能
4. 运行完整测试套件验证

---

**文档版本**: v1.0  
**创建日期**: 2026-06-23  
**适用版本**: DigitalLife V2.x