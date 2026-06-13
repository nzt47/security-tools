# SafeFileReader 异常事件告警规则

> **版本**: 1.0  
> **日期**: 2026-06-09  
> **适用模块**: `utils/file_reader.py` (SafeFileReader)

---

## 一、告警规则总览

### 1.1 Prometheus 指标列表

| 指标名称 | 类型 | 标签 | 说明 |
|----------|------|------|------|
| `yunshu_safe_file_reader_errors_total` | Counter | `error_type`, `file_path` | 错误总数 |
| `yunshu_safe_file_reader_encoding_fallbacks_total` | Counter | `from_encoding`, `to_encoding`, `file_path` | 编码降级次数 |
| `yunshu_safe_file_reader_read_duration_seconds` | Histogram | `file_path` | 读取耗时分布 |
| `yunshu_safe_file_reader_loaded_history_count` | Gauge | `file_path` | 加载的历史对话数 |
| `yunshu_safe_file_reader_invalid_ratio` | Gauge | `file_path` | 无效行比例 (0-1) |

### 1.2 错误类型 (error_type)

| 错误类型 | 触发条件 | 告警级别 |
|----------|----------|----------|
| `file_not_found` | 文件不存在 | Warning |
| `file_too_large` | 文件超过大小限制 | Warning |
| `encoding_failed` | 所有编码尝试均失败 | Critical |
| `json_parse_failed` | JSON 解析失败（单行） | 仅计数 |
| `history_load_failed` | 历史记忆加载失败 | Critical |

---

## 二、告警规则详情

配置文件: `monitoring/alerts_safe_file_reader.yml`

### 2.1 文件读取失败类

| 告警名称 | 触发条件 | 级别 | 说明 |
|----------|----------|------|------|
| `SafeFileReaderFileNotFound` | 5m 内出现文件不存在错误 | Warning | 可能是首次运行或配置错误 |
| `SafeFileReaderFileTooLarge` | 5m 内出现文件过大被拒绝 | Warning | 可能配置不当或数据异常增长 |

### 2.2 编码异常类

| 告警名称 | 触发条件 | 级别 | 说明 |
|----------|----------|------|------|
| `SafeFileReaderEncodingFallback` | 5m 内出现编码降级 | Info | UTF-8 失败，已自动降级到备用编码 |
| `SafeFileReaderAllEncodingsFailed` | 5m 内所有编码均失败 | Critical | 文件完全无法读取，需立即处理 |

### 2.3 数据质量类

| 告警名称 | 触发条件 | 级别 | 说明 |
|----------|----------|------|------|
| `SafeFileReaderHighInvalidRatio` | 无效行比例 > 10%，持续 5m | Warning | 数据质量下降，可能有损坏行 |
| `SafeFileReaderConsecutiveParseFailures` | 5m 内 >10 行 JSON 解析失败 | Critical | 文件可能已损坏 |

### 2.4 服务启动加载类

| 告警名称 | 触发条件 | 级别 | 说明 |
|----------|----------|------|------|
| `SafeFileReaderHistoryLoadFailed` | 5m 内出现历史加载失败 | Critical | 服务启动时无法加载历史记忆 |
| `SafeFileReaderHistoryLoadEmpty` | 加载的历史计数 = 0，持续 2m | Info | 可能是首次运行或文件为空 |

### 2.5 性能类

| 告警名称 | 触发条件 | 级别 | 说明 |
|----------|----------|------|------|
| `SafeFileReaderSlowRead` | P95 读取时间 > 5s，持续 5m | Warning | 文件过大或 IO 性能问题 |

---

## 三、日志关键字监控

如果未部署 Prometheus/Grafana，可通过日志关键字进行监控：

### 3.1 需要告警的日志模式

| 日志模式 | 严重级别 | 建议动作 |
|----------|----------|----------|
| `\[历史加载\].*文件读取失败` | Critical | 检查文件是否存在、权限是否正确 |
| `\[历史加载\].*SafeFileReader 工具类不可用` | Critical | 检查 utils/file_reader.py 是否存在 |
| `\[历史加载\].*加载过程发生异常` | Critical | 查看异常堆栈，排查根因 |
| `\[历史加载\].*所有编码均失败` | Critical | 检查文件编码是否正确 |
| `\[历史加载\].*文件过大.*拒绝读取` | Warning | 检查文件大小限制配置 |
| `\[历史加载\].*文件中没有有效消息` | Warning | 检查文件内容是否为空或格式错误 |

### 3.2 正常的日志模式（无需告警）

| 日志模式 | 说明 |
|----------|------|
| `\[历史加载\].*文件不存在，跳过加载` | 首次启动正常现象 |
| `\[历史加载\].*使用 utf-8-sig 编码读取成功` | 编码自动降级成功 |
| `\[历史加载\].*文件读取完成 - 有效.*无效` | 正常加载完成 |
| `\[历史加载\].*配对完成.*最终加载历史对话` | 正常加载完成 |

---

## 四、部署方法

### 4.1 Prometheus 告警规则部署

```bash
# 将告警规则文件复制到 Prometheus 配置目录
cp monitoring/alerts_safe_file_reader.yml /etc/prometheus/rules/

# 在 prometheus.yml 中引用规则文件
# rule_files:
#   - "rules/*.yml"

# 重载 Prometheus 配置
curl -X POST http://localhost:9090/-/reload
```

### 4.2 Grafana 看板配置

建议在现有看板中添加以下面板：

1. **SafeFileReader 错误率** - 按 error_type 分组的错误计数
2. **文件读取耗时分布** - Histogram 热力图
3. **历史加载计数** - Gauge 实时值
4. **无效行比例** - 各文件的数据质量趋势

### 4.3 日志采集（ELK/ Loki）

```yaml
# Loki 日志处理规则
processors:
  - drop_event:
       when:
         not:
           regexp:
             message: "\\[历史加载\\]|SafeFileReader"
  - add_fields:
       target: ''
       fields:
         component: safe_file_reader
```

---

## 五、应急响应手册

### 5.1 Critical 级别告警处理

#### SafeFileReaderHistoryLoadFailed (历史加载失败)
1. 检查 `data/messages.jsonl` 文件是否存在
2. 检查文件编码是否正确: `file data/messages.jsonl`
3. 检查文件内容: `head -5 data/messages.jsonl`
4. 如果文件损坏，从备份恢复: `.\scripts\rollback.ps1 -Target data`

#### SafeFileReaderAllEncodingsFailed (编码完全失败)
1. 检查文件编码: `chardetect data/messages.jsonl`
2. 尝试手动转换: `iconv -f GBK -t UTF-8 input > output`
3. 如果无法恢复，从备份恢复

#### SafeFileReaderConsecutiveParseFailures (连续 JSON 失败)
1. 定位损坏行: `python -c "import json; [json.loads(l) for i,l in enumerate(open('data/messages.jsonl')) if l.strip()]"`
2. 删除或修复损坏行
3. 检查是否有进程在异常写入文件

### 5.2 Warning 级别告警处理

#### SafeFileReaderFileTooLarge (文件过大)
1. 检查文件大小: `ls -lh data/messages.jsonl`
2. 考虑归档旧消息
3. 如需调整限制，修改 `SafeFileReader` 的 `max_size_mb` 参数

#### SafeFileReaderHighInvalidRatio (无效行比例高)
1. 检查写入端是否有异常
2. 统计损坏行数量和内容
3. 修复写入逻辑或清理损坏数据

---

## 六、相关文档

- 工具类源码: `utils/file_reader.py`
- 告警规则: `monitoring/alerts_safe_file_reader.yml`
- 部署手册: `docs/deployment_guide_history_fix.md`
- 回滚脚本: `scripts/rollback.ps1`
- 修复报告: `docs/history_persistence_fix_report.md`
