# L1 意图匹配错误码文档

> **用途**：前端对接参考，针对技能管理系统 Layer 1 元数据匹配场景的错误处理与用户提示。
>
> **适用接口**：`POST /api/skills-mgmt/match`
>
> **最后更新**：2026-06-30

---

## 一、接口概览

### 请求

```
POST /api/skills-mgmt/match
Content-Type: application/json

{
  "intent": "帮我解析 PDF 文件",
  "top_k": 5,
  "enabled_only": true,
  "min_score": 0.01
}
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `intent` | string | 是 | — | 用户意图文本（自然语言或关键词） |
| `top_k` | int | 否 | `5` | 返回前 K 个匹配结果 |
| `enabled_only` | bool | 否 | `true` | 是否仅返回启用状态的技能 |
| `min_score` | float | 否 | `0.01` | 最低匹配分阈值（0.0~1.0） |

### 成功响应（HTTP 200）

```json
{
  "ok": true,
  "matches": [
    {
      "skill_id": "pdf-extractor",
      "name": "PDF 文档解析器",
      "description": "提取 PDF 文件中的文本、图片和元数据",
      "score": 1.0,
      "estimated_tokens": 138,
      "category": "document",
      "tags": ["pdf", "parse"],
      "version": "1.2.0",
      "enabled": true
    }
  ],
  "total_scanned": 3,
  "elapsed_ms": 0.12,
  "estimated_total_tokens": 138
}
```

### 错误响应统一格式

```json
{
  "ok": false,
  "error": "人类可读的错误描述",
  "code": "SKILL_XXX_XXX",
  "details": { }
}
```

---

## 二、L1 匹配失败场景分类

L1 匹配失败分为 **三大类**：

| 类别 | 特征 | HTTP 状态 | 前端处理策略 |
|------|------|-----------|--------------|
| **A. 参数校验失败** | 请求参数非法 | 400 | 即时提示用户修正输入 |
| **B. 业务空结果** | 匹配正常完成但无命中 | 200 | 引导用户调整关键词或创建新技能 |
| **C. 系统异常** | 服务端内部错误 | 500 | 提示稍后重试，记录错误日志 |

---

## 三、详细错误码清单

### A. 参数校验失败（HTTP 400）

#### A1. `SKILL_VALIDATION_ERROR` — 意图为空

| 字段 | 值 |
|------|-----|
| **错误码** | `SKILL_VALIDATION_ERROR` |
| **HTTP 状态** | 400 |
| **错误消息** | `缺少 intent 参数` |
| **触发场景** | `intent` 字段缺失、为空字符串 `""` 或仅含空白字符 `"   "` |
| **根因** | 前端未填写意图即提交，或输入框被清空后未拦截 |
| **details** | 无 |

**前端友好提示**：
> ⚠️ 请输入您想完成的任务描述，例如「帮我解析 PDF 文件」

**前端处理建议**：
- 提交按钮在 `intent` 为空时禁用（`disabled`）
- 输入框添加 `placeholder`：`描述你想完成的任务...`
- 添加防抖（300ms），避免空值快速提交

---

#### A2. `SKILL_VALIDATION_ERROR` — top_k 参数非法

| 字段 | 值 |
|------|-----|
| **错误码** | `SKILL_VALIDATION_ERROR` |
| **HTTP 状态** | 400 |
| **错误消息** | `参数错误: invalid literal for int() with base 10: 'abc'` |
| **触发场景** | `top_k` 传入非整数字符串（如 `"abc"`、`null`、`[]`） |
| **根因** | 前端类型转换错误或接口调试时传错类型 |

**前端友好提示**：
> ⚠️ 参数格式错误，请刷新页面重试

**前端处理建议**：
- `top_k` 应由前端控件（如下拉框）控制，不允许用户直接输入
- 推荐选项：`3 / 5 / 10 / 20`

---

#### A3. `SKILL_VALIDATION_ERROR` — min_score 参数非法

| 字段 | 值 |
|------|-----|
| **错误码** | `SKILL_VALIDATION_ERROR` |
| **HTTP 状态** | 400 |
| **错误消息** | `参数错误: could not convert string to float: 'high'` |
| **触发场景** | `min_score` 传入非浮点字符串（如 `"high"`、`"strict"`） |
| **根因** | 前端传入了语义化字符串而非数值 |

**前端友好提示**：
> ⚠️ 匹配阈值设置异常，已重置为默认值

**前端处理建议**：
- `min_score` 使用滑块控件，范围 `0.0 ~ 1.0`，步长 `0.05`
- 默认值 `0.01`（宽松匹配）

---

### B. 业务空结果（HTTP 200，`matches: []`）

> **注意**：此类场景 HTTP 状态为 200，表示请求成功但无匹配结果。前端需检查 `matches.length === 0`。

#### B1. 空仓库 — 无可用技能

| 字段 | 值 |
|------|-----|
| **错误码** | 无（`ok: true`） |
| **HTTP 状态** | 200 |
| **响应特征** | `matches: []`, `total_scanned: 0` |
| **触发场景** | 系统初次部署，尚未安装任何技能 |
| **根因** | `data/skills_repo/` 目录为空或无合法技能包 |

**前端友好提示**：
> 📭 技能库暂无可用技能
>
> 您可以：
> - 点击「上传技能包」安装 `.zip` 格式的技能包
> - 点击「AI 创建」由 AI 辅助生成新技能
> - 联系管理员导入技能仓库

**前端处理建议**：
- 显示空状态插图（Empty State）
- 提供「上传技能包」和「AI 创建」两个 CTA 按钮

---

#### B2. 无匹配 — 意图与现有技能无关

| 字段 | 值 |
|------|-----|
| **错误码** | 无（`ok: true`） |
| **HTTP 状态** | 200 |
| **响应特征** | `matches: []`, `total_scanned: N`（N > 0） |
| **触发场景** | 用户输入的意图与所有已安装技能的元数据均无关键词重合 |
| **根因** | 意图文本中的关键词未出现在任何技能的 `name`/`description`/`tags`/`category` 中 |

**示例**：
- 意图：`"帮我做一道红烧肉"` → 仓库中只有 PDF/Excel 技能 → 无匹配
- 意图：`"video edit"` → 仓库中只有文档类技能 → 无匹配

**前端友好提示**：
> 🔍 未找到匹配「{intent}」的技能
>
> 建议：
> - 尝试更通用的关键词（如「PDF」「Excel」「图表」）
> - 检查拼写是否正确
> - 浏览全部技能库查看可用技能
> - 创建一个新技能来完成此任务

**前端处理建议**：
- 显示「未找到匹配技能」的空状态
- 提供「浏览全部技能」按钮跳转到技能列表
- 提供「创建新技能」按钮

---

#### B3. 阈值过高 — min_score 过滤掉所有结果

| 字段 | 值 |
|------|-----|
| **错误码** | 无（`ok: true`） |
| **HTTP 状态** | 200 |
| **响应特征** | `matches: []`, `total_scanned: N`（N > 0） |
| **触发场景** | `min_score` 设置过高（如 `0.9`），所有技能的匹配分均低于阈值 |
| **根因** | 阈值设置不合理，或用户意图与技能只有部分关键词重合 |

**诊断方法**：
- 将 `min_score` 降为 `0.0` 重新请求，若返回结果则确认是阈值问题
- 检查返回的 `score` 字段，了解实际匹配分分布

**前端友好提示**：
> ⚠️ 当前匹配阈值（{min_score}）过高，未筛选到合适技能
>
> 已自动降低阈值为您重新匹配...

**前端处理建议**：
- 当 `matches` 为空且 `total_scanned > 0` 时，自动以 `min_score: 0.0` 重试一次
- 重试仍为空则显示 B2 的提示
- 暴露「匹配严格度」滑块（宽松 / 标准 / 严格 → 0.0 / 0.01 / 0.3）

---

#### B4. 全部技能被禁用 — enabled_only 过滤

| 字段 | 值 |
|------|-----|
| **错误码** | 无（`ok: true`） |
| **HTTP 状态** | 200 |
| **响应特征** | `matches: []`, `total_scanned: N`（N > 0），`enabled_only: true` |
| **触发场景** | 所有技能的 `enabled` 字段为 `false`，但 `enabled_only=true` |
| **根因** | 管理员批量禁用了所有技能，或技能审核未通过被自动禁用 |

**诊断方法**：
- 用 `enabled_only: false` 重新请求，若返回结果则确认是过滤问题
- 检查 `/api/skills-mgmt` 列表中各技能的 `enabled` 字段

**前端友好提示**：
> ⛔ 当前没有启用的技能
>
> 请联系管理员启用技能，或前往技能管理页面开启所需技能。

**前端处理建议**：
- 提供跳转到「技能管理」页面的链接
- 管理员角色可看到「启用技能」快捷操作

---

#### B5. 意图无法分词 — 仅含特殊字符

| 字段 | 值 |
|------|-----|
| **错误码** | 无（`ok: true`） |
| **HTTP 状态** | 200 |
| **响应特征** | `matches: []`, `total_scanned: N` |
| **触发场景** | 意图仅含标点、emoji、特殊符号（如 `"!!!"`、`"😊"`、`"..."`） |
| **根因** | 分词器 `_tokenize()` 使用正则 `\w+` 匹配，特殊字符无法产生有效 token，导致 `query_tokens` 为空，所有技能 `score = 0.0` |

**前端友好提示**：
> ❓ 无法理解您的输入
>
> 请用文字描述您想完成的任务，例如「解析 PDF」「制作 Excel 图表」

**前端处理建议**：
- 前端预校验：`intent.replace(/[^\w\u4e00-\u9fa5]/g, '').length === 0` 时提示
- 添加输入示例芯片（Chips）：点击快速填充示例意图

---

### C. 系统异常（HTTP 500）

#### C1. `SKILL_INTERNAL_ERROR` — 元数据索引加载失败

| 字段 | 值 |
|------|-----|
| **错误码** | `SKILL_INTERNAL_ERROR` |
| **HTTP 状态** | 500 |
| **错误消息** | `意图匹配失败: {详细错误}` |
| **触发场景** | `data/skills_repo/` 目录下某个 `skill.md` 文件损坏、权限不足、磁盘 IO 错误 |
| **根因** | 技能文件被外部程序篡改、磁盘损坏、并发写入冲突 |
| **details** | `{"intent": "用户意图前200字符"}` |

**前端友好提示**：
> 💥 系统暂时无法完成匹配
>
> 技能库可能存在损坏文件，技术团队已收到告警。
> 请稍后重试，或联系管理员检查技能仓库。

**前端处理建议**：
- 显示错误重试按钮（指数退避：3s / 6s / 12s）
- 记录错误码到本地日志，便于用户反馈
- 提供「查看健康状态」链接到 `/api/skills-mgmt/health`

---

#### C2. `SKILL_MD_NO_FRONTMATTER` — skill.md 格式错误

| 字段 | 值 |
|------|-----|
| **错误码** | `SKILL_MD_NO_FRONTMATTER` |
| **HTTP 状态** | 500（通过 `SKILL_INTERNAL_ERROR` 包装） |
| **错误消息** | `skill.md front matter 未闭合（缺少结束的 ---）` |
| **触发场景** | 某个技能的 `skill.md` 文件缺少 YAML front matter 结束标记 |
| **根因** | 手动编辑 skill.md 时遗漏 `---` 闭合符 |

**前端友好提示**：
> ⚠️ 技能仓库中存在格式错误的技能文件
>
> 受影响技能将被跳过，其他技能仍可正常匹配。
> 请管理员检查技能文件格式。

**前端处理建议**：
- 此错误通常被 `load_metadata_index()` 内部捕获并跳过单个技能
- 仅当所有技能都损坏时才会导致 500
- 建议管理员查看后端日志中的 `load_metadata_index.skip` 记录

---

#### C3. `SKILL_MD_YAML_ERROR` — YAML 解析失败

| 字段 | 值 |
|------|-----|
| **错误码** | `SKILL_MD_YAML_ERROR` |
| **HTTP 状态** | 500（通过 `SKILL_INTERNAL_ERROR` 包装） |
| **错误消息** | `YAML 解析失败: {详细错误}` |
| **触发场景** | skill.md 的 front matter 含有非法 YAML 语法（如缩进错误、未转义特殊字符） |
| **根因** | 手动编辑 YAML 时的语法错误 |

**前端友好提示**：同 C2

---

## 四、错误码速查表

| 错误码 | HTTP | 场景 | 严重程度 | 用户可操作 |
|--------|------|------|----------|-----------|
| `SKILL_VALIDATION_ERROR` | 400 | 意图为空 | 中 | 是 — 重新输入 |
| `SKILL_VALIDATION_ERROR` | 400 | top_k 非法 | 中 | 是 — 刷新页面 |
| `SKILL_VALIDATION_ERROR` | 400 | min_score 非法 | 中 | 是 — 重置参数 |
| —（空结果） | 200 | 空仓库 | 低 | 是 — 上传技能 |
| —（空结果） | 200 | 无匹配 | 低 | 是 — 调整关键词 |
| —（空结果） | 200 | 阈值过高 | 低 | 是 — 降低阈值 |
| —（空结果） | 200 | 全部禁用 | 中 | 是 — 启用技能 |
| —（空结果） | 200 | 无法分词 | 低 | 是 — 重新输入 |
| `SKILL_INTERNAL_ERROR` | 500 | 索引加载失败 | 高 | 否 — 等待修复 |
| `SKILL_MD_NO_FRONTMATTER` | 500 | skill.md 损坏 | 高 | 否 — 管理员修复 |
| `SKILL_MD_YAML_ERROR` | 500 | YAML 语法错误 | 高 | 否 — 管理员修复 |

---

## 五、前端对接代码示例

### 5.1 统一错误处理函数

```javascript
/**
 * 处理 L1 匹配 API 响应，返回用户可读的提示信息
 *
 * 状态同步机制:
 * - Request ID: 每次请求附带唯一 seq，仅最新 seq 的响应才更新 UI
 * - AbortController: 新请求发出时取消上一个未完成请求
 */
function handleMatchResponse(status, data, requestSeq, latestSeq) {
  // 1. 请求序号校验 — 丢弃过期响应
  if (requestSeq !== latestSeq) {
    console.log(`[Stale] 丢弃过期响应 seq=${requestSeq}, current=${latestSeq}`);
    return null;
  }

  // 2. HTTP 400 — 参数校验失败
  if (status === 400) {
    const code = data.code || 'SKILL_VALIDATION_ERROR';
    if (data.error.includes('intent')) {
      return { type: 'warn', msg: '请输入您想完成的任务描述' };
    }
    return { type: 'warn', msg: '参数格式错误，请刷新页面重试' };
  }

  // 3. HTTP 500 — 系统异常
  if (status === 500) {
    return {
      type: 'error',
      msg: '系统暂时无法完成匹配，请稍后重试',
      code: data.code,
      retryable: true
    };
  }

  // 4. HTTP 200 — 业务空结果
  if (status === 200 && data.ok) {
    const matches = data.matches || [];
    if (matches.length === 0) {
      if (data.total_scanned === 0) {
        return {
          type: 'empty',
          msg: '技能库暂无可用技能',
          action: 'upload_or_create'
        };
      }
      return {
        type: 'empty',
        msg: `未找到匹配「${intent}」的技能`,
        action: 'adjust_keyword'
      };
    }
    // 正常返回结果
    return { type: 'success', matches, data };
  }

  // 5. 未知状态
  return { type: 'error', msg: '未知错误，请联系管理员', retryable: false };
}
```

### 5.2 带防抖与取消的请求函数

```javascript
/**
 * 带防抖 + AbortController 的 L1 匹配请求
 *
 * 防抖: 300ms 内多次输入只发最后一次
 * 取消: 新请求发出时自动取消上一个未完成请求
 * 序号: 仅最新 seq 的响应才更新 UI
 */
let matchAbortController = null;
let matchRequestSeq = 0;
let matchDebounceTimer = null;

async function fetchMatch(intent, options = {}) {
  // 防抖: 300ms
  if (matchDebounceTimer) clearTimeout(matchDebounceTimer);

  return new Promise((resolve) => {
    matchDebounceTimer = setTimeout(async () => {
      // 取消上一个请求
      if (matchAbortController) {
        matchAbortController.abort();
      }
      matchAbortController = new AbortController();

      // 生成新序号
      const currentSeq = ++matchRequestSeq;

      try {
        const resp = await fetch('/api/skills-mgmt/match', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            intent,
            top_k: options.top_k || 5,
            min_score: options.min_score || 0.01,
            enabled_only: options.enabled_only !== false
          }),
          signal: matchAbortController.signal
        });

        const data = await resp.json();
        resolve(handleMatchResponse(resp.status, data, currentSeq, matchRequestSeq));
      } catch (err) {
        if (err.name === 'AbortError') {
          // 被取消的请求，静默忽略
          resolve(null);
        } else {
          resolve({
            type: 'error',
            msg: '网络异常，请检查连接后重试',
            retryable: true
          });
        }
      }
    }, 300);
  });
}
```

### 5.3 空结果自动降级重试

```javascript
/**
 * 空结果时自动降低 min_score 重试一次
 */
async function fetchMatchWithFallback(intent) {
  // 第一次: 标准阈值
  let result = await fetchMatch(intent, { min_score: 0.01 });

  // 空结果 + 有技能被扫描 → 降低阈值重试
  if (result && result.type === 'empty' && result.data?.total_scanned > 0) {
    console.log('[Fallback] 降低阈值重试...');
    result = await fetchMatch(intent, { min_score: 0.0 });
    if (result && result.type === 'success') {
      result.warning = '已降低匹配严格度为您找到以下结果';
    }
  }
  return result;
}
```

---

## 六、测试用例对照

以下测试用例覆盖所有 L1 匹配失败场景，详见 [test_skill_manager.py](file:///c:/Users/Administrator/agent/tests/unit/test_skill_manager.py)：

| 测试类 | 测试用例 | 对应场景 |
|--------|----------|----------|
| `TestL1MatchFailures` | `test_match_empty_intent` | A1 — 意图为空 |
| `TestL1MatchFailures` | `test_match_whitespace_intent` | A1 — 意图为空白 |
| `TestL1MatchFailures` | `test_match_no_results` | B2 — 无匹配 |
| `TestL1MatchFailures` | `test_match_empty_repo` | B1 — 空仓库 |
| `TestL1MatchFailures` | `test_match_min_score_too_high` | B3 — 阈值过高 |
| `TestL1MatchFailures` | `test_match_invalid_top_k` | A2 — top_k 非法 |
| `TestL1MatchFailures` | `test_match_negative_top_k` | A2 — top_k 负数 |
| `TestL1MatchFailures` | `test_match_disabled_only` | B4 — 全部禁用 |

---

## 七、健康检查与诊断

当 L1 匹配持续失败时，建议按以下顺序诊断：

### 7.1 检查服务健康

```
GET /api/skills-mgmt/health
```

```json
{
  "ok": true,
  "module": "skills_mgmt",
  "three_layer": {
    "file_store": { "ok": true, "repo_path": "..." },
    "executor": { "ok": true }
  }
}
```

### 7.2 检查技能列表

```
GET /api/skills-mgmt
```

确认：
- `total > 0` → 仓库非空
- 各技能 `enabled: true` → 未被禁用
- 各技能 `status: "approved"` → 已通过审核

### 7.3 检查元数据索引

```
GET /api/skills-mgmt/three-layer/summary
```

返回三层架构统计摘要，确认 Layer 1 元数据索引已正确加载。

### 7.4 查看后端日志

搜索以下结构化日志关键字：

```
"action": "match.layer1.ok"       — 匹配成功
"action": "load_metadata_index.skip" — 技能被跳过（文件损坏）
"action": "load_metadata_index.ok"   — 索引加载成功
```

---

## 八、版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0.0 | 2026-06-30 | 初始版本，覆盖 11 种 L1 匹配失败场景 |
