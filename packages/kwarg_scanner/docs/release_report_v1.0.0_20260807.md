# kwarg-scanner v1.0.0 发布报告

> **发布日期**: 2026-08-07
> **发布范围**: 镜像优化 + CI 预构建镜像改造 + SonarQube GIIF 路径修复
> **发布人**: nzt47

---

## 1. 发布内容总览

| 项目 | 内容 |
|------|------|
| 镜像版本 | **1.0.0**（tag: `latest` / `1.0.0` / `1.0.0-alpine`） |
| 镜像仓库 | `ghcr.io/nzt47/kwarg-scanner`（私有，GHCR） |
| 镜像 digest | `sha256:24e146072118b2f6a751539a960cf5a1b6e3cb6f872806688fe62856febb6a27` |
| Git 提交 | `f337f7b4`（kwarg-scanner 优化）+ `119f13a7`（知识库模块） |
| 推送分支 | `origin/master`（`d3755b7c..119f13a7`） |

### 1.1 本次 Git 变更（commit `f337f7b4`，9 文件 +126/-27）

- **Dockerfile**: 基础镜像 `python:3.12-slim` → `python:3.12-alpine`；修正 OCI `source` label 指向真实源码仓库
- **reporter.py / cli.py**: GIIF 报告新增 `base_path` 参数，剥离容器内 `/project` 扫描根前缀，修复外部问题无法导入 SonarQube 的路径匹配 bug
- **CI 工作流**: 新增 `publish-image` job（显式 `packages: write` 权限）自动推送 GHCR；默认改用预构建镜像（`BUILD_LOCALLY=false`），免去每次 CI 构建
- **测试**: 新增 2 个 base_path 用例（共 28 单测通过）

## 2. 镜像大小优化

| 指标 | 优化前（单阶段 slim） | 优化后（多阶段 alpine） | 变化 |
|------|---------------------|------------------------|------|
| 镜像大小 | 191 MB | **87.4 MB** | **-54%** |
| 应用层 | ~11 MB | ~11 MB | 持平 |
| 基础层 | 179 MB（slim） | ~76 MB（alpine） | 主要收益来源 |

**优化手段**:
1. 多阶段构建（builder 预装构建依赖 + runtime 离线安装 wheel，消除 build isolation 重复下载）
2. 基础镜像切换 alpine（零依赖纯 Python 包，无编译/动态库兼容风险）
3. `--no-cache-dir` + `pip cache purge` + 删除临时 wheel + `.dockerignore`

## 3. 构建时间优化

| 场景 | 优化前 | 优化后（实测） | 提升 |
|------|--------|---------------|------|
| 增量构建（CI 最常见，缓存命中） | ~42s | **5.7s** | **7.4 倍加速** |
| 全量冷构建（无缓存） | 单阶段 40.6s（pip 层） | 101.3s（网络下载主导） | 受带宽限制 |

> 冷构建耗时集中在依赖下载（本机带宽 ~19kB/s），非构建逻辑问题；CI 环境网络良好时大幅缩短。

## 4. CI 验证结果

### 4.1 本地完整模拟（对齐 CI 工作流命令）

| CI 步骤 | 验证命令 | 结果 |
|---------|---------|------|
| 登录 GHCR | `docker login ghcr.io -u nzt47` | ✅ Login Succeeded |
| 拉取预构建镜像 | `docker pull ghcr.io/nzt47/kwarg-scanner:latest` | ✅ digest 匹配 |
| 镜像健康检查 | `docker run --rm IMAGE --health` | ✅ healthy, v1.0.0 |
| high-risk-scan | `-e MIN_RISK=HIGH --path /project/agent` | ✅ 399 文件 / HIGH=0 / **exit 0** |
| medium-risk-scan | `-e MIN_RISK=MEDIUM --path /project/agent` | ✅ MEDIUM=0 / exit 0 |
| SonarQube GIIF | `-e OUTPUT_FORMAT=sonarqube` | ✅ GIIF issues 输出正常 |

### 4.2 远端真实触发（推送 `f975c0d9` 后 GitHub Actions）

| Workflow | 状态 |
|----------|------|
| **关键字参数冲突扫描 (Docker)** | ✅ **completed / success** |
| ├─ 准备扫描器镜像（prepare-image） | ✅ success（PAT 登录 + 拉取 + 健康检查） |
| ├─ HIGH 风险扫描 (Docker, 阻断) | ✅ success（exit 0，无 HIGH） |
| ├─ MEDIUM 风险扫描 (Docker, 提醒) | ✅ success |
| └─ 构建并推送镜像到 GHCR / 自定义扫描 | ⏭ skipped（非 main 分支 / 非手动触发，预期行为） |
| kwarg 扫描 → SonarQube | ❌ 首跑失败（3 项根因：写权限 / 误判 HIGH / 步骤中断，见 4.3 问题 4-6 与 4.4） |
| 核心不变量监控 (verify_core_invariants) | ✅ completed / success（12/12 通过） |

### 4.3 问题修复记录（本发布后续迭代）

**问题 1：工作流未被触发**（已修复 commit `c6def20f`）
- 根因: `push.branches` 仅配 `main`/`develop`/`release/**`，项目主分支为 `master`
- 修复: 两个 kwarg 工作流 `push.branches` + `pull_request.branches` 加入 `master`

**问题 2：GHCR 私有镜像拉取 denied**（已修复 commit `1b0d0794`）
- 根因: 包 `ghcr.io/nzt47/kwarg-scanner` 为用户空间私有包且未关联仓库，`GITHUB_TOKEN` 仅能访问与工作流仓库关联的包
- 修复: 3 处 GHCR 登录（publish-image / prepare-image / sonarqube）改用 `secrets.GHCR_TOKEN`（PAT，已配置仓库 Secret）

**问题 3：扫描 job 拉镜像 unauthorized**（已修复 commit `f975c0d9`）
- 根因: high-risk-scan / medium-risk-scan / custom-scan 每个 job 独立 runner，prepare-image 的 `docker login` 不跨 job 共享
- 修复: 三个扫描 job 在 `docker run` 前补充 `docker/login-action`（`BUILD_LOCALLY=true` 时跳过）

**问题 4：GIIF 报告写入 PermissionError**（已修复）
- 根因: 镜像内 scanner 用户为非 root（UID 100），runner workspace 属主为 runner（UID 1001），容器无法写 `/project/kwarg-sonar-issues.json`
- 修复: GIIF 扫描前 `sudo chmod -R a+rwX ${github.workspace}` 放开写权限

**问题 5：写权限崩溃被误判为 HIGH 阻断**
- 根因: `kwarg-scan` 因写失败崩溃 exit 1，入口脚本 `docker-entrypoint.sh` 在报告文件缺失时走 else 分支默认 `HIGH_COUNT=1` → 误报 "发现 HIGH 风险"（同镜像同树本地复跑实际 **HIGH=0 / exit 0**）
- 修复: 同问题 4（权限放开后不再崩溃）；GIIF 步骤改用 `docker run ... && SCAN_EXIT=0 || SCAN_EXIT=$?` 捕获真实退出码，避免 `set -e` 直接中断步骤

**问题 6：扫描容器无法连通 SonarQube + 步骤中断链**（已修复）
- 根因1: GIIF 步骤失败后，"启动 SonarQube"/"等待就绪" 步骤无 `if: always()` 被跳过 → `SONAR_TOKEN` 为空 → 扫描连接拒绝
- 根因2: `sonarqube-scan-action@v2` 容器运行于 bridge 网络，`localhost:9000` 指向容器自身，无法访问同机 SonarQube 容器；且 `sonar-scanner-cli:latest` 已升级为 Scanner 8.0（仅支持 SonarQube Cloud，强制要求 `sonar.organization`）
- 修复: 启动/等待步骤加 `if: always()`；改用 `docker run --network host` + 固定 `sonar-scanner-cli:10.0`（Scanner 5.0.1，兼容 9.9 LTS）；加 `-Dsonar.scm.disabled=true` 规避 SCM git 探测

### 4.4 SonarQube 自包含链路修复与本地端到端验证

CI 首跑（`b28dd107`, run `31198301660`）失败后，本地用同镜像（digest `24e14607…`）完整复现并验证：

| 验证项 | 结果 |
|--------|------|
| 9.9 LTS 容器就绪（首次 ES 初始化） | ✅ ~45s 达到 UP |
| token 生成（admin/admin → ci-token） | ✅ 44 字符 |
| Scanner 5.0.1 连接服务器（`--network host`） | ✅ `Analyzing on SonarQube server 9.9.8.100196` |
| GIIF 外部问题导入 | ✅ `Imported 0 issues`（空报告基线） |
| 分析执行与上传 | ✅ `ANALYSIS SUCCESSFUL / EXECUTION SUCCESS` |

结论:
- **CI 自包含方案成立**：job 内起 9.9 LTS 容器 + 动态生成 token，**无需 SONAR_HOST_URL / SONAR_TOKEN 仓库 Secrets**
- 同镜像同树本地复跑 HIGH=0，证实 CI 的 HIGH=1 为写权限崩溃误判（问题 5）

## 5. SonarQube 集成验证（GIIF 数据准确性）

| 验证项 | 结果 |
|--------|------|
| 外部问题导入 | ✅ 修复后 6 个 issues 全部落库（3 内置 + 3 外部） |
| severity/type 映射 | ✅ HIGH→MAJOR/BUG、MEDIUM→MINOR/BUG、LOW→INFO/CODE_SMELL |
| 文件路径关联 | ✅ 剥离 `/project` 前缀后与 `sonar.sources` 相对路径精确匹配 |
| 行号/列号 | ✅ 与实际代码位置一致 |
| CI 自包含链路（9.9 LTS + Scanner 5.0.1） | ✅ 本地端到端 ANALYSIS SUCCESSFUL（见 4.4） |

## 6. 结论与后续

- 镜像从 **191MB → 87.4MB（-54%）**，增量构建 **42s → 5.7s**，满足优化目标
- 预构建镜像已推送 GHCR 并被 CI 配置引用，本地模拟全链路验证通过
- kwarg 工作流 `push.branches` 已加 master（commit `c6def20f`），推送后自动触发扫描
- SonarQube 自包含链路已本地端到端验证（4.4），修复提交后将重跑 CI 确认 success
- **后续建议**: ① 轮换 `GHCR_TOKEN`（多次出现在会话/推送记录）；② 清理本地 `.tmp-sq-*` 临时目录；③ 可选加固 `docker-entrypoint.sh`（配置了 OUTPUT_FILE 但文件缺失时应返回 `E_SCAN_CRASHED` 而非默认 `HIGH_COUNT=1`），需重建并重推镜像
