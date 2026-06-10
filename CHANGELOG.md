# 更新日志 (CHANGELOG)

## 版本 1.0.1 - 2026-06-11

### 🐛 Bug 修复

#### agent/network_config.py
1. **修复 validate_llm_instance 数值验证问题**
   - 问题：值为 0 的字段（如 timeout=0）无法通过验证
   - 原因：使用 `if instance.get('timeout')` 时，值为 0 会被视为 False
   - 修复：改为 `if 'timeout' in instance`，确保 0 值也能正确验证

2. **修复 import_config 缺少 change_log 问题**
   - 问题：使用 'overwrite' 策略导入配置时，缺少 'change_log' 键导致 KeyError
   - 修复：在覆盖后调用 `_ensure_config_structure()` 确保基本结构存在

3. **修复 update 方法缺少日志记录问题**
   - 问题：update 方法未记录变更日志
   - 修复：添加 `self._add_change_log()` 调用

4. **增强 _ensure_config_structure 方法**
   - 添加缺失字段的默认值：llm、external_services、search_api_keys
   - 确保导入配置时不会出现 KeyError

5. **修复删除默认 LLM 实例时的配置同步问题**
   - 问题：删除默认实例后，default_llm_instance 未同步更新
   - 修复：在删除时检查并清空 default_llm_instance

### ✨ 功能增强

#### agent/network_config.py - 日志增强
在以下核心函数中添加了详细的 logger.info 打印：

| 函数 | 添加的日志内容 |
|------|--------------|
| `get_llm_instances()` | 获取实例列表、实例数量 |
| `get_llm_instance()` | 获取单个实例、查找结果 |
| `add_llm_instance()` | 开始添加、初始化完成、名称重复警告、加密保存、添加成功 |
| `update_llm_instance()` | 开始更新、更新成功、未找到实例警告 |
| `delete_llm_instance()` | 开始删除、删除加密密钥、删除成功、未找到实例警告 |
| `set_default_llm_instance()` | 开始设置、实例存在检查、设置成功 |

#### agent/system_tools.py - 日志增强
在以下核心函数中添加了详细的 logger.info 打印：

| 函数 | 添加的日志内容 |
|------|--------------|
| `safe_resolve_path()` | 路径解析开始、成功、异常、完成 |
| `read_file()` | 文件读取开始、安全解析、文件大小、二进制检测、解码过程、完成 |
| `write_file()` | 文件写入开始、安全解析、内容大小、备份过程、目录创建、写入完成 |
| `list_directory()` | 目录列出开始、安全解析、遍历过程、最大条目限制、完成 |
| `search_files()` | 搜索开始、安全解析、目录遍历、模式匹配、结果数量、完成 |
| `_get_single_file_info()` | 文件信息获取开始、stat获取、属性检测、符号链接处理、完成 |

### 🧪 测试增强

#### tests/unit/test_network_config.py
新增 `TestLLMConfigIntegration` 测试类，包含以下测试用例：

1. `test_default_llm_instance_config_exists` - 测试默认配置中存在 default_llm_instance 字段
2. `test_set_default_llm_instance_updates_config` - 测试设置默认实例同时更新配置字段
3. `test_set_default_updates_is_default_flag` - 测试设置默认实例时更新 is_default 标记
4. `test_default_llm_instance_persists_on_reload` - 测试默认实例配置在重新加载后保持不变
5. `test_default_llm_instance_empty_when_no_instances` - 测试无实例时 default_llm_instance 为空
6. `test_ensure_config_structure_adds_default_field` - 测试配置结构确保函数添加缺失字段

### 📦 界面改进

#### templates/index.html
- 合并 LLM 服务配置和 LLM 多实例管理为统一的界面
- 新增默认实例下拉选择框
- 优化布局，提升用户体验

#### static/js/network-config.js
- 新增 `updateDefaultLlmInstanceSelect()` 函数：更新默认实例选择下拉框
- 修改 `renderLlmInstances()`：渲染实例列表时同步更新下拉框
- 修改 `setDefaultLlmInstance()`：设置默认实例后重新加载完整配置

### 📝 文档

#### docs/network_config_guide.md
- 完善网络配置操作指南
- 添加详细的配置示例

---

## 版本 1.0.0 - 初始版本

### 主要功能
- LLM 多实例管理
- MCP 服务配置管理
- 配置导入/导出
- 配置变更日志
- 敏感信息加密存储
