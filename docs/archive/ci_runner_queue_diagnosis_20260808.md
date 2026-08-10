# CI Runner 排队风暴诊断报告（2026-08-08）

> 触发：observability-ci 全项目 run 排队 30+ 分钟无进展（2026-08-08 14:12 触发，15 job 全 queued）
> 治理：P2-4 concurrency 配置（commit `3703bd7d`）
> 关联：master_governance_retrospective_20260808.md（P2-4 规划来源）

---

## 1. 现象

| 指标 | 实测值 |
|---|---|
| 排队中的 run 数 | **36**（`gh api .../runs?per_page=100` 统计 queued） |
| 单 run 排队时长 | observability-ci 全项目 15 job **30+ 分钟**全 queued，无任何 job 开始执行 |
| 排队窗口 | 2026-08-08 22:16（UTC+8）→ 22:50 仍全 queued |
| workflow 总数 | **36 个**（.github/workflows/ 目录） |
| 最昂贵 job | observability-ci 全项目 6-shard（30-45min/run） |

## 2. 根因分析（四层）

### R1：run 生成量过大（结构性）
- 36 个 workflow × 多触发点（push/pull_request/schedule/cron/merge_group）多分支并发
- 单次 push 实测触发 **11+ 个 workflow run**（2026-08-08 多次观察）
- 密集 push 时 run 数量线性累积，无上限机制

### R2：无取消机制（本轮核心缺口）
- 全部 workflow **无 concurrency 配置**（2026-08-08 检查 4 个核心 workflow 均缺失）
- 同分支连续 push 产生的旧 run 持续排队/运行，与最新 run 重复占用 runner
- 旧 run 结果已过时（被新 commit 取代），纯浪费

### R3：公共 runner 资源受限（环境约束）
- GitHub 公共仓库 runner 并发受 plan 限制（repository 级 job 并发上限）
- 36 queued run × 每 run 多 job → job 排队深度大，observability-ci 15 job 全排队
- 排队 run 抢占 runner 后，新触发的 run 排队更长（正反馈放大）

### R4：昂贵 job 放大效应
- observability-ci 全项目 job 单 run 30-45min，等价消耗 = 数十个轻量 job
- 排队风暴下此类 job 的排队时长被指数放大（排队 + 执行都长）

## 3. 治理措施（P2-4，已实施）

commit `3703bd7d`：4 个核心 workflow（observability-ci / ci / ci-cd / tool-tests）新增：

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

**机制**：同 workflow + 同 ref 的新 run 启动时，旧 run（含排队中）立即取消。
- ✅ 同分支密集 push 不再累积重复 run（治 R2）
- ✅ 不同分支/PR 的 run 仍并行（group 含 ref）
- ⏳ 排队中的旧 run 被 cancel 后，其占用的排队 slot 释放（间接治 R1/R4）

## 4. 预期效果与遗留限制

**预期**：master 同 workflow 同时最多 1 个 run 在跑/排队 → 排队深度显著下降；36 queued 中同 ref 重复部分被清空。

**遗留限制（后续 P2 候选）**：
1. **跨 workflow 排队**仍存在——concurrency 仅治「同 workflow 同 ref」；36 个 workflow 中仅 4 个已配置。→ 建议其余高频 workflow 批量补配
2. 触发点缩减：`on:` 中可考虑按目录细化（部分 workflow 全分支触发）
3. 昂贵 job 拆分/降频：observability-ci 全项目可评估缩小分桶或降为 nightly（与 C3 联动）
4. 公共 runner 容量是平台约束，超出本仓库治理范围

## 5. 验证方法

push 后观察：同 ref 旧 observability-ci run（如 `31261391038`）应在新 run 启动时被 cancel（status=completed, conclusion=cancelled）；新 run（`31262348181`）正常执行。排队 run 总数应下降。
