# START-S9-01 对话编排「答非所问 + 跨轮串台」修复（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S9-01_对话编排答非所问与串台修复.md`](TASK-S9-01_对话编排答非所问与串台修复.md)（★ 必须先完整阅读）
> 输入依据 → [`../真用前置_模型凭证核查_20260913.md`](../真用前置_模型凭证核查_20260913.md)（**§八 D2**：现象、证据、已定位部分）
> 基线：`master`｜预估：3–5 人日｜**优先级：最高**（不修则「真用」无法开始）

---

## 一、开工前确认

1. `--base master`；worktree id 用 **`s901`**。
2. **前置已完成**：模型凭证已修好（DeepSeek 通道可用），`list_directory` 能真实执行 ——
   所以你能**真机复现**本任务的现象，不需要造桩。
3. 本任务**只动编排/响应装配**，**不要动** `agent/skills_mgmt/output_guard.py`（那是 S9-02 的地盘）。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S9-01 — 对话编排「答非所问 + 跨轮串台」修复
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S9-01_对话编排答非所问与串台修复.md（★ 必须先完整阅读）
【输入依据】C:\Users\Administrator\agent\docs\zh\真用前置_模型凭证核查_20260913.md（§八 D2：证据与已定位部分，勿重新推导）
【预估】3–5 人日｜【状态】无待裁定项｜【优先级】最高（真用入口阻塞项）

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s901 --base master
此后所有 git 操作在 .worktrees/s901/ 内；主工作区禁令：✗ git checkout ✗ git reset --hard ✗ git add -A（只 git add <具体文件>）
双远端推送：git push origin master && git push gitee master

━━━ 二、现象（2026-09-13 真机实测，可复现）━━━
  提问「帮我列出当前工作目录下的文件」 → response=真实目录列表 ✅，reasoning 相关 ✅
  提问「2 加 3 等于多少？只回答数字」 → response=**一整篇 `# self_reflection` 技能文档** ❌
                                      reasoning=**上一轮"列文件被截断"的思考** ❌
  判据：换了完全不同的问题，tool_steps 与 reasoning 却与上一轮**逐字相同** ⇒ 全局残留。

━━━ 三、已定位部分（不用重新查）━━━
  ① 串台直接原因：agent/server_routes/routes_chat.py:428-429
       "tool_steps": getattr(Yunshu, '_last_tool_steps', []),
       "reasoning":  getattr(Yunshu, '_last_reasoning', None),
     取自**全局单例实例属性**，未按会话隔离；本轮没写就返回上一轮的值。
     （同文件 342-344 行注释声称已"根治会话串扰"，但只覆盖 session_id，没覆盖这两个属性。）
  ② 这两个属性的写入点共 7 处：lifecycle_manager.py:445-446（初始化）、
     orchestrator.py:2880 / 2979 / 3017 / 3057 / 3440-3441 / 3450-3451。
     ⚠️ 3441/3451 写作 `_result.get("reasoning") or self._last_reasoning` —— **or 保留旧值**，
        很可能是串台的直接注入点；这类写法一律改成显式赋值。
  ③ response 入口：agent/orchestrator/orchestrator.py:429 `def chat(self, user_input, *, session_id=None, session_mgr=None)`
     —— **"为什么返回技能文档"尚未定位，这是本任务主要工作量。**

━━━ 四、要做的事 ━━━
  1. **先固化现象**（不改代码）：加回归用例 —— 连续两次不同提问（不同 session_id），
     断言第二次的 tool_steps/reasoning 不得等于第一次（当前应**失败**，作为回归锚）。
  2. **定位 response 为何是技能文档**：顺 orchestrator.chat() 走
     技能检索/注入 → 上下文装配 → 模型调用 → 响应后处理，给出**代码行级**根因，不接受"可能/大概"。
  3. **修串台**：把 _last_tool_steps/_last_reasoning 改为**按会话隔离**（会话级字典或写入 session_mgr 消息元数据），
     或每轮入口先清空；禁止用 `or` 回退旧值；**保持 /api/chat 响应字段名与语义不变**。
  4. **修答非所问**（按第 2 步根因）。
  5. **回归**：test_orchestrator_*、test_prompt_cache_order.py、test_orchestrator三层路由_e2e.py、
     test_digital_life_comprehensive.py（这些直接读写那两个属性，需同步更新，勿靠兼容层掩盖）。

━━━ 五、验收判据（必须机器可读）━━━
  1. 不串台：连续两次不同提问 → tool_steps 与 reasoning **不相等**；本轮无内容时返回**空**而非上一轮值
  2. 答得对：「2 加 3 等于多少」→ 回答含 `5`；不得返回技能文档正文；不得返回原始工具 JSON
  3. 会话隔离：两个不同 session_id 交替提问，各自 tool_steps/reasoning 互不污染
  4. 工具仍可用：需工具的提问仍真实执行并出现在 tool_steps 中
  5. 无回归：相关套件全过；公开接口字段名与语义不变
  6. 不编造：报告里每个片段都能从日志/响应原样复现

━━━ 六、真机复验命令（照此取证，别只看单测）━━━
  $env:PYTHONIOENCODING='utf-8'
  $tok = (Select-String -Path 'C:\Users\Administrator\agent\.env' -Pattern '^FLASK_API_TOKEN=(.*)$').Matches[0].Groups[1].Value.Trim()
  # 连续两次不同提问，对比 tool_steps / reasoning / response
  foreach ($q in @('帮我列出当前工作目录下的文件','2 加 3 等于多少？只回答数字')) {
    $body = @{ message = $q; session = "s901-$([guid]::NewGuid().ToString('N').Substring(0,8))" } | ConvertTo-Json -Compress
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:5678/api/chat' -Method POST -ContentType 'application/json; charset=utf-8' `
         -Body ([Text.Encoding]::UTF8.GetBytes($body)) -UseBasicParsing -TimeoutSec 150
    $j = $r.Content | ConvertFrom-Json
    "Q=$q"; "  tool_steps=$($j.tool_steps | ConvertTo-Json -Compress -Depth 4)"; "  reasoning=$($j.reasoning)"; "  response=$($j.response.Substring(0,[Math]::Min(200,$j.response.Length)))"
  }
  # 注意：改代码后需重启服务才生效（PID 见 Get-NetTCPConnection -LocalPort 5678）

━━━ 七、本地门禁 ━━━
  • pytest 相关套件 + 邻接回归；覆盖率 ≥80%
  • kwarg 扫描两条（--path agent 与 --path tests，--min-risk HIGH）；mypy 改动模块；lint-imports --config .importlinter
  • 真实提交场景验证 pre-commit；跑完 git status 检查产物漂移并还原
  • 涉及会话/台账的用例必须**显式传路径或 autouse 隔离**（历史两次污染教训）

━━━ 八、上游已知坑 ━━━
  1. 那两个属性被**多个测试直接读写**（test_digital_life_comprehensive / test_prompt_cache_order /
     test_orchestrator_workflow_learning_layer / test_orchestrator三层路由_e2e）⇒ 改存储结构时同步更新用例。
  2. orchestrator.py 内 7 处写入点分散在 2880/2979/3017/3057/3440/3450 附近，
     改一处漏一处会留同类 bug ⇒ 建议**收敛成单一 setter**。
  3. 与 S9-02（输出护栏 D1，改 agent/skills_mgmt/output_guard.py）相邻但不重叠 —— **不要顺手动对方文件**，避免冲突。
  4. 上一轮遗留"会话上下文 454.7% 超限"（D4，归 S9-03）会**加剧**答非所问；
     若定位中发现根因其实是上下文超限，请在报告中**明确指认并转 S9-03**，不要在本任务里顺手改预算逻辑。
  5. 复验时"回复是技能文档"与"回复被护栏拦"是两回事：**护栏那条（S9-02）不在你范围内**，
     若你看到"（输出校验未通过，已拦截）"，那是 D1，按 S9-02 处理，不要在本任务修。

━━━ 九、回报格式 ━━━
交付物 / 验收逐条（含证据命令与原始输出）/ 根因（代码行级）/ 质量证据（套件、门禁）/ 遗留（带归属）/ 总览更新 / 双远端 SHA
```

---

## 三、开工自查清单

- [ ] 已读任务书与 `真用前置_模型凭证核查_20260913.md` §八 D2
- [ ] `--base master`、id=`s901`；worktree 内工作
- [ ] **先固化现象**（回归用例先失败）再改代码
- [ ] `response` 为何是技能文档 → 给出**代码行级**根因
- [ ] `_last_tool_steps` / `_last_reasoning` 按会话隔离；无 `or` 回退旧值
- [ ] 未动 `agent/skills_mgmt/output_guard.py`（S9-02 地盘）
- [ ] 真机复验两次不同提问，字段确实不同（附原始输出）
- [ ] 相关套件全过；门禁四条齐全
- [ ] 双远端同点推送
