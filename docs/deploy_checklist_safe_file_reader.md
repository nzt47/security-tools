# SafeFileReader 历史记忆容错 - 生产环境部署检查清单

**版本**: v1.0  
**日期**: 2026-06-10  
**状态**: ⬜ 待检查

---

## 一、代码变更检查

### 1.1 核心修复
| 检查项 | 状态 | 文件路径 | 责任人 |
|--------|------|----------|--------|
| 历史加载逻辑集成 SafeFileReader | ⬜ | [app_server.py](file:///c:/Users/Administrator/agent/app_server.py#L264-L355) | |
| 移除 emoji 字符避免编码问题 | ⬜ | [app_server.py](file:///c:/Users/Administrator/agent/app_server.py#L128) | |
| Prometheus 指标注册修复（registry → DEFAULT_REGISTRY） | ⬜ | [app_server.py](file:///c:/Users/Administrator/agent/app_server.py#L100) | |

### 1.2 SafeFileReader 工具类
| 检查项 | 状态 | 文件路径 | 说明 |
|--------|------|----------|------|
| 文件大小限制（10MB） | ⬜ | [utils/file_reader.py](file:///c:/Users/Administrator/agent/utils/file_reader.py) | 防 DoS |
| 编码降级链（utf-8 → utf-8-sig → gbk） | ⬜ | [utils/file_reader.py](file:///c:/Users/Administrator/agent/utils/file_reader.py#L100-L120) | 兼容多种编码 |
| 逐行容错解析 | ⬜ | [utils/file_reader.py](file:///c:/Users/Administrator/agent/utils/file_reader.py#L120-L150) | 单行失败不影响整体 |
| JSON 字段验证（role, content） | ⬜ | [utils/file_reader.py](file:///c:/Users/Administrator/agent/utils/file_reader.py#L130-L145) | 数据质量检查 |
| Prometheus 指标上报 | ⬜ | [utils/file_reader.py](file:///c:/Users/Administrator/agent/utils/file_reader.py#L160-L180) | 错误计数、耗时、比例 |

---

## 二、告警规则检查

### 2.1 主配置文件
| 告警规则 | 严重级别 | 触发条件 | 状态 |
|----------|----------|----------|------|
| SafeFileReaderFileNotFound | warning | 文件不存在 | ⬜ |
| SafeFileReaderFileTooLarge | warning | 文件大小超过限制 | ⬜ |
| SafeFileReaderEncodingFallback | info | UTF-8 降级到其他编码 | ⬜ |
| SafeFileReaderAllEncodingsFailed | critical | 所有编码均失败 | ⬜ |
| SafeFileReaderHighInvalidRatio | warning | 无效行比例 > 10% | ⬜ |
| SafeFileReaderConsecutiveParseFailures | critical | JSON 解析失败 > 10 条（5m） | ⬜ |
| SafeFileReaderHistoryLoadFailed | critical | 历史加载失败 | ⬜ |
| SafeFileReaderHistoryLoadEmpty | info | 历史加载为空 | ⬜ |
| SafeFileReaderSlowRead | warning | 95 分位读取时间 > 5s | ⬜ |

### 2.2 配置验证
| 检查项 | 状态 | 说明 |
|--------|------|------|
| 告警规则已同步到主配置 | ⬜ | [monitoring/alerts.yml](file:///c:/Users/Administrator/agent/monitoring/alerts.yml) |
| Alertmanager 配置正确 | ⬜ | 确认告警接收渠道（邮件/钉钉/短信） |
| 告警静默规则配置 | ⬜ | 首次运行空文件场景设置静默 |

---

## 三、回滚脚本检查

### 3.1 Shell 版本（Linux/macOS/WSL）
| 检查项 | 状态 | 文件路径 |
|--------|------|----------|
| 脚本存在且可执行 | ⬜ | [scripts/rollback.sh](file:///c:/Users/Administrator/agent/scripts/rollback.sh) |
| 支持 -t monitoring 参数 | ⬜ | 可回滚告警规则、SafeFileReader、Prometheus 配置 |
| 支持 -t code 参数 | ⬜ | 可回滚应用服务器代码 |
| 支持 -t data 参数 | ⬜ | 可回滚历史记忆数据 |
| 回滚前自动备份当前版本 | ⬜ | 创建 .pre_rollback_* 文件 |
| 服务状态验证 | ⬜ | 重启后检查 /api/health |

### 3.2 PowerShell 版本（Windows）
| 检查项 | 状态 | 文件路径 |
|--------|------|----------|
| 脚本存在 | ⬜ | [scripts/rollback.ps1](file:///c:/Users/Administrator/agent/scripts/rollback.ps1) |
| 支持 -Target monitoring 参数 | ⬜ | 同步功能 |
| 脚本签名（可选） | ⬜ | 生产环境建议签名 |

---

## 四、监控指标检查

### 4.1 Prometheus 指标
| 指标名称 | 类型 | 用途 | 状态 |
|----------|------|------|------|
| yunshu_safe_file_reader_errors_total | Counter | 错误计数（json_parse_failed, file_not_found 等） | ⬜ |
| yunshu_safe_file_reader_encoding_fallbacks_total | Counter | 编码降级次数 | ⬜ |
| yunshu_safe_file_reader_read_duration_seconds | Histogram | 读取耗时分布 | ⬜ |
| yunshu_safe_file_reader_loaded_history_count | Gauge | 加载的历史对话数 | ⬜ |
| yunshu_safe_file_reader_invalid_ratio | Gauge | 无效行比例 | ⬜ |

### 4.2 Grafana Dashboard（建议配置）
| Panel | 用途 | 状态 |
|-------|------|------|
| 历史加载成功率 | 监控文件读取健康度 | ⬜ |
| JSON 解析失败率 | 检测数据损坏 | ⬜ |
| 加载耗时趋势 | 发现性能问题 | ⬜ |
| 无效行比例告警 | 数据质量监控 | ⬜ |

---

## 五、数据备份检查

### 5.1 备份文件
| 文件 | 备份规则 | 状态 |
|------|----------|------|
| app_server.py.bak_* | 部署前自动备份 | ⬜ |
| data/messages.jsonl.bak_* | 每日自动备份 | ⬜ |
| utils/file_reader.py.bak_* | 工具类变更备份 | ⬜ |
| monitoring/alerts.yml.bak_* | 告警规则变更备份 | ⬜ |

### 5.2 备份验证
| 检查项 | 状态 | 说明 |
|--------|------|------|
| 备份文件存在 | ⬜ | 检查至少有一个有效备份 |
| 备份文件可读 | ⬜ | 验证备份文件完整性 |
| 备份时间合理 | ⬜ | 最近 24 小时内有备份 |

---

## 六、部署前验证

### 6.1 功能验证
| 测试项 | 步骤 | 预期结果 | 状态 |
|--------|------|----------|------|
| 正常历史加载 | 重启服务 | 历史记录正确显示 | ⬜ |
| 文件损坏容错 | 注入损坏行后重启 | 服务正常运行，损坏行跳过 | ⬜ |
| 空文件容错 | 使用空文件启动 | 服务正常，无历史 | ⬜ |
| 编码容错 | 使用 GBK 编码文件 | 自动降级解析成功 | ⬜ |

### 6.2 告警验证
| 测试项 | 步骤 | 预期结果 | 状态 |
|--------|------|----------|------|
| 连续解析失败告警 | 注入 15 条损坏行 | json_parse_failed > 10，触发告警 | ⬜ |
| 无效比例告警 | 无效行 > 10% | invalid_ratio > 0.1，触发告警 | ⬜ |
| 告警恢复 | 恢复正常文件重启 | 告警自动清除 | ⬜ |

---

## 七、回滚演练

| 步骤 | 操作 | 验证点 | 状态 |
|------|------|--------|------|
| 1 | 执行 `bash scripts/rollback.sh -t all` | 回滚脚本正常执行 | ⬜ |
| 2 | 验证服务重启 | /api/health 返回 200 | ⬜ |
| 3 | 验证历史记录 | 历史数据完整保留 | ⬜ |
| 4 | 验证告警规则 | 恢复到原告警配置 | ⬜ |

---

## 八、应急响应

### 8.1 故障处理流程
| 场景 | 告警规则 | 响应步骤 | SLA |
|------|----------|----------|-----|
| 文件损坏 | SafeFileReaderConsecutiveParseFailures | 检查 messages.jsonl，恢复备份 | 15分钟 |
| 编码失败 | SafeFileReaderAllEncodingsFailed | 检查文件编码格式 | 30分钟 |
| 历史丢失 | SafeFileReaderHistoryLoadFailed | 从备份恢复 | 10分钟 |
| 读取缓慢 | SafeFileReaderSlowRead | 检查文件大小，清理历史 | 30分钟 |

### 8.2 联系人
| 角色 | 姓名 | 联系方式 |
|------|------|----------|
| 值班工程师 | | |
| 开发负责人 | | |
| 运维负责人 | | |

---

## 九、签名确认

| 角色 | 签名 | 日期 |
|------|------|------|
| 开发负责人 | | |
| 测试负责人 | | |
| 运维负责人 | | |
| 安全负责人 | | |

---

*文档版本: v1.0 | 生成时间: 2026-06-10*
