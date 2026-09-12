# TASK-S4-03 混沌演练实测记录（v7.2 §11.10）

- 运行时刻：2026-09-12T07:14:38+08:00
- 结果：**4/4 通过**
- 命令：`python scripts/chaos_s4_03_drill.py`
- 环境：全部落盘于临时目录（`tempfile.mkdtemp()`），退出即弃；**未触碰主工作区数据与运行中服务**

## 覆盖对照（§11.10 季度清单）

| 清单项 | 本任务覆盖 | 说明 |
|---|---|---|
| kill -9 主进程 | ✅ D1 | 杀的是本脚本 spawn 的 Watchdog 持有子进程 |
| 删最新快照 | ✅ D3 | 整包台账删包 → 回滚被拒 + L4 |
| 向审计链注入一条篡改 | ✅ D2 | 真 UPDATE SQLite → 链校验定位注入点 |
| Saga 补偿失败 | ✅ D4 | §4.6 失败路径（本任务自有项） |
| 断网 10 分钟 | ❌ 未覆盖 | 属季度清单其余项，不在本任务范围 |
| 磁盘写满 | ❌ 未覆盖 | 同上 |
| 上游返回畸形 JSON | ❌ 未覆盖 | 同上 |
| kill Watchdog 本身（验证互 watch） | ❌ 未覆盖 | 互 watch 属主进程/Watchdog 双侧实现，本任务只做单机唯一性（P7.2-16） |

## 逐项记录

### D1 kill -9 主进程（Watchdog 持有者）→ 单机唯一性 + 杀伤后恢复

- **故障注入**：kill -9 持有 watchdog.lock 的子进程（模拟主进程崩溃）
- **结论**：✅ 通过

| 阶段 | 动作 | 明细 |
|---|---|---|
| 2026-09-12T07:14:38+08:00 | spawn | `{"pid": 7052, "cmd": "python -c <child watchdog holder>"}` |
| 2026-09-12T07:14:38+08:00 | chaos | `{"action": "kill -9", "pid": 7052}` |
| 2026-09-12T07:14:38+08:00 | killed | `{"pid": 7052, "returncode": 1}` |
| 2026-09-12T07:14:39+08:00 | cleanup | `{"lock_released": true}` |

**期望 vs 实测**

| # | 期望 | 结果 | 证据 |
|---|---|---|---|
| 1 | 子进程成功持有单例锁 | ✅ | `{"lock_path": "C:\\Windows\\TEMP\\s403-chaos-3luka7kr\\d1\\watchdog.lock", "holder": {}}` |
| 2 | 第二个 Watchdog 实例被拒（分裂脑防护） | ✅ | `{"exception": "SplitBrainError", "holder": {"pid": 7052, "host": "DESKTOP-CN00D5I", "role": "watchdog", "started_at": 1789168478.7046254, "started_iso": "2026-09-12T07:14:38.704625", "backend": "single_host_lockfile", "note": ""}, "lock_path": "C:\\Windows\\TEMP\\s403-chaos-3luka7kr\\d1\\watchdog.lock"}` |

**恢复验证**

| # | 恢复项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 杀伤后新实例可重获单例锁 | ✅ | `{"held": true, "holder": {"pid": 20952, "host": "DESKTOP-CN00D5I", "role": "watchdog", "started_at": 1789168479.227245, "started_iso": "2026-09-12T07:14:39.227245", "backend": "single_host_lockfile", "note": ""}}` |
| 2 | 杀伤后锁文件被判为陈旧（可安全清理） | ✅ | `{"lockfile_holder": {"pid": 20952, "host": "DESKTOP-CN00D5I", "role": "watchdog", "started_at": 1789168479.227245, "started_iso": "2026-09-12T07:14:39.227245", "backend": "single_host_lockfile", "note": ""}, "note": "字节 0 哨兵 + 定长身份槽；OS 锁随进程消亡释放"}` |

### D2 向审计链注入篡改 → 链式校验定位注入点（两种注入的影响范围分别验证）

- **故障注入**：直接 UPDATE 审计 SQLite（绕过写入 API）：D2a 改 payload；D2b 改 payload_hash
- **结论**：✅ 通过

| 阶段 | 动作 | 明细 |
|---|---|---|
| 2026-09-12T07:14:39+08:00 | append | `{"entries": 8, "db": "C:\\Windows\\TEMP\\s403-chaos-3luka7kr\\d2\\audit.db", "table": "audit_chain"}` |
| 2026-09-12T07:14:39+08:00 | chaos-D2a | `{"action": "UPDATE payload", "seq": 4}` |
| 2026-09-12T07:14:39+08:00 | chaos-D2b | `{"action": "UPDATE payload_hash", "seq": 4}` |
| 2026-09-12T07:14:39+08:00 | cleanup | `{"db_removed_with_tmpdir": true}` |

**期望 vs 实测**

| # | 期望 | 结果 | 证据 |
|---|---|---|---|
| 1 | 注入前链完整（基线可信） | ✅ | `{"checked": 8, "summary": "OK — 链完整，已校验 8 条（seq 1..8，链头 self_hash=428edf3879b1c0ac…）"}` |
| 2 | D2a 篡改确实落到库中（影响行数=1） | ✅ | `{"rowcount": 1, "seq": 4}` |
| 3 | D2a 篡改被检出（链不再完整） | ✅ | `{"summary": "TAMPERED — 首个异常 seq=4（payload_hash 重算不一致（载荷/元数据被篡改））: payload_hash 重算=dcad4a7d242b36fc… ≠ 存储=31f9b38cd022f0fa…（载荷或元数据被改）；异常共 1 处（注入点之后因哈希前向传播全部失败）"}` |
| 4 | D2a 首个异常位置 == 注入点 | ✅ | `{"first_bad_seq": 4, "expected": 4}` |
| 5 | D2a 影响范围 = 恰好注入点 1 条（payload 只绑定本条） | ✅ | `{"bad_seqs": [4], "reason": "payload_hash_mismatch"}` |
| 6 | D2b 篡改确实落到库中（影响行数=1） | ✅ | `{"rowcount": 1, "seq": 4, "field": "payload_hash"}` |
| 7 | D2b 首个异常位置 == 注入点 | ✅ | `{"first_bad_seq": 4, "expected": 4}` |
| 8 | D2b 影响范围 = 注入点及其后全部（哈希前向传播） | ✅ | `{"bad_seqs": [4, 5, 6, 7, 8], "reason": "payload_hash_mismatch"}` |

**恢复验证**

| # | 恢复项 | 结果 | 证据 |
|---|---|---|---|
| 1 | D2a 还原 payload 后链恢复完整 | ✅ | `{"checked": 8, "summary": "OK — 链完整，已校验 8 条（seq 1..8，链头 self_hash=428edf3879b1c0ac…）"}` |
| 2 | D2b 还原 payload_hash 后链恢复完整 | ✅ | `{"checked": 8, "summary": "OK — 链完整，已校验 8 条（seq 1..8，链头 self_hash=428edf3879b1c0ac…）"}` |

### D3 删最新快照 → 整包回滚被拒并升级 L4；回退到现存包恢复

- **故障注入**：从整包台账中删除最新 bundle（模拟快照被误删/损坏）
- **结论**：✅ 通过

| 阶段 | 动作 | 明细 |
|---|---|---|
| 2026-09-12T07:14:39+08:00 | snapshot | `{"bundles": 2, "v1": "rb-4075d5bc73c8", "v2": "rb-62e2a4a310cd"}` |
| 2026-09-12T07:14:39+08:00 | chaos | `{"action": "delete newest bundle", "deleted": "sha256:62e2a4a310cde1acd4fd06c950fc5a4268f02a776201daa779616fe93b70c9e9"}` |
| 2026-09-12T07:14:39+08:00 | cleanup | `{"incident_dir": "C:\\Windows\\TEMP\\s403-chaos-3luka7kr\\d3\\incidents"}` |

**期望 vs 实测**

| # | 期望 | 结果 | 证据 |
|---|---|---|---|
| 1 | 最新包确已消失 | ✅ | `{"count": 1}` |
| 2 | 回滚到缺失包被拒 | ✅ | `{"exception": "BundleNotFoundError", "message": "整包不在台账中: 'sha256:62e2a4a310cde1acd4fd06c950fc5a4268f02a776201daa779616fe93b70c9e9'"}` |
| 3 | 部分回滚被拒并触发 L4 | ✅ | `{"missing": ["code", "weights", "data_baseline", "manifest"], "incident_id": "inc-a6df96e785a6"}` |
| 4 | L4 事故卡已落盘 | ✅ | `{"cards": ["inc-a6df96e785a6"], "severities": ["L4"]}` |

**恢复验证**

| # | 恢复项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 回退到现存包可恢复（五组件整包计划） | ✅ | `{"applied": true, "moves": 5, "is_full_bundle": true, "from_bundle": ""}` |

### D4 Saga 补偿失败 → 升级 L4 + 最高告警 + journal 留证

- **故障注入**：补偿回调抛异常（模拟「外部副作用已发生且无法自动撤销」）
- **结论**：✅ 通过

| 阶段 | 动作 | 明细 |
|---|---|---|
| 2026-09-12T07:14:39+08:00 | executed | `{"state": "executed", "three_phase": false}` |
| 2026-09-12T07:14:39+08:00 | chaos | `{"action": "abort → compensate（其中一个补偿抛错）"}` |

**期望 vs 实测**

| # | 期望 | 结果 | 证据 |
|---|---|---|---|
| 1 | 补偿失败被标记（不静默） | ✅ | `{"failed": ["rotate_key"], "compensated": ["write_config"]}` |
| 2 | 升级 L4（escalated=True） | ✅ | `{"state": "escalated", "incident_id": "inc-6aa5c8ef084d"}` |
| 3 | 开出事故卡 | ✅ | `{"incident_id": "inc-6aa5c8ef084d"}` |
| 4 | 事故卡级别为 L4 | ✅ | `{"severities": ["L4"]}` |
| 5 | journal 含 prepare/execute/abort/补偿/escalate | ✅ | `{"steps": ["prepare", "execute", "abort", "compensate:rotate_key", "compensate:write_config", "escalate"]}` |
| 6 | 重复补偿幂等（已成功步骤跳过） | ✅ | `{"skipped": ["write_config"], "failed": ["rotate_key"], "calls": ["boom", "cfg", "boom"]}` |

**恢复验证**

| # | 恢复项 | 结果 | 证据 |
|---|---|---|---|
| 1 | journal 可从磁盘重读并重建状态（重启后仍可处置） | ✅ | `{"rebuilt_state": "escalated", "journal_entries": 8, "incomplete_sagas": []}` |
