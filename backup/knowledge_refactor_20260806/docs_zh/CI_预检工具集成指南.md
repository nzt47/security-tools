# CI/CD 集成指南：ChromaDB 导入降级预检工具包

> 说明如何在 GitHub Actions 中集成预检工具包。当前 [ci.yml](../../../.github/workflows/ci.yml)
> 已按本指南实现（`chromadb-preflight` job，容器化运行）。本指南同时给出从零配置步骤
> 与自定义选项，供新仓库/新分支复用。
>
> 曾因外部回滚丢失，本文件重建。逻辑以 [agent/preflight/](../../../agent/preflight/) 与
> [scripts/README.md](../../../scripts/README.md) 为准。

## 1. 架构概览

```
                     ┌─────────────────────────────┐
                     │   chromadb-preflight job    │   CI（GitHub Actions）
                     └──────────────┬──────────────┘
                                    │ needs 阻断（失败即跳过）
        ┌───────────────────────────┴──────────────────────────┐
        ▼                                                       ▼
python -m agent.preflight（12 条路径）          pytest 用例（分支级 + 整体级，20 用例）
        │                                                       │
        └─────────────── 统一入口 agent/preflight/ ─────────────┘
                     （单事实源，本地/CI/容器共用）
```

- **统一入口**：`python -m agent.preflight`——本地、CI、容器三处完全一致
- **两道防线**：CLI（12 条导入路径，含 30s 子进程超时降级）+ pytest（20 用例）
- **零重依赖**：全 mock，无需 chromadb/torch；容器仅 python:3.12-slim + pytest
- **退出码契约**：0=全过 / 1=任一失败或 `PREFLIGHT_FAKE_FAIL` 故障演练

## 2. 当前集成方式（ci.yml 已配置）

```yaml
  chromadb-preflight:
    name: ChromaDB 导入降级预检（容器化）
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: 检出代码
        uses: actions/checkout@v6

      - name: 构建预检镜像
        run: docker build -t yunshu-preflight .

      - name: 运行预检（容器）
        run: |
          echo "=== 1/2 CLI：12 条导入路径 ==="
          docker run --rm yunshu-preflight
          echo "=== 2/2 pytest 用例（分支级 + 整体级）==="
          docker run --rm -e CI=true --entrypoint python yunshu-preflight -m pytest \
            tests/unit/test_memory_optimized_import.py \
            tests/unit/test_preflight_runner.py -q -p no:cacheprovider --no-header
          echo "=== ChromaDB 导入降级预检通过 ==="

  unit-tests:
    needs: [chromadb-preflight]   # 预检失败 → 整个矩阵被跳过，不消耗资源
    ...
```

## 3. 从零配置步骤（新仓库/复用）

1. **复制代码**：`agent/preflight/`、`Dockerfile`、`.dockerignore`、
   `tests/unit/test_memory_optimized_import.py`、`tests/unit/test_preflight_runner.py`
2. **验证本地**：
   ```bash
   python -m agent.preflight && python -m pytest tests/unit/test_memory_optimized_import.py tests/unit/test_preflight_runner.py -q
   ```
3. **添加 job**（见上节 YAML）：`chromadb-preflight` 独立轻量 job
4. **挂接阻断**：目标 job（如 `unit-tests`）加 `needs: [chromadb-preflight]`
5. **演练阻断**：按 [故障演练](#5-故障演练场景与预期) 验证一次

## 4. 自定义选项

| 场景 | 做法 |
|------|------|
| 无需容器（docker 不可用的 runner） | 两步直接跑：`python -m agent.preflight` + pytest 两文件（见 [scripts/chromadb_preflight.sh](../../../scripts/chromadb_preflight.sh)） |
| 降低构建耗时 | 预构建镜像推 registry：`docker tag yunshu-preflight ghcr.io/<org>/preflight && docker push ...`，CI 改 `docker run ghcr.io/<org>/preflight`（省 build 步） |
| 附带决策日志 | CLI 加 `--verbose`（或 `docker run --rm -e PYTHONVERBOSE ...`） |
| 调整超时 | `timeout-minutes: 10` 按镜像拉取/构建时长调整 |

> 注意：镜像内仅含 `agent/` + `tests/unit/`，**不包含** `scripts/`、`knowledge/`、
> `memory/` 等目录（见 `.dockerignore`）。若 CI 需在容器内跑其他代码，需扩展 Dockerfile COPY。

## 5. 故障演练场景与预期

| 场景 | 触发 | 预期 |
|------|------|------|
| 本地验证退出码 | `PREFLIGHT_FAKE_FAIL=1 python -m agent.preflight` | exit 1，stderr 显示"故障演练" |
| 容器演练 | `PREFLIGHT_FAKE_FAIL=1 docker run --rm yunshu-preflight` | exit 1 |
| CI 面板演练 | ci.yml 该步改为 `PREFLIGHT_FAKE_FAIL=1 docker run --rm yunshu-preflight` 后 push | preflight job 标红（`Process completed with exit code 1`），`unit-tests` 全部灰色 **Skipped** |
| 恢复 | 移除 `PREFLIGHT_FAKE_FAIL=1` 再 push | 恢复正常 |

面板表现细节见 [ci_preflight_fail_demo.md](../../../scripts/ci_preflight_fail_demo.md)。

## 6. 常见问题

- **docker 不可用**：回退非容器两步（见上表），阻断语义不变（`needs` 只看 job 退出码）。
- **构建慢**：首次拉 `python:3.12-slim` 较慢属正常；后续层缓存（pip 层不变则不重建）。
- **容器内 pytest 找不到 conftest**：必须同时 COPY `tests/conftest.py`（顶层）与
  `tests/unit/`（Dockerfile 已含）。
- **本地与 CI 行为不一致**：两端都走 `python -m agent.preflight`；若仍不一致，
  用容器验证（`docker run --rm yunshu-preflight --verbose`）。
