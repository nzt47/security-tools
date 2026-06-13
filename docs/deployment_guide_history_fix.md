# 云枢历史记忆持久化修复 - 部署操作手册

> **版本**: 1.0  
> **日期**: 2026-06-09  
> **修复内容**: 历史对话重启后丢失问题

---

## 一、修复内容概览

| 文件 | 修改内容 | 影响范围 |
|------|----------|----------|
| `app_server.py` | 新增 `_load_chat_history_from_file()` 函数 | 服务器启动流程 |
| `utils/file_reader.py` | 新增 SafeFileReader 通用容错工具类 | 通用（可供其他模块调用） |

---

## 二、部署步骤

### 2.1 前置检查
```powershell
# 1. 确认服务器运行状态
curl http://localhost:5678/api/health

# 2. 确认记忆文件存在
Test-Path c:\Users\Administrator\agent\data\messages.jsonl

# 3. 确认当前历史数
curl http://localhost:5678/api/history
```

### 2.2 备份（部署前必做）
```powershell
# 备份 app_server.py
Copy-Item c:\Users\Administrator\agent\app_server.py -Destination "c:\Users\Administrator\agent\app_server.py.bak_$(Get-Date -Format 'yyyyMMdd')" -Force

# 备份记忆文件
Copy-Item c:\Users\Administrator\agent\data\messages.jsonl -Destination "c:\Users\Administrator\agent\data\messages.jsonl.bak_$(Get-Date -Format 'yyyyMMdd')" -Force
```

### 2.3 重启服务
```powershell
# 停止当前服务器（终端7）
# 按 Ctrl+C 或在 IDE 中停止运行进程

# 重新启动
cd C:\Users\Administrator\agent
$env:YUNSHU_FEATURE_SANDBOX='false'
python app_server.py
```

### 2.4 验证部署
```powershell
# 等待服务器启动完成，在日志中查找以下内容：
# 📂 [历史加载] 开始从文件加载历史对话
# ✅ [历史加载] 最终加载历史对话: N 条

# 验证历史 API
curl http://localhost:5678/api/history

# 验证新对话保存
curl -X POST http://localhost:5678/api/chat -H "Content-Type: application/json" -d '{"message":"测试部署验证"}'
```

---

## 三、启动日志检查清单

部署后启动日志应包含：

```
======================================================================
📂 [历史加载] 开始从文件加载历史对话
📂 [历史加载] 文件路径: .\data\messages.jsonl
📊 [历史加载] 文件大小: X.XX KB
✅ [历史加载] 文件读取完成 - 有效: N 条，无效: 0 条
✅ [历史加载] 配对完成 - 成功: N 对，跳过: M 条
✅ [历史加载] 最终加载历史对话: N 条
======================================================================
```

**异常日志及处理**：

| 日志内容 | 原因 | 处理方式 |
|----------|------|----------|
| `⚠️ 文件不存在，跳过加载` | 首次运行或文件被删除 | 正常，首次运行无历史 |
| `⚠️ 第 N 行 JSON 解析失败` | 文件中有损坏行 | 自动跳过，无需处理 |
| `❌ 文件编码错误` → `🔄 尝试 utf-8-sig` | 编码非 utf-8 | 自动降级，无需处理 |
| `❌ 备用编码也失败` | 编码完全不可读 | 检查文件来源，重新生成 |
| `⚠️ 配对完成 - 跳过: N 条` | 消息不成对 | 正常，孤立消息被跳过 |

---

## 四、回滚方案

如部署后出现问题：

```powershell
# 1. 停止服务器

# 2. 恢复备份
Copy-Item "c:\Users\Administrator\agent\app_server.py.bak_YYYYMMDD" -Destination "c:\Users\Administrator\agent\app_server.py" -Force

# 3. 恢复记忆文件（如需要）
Copy-Item "c:\Users\Administrator\agent\data\messages.jsonl.bak_YYYYMMDD" -Destination "c:\Users\Administrator\agent\data\messages.jsonl" -Force

# 4. 重新启动服务器
python app_server.py
```

---

## 五、注意事项

1. **记忆文件路径**: 始终使用 `./data/messages.jsonl`，修改 `config.py` 的 `memory.data_dir` 需同步修改加载逻辑中的路径
2. **文件大小**: 当前无自动清理机制，文件会持续增长。建议定期归档旧消息
3. **编码要求**: 记忆文件必须为 UTF-8 或 UTF-8-sig 编码，不支持 GBK
4. **并发安全**: 启动加载期间如有新消息写入，不会冲突（文件追加模式）
5. **新增工具类**: `utils/file_reader.py` 为通用工具，可被其他模块调用，不影响现有功能

---

## 六、通用工具类使用示例

```python
from utils.file_reader import SafeFileReader

# 读取 JSON Lines（配置文件、日志等）
reader = SafeFileReader("data/config.jsonl", max_size_mb=5, log_prefix="配置加载")
result = reader.read_json_lines(required_fields=["key", "value"])

if result.success:
    for item in result.valid_lines:
        process_config(item)
else:
    logger.warning("配置加载失败: %s", result.error)

# 读取纯文本
reader = SafeFileReader("data/notes.txt", log_prefix="笔记读取")
result = reader.read_text_lines()
```

---

## 七、联系方式

- **问题反馈**: 查看启动日志中的 `[历史加载]` 前缀日志
- **测试脚本**: `tests/unit/test_history_load_edge_cases.py`
- **测试报告**: `docs/history_persistence_fix_report.md`
