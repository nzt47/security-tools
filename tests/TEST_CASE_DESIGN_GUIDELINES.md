# 云枢系统测试用例设计规范

本文件定义了测试用例的设计原则、命名规范和最佳实践

## 测试用例命名规范

```
命名模式: test_{模块}_{功能}_{场景}_{预期结果}

示例:
- test_memory_store_success
- test_memory_retrieve_with_filters
- test_permission_deny_dangerous_operation
- test_permission_allow_safe_operation
- test_monitoring_collect_metrics
```

## 测试用例组织结构

```
tests/
├── unit/                    # 单元测试
│   ├── test_memory.py       # 记忆模块单元测试
│   ├── test_permission.py   # 权限模块单元测试
│   └── test_monitoring.py   # 监控模块单元测试
│
├── integration/             # 集成测试
│   ├── test_v2_features.py  # V2功能集成测试
│   └── test_end_to_end.py   # 端到端测试
│
├── e2e/                     # 端到端测试
│   ├── test_user_flows.py   # 用户流程测试
│   └── test_system_flows.py # 系统流程测试
│
├── fixtures/                # 测试数据
│   ├── test_config.json     # 测试配置
│   └── test_cases.json      # 测试用例数据
│
└── conftest.py             # pytest配置
```

## 测试用例设计原则

1. **单一职责原则**
   - 每个测试用例只测试一个功能点
   - 测试用例名称清晰表达测试意图

2. **独立性原则**
   - 测试用例之间相互独立
   - 使用fixtures管理测试依赖
   - 测试前后做好环境清理

3. **可重复性原则**
   - 测试用例可以多次执行
   - 测试结果不受执行顺序影响
   - 使用固定的测试数据

4. **可维护性原则**
   - 公共逻辑提取到fixtures或helpers
   - 复杂的测试数据使用JSON/YAML管理
   - 添加清晰的注释和文档字符串

5. **覆盖率原则**
   - 覆盖正常路径和异常路径
   - 覆盖边界条件
   - 覆盖错误处理

## 测试用例优先级定义

**P0 - 关键测试（必须通过）**
- 核心功能测试
- 安全相关测试
- 权限控制测试
- 系统稳定性测试

**P1 - 重要测试（建议通过）**
- 功能完整性测试
- 集成测试
- 监控告警测试

**P2 - 一般测试（尽量覆盖）**
- 边界条件测试
- 性能测试
- 异常场景测试

## 测试覆盖率目标

目标覆盖率:
- 核心模块（agent/）: 80%+
- 记忆系统: 85%+
- 权限系统: 90%+
- 监控系统: 75%+
- 整体项目: 70%+

## 测试数据管理策略

1. **内联数据**
   - 简单的测试数据直接写在测试文件中
   - 使用pytest fixtures

2. **外部文件**
   - 复杂的测试数据存储在JSON/YAML文件
   - 放在 tests/fixtures/ 目录
   - 通过TestDataManager加载

3. **动态生成**
   - 使用faker库生成测试数据
   - 使用工厂模式创建复杂对象

4. **脱敏原则**
   - 测试数据不包含真实的敏感信息
   - 使用脱敏或匿名的测试数据

## 测试执行策略

1. **快速测试 (标记: @pytest.mark.quick)**
   - 执行时间 < 1秒
   - 不依赖外部服务
   - 在CI中默认执行

2. **完整测试 (标记: @pytest.mark.slow)**
   - 执行时间较长
   - 可能依赖外部服务
   - 使用 --runslow 选项执行

3. **特殊测试**
   - @pytest.mark.requires_llm: 需要LLM服务
   - @pytest.mark.integration: 集成测试
   - @pytest.mark.e2e: 端到端测试

## 最佳实践

1. 使用assert而不是if
   ```python
   # ❌ 不要这样
   if result == expected:
       pass

   # ✅ 要这样
   assert result == expected
   ```

2. 使用具体的断言消息
   ```python
   assert user.id is not None, "用户ID不应该为空"
   ```

3. 使用pytest.mark.parametrize减少重复
   ```python
   @pytest.mark.parametrize("input,expected", [
       (1, 2),
       (2, 3)
   ])
   def test_add_one(input, expected):
       assert input + 1 == expected
   ```

4. 及时清理测试环境
   ```python
   # ✅ 使用yield fixtures进行清理
   @pytest.fixture
   def temp_file():
       f = open("temp.txt", "w")
       yield f
       os.remove("temp.txt")
   ```

5. 添加测试文档字符串
   ```python
   def test_something():
       """测试描述"""
       pass
   ```
