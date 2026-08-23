# simulate_ci_failure_notify.py 修复对比报告

## 元信息

| 项 | 值 |
|---|---|
| 生成时间 | 2026-08-23 23:25:00 |
| 旧版 | `daaa3f6e (scripts/simulate_ci_failure_notify.py)` |
| 修复版 | `HEAD (scripts/simulate_ci_failure_notify.py)` |
| 旧版行数 | 391 |
| 新版行数 | 402 |

## 背景

旧版 `daaa3f6e`(原 da309690) 提交时**缺失退出码逻辑**: `boundary_checks` 无返回值,
main 中无 `blocked` 标记与 `sys.exit(1)`, 导致 pre-commit 的 WORKFLOW_SIM 段调用
`python simulate_ci_failure_notify.py --all` 永远返回 0, 拦截形同虚设 —— 属无痕回滚风险
(修复存在但未入库, 一旦工作区被还原即静默回归)。

## 关键功能差异

| 功能 | 旧版 (daaa3f6e) | 修复版 (HEAD) |
|---|---|---|
| `boundary_checks` 返回值 | 无(返回 None) | 返回 (通过数, 总数) |
| yml 预检发现失效 action | 仅打印 [BLOCK], 不阻断 | 打印 [BLOCK] 且 `blocked=True` |
| 边界检查失败 | 仅打印 FAIL | `blocked=True` |
| 最终退出码 | 恒 0 | BLOCK/边界失败 → exit 1, 否则 exit 0 |
| pre-commit 拦截效果 | 无效(永远放行) | 有效(失败阻止提交) |

## 功能标记提取

### 旧版标记
```
def job_notify_should_run(workflow_run: Optional[Dict], simulate_failure: Optional[bool]) -> bool:
def step_dingtalk_should_run(webhook: str) -> bool:
def step_issue_should_run(workflow_run: Optional[Dict]) -> bool:
def job_recover_should_run(workflow_run: Optional[Dict]) -> bool:
def detect_recovery(history: List[Dict], current_run_id: Optional[int]) -> tuple:
def step_recover_dingtalk_should_run(recovered: bool, webhook: str) -> bool:
def step_recover_note_should_run(recovered: bool, webhook: str) -> bool:
def do_prep(workflow_run: Optional[Dict]) -> Dict[str, str]:
def build_notify_cmd(webhook: str, status: str, prep: Dict[str, str],
def run_cmd(cmd: List[str]) -> None:
def run_notify_job(scenario: Dict[str, Any], out: List[str]) -> None:
def run_recover_job(scenario: Dict[str, Any], out: List[str]) -> None:
def boundary_checks(out: List[str]) -> None:
def make_scenarios(webhook_override: str = "") -> Dict[str, Dict[str, Any]]:
def main() -> None:
def run_one(name: str, out: List[str]) -> None:
boundary_checks([])
```

### 修复版标记
```
def job_notify_should_run(workflow_run: Optional[Dict], simulate_failure: Optional[bool]) -> bool:
def step_dingtalk_should_run(webhook: str) -> bool:
def step_issue_should_run(workflow_run: Optional[Dict]) -> bool:
def job_recover_should_run(workflow_run: Optional[Dict]) -> bool:
def detect_recovery(history: List[Dict], current_run_id: Optional[int]) -> tuple:
def step_recover_dingtalk_should_run(recovered: bool, webhook: str) -> bool:
def step_recover_note_should_run(recovered: bool, webhook: str) -> bool:
def do_prep(workflow_run: Optional[Dict]) -> Dict[str, str]:
def build_notify_cmd(webhook: str, status: str, prep: Dict[str, str],
def run_cmd(cmd: List[str]) -> None:
def run_notify_job(scenario: Dict[str, Any], out: List[str]) -> None:
def run_recover_job(scenario: Dict[str, Any], out: List[str]) -> None:
def boundary_checks() -> tuple:
return ok, len(checks)
def make_scenarios(webhook_override: str = "") -> Dict[str, Dict[str, Any]]:
def main() -> None:
blocked = False
blocked = True
def run_one(name: str, out: List[str]) -> None:
ok, total = boundary_checks()
blocked = True
if blocked:
sys.exit(1)
```

## 逐行差异

```diff
@@ 旧版行 244-387 | 新版行 244-398 @@
- 244| def boundary_checks(out: List[str]) -> None:
- 245|     """边界情况清单（B1-B8）"""
- 246|     checks = [
- 247|         ("B1  webhook 空时 notify job 不中断", lambda: True,
- 248|          "钉钉 step 跳过但 Issue/邮件 step 继续，job 仍 success"),
- 249|         ("B2  手动触发 prep 不崩溃", lambda: do_prep(None)["workflow_name"] == "手动触发(workflow_dispatch)",
- 250|          "workflow_run null 时兜底值生效"),
- 251|         ("B3  手动触发不误建 Issue", lambda: not step_issue_should_run(None),
- 252|          "head_branch null != 'master'"),
- 253|         ("B4  手动触发 recover job 不运行", lambda: not job_recover_should_run(None),
- 254|          "workflow_run null → if false"),
- 255|         ("B5  无历史 run 不误报恢复", lambda: detect_recovery([], 1)[0] is False,
- 256|          "recovered='false'，恢复通知两 step 均跳过"),
- 257|         ("B6  webhook 空 + recovered 有提示", lambda: step_recover_note_should_run(True, ""),
- 258|          "恢复说明 step 提示配置，不静默"),
- 259|         ("B7  空 webhook 不会触发调用", lambda: not step_dingtalk_should_run(""),
- 260|          "step if 先行，--webhook \"\" 不会被执行"),
- 261|         ("B8  布尔判定兼容（真布尔/字符串）", lambda: bool(True) and (not bool(None)),
- 262|          "boolean input 为真布尔时 job if 成立"),
- 263|     ]
- 264|     print("##[group]边界情况检查（B1-B8）")
- 265|     ok = 0
- 266|     for name, fn, note in checks:
- 267|         passed = bool(fn())
- 268|         ok += int(passed)
- 269|         print(f"  [{'PASS' if passed else 'FAIL'}] {name} — {note}")
- 270|     print(f"  边界检查通过: {ok}/{len(checks)}")
- 271|     print("##[endgroup]")
- 272| 
- 273| 
- 274| # ════════════════════════════════════════════════════════════════════
- 275| # 场景定义
- 276| # ════════════════════════════════════════════════════════════════════
- 277| 
- 278| def make_scenarios(webhook_override: str = "") -> Dict[str, Dict[str, Any]]:
- 279|     return {
- 280|         "wf_failure": {
- 281|             "trigger": "workflow_run (conclusion=failure)",
- 282|             "workflow_run": {"id": 11, "name": "云枢系统测试流程", "conclusion": "failure",
- 283|                              "head_branch": "master", "head_sha": "abc1234def5678",
- 284|                              "html_url": f"https://github.com/{REPO}/actions/runs/11",
- 285|                              "actor": {"login": "nzt47"}},
- 286|             "simulate_failure": None,
- 287|             "webhook": webhook_override,
- 288|             "history": [],
- 289|         },
- 290|         "manual_simulate": {
- 291|             "trigger": "workflow_dispatch (simulate_failure=true)",
- 292|             "workflow_run": None,
- 293|             "simulate_failure": True,
- 294|             "webhook": webhook_override,
- 295|             "history": [],
- 296|         },
- 297|         "manual_with_webhook": {
- 298|             "trigger": "workflow_dispatch (simulate_failure=true) + webhook 已配置",
- 299|             "workflow_run": None,
- 300|             "simulate_failure": True,
- 301|             "webhook": webhook_override or "https://oapi.dingtalk.com/robot/send?access_token=DEMO",
- 302|             "history": [],
- 303|         },
- 304|         "docker_recover": {
- 305|             "trigger": "workflow_run (kwarg-docker-scan success，上次失败)",
- 306|             "workflow_run": {"id": 22, "name": "关键字参数冲突扫描 (Docker)", "conclusion": "success",
- 307|                              "head_branch": "master", "head_sha": "fedcba9876543210",
- 308|                              "html_url": f"https://github.com/{REPO}/actions/runs/22",
- 309|                              "actor": {"login": "nzt47"}},
- 310|             "simulate_failure": None,
- 311|             "webhook": webhook_override,
- 312|             "history": [{"id": 21, "conclusion": "failure",
- 313|                          "head_sha": "deadbeef", "html_url": f"https://github.com/{REPO}/actions/runs/21"}],
- 314|         },
- 315|         "docker_recover_webhook": {
- 316|             "trigger": "workflow_run (kwarg-docker-scan success) + webhook 已配置",
- 317|             "workflow_run": {"id": 22, "name": "关键字参数冲突扫描 (Docker)", "conclusion": "success",
- 318|                              "head_branch": "master", "head_sha": "fedcba9876543210",
- 319|                              "html_url": f"https://github.com/{REPO}/actions/runs/22",
- 320|                              "actor": {"login": "nzt47"}},
- 321|             "simulate_failure": None,
- 322|             "webhook": webhook_override or "https://oapi.dingtalk.com/robot/send?access_token=DEMO",
- 323|             "history": [{"id": 21, "conclusion": "failure",
- 324|                          "head_sha": "deadbeef", "html_url": f"https://github.com/{REPO}/actions/runs/21"}],
- 325|         },
- 326|         "docker_success_no_change": {
- 327|             "trigger": "workflow_run (kwarg-docker-scan success，上次也 success)",
- 328|             "workflow_run": {"id": 24, "name": "关键字参数冲突扫描 (Docker)", "conclusion": "success",
- 329|                              "head_branch": "master", "head_sha": "11112222",
- 330|                              "html_url": f"https://github.com/{REPO}/actions/runs/24",
- 331|                              "actor": {"login": "nzt47"}},
- 332|             "simulate_failure": None,
- 333|             "webhook": webhook_override,
- 334|             "history": [{"id": 23, "conclusion": "success", "head_sha": "aaaabbbb",
- 335|                          "html_url": f"https://github.com/{REPO}/actions/runs/23"}],
- 336|         },
- 337|     }
- 338| 
- 339| 
- 340| def main() -> None:
- 341|     parser = argparse.ArgumentParser(description="本地模拟 ci-failure-notify.yml 判定与通知链路")
- 342|     parser.add_argument("--scenario", choices=["wf_failure", "manual_simulate", "manual_with_webhook",
- 343|                                                "docker_recover", "docker_recover_webhook",
- 344|                                                "docker_success_no_change"])
- 345|     parser.add_argument("--webhook", default="", help="模拟 DINGTALK_WEBHOOK 值（默认空=未配置）")
- 346|     parser.add_argument("--live", action="store_true", help="真实调用 observability_dingtalk_notify.py")
- 347|     parser.add_argument("--all", action="store_true", help="运行全部场景 + 边界检查")
- 348|     args = parser.parse_args()
- 349| 
- 350|     if not args.scenario and not args.all:
- 351|         parser.error("必须指定 --scenario 或 --all")
- 352| 
- 353|     # 预检：yml 无残留失效 action 引用（仅匹配代码行 `uses:`，排除注释提及）
- 354|     if os.path.exists(WORKFLOW_FILE):
- 355|         content = open(WORKFLOW_FILE, encoding="utf-8").read()
- 356|         # 过滤注释行（# 开头）后查找 uses: 引用
- 357|         code_lines = [ln for ln in content.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
- 358|         if any("uses: visiblelabs/dingtalk-action" in ln for ln in code_lines):
- 359|             print(f"[BLOCK] {WORKFLOW_FILE} 仍含代码级 visiblelabs/dingtalk-action 引用，修复不完整！")
- 360|         else:
- 361|             print(f"[OK] {WORKFLOW_FILE} 无代码级 visiblelabs/dingtalk-action 引用（仅注释提及）")
- 362|     else:
- 363|         print(f"[WARN] 未找到 {WORKFLOW_FILE}，仅验证判定逻辑")
- 364| 
- 365|     scenarios = make_scenarios(args.webhook)
- 366| 
- 367|     def run_one(name: str, out: List[str]) -> None:
- 368|         print(f"\n{'=' * 72}")
- 369|         print(f"场景: {name}  ({scenarios[name]['trigger']})")
- 370|         print(f"      DINGTALK_WEBHOOK = {scenarios[name]['webhook'] or '(未配置)'}")
- 371|         print("=" * 72)
- 372|         run_notify_job(scenarios[name], out)
- 373|         run_recover_job(scenarios[name], out)
- 374|         print("\n".join(out))
- 375| 
- 376|     if args.all:
- 377|         out: List[str] = []
- 378|         for name in scenarios:
- 379|             run_one(name, [])
- 380|             out.clear()
- 381|         print("\n" + "=" * 72)
- 382|         print("边界情况检查（所有场景）")
- 383|         print("=" * 72)
- 384|         boundary_checks([])
- 385|     else:
- 386|         out: List[str] = []
- 387|         run_one(args.scenario, out)
  ---- 分隔 ----
+ 244| def boundary_checks() -> tuple:
+ 245|     """边界情况清单（B1-B8），返回 (通过数, 总数)"""
+ 246|     checks = [
+ 247|         ("B1  webhook 空时 notify job 不中断", lambda: True,
+ 248|          "钉钉 step 跳过但 Issue/邮件 step 继续，job 仍 success"),
+ 249|         ("B2  手动触发 prep 不崩溃", lambda: do_prep(None)["workflow_name"] == "手动触发(workflow_dispatch)",
+ 250|          "workflow_run null 时兜底值生效"),
+ 251|         ("B3  手动触发不误建 Issue", lambda: not step_issue_should_run(None),
+ 252|          "head_branch null != 'master'"),
+ 253|         ("B4  手动触发 recover job 不运行", lambda: not job_recover_should_run(None),
+ 254|          "workflow_run null → if false"),
+ 255|         ("B5  无历史 run 不误报恢复", lambda: detect_recovery([], 1)[0] is False,
+ 256|          "recovered='false'，恢复通知两 step 均跳过"),
+ 257|         ("B6  webhook 空 + recovered 有提示", lambda: step_recover_note_should_run(True, ""),
+ 258|          "恢复说明 step 提示配置，不静默"),
+ 259|         ("B7  空 webhook 不会触发调用", lambda: not step_dingtalk_should_run(""),
+ 260|          "step if 先行，--webhook \"\" 不会被执行"),
+ 261|         ("B8  布尔判定兼容（真布尔/字符串）", lambda: bool(True) and (not bool(None)),
+ 262|          "boolean input 为真布尔时 job if 成立"),
+ 263|     ]
+ 264|     print("##[group]边界情况检查（B1-B8）")
+ 265|     ok = 0
+ 266|     for name, fn, note in checks:
+ 267|         passed = bool(fn())
+ 268|         ok += int(passed)
+ 269|         print(f"  [{'PASS' if passed else 'FAIL'}] {name} — {note}")
+ 270|     print(f"  边界检查通过: {ok}/{len(checks)}")
+ 271|     print("##[endgroup]")
+ 272|     return ok, len(checks)
+ 273| 
+ 274| 
+ 275| # ════════════════════════════════════════════════════════════════════
+ 276| # 场景定义
+ 277| # ════════════════════════════════════════════════════════════════════
+ 278| 
+ 279| def make_scenarios(webhook_override: str = "") -> Dict[str, Dict[str, Any]]:
+ 280|     return {
+ 281|         "wf_failure": {
+ 282|             "trigger": "workflow_run (conclusion=failure)",
+ 283|             "workflow_run": {"id": 11, "name": "云枢系统测试流程", "conclusion": "failure",
+ 284|                              "head_branch": "master", "head_sha": "abc1234def5678",
+ 285|                              "html_url": f"https://github.com/{REPO}/actions/runs/11",
+ 286|                              "actor": {"login": "nzt47"}},
+ 287|             "simulate_failure": None,
+ 288|             "webhook": webhook_override,
+ 289|             "history": [],
+ 290|         },
+ 291|         "manual_simulate": {
+ 292|             "trigger": "workflow_dispatch (simulate_failure=true)",
+ 293|             "workflow_run": None,
+ 294|             "simulate_failure": True,
+ 295|             "webhook": webhook_override,
+ 296|             "history": [],
+ 297|         },
+ 298|         "manual_with_webhook": {
+ 299|             "trigger": "workflow_dispatch (simulate_failure=true) + webhook 已配置",
+ 300|             "workflow_run": None,
+ 301|             "simulate_failure": True,
+ 302|             "webhook": webhook_override or "https://oapi.dingtalk.com/robot/send?access_token=DEMO",
+ 303|             "history": [],
+ 304|         },
+ 305|         "docker_recover": {
+ 306|             "trigger": "workflow_run (kwarg-docker-scan success，上次失败)",
+ 307|             "workflow_run": {"id": 22, "name": "关键字参数冲突扫描 (Docker)", "conclusion": "success",
+ 308|                              "head_branch": "master", "head_sha": "fedcba9876543210",
+ 309|                              "html_url": f"https://github.com/{REPO}/actions/runs/22",
+ 310|                              "actor": {"login": "nzt47"}},
+ 311|             "simulate_failure": None,
+ 312|             "webhook": webhook_override,
+ 313|             "history": [{"id": 21, "conclusion": "failure",
+ 314|                          "head_sha": "deadbeef", "html_url": f"https://github.com/{REPO}/actions/runs/21"}],
+ 315|         },
+ 316|         "docker_recover_webhook": {
+ 317|             "trigger": "workflow_run (kwarg-docker-scan success) + webhook 已配置",
+ 318|             "workflow_run": {"id": 22, "name": "关键字参数冲突扫描 (Docker)", "conclusion": "success",
+ 319|                              "head_branch": "master", "head_sha": "fedcba9876543210",
+ 320|                              "html_url": f"https://github.com/{REPO}/actions/runs/22",
+ 321|                              "actor": {"login": "nzt47"}},
+ 322|             "simulate_failure": None,
+ 323|             "webhook": webhook_override or "https://oapi.dingtalk.com/robot/send?access_token=DEMO",
+ 324|             "history": [{"id": 21, "conclusion": "failure",
+ 325|                          "head_sha": "deadbeef", "html_url": f"https://github.com/{REPO}/actions/runs/21"}],
+ 326|         },
+ 327|         "docker_success_no_change": {
+ 328|             "trigger": "workflow_run (kwarg-docker-scan success，上次也 success)",
+ 329|             "workflow_run": {"id": 24, "name": "关键字参数冲突扫描 (Docker)", "conclusion": "success",
+ 330|                              "head_branch": "master", "head_sha": "11112222",
+ 331|                              "html_url": f"https://github.com/{REPO}/actions/runs/24",
+ 332|                              "actor": {"login": "nzt47"}},
+ 333|             "simulate_failure": None,
+ 334|             "webhook": webhook_override,
+ 335|             "history": [{"id": 23, "conclusion": "success", "head_sha": "aaaabbbb",
+ 336|                          "html_url": f"https://github.com/{REPO}/actions/runs/23"}],
+ 337|         },
+ 338|     }
+ 339| 
+ 340| 
+ 341| def main() -> None:
+ 342|     parser = argparse.ArgumentParser(description="本地模拟 ci-failure-notify.yml 判定与通知链路")
+ 343|     parser.add_argument("--scenario", choices=["wf_failure", "manual_simulate", "manual_with_webhook",
+ 344|                                                "docker_recover", "docker_recover_webhook",
+ 345|                                                "docker_success_no_change"])
+ 346|     parser.add_argument("--webhook", default="", help="模拟 DINGTALK_WEBHOOK 值（默认空=未配置）")
+ 347|     parser.add_argument("--live", action="store_true", help="真实调用 observability_dingtalk_notify.py")
+ 348|     parser.add_argument("--all", action="store_true", help="运行全部场景 + 边界检查")
+ 349|     args = parser.parse_args()
+ 350| 
+ 351|     if not args.scenario and not args.all:
+ 352|         parser.error("必须指定 --scenario 或 --all")
+ 353| 
+ 354|     # 预检：yml 无残留失效 action 引用（仅匹配代码行 `uses:`，排除注释提及）
+ 355|     blocked = False
+ 356|     if os.path.exists(WORKFLOW_FILE):
+ 357|         content = open(WORKFLOW_FILE, encoding="utf-8").read()
+ 358|         # 过滤注释行（# 开头）后查找 uses: 引用
+ 359|         code_lines = [ln for ln in content.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
+ 360|         if any("uses: visiblelabs/dingtalk-action" in ln for ln in code_lines):
+ 361|             print(f"[BLOCK] {WORKFLOW_FILE} 仍含代码级 visiblelabs/dingtalk-action 引用，修复不完整！")
+ 362|             blocked = True
+ 363|         else:
+ 364|             print(f"[OK] {WORKFLOW_FILE} 无代码级 visiblelabs/dingtalk-action 引用（仅注释提及）")
+ 365|     else:
+ 366|         print(f"[WARN] 未找到 {WORKFLOW_FILE}，仅验证判定逻辑")
+ 367| 
+ 368|     scenarios = make_scenarios(args.webhook)
+ 369| 
+ 370|     def run_one(name: str, out: List[str]) -> None:
+ 371|         print(f"\n{'=' * 72}")
+ 372|         print(f"场景: {name}  ({scenarios[name]['trigger']})")
+ 373|         print(f"      DINGTALK_WEBHOOK = {scenarios[name]['webhook'] or '(未配置)'}")
+ 374|         print("=" * 72)
+ 375|         run_notify_job(scenarios[name], out)
+ 376|         run_recover_job(scenarios[name], out)
+ 377|         print("\n".join(out))
+ 378| 
+ 379|     if args.all:
+ 380|         out: List[str] = []
+ 381|         for name in scenarios:
+ 382|             run_one(name, [])
+ 383|             out.clear()
+ 384|         print("\n" + "=" * 72)
+ 385|         print("边界情况检查（所有场景）")
+ 386|         print("=" * 72)
+ 387|         ok, total = boundary_checks()
+ 388|         if ok < total:
+ 389|             blocked = True
+ 390|     else:
+ 391|         out: List[str] = []
+ 392|         run_one(args.scenario, out)
+ 393| 
+ 394|     # 退出码语义：--all 时 BLOCK/边界失败 → exit 1（供 pre-commit hook 阻塞）
+ 395|     if blocked:
+ 396|         print("\n[RESULT] 校验未通过 → exit 1（提交应被阻止）")
+ 397|         sys.exit(1)
+ 398|     print("\n[RESULT] 校验通过 → exit 0")
```

## 修复验证

- 构造含 `visiblelabs/dingtalk-action@v1` 的临时 yml → 预检 [BLOCK] → exit 1
- 真实 `git commit` 被 hook 拦截: `[pre-commit][ERROR] 工作流模拟校验未通过, 提交被阻止`
- 正常 yml → 6 场景判定符合预期, 边界检查 8/8 PASS → exit 0

## 结论

退出码逻辑缺失会让本地预检静默失效。已通过 `c4384355` 固化入库,
但 `verify_core_invariants.py` 静态模式检查无法覆盖此类函数级缺失,
需靠人工对比或本文档追溯。后续改动 simulate 脚本时务必保持 `--all` 的退出码语义(失败必须 exit 1)。
