# START-S7-01 开关中心（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S7-01_开关中心.md`](TASK-S7-01_开关中心.md)（★ 必须先完整阅读）
> 批次与通用约定 → [`PARALLEL_S7批次总表.md`](PARALLEL_S7批次总表.md)｜基线：`master` / `531515e0`｜预估：5–8 人日

---

## 一、开工前确认

1. `--base master`；worktree id 用 **`s701`**（须 `s<数字>` 形式）。
2. 依赖 S6-01（治理面板）、S4-01（Actor 矩阵/审批面安全）、S2-02（链式审计）、S2-03（events）——**均已结案**，无待裁定项。
3. 本任务跨前端（`yunshu-ui`）与后端（`agent/settings/` 新包），落点与 S7 其他任务**不重叠**，可并行。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S7-01 — 开关中心：把全部布尔开关与关键阈值呈现在 UI（含三级权限与审计）
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S7-01_开关中心.md（★ 必须先完整阅读）
【批次总表】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\PARALLEL_S7批次总表.md（worktree/门禁/硬约束/回报格式）
【上游设计文档】C:\Users\Administrator\Desktop\设计思路\CloudPivot_v7.2_final_合并归档(智谱审核版).md
                （相关章节：§7 UI 五坑 / §7.0 Actor 矩阵 / §3.5 审计 / §11.2 性能预算 / P7.2-24 审计平权）
【预估】5–8 人日
【状态】依赖 S6-01/S4-01/S2-02/S2-03 全部结案；无待裁定项

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s701 --base master
此后所有 git 操作在 .worktrees/s701/ 内；主工作区禁令与提交流程见批次总表 §三。
双远端推送：git push origin master && git push gitee master（保持同点）

━━━ 二、任务目标（摘要）━━━
1. **开关注册表（唯一事实源）**：`agent/settings/registry.py` 的 `SettingSpec`（key/category/type/default/env_name/config_path/
   risk(A|B|C)/needs_restart/description/validator/owner_module）
2. **完整性机械提取**：`scripts/scan_settings.py` 从代码提取全部开关读取点（`_env_flag` / `os.environ.get` / `config.get`），
   与既有 `agent/monitoring/observability_config.py` 校验注册表**合并**；**机械提取项零缺口（缺口即测试失败）**
3. **生效来源与覆盖层**：`data/ui_settings.json`（gitignore），优先级 **env > ui_override > config > default**；
   `resolve(key) -> {value, source, shadowed_by}` 如实反映"被谁覆盖"
4. **三级风险权限**：
   A 可直接切（可观测采样/日志级别等低频风险）
   B 需二次认证 + 双人确认（自愈自动执行、熔断回滚、关沙箱、审批豁免、成本刹车阈值）
   C 只读脱敏（API_KEY / SMTP_PASSWORD / *_URL / 绝对路径）——**永不返回明文**
5. **后端 API**：`GET /api/cp/settings`、`POST /api/cp/settings/<key>`、`POST /api/cp/settings/<key>/reset`；
   全部 `@require_token` + §7.0 矩阵（**改开关 = human 专属**，auto/sub_agent 一律拒）
6. **审计与回执**：每次变更 `audit.record(action="settings.change", payload={old,new,source})` + `policy.decision` + 事件；
   回执标注生效方式（`hot` / `needs_restart` / `next_task`）
7. **前端**：`yunshu-ui/src/pages/hub/governance/` 内新增「开关中心」（复用现成 `SwitchField.tsx`）：
   分类折叠 + 搜索 + 风险徽章 + **生效来源标签** + 说明 + 需重启标记 + **被 env 锁定时置灰并注明原因** + C 级掩码显示
8. **改动前二次确认（B 级）**：展示影响面 / 回滚方式 / 生效方式后再提交

━━━ 三、已就绪前置：勿重复实现 ━━━
  • 配置校验注册表：agent/monitoring/observability_config.py（**已有 path/范围/校验/说明，直接当 UI 元数据源**）
  • 治理面板与口径纪律：agent/ui_panels/（schema.py 的 `metric()` 唯一上屏出口 + `untraceable_scan()`）+ 前端 hub/governance/
  • 前端组件：yunshu-ui/src/.../SwitchField.tsx（现成开关组件）、既有 apiClient（自动带 token）
  • 审批与身份：S4-01 的 Actor 矩阵（agent/security/）+ `server_auth.require_token` + 二次认证设施
  • 审计与事件：agent/audit/facade.py::audit.record(...)（治理类事件已有镜像规则，勿重复留痕）+ agent/observability/events.py
  ★ 关键：**不要重新手写开关清单**（会漂移）——用机械提取 + 既有注册表合并，并加缺口断言

━━━ 四、本任务特有硬约束 ━━━
1. **不许"UI 改了但不生效"**：被 env 锁定的项必须置灰 + 注明原因；生效来源必须真实
2. 写覆盖层**绝不修改** `.env` / `config.yaml`（用例断言文件未被改动）
3. C 级密钥类：响应体与前端**都不得含明文**（沿用 S4-01 裁定 B 的掩码口径）
4. B 级：无二次认证 + 双人确认不可通过；且不得被"批量提交"绕过
5. 高危开关的**默认值不得被 UI 悄悄改写**（默认值只读展示；改的是覆盖层）
6. 未配置的新开关（有 env_name 但无 config_path）必须在 UI 如实标注"仅支持环境变量"

━━━ 五、验收与交付物 ━━━
交付：1) agent/settings/（registry + resolver + override store）；2) scripts/scan_settings.py；3) 三组 API 路由；
      4) 开关中心页面；5) 覆盖层与优先级解析；6) TASK-S7-01_验收报告.md（含开关清单统计与生效来源证据）；
      7) S7-01_交付结案报告_<日期>.md；8) 更新 00_总览 状态行
验收：逐条对照任务书 §四（含"零缺口提取"“置灰说明”“不改配置文件”“C 级无明文”“审计入链”）

━━━ 六、本地门禁 ━━━
  后端：pytest tests/unit/test_settings*.py（新增）+ ui_panels/security/approval/audit 邻接套件
        kwarg 扫描两条（--path agent 与 --path tests，--min-risk HIGH）
        mypy 新增模块 + 既有阻塞模块；importlinter lint --config .importlinter
  前端：npx tsc -b --noEmit + eslint 零告警 + vitest（新增组件用例）
        npm run build:flask 产物同步到 templates/yunshu.html（**否则页面看不到**）
  通用：真实提交场景验证 pre-commit；跑完门禁后 git status 检查产物漂移并还原

━━━ 七、上游已知坑 ━━━
1. 落盘类用例（覆盖层/注册表）必须**显式传路径或 autouse 会话级隔离**（S3-02/S3-03 两次污染教训）
2. 改开关会改动全局行为 → 用例必须验证"未变更时零影响"（防误伤）
3. 审计写入有镜像规则：治理类事件已入链，**勿在 settings 路径重复留痕**
4. 前端页面新增后必须同步 Flask 构建产物，否则"实现了但看不到"
5. 前端用例避免依赖真实时钟/时区（既有跨午夜归档用例踩过）
6. 密钥类断言要防止"断言失败时把明文打进日志"（用掩码比较）

━━━ 八、回报格式 ━━━
见批次总表 §三"统一回报格式"；额外须附：开关清单统计（按类别/风险级）、"零缺口"提取证据、一张真实生效来源（含被 env 锁定置灰）的证据
```

---

## 三、开工自查清单

- [ ] 已读任务书全文（尤其三级风险分级与生效来源设计）
- [ ] `--base master`、id=`s701`；worktree 内工作
- [ ] 机械提取零缺口（缺口断言会失败）
- [ ] 生效来源真实、被 env 锁定项置灰说明
- [ ] 写覆盖层不改 `.env`/`config.yaml`
- [ ] C 级响应无明文；B 级二次认证 + 双人确认
- [ ] 每次变更入链式审计 + policy.decision，`verify_chain` 仍通过
- [ ] 前端 tsc/eslint/vitest 全绿 + `build:flask` 同步
- [ ] 双远端同点推送
