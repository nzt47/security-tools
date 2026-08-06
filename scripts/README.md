# ChromaDB 导入降级预检工具包

守护 `agent/memory_optimized.py` 的「子进程探测 → 主进程导入 → Mock 兜底」三段式导入逻辑。
统一入口为 **`python -m agent.preflight`**（[agent/preflight/](../agent/preflight/) 包，单事实源），
本地与 CI 共用同一实现。**本工具包守护的内容曾因外部回滚丢失一次**，任何修改需保持
CLI（12 条路径）与 pytest（分支级 + 整体级）双份验证一致。

## 组成

| 文件 | 作用 |
|------|------|
| [agent/preflight/](../agent/preflight/) | 工具包核心：`runner.py`（12 条路径检查）+ `__main__.py`（CLI） |
| [chromadb_preflight.ps1](chromadb_preflight.ps1) | 本地一键预检薄壳（Windows/PowerShell） |
| [chromadb_preflight.sh](chromadb_preflight.sh) | 一键预检薄壳（Linux/bash） |
| [test_memory_optimized_import.py](../tests/unit/test_memory_optimized_import.py) | pytest 分支级 14 用例 |
| [test_preflight_runner.py](../tests/unit/test_preflight_runner.py) | pytest 整体级（复用 runner，取代 demo 脚本） |
| [view_chromadb_logs.ps1](view_chromadb_logs.ps1) | 过滤查看决策日志（action 链） |
| [Dockerfile](../Dockerfile) | 容器化（仅源码+pytest，无 torch/chromadb 重依赖） |

CI 集成见 [ci.yml](../.github/workflows/ci.yml) 的 `chromadb-preflight` job（容器化运行，
`unit-tests` 通过 `needs` 依赖它，预检失败即阻断 6 shard 矩阵）。

## 使用

```powershell
# 一键预检（CLI 12 条路径 + pytest 用例，失败即非零退出）
.\scripts\chromadb_preflight.ps1

# 直接调用统一 CLI（CI 与本地一致）
python -m agent.preflight
python -m agent.preflight --verbose          # 附带决策日志（logging INFO）

# 只看决策路径（probe_start → probe_ok → ready|chromadb|client_failed|timeout）
.\scripts\view_chromadb_logs.ps1
.\scripts\view_chromadb_logs.ps1 -Filter "timeout"   # 只看降级分支
```

```bash
# Linux / CI
bash scripts/chromadb_preflight.sh
python -m agent.preflight
```

### 容器化部署（Docker）

> 镜像仅含 `agent/` + `tests/unit/` + pytest（无 torch/chromadb 重依赖，约百 MB 级）。
> 完整 CI 集成见 [CI 集成指南](../docs/zh/知识库重构计划/CI_预检工具集成指南.md)。

**步骤 1：构建并本地验证**

```bash
docker build -t yunshu-preflight .
docker run --rm yunshu-preflight                        # 12 条导入路径 → exit 0
docker run --rm --entrypoint python yunshu-preflight -m pytest \
    tests/unit/test_memory_optimized_import.py \
    tests/unit/test_preflight_runner.py -q              # pytest 用例 → 全绿
```

**步骤 2（可选）：推送私有 registry，CI 免构建**

```bash
docker tag yunshu-preflight ghcr.io/<org>/preflight:latest
docker push ghcr.io/<org>/preflight:latest
# CI 中直接：
#   docker run --rm ghcr.io/<org>/preflight:latest
```

**步骤 3：CI 使用**（二选一）

```yaml
# A. CI 内构建（仓库内方式，当前 ci.yml 采用）
      - name: 构建预检镜像
        run: docker build -t yunshu-preflight .
      - name: 运行预检（容器）
        run: docker run --rm yunshu-preflight

# B. 预构建镜像（省构建时间，需先推送 registry）
      - name: 运行预检（容器）
        run: docker run --rm ghcr.io/<org>/preflight:latest
```

### 故障演练场景

> 开关：环境变量 `PREFLIGHT_FAKE_FAIL`（任意非空值触发），CLI 立即以 exit 1 结束。

| 场景 | 命令 | 预期 |
|------|------|------|
| 本地 CLI | `$env:PREFLIGHT_FAKE_FAIL="1"; python -m agent.preflight` | exit 1，stderr 显示"故障演练" |
| 本地容器 | `PREFLIGHT_FAKE_FAIL=1 docker run --rm yunshu-preflight` | exit 1 |
| CI 面板 | ci.yml 该步改为 `PREFLIGHT_FAKE_FAIL=1 docker run --rm yunshu-preflight` 后 push | `chromadb-preflight` 标红（`Process completed with exit code 1`），`unit-tests` 6 个矩阵 job 全部灰色 **Skipped** |
| 恢复 | 移除 `PREFLIGHT_FAKE_FAIL=1` 再 push | 恢复正常 |

面板细节：`docker run --rm -e CI=true --entrypoint python yunshu-preflight -m pytest ...` 时
pytest 走 CI 路径（conftest 封禁原生扩展）。演练完成后务必清理：`Remove-Item Env:PREFLIGHT_FAKE_FAIL`。

## 决策日志链（排查对照）

| 分支 | action 链 |
|------|-----------|
| 探测不可用 | `probe_start`(info) → `chromadb.timeout`(warn) |
| 导入失败 | `probe_start` → `probe_ok`(info) → `chromadb`(warn) |
| 创建失败 | `probe_start` → `probe_ok` → `chromadb.client_failed`(warn) |
| 全部成功 | `probe_start` → `probe_ok` → `chromadb.ready`(info) |

## 单事实源说明（不易）

- **逻辑归属**：12 条路径检查实现于 `agent/preflight/runner.py`；CLI 与 pytest 均调用
  `run_preflight()`，不再维护 demo/pytest 两套断言（原 `scripts/demo_memory_optimized_import.py`
  已删除）。
- **退出码契约**：0=全过 / 1=任一失败或故障演练；CI `needs` 阻断依赖该契约。
