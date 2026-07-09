# P2-1：技能仓库 Git 双向同步

> 前置依赖：P1 阶段的 `skills_mgmt/` 三层文件存储架构（`SkillFileStore`）已就绪。
> 完成后输出：同步服务 + API 端点 + 冲突解决策略 + 单元测试套件。

---

## 1. 背景与目标

### 1.1 现状

`SkillFileStore` 以文件系统目录 `data/skills_repo/` 存储技能，每个技能一个目录：

```
data/skills_repo/
├── my_skill/
│   ├── skill.md          # YAML front matter(元数据) + Markdown body(使用说明)
│   ├── scripts/          # 执行脚本
│   │   └── main.py
│   └── temp/             # 业务模板
└── another_skill/
    ├── skill.md
    ├── scripts/
    └── temp/
```

**缺失**：无 Git 集成，技能变更无法版本化追踪，无法多用户协作共享。

### 1.2 目标

- **双向同步**：本地技能变更可推送到 GitHub 远程仓库；远程变更可拉取到本地
- **多用户协作**：多用户通过分支 + Pull Request 模式协作，避免直接 push 冲突
- **冲突解决**：自动三路合并 + 人工解决复杂冲突
- **可观测**：同步操作全链路结构化日志（trace_id, action, duration_ms）
- **安全**：Credential 不入库，通过环境变量/Git Credential Manager 注入

### 1.3 不变量（不易）

- `SkillFileStore` 现有 API 不破坏（向后兼容）
- `skill.md` 的 YAML front matter + Markdown body 格式不变
- 三层物理分离（元数据/使用说明/脚本）不变
- 路径越界安全检查不弱化

---

## 2. 架构设计

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────┐
│                  SkillsMgmtService                   │
│  (门面层 — 新增 sync_pull / sync_push / sync_status) │
├─────────────────────────────────────────────────────┤
│  SkillFileStore        │       GitSync (新增)        │
│  (文件 CRUD)           │  (Git 操作封装)             │
│  - create/read/update  │  - init / clone / pull      │
│  - delete / list       │  - push / status / diff     │
│  - load_metadata_index │  - branch / checkout        │
│                        │  - merge / conflict resolve │
├─────────────────────────────────────────────────────┤
│              data/skills_repo/ (Git 仓库)            │
│                   │                                  │
│                   ▼                                  │
│              GitHub Remote                           │
│              (origin/main + user branches)           │
└─────────────────────────────────────────────────────┘
```

### 2.2 GitSync 类设计

新增文件：`agent/skills_mgmt/git_sync.py`

```python
class GitSync:
    """技能仓库 Git 双向同步管理器

    设计原则:
        - 最小依赖: 直接 subprocess 调用 git CLI，不引入 GitPython
        - 幂等安全: pull/push 可重复执行，不产生副作用
        - 边界显性化: Git 命令失败 → 抛出带业务码的 GitSyncError
        - 可观测: 所有操作输出结构化日志
    """

    def __init__(self, repo_path: Path, remote_url: Optional[str] = None):
        self._repo = repo_path
        self._remote_url = remote_url
        self._lock = threading.RLock()

    # ─── 仓库初始化 ───

    def init_repo(self) -> None:
        """初始化 Git 仓库（git init + 设置 main 分支）"""

    def clone_remote(self, remote_url: str) -> None:
        """克隆远程仓库到本地（若本地已有内容则 merge）"""

    def configure_remote(self, remote_url: str, name: str = "origin") -> None:
        """配置远程仓库地址"""

    # ─── 同步操作 ───

    def pull(self, *, branch: str = "main", rebase: bool = True) -> SyncResult:
        """拉取远程变更到本地

        Args:
            branch: 远程分支名
            rebase: True 使用 git pull --rebase（避免 merge commit）
                    False 使用 git pull --no-rebase
        Returns:
            SyncResult: 包含变更文件列表、冲突信息
        """

    def push(self, *, branch: str = "main", force: bool = False) -> SyncResult:
        """推送本地变更到远程

        Args:
            branch: 目标分支名
            force: 是否强制推送（危险操作，需确认）
        Returns:
            SyncResult: 包含推送的 commit 信息
        """

    # ─── 分支管理（多用户协作） ───

    def create_branch(self, branch_name: str, base: str = "main") -> None:
        """创建新分支（用于用户隔离）"""

    def checkout(self, branch_name: str) -> None:
        """切换分支"""

    def merge_branch(self, source: str, target: str = "main") -> MergeResult:
        """合并分支（用于 PR 合并）"""

    # ─── 状态查询 ───

    def status(self) -> GitStatus:
        """获取仓库状态（未提交变更、当前分支、ahead/behind）"""

    def diff(self, *, cached: bool = False) -> str:
        """获取 diff 输出"""

    def log(self, *, limit: int = 20) -> List[CommitInfo]:
        """获取提交历史"""

    # ─── 提交管理 ───

    def add(self, paths: Optional[List[str]] = None) -> None:
        """暂存文件（paths=None 则 add all）"""

    def commit(self, message: str, author: Optional[str] = None) -> str:
        """提交变更，返回 commit SHA"""

    # ─── 冲突处理 ───

    def has_conflicts(self) -> bool:
        """检查是否存在未解决的冲突"""

    def list_conflicts(self) -> List[ConflictFile]:
        """列出冲突文件列表"""

    def resolve_conflict(self, file_path: str, resolution: str) -> None:
        """解决单个文件冲突（resolution: ours/theirs/manual:<content>）"""

    def abort_merge(self) -> None:
        """放弃合并（git merge --abort）"""

    # ─── 内部 ───

    def _run_git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """执行 git 命令的统一入口

        - 捕获 stdout/stderr
        - check=True 时非零退出码抛 GitSyncError
        - 记录结构化日志
        """
```

### 2.3 数据模型

```python
@dataclass
class SyncResult:
    """同步操作结果"""
    success: bool
    action: str               # "pull" | "push" | "merge"
    branch: str
    commits: List[CommitInfo]  # 涉及的提交
    changed_files: List[str]   # 变更文件列表
    conflicts: List[ConflictFile]  # 冲突文件（仅 pull/merge）
    error: Optional[str]       # 错误信息

@dataclass
class GitStatus:
    """仓库状态"""
    current_branch: str
    clean: bool                # 工作区是否干净
    ahead: int                 # 领先远程的 commit 数
    behind: int                # 落后远程的 commit 数
    modified_files: List[str]
    untracked_files: List[str]

@dataclass
class CommitInfo:
    """提交信息"""
    sha: str
    message: str
    author: str
    timestamp: str

@dataclass
class ConflictFile:
    """冲突文件"""
    path: str
    skill_id: str              # 关联的技能 ID
    conflict_type: str         # "both_modified" | "deleted_by_them" | ...
    resolution: Optional[str]  # 解决方案（None=未解决）

@dataclass
class MergeResult:
    """合并结果"""
    success: bool
    source_branch: str
    target_branch: str
    merged_commits: int
    conflicts: List[ConflictFile]
```

### 2.4 多用户协作模型

```
GitHub Remote (origin)
├── main              ← 稳定分支，只接受 PR 合并
├── user/alice        ← Alice 的工作分支
├── user/bob          ← Bob 的工作分支
└── feature/skill-x   ← 功能分支（新增技能时创建）
```

**协作流程**：
1. 用户在本地 `data/skills_repo/` 创建/修改技能
2. `GitSync.create_branch("user/<username>")` 创建用户分支
3. `GitSync.commit()` + `GitSync.push(branch="user/<username>")` 推送到远程
4. 在 GitHub 上创建 Pull Request: `user/<username>` → `main`
5. 其他用户 Review 后合并
6. 所有用户 `GitSync.pull(branch="main")` 同步最新变更

---

## 3. API 设计

### 3.1 SkillsMgmtService 扩展

在 `SkillsMgmtService` 中新增同步方法（不破坏现有 API）：

```python
class SkillsMgmtService:
    # ... 现有方法不变 ...

    # ─── Git 同步（新增） ───

    def sync_init(self, remote_url: str) -> GitStatus:
        """初始化 Git 仓库并配置远程地址"""

    def sync_pull(self, *, branch: str = "main") -> SyncResult:
        """拉取远程变更"""

    def sync_push(self, *, branch: str = "main",
                  message: str = "auto: sync skills") -> SyncResult:
        """提交本地变更并推送"""

    def sync_status(self) -> GitStatus:
        """获取同步状态"""

    def sync_create_branch(self, branch_name: str) -> None:
        """创建工作分支"""

    def sync_merge(self, source: str, target: str = "main") -> MergeResult:
        """合并分支"""

    def sync_resolve_conflict(self, file_path: str,
                               resolution: str) -> None:
        """解决冲突"""

    def sync_log(self, *, limit: int = 20) -> List[CommitInfo]:
        """获取提交历史"""
```

### 3.2 REST API 端点

新增文件：`agent/server_routes/routes_skills_sync.py`

| 方法 | 路径 | 描述 |
|------|------|------|
| POST | `/api/skills-mgmt/sync/init` | 初始化 Git 仓库 |
| POST | `/api/skills-mgmt/sync/pull` | 拉取远程变更 |
| POST | `/api/skills-mgmt/sync/push` | 提交并推送本地变更 |
| GET  | `/api/skills-mgmt/sync/status` | 获取同步状态 |
| POST | `/api/skills-mgmt/sync/branch` | 创建工作分支 |
| POST | `/api/skills-mgmt/sync/merge` | 合并分支 |
| POST | `/api/skills-mgmt/sync/conflict/resolve` | 解决冲突 |
| GET  | `/api/skills-mgmt/sync/log` | 获取提交历史 |
| GET  | `/api/skills-mgmt/sync/conflicts` | 获取冲突列表 |

**请求/响应示例**：

```json
// POST /api/skills-mgmt/sync/pull
// Request
{ "branch": "main", "rebase": true }

// Response (成功)
{
  "success": true,
  "action": "pull",
  "branch": "main",
  "commits": [
    { "sha": "abc1234", "message": "feat: 新增 PDF 解析技能", "author": "alice", "timestamp": "2026-07-09T10:00:00Z" }
  ],
  "changed_files": ["pdf_parser/skill.md", "pdf_parser/scripts/main.py"],
  "conflicts": []
}

// Response (冲突)
{
  "success": false,
  "action": "pull",
  "branch": "main",
  "commits": [],
  "changed_files": [],
  "conflicts": [
    { "path": "pdf_parser/skill.md", "skill_id": "pdf_parser", "conflict_type": "both_modified", "resolution": null }
  ]
}
```

---

## 4. 冲突解决策略

### 4.1 自动合并（无冲突）

Git 的三路合并自动处理以下场景：
- 不同技能文件的独立变更（无冲突）
- 同一技能的不同文件变更（如 skill.md 和 scripts/main.py 各自修改）

### 4.2 冲突检测与分类

```python
class ConflictResolver:
    """冲突解决器"""

    def detect(self, sync_result: SyncResult) -> List[ConflictFile]:
        """从 sync_result 中提取冲突文件"""

    def categorize(self, conflict: ConflictFile) -> str:
        """分类冲突类型:
        - content_conflict: 同一 skill.md 同一区域被双方修改
        - add_add: 双方新增同名技能
        - modify_delete: 一方修改一方删除
        """

    def auto_resolve(self, conflict: ConflictFile) -> bool:
        """尝试自动解决:
        - YAML front matter 字段级合并（不同字段可自动合并）
        - Markdown body 整体替换（无法自动合并时用 ours 或 theirs）
        Returns: True=已解决, False=需人工介入
        """
```

### 4.3 YAML Front Matter 字段级合并

`skill.md` 的 front matter 是结构化 YAML，可按字段合并：

```yaml
# Alice 的版本
---
id: pdf_parser
name: PDF解析
version: 1.2.0
tags: [pdf, parse]
enabled: true
---

# Bob 的版本
---
id: pdf_parser
name: PDF解析器
version: 1.1.0
author: bob
tags: [pdf, parse, ocr]
enabled: true
---

# 自动合并结果（非冲突字段自动合并，冲突字段标记）
---
id: pdf_parser       # 无冲突
name: PDF解析器       # 冲突! Alice="PDF解析" Bob="PDF解析器" → 需人工选择
version: 1.2.0       # 冲突! 取较高版本
author: bob           # 无冲突（Bob 新增）
tags: [pdf, parse, ocr]  # 冲突! 合并并去重
enabled: true         # 无冲突
---
```

### 4.4 人工解决 UI

冲突无法自动解决时，前端显示冲突解决对话框：
- 左右分栏显示 ours / theirs 版本
- 逐字段选择（用于 YAML front matter）
- 整体选择（用于 Markdown body）
- 解决后调用 `/api/skills-mgmt/sync/conflict/resolve`

---

## 5. 安全设计

### 5.1 Credential 管理

- **禁止** 将 GitHub Token 写入配置文件或代码
- 通过环境变量 `YUNSHU_GIT_TOKEN` 注入
- 或使用 Git Credential Manager（系统级凭据存储）
- `GitSync._run_git()` 设置 `GIT_TERMINAL_PROMPT=0` 防止交互式提示挂起

### 5.2 命令注入防护

```python
def _run_git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """所有参数通过列表传递给 subprocess，不经过 shell，防止注入"""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(
        ["git", *args],
        cwd=self._repo,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,  # 防止网络操作挂起
    )
```

### 5.3 路径安全

- `GitSync` 操作范围限制在 `self._repo` 目录内
- `resolve_conflict()` 的 `file_path` 参数进行路径越界检查（复用 `SkillFileStore._validate_skill_id`）

---

## 6. 实施计划

### Phase 1：核心 GitSync 类（2 天）
- [ ] 新建 `agent/skills_mgmt/git_sync.py`
- [ ] 实现 `init_repo` / `configure_remote` / `add` / `commit`
- [ ] 实现 `pull` / `push` / `status`
- [ ] 实现 `_run_git` 统一命令执行器
- [ ] 数据模型：`SyncResult` / `GitStatus` / `CommitInfo` / `ConflictFile`

### Phase 2：冲突解决（1 天）
- [ ] 新建 `agent/skills_mgmt/conflict_resolver.py`
- [ ] 实现 YAML front matter 字段级合并
- [ ] 实现冲突检测与分类
- [ ] 实现 `auto_resolve` + `list_conflicts`

### Phase 3：服务集成 + API（1 天）
- [ ] 在 `SkillsMgmtService` 中新增 `sync_*` 方法
- [ ] 新建 `agent/server_routes/routes_skills_sync.py`
- [ ] 注册路由到 Flask app
- [ ] 在 `__init__.py` 中导出 `GitSync`

### Phase 4：前端集成（1 天）
- [ ] 在 `SkillManagement` 组件中新增「同步」标签页
- [ ] 同步状态面板（当前分支、ahead/behind、未提交变更）
- [ ] 拉取/推送按钮 + 操作日志
- [ ] 冲突解决对话框（左右分栏 + 字段选择）

### Phase 5：测试（1 天）
- [ ] `tests/unit/test_git_sync.py` — GitSync 单元测试（mock subprocess）
- [ ] `tests/unit/test_conflict_resolver.py` — 冲突解决测试
- [ ] `tests/integration/test_skills_sync_e2e.py` — 端到端同步流程
- [ ] 在 CI 中新增 `git` 可用性检查

---

## 7. 测试策略

### 7.1 单元测试

```python
class TestGitSync:
    """GitSync 单元测试（mock subprocess）"""

    @patch('subprocess.run')
    def test_init_repo_creates_git_directory(self, mock_run):
        """init_repo 应调用 git init"""

    @patch('subprocess.run')
    def test_pull_rebase_success(self, mock_run):
        """pull --rebase 成功时返回 SyncResult"""

    @patch('subprocess.run')
    def test_pull_conflict_detected(self, mock_run):
        """pull 检测到冲突时返回 conflict 列表"""

    @patch('subprocess.run')
    def test_push_to_remote(self, mock_run):
        """push 推送到远程分支"""

    @patch('subprocess.run')
    def test_command_injection_prevented(self, mock_run):
        """恶意分支名不会触发 shell 注入"""

    @patch('subprocess.run')
    def test_timeout_handling(self, mock_run):
        """网络超时不挂起"""

class TestConflictResolver:
    """冲突解决器测试"""

    def test_yaml_field_level_merge_no_conflict(self):
        """不同字段的 YAML 变更可自动合并"""

    def test_yaml_same_field_conflict_detected(self):
        """同字段的 YAML 变更标记为冲突"""

    def test_markdown_body_replaced_by_ours(self):
        """Markdown body 冲突时按策略选择"""

    def test_add_add_conflict_for_new_skill(self):
        """双方新增同名技能检测为 add/add 冲突"""
```

### 7.2 集成测试

使用临时目录 + 本地 bare Git 仓库模拟远程：
```python
@pytest.fixture
def git_env(tmp_path):
    """创建临时 Git 仓库 + bare 远程"""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)])
    local = tmp_path / "local"
    local.mkdir()
    sync = GitSync(local, remote_url=str(remote))
    sync.init_repo()
    sync.configure_remote(str(remote))
    return sync
```

---

## 8. 依赖与风险

### 8.1 依赖
- `git` CLI（系统级，CI 需确保可用）
- 无新增 Python 依赖（使用 subprocess + PyYAML 已有）

### 8.2 风险
| 风险 | 影响 | 缓解 |
|------|------|------|
| git 未安装 | 同步功能不可用 | 启动时检测，降级为纯文件模式 |
| 网络不可达 | pull/push 失败 | 超时 30s + 重试 + 明确错误提示 |
| 大量冲突 | 用户体验差 | YAML 字段级自动合并减少人工介入 |
| Token 泄漏 | 安全风险 | 环境变量注入 + .gitignore 排除 |
