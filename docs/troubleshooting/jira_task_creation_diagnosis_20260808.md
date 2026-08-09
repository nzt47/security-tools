# Jira 任务创建失败排查日志与错误原因分析

> 日期：2026-08-08 ｜ 脚本：[scripts/create_jira_tasks.py](file:///c:/Users/Administrator/agent/scripts/create_jira_tasks.py) ｜ 目标任务：KN-101 ~ KN-105

---

## 1. 执行记录

| 项 | 值 |
|---|---|
| 命令 | `python -B scripts/create_jira_tasks.py`（无 `--dry-run`） |
| 退出码 | **1** |
| 结果 | **未创建任何任务**（fail-fast 保护，无部分创建） |
| 环境检查 | `.env` 无 `JIRA_*` 配置；`$env:JIRA_BASE_URL` / `$env:JIRA_USER` / `$env:JIRA_TOKEN` 均为空 |

## 2. 实际日志（stderr）

```
Traceback (most recent call last):
  File "C:\Users\Administrator\agent\scripts\create_jira_tasks.py", line 185, in <module>
    sys.exit(main())
  File "C:\Users\Administrator\agent\scripts\create_jira_tasks.py", line 164, in main
    base = _env("JIRA_BASE_URL").rstrip("/")
  File "C:\Users\Administrator\agent\scripts\create_jira_tasks.py", line 114, in _env
    raise RuntimeError(f"缺少环境变量 {name}（请在 .env 或环境变量中配置）")
RuntimeError: 缺少环境变量 JIRA_BASE_URL（请在 .env 或环境变量中配置）
```

## 3. 错误原因分析

### 3.1 主因（确定性）：Jira 凭据未配置
脚本在 `main()` 执行任何 HTTP 请求**之前**即调用 `_env("JIRA_BASE_URL")` 做 fail-fast 校验（[create_jira_tasks.py#L164](file:///c:/Users/Administrator/agent/scripts/create_jira_tasks.py#L164)、[_env#L114](file:///c:/Users/Administrator/agent/scripts/create_jira_tasks.py#L114)）。三个凭据均缺失，第一个即抛 `RuntimeError`。

- `JIRA_BASE_URL`：缺失 → **本次实际失败点**
- `JIRA_USER`：同样缺失（同因，未走到）
- `JIRA_TOKEN`：同样缺失（同因，未走到）

**结论**：KN-101~105 未创建，且因 fail-fast 设计**不存在半创建状态**——这属于预期行为而非脚本缺陷。

### 3.2 脚本设计说明（为何安全）
`_env()` 校验先于循环，五个任务的数据（TASKS）已静态内置，任何创建都发生在全部凭据校验通过之后。缺凭据时退出码 1、无网络请求、无副作用。

### 3.3 潜在失败模式（凭据就绪后可能遇到的，预防性记录）

| # | 模式 | 症状 | 排查方向 |
|---|---|---|---|
| 1 | Token 无效/过期 | HTTP 401 | 重新生成 Jira API Token（非密码） |
| 2 | 无项目创建权限 | HTTP 403 | 检查用户在 `<project>` 的角色（需 Create Issues） |
| 3 | URL 错误/域名不可达 | 连接错误/HTTP 404 | 核对 `JIRA_BASE_URL` 为实例根地址（如 `https://xxx.atlassian.net`） |
| 4 | issue 类型名不存在 | HTTP 400 | 确认 `--type`（默认「技术任务」）在目标项目存在 |
| 5 | 优先级名不存在 | HTTP 400 | `High/Medium/Low` 为 Jira 默认三态，自定义方案需调整 |
| 6 | 组件名不存在 | HTTP 400 | 脚本用 `components: [{"name": "knowledge"}]`，需先在项目建该组件 |
| 7 | 幂等查询 API 版本 | 查询 404 | 新版 Jira 用 `/rest/api/3/search`；脚本当前为 `/rest/api/2/search` |
| 8 | 网络/代理 | 超时 | 公司代理需在环境变量配置，脚本 `timeout=30` |

## 4. 排查步骤（按序执行）

1. 在 `.env` 追加：
   ```
   JIRA_BASE_URL=https://<你的实例>.atlassian.net
   JIRA_USER=<你的邮箱>
   JIRA_TOKEN=<API Token>
   JIRA_PROJECT=<项目key>        # 可选，默认 KN
   ```
2. `python -B scripts/create_jira_tasks.py --dry-run` —— 验证 5 个任务数据解析（不请求）
3. `python -B scripts/create_jira_tasks.py` —— 实际创建（幂等：summary 匹配到已存在任务自动跳过）
4. 检查输出：`[created] KN-101 → <JIRA key>` 逐条
5. 复核：脚本 JQL 查询或 Jira UI 确认 5 条

## 5. 结论

- 本轮未创建任何任务（凭据缺失，fail-fast 正确拦截）；
- KN-101~105 的**定义数据已就绪**（标题/优先级/组件/完整 DoD 描述），凭据就绪后重跑即可，无需改动脚本；
- 若重跑后出现 §3.3 任一模式，按表排查。

## 6. 附：凭据安全提示

- `JIRA_TOKEN` 属敏感信息，`.env` 应保持 gitignore（本项目已忽略）；推送前勿将 `.env` 内容复制进提交。
