# Git hooks（core.hooksPath = hooks）

本目录存放仓库自带的 git hooks，用「仓库提交 + core.hooksPath」随克隆生效，
避免每个 clone 手工往 .git/hooks 里复制。

启用（本仓库已执行过；新克隆/新机器需执行一次）：
    git config core.hooksPath hooks

当前 hook：
- pre-commit → 调用 `python scripts/clean_runtime_noise.py`，每次提交前自动清理
  “运行噪音”：data/learned_workflows.json 的统计字段漂移（success_count/
  confidence/updated_at/last_used_at 等）、以及仅换行差异的文件。真实的数据
  /代码改动不会被清理；纯噪音时工作区也一并恢复干净。

临时跳过（不推荐，仅用于有意保留噪音改动时）：
    git commit --no-verify
