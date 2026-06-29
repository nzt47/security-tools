# kwarg-scanner Docker 镜像 — 测试报告

> **生成时间**: 2026-06-30
> **镜像版本**: 1.0.0
> **基础镜像**: python:3.12-slim
> **镜像大小**: 191 MB (CONTENT SIZE: 46.7 MB)

## 1. 测试摘要

| 维度 | 用例数 | 通过 | 失败 | 结果 |
|------|--------|------|------|------|
| 功能测试 | 4 | 4 | 0 | ✅ 通过 |
| 边界测试 | 3 | 3 | 0 | ✅ 通过 |
| 错误处理测试 | 2 | 2 | 0 | ✅ 通过 |
| 兼容性测试 | 1 | 1 | 0 | ✅ 通过 |
| **总计** | **10** | **10** | **0** | ✅ **全部通过** |

## 2. 功能测试

### 2.1 健康检查 (test_health_check)
- **命令**: `docker run --rm kwarg-scanner:latest --health`
- **预期**: 返回 JSON `{"status":"healthy","scanner":"available","version":"1.0.0"}`
- **实际**: ✅ 返回正确 JSON，exit 0
- **结构化日志**: stderr 输出 `{"trace_id":"...","action":"health_check","status":"healthy"}`

### 2.2 版本输出 (test_version)
- **命令**: `docker run --rm kwarg-scanner:latest --version`
- **预期**: 输出 `kwarg-scanner 1.0.0`
- **实际**: ✅ 输出正确，exit 0

### 2.3 扫描干净代码 (test_scan_clean_code)
- **命令**: `docker run --rm -v "$PROJECT:/project" kwarg-scanner --path /project/packages/kwarg_scanner/kwarg_scanner`
- **预期**: exit 0，0 处发现
- **实际**: ✅ exit 0，0 HIGH / 0 MEDIUM / 0 LOW，耗时 223ms

### 2.4 扫描高风险代码 (test_scan_with_high_risk)
- **命令**: 扫描含 `emit("x", trace_id="t", **payload)` 的代码
- **预期**: exit 1，发现 1 处 HIGH，给出修复建议
- **实际**: ✅ exit 1，1 HIGH，建议 `_RESERVED = {'trace_id'}; safe = {k: v for k, v in payload.items() if k not in _RESERVED}`

## 3. 边界测试

### 3.1 空目录扫描
- **命令**: 挂载空目录扫描
- **预期**: exit 0，0 处发现
- **实际**: ✅ exit 0

### 3.2 无效风险等级
- **命令**: `MIN_RISK=INVALID`
- **预期**: exit 2，参数错误
- **实际**: ✅ 返回错误码，输出 `E_INVALID_RISK_LEVEL`

### 3.3 未挂载 /project
- **预期**: 友好错误提示
- **实际**: ✅ 返回 `E_PROJECT_NOT_MOUNTED` 错误

## 4. 错误处理测试

### 4.1 结构化日志完整性
- **验证字段**: `trace_id`, `module_name`, `action`, `duration_ms`
- **实际**: ✅ 所有日志行均包含四个必填字段

### 4.2 退出码映射
- exit 0 → 扫描通过
- exit 1 → HIGH 风险阻断
- exit 2 → 参数错误
- exit 3 → 内部错误
- **实际**: ✅ 映射正确

## 5. 兼容性测试

### 5.1 JSON 输出到文件
- **命令**: `OUTPUT_FORMAT=json OUTPUT_FILE=/project/report.json`
- **预期**: 生成有效 JSON 文件
- **实际**: ✅ 生成 `{"scan_time":"...","total":0,"summary":{...},"findings":[]}`

## 6. 性能数据

| 场景 | 扫描路径 | 文件数 | 耗时 |
|------|----------|--------|------|
| 小目录 | packages/kwarg_scanner/kwarg_scanner | ~6 | 223ms |
| 大目录 | agent/ (整个项目) | ~100+ | 3852ms |

## 7. 可观测性验证

### 7.1 结构化日志（遵循硬约束）
所有日志输出到 stderr，格式为 JSON，包含必填字段:
```json
{
  "trace_id": "47ab3a43c4f56d55",
  "module_name": "kwarg_scanner_ci",
  "action": "scan_complete",
  "duration_ms": 223.78,
  "result": "success",
  "exit_code": "0",
  "total_duration_ms": "221.14",
  "high_risk_count": "0"
}
```

### 7.2 边界显性化
错误场景抛出带业务错误码的明确错误:
- `E_PROJECT_NOT_MOUNTED` — 未挂载代码目录
- `E_INVALID_RISK_LEVEL` — 无效风险等级
- `E_INVALID_FORMAT` — 无效输出格式
- `E_PATH_NOT_FOUND` — 路径不存在
- `E_MISSING_VALUE` — 参数缺少值
- `E_UNKNOWN_ARG` — 未知参数
- `E_INVALID_ARGS` — 参数错误
- `E_SCANNER_INTERNAL` — 内部错误

### 7.3 埋点预留
`trackEvent()` 函数已集成到关键节点:
- `scan_invoked` — 扫描启动
- `scan_success` — 扫描成功
- `scan_blocked` — 扫描阻断
- `scan_error` — 扫描错误

### 7.4 健康检查
`--health` 命令返回镜像状态:
```json
{"status":"healthy","scanner":"available","version":"1.0.0"}
```

## 8. CI 集成示例

### GitHub Actions
```yaml
- name: 关键字参数冲突扫描
  run: |
    docker build -t kwarg-scanner:local ./packages/kwarg_scanner
    docker run --rm -v "${{ github.workspace }}:/project" kwarg-scanner:local
```

### GitLab CI
```yaml
kwarg-scan:
  image: kwarg-scanner:latest
  script:
    - docker-entrypoint.sh
  variables:
    MIN_RISK: HIGH
    OUTPUT_FORMAT: json
```

## 9. 镜像详情

| 属性 | 值 |
|------|-----|
| 基础镜像 | python:3.12-slim |
| 镜像大小 | 191 MB |
| 用户 | scanner (非 root) |
| 工作目录 | /project |
| 入口点 | docker-entrypoint.sh |
| 健康检查 | 每 30s 执行 `--health` |
| OCI 标签 | 已设置 (title/description/version/source/licenses) |
