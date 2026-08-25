# Gitleaks 既有基线问题治理方案

> 项目：云枢 · AI 智能体桌面工作台
> 目标：治理 CI「Gitleaks 硬编码密码扫描」失败（既有基线，非 PR #749 引入）
> 日期：2026-08-21

---

## 1. 问题背景

Gitleaks 扫描（全分支）持续失败，当前命中 4 处（均为**既有代码**，非本次 Dashboard 联调新增）：

| # | 文件 | 行 | 命中类型 | 风险定性 |
|---|---|---|---|---|
| 1 | `app_server.py` | 1328 | `hardcoded-password-assignment` | ⚠️ 真实风险（默认密码可被使用） |
| 2 | `app_server.py` | 1513 / 1530 | `hardcoded-password-assignment` | ⚠️ 待核实行号（登录校验相关） |
| 3 | `scripts/guard_llm_api_key.py` | 36 | `openai-api-key` | ✅ 误报（占位符黑名单） |
| 4 | `yunshu-ui/src/pages/Profile.tsx` | 13 | `hardcoded-password-assignment` | ✅ 演示数据（mock 表单默认值） |

## 2. 逐项分析与治理方案

### 2.1 `app_server.py` 管理员密码默认值（核心治理项）

**现状**（[app_server.py:1327-1330](../../app_server.py#L1327-L1330)）：

```python
_ADMIN_USERNAME = os.environ.get("YUNSHU_ADMIN_USERNAME", "admin")
_ADMIN_PASSWORD = os.environ.get("YUNSHU_ADMIN_PASSWORD", "admin123")
if _ADMIN_PASSWORD == "admin123":
    logger.warning("管理后台使用默认密码（admin123），仅限本地联调；生产请设置 YUNSHU_ADMIN_PASSWORD")
```

**风险**：已支持环境变量，但**存在硬编码默认值** `admin123`——若生产部署忘记设置 `YUNSHU_ADMIN_PASSWORD`，将静默使用默认密码（仅打 warning），属于真实安全风险。

**治理方案（分两步，可回滚）**：

**Step 1（立即，低风险）：生产环境强制无默认**

```python
import os

_ENV = os.environ.get("YUNSHU_ENV", "development")

def _load_admin_password() -> str:
    """管理后台密码：环境变量优先；生产环境（YUNSHU_ENV=production）未设置则拒绝启动。
    【Why】消除硬编码默认密码：本地联调可用默认值，生产必须显式注入，避免静默弱口令。"""
    pwd = os.environ.get("YUNSHU_ADMIN_PASSWORD")
    if pwd:
        return pwd
    if _ENV == "production":
        raise RuntimeError(
            "生产环境必须设置 YUNSHU_ADMIN_PASSWORD 环境变量；拒绝使用默认密码启动"
        )
    return "admin123"  # 仅本地联调兜底

_ADMIN_USERNAME = os.environ.get("YUNSHU_ADMIN_USERNAME", "admin")
_ADMIN_PASSWORD = _load_admin_password()
```

- 验收：`YUNSHU_ENV=production` 且未设密码时启动抛错；本地联调行为不变
- 回归：现有依赖默认密码的测试（`admin/admin123`）在 development 模式下不受影响

**Step 2（后续，可选）：登录接口校验逻辑收口**

- 核查 1513 / 1530 行（登录校验引用点），统一经 `_ADMIN_PASSWORD` 读取，禁止在函数内再次硬编码
- `.env.example` 补充 `YUNSHU_ADMIN_USERNAME/PASSWORD` 说明与强密码要求（≥12 位含大小写数字符号）

### 2.2 `guard_llm_api_key.py` 占位符黑名单（误报处理）

**现状**（[guard_llm_api_key.py:36](../../scripts/guard_llm_api_key.py#L35-L40)）：`PLACEHOLDER` 集合中的 `sk-test-1234567890abcdef` 等用于识别**假 key**，Gitleaks 按 `sk-` 前缀误判为 OpenAI key。

**方案**（二选一，推荐 A）：

- **A. 行内豁免注释**（最小改动）：
  ```python
  PLACEHOLDER = {
      "sk-test-1234567890abcdef",          # gitleaks:allow - 测试占位符（黑名单识别用，非真实 key）
      ...
  }
  ```
- **B. Gitleaks 规则白名单**：在 `.gitleaks.toml` 为 `openai-api-key` 规则追加 `allowlist`（按仓库/文件名豁免）

- 验收：重跑 Gitleaks，`guard_llm_api_key.py` 不再命中

### 2.3 `Profile.tsx` 演示表单默认值（演示数据处理）

**现状**（[Profile.tsx:13](../../yunshu-ui/src/pages/Profile.tsx#L13)）：`useState<LoginParams>({ username: 'admin', password: '123456' })`——组件演示页的预填表单（mock 凭证）。

**方案**：改为空值占位（演示页不预填密码），消除硬编码字符串：

```tsx
const [form, setForm] = useState<LoginParams>({ username: '', password: '' })
```

- 验收：重跑 Gitleaks 不再命中 Profile.tsx；演示页功能（手动输入）不受影响

## 3. 实施计划

| 步骤 | 内容 | 风险 | 验证 |
|---|---|---|---|
| 1 | 2.1 Step 1：生产强制无默认密码 | 低（本地行为不变） | 单测 + `YUNSHU_ENV=production` 启动测试 |
| 2 | 2.2 A：占位符行内豁免注释 | 极低（仅注释） | 本地 gitleaks 复扫 |
| 3 | 2.3：Profile.tsx 表单去默认密码 | 低（演示页） | 构建 + 页面冒烟 |
| 4 | 2.1 Step 2：登录校验收口 + .env.example 说明 | 中（涉及鉴权） | 登录链路回归 + 文档评审 |
| 5 | CI 重跑 Gitleaks + 边界值扫描基线更新 | — | 确认 failures 归零（或仅剩基线说明） |

## 4. 验证方式

- 本地复扫：安装 gitleaks 后 `gitleaks git --repo-path . --log-opts="--all"`（或复用 CI 工作流重跑）
- 回归保障：管理员登录链路（env 注入 / 默认值 / 生产拒绝启动）三类用例
- 边界值扫描：若硬编码边界值基线需更新（122 vs 118 差异项定位后），同步更新基线文件

## 5. 风险与注意

1. **Step 1 属安全行为变更**：生产启动策略改变（缺密码从"警告启动"变为"拒绝启动"），需在发布说明中标注运维影响
2. 治理范围限定为**既有基线问题**，与 PR #749 功能解耦，建议单独立项排期
3. 若后续接入 secrets 管理（Vault 等），密码读取可再下沉，当前 env 方案为最小充分解

## 6. 迁移时间表

### 6.1 目标与排期原则

- **目标**：3 类 Gitleaks 硬编码命中清零 + 硬编码边界值扫描恢复通过
- **排期原则**：风险优先（真实密码风险 > 误报/演示数据 > 基线维护）；每步独立可回滚；每里程碑设验收检查点
- 排期以**里程碑**组织（不承诺绝对时长），可与版本发布节奏对齐

### 6.2 里程碑排期

| 里程碑 | 范围 | 内容（详见第 2 节方案） | 依赖 | 验收标准 | 建议发布窗口 |
|---|---|---|---|---|---|
| **M1 安全加固** | `app_server.py` 默认密码（1328 + 1513/1530） | ① 生产强制无默认（`YUNSHU_ENV=production` 缺密码拒绝启动）② 登录校验收口统一 ③ `.env.example` 补强密码说明 | 无 | 生产缺密码启动抛错；本地联调不变；Gitleaks 命中 1 清零；登录链路回归通过 | 随下一小版本（标注运维影响） |
| **M2 误报与演示清理**（可与 M1 并行） | `guard_llm_api_key.py` 占位符 + `Profile.tsx` 演示密码 | ① 行内 `gitleaks:allow` 豁免（或 `.gitleaks.toml` allowlist）② 表单改空值占位 | 无 | 两处命中清零；gitleaks 复扫通过 | 与 M1 同窗口或紧随 |
| **M3 边界值基线** | 硬编码边界值扫描（122 vs 118） | ① 差异项归属定位（工作区未提交 vs 分支历史）② 合理边界值配置化或更新基线 ③ CI 重跑 | M1/M2 完成（避免与密码治理相互干扰） | CI「硬编码边界值扫描」通过；基线文件更新记录在案 | M1/M2 后同发布窗口 |

### 6.3 检查点与门禁

- **每里程碑完成后**：本地 gitleaks 复扫 + 触发 CI 对应 workflow 重跑，确认对应失败项归零后再进入下一里程碑
- **M3 前置条件**：必须先完成差异项归属定位（防止把未提交/历史改动误并入基线）
- **最终门禁**：3 项 CI 失败（Gitleaks / 边界值 / ChromaDB 环境类）状态复核——ChromaDB 容器预检属环境问题，单独跟踪，不阻塞本时间表

### 6.4 风险缓冲

- M1 涉及鉴权逻辑：预留登录链路回归窗口（含 admin 默认 / env 注入 / 生产拒绝 三类用例）
- M2 的 gitleaks 豁免方式选择（行内注释 vs 全局 allowlist）：需在 M2 开始前与安全规范对齐，避免豁免面过大
- M3 基线更新需评审：新增基线必须有归属说明，防止掩盖真实新增硬编码
