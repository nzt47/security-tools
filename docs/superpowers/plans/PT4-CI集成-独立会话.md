# PT4 — CI 集成：新测试加入 CI pipeline

> **目标：** PT1/PT2/PT3 新增的测试全部在 CI 中自动运行
> **项目路径：** `c:\Users\Administrator\agent`
> **分支建议：** `refactor/PT4-ci-integration`

## 一、背景

当前 `.github/workflows/ci.yml` 中没有覆盖：
- `tests/e2e/`（E2E 测试，离线部分不需要服务器）
- `tests/integration/`（集成测试）
- 新模块的单测（model_router, task_planner 等）

## 二、操作步骤

### Step 1: 检查当前 CI 配置

```bash
cat .github/workflows/ci.yml
```

找到测试执行的部分（通常在 pytest 步骤附近）。

### Step 2: 修改 `.github/workflows/ci.yml`

在 `jobs.test.steps` 中找到 pytest 执行步骤，增加以下测试分组：

```yaml
# ── 新模块单元测试 ──
- name: Test new modules
  run: |
    python -m pytest tests/unit/test_model_router.py -x -q --tb=short
    python -m pytest tests/unit/test_task_planner.py -x -q --tb=short
    python -m pytest tests/unit/test_hitl.py -x -q --tb=short
    python -m pytest tests/unit/test_health.py -x -q --tb=short
    python -m pytest tests/unit/test_cognitive_loop.py -x -q --tb=short
    python -m pytest tests/unit/test_guardrails.py -x -q --tb=short
    python -m pytest tests/unit/test_workflow_engine.py -x -q --tb=short
    python -m pytest tests/unit/test_audit.py -x -q --tb=short
    python -m pytest tests/unit/test_subagent.py -x -q --tb=short

# ── 集成测试 ──
- name: Integration tests
  run: |
    python -m pytest tests/integration/ -x -q --tb=short

# ── 离线 E2E 测试（无需服务器） ──
- name: E2E offline tests
  run: |
    python -m pytest tests/e2e/test_offline_basic.py tests/e2e/test_offline_workflow.py -x -q --tb=short

# ── 在线 E2E 测试（需服务器，可选） ──
- name: E2E online tests
  if: github.event_name == 'push' && github.ref == 'refs/heads/master'
  run: |
    # 启动测试服务器
    python app_server.py &
    sleep 5
    # 运行在线 E2E
    python -m pytest tests/e2e/test_online_chat.py tests/e2e/test_online_tool_call.py -x -q --tb=short
    # 清理
    kill %1 2>/dev/null || true
```

### Step 3（优化方案）：合并为一个命令

如果步骤太多，可以用一个合并命令：

```yaml
- name: Run all tests
  run: |
    python -m pytest \
      tests/unit/test_model_router.py \
      tests/unit/test_task_planner.py \
      tests/unit/test_hitl.py \
      tests/unit/test_health.py \
      tests/unit/test_cognitive_loop.py \
      tests/unit/test_guardrails.py \
      tests/unit/test_workflow_engine.py \
      tests/unit/test_audit.py \
      tests/unit/test_subagent.py \
      tests/integration/ \
      tests/e2e/test_offline_basic.py \
      tests/e2e/test_offline_workflow.py \
      -x -q --tb=short
```

## 三、验证

```bash
# 在本地模拟 CI 环境
python -m pytest \
  tests/unit/test_model_router.py \
  tests/unit/test_task_planner.py \
  tests/unit/test_hitl.py \
  tests/unit/test_health.py \
  tests/unit/test_cognitive_loop.py \
  tests/unit/test_guardrails.py \
  tests/unit/test_workflow_engine.py \
  tests/unit/test_audit.py \
  tests/unit/test_subagent.py \
  tests/integration/ \
  tests/e2e/test_offline_basic.py \
  tests/e2e/test_offline_workflow.py \
  -x -q --tb=short

# 确认 CI 配置文件语法正确
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('CI 配置语法正确')"
```

## 四、提交

```bash
git add .github/workflows/ci.yml
git commit -m "ci: PT1-PT4 新测试加入CI pipeline

新增:
- 9个新模块单元测试
- tests/integration/ 集成测试
- tests/e2e/ 离线E2E测试
- tests/e2e/ 在线E2E测试（仅master分支）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

## 五、验收

```bash
# 推送后等待 CI 运行
git push origin refactor/PT4-ci-integration

# 查看 CI 状态
gh run list --limit 3
```
