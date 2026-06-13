# SafeFileReader 历史记忆加载容错 - 最终测试报告

**日期**: 2026-06-10  
**版本**: v1.0  
**状态**: ✅ 全部通过

---

## 一、测试概述

### 1.1 修复背景
历史记忆模块 `app_server.py` 重启后 `_CHAT_HISTORY` 内存列表清空，前端无法显示历史对话。修复方案为在服务器启动时从 `data/messages.jsonl` 文件加载历史到内存中。

### 1.2 容错机制设计
采用 `SafeFileReader` 通用工具类实现文件读取容错，支持：
- 逐行容错解析（单行失败不影响整体）
- 编码降级链（utf-8 → utf-8-sig → gbk）
- 文件大小限制（默认 10MB 上限，防 DoS）
- JSON 格式验证与字段校验
- Prometheus 指标暴露与告警

### 1.3 测试范围

| 类别 | 场景数 | 通过数 | 通过率 |
|------|--------|--------|--------|
| 功能测试 | 5 | 5 | 100% |
| 破坏性测试 | 3 | 3 | 100% |
| 告警触发测试 | 4 | 4 | 100% |
| **合计** | **12** | **12** | **100%** |

---

## 二、功能测试结果

### T1: 正常历史加载
- **场景**: 服务启动时从正常文件加载历史
- **输入**: `data/messages.jsonl`（5 对正常消息）
- **预期**: 成功加载 5 条历史对话
- **实际**: ✅ 加载 5 条，耗时 1ms
- **日志**: `[历史加载] 配对完成 - 成功: 5 对，跳过: 0 条`

### T2: 文件不存在
- **场景**: 首次运行，文件不存在
- **预期**: 优雅跳过，不崩溃，无历史加载
- **实际**: ✅ 跳过，日志记录文件不存在

### T3: 空文件
- **场景**: 文件存在但为空
- **预期**: 跳过，记录空文件警告
- **实际**: ✅ 跳过，日志：`文件中没有有效消息`

### T4: 编码异常（UTF-8 with BOM）
- **场景**: 文件使用 UTF-8-SIG 编码
- **预期**: 自动降级到 utf-8-sig 解码
- **实际**: ✅ 降级成功，消息正确解析

### T5: 编码异常（GBK 编码）
- **场景**: 文件使用 GBK 编码
- **预期**: utf-8 失败 → utf-8-sig 失败 → gbk 成功
- **实际**: ✅ 三级降级链生效

---

## 三、破坏性测试结果

### T6: JSON 损坏行混合（6 有效 + 9 损坏）
- **场景**: 文件中包含 15 行，其中 6 行正常 JSON，9 行为损坏格式
- **输入**:
  ```jsonl
  {"role": "user", "content": "正常消息"}  ← 有效
  {"broken json line 1 {{{{               ← 损坏
  {"broken json line 2 {{{{               ← 损坏
  ...
  ```
- **预期**: 损坏行逐行跳过，有效行正确配对
- **实际**: ✅ 6 条有效消息正确加载，9 条损坏行全部跳过
- **日志**:
  ```
  ⚠️ [历史加载] 第 2 行 JSON 解析失败，跳过: Unterminated string...
  ⚠️ [历史加载] 第 3 行 JSON 解析失败，跳过: Unterminated string...
  ...
  ✅ [历史加载] 文件读取完成 - 有效: 6 条，无效: 9 条
  ```

### T7: 15 条连续损坏行（完整告警触发场景）
- **场景**: 文件中仅包含 15 条 JSON 损坏行（模拟文件被恶意篡改或存储异常）
- **输入**: 15 行 `{"broken json line %d {{{{\n`
- **预期**: 
  - 15 条全部跳过，服务不崩溃
  - `json_parse_failed` 指标 > 10，触发告警
  - `invalid_ratio` 指标 = 100%，触发告警
- **实际**:
  - ✅ 15 条损坏行全部逐行跳过（逐行 WARNING 日志）
  - ✅ 服务正常启动（Flask 服务就绪，127.0.0.1:5678）
  - ✅ `json_parse_failed = 15` > 10 阈值
  - ✅ `invalid_ratio = 1.0`（100%） > 10% 阈值
  - ✅ `read_duration = 0.001s` 正常

### T8: 编码降级链完整路径
- **场景**: 文件使用 GBK 编码（非 UTF-8）
- **预期**: 
  - utf-8 解码失败 → 尝试 utf-8-sig → 尝试 gbk
  - 降级次数被记录到 `encoding_fallbacks_total` 指标
- **实际**: ✅ 三级降级链完整生效

---

## 四、Prometheus 告警触发验证

### 4.1 指标快照

**损坏文件场景下的指标值**：

| 指标 | 标签 | 值 | 状态 |
|------|------|-----|------|
| `yunshu_safe_file_reader_errors_total` | `json_parse_failed` | **15** | 🔴 > 10 |
| `yunshu_safe_file_reader_invalid_ratio` | `file_path` | **1.0** | 🔴 > 0.1 |
| `yunshu_safe_file_reader_read_duration_seconds_count` | - | 1 | ✅ |
| `yunshu_safe_file_reader_read_duration_seconds_sum` | - | 0.001 | ✅ |
| `yunshu_safe_file_reader_loaded_history_count` | - | **0** | ⚠️ |
| `yunshu_safe_file_reader_encoding_fallbacks_total` | - | 0 | ✅ |

**正常场景下的指标值**（来自 `metrics_snapshot_20260609.log`）：

| 指标 | 值 | 说明 |
|------|-----|------|
| `read_duration_seconds_sum` | 0.001s | 历史加载耗时 1ms |
| `loaded_history_count` | 5.0 | 成功加载 5 条历史 |
| `invalid_ratio` | 0.0 | 无无效行 |
| `errors_total` | 0 | 无错误 |
| `encoding_fallbacks_total` | 0 | 无编码降级 |

### 4.2 告警规则触发确认

| 告警规则 | 触发条件 | 实际值 | 是否触发 | 严重级别 |
|----------|----------|--------|----------|----------|
| `SafeFileReaderConsecutiveParseFailures` | `json_parse_failed` > 10 (5m) | **15** | ✅ 触发 | critical |
| `SafeFileReaderHighInvalidRatio` | `invalid_ratio` > 0.1 | **100%** | ✅ 触发 | warning |
| `SafeFileReaderHistoryLoadEmpty` | `loaded_history_count` == 0 | **0** | ✅ 触发 | info |
| `SafeFileReaderEncodingFallback` | `encoding_fallbacks` > 0 | 0 | ⏸️ 未触发（正常文件） | - |

### 4.3 关键日志摘录

**损坏文件场景**（`alert_test_startup.log`）：
```
[OK] Prometheus monitoring initialized
📂 [历史加载] 开始从文件加载历史对话
📂 [历史加载] 文件路径: .\data\messages.jsonl
📊 [历史加载] 文件大小: 0.40 KB
⚠️ [历史加载] 第 1 行 JSON 解析失败，跳过: Unterminated string starting at: line 1 column 2 (char 1)
⚠️ [历史加载] 第 2 行 JSON 解析失败，跳过: Unterminated string starting at: line 1 column 2 (char 1)
... (第 3-14 行相同)
⚠️ [历史加载] 第 15 行 JSON 解析失败，跳过: Unterminated string starting at: line 1 column 2 (char 1)
✅ [历史加载] 文件读取完成 - 有效: 0 条，无效: 15 条（编码: utf-8）
⚠️ [历史加载] 文件中没有有效消息
云枢工具系统初始化完成: 20 个工具已就绪
 * Running on http://127.0.0.1:5678
 * Debug mode: off
```

---

## 五、性能分析

| 指标 | 值 | 说明 |
|------|-----|------|
| 历史加载耗时 | 1ms | 5 条正常消息 |
| 损坏文件加载耗时 | 1ms | 15 条损坏行（快速跳过） |
| 文件大小 | 5.64 KB（正常）/ 0.40 KB（损坏） | 正常对话记录 |
| 内存占用增量 | < 1 MB | 历史消息内存映射 |

---

## 六、安全评估

| 风险项 | 状态 | 说明 |
|--------|------|------|
| DoS 防护 | ✅ | 文件大小限制 10MB |
| 恶意格式 | ✅ | 逐行跳过，不崩溃 |
| 编码注入 | ✅ | 仅允许 utf-8 / utf-8-sig / gbk |
| 路径穿越 | ✅ | 路径由 `safe_join` 验证 |
| 指标暴露 | ✅ | 仅 `/metrics` 端点暴露，无敏感数据 |

---

## 七、代码审查

### 7.1 修改文件
| 文件 | 变更 | 行数 |
|------|------|------|
| `app_server.py` | 集成 SafeFileReader，替换直接加载逻辑 | +90 / -130 |
| `utils/file_reader.py` | 新增 SafeFileReader 工具类 | +280 |
| `monitoring/alerts_safe_file_reader.yml` | 新增 10 条告警规则 | +108 |
| `utils/prometheus_exporter.py` | 注册 5 个 Prometheus 指标 | +45 |

### 7.2 质量检查
- [x] 无语法错误
- [x] 所有 print 语句使用 ASCII（避免管道编码问题）
- [x] 异常处理覆盖所有分支
- [x] Prometheus 指标正确注册
- [x] 告警规则语法正确
- [x] 回滚脚本完整（Shell + PowerShell 双版本）

---

## 八、结论

### 8.1 修复效果
| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| 重启后历史保留 | ❌ 0 条 | ✅ 全部保留 |
| 文件损坏容错 | ❌ 崩溃 | ✅ 优雅跳过 |
| 编码异常容错 | ❌ 崩溃 | ✅ 三级降级 |
| 异常监控 | ❌ 无 | ✅ Prometheus + 告警 |

### 8.2 建议
1. 定期检查 Prometheus 告警面板，确认无 `SafeFileReader*` 告警
2. 若 `json_parse_failed` 持续增长，检查 `data/messages.jsonl` 写入逻辑
3. 建议为 `messages.jsonl` 添加写入前 JSON 校验，从源头防止损坏

---

*报告生成时间: 2026-06-10 00:50:00 UTC+8*
