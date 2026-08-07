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
| kwarg 扫描 → SonarQube | ❌ failure（SONAR_HOST_URL secret 未配置，与镜像无关） |
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

## 5. SonarQube 集成验证（GIIF 数据准确性）

| 验证项 | 结果 |
|--------|------|
| 外部问题导入 | ✅ 修复后 6 个 issues 全部落库（3 内置 + 3 外部） |
| severity/type 映射 | ✅ HIGH→MAJOR/BUG、MEDIUM→MINOR/BUG、LOW→INFO/CODE_SMELL |
| 文件路径关联 | ✅ 剥离 `/project` 前缀后与 `sonar.sources` 相对路径精确匹配 |
| 行号/列号 | ✅ 与实际代码位置一致 |

## 6. 结论与后续

- 镜像从 **191MB → 87.4MB（-54%）**，增量构建 **42s → 5.7s**，满足优化目标
- 预构建镜像已推送 GHCR 并被 CI 配置引用，本地模拟全链路验证通过
- **待办**: 修复 kwarg 工作流 `push.branches` 分支名不匹配（master vs main），使推送后自动触发扫描
