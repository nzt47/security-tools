# SonarQube 自包含集成修复技术总结（2026-08-08）

> 关联报告：`release_report_v1.0.0_20260807.md`（4.3 问题 4-6、4.4 本地端到端验证）
> 关联工作流：`.github/workflows/kwarg-sonarqube.yml`
> 作者：nzt47 | 日期：2026-08-08

---

## 1. 背景与目标

kwarg-scanner 的扫描结果需要导入 SonarQube，作为代码质量门禁的补充数据源。方案历经权衡：

- 初版思路：仓库 Secrets 配置 `SONAR_HOST_URL` / `SONAR_TOKEN` + 外部 SonarQube 服务器
- 定稿方案（本次采用）：**CI 自包含** —— job 内启动 SonarQube LTS 容器、动态生成 token、扫描上传后销毁，零外部依赖

目标：
1. 无外部服务器运维与凭据泄露面
2. 全链路 CI 可复现、失败可定位
3. 与既有「关键字参数冲突扫描 (Docker)」工作流语义一致（HIGH 阻断 + 报告上传）

## 2. 方案演进

| 阶段 | 方案 | 结论 |
|------|------|------|
| 初版 | Secrets + 公网 SonarQube | 放弃：依赖服务器运维、Secrets 暴露面 |
| 定稿 | CI 内起 `sonarqube:lts-community` + 动态 token + `--network host` 扫描 | 采用（本总结） |

## 3. 修复后工作流架构（步骤链）

```text
检出代码 (fetch-depth: 0)
  → 登录 GHCR（secrets.GHCR_TOKEN 拉私有镜像）
  → 生成 GIIF 报告（chmod 放开写权限 + &&/|| 捕获退出码）
  → 上传 GIIF artifact（if: always()）
  → HIGH 阻断检查（scan_exit != 0 则 exit 1）
  → 启动 SonarQube 容器（if: always()）
  → 等待就绪 + 生成 token（if: always()）
  → SonarQube 扫描：docker run --network host sonar-scanner-cli:10.0
    （if: always() && 报告存在 && token 非空）
  → 清理容器（if: always()）
```

关键设计点：**扫描阻断与 SonarQube 上传解耦**。即使 HIGH 阻断使 job 失败，`if: always()` 仍保证已生成的 GIIF 问题被上传，阻断不影响数据入库。

## 4. 三个失败根因深度剖析

### 4.1 写权限 PermissionError

- **现象**：容器内 `kwarg-scan` 写 `/project/kwarg-sonar-issues.json` 报 `PermissionError: [Errno 13]`
- **根因**：镜像以非 root 运行。Dockerfile `adduser -S -G scanner ...` 在 alpine（busybox）下创建系统用户 UID=100，`USER scanner` 生效；GitHub runner workspace 属主为 runner（UID 1001）。UID 不匹配 → 目录无写权限（读取正常，因为文件世界可读）
- **修复**：扫描前 `sudo chmod -R a+rwX ${github.workspace}`
- **验证**：修复后 CI 的 GIIF 步骤 success，报告正常写出

### 4.2 写权限崩溃被误判为 HIGH 阻断

- **现象**：CI 报 `high_risk_count: 1`、`reason: high_risk_detected` 并阻断；但本地用**同镜像（digest `24e14607…`）+ 同树（b28dd107）**复跑结果为 HIGH=0、exit 0
- **根因**：写失败 → Python traceback → `kwarg-scan` exit 1。入口脚本 `docker-entrypoint.sh` 对 exit 1 的处理存在缺陷：已配置 `OUTPUT_FILE` 但**报告文件缺失**时落入 else 分支，默认 `HIGH_COUNT=1`（该分支本意只覆盖"未配置 OUTPUT_FILE 时 exit 1 即 HIGH"），把"进程崩溃"误判为"发现 HIGH 风险"
- **修复**：
  1. 4.1 的 chmod 使写成功，崩溃不再发生（治本）
  2. GIIF 步骤改为 `docker run ... && SCAN_EXIT=0 || SCAN_EXIT=$?` 捕获真实退出码（治标：`set -e` 下原写法在 docker run 失败时会直接中断步骤，`scan_exit` 无法写入 `GITHUB_OUTPUT`）
- **证据链**：本地复跑 exit 0 + 报告 `{"issues": []}`，与 CI 的 HIGH=1 形成矛盾 → 锁定为崩溃误判

### 4.3 步骤中断链 + 网络不可达 + 扫描器版本漂移

三个子问题叠加导致首跑失败：

1. **步骤中断**：GIIF 步骤失败后，"启动 SonarQube"/"等待就绪" 无 `if: always()` 被跳过 → `SONAR_TOKEN` 为空 → 后续扫描步骤连接拒绝
2. **网络不可达**：`sonarqube-scan-action@v2` 容器运行于 docker bridge 网络，`localhost:9000` 指向容器自身，无法访问同机的 SonarQube 服务器容器
3. **版本漂移**：`sonar-scanner-cli:latest` 已升级为 Scanner 8.0.1.6346（SonarQube Cloud 专用：强制 `sonar.organization`、动态下载 JRE、不支持 9.9 Server）

**修复**：
- 启动/等待步骤加 `if: always()`
- 扫描改用 `docker run --network host` + 固定 `sonar-scanner-cli:10.0`（含 Scanner 5.0.1.3006，兼容 9.9 LTS）
- 加 `-Dsonar.scm.disabled=true` 规避 SCM git 探测（本地 worktree 的 `.git` 指针文件会导致 JGit 解析失败；CI 为正常 `.git` 目录不受影响，禁用后两侧行为一致）

**版本兼容性纠正**：早期排查曾推断"Scanner 5.0 调用 `/api/v2/analysis/version` 不被 9.9 支持"——**实测为误判**。Scanner 5.0.1（cli:10.0）与 SonarQube 9.9.8 LTS 完全兼容，`Analyzing on SonarQube server 9.9.8.100196` 并成功完成分析。

## 5. 本地端到端验证（对齐 CI 全链路）

| 验证项 | 结果 |
|--------|------|
| 9.9 LTS 容器就绪（首次 ES 初始化） | ✅ ~45s 达到 UP |
| token 生成（admin/admin → ci-token） | ✅ 44 字符 |
| Scanner 5.0.1 连接服务器（`--network host`） | ✅ `Analyzing on SonarQube server 9.9.8.100196` |
| GIIF 外部问题导入 | ✅ `Imported 0 issues`（空报告基线） |
| 分析执行与上传 | ✅ `ANALYSIS SUCCESSFUL / EXECUTION SUCCESS` |

> 复现方法：本地以与 CI 完全相同的镜像 digest、相同树、相同 env（`MIN_RISK=MEDIUM`、无 `--path`）执行扫描，得到 HIGH=0，直接证明 4.2 的 HIGH=1 为崩溃误判。

## 6. CI 验证结果（推送 `4349b0fa` 自动触发）

| 工作流 | run | 结果 |
|--------|-----|------|
| kwarg 扫描 → SonarQube | `31202747704` | ✅ success，2m35s（GIIF / 启动 / 就绪+token / 扫描 / 清理 全通过，HIGH 阻断正确跳过） |
| 关键字参数冲突扫描 (Docker) | `31202747729` | ✅ success，1m13s（prepare-image / HIGH 扫描 / MEDIUM 扫描） |

## 7. 遗留问题与建议

1. **`GHCR_TOKEN` 轮换**：该 PAT 多次出现在会话与推送记录中，建议立即轮换
2. **入口脚本加固（可选，需重建镜像）**：`docker-entrypoint.sh` 在"已配置 `OUTPUT_FILE` 但文件缺失"时应返回 `E_SCAN_CRASHED`（exit 3）而非默认 `HIGH_COUNT=1`，避免任何写失败场景再次产生误报
3. **自包含方案局限**：SonarQube 为 CI 内临时容器，分析结果不落持久化仪表盘；如需长期趋势看板需部署真实服务器（社区版自托管或 SonarQube Cloud）
4. **scanner 镜像 tag 固定**：已固定 `cli:10.0`，防止 `latest` 漂移到 Cloud-only 版本导致回归
5. **可选优化**：GIIF 扫描范围与 `sonar.sources=agent,packages` 对齐，减少对未索引文件的问题导入噪音

## 8. 相关 commit 与文件清单

| 项 | 值 |
|----|-----|
| 自包含改造 commit | `b28dd107` |
| 本次修复 commit（本地 → 远端 rebase 后） | `eeabdb25` → `4349b0fa` |
| 工作流文件 | `.github/workflows/kwarg-sonarqube.yml` |
| 发布报告 | `packages/kwarg_scanner/docs/release_report_v1.0.0_20260807.md` |
| 入口脚本（待加固） | `packages/kwarg_scanner/docker-entrypoint.sh` |
| 扫描镜像 | `ghcr.io/nzt47/kwarg-scanner:latest`（digest `sha256:24e14607…`） |
| SonarQube 服务器镜像 | `sonarqube:lts-community`（9.9.8 LTS） |
| 扫描器镜像 | `sonarsource/sonar-scanner-cli:10.0`（Scanner 5.0.1.3006） |
