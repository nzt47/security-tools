# resolve_pr634_conflict.ps1 — 解决 PR #634（develop → master）合并冲突
#
# 侦查事实（2026-08-14）：
#   - 分叉点 0cf53264；develop 独有 95 提交；冲突文件 4 个：
#     .pre-commit-config.yaml / tests/conftest.py /
#     tests/integration/test_orchestrator三层路由_e2e.py / tests/unit/test_planning_defect_d16.py
#   - 方向：merge origin/master INTO develop → 本侧(ours)=develop、对侧(theirs)=master
#
# 安全约束（守【不易】）：
#   - 禁止 force push（develop 含并行会话已推送提交，历史不可改写）
#   - 禁止 git add -A / git add .（并行会话的 unstaged 改动不属本任务）
#   - 不 rebase 95 提交（重写历史风险高）

$ErrorActionPreference = "Stop"

Write-Host "===[0/6] 前置安全检查===" -ForegroundColor Cyan
$status = git status --short
if ($status) {
    Write-Host "检测到未提交改动（可能含并行会话改动，需人工区分）:" -ForegroundColor Yellow
    Write-Host $status
    Write-Host "继续前请确认：以下改动中不属于本冲突解决范围的，将保持 unstaged 不动。" -ForegroundColor Yellow
}

# 0.5 并行会话阻塞检测（2026-08-14 实测教训）
#    merge 被 TASK-03 的 staged 改动（learning_budget/learning_metrics*/config.yaml/
#    TASK-03 文档）阻断（"local changes would be overwritten"）。
#    ⚠️ 禁止 stash/checkout 这些文件——它们是并行会话活跃工作（守不易）：
#    等待并行会话提交收口后工作区对这些文件变干净，merge 才可安全执行。
$blocker_pattern = "(agent/learning_budget\.py|agent/learning_metrics\.py|agent/learning_metrics_api\.py|config\.yaml|docs/zh/智能体学习机制重构计划/变更说明/TASK-03_)"
$blocked = ($status | Select-String -Pattern $blocker_pattern)
if ($blocked) {
    Write-Host "  [阻塞] 检测到并行会话活跃改动与 master 合并路径重叠:" -ForegroundColor Red
    Write-Host $blocked
    Write-Host "  请等待并行会话完成提交（git status 对这些文件变干净）后重新运行本脚本。" -ForegroundColor Red
    Write-Host "  禁止 stash/checkout/reset 这些文件（守不易：并行会话未提交工作不可触碰）。" -ForegroundColor Red
    exit 3
}

Write-Host "===[1/6] 同步远端===" -ForegroundColor Cyan
git fetch origin master develop

Write-Host "===[2/6] 合并 master 到 develop（--no-commit 不自动提交）===" -ForegroundColor Cyan
git checkout develop
git merge origin/master --no-commit
if ($LASTEXITCODE -gt 1) { throw "merge 异常退出（exit=$LASTEXITCODE）" }

Write-Host "===[3/6] 解决 4 个冲突文件===" -ForegroundColor Cyan

# (1) test_orchestrator三层路由_e2e.py —— 测试须与实现配套，取本侧 develop 版本
#     依据（2026-08-14 实际 merge commit c9026686）：
#       master 版依赖 _interaction_lock，与 develop 的 orchestrator 实现不匹配（9 failed）；
#       取 develop 版后 CI 仍暴露 mock 缺 _interaction_lock（见 docs/pr_conflict_resolution.md
#       第五节 P1 复验项），须在并行会话收口后补 mock 属性。
git checkout --ours -- "tests/integration/test_orchestrator三层路由_e2e.py"
git add "tests/integration/test_orchestrator三层路由_e2e.py"
Write-Host "  [OK] test_orchestrator三层路由_e2e.py -> 取 develop（与实现配套）"

# (2) test_planning_defect_d16.py —— 我方（develop）改动优先
git checkout --ours -- "tests/unit/test_planning_defect_d16.py"
git add "tests/unit/test_planning_defect_d16.py"
Write-Host "  [OK] test_planning_defect_d16.py -> 取 develop"

# (3) .pre-commit-config.yaml —— 两侧 hook 都保留（手动合并）
#     打开文件删除冲突标记，保留两侧条目；或执行下方 Python 三方合并脚本。
Write-Host "  [手动] .pre-commit-config.yaml：删除 <<<<<<< / ======= / >>>>>>> 标记，两侧 hook 条目均保留" -ForegroundColor Yellow

# (4) tests/conftest.py —— 冲突段保留 develop 的"强制复位 NOTSET"（删 master 快照/恢复）
#     依据（三方对比 + 场景推演）：
#       master (+7)：快照 _saved_manager_disable 并在 yield 后恢复原值
#       develop (+35/-1)：三处改动，其中"0b 强制复位 manager.disable=NOTSET"
#     场景推演：master 快照方案在"前序测试已泄漏（进入 fixture 时 disable=50）"时
#               快照到 50 并恢复 50，泄漏永远无法自愈（场景 C 缺陷）；
#               develop 强制复位无条件清除泄漏（覆盖场景 A/B/C）。
#     结论：冲突段保留 develop 强制复位行，删除 master 的 _saved_manager_disable
#           快照/恢复共 7 行；develop 的 _force_reset_intent_rules 恢复 + transformers
#           注释（不冲突部分）由 git 自动保留。
Write-Host "  [手动] tests/conftest.py：冲突段保留 develop 强制复位（logging.root.manager.disable = logging.NOTSET），删除 master 的 _saved_manager_disable 快照/恢复 7 行" -ForegroundColor Yellow

Write-Host "===[4/6] 验证===" -ForegroundColor Cyan
Write-Host "  冲突文件剩余:"
git diff --name-only --diff-filter=U
python -m pytest tests/unit/test_planning_defect_d16.py tests/unit/test_response_workflows.py -q --no-header
if ($LASTEXITCODE -ne 0) { throw "冲突域回归失败，勿提交" }

Write-Host "===[5/6] 提交合并===" -ForegroundColor Cyan
git add "tests/conftest.py" ".pre-commit-config.yaml"
git status --short
Write-Host "请确认 staged 集合仅含 4 个冲突文件后执行：git commit -m ""merge: 解决 PR #634 与 master 冲突（CI/夹具/e2e/D16）"""

Write-Host "===[6/6] 推送（人工确认后）===" -ForegroundColor Cyan
Write-Host "  git push origin develop   # PR #634 mergeable 将自动变 MERGEABLE"
Write-Host "  禁止 git push --force（守不易：develop 历史含并行会话提交）"
