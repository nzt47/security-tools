# CI 流水线预检集成指南（GitHub Actions / GitLab CI）

> 日期：2026-08-16
> 前置：预检脚本 [`scripts/ci_l3_context_preflight.py`](../scripts/ci_l3_context_preflight.py)
> 背景：镜像 context 与工作区代码漂移导致 130 项测试全量 ERROR，见
> [CI 修复验证报告](ci_l3_context_sync_verify_20260816.md)

## 1. 脚本契约

```bash
python scripts/ci_l3_context_preflight.py           # 文本报告
python scripts/ci_l3_context_preflight.py --json    # JSON 输出（推荐 CI 使用）
python scripts/ci_l3_context_preflight.py --git-clean-only  # 仅 git 一致性
```

- **退出码**：0 = 通过放行；1 = 任一校验失败（CI 应中断）
- **JSON 结构**：`{"<check_key>": {"label", "ok", "issues[]"}}`，4 个键固定
  （`build_files` / `critical_modules` / `git_clean` / `tracked_coverage`）

## 2. GitHub Actions 集成（已落地）

已集成于 `.github/workflows/l3-docker-tests.yml` 的 `build-image` job，
位于「检出代码」之后、「构建镜像」之前：

```yaml
      - name: 检出代码
        uses: actions/checkout@v6

      # [预检] 2026-08-16：context 与工作区漂移防拦截，构建前 fail fast
      - name: L3 context 一致性预检
        run: |
          echo "=== L3 context 一致性预检 ==="
          python scripts/ci_l3_context_preflight.py --json || exit 1

      - name: 设置 Docker Buildx
        uses: docker/setup-buildx-action@v3
```

要点：
- 在**构建之前**执行，避免把问题留到测试阶段（构建 ~17min 白跑）
- `|| exit 1` 强制失败即中断；`--json` 输出进入 step 日志便于定位
- 依赖 Python 3.8+（无第三方包，纯标准库）

## 3. GitLab CI 集成（.gitlab-ci.yml）

GitLab 用「预检 job」串联在构建 job 之前（`needs` 或阶段依赖）：

```yaml
stages:
  - preflight
  - build
  - test

# 预检 job：任何 context 不一致在此阶段拦截
l3-context-preflight:
  stage: preflight
  image: python:3.12-slim
  script:
    - python scripts/ci_l3_context_preflight.py --json || exit 1
  artifacts:
    when: always
    paths:
      - preflight_*.json
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"   # MR 必跑
    - if: $CI_COMMIT_BRANCH =~ /^(master|main|develop)$/ # 主分支必跑
    - when: manual                                      # 其余手动触发

# 构建 job 依赖预检通过（needs 表达式）
l3-build-image:
  stage: build
  needs: ["l3-context-preflight"]
  script:
    - docker build -f Dockerfile.linux-test -t agent-test-sqlite-vec:latest .
  # ... 其余构建步骤
```

要点：
- 独立 `preflight` 阶段 + `needs` 硬依赖，语义清晰
- `rules` 控制触发范围（MR / 主分支强制，其他手动）
- GitLab 需在 runner 环境预装 Python 3.8+（或用 `python:3.12-slim` 镜像）

## 4. 其他 CI 通用接入模板

任何 CI（Jenkins / CircleCI / Travis）同一模式：**检出 → 预检 → 构建**。

```bash
# 通用片段（bash）
set -e
python scripts/ci_l3_context_preflight.py --json
echo "预检通过，进入构建阶段"
```

## 5. 失败排查速查

| 失败项 | 含义 | 处置 |
|---|---|---|
| `build_files` | Dockerfile/compose/脚本缺失 | 检查检出完整性 |
| `critical_modules` | conftest 引用链模块缺失（如 `agent/skills_mgmt/lineage.py`） | `git pull` 同步代码，确认模块已入库 |
| `git_clean` | context 目录（agent/memory/scripts/tests）有未提交修改 | 提交/暂存后再跑，保证镜像快照 == HEAD |
| `tracked_coverage` | 已跟踪文件在磁盘缺失 | 还原工作区，防 context 打包遗漏 |
