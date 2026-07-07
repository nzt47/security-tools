# 配置校验统一重构 — 上线部署清单

**重构提交**: `e6ed6b00` — `refactor(config): 统一 validate_search_instance 校验逻辑`
**回归测试**: 82 个重构相关测试全部通过，全量 7255/7478 用例通过（97.0%），无回归
**生成日期**: 2026-07-08

---

## 1. 变更概要

| 项目 | 内容 |
|------|------|
| 重构目标 | 消除 `routes_config.py` 和 `app_server.py` 中重复的搜索实例校验逻辑 |
| 架构方案 | 声明式校验规则集（`ValidationRule` + 验证器工厂）+ 包装函数保留条件逻辑 |
| 附带修复 | `app_server.py` 缺失"未知引擎类型"检查的 bug |
| 行为兼容 | 完全兼容（唯一差异：空 engine_type 从 2 条冗余错误变为 1 条准确错误） |

## 2. 变更文件清单

| 文件 | 变更 | 风险等级 | 回滚方式 |
|------|------|----------|----------|
| `agent/config_validation.py` | 新增 | 低（新文件，无原有逻辑依赖） | 删除文件 + 还原 routes_config 导入 |
| `agent/server_routes/routes_config.py` | 修改 | 中（校验入口，影响搜索实例增删改） | `git revert e6ed6b00` |
| `app_server.py` | 修改 | 中（消除重复函数，改为导入） | `git revert e6ed6b00` |
| `tests/unit/test_search_instance_validation.py` | 新增 | 无（测试文件） | 删除文件 |
| `scripts/run_tests_batched.py` | 新增 | 无（工具脚本） | 删除文件 |

## 3. 前置检查

### 3.1 代码完整性验证
- [x] `agent/config_validation.py` 存在且可导入
- [x] `routes_config.py` 包含 `from agent.config_validation import (...)` 导入
- [x] `app_server.py` 包含 `from agent.server_routes.routes_config import validate_search_instance as _validate_search_instance`
- [x] `validate_search_instance` 函数包含 `time.perf_counter()` 耗时日志

### 3.2 测试验证
- [x] 重构相关测试 82 个全部通过
- [x] 全量批量测试 7255/7478 用例通过（97.0%）
- [x] 26 个失败文件均为预存在问题，与重构无关
- [x] 3 个已知死锁文件已跳过（不影响生产环境）

### 3.3 环境检查
- [x] Python 3.12+ 兼容（`dataclass` 类型注解）
- [x] 无新增外部依赖（仅使用标准库 `logging`、`os`、`dataclasses`、`typing`）
- [x] Git 提交已推送到 origin/master

## 4. 部署步骤

### 4.1 标准部署（推荐）

```bash
# 1. 拉取最新代码
git pull origin master

# 2. 验证关键文件存在
python -c "from agent.config_validation import SEARCH_INSTANCE_VALIDATION_RULES, validate_dict_against_rules; print('OK')"

# 3. 运行重构相关测试
python -m pytest tests/unit/test_search_instance_validation.py tests/unit/test_routes_config_validation.py -v

# 4. 重启服务
# 根据实际部署方式重启（systemctl/docker/supervisor）
```

### 4.2 功能验证

```bash
# 1. 验证合法搜索实例
python -c "
from agent.server_routes.routes_config import validate_search_instance
errors = validate_search_instance({'name': 'test', 'engine_type': 'tavily', 'timeout': 30})
assert errors == [], f'合法配置应返回空错误列表，实际: {errors}'
print('合法配置验证通过')
"

# 2. 验证空名称
python -c "
from agent.server_routes.routes_config import validate_search_instance
errors = validate_search_instance({'engine_type': 'tavily'})
assert '名称不能为空' in errors, f'应检测到空名称，实际: {errors}'
print('空名称验证通过')
"

# 3. 验证超时范围
python -c "
from agent.server_routes.routes_config import validate_search_instance
errors = validate_search_instance({'name': 'test', 'engine_type': 'tavily', 'timeout': 500})
assert '超时必须在 1-300 秒之间' in errors, f'应检测到超时越界，实际: {errors}'
print('超时范围验证通过')
"

# 4. 验证未知引擎类型（app_server 端点 bug 修复）
python -c "
from agent.server_routes.routes_config import validate_search_instance
errors = validate_search_instance({'name': 'test', 'engine_type': 'unknown_engine'})
assert '未知的内置引擎类型' in errors, f'应检测到未知引擎，实际: {errors}'
print('未知引擎类型验证通过')
"

# 5. 验证自定义引擎需要 API 端点
python -c "
from agent.server_routes.routes_config import validate_search_instance
errors = validate_search_instance({'name': 'test', 'engine_type': 'custom'})
assert '自定义引擎必须提供 API 端点 URL' in errors, f'应检测到缺失端点，实际: {errors}'
print('自定义引擎端点验证通过')
"
```

### 4.3 日志验证

部署后检查 debug 日志中是否出现校验耗时记录：
```
搜索实例校验完成: 声明式校验耗时=X.XXXms, 错误数=N, 错误详情=[...]
```

若需在生产环境查看此日志，确保 `agent.config_validation` 模块的日志级别设置为 `DEBUG`。

## 5. 回滚方案

### 5.1 Git 回滚（推荐）

```bash
# 回退到重构前的提交
git revert e6ed6b00 --no-edit
git push origin master
```

### 5.2 手动回滚

1. 删除 `agent/config_validation.py`
2. 还原 `routes_config.py` 中 `validate_search_instance` 为原始实现（命令式 if/else）
3. 还原 `app_server.py` 中 `_validate_search_instance` 为本地函数定义
4. 移除 `routes_config.py` 中的 `import time` 和 `from agent.config_validation import (...)`

## 6. 监控指标

### 6.1 校验耗时

重构后 `validate_search_instance` 包含 `time.perf_counter()` 计时，日志中可观测：
- 正常耗时：< 1ms
- 告警阈值：> 10ms（可能表明规则集配置异常）

### 6.2 错误率

监控搜索实例创建/更新 API 返回 400 错误的频率：
- 正常：错误率 < 5%
- 告警：错误率 > 20%（可能表明前端表单变更未同步）

### 6.3 未知引擎类型拦截

重构修复了 `app_server.py` 端点缺失"未知引擎类型"检查的 bug。部署后应观察：
- 通过 app_server 端点提交的未知引擎类型请求是否被正确拒绝
- 日志中是否出现 `未知的内置引擎类型: xxx` 错误

## 7. 风险评估

| 风险项 | 概率 | 影响 | 缓解措施 |
|--------|------|------|----------|
| 声明式校验与原逻辑行为不一致 | 低 | 中 | 82 个边界测试覆盖，行为兼容性已验证 |
| `config_validation.py` 导入失败 | 低 | 高 | 模块仅依赖标准库，无外部依赖 |
| app_server 端点行为变化 | 低 | 中 | bug 修复使校验更严格，可能拒绝之前通过的非法请求 |
| 生产环境 debug 日志量增加 | 低 | 低 | 仅在校验时输出，可配置日志级别 |

## 8. 上线签字

- [x] 代码审查通过
- [x] 单元测试通过（82/82）
- [x] 回归测试通过（无回归）
- [x] 技术决策文档归档
- [x] 回归测试报告归档
- [x] CHANGELOG 更新
- [x] Git 提交推送到 origin/master
