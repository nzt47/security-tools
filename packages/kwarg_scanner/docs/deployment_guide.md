# kwarg-scanner Docker 镜像部署操作手册

> **版本**: 1.0.0
> **更新日期**: 2026-06-30
> **适用对象**: DevOps / CI 流水线维护者 / 开发者

本手册覆盖 kwarg-scanner Docker 镜像的**构建、本地运行、CI 集成、SonarQube 报告对接、故障排查**全流程操作步骤。

---

## 目录

1. [前置要求](#1-前置要求)
2. [镜像构建](#2-镜像构建)
3. [本地运行](#3-本地运行)
4. [CI 流水线集成](#4-ci-流水线集成)
5. [SonarQube 报告对接](#5-sonarqube-报告对接)
6. [故障排查](#6-故障排查)
7. [参考信息](#7-参考信息)

---

## 1. 前置要求

| 工具 | 版本要求 | 用途 |
|------|---------|------|
| Docker | >= 20.10 (建议 24+) | 构建/运行镜像 |
| Docker BuildKit | 默认开启 (Docker Desktop 自带) | 加速构建与缓存 |
| 可选: SonarQube | >= 9.9 LTS | 接收外部问题报告 |
| 可选: sonar-scanner | 任意 | 上传报告到 SonarQube |

验证环境:

```bash
docker --version
docker info | grep -i "server version"   # 确认 daemon 已启动
```

---

## 2. 镜像构建

### 2.1 标准构建

```bash
# 在项目根目录执行
docker build -t kwarg-scanner:1.0.0 -t kwarg-scanner:latest ./packages/kwarg_scanner
```

### 2.2 构建参数说明

镜像构建过程共 8 层，构建逻辑如下:

| 步骤 | 内容 | 耗时占比 |
|------|------|---------|
| FROM | 拉取 python:3.12-slim 基础镜像 | 高（仅首次） |
| LABEL | OCI 元数据标签 | 极低 |
| RUN | 创建非 root 用户 scanner | 低 |
| WORKDIR | 设置 /project 挂载点 | 极低 |
| COPY | 复制 pyproject.toml + 包源码 | 低 |
| RUN | pip 安装 + 清理缓存 | 中 |
| COPY | 复制入口脚本 + 去除 CRLF + 赋权 | 低 |
| HEALTHCHECK/ENTRYPOINT/CMD | 元数据 | 极低 |

### 2.3 利用缓存加速重复构建

**关键**: 仅复制 `pyproject.toml` 再安装依赖，改动源码不会导致 pip 层重建：

```bash
# 首次构建（全量）
docker build -t kwarg-scanner:latest .

# 后续构建（仅当 pyproject.toml 变更时 pip 层才重建）
# 源码变更 → 命中缓存，秒级完成
docker build -t kwarg-scanner:latest .
```

### 2.4 多架构构建（可选）

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t kwarg-scanner:1.0.0 \
  --push . \
  ./packages/kwarg_scanner
```

### 2.5 构建验证

```bash
# 检查镜像存在
docker images kwarg-scanner

# 验证健康状态
docker run --rm kwarg-scanner --health
# 预期输出: {"status":"healthy","scanner":"available","version":"1.0.0"}

# 验证版本
docker run --rm kwarg-scanner --version
# 预期输出: kwarg-scanner 1.0.0
```

---

## 3. 本地运行

### 3.1 默认模式（HIGH 风险阻断）

```bash
# 挂载当前目录到 /project，默认扫描 /project，HIGH 风险时退出码 1
docker run --rm -v "$(pwd):/project" kwarg-scanner
```

### 3.2 指定扫描路径

```bash
# 方式 1: 命令行参数
docker run --rm -v "$(pwd):/project" kwarg-scanner --path /project/src --min-risk HIGH

# 方式 2: 环境变量
docker run --rm -v "$(pwd):/project" \
  -e SCAN_PATH=/project/src \
  -e MIN_RISK=HIGH \
  kwarg-scanner
```

### 3.3 输出 JSON 报告到文件

```bash
docker run --rm -v "$(pwd):/project" \
  -e MIN_RISK=HIGH \
  -e OUTPUT_FORMAT=json \
  -e OUTPUT_FILE=/project/report.json \
  kwarg-scanner
# 报告写入挂载目录下的 report.json
```

### 3.4 输出 SonarQube 兼容报告

```bash
docker run --rm -v "$(pwd):/project" \
  -e MIN_RISK=MEDIUM \
  -e OUTPUT_FORMAT=sonarqube \
  -e OUTPUT_FILE=/project/kwarg-sonar-issues.json \
  kwarg-scanner
# 生成 SonarQube Generic Issue Import Format 报告
```

### 3.5 启用结构化日志

```bash
docker run --rm -v "$(pwd):/project" \
  -e ENABLE_LOGGING=true \
  kwarg-scanner
# stderr 输出含 trace_id/module_name/action/duration_ms 的 JSON 日志
```

### 3.6 扫描 MEDIUM 及以上风险（代码审查用）

```bash
docker run --rm -v "$(pwd):/project" -e MIN_RISK=MEDIUM kwarg-scanner
```

### 3.7 交互式调试（进入容器）

```bash
# 覆盖入口点，进入 shell
docker run --rm -it -v "$(pwd):/project" --entrypoint /bin/sh kwarg-scanner
# 容器内手动执行:
#   kwarg-scan --path /project --min-risk HIGH --format json
#   docker-entrypoint.sh --health
```

### 3.8 退出码速查

```bash
docker run --rm -v "$(pwd):/project" kwarg-scanner; echo "exit=$?"
# exit=0 通过    exit=1 HIGH 风险阻断    exit=2 参数错误    exit=3 内部错误
```

---

## 4. CI 流水线集成

### 4.1 GitHub Actions

参考仓库内的 [.github/workflows/kwarg-docker-scan.yml](../../.github/workflows/kwarg-docker-scan.yml)（含 HIGH 阻断、MEDIUM 提醒、手动触发三任务）。

最小配置:

```yaml
name: 关键字参数冲突扫描
on: [push, pull_request]
jobs:
  kwarg-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: 构建镜像
        run: docker build -t kwarg-scanner:local ./packages/kwarg_scanner
      - name: 运行扫描（HIGH 阻断）
        run: |
          docker run --rm \
            -v "${{ github.workspace }}:/project" \
            -e MIN_RISK=HIGH \
            -e OUTPUT_FORMAT=json \
            -e OUTPUT_FILE=/project/kwarg-report.json \
            kwarg-scanner:local
      - name: 上传报告
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: kwarg-scan-report
          path: kwarg-report.json
```

### 4.2 GitLab CI

```yaml
# .gitlab-ci.yml
kwarg-scan:
  stage: test
  image: docker:24
  services:
    - docker:24-dind
  script:
    - docker build -t kwarg-scanner:local ./packages/kwarg_scanner
    - docker run --rm -v "$PWD:/project" -e MIN_RISK=HIGH kwarg-scanner:local
  artifacts:
    when: always
    paths:
      - kwarg-report.json
```

### 4.3 Jenkins Pipeline

```groovy
// Jenkinsfile
pipeline {
    agent any
    stages {
        stage('kwarg-scan') {
            steps {
                sh '''
                    docker build -t kwarg-scanner:local ./packages/kwarg_scanner
                    docker run --rm -v "$WORKSPACE:/project" \\
                        -e MIN_RISK=HIGH \\
                        -e OUTPUT_FORMAT=json \\
                        -e OUTPUT_FILE=/project/kwarg-report.json \\
                        kwarg-scanner:local
                '''
            }
        }
    }
}
```

### 4.4 使用预构建镜像（推荐，省去每次构建）

```bash
# 1. 打标签并推送到 GHCR 私有仓库
docker tag kwarg-scanner:1.0.0 ghcr.io/nzt47/kwarg-scanner:1.0.0
docker login ghcr.io -u <username> --password-stdin
docker push ghcr.io/nzt47/kwarg-scanner:1.0.0

# 2. CI 直接拉取使用（私有仓库需先登录）
docker login ghcr.io -u <username> --password-stdin
docker run --rm -v "$(pwd):/project" ghcr.io/nzt47/kwarg-scanner:1.0.0
```

> **镜像体积**: 优化后镜像约 87MB（`python:3.12-alpine` 基础镜像，多阶段构建）。
> 详细构建性能分析见 [build_performance_report.md](./build_performance_report.md)。

---

## 5. SonarQube 报告对接

扫描器支持输出 **SonarQube Generic Issue Import Format (GIIF)**，可直接导入 SonarQube 统一展示。

### 5.1 生成 GIIF 报告

```bash
docker run --rm -v "$(pwd):/project" \
  -e MIN_RISK=MEDIUM \
  -e OUTPUT_FORMAT=sonarqube \
  -e OUTPUT_FILE=/project/kwarg-sonar-issues.json \
  kwarg-scanner
```

生成文件示例:

```json
{
  "issues": [
    {
      "engineId": "kwarg-scanner",
      "ruleId": "python:kwarg-conflict",
      "severity": "MAJOR",
      "type": "BUG",
      "primaryLocation": {
        "message": "函数 emit 接受 **kwargs，显式参数 trace_id 可能与 **payload 中的同名键冲突。修复: 过滤保留键 _RESERVED = {'trace_id'}",
        "filePath": "agent/skills_mgmt/observability.py",
        "textRange": {
          "startLine": 68,
          "endLine": 68,
          "startColumn": 0,
          "endColumn": 1
        }
      }
    }
  ]
}
```

### 5.2 风险等级 → SonarQube 严重度映射

| 扫描风险 | SonarQube Severity | SonarQube Type |
|---------|-------------------|----------------|
| HIGH   | MAJOR             | BUG            |
| MEDIUM | MINOR             | BUG            |
| LOW    | INFO              | CODE_SMELL     |

> **路径规则**: 容器内扫描根为挂载点 `/project`，生成 GIIF 时 `filePath`
> 自动剥离该前缀（`/project/src/x.py` → `src/x.py`），与 sonar-scanner
> 分析的相对路径对齐，确保外部问题可关联到已分析文件。

### 5.3 通过 sonar-scanner 上传（CI 场景）

```bash
# 生成 GIIF 报告
docker run --rm -v "$(pwd):/project" \
  -e MIN_RISK=MEDIUM \
  -e OUTPUT_FORMAT=sonarqube \
  -e OUTPUT_FILE=/project/kwarg-sonar-issues.json \
  kwarg-scanner

# 配置 sonar-project.properties 指定外部报告路径
cat > sonar-project.properties <<'EOF'
sonar.projectKey=yunshu-agent
sonar.sources=agent
sonar.externalIssuesReportPaths=kwarg-sonar-issues.json
EOF

# 执行 sonar-scanner
sonar-scanner \
  -Dsonar.host.url=$SONAR_HOST_URL \
  -Dsonar.token=$SONAR_TOKEN
```

### 5.4 GitHub Actions 完整集成（SonarQube + kwarg-scanner）

```yaml
jobs:
  sonarqube:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: 生成 kwarg 报告（GIIF）
        run: |
          docker build -t kwarg-scanner:local ./packages/kwarg_scanner
          docker run --rm -v "${{ github.workspace }}:/project" \
            -e MIN_RISK=MEDIUM \
            -e OUTPUT_FORMAT=sonarqube \
            -e OUTPUT_FILE=/project/kwarg-sonar-issues.json \
            kwarg-scanner:local
      - name: SonarQube 扫描
        uses: sonarsource/sonarqube-scan-action@v2
        env:
          SONAR_HOST_URL: ${{ secrets.SONAR_HOST_URL }}
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
```

> **注意**: SonarQube 需在项目配置中开启外部问题导入权限，或使用具备该权限的 token。

---

## 6. 故障排查

| 症状 | 可能原因 | 解决方案 |
|------|---------|---------|
| `exec: "docker-entrypoint.sh": executable file not found` | CRLF 行尾导致 shebang 失效 | Dockerfile 已内置 `sed -i 's/\r$//'` 修复；重新构建镜像 |
| `E_PROJECT_NOT_MOUNTED` | 未挂载 /project | 加 `-v "$(pwd):/project"` |
| `E_INVALID_RISK_LEVEL` | MIN_RISK 值非法 | 仅支持 LOW/MEDIUM/HIGH |
| 扫描结果全为 0 | 挂载路径错误/目录为空 | 确认挂载目录含 .py 文件，`--path` 指向正确子目录 |
| 容器无写权限 | 非 root 用户 scanner | OUTPUT_FILE 写入挂载目录时，确认宿主目录可写 |
| `docker: permission denied` | 当前用户无 docker 权限 | 将用户加入 docker 组或使用 root 执行 |
| 镜像构建慢 | 无缓存 + 网络慢 | 启用 BuildKit 缓存，或使用预构建镜像 |

### 日志排查

```bash
# 查看结构化日志（stderr）
docker run --rm -v "$(pwd):/project" -e ENABLE_LOGGING=true kwarg-scanner 2>&1
```

---

## 7. 参考信息

### 7.1 环境变量总表

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SCAN_PATH` | `/project` | 扫描路径 |
| `MIN_RISK` | `HIGH` | 最低风险等级 LOW/MEDIUM/HIGH |
| `OUTPUT_FORMAT` | `text` | 输出格式 text/json/sonarqube |
| `OUTPUT_FILE` | (空) | 输出文件路径（空则输出 stdout） |
| `ENABLE_LOGGING` | `false` | 是否输出结构化 JSON 日志 |

### 7.2 退出码

| 码 | 含义 | CI 行为 |
|----|------|---------|
| 0 | 扫描通过，无 HIGH 风险 | 通过 |
| 1 | 发现 HIGH 风险 | 阻断 |
| 2 | 参数错误 | 阻断 |
| 3 | 扫描器内部错误 | 阻断 |

### 7.3 文件清单

| 文件 | 说明 |
|------|------|
| `packages/kwarg_scanner/Dockerfile` | 镜像构建定义 |
| `packages/kwarg_scanner/docker-entrypoint.sh` | CI 入口脚本（结构化日志 + 退出码） |
| `packages/kwarg_scanner/.dockerignore` | 构建上下文排除规则 |
| `packages/kwarg_scanner/kwarg_scanner/reporter.py` | 报告生成（含 SonarQube GIIF） |
| `.github/workflows/kwarg-docker-scan.yml` | GitHub Actions 集成 |
| `.github/workflows/kwarg-sonarqube.yml` | SonarQube 上传集成 |

### 7.4 构建时间与优化

| 优化项 | 效果 |
|--------|------|
| `COPY pyproject.toml` 先于源码 | pip 层缓存复用，源码改动不重装依赖 |
| `pip install --no-cache-dir` | 减小镜像体积 |
| 合并 RUN 指令 | 减少层数 |
| `pip cache purge` | 清理 pip 缓存减小体积 |
| 多阶段构建（可选） | 进一步瘦身（当前为单阶段） |
| 预构建镜像推送到 Registry | CI 免构建，直接拉取 |

详细耗时分析见 [build_performance_report.md](./build_performance_report.md)。
