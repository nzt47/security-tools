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

### 2.4 ✅ 已修（提交 `86459fd3`）：stray-approval 守卫判据升级为 mtime 差集

#### 2.4.1 问题全貌（比"只改了一半"更严重）

`tests/conftest.py` 有**两道** stray-approval 守卫，`400b76f4` 只改了**一道**，且**改法是错的**：

| 守卫 | 位置 | `400b76f4` 后的行为 |
|---|---|---|
| 逐用例 | 现 `tests/conftest.py` 的 `_no_stray_approval_store` | 加了 `_live_server_running()` 探测 ⇒ **后端在跑时整体跳过归因** |
| 会话级 | `_isolate_approval_stores` 的 teardown assert | **未加**任何探测，仍是裸「文件存在即失败」 |

两个方向**都错**：

| 场景 | 修复前的后果 |
|---|---|
| 用例**自己**写出 stray（我用 negative probe 实测复现） | 守卫打印「检测到运行中的后端…由其写入」⇒ **真缺陷被甩锅给后端、且文件不删 ⇒ 守卫彻底失效** |
| 会话前就存在的后端真实文件（`approval_records.jsonl`） | 会话级裸 assert ⇒ **必假失败**（session teardown ERROR，且会被误读成"我的提交引入回归"） |

#### 2.4.2 关键实测依据（判据可行性）

本机后端 `python app_server.py`（PID 1792）存活时：

```
T0: agent/data/approval_records.jsonl  len=3673  mtime=2026-09-20 00:20:04
（等待 12 秒）
T1: agent/data/approval_records.jsonl  len=3673  mtime=2026-09-20 00:20:04   ← 完全未变
```

⇒ **后端只在有审批动作时写，不是持续写** ⇒ 「某个时间窗口内该文件有没有被改动」
是一条**精确可用**的归属判据，无需靠"后端是否在跑"这种粗粒度信号。

另：`.env:1215` 的 `APPROVAL_RECORDS_PATH=agent/data/approval_records.jsonl`
**是服务的真实配置** ⇒ 后端写该路径是**正常行为**，不是缺陷。

#### 2.4.3 修法（已提交）

两道守卫统一改为比较窗口前后的 `(size, mtime_ns)`：
- 窗口内发生变化 ⇒ 判为本窗口内被写 ⇒ 报 stray（并删除产物）
- 完全一致 ⇒ 与之无关

同时**删除** `_live_server_running()`（零调用 ⇒ 死代码，只留注释说明废弃原因）。

#### 2.4.4 双向负例验证（后端存活条件下）

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 用例期间**自己写出** stray | 被甩锅给后端（**漏检**） | **被抓**（消息含 `执行期间,审批库被写到了 agent/data/`）✅ |
| 会话前已存在的后端真实文件 | **必假失败** | **不误报** ✅ |

相关面复跑 **210 passed / 0 failed**。

#### 2.4.5 ⚠️ 我在构造负例时犯的第 10 个错

第一版负例在**启动 pytest 之前**造文件 ⇒ 该文件"先于会话存在"，按新语义**本来就不该触发**，
于是"没报错"被我一度读成"修复过度放行"。
**真负例必须让文件在会话/用例执行期间出现。** 已改为用一个临时用例在测试体内写文件。

另一个差点犯的错：第一版负例打算覆盖 `agent/data/approval_records.jsonl` ——
**那是后端正在使用的真实审批库**（内含真实 `delegate` 工具审批单）。
已改为写 `tool_approval_uses.jsonl`（后端不写该路径）。**判定"这是测试残留"之前，
先确认它是不是用户的真实运行数据。**

> `agent/data/approval_records.jsonl` 的归属**至今未完全确定**：内容含 `delegate`
> 工具的真实审批单（`L1`、命中伦理硬规则 E002、`trigger: tool_gate`、`00:17:27` 创建），
> **可能是后端处理真实请求写的，不是测试残留** ⇒ **未删除**。若确认是残留再清理。

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
- **`yunshu-ui/**`、`agent/server_routes/**`、`memory/memory_manager.py`、`plugins/chat.py`、
  `templates/yunshu.html` 等是用户在飞工作 ⇒ 不得提交**
- 工作区还有 6 个 `?? tests/unit/test_*.py` 属**会话链路修复**未提交件，非本轮产物

### 5.4b ⚠️ 已识别但**不属于我**的改动（压缩后勿误判为我的）

| 文件 | mtime | 性质 | 我的处置 |
|---|---|---|---|
| `memory/tests/test_integration.py`、`test_llm_service.py`、`test_memory_manager.py` | 00:14–00:15 | **另一并行会话的实质改动**（+44 −20）：把假密钥 `"sk-test"` 换成 `"sk-test-key-valid-12345"` 一类（约 10 处） | **不碰**。注意：这些 mtime **早于**我为验证而起的回归（00:21），且内容与认知链路修复无关 |
| `tests/contract/contracts/*.json`（6 个） | 00:22 | **跑契约测试的副产物**，diff 只有 `"generated_at"` 时间戳 | 回归结束后 `git checkout -- tests/contract/` 还原 |

> 排查方法（可复用）：**先看 mtime 与"我的动作时刻"的先后**，再用 `git diff --stat` 看是
> "时间戳类"还是"实质内容类"。两者都不满足"我改的"，就不要动。

### 5.5 用户最新要求（**必须贯彻**）
> **长任务一律交给子代理执行，不要撑爆主会话上下文。**
> ⇒ 自己不要读长输出（尤其全量测试日志 ~1.3 万行）；给子代理完整自包含提示词，
> 让它在**独立上下文**里跑，只回**结构化结论**。
> ⇒ 该要求由用户在本轮明确重申：**"这一点比贯彻始终不用我以后再说"** —— 即长期生效，
> 不需用户每次提醒。

---

## 6. 临时探针文件（未跟踪，可删可留作证据）

仓库根目录：`_tmp_verify_noise.py`（噪声+残渣量化）、`_tmp_check_dup.py`（真重复精确判据）、
`_tmp_prod_path.py`（**真实生产路径 + 800 截断口径**）、`_tmp_diag_reading.py`（字段形态诊断）、
`_tmp_diag_fallback.py`、`_tmp_check_residue.py`、`_tmp_final_accept.py`、`_tmp_probe.py`
及其 `_tmp_*.txt` 输出。**这些是本轮结论的原始证据，建议保留。**

---

## 7. 全量回归结论（2026-09-20，子代理独立上下文执行）

### 7.1 结论

**没有"与认知链路修复有关"的新增回归。** 但环境**高度受污染**，详见 7.3。

| 项 | 值 |
|---|---|
| 跑法 | `python scripts/run_full_pytest.py`（4 块 / 4 worker / mode=fast） |
| 合计 | **22391 passed / 7 failed / 1 error / 49 skipped / 47 xfailed / 490 deselected** |
| 墙钟 | **44m06s** |
| 分块完整性 | `✔ 全部 4 个分块均正常收尾，无文件丢失` |
| 基线对照 | 基线 7 条；**命中 1 条**；**11 条中 6 条已不复现**（TASK-01 在飞改动收缩） |

### 7.2 首轮跑法为何失败（**重要教训**）

**单进程全量在 23% 处被 `pytest-timeout` 杀掉**（`collected 22968`，无结束摘要，exit=1）：

- 最后开始执行的文件：`tests/unit/test_date_shift_blindspots_guard.py`
  （卡在第 17 个用例 `test_scan_surface_is_not_silently_empty`）
- **769 个测试文件中 580 个从未执行**（只有 189 个开始过）
- 隔离单跑该用例 **耗时 46.37s**（预算 120s ⇒ 余量仅 **2.6×**）
- 杀进程时机器上**另有一个 `pytest tests/unit` 全量进程在跑** ⇒ 叠加 IO/WMI 慢路径后越过 120s
- ⇒ 印证 `pytest.ini:33-45` 的记载：thread 法超时是 **`os._exit(1)` 直接杀整个进程**，
  后面的文件**一个都不执行、且不输出摘要**，rc=1 与"真有失败"无法区分
- ⇒ **正确入口是 `scripts/run_full_pytest.py`**（逐块校验收尾 + 未跑完的块逐文件补跑）

### 7.3 ⚠️ 环境受污染（本轮结论的强度限制）

| 污染源 | 影响 |
|---|---|
| **另一 `pytest tests/unit` 全量进程全程并行**（PID 移接力，00:13→约 01:50） | 本轮时间类断言/超时的主要污染源；性能断言实测 **33× 膨胀**（230.8ms vs 隔离 6.9–7.1ms） |
| **仓库在跑测试期间被 rebase** | 本轮实际测的工作树**领先 `400b76f4` 约 17 个提交**，非精确快照 |
| `run_full_pytest.py` **未加 `-p no:randomly`** | 与纪律 3 有偏差；叠加本仓已知顺序污染 ⇒ 忽红忽绿 |

⇒ 因此子代理改用**固定顺序最小复现**下判断（而非依赖分块结果），这是正确做法。

### 7.4 我据其报告**实际修掉的两个真实缺陷**

| # | 缺陷 | 证据 | 提交 |
|---|---|---|---|
| 1 | **`tests/unit/test_llm_service_base_url.py` 的 `importlib.reload` 造成顺序污染**（**是我自己在 `5b14823b`/`606d6c6e` 引入的**）—— reload 重新执行模块体、用新函数对象重建 `LLMService` 类，使后跑的 `test_llm_monitor_singleton.py::TestInstallHooks` 的类级补丁"消失" | 确定性最小复现：两文件同跑 **5 failed / 27 passed**；单跑目标文件 **21 passed**；修复后**双向各 32 passed** | `be317554` |
| 2 | **`TestPerformance` 两条墙钟断言缺 `serial` marker** —— 本仓 `serial` 机制（CI 拆 `not serial` 并行 / `serial` 串行段两条 lane，`observability-ci.yml:957,996`）本可避免，但该类**漏标** ⇒ CI 并行 lane 与本地分块都会假红 | 并行 230.8ms vs 隔离 6.9–7.1ms（**33×**）；`-m serial` 精确选中 2 条 | `b22bbf92` |

> **两处都刻意不"放宽阈值/加豁免"**：那会削弱守卫的真实检测能力。正确做法是**隔离测量环境**。

### 7.5b 其余待决策 / 未闭环

| # | 事项 | 状态 |
|---|---|---|
| 1 | runner 缺 `-p no:randomly` | ✅ **已修**（`343d2bc2`）—— 两处 pytest 调用都补上；理由：pytest-randomly 还会打乱**文件内**用例顺序，改变全局单例/类级补丁装配次序，而本脚本产物是与 `failures_baseline.txt` 对照的门禁结论，必须可复现 |
| 2 | `test_date_shift_blindspots_guard.py` 的 46.37s 单用例（曾是"首轮全量杀手"，导致 580 文件未执行） | ✅ **已修**（`794fc852`）—— 标 `@pytest.mark.slow`；fast lane 不再被拖垮，slow lane（单块无争用）照常监控 |
| 3 | `test_circuit_breaker_boundary.py` 墙钟余量不足 | ✅ **已修**（`343d2bc2`）—— 三个用例改 `patch` 注入时间，替掉 `sleep`；余量从 0.1~0.2s 变为确定性数值比较。36 passed × 5 次全稳 |
| 4 | 另一并行会话仍在改写历史（rebase） | ⏳ 我的提交被重写为 `f7472c61` / `64b74e57` / `d115ec0b` / `2252080a`，内容**全部保留**。**不要 rebase 或推送**，以免与它冲突 |
| 5 | 是否为本轮精确快照重跑全量 | ⏳ 需**干净无并发**环境；本轮只能支撑"该改动没有被任何新失败指向" |

### 7.6 子代理第 2 版报告推翻了我的一处表述（**必须接受**）

子代理逐 blob 核对了第一轮实际加载的 conftest，结论：

> `agent/data/approval_records.jsonl` **在会话开始前就存在**（mtime 00:20:04 < 会话 00:21:11，
> 且至今仍是 3673B / 同一 mtime）⇒ 它在**每个用例开始时都在 `strays` 里**
> ⇒ 逐用例守卫中 `p not in strays` 那一支（**唯一调用 `_live_server_running()` 的地方**）
> **恒不可达** ⇒ 我加的那个探测**本轮一次都没被调用，零行为效果**；
> 会话级 assert 也就不可能被它"新引入地"触发。

⇒ **接受该更正**。同时它给出的建议与我的最终做法一致：
**应沿用 `(size, mtime_ns)` 差集口径，而不是恢复"后端在跑就跳过"的探测。**

> ⚠️ 注意我的**负例验证仍然是有效的**（当时确实观察到守卫打印"跳过 stray 归因"）——
> 那一次 `tool_approval_uses.jsonl` 是**在用例期间**由探针用例写出的，满足了"窗口内新增"，
> 故探测分支可达并被证实会误判。两者不矛盾：
> **在"文件先于会话存在"的常规环境下该分支不可达（零效果），
> 在"窗口内新增"的非常规环境下它会误判（有害）。**
> ⇒ 结论不变：**该判据应当删除，已删除。**

### 7.8 🔴 一条**我自己制造的错误归因**（子代理拒绝录入，它是对的）

**我给出的错误描述**：我在汇总里写了「session teardown ERROR（`approval_records.jsonl`）」
并让子代理按此登记。

**事实（子代理用 9 个日志逐串检索后驳回）**：

| 检索串 | 出现次数 |
|---|---|
| `执行期间，审批库被写到了 agent/data/`（逐用例守卫，`tests/conftest.py:126`） | **0** |
| `本次会话期间`（会话级守卫，`tests/conftest.py:271`） | **0** |
| 字符串 `审批库` 本身 | **0** |

两轮跑法里**唯一的 teardown ERROR** 是：

```
ERROR at teardown of tests/integration/test_audit_trace.py::
    TestAuditTrace::test_different_trace_ids_isolated
仓库根 .env 的 LLM_API_KEY 在测试期间被改写…before_len=36 after_len=47
```

它来自**另一道守卫** —— `.env` 篡改守卫（`tests/conftest.py:681-685`），
与审批库守卫**完全无关**。

**错误是怎么产生的**：子代理最初只**预测**"会话级守卫会失败"，随后**自己更正**了；
而我在汇总时把它的预测与它报告的 **#7（`.env` 那条）** 合并叙述，
于是把一条**从未发生的观测**写成了事实。**责任在我，不在它。**

**子代理的处理值得记录**：它明确拒绝按我的措辞登记，理由是
「那会是一条假观测，正是你一路在防的东西」——
**这是正确的**。本条保留，作为"上级指令与实测冲突时应以实测为准"的实例。

**已独立核实**（我复核，非转述）：
- `conftest.py:126` / `:271` 两道审批库守卫文案**确实存在**（子代理引用准确）
- `.env` 守卫在 `:681-685`，且**仍是真 assert**（未被另一会话削弱）；
  `4cc9a9da` 修的是**肇事方**（演示脚本不再在 import 期改写 `.env`），不是守卫本身

### 7.9 ⚠️ 对 7.6 的精度补充

7.6 说"我那个探测在本轮零调用"——**成立**（`approval_records.jsonl` 会话前即存在 ⇒
逐用例分支恒不可达）。但**不能说"我的负例验证无效"**：负例当时确实观察到守卫打印
`跳过 stray 归因`，因为那次是探针用例**在用例期间**写出文件（满足"窗口内新增"）。
⇒ 完整结论：**该判据在常规环境下零效果、在非常规环境下有害 ⇒ 应当删除。已删除。**

另更正一处：`d115ec0b` 与 `86459fd3` **并非"同一提交的两个 SHA"** ——
两者 tree 不同（`0f87ed0dbe` vs `942ae10870`）、parent 不同，是 rebase 产生的
**同 message 内容重写版**，不是同一对象。

### 7.10 未闭合项（如实登记，不编解释）

| 项 | 状态 |
|---|---|
| 分块跑 header selected 合计 `22478` vs 各块 summary 加总 `22499`，**差 21 条（0.09%）** | **未对齐**。子代理猜测可能是 setup/teardown 级 error 的重复计数口径，但**未证实**；我用单进程复跑（3 文件 68 passed）**无法复现**，故该差异来自 runner 的**分块拼接**。**不影响任何 pass/fail 判定**，不编解释 |

### 7.11 对 `agent/data/approval_records.jsonl` 的最终结论

- 内容：**`delegate` 工具的真实审批单**（`L1`、命中伦理硬规则 E002、`trigger: tool_gate`、
  `created_at 00:17:27`），共 3 行 / 3673 字节
- mtime 全程恒为 `00:20:04`（会话开始前即为该值，直到最后未变）
  ⇒ 与子代理的独立观测一致：**后端只在有审批动作时写，不是持续写**
- **归属仍不完全确定**（可能是后端处理真实请求所写，也可能是先前某次 pytest 的残留）
  ⇒ **未删除**。若要清理，请先确认无业务价值

### 7.12 本轮我提交的测试链（全部未 push）

| 提交 | 内容 |
|---|---|
| `be317554` | 去掉 `test_llm_service_base_url.py` 的 `importlib.reload`（顺序污染，**我自己引入的**） |
| `b22bbf92` | `TestPerformance` 补 `serial`（并行 33× 膨胀 ⇒ 假红） |
| `794fc852` | 扫描面守卫标 `slow` + 裁定 conftest 的 mtime 令牌命中（`fs_clock_vs_today`） |
| `db9ac38f` | `llm_monitor_singleton` 收尾强制复原 `_do_chat`（**收尾不可靠 ⇒ 补丁永久泄漏**） |
| `343d2bc2` | runner 固定顺序 + 熔断器边界用例改注入时间 |
| `c5abd4df` | 修正 §7.8 那条**我自己制造的假观测** + 登记 21 条未对齐项 |

**最终验证：11 个受影响测试文件 `342 passed / 0 failed`。**
（时间线：修复前同一批为 `305 passed / 1 failed`）

> ⚠️ 本文件里出现的 `400b76f4` / `86459fd3` / `edbff7c6` / `4a114f03` 是**当时的 SHA**；
> 另一并行会话做过 rebase，它们在当前历史中的对应体见 §7.6 / §7.9。
> **引用前请用 `git log --grep=<主题关键词>` 重新定位**，不要直接依赖本文件里的短 SHA。
