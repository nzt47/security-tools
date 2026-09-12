# START-S8-03 灰度容器隔离（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S8-03_灰度容器隔离.md`](TASK-S8-03_灰度容器隔离.md)（★ 必须先完整阅读）
> 批次与通用约定 → [`PARALLEL_S8批次总表.md`](PARALLEL_S8批次总表.md)｜基线：`master` / `7e094eab`｜预估：6–9 人日

---

## 一、开工前确认

1. `--base master`；worktree id 用 **`s803`**。
2. 依赖 S3-02（沙箱/判定集）、S3-03（shadow）、S4-03（隔离范式）——**均已结案**。
3. 本任务**解锁 `real_takeover`**（当前 `real_takeover=false`、`container_isolated=false`）——但**默认仍关闭**。
4. **诚实底线**：无 Docker 时降级为强隔离子进程，**必须如实标注等级**，不得把子进程冒充容器。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S8-03 — 灰度容器隔离（解锁"真实流量接管"）
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S8-03_灰度容器隔离.md（★ 必须先完整阅读）
【批次总表】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\PARALLEL_S8批次总表.md
【上游依据】S3-02 遗留 #7 / M6；S7-05 未完成项 #3；v7.2 §5.1（生成代码一律 Docker）/§5.2（TEST 约束）
【预估】6–9 人日｜【状态】无待裁定项

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s803 --base master
此后所有 git 操作在 .worktrees/s803/ 内；主工作区禁令与提交流程见批次总表 §三。
双远端推送：git push origin master && git push gitee master

━━━ 二、任务目标（摘要）━━━
1. 隔离等级模型：IsolationLevel ∈ {in_process, subprocess_hardened, container}；
   探测 Docker 可用性 → container；否则 subprocess_hardened；都不可用 → 保持 in_process 并**拒绝 real_takeover**
   产出 docs/zh/灰度执行隔离设计.md（等级语义 + **不保证边界**诚实清单 + 选择/降级策略）
2. 容器路径（Linux/Docker）：只读挂载源码 / --network none（或白名单）/ --memory·--cpus·--pids-limit /
   无 --privileged / 不挂载宿主 $HOME·SSH·凭据 / 非 root / 临时工作目录
3. 子进程路径（Windows 无 Docker）：复用 S4-04 已验证范式（env_mode=replace 清 HOME/USERPROFILE/SSH_AUTH_SOCK、
   工作目录隔离、资源与超时限制、网络经既有 egress_guard）——**如实标注不提供内核级隔离**
4. 与灰度链路接线：
   · ReplaySandbox 增加 isolation_level；**record-and-replay 语义不变（副作用绝不双写）**
   · ShadowRunner 按 isolation_level 执行候选
   · real_takeover 开关（**默认关闭**，环境变量显式开启）：开启后按抽样比例走候选执行（仍在隔离环境内）
   · 每日预算（沿用 S3-03）；连续失败 N 次 → 自动回落 sandbox_replay_only + 事故卡
   · 全程 Trace（actor 标注）+ 审计 + 既有事件类型
5. 隔离性实测（核心验收）：scripts/verify_isolation.py 输出**机器可读证据**——
   环境（HOME/SSH_AUTH_SOCK 空、宿主凭据不可见、宿主目录不可见）/ 网络（外部连接被拒或仅白名单）/
   资源（超限被终止）/ 文件（源码只读、仅临时目录可写）；**容器与子进程分别跑并出对比表**

━━━ 三、已就绪前置：勿重复实现 ━━━
  • 沙箱与判定集：agent/digestion/sandbox.py（ReplaySandbox 三层比对 + record-and-replay + SandboxQuota）、cases.py
  • 灰度与内化：agent/digestion/shadow.py（预算/抽样/judge_kind/p99_wall）、internalize.py（六条件）
  • 隔离范式（已验证）：agent/subagent/（env_mode=replace、子进程内 HOME/SSH 为空的实测范式）
  • 出域控制：agent/guardrails/egress_guard.py（S4-02）
  • 自愈与事故卡：agent/self_healing/levels.py（raise_incident）
  ★ 不要自建第二套沙箱/回放

━━━ 四、本任务特有硬约束 ━━━
1. **等级如实标注**：无 Docker 不得冒充容器；设计文档必须列"不保证的边界"
2. **副作用只记录不双写**（用例断言真实环境无副作用）
3. real_takeover **默认关闭**；开启需显式配置 + 有预算上限
4. 连续失败自动回落 `sandbox_replay_only` + 事故卡（用例）
5. 资源超限被终止时记 `quota_exceeded`（不静默）
6. **不做**自动合入候选产物（属 L2，另议）；**不做**集群级隔离（P5）

━━━ 五、验收与交付物 ━━━
交付：1) 灰度执行隔离设计.md；2) 容器路径 + 子进程路径执行器；3) ReplaySandbox.isolation_level 接入；
      4) real_takeover 开关（默认关）+ 预算 + 失败回落；5) scripts/verify_isolation.py + 对比表；
      6) 端到端演示（隔离执行 → 灰度记录 → 未双写 → 回退验证）；7) TASK-S8-03_验收报告.md；
      8) S8-03_交付结案报告_<日期>.md；9) 更新 00_总览 状态行
验收：逐条对照任务书 §四（含等级如实、探针证据、未双写、默认关闭、回落）

━━━ 六、本地门禁 ━━━
  • 相关套件：pytest tests/unit/test_isolation*.py（新增）+ digestion（shadow/sandbox/gate）+ subagent + guardrails 邻接回归
  • kwarg 扫描两条（--path agent 与 --path tests）；mypy 新增模块；importlinter
  • 真实提交场景验证 pre-commit；跑完门禁后 git status 检查产物漂移并还原
  • 容器相关用例：无 Docker 环境须 skip 并**如实标注**（不得伪通过）

━━━ 七、上游已知坑 ━━━
1. 无 Docker 的 CI 环境 → 容器用例必须 gate（`skipif`）并注明；断言不得依赖容器存在
2. 子进程隔离在 Windows 与 Linux 语义不同（S5-01 教训）→ 平台断言要 gate 或改为跨平台
3. record-and-replay 用例要防止"误真写"：用只读挂载 + 临时目录 + 事后校验真实目录未被改动
4. 隔离类用例耗时长 → 加 @pytest.mark.timeout 并控制规模；必要时标 `slow`
5. 演示脚本产出的运行时文件不入库（gitignore）

━━━ 八、回报格式 ━━━
见批次总表 §三"统一回报格式"；额外须附：隔离探针原始输出（容器 vs 子进程对比表）、未双写证据、回退验证记录、等级如实标注说明
```

---

## 三、开工自查清单

- [ ] 已读任务书；确认等级模型与"不保证边界"清单
- [ ] `--base master`、id=`s803`；worktree 内工作
- [ ] 容器路径与子进程路径均已实现（或如实降级）
- [ ] 探针对两种等级分别出机器可读证据
- [ ] 副作用未双写（断言）
- [ ] real_takeover 默认关闭 + 有预算 + 失败回落
- [ ] 端到端演示可复现
- [ ] 无 Docker 环境的用例已 gate 且如实标注
- [ ] 双远端同点推送
