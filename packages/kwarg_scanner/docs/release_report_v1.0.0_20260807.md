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

### 4.2 远端真实触发（推送 `119f13a` 后 GitHub Actions）

| Workflow | 状态 |
|----------|------|
| 核心不变量监控 (verify_core_invariants) | ✅ completed / success（12/12 通过） |
| tlm-hook-failsafe E2E | 🔄 in_progress |
| Error Reporting System CI/CD | 🔄 in_progress |
| 部署文档到 GitHub Pages / 日志性能守护 / 可观测性质量保障 | ⏳ queued |

### 4.3 ⚠️ 已知问题：kwarg 扫描工作流未被触发

**根因**: [kwarg-docker-scan.yml](file:///c:/Users/Administrator/agent/.github/workflows/kwarg-docker-scan.yml#L11-L20) 与 `kwarg-sonarqube.yml` 的 `push.branches` 仅配置 `main` / `develop` / `release/**`，而项目主分支为 **`master`**，导致推送到 master 不会自动触发镜像扫描流水线。

**建议修复**: 在 `on.push.branches` 中加入 `master`（或统一分支策略）。

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
