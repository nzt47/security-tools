# DigitalLife V2 功能部署指南

> 本文档详细说明了 DigitalLife V2 功能模块的配置、部署和性能调优方法。

## 📋 目录

1. [V2 功能概述](#v2-功能概述)
2. [配置参数详解](#配置参数详解)
3. [快速部署](#快速部署)
4. [高级配置](#高级配置)
5. [性能监控](#性能监控)
6. [故障排查](#故障排查)

---

## 🎯 V2 功能概述

DigitalLife V2 引入了三个增强模块：

| 模块 | 功能 | 依赖 |
|------|------|------|
| **LifeTrace** | 三层记忆树系统（源/主题/全局） | `lifetrace` 包 |
| **Persona** | 人格模型系统 | `persona` 包 |
| **Distillation** | 人格蒸馏学习 | `persona` 包 |

### 特性

- ✅ **模块化设计**：每个功能独立，可按需启用
- ✅ **智能降级**：依赖模块不可用时自动禁用并警告
- ✅ **性能监控**：内置加载时间追踪
- ✅ **向后兼容**：默认禁用，不影响现有部署

---

## ⚙️ 配置参数详解

### 完整配置示例

```python
config = {
    # === V2 功能开关 ===
    "features": {
        "v2_lifetrace": True,       # 启用 LifeTrace 记忆系统
        "v2_persona": True,         # 启用人格系统
        "v2_distillation": True,    # 启用人格蒸馏学习
    },
    
    # === LifeTrace 配置 ===
    "lifetrace": {
        "data_dir": "./data/lifetrace",  # 记忆数据目录
    },
    
    # === Persona 配置 ===
    "persona": {
        "persona_path": "./data/persona.json",  # 人格配置文件路径
    },
    
    # === Distillation 配置 ===
    "distillation": {
        "data_dir": "./data/persona",      # 偏好数据目录
        "interval": 10,                    # 批量学习间隔（交互次数）
    },
    
    # === 其他可选配置 ===
    "sensor": {
        "watch_dirs": ["./workspace"],
        "enable_change_detection": True,
        "enable_event_monitor": True,
    },
    
    "memory": {
        "data_dir": "./memory_data",
    },
    
    "backup_dir": "./.backups",
}
```

### 配置参数说明

#### `features` (V2 功能开关)

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `v2_lifetrace` | bool | `False` | 启用三层记忆树系统 |
| `v2_persona` | bool | `False` | 启用人格模型 |
| `v2_distillation` | bool | `False` | 启用人格蒸馏学习 |

#### `lifetrace` (记忆系统配置)

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `data_dir` | str | `"./data/lifetrace"` | 记忆数据持久化目录 |

#### `persona` (人格系统配置)

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `persona_path` | str | `None` | 人格配置文件路径 |

#### `distillation` (人格蒸馏配置)

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `data_dir` | str | `"./data/persona"` | 偏好学习数据目录 |
| `interval` | int | `10` | 批量学习触发间隔（交互次数） |

---

## 🚀 快速部署

### 方式 1：仅启用基础功能（推荐生产环境）

```python
from agent.digital_life import DigitalLife

# 基础版本 - 稳定可靠
dl = DigitalLife()
```

### 方式 2：启用完整 V2 功能

```python
from agent.digital_life import DigitalLife

# 完整 V2 版本
config = {
    "features": {
        "v2_lifetrace": True,
        "v2_persona": True,
        "v2_distillation": True,
    },
    "lifetrace": {
        "data_dir": "./data/lifetrace"
    },
    "distillation": {
        "data_dir": "./data/persona",
        "interval": 10
    }
}

dl = DigitalLife(config=config)
```

### 方式 3：部分启用 V2 功能

```python
# 仅启用记忆系统
config = {
    "features": {
        "v2_lifetrace": True,
        "v2_persona": False,
        "v2_distillation": False,
    }
}

dl = DigitalLife(config=config)
```

---

## 🎨 高级配置

### 生产环境配置示例

```python
import logging

# 设置日志级别
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# 生产环境配置
production_config = {
    "features": {
        "v2_lifetrace": True,
        "v2_persona": True,
        "v2_distillation": True,
    },
    
    "lifetrace": {
        "data_dir": "/var/lib/Yunshu/lifetrace"
    },
    
    "persona": {
        "persona_path": "/etc/Yunshu/persona.json"
    },
    
    "distillation": {
        "data_dir": "/var/lib/Yunshu/persona",
        "interval": 20  # 生产环境可以使用更大的间隔
    },
    
    "sensor": {
        "watch_dirs": ["/workspace", "/home"],
        "enable_change_detection": True,
        "enable_event_monitor": True,
    },
    
    "backup_dir": "/var/backups/Yunshu",
    
    "vector_memory": {
        "collection_name": "Yunshu_memory",
        "persist_dir": "/var/lib/Yunshu/memory"
    },
}

dl = DigitalLife(config=production_config)
```

### 开发环境配置示例

```python
# 开发环境配置
dev_config = {
    "features": {
        "v2_lifetrace": True,
        "v2_persona": True,
        "v2_distillation": True,
    },
    
    "lifetrace": {
        "data_dir": "./dev_data/lifetrace"
    },
    
    "distillation": {
        "data_dir": "./dev_data/persona",
        "interval": 5  # 开发环境使用更小的间隔
    },
    
    "sensor": {
        "watch_dirs": ["./workspace"],
        "enable_change_detection": True,
        "enable_event_monitor": True,
    },
}

dl = DigitalLife(config=dev_config)
```

---

## 📊 性能监控

### 查看 V2 功能状态

```python
# 获取 V2 功能状态
features = dl.get_v2_features()
print(features)
# {
#     "v2_lifetrace": True,
#     "v2_persona": True,
#     "v2_distillation": True,
#     "available": {
#         "lifetrace": True,
#         "persona": True
#     }
# }
```

### 查看性能报告

```python
# 获取性能报告
perf_report = dl.get_performance_report()
print(perf_report)
# {
#     "performance_summary": {
#         "v2.lifetrace": {
#             "count": 1,
#             "total": 5.23,
#             "avg": 5.23,
#             "min": 5.23,
#             "max": 5.23
#         }
#     },
#     "v2_modules": {
#         "lifetrace": True,
#         "persona": True,
#         "distillation": True
#     }
# }
```

### 查看完整状态

```python
# 获取完整状态报告
status = dl.get_status()
print(status)
```

### 日志输出示例

初始化时会输出详细的性能信息：

```
================================================================================
🚀 云枢初始化开始
================================================================================
📋 模块可用性检查:
   - LifeTrace: ✅ 可用
   - Persona: ✅ 可用
   - Planning: ✅ 可用
   - Vector Memory: ✅ 可用
   - Monitoring: ✅ 可用
   - Voice: ✅ 可用
   - OCR: ✅ 可用

🎛️  V2 功能配置:
   请求: v2_lifetrace=True, v2_persona=True, v2_distillation=True
   实际: v2_lifetrace=True, v2_persona=True, v2_distillation=True

================================================================================
开始初始化各子系统
================================================================================
[性能] 模块 'V2-LifeTrace' 加载完成，耗时: 3.45ms
[性能] 模块 'V2-Persona' 加载完成，耗时: 2.18ms
[性能] 模块 'V2-Distillation' 加载完成，耗时: 1.67ms

================================================================================
🎉 云枢初始化完成！
================================================================================

📊 V2 模块加载性能:
   • v2.lifetrace: 平均=3.45ms, 最小=3.45ms, 最大=3.45ms
   • v2.persona: 平均=2.18ms, 最小=2.18ms, 最大=2.18ms
   • v2.distillation: 平均=1.67ms, 最小=1.67ms, 最大=1.67ms

================================================================================
```

---

## 🔧 故障排查

### 常见问题

#### 1. V2 功能未启用

**症状**：
```
🎛️  V2 功能配置:
   请求: v2_lifetrace=True, v2_persona=True, v2_distillation=True
   实际: v2_lifetrace=False, v2_persona=False, v2_distillation=False
```

**原因**：
- 依赖模块未安装

**解决方案**：
```bash
# 安装依赖
pip install lifetrace persona
```

#### 2. 模块加载失败

**症状**：
```
[性能] 模块 'V2-LifeTrace' 加载失败，耗时: 5.23ms，错误: No module named 'xxx'
❌ V2-LifeTrace 初始化失败: No module named 'xxx'
```

**原因**：
- 依赖包版本不兼容

**解决方案**：
```bash
# 升级依赖
pip install --upgrade lifetrace persona
```

#### 3. 性能问题

**症状**：
- V2 模块加载时间过长

**诊断**：
```python
# 查看性能报告
perf_report = dl.get_performance_report()
print(perf_report)
```

**优化建议**：
- 调整 `distillation.interval` 参数
- 使用 SSD 存储数据目录
- 减少 `watch_dirs` 监控目录数量

---

## 📝 最佳实践

### 1. 渐进式部署

```python
# 阶段1：基础版本（稳定）
dl = DigitalLife()

# 阶段2：添加记忆功能
config = {"features": {"v2_lifetrace": True}}
dl = DigitalLife(config=config)

# 阶段3：完整 V2 功能
config = {
    "features": {
        "v2_lifetrace": True,
        "v2_persona": True,
        "v2_distillation": True,
    }
}
dl = DigitalLife(config=config)
```

### 2. 配置验证

```python
# 在部署前验证配置
def validate_config(config):
    features = config.get("features", {})
    
    if features.get("v2_distillation") and not features.get("v2_persona"):
        raise ValueError("v2_distillation 需要 v2_persona 同时启用")
    
    return True

# 使用
validate_config(config)
dl = DigitalLife(config=config)
```

### 3. 监控集成

```python
# 集成到监控系统
import logging

# 自定义日志处理器
class V2MetricsHandler(logging.Handler):
    def emit(self, record):
        if "性能" in record.getMessage():
            # 发送到监控系统
            send_metrics_to_prometheus(record.getMessage())

# 配置
handler = V2MetricsHandler()
handler.setLevel(logging.INFO)
logging.getLogger().addHandler(handler)
```

---

## 📚 相关文档

- [DigitalLife API 参考](api_digital_life.md)
- [LifeTrace 记忆系统](lifetrace.md)
- [Persona 人格系统](persona.md)
- [性能监控指南](performance.md)

---

## 🔄 更新日志

### v2.0.0 (2026-05-31)

- ✨ 新增 V2 功能模块
- ✨ 实现 LifeTrace 三层记忆系统
- ✨ 实现 Persona 人格模型
- ✨ 实现 Distillation 人格蒸馏
- ✨ 添加性能监控功能
- ✨ 优化模块加载性能
- ✅ 完整的单元测试覆盖

---

**文档版本**: v1.0  
**最后更新**: 2026-05-31  
**维护者**: 云枢开发团队
