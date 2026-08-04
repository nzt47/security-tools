# Git Hook 与 CI 升级总结（-BomDiag + JSON/ELK + 演示）

> **文档目的**：汇总本次 Git Hook 与 CI 升级的全部变更点，作为团队速查入口。
> **适用范围**：所有使用 `sync_precommit_hook.ps1` 部署 Hook、引用 `git_precommit_check.ps1` 的仓库。
> **文档版本**：v1.0 | **更新日期**：2026-08-04
> **关联主指南**：[Git Hook 与 BOM 排查避坑指南](git_hook_bom_guide.md)

---

## 一、变更点清单（按提交）

| 提交 | 文件 | 变更内容 |
|------|------|---------|
| `03b929e3` | `.github/workflows/ci.yml` | docs-precheck-tests job 预检 step 改用 `git_precommit_check.ps1 -BomDiag -JsonOutput`，**PR 阶段即触发**（`on: pull_request`） |
| `03b929e3` | `scripts/dev/git_precommit_check.ps1` | 新增 `-JsonOutput` switch + `Write-Log` 统一日志函数；`-BomDiag`/`-JsonOutput` 透传下游 `precheck_docs.ps1` |
| `03b929e3` | `scripts/dev/precheck_docs.ps1` | 新增 `-JsonOutput` + `Write-Log`（统一日志入口）；`-BomDiag` 字节级诊断：BOM 状态 / 锚点剥离 / 路径解析 |
| `03b929e3` | `docs/ci_guidelines/git_hook_bom_guide.md` | 团队避坑指南首次成文（编码契约 + 事故速查 + 工具链 + JSON 接入 + 透传链路 + 避坑清单） |
| `14337414` | `docs/ci_guidelines/git_hook_bom_guide.md` | 新增 4.4 节 ELK/Filebeat 解析 JSON 日志配置示例 + 3.2 节演示引用 |
| `14337414` | `docs/ci_guidelines/assets/bomdiag_pr_demo.gif` | PR 阶段 `-BomDiag` 拦截演示（真实输出，人类可读 + JSON/ELK 双视角） |
| 前置 | hook 模板 `hook_fail_safe.psm1` | `-BomDiag` 字节级调试开关 + 多仓库同步部署 |

### 1.1 核心行为变化

- **日志统一**：`git_precommit_check.ps1` 与 `precheck_docs.ps1` 共用 `Write-Log`，`-JsonOutput` 时输出单行 JSON。
- **PR 阶段拦截**：CI 与本地 hook 使用同一判定链，BOM 边缘问题在 PR 页面直接可见，不再依赖本地 hook 才暴露。
- **字节级证据**：`-BomDiag` 输出 BOM 头部 hex（如 `EF BB BF EF BB BF 23 20 E9`），定位从"猜"变成"看"。

### 1.2 关键修复（本轮踩坑）

| Bug | 根因 | 修复 |
|-----|------|------|
| `Write-Host -ForegroundColor $null` 报参数绑定错误 | INFO/DEBUG 级前景色为 `$null`，PS 5.1 拒绝 | 带色/裸写分支分离，条件传参 |
| `Write-Log` 空消息绑定错误 | `[Parameter(Mandatory)][string]$Message` 拒绝空串 | 移除 Mandatory；JSON 模式 `IsNullOrWhiteSpace` 跳过空行 |
| PS 5.1 `-File` 调用不绑定 `-Verbose` | `-Verbose` 是保留参数名 | 用自定义 `-BomDiag` switch 实现，CI 与本地同源 |

---

## 二、JSON 结构化日志与 ELK 配置细节

### 2.1 输出格式

每条日志**一行 JSON**（UTF-8 无 BOM，stdout）：

```json
{"ts":"2026-08-04T04:12:33.1234567Z","level":"ERROR","event":"broken_link","msg":"  [BROKEN] guide.md: ../x.md","data":{"file":"guide.md","link":"../x.md","host":"C:\\repo\\docs\\guide.md"}}
```

| 字段 | 说明 |
|------|------|
| `ts` | UTC ISO-8601（PowerShell `o` 格式） |
| `level` | `INFO` / `WARN` / `ERROR` / `OK` / `DEBUG` |
| `event` | 事件名：`broken_link` / `block` / `pass` / `summary` / `diag` / `bom` / `header` 等 |
| `msg` | 人类可读正文，**保留 `[BROKEN]`/`[BLOCK]`/`[OK]` 标记**（回归断言与 GitHub Actions 阅读不受影响） |
| `data` | 可选附加字段（BOM hex、文件路径、计数等） |

### 2.2 Filebeat 采集（filestream + ndjson）

```yaml
filebeat.inputs:
  - type: filestream
    id: ci-precheck-json
    enabled: true
    paths:
      - /var/log/ci/precheck/*.log
    parsers:
      - ndjson:
          target: ""
    json:
      keys_under_root: true
      add_error_key: true
      overwrite_keys: true

processors:
  - timestamp:
      field: ts
      layouts:
        - '2006-01-02T15:04:05.999999999Z07:00'
      timezone: UTC
  - drop_fields:
      fields: ["ecs", "agent", "log"]
      ignore_missing: true
```

### 2.3 Elasticsearch ingest pipeline（可选）

```json
{
  "description": "precheck_docs JSON 日志管道",
  "processors": [
    { "date": { "field": "ts", "target_field": "@timestamp",
                "formats": ["iso8601"], "timezone": "UTC" } },
    { "lowercase": { "field": "level", "target_field": "level" } },
    { "set": { "field": "event.kind", "value": "ci-precheck" } },
    { "set": { "field": "event.category", "value": "build" } }
  ]
}
```

### 2.4 字段映射与典型查询

| 字段 | 类型建议 | 用途 |
|------|---------|------|
| `ts` / `@timestamp` | `date` | 时间线分析（UTC） |
| `level` | `keyword` | 过滤 `ERROR` / `BLOCK` 事件 |
| `event` | `keyword` | 按事件聚合 |
| `msg` | `text` + `keyword` | 全文检索 + 关键词匹配 `[BLOCK]` |
| `data.*` | 动态映射 | BOM hex、路径、计数等载荷 |

```lucene
level: ERROR AND event: broken_link
event: block AND msg: "*BOM*"
data.bom_count: >= 2
```

### 2.5 过滤非 JSON 格式日志行

日志流可能混入非 JSON 行（如 pytest 原始输出）。`json.add_error_key: true`
会在解析失败时写入 `error.message` 字段，配合 `drop_event` 处理器按条件丢弃：

```yaml
processors:
  # 方式一：解析失败（含 error.message）的坏行直接丢弃
  - drop_event:
      when:
        has_fields: ['error.message']

  # 方式二：仅保留 precheck JSON 行（必含 ts/level/event），其余一律丢弃
  - drop_event:
      when:
        not:
          all:
            - has_fields: ['ts']
            - has_fields: ['level']
            - has_fields: ['event']
```

说明：

- **方式一** 依赖 `json.add_error_key: true`（4.4 节 Filebeat 配置已含）；非 JSON 行解析失败时 Filebeat 写入 `error.message`，条件命中即丢弃。
- **方式二** 与 `json.keys_under_root: true` 配合：JSON 行字段被提升到文档根，非 JSON 行缺失 `ts/level/event` 任一即被丢弃。
- 若仍需保留非 JSON 行做审计（默认行为：它们作为普通 message 保留），只配置方式一即可，**不要**同时配置方式二。

---

## 三、`-BomDiag` 调试用法

### 3.1 透传链路（CI 与本地同源）

```
CI:  .github/workflows/ci.yml docs-precheck-tests job
      └─ git_precommit_check.ps1 -BomDiag -JsonOutput
本地: TLM_HOOK_VERBOSE=1 → hook bash VERBOSE_ARG="-BomDiag"
      └─ git_precommit_check.ps1 -BomDiag
          └─ precheck_docs.ps1 -BomDiag [-JsonOutput]
```

### 3.2 开启方式

```powershell
# 本地手动开启字节级诊断
& powershell -ExecutionPolicy Bypass -File scripts/dev/git_precommit_check.ps1 `
  -TargetRepo . -BomDiag

# CI（docs-precheck-tests job 已内置）
& powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev/git_precommit_check.ps1 `
  -TargetRepo $env:GITHUB_WORKSPACE -BomDiag -JsonOutput
```

### 3.3 输出解读（真实捕获示例）

`-BomDiag` 开启后，失效链接会额外输出（对应 JSON 模式 `event=diag` 的 DEBUG 行）：

```
[BOM] 叠加 BOM: ...\docs\guide.md (BOM x2, head: EF BB BF EF BB BF 23 20 E9)
[BROKEN] guide.md: missing.md
  [DIAG] 链接原文: \[详细说明\] (./missing.md)
  [DIAG] 剥离锚点: 后缀= 文件部分=missing.md
  [DIAG] 解析路径: C:\...\docs\missing.md
  [DIAG] 存在性:   File=False Dir=False
  [DIAG] 宿主文件 BOM: Stacked (BOM x2, head: EF BB BF EF BB BF 23 20 E9)
[BLOCK] 阻塞模式：失效链接 1 > 阈值 0
[BLOCK] 提交被阻止
```

判定要点：

- **BOM 状态**：`None`（无 BOM）/ `Single`（恰好 1 个，契约态）/ `Stacked`（叠加 ≥2，破坏态）。
- **head hex**：看前 8 字节，`EF BB BF` 连续出现次数即叠加数。
- **exit code**：任何 BLOCK → exit 1 → PR 合并被阻止 / 本地提交被阻止。

### 3.4 与 JSON 模式配合

- DEBUG 级（`event=diag`）**仅 `-BomDiag` 时输出**，默认场景日志量可控。
- JSON 模式空行剔除、DEBUG 默认不输出；`data` 字段携带 `head_hex` / `bom_count` / `state` 供 ELK 聚合。

---

## 四、验证与回归

| 检查项 | 结果 |
|--------|------|
| 完整预检（`git_precommit_check.ps1 -TargetRepo .`） | ✅ exit 0，锚点回归 4/4 |
| 链接预检 | ✅ 684 文件 / 593 链接 / 0 失效 |
| `verify_core_invariants.py` | ✅ 12/12 项通过 |
| 演示 GIF 真实输出 | ✅ 人类可读 + JSON 双视角均 exit 1 拦截 |
| `check_ps1_encoding.py` | ✅ BLOCK/WARN 分级生效 |

---

## 五、相关文档索引

- [Git Hook 与 BOM 排查避坑指南](git_hook_bom_guide.md) — 主指南（编码契约 / 事故速查 / ELK / 透传链路 / 避坑清单）
- [BOM 事故复盘](precommit_hook_bom_incident_report.md) — 事故时间线与根因
- [Hook 复用指南](precommit_hook_reuse_guide.md) — 多仓库部署 / 同步 / 回滚
- [演示 GIF](assets/bomdiag_pr_demo.gif) — PR 阶段 `-BomDiag` 拦截过程
- 编码 BOM 排查清单：`scripts/check_ps1_encoding.py`（自动化清单脚本，详见主指南 3.3 节）
