# 交接快照 — 云枢能力层重构审计 + 认知链路修复（2026-09-20 00:20）

> **用途**：本文件是**上下文压缩后仅凭它能继续工作**的最小交接件。
> 所有数字均来自本轮实测，已标注证据位置；未实测的一律写明"未验证"。

---

## 1. 已完成并落地的提交（`C:\Users\Administrator\agent`）

| 提交 | 内容 | 验证 |
|---|---|---|
| `82bbe6fd` | P0-1 `load_tool_meta` 换 `CSafeLoader` + 进程级缓存 | 实测 assemble 170.3ms → 0.6ms |
| `9bf68e23` | 地基加固（69 文件：依赖三方一致 / 测试基线重建 / 门禁诚实化 / 覆盖率口径统一 / pre-commit 接线） | TASK-03 产出，已核实 |
| `60d7427d` | 技能自动归类（4 文件，根因是"并列 + 表序"，不是分数低） | TASK-02 产出，已核实 |
| `606d6c6e` | 会话链路：DSML 标记泄漏根因 + 空返回 + LLM `base_url` 静默失效（33 文件） | TASK-01 产出，已核实 |
| `db092bf9` | 工作台体验（45 文件，**用户在飞切片**，非我所做） | — |
| **`400b76f4`** | **修复认知链路噪声：readings 类型不兼容 + `_fallback` 从未调用（8 文件）** | **本轮，见 §2** |

**全程未 push。** 回滚方式：`git revert <hash>`。

---

## 2. 本轮核心产出：`400b76f4`（认知链路静默失效）

### 2.1 根因（实测，**推翻了先前归因**）

```
cognitive/translator.py（修复前首行）
    if not isinstance(reading, dict):
        return "传感器读数未识别"      # ← 把全部真实读数拦在这里
```

- `BodySensor().collect_all()` → 652 条，类型分布 **`{'SensorReading': 652}`**
- `translate_all(同 652 条)` → **100% 返回"传感器读数未识别"**
- 首条：`sensor_name='behavior_disk_total_read' value=68150101 unit='次' description='磁盘总读取次数'`
  ⇒ **字段完好，只是装在对象上而不是 dict 里**

**成因是两层，必须同时修**：
1. **类型不兼容**（决定性，此前完全漏掉）⇒ 与规则多少无关，100% 走"未识别"。
2. **规则覆盖率低**：真实 645 个不同 `sensor_name`，`PromptConfig` 默认仅 **5 条规则** ⇒
   即便类型修好，不调 `_fallback()` 仍返回固定串。

### 2.2 影响口径（**两个口径都要报**，否则误判严重性）

生产接线：`agent/digital_life_persona.py::_build_body_status`
= `[r.to_dict() for r in readings]` → `inj.inject(...)` → **`len > 800` 时截断**。

| 指标 | 修复前 | 修复后 |
|---|---|---|
| `translate_all` 未识别占比 | **100%**（652/652） | **0.15%**（1/651） |
| **截断的 800 字符内**（LLM 真正看到的） | **全是"传感器读数未识别"** | **20 行真实读数、0 噪声** |
| 值/单位重复渲染 | 有（`'用户态 CPU: 11.0%: 11.0%'`） | **0 条** |
| 类型名冒充单位 | 3 条（`'…: Truebool'`） | **0 条** |
| `inject()` 全量长度 | — | 19,590 字符（生产截断至 800） |

### 2.3 改动清单（`400b76f4`，8 文件 / +1830 −26）

| 文件 | 改动 |
|---|---|
| `cognitive/translator.py` | 新增 `_coerce()`（dict 原样；带 `__dict__` 的对象提取**最小字段集**，只搬真实存在的键，**不注入默认值**；其它类型返回 `None`）；无规则命中改走 `_fallback()`；NaN 显式判掉；`_fallback()` 用 `_already_carries()` 消除重复渲染（6 类真实形态）+ `_clean_unit()` 丢弃类型名单位；新增 `_strip_ws()` |
| `sensor/body_sensor.py` | 新增模块级 `_normalize_reading()` 在**唯一收集点**归一化；`_apply_tags()` 兼容 dict/对象且单条失败只影响自己 |
| `tests/unit/test_translator_object_readings.py` | **新增 36 用例**，覆盖此前零覆盖的**对象路径**；含不变量断言「**对象与等价 dict 必须同结果**」 |
| `tests/unit/test_cognitive_engine.py` | +523 行（补测，`L-2` 三条断言更新为修复后行为） |
| `tests/unit/test_sensor_body_switch.py` | +654 行（补测） |
| `tests/test_cognitive_boundary.py` | `test_nonexistent_sensor` 断言更新 + 归因校正 |
| `cognitive/test_cognitive/test_translator.py` | 包内旧断言同步 |
| `tests/conftest.py` | `_live_server_running()`：后端在跑时跳过 stray 归因（避免把服务真实写入误判为用例写入） |

**验证**：`310 passed / 0 failed`（含项目 `pytest.ini` 配置口径）。
`git status` 已确认这 3 个测试文件在 HEAD 内。

---

## 3. 本轮**我自己犯的错**（逐条记录，供压缩后不重犯）

| # | 错误 | 真相 | 防复发 |
|---|---|---|---|
| 1 | 把噪声归因于「`_fallback()` 从未被调用」（只对了一半） | 决定性的第一层是**类型不兼容** | 报根因必须区分"现象"与"成因" |
| 2 | 认为补测 93 条全绿 ⇒ 修复已生效 | **夹具传 dict、生产传对象** ⇒ 测试夹具冒充生产 | 已写入 `TASK-00` §0.2f；用真实产物复测 |
| 3 | 用 `pytest ... -o addopts=""` 跑全量，报 3 个 `import file mismatch` ERROR，一度怀疑代码 | **是我抹掉了 `pytest.ini:26` 的 `--import-mode=importlib`**；`tests/` 下有同名文件。那 3 个文件单独跑 **134 passed** | 已写入 `TASK-00` §0.2e：**禁止清空 addopts** |
| 4 | 假设 `translate_all("abc")` 抛 `TypeError` 并据此写断言 | 实测**不抛**。查证后确认既有契约是 `PromptInjector.inject("invalid")` **优雅降级** | 写断言前先查证既有测试，别臆造契约 |
| 5 | `_already_carries()` 初版用 `rsplit(None,1)`，漏掉"数字紧贴单位"（`'用户态 CPU: 11.0%'`） | 修 3 轮才对；真实形态共 6 类 | 用真实数据量化残渣（真重复从 36 → 19 → 7 → **0**） |
| 6 | `_coerce()` 初版写 `data.get("value", 0)` 想兜底，反而产出 `'x: '` 垃圾 | 改为**只搬真实存在的键** | 不注入默认值，让"缺字段"信号原样传递 |
| 7 | 把 `_already_carries` 写成 `@staticmethod` 却在里面用 `self._TAIL_*` | `NameError`，6 条失败 | — |
| 8 | 把 `_NUMBER`（正则源串）当编译后的 pattern 用 `.findall()` | `AttributeError`；已加 `_NUMBER_RE` | — |
| 9 | 用不存在的测试路径（`tests/test_sensor_body_switch.py` 等）跑 pytest，整条命令中止 | 应为 `tests/unit/...` | 跑前验路径存在 |

---

## 4. 交付物目录（`C:\Users\Administrator\Desktop\设计思路\云枢能力层重构审计与子任务\`）

本轮**已更新**的文件：

| 文件 | 更新内容 |
|---|---|
| `README.md` | 新增「**第 21 条**：执行期间发现并修复的生产级静默失效」；"已更正前结论"表**追加 2 行**（归因错一层 / 补测全绿无效推论） |
| `05-子任务执行结果记录.md` | 新增 **§6**（发现路径 / 真实根因 / 影响口径 / 修复验证 / 我犯的 2 个错） |
| `TASK-00-共享前置与术语映射.md` | 新增 **§0.2e**（禁止清空 addopts）+ **§0.2f**（测试夹具冒充生产） |

仓库内已更新：`docs/closeout/COVERAGE_GAP_PLAN_20260919.md`（**§7.5** L-2 结案 + §0 速览补齐 + L-2/L-10 状态改"已结案"）。
⚠️ 该文件**尚未提交**（`?? docs/closeout/COVERAGE_GAP_PLAN_20260919.md`）。

---

## 5. 关键事实速查（压缩后仍需要）

### 5.1 环境
- `waitress` 16 线程绑 `127.0.0.1:5678`；冷启动实测 **81.7s / 94.94s**
- 启动入口唯一：`start_yunshu.bat`（`sensor_server.py` / `sensor/main.py` 均**未接线**）
- 部署镜像 **14.6–14.8GB**；**监控栈从未运行**（prometheus/grafana/loki/alertmanager 容器 0 个）
- **69 条告警规则永不加载**（`prometheus.yml` 只声明 4 个 `rule_files` ⇒ 实际 34 条）

### 5.2 测试环境陷阱（**必守**）
1. **禁止** `-o addopts=""`（会抹掉 `--import-mode=importlib` / `--timeout` / `--ignore` 清单）→ §0.2e
2. 必须 `-p no:randomly`（本仓装 `pytest-randomly`，随机序使全局单例用例忽红忽绿）
3. 不用管道捕获 pytest 输出；用 `> file 2>&1` 再读文件（沙箱无法开命名管道 ⇒ 假挂死）
4. `$env:PYTHONUTF8=1`（否则 GBK 解码制造 4 类假象）
5. PowerShell `>` 写 JSON 是 UTF-16 ⇒ 用 Python 写

### 5.3 尚未做的（按优先级）
| 项 | 说明 |
|---|---|
| **全量测试回归** | 本轮只跑到 `310 passed`（相关面）；**全量未完成**（子代理做，输出别回主上下文） |
| P0-3 / P0-4 / P0-5 | `sensor/registry.py`、`change_detector._diff_*`、`sensor_reading/tags/novelty` 补测 |
| P0-6 | 处置包内测试 426 行（受"禁改 `pytest.ini`"阻断，需移交） |
| P0-7 | 删 `core/local_llm.py`（唯一死代码，无删除授权） |
| TASK-04~08 | 能力规格正式化 / Registry+Loader+非 LLM 入口 / 身份与 L0–L3 / 安全接线 / 性能可观测性 |
| L-1 / L-5 / L-11 / L-12 | `generate_coverage_workflow.py:122` 硬编码单包；`sensor/registry.py:125-128` 未排除 `test_*.py`；删死代码；陈旧副本 |
| 前端 CI | `yunshu-ui/package.json` 无 `test` 脚本 ⇒ 建议补 `"test": "vitest run"` |

### 5.4 权限与边界
- 用户授权：**代做决定 + 直接执行**；但**不 push**、**不擅自删生产文件**
- **`yunshu-ui/**`、`agent/server_routes/**`、`memory/memory_manager.py`、`plugins/chat.py` 等是用户在飞工作 ⇒ 不得提交**
- 工作区还有 6 个 `??` 测试文件属**会话链路修复**未提交件，非本轮产物

### 5.5 用户最新要求（**必须贯彻**）
> **长任务一律交给子代理执行，不要撑爆主会话上下文。**
> ⇒ 自己不要读长输出（尤其全量测试日志 ~1.3 万行）；给子代理完整自包含提示词，
> 让它在**独立上下文**里跑，只回**结构化结论**。

---

## 6. 临时探针文件（未跟踪，可删可留作证据）

仓库根目录：`_tmp_verify_noise.py`（噪声+残渣量化）、`_tmp_check_dup.py`（真重复精确判据）、
`_tmp_prod_path.py`（**真实生产路径 + 800 截断口径**）、`_tmp_diag_reading.py`（字段形态诊断）、
`_tmp_diag_fallback.py`、`_tmp_check_residue.py`、`_tmp_final_accept.py`、`_tmp_probe.py`
及其 `_tmp_*.txt` 输出。**这些是本轮结论的原始证据，建议保留。**
