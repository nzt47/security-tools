# TASK-S8-03 灰度容器隔离（解锁"真实流量接管"）

> 所属阶段：**S8 生产化加固批次**｜依赖：S3-02（回放沙箱/判定集）、S3-03（shadow 灰度）、S4-03（注入防御/隔离声明）｜预估：6–9 人日
> 来源：S3-02 遗留 #7 / M6（"回放沙箱是进程内确定性执行模型，非容器隔离"）、S7-05 未完成项 #3（`real_takeover=false`、`container_isolated=false`）、v7.2 §5.1（生成代码一律 Docker）/§5.2（TEST 约束）
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`

---

## 一、目标描述

当前 shadow 灰度只能"**记录选中**"而不能**真实接管**，根因是：候选实现**没有可安全执行的环境**（回放沙箱是进程内模型，`real_takeover=false`、`container_isolated=false`）。本任务补齐执行隔离，从而**解锁真实流量接管**：

1. **双路径隔离**：Linux/Docker 环境用**容器**；Windows 开发机无 Docker 时用**强隔离子进程沙箱**，并**如实标注隔离等级**（不得把子进程冒充容器）；
2. **隔离边界可验证**（机器可读证据）：无宿主网络（除白名单）/ 无 `$HOME` / 无 SSH agent / 只读源码挂载 / 资源配额（CPU·内存·时间）/ 无宿主凭据；
3. **与既有沙箱协作**：`ReplaySandbox` 增加 `isolation_level ∈ {in_process, subprocess_hardened, container}`；**record-and-replay 语义不变（副作用绝不双写）**；
4. **灰度接管可开、默认关闭、失败可回退**：`real_takeover` 标志接线 + 每日预算 + 失败即回落 `sandbox_replay_only` + 审计。

## 二、执行步骤

### 步骤 1：隔离等级模型与选择策略
- 定义 `IsolationLevel` 与探测逻辑：检测 Docker 可用性 → `container`；否则 `subprocess_hardened`；两者都不可用 → 保持 `in_process`（现状）并**拒绝** `real_takeover`；
- 产出 `docs/zh/灰度执行隔离设计.md`：等级语义 / 各自保证的边界 / **不保证的边界**（诚实清单）/ 选择与降级策略。

### 步骤 2：容器路径（Linux/Docker）
- 镜像与启动参数：只读挂载源码、`--network none`（或白名单网络）、`--memory/--cpus/--pids-limit`、无 `--privileged`、不挂载宿主 `$HOME`/SSH/凭据、非 root 用户、临时工作目录；
- 候选执行入口：把候选实现（draft SKILL / 生成代码）与判定集输入送入容器 → 收集输出与**副作用记录**（record-and-replay：只记录不真实外发）；
- 超时与配额超限 → 容器内终止 + 记为 `quota_exceeded`（不静默）。

### 步骤 3：强隔离子进程路径（Windows 无 Docker）
- 复用 S4-04 已验证的隔离范式：`env_mode=replace`（清 `HOME`/`USERPROFILE`/`SSH_AUTH_SOCK` 等）、工作目录隔离、资源与超时限制、网络调用经既有 `egress_guard`（S4-02）；
- **诚实标注**：该等级**不提供**内核级隔离（无 namespace/cgroup），报告中必须写明与容器的差距。

### 步骤 4：与灰度链路接线
- `shadow.py`：`ShadowRunner` 支持按 `isolation_level` 执行候选；
- `real_takeover` 开关（默认关闭，环境变量显式开启）：开启后真实流量按抽样比例走候选执行（**仍在隔离环境内**，产物与结果比对后决定是否采用）；
- 预算与失败处理：每日预算（沿用 S3-03）、连续失败 N 次 → 自动回落 `sandbox_replay_only` + 事故卡；
- 全程：Trace（actor 标注）+ 审计 + 事件（`healing.triggered` / `digest.stage` 等既有类型）。

### 步骤 5：隔离性实测（核心验收）
- 产出探针脚本 `scripts/verify_isolation.py`：在目标等级内实测并输出**机器可读证据**：
  - 环境：`HOME`/`USERPROFILE`/`SSH_AUTH_SOCK` 为空、宿主凭据不可见、宿主工作目录不可见；
  - 网络：外部连接被拒（或仅白名单可达）；
  - 资源：内存/CPU/时间配额超限时被终止；
  - 文件：源码只读（写入被拒）、仅临时目录可写。
- 对容器与子进程**分别**跑，输出对比表（各自保证 vs 不保证）。

### 步骤 6：演示与回归
- 端到端演示：在容器（或子进程）隔离下跑通一次候选执行 → 产生灰度记录 → 验证**未双写真实环境** → 验证回退路径（连续失败自动回落）；
- 回归：`digestion`（shadow/sandbox/gate）、`subagent`（隔离范式）、`guardrails`（egress）邻接套件零回归；
- 撰写 `TASK-S8-03_验收报告.md`（含探针原始输出与对比表）。

## 三、预期成果

1. `docs/zh/灰度执行隔离设计.md`（等级语义 + **不保证边界**诚实清单）。
2. 容器路径与强隔离子进程路径的执行器；`ReplaySandbox.isolation_level` 接入。
3. `real_takeover` 开关（默认关闭）+ 预算 + 失败自动回落 + 审计。
4. `scripts/verify_isolation.py` 探针与对比表。
5. 端到端演示记录 + `TASK-S8-03_验收报告.md`。

## 四、评估标准（验收清单）

- [ ] 隔离等级模型落地；无 Docker 时**如实降级**并拒绝 `real_takeover`（不得冒充容器）
- [ ] 容器路径：只读源码 / 网络受限 / 资源配额 / 非 root / 无宿主凭据（配置与实测双证）
- [ ] 子进程路径：`HOME`/`SSH_AUTH_SOCK` 为空、宿主凭据不可见（实测证据）
- [ ] 探针对容器与子进程**分别**输出机器可读证据，并给出**不保证边界**对比
- [ ] `record-and-replay` 语义不变：**副作用只记录不双写**（用例断言真实环境无副作用）
- [ ] `real_takeover` **默认关闭**；开启需显式配置；有每日预算上限
- [ ] 连续失败 → 自动回落 `sandbox_replay_only` + 事故卡（用例）
- [ ] 端到端演示可复现（隔离执行 → 灰度记录 → 未双写 → 回退验证）
- [ ] 既有 `digestion`/`subagent`/`guardrails` 套件零回归；新增单测全绿、覆盖率 ≥80%

## 五、明确不做与风险

- ❌ **不做**"自动合入候选产物"（L2 白名单自动合入属后续决策，不在本任务）；
- ❌ **不做**集群级隔离（P5 Backlog）；
- ⚠️ **风险**：容器方案依赖 Docker（生产已有）；Windows 无 Docker 时能力受限——**必须如实降级并在 UI/报告中体现**，不得让"看起来能接管"。
