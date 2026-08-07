# v1.0.0 发布变更日志

> 生成时间：2026-08-07 | 发布点：`v1.0.0` tag = `004ce23e` | 远程 master = `5449eb68`

## 一、新增功能

### 知识层卡片引擎（agent/knowledge）
- **素材层 ingest 管道**（`1932869c`）：收集即入库，素材统一落库
- **卡片引擎核心**（`11028240`）：CardStore CRUD / 生命周期状态机（draft/current/archive/unknown）/ 双向链接解析 / 孤儿与断链检测 / index.md 全量与增量一致性
- **CLI 主入口** `python -m agent.knowledge`（`11028240`）：index-rebuild / card-list / card-transition / check-links / orphans 五子命令，退出码契约 0/1（后续 `24f8c4d4` 批量处理再增 import / export / list，现共 8 子命令）
- **CLI 批量处理**（`24f8c4d4`）：`import`（目录批量导入，同 slug 冲突跳过，`--force` 走 update）、`export`（frontmatter md 导出，round-trip 兼容 import）、`list`（分组 + 状态统计）
- **预提交自动化**：`knowledge-cli-verify` hook（32 项全生命周期断言，`--pre-commit --traceback` 静默模式）

### 发布流程（release）
- **发布自动化** `release-auto.yml`（guard → auto-release → alert-on-failure 三 job 架构）
- **发布前自动检查** `release-precheck.yml`（`18fbf93c`）+ 新手上手指南 quickstart
- **发布模拟镜像** docker/release-sim（`5432945f`）：git/python3/pwsh7.4/curl/gh 全依赖 + `--health` 自检，规避 WSL 模拟三坑
- **Shell 函数库** `release_shell_lib.sh`（`5432945f`）：curl_http_code / gh_api_len 等容错封装
- **首次发布引导** `release_first_release.ps1`（`5432945f`）：5 步引导 + 每步自动校验

## 二、修复点

### 发布流程可靠性（v1.0.5 ~ v1.0.8 系列验证修复）
- **curl 网络失败跳过重试**：网络层失败（超时/连接拒绝）在 `set -e` 下终止 step、跳过 while 重试循环 → 映射为 HTTP 500 进入重试 + 响应体读取容错
- **bash 退出码陷阱**：`if cmd; then ...; fi` 中 cmd 失败且无 `else` 时 `$?` 恒 0 → 退出码捕获必须放 `else` 分支或 `cmd && {...} || RC=$?`
- **guard 静默中断**（`21b3a071`）：alert-on-failure `needs` 加入 guard，guard 失败不再无告警静默中断发布
- **Gitee 同步**：503/超时/401/404/403 重试 3 次×10s，409/422 幂等冲突不重试；Gitee 无删 tag API，用 `git push gitee :refs/tags/<tag>`

### 代码正确性
- **routing_observability kwarg 冲突**（`7ff1a63f`）：`add_layer` 显式参数与 `**fields` 展开冲突（HIGH 风险扫描拦截）→ 展开前过滤保留键
- **gitleaks 配置误报**（`7ff1a63f`）：`gitleaks-config.toml` PEM 测试样例加入白名单
- **失效链接检查误报**（`01a57e3f`）：`Test-LinkBroken` 未剥离 `#锚点` 误判 + 类型 9 缺 return 穿透 → 修复后 docs 失效链接 5 → 0

### 数据安全
- **误删恢复**（远程 `28ad68fc`，本地对应 `dd87c306`）：无路径提交误带入 4 份 v1.0.0 归档报告删除 → 恢复提交重新纳入版本控制

## 三、测试与验证记录

| 项 | 结果 |
|---|---|
| 知识引擎单测（schema/lifecycle/card/links/cli） | 220+ 项通过 |
| 知识引擎覆盖率（核心 7 模块） | card/index/links/lifecycle/schema/logbook 100%，__main__ 92% |
| verify_knowledge_cli.py 断言 | 32 项全 PASS |
| pre-commit --all-files | 4/4 Passed（kwarg / tool-index-sync / 敏感信息 / knowledge-cli-verify） |
| regression（73 项） | 73 passed（BOM 污染 5 失败已修复） |
| 核心不变量校验（pre-push） | 12/12 PASS |
| release 模拟验证 | v1.0.5~v1.0.8（超时/503/403/401 重试链路 + 退出码传播） |

## 四、发布流程里程碑

1. release 工作流优化：18 项优化点落地（重试/超时/告警/守卫/退出码/日志）
2. v1.0.0 标签前移 7 次 + 双端同步（GitHub/Gitee）
3. PR #354 归档：final_status / final_confirmation / ingest 快照 / sync 归档 4 份报告
4. 文档三件套：checklist / template / retrospective + manual / summary

## 五、提交记录汇总（v1.0.0 发布主线）

```
28ad68fc fix(docs): 恢复 24f8c4d4 误删的 v1.0.0 归档报告          （远程）
5432945f feat(release): 发布模拟镜像 + Shell 函数库 + 首次发布引导
24f8c4d4 feat(knowledge): CLI 批量处理 import/export/list + 32 项断言
004ce23e docs(release): v1.0.0 最终状态确认与最终确认报告归档 (#354)  ← v1.0.0 tag
18fbf93c feat(release): 发布前自动检查 release-precheck.yml + quickstart
7ff1a63f fix(observability): kwarg 冲突与 gitleaks 配置误报
01a57e3f fix(docs): 失效链接检查误报 + ops 日志路径 + --traceback
11028240 feat(knowledge): 卡片引擎 CLI 主入口与预提交全生命周期校验
1932869c feat(knowledge): 素材层 ingest 管道
21b3a071 fix(release): alert-on-failure needs 加 guard
...
```
