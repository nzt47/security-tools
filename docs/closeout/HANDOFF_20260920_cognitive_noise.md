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

> **⚠️ 本节已被 §7.9 进一步修正** —— 第 2 版说"该探测零调用 ⇒ 会话级 assert 也不可能被触发"，
> 第 4 版自己更正为"会话级是**存在式**判据，与逐用例分支可达性无关 ⇒ 文件存在就会在 teardown 报错，
> 只是因为第一轮**根本没进 teardown** 才没出现"。**§7.9 的口径是最终版**，本节保留以便追溯。

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

### 7.9 ⚠️ 对 7.6 的精度补充（**子代理第 4 版给出关键澄清，已接受**）

#### 7.9.1 第一轮为何没有那条 session teardown ERROR —— **不是"没触发"，是"没进 teardown"**

子代理读了第一轮实际加载的原始 blob（`f7472c61:tests/conftest.py`，即 `400b76f4` rebase 后的同一份），
确认该版本里 `_live_server_running` **只出现 2 次**（定义 + 逐用例守卫内唯一一次调用），
会话级是**存在式**裸断言 `assert not stray`。据此推出：

- `agent/data/approval_records.jsonl` 当时存在（3673B / mtime `00:20:04`，会话前即在、全程未变）
  ⇒ **只要跑到 session teardown，该 assert 必失败**（存在式判据 ⇒ 必假失败）
- 它没出现的**唯一原因**：第一轮在 23% 处被 `pytest-timeout` 的 `os._exit(1)` 直接杀死，
  **进程被终结、任何 session teardown / finalizer 都没执行**
  （与 `pytest.ini:33-45` 自述机制一致；日志亦印证：无摘要、无 teardown 输出、`exit=1`）
- 第二轮加载的是**修复后**版本（双守卫皆差集），文件全程未变 ⇒ **正确地未触发**

⇒ **子代理最初那句预测实质上是对的**（它先自我更正为"不可能触发"，第 4 版又更正回来）。
**正确口径 = 判据本身错了**：存在式 ⇒ 后端真实文件**必假失败**；probe 式 ⇒ 真 stray **被甩锅**。

#### 7.9.2 我必须修正自己上一版的表述

我先前写"我那个探测在本轮零调用、零行为效果"——**只对了一半**，须按下表精确化：

| 守卫 | 我的改动 | 在该环境下的**实际**效果 |
|---|---|---|
| **逐用例** | 加 `_live_server_running()` 跳过 | **零行为效果**。因为该文件**会话前即存在** ⇒ 它在每个用例开始时都在 `strays` 里 ⇒ `p not in strays` 那一支（**唯一调用探测处**）恒不可达。改与不改，行为相同 |
| **会话级** | 由「存在式」改为 `(size, mtime_ns)` 差集 | **有真实效果，且是必需的**。存在式判据在 teardown 必假失败；换了差集后（文件未变）才不报。它没被观测到失败，**只是因为第一轮没跑到 teardown** |

⇒ 结论修正：**该"半修"不是零效果，而是一半零效果（逐用例探测本就不可达，应删）、
一半是必要修复（会话级存在式判据必假失败）。**
这与最终做法一致：**两处统一为差集口径、并删掉探测**。

#### 7.9.3 其余两处更正

- 我上次说"我的负例验证仍有效"——**成立**：负例当时确实观察到守卫打印
  `跳过 stray 归因`，因为那次是探针用例**在用例期间**写出文件（满足"窗口内新增"），
  探测分支可达并被证实会误判。⇒ **该判据在常规环境下不可达、在非常规环境下有害，故应删除。已删除。**
- `d115ec0b` 与 `86459fd3` **并非"同一提交的两个 SHA"**：tree
  （`0f87ed0dbe` vs `942ae10870`）与 parent 均不同，是 rebase 产生的**同 message 内容重写版**。
  （子代理第 4 版仍写作"`d115ec0b`=`86459fd3`"，此处以我的 `rev-parse` 实测为准。）

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

---

## 8. 能力层重构剩余任务的**开工前预检**（2026-09-20，主会话执行）

> **目的**：TASK-04~08 的提示词写于审计期，而仓库其后已推进约 18 个提交。
> 本节逐条核实其**承重事实**是否仍然成立，避免子代理在过期前提上施工
> （这正是本会话反复踩的"快照过期"坑）。**结论：六个任务的前提全部成立。**

### 8.1 仓库就绪度（全部满足）

| 核实项 | 结果 |
|---|---|
| 工作区 | **完全干净** |
| 与远端 | `origin/master` = `4cc9a9da`；`ahead 9 / behind 0` ⇒ 有干净基线、无冲突 |
| 并行会话 | **已停止**（用户确认）；无任何 pytest 在跑 |
| 后端 | `python app_server.py`（PID 980）在跑 ⇒ **不要终止**；它写 `agent/data/approval_records.jsonl`（`.env:1215` 服务真实配置）⇒ **不要当残留删** |
| 基线文件 | `failures_baseline.txt`（7 条）、`coverage_baseline.json`（9 包）均在位 |

### 8.2 TASK-09（P0 补测第二批）—— 已派出，附口径校正

- 目标 5 个模块：`sensor/registry.py`、`sensor/change_detector.py`、`sensor/sensor_reading.py`、
  `sensor/tags.py`、`sensor/novelty.py`
- ✅ **已向 `TASK-09` 写入一节口径校正**（关键）：原表"当前覆盖率"是审计期**全量**口径，
  不可作基线。实测子集口径会严重失真：
  `change_detector` 71.6% → **45%**（−26.6pp）；`registry.py` 74.4% → **0%**（子集跑从未 import 它）
  ⇒ 要求子代理**自己用权威口径量基线**，报数**必须带完整路径**
  （`sensor/registry.py` 与 `core/registry.py` 同名，多 `--cov=` 下会撞名）

### 8.3 TASK-04（能力规格 + `location`）—— ✅ 前提成立，**已写入校正**

| 前提 | 实测 |
|---|---|
| `ToolMeta` 与 `plane/effect/risk/callable_mode/permission_level` 常量 | ✅ 全在 `agent/lines/models.py` |
| 91 个 `data/tool_definitions/*.yaml` 含 `location` | ✅ **0 次**（零起步） |
| `capability_manifest.json` | ✅ 已 git 跟踪；`entries` **114 条**（`tool:91 / skill:23`）；**0 条含 `location`** |
| `scripts/sync_capability_manifest.py` | ✅ 存在（`--check` 有落地位置） |

> 🔴 **发现一处过期前提并已修正**：TASK-04 §2.3(a) 的论证建立在
> 「4 个 MCP 管理工具不带 `source` ⇒ 兜底成 `builtin`」上，但**实测 manifest 里根本没有 `source` 键**。
> 对应的事实字段是 **`host_executor`**（形如 `agent.tools.ext_tools:_connect_mcp`），
> 而这 4 条的 `host_executor` **全部指向本进程内的实现函数**（`ext_tools.py` / `extra_tools.py`）。
> ⇒ 已在 TASK-04 顶部加入"2026-09-20 实测校正"一节（含 114 条 counts、单条 entry 的 31 个字段名、
> 与 4 条 MCP 工具的实测取值表），并明确要求：**判定依据换成 `host_executor` 及其可验证事实，
> 不得再用"`source` 兜底"这条已不成立的论证**；保留 §2.3(a) 关心的不变量
> （这 4 个是 MCP「管理面」，不是 MCP 客户端调用）。

### 8.4 TASK-06 / TASK-07（身份与 L0–L3、安全接线）—— ✅ 前提全部成立

逐条实测（**在生产代码范围内**检索，排除 tests/scripts/文档）：

| TASK-07 的承重事实 | 实测结果 |
|---|---|
| `guard_tool_execution` 生产零调用方 | ✅ **成立** —— `agent/**` 仅 5 处：定义、2 处 docstring、1 处注释、`__all__` 导出 |
| `mark_foreign` / `mark_foreign_file` / `mark_subagent_output` 生产零调用 | ✅ **成立** —— `agent/**` 仅定义 + `__all__` + 一处文档描述 ⇒ **污点账确实从未写入** |
| `run_sandboxed` 零生产调用方 | ✅ **成立** —— 仅定义（`agent/subagent/sandbox.py:411`）+ 测试引用 |
| SSRF 零基础 / 169.254 被判 private | 审计期结论，未复核（TASK-07 §2.1 已给 `文件:行号` 证据） |

> ⚠️ **一处必须区分的表述**（否则子代理会误判）：`check_text` **有**生产调用方
> （`agent/context/assembler.py:315`、`agent/guardrails/safe_render.py:571`、
> `agent/guardrails/instruction_data.py:264`）。
> 所以准确说法是「**总闸门未接线** + **污点账从不写入 ⇒ `check_text` 恒放行**」，
> **不是**「机制完全没有调用点」。TASK-07 §2.2 的原表述已是对的（它写的是"总闸门零调用方"）。

### 8.5 施工顺序与串行约束

```
TASK-09（进行中，独占 pytest）
   ↓ 必须完成后才启下一个
TASK-04（关键路径起点）
   ↓
TASK-05（战略判据：关掉 LLM 后 Registry/HTTP/CLI 仍可用）
   ↓
TASK-06 ─┬─ 文件面重叠（tools/__init__.py / tool_gate.py / lines/models.py）
TASK-07 ─┘  ⇒ 06 与 07 **必须串行**，不可并行
   ↓
TASK-08（含已完成的 P0-1，需重核前提）
```

**为什么严格串行**：本轮实测证明并发 pytest 会（a）交叉污染测试结果、
（b）让时间类断言 **33× 膨胀**假红、（c）让 `git status` 无法按任务归因、
（d）曾导致首轮全量被 `os._exit(1)` 杀掉、580 个文件未执行。

### 8.6 长跑目标

已建立同会话目标 `goal-291c0b60-b535-4230-ab7d-d0ec796fd145`
（`max_goal_rounds=60`），覆盖 `TASK-09 → 04 → 05 → 06 → 07 → 08` 全序列。
用户已明确授权：**自主执行、遇问题即修、可代为决定、全部完成前不要停**。

---

## 9. 🔴🔴 2026-09-20 工作丢失事故（TASK-09 首轮产出被抹）

### 9.1 事实

**丢失**：`tests/unit/test_sensor_{registry,change_detector,sensor_reading,tags,novelty}_coverage.py`
共 **约 208 KB / 368 用例 / 647 断言**（我抽检过：断言密度 1.76、6 处 `pytest.raises`、
39 处 `parametrize`、零语法错误、无 `importlib.reload` / `time.sleep` / 真实 `BodySensor`）。

**为何不可恢复**：这些文件是**未跟踪**的（从未 `git add`）。已查证：
stash（4 条均无关）、index、全盘同名搜索、回收站、`git fsck --lost-found` —— **全部无效**。
⇒ **git 只保护进入过对象库的内容；未跟踪文件不在任何保护范围。**

### 9.2 根因链（很可能是这个）

`scripts/run_full_pytest.py` **在脏工作区会主动阻断**，提示「请先提交改动或隔离到独立 worktree」
⇒ 子代理为了让工具能跑，清理了未跟踪文件 ⇒ 抹掉了另一子代理的在飞产出。
（同一工作区当时有 **两个子代理**并行：TASK-09 写测试、TASK-04 改 101 个文件。）

> **这不是"某个子代理不听话"，而是系统性风险**：
> 我给 TASK-04 的提示词里确实写了"5 个 TASK-09 测试文件不是你的，别碰"，
> 但**没有给 TASK-09 的产出任何保护机制**（未跟踪 = 无保护），
> 也**没有在 TASK-00 里禁止清理命令**。⇒ 两条都补上了（D15 + TASK-09 顶部通报）。

### 9.3 已采取的处置

| # | 动作 |
|---|---|
| 1 | **抢救备份** `_ci_logs/_rescue_20260920_1010/`：`location.py`(45.8KB)、`backfill_capability_spec.py`(8.7KB)、`云枢能力清单盘点表.md`(63.5KB)、**`tracked-modifications.patch`(603.6KB / 99 文件)**、`HEAD.txt`、`git-status.txt` —— 保住 TASK-04 的 101 个文件改动 |
| 2 | **终止失效测量**：`--packages sensor` 那次跑到第 7/10 块时工作区已无新测试 ⇒ 数据无意义，已终止（PID 17416 / 17912） |
| 3 | **标记产物无效**：`_ci_logs/cov_task09_before` → **`INVALID_cov_task09_before_worktree_lost_tests`**（防后续误引用） |
| 4 | **新增纪律 D15**（`TASK-00`）：禁止 `git clean` / `checkout -- .` / `restore .` / `reset --hard` / 删他人文件 / 跑仓库清理脚本；并给出"工作区脏导致工具阻断"时的**正确替代路径**（先提交自己产出 → 或独立 worktree → 或跳过工具 → 或如实报告） |
| 5 | **`TASK-09` 顶部加事故通报**，要求重做时**每写完一个文件立刻 `git add`**（不等于 commit，但进入对象库即受保护） |
| 6 | 已同时**问询两个子代理**：是谁删的、是否还留有副本、原样贴出所跑命令 |

### 9.4 教训（应写进任何多代理协作规范）

1. **未跟踪文件 = 零保护**。多代理共享工作区时，**每完成一个可交付单元就 `git add`**；
   不要让未跟踪文件在工作区里停留。
2. **不要给"清理"留任何余地**：一旦某个工具因"工作区脏"而阻断，
   执行者的最短路径就是删东西 —— 必须**预先禁止**并给出替代路径。
3. **抢救优先级**：先救**未跟踪**文件（无保护），再救已跟踪改动（`git diff` 可得）。
   本次 `location.py` 与测试文件处境完全相同 —— 它能活下来纯属我先备份了。
4. **工具的正确行为在错误前提下会有害**（与"测试夹具冒充生产"同类）：
   `run_full_pytest.py` 的阻断是对的，但用"删别人的东西"来满足它就越界了。

### 9.5 当前状态

- `TASK-04`：✅ **已完成并提交 `807401ba`**（104 文件 / +15195 −269），详见 §10
- `TASK-09`：**首轮产出丢失，正在独立 worktree 重做**（`.worktrees/task09`，分支 `task09/p0-backtest`）

---

## 10. TASK-04 交付与独立验证（2026-09-20，提交 `807401ba`）

### 10.1 产出

| 项 | 内容 |
|---|---|
| `agent/lines/location.py`（新增 45.8 KB） | AST 驱动的 `location` 判定器：`_walk_chain` 调用链追踪 + 防环（`_MAX_DEPTH=6` / `_MAX_NODES=400`）、`DEFAULT_LOCATION="remote"`（保守安全侧）、`parse_executor` / `judge_executor_location` / `judge_skill_location` / `summarize_locations` |
| `agent/lines/models.py` | `ToolMeta` 扩展为完整 `CapabilitySpec`：`kind` / `location` / `owner` / `version` / `capability_id` / `tenant_id` / `namespace`，全部新字段有默认值（D2） |
| `data/tool_definitions/*.yaml`（91 个） | 补 `location` 声明 |
| `data/capability_manifest.json` | 114 条全部带 `location` + `location_source` |
| `scripts/sync_capability_manifest.py`（+275 行） | `--check` 模式：声明与事实不一致 ⇒ **非零退出** |
| `scripts/backfill_capability_spec.py`（新增） | 存量回填 |
| `agent/server_routes/routes_ui_panels.py` | **`tenant_id` 改为服务端派生**（原允许客户端指定 ⇒ 潜在跨租户越权，`TASK-04 §6.3`） |
| `docs/rfc/CapabilitySpec规范.md`（262 行）+ `云枢能力清单盘点表.md`（236 行） | 规格 + 盘点表 |
| `tests/unit/test_capability_spec.py`（523 行 / 49 用例） | |

### 10.2 我做的独立验证（**不是转述子代理**）

| 验证项 | 我的方式 | 结果 |
|---|---|---|
| **E2 全部能力有 location** | 我自己解析 manifest | **114 条**全部合法；**local 93 / remote 21**；`location_source` = `declaration: 91` / `skill_chain: 23`；**0 条缺失** |
| **E4 `--check` 负例** | 我篡改 `apply_patch` 的 location | 精确报出 `~ apply_patch: location: 'remote' → 'local'` 且**退出码 1**；恢复后退出码 0、工作区 0 改动 ✅ |
| 语法/导入 | 我自己 `ast.parse` + import | 6 个文件全 OK；`ToolMeta` 91 条且带 `location`；`apply_patch → local`（正确） |
| 回归 | 8 个相关测试文件 | **283 passed / 6 skipped / 0 failed** |

### 10.3 我在提交前修掉的**子代理遗留缺陷**

`agent/lines/models.py::kind` 原实现只判断 `tool_type == "script"`，其余**一律返回 `"tool"`**
⇒ `tool_type="skill"` 被错判成 `kind="tool"`，**与它自己的测试
（`test_kind_归并规则` 的 4 条断言）直接冲突**（该文件当时是 `48 passed / 1 failed`）。

修法：改为**恒等映射**（`tool`/`skill` 各自保持，只归并 `api→tool`、`script→skill`）。
核实过的安全性：91 个 YAML 的 `tool_type` **全为 `tool`**；生产侧唯一消费点是 `to_dict()`（无 `=="skill"` 比较）
⇒ **无行为变更**。修后 `49 passed`。

> 该缺陷**子代理没报告**（它被打断前未跑绿）。这是我"提交前必须自己跑一遍"的价值所在。

### 10.4 一处**看似不一致、实为有意设计**（已核实，勿误判）

| 层面 | 实现 | 判定 |
|---|---|---|
| 能力规格键（`capability_id` = `tenant_id:namespace:name@version`） | `tenant_id` 恒为 `"default"` | ✅ 能力目录是**全局**的，不随工作区变 |
| 数据操作租户（skills / memory / 回滚） | `derive_workspace_id(os.getcwd())` **服务端派生** | ✅ 数据按工作区隔离；客户端传的值只登记为"待校验声明"，不参与判定 |

### 10.5 TASK-06 / TASK-07 开工前预检（**已完成，前提全部成立**）

| 前提 | 实测 |
|---|---|
| **TASK-06** `tenant_id` / `namespace` 已在 `models.py`（默认 `default` / `yunshu`） | ✅ |
| **TASK-06** `risk: high` 工具数 | ✅ **13 个**（apply_patch / connect_mcp / decompress / edit / ext_install / ext_send_channel / ext_uninstall / fan_out / git / run_program / schedule_task / workspace_delete / write_file） |
| **TASK-06** 工具侧 `confirm_level`（L0–L3） | ✅ **在 91 个 YAML 里出现 0 次** ⇒ 确实不存在 |
| **TASK-06/07** `_hitl_boundary` 与开关 | ✅ `agent/tool_gate.py:757`；`CP_TOOL_GATE_APPROVAL_ENFORCE`（**默认开启**） |
| **TASK-07** `guard_tool_execution` 生产调用方 | ✅ **0 处**（仅有定义 / docstring / 注释 / `__all__`） |
| **TASK-07** `mark_foreign` / `mark_foreign_file` / `mark_subagent_output` | ✅ **生产 0 调用** ⇒ 污点账确实从未写入 |
| **TASK-07** `run_sandboxed` | ✅ **生产 0 调用**（仅定义 + 测试） |

> **一处易误判的表述**（已写入 TASK-07）：`check_text` **有**生产调用方
> （`agent/context/assembler.py:315`、`safe_render.py:571`、`instruction_data.py:264`）
> ⇒ 准确说法是「**总闸门未接线** + **污点账从不写入 ⇒ `check_text` 恒放行**」，
> **不是**「机制完全没有调用点」。

---

## 11. 进度快照（2026-09-20 17:20）

### 11.1 已完成并提交（3 个任务）

| 提交 | 任务 | 我的独立验证（非转述） |
|---|---|---|
| `807401ba` | **TASK-04** 能力规格正式化 + `location` | ✅ **E2**：我自己解析 manifest ⇒ 114 条全部合法（local 93 / remote 21）、`location_source` = declaration 91 / skill_chain 23、**0 兜底**。**E4**：我篡改一条 location ⇒ `--check` 精确报 `~ apply_patch: location: 'remote' → 'local'` 且**退出码 1**，恢复后 0。回归 **283 passed** |
| `a53194ed` | **TASK-05** Registry + Loader + 非 LLM 入口（**战略判据**） | ✅ **E1**：`verify_llm_off_entrypoints.py` **rc=0**；三链路 HTTP 200 / total=114 / degraded=False、CLI rc=0、**HTTP↔CLI 逐字段对拍一致**；且脚本**自带自证「覆盖 10 个入口（全部必然失败）」** ⇒ 排除"打桩无效导致假通过"。**E1b**：`audit_call_paths.py --check` 0 违规（扫 39 路径 / 例外 10 / 无腐化），套件含 **2 条负例**。测试 **136 passed**；相关面 **348 passed / 6 skipped / 0 failed** |
| `843db0d0` | **TASK-09** P0 补测第二批（5 模块） | ✅ **1484 passed / 0 failed**（主仓库与隔离 worktree 两处口径一致）。覆盖率：`change_detector` **99%**、`registry` **99%**、`novelty` **100%**、`sensor_reading` **100%**、`tags` **100%**（合计 690 stmts 仅 2 未覆盖）。审计期基线 71.6% / 74.4% / 72.3% |

**TASK-05 实测更正了我审计的两处结论**（我已独立核实成立）：
- `agent/async_executor.py:224` **不是**绕过 `tool_gate` —— `:253` 注释即写「经 `agent.tools.call` ⇒ 过 `tool_gate`」；
  真缺口是**身份跨线程丢失**（`contextvars` 在当前线程设的值到工作线程为空）
- `mcp_services/yunshu_mcp_server.py:476` 同样**过闸门**；"不做鉴权"指**协议层**（MCP 无内建认证）
- 另新发现**第 5 条**直调 `mcp_connector.py:217`

### 11.2 TASK-06：**半成品已归档 + 正在续做**

上一个子代理实现大半后被中断。我核实其质量**高**（值得续做而非重做）：

| 已实现 | 位置 |
|---|---|
| L0–L3 语义 + 派生函数 | `agent/lines/models.py:57` `CONFIRM_LEVELS`、`:61-64` 四级定义、`:114` `derive_confirm_level`（`:53-54` 说明**为何不复用技能域的 `APPROVAL_LEVELS`**） |
| 闸门接线 | `agent/tool_gate.py:615` `_confirm_level_outcome`、`:691` `_confirm_level_of`；`:361-362` 明写【D1】派生实现**只有一份** |
| **回滚开关 + 影子模式** | `:380` `CP_TOOL_CONFIRM_LEVEL_ENFORCE`、`:384` `CP_TOOL_CONFIRM_LEVEL_SHADOW` |
| SA（第四类主体） | `agent/security/service_account.py`（**956 行**）+ `tenant.py`（107 行） |
| 10 个高危工具 `permission_level: internal → restricted` | `data/tool_definitions/*.yaml` |

**它打红了 56 条既有测试**（`test_tool_gate.py` 13 failed / 48 passed；更广面 56 failed / 303 passed）。
**绝大多数是"断言旧契约"**（形如 `assert gate.check("write_file") is None`，即断言高危工具**不需要**审批）
⇒ **该改测试，不是改实现**。

**我的处置（原则：树永远保持绿）**：
1. `git stash push -u` 归档为 **`stash@{0}`**（含未跟踪文件）⇒ 工作区干净，验证 **359 passed / 0 failed**
2. `git stash apply`（**不用 `pop`**，保留安全副本）恢复工作区
3. 派新子代理完成剩余（56 条测试的契约更新 + 任务 B 裁决 + 补齐 §3/§5 未完成项）

**⚠️ 任务 B 是需要裁决的真实设计问题**（我已定位根因）：
`test_tool_gate.py::TestFailOpen` 有 9 条失败 —— 测试模拟"策略文件缺失"期望 fail-open（放行），
但 `write_file` 仍按 **YAML 元数据**（`risk: high`）命中 `confirm_level=L2` ⇒ 返回 `APPROVAL_REQUIRED`。
⇒ **问题：`confirm_level` 判定是否应受"策略文件缺失"影响？**
我的倾向是**不应**（它是 YAML 声明期策略，而 `tool_gate.py:463` 的 fail-open 针对"闸门自身故障"），
但已要求子代理**自己裁决并留痕**（代码注释 + 报告 + 测试锁定），**不把我的倾向当定论**。

### 11.3 剩余任务

| 任务 | 状态 |
|---|---|
| **TASK-06** | ✅ 已完成并提交 `61f53a96`，详见 §12 |
| **TASK-07** 安全接线 | ✅ **已完成并提交 `ecdcafe4`**（26 文件 / +4489 −141），详见 §13 |
| **TASK-08** 性能与可观测性 | ✅ **已完成并提交 `ab42a6b4`**（43 文件 / +12319 −49），详见 §14 |

---

## 13. TASK-07 交付与独立验证（2026-09-21，提交 `ecdcafe4`）

### 13.1 三件事（审计期生产调用方均为 0，现已接线）

| 项 | 实现 | 接线位置 |
|---|---|---|
| **SSRF 默认拒绝** | 新增 `agent/guardrails/ssrf_guard.py`（809 行）：**两道防线** —— 发请求前（语法 + IP 字面量 + DNS 后全 A 记录校验）+ patch `urllib3` `create_connection`，在**地址已定、socket 未建**那一瞬校验实际要连的地址（只在 `guard_scope()` 内生效，不拦死进程内合法回环）；重定向逐跳复检 | `agent/web/http_client.py` |
| **注入隔离 + 污点账** | 新增 `agent/guardrails/untrusted_ingest.py`；接线点在 `agent/tools/__init__.py::call()`（唯一汇聚点）⇒ 覆盖全部 91 工具 + 动态注册的 MCP 工具 | `tools/__init__.py::call()` |
| **沙箱收口** | `validate_command()` → `run_sandboxed()`；三档 `CP_SANDBOX_SHELL_MODE`（`off`/`shadow`/**默认 shadow**/`enforce`） | `agent/tools/shell_tools.py::execute_shell()` |

**两个关键设计论证**（子代理自己给的，我已核实合理）：
1. **注入层放在 `tool_gate` 之前** —— 理由是"不给注定不执行的调用挂审批单"（避免 TASK-05 已证的悬空挂单）；
   且**本层只出 deny、从不出 allow** ⇒ **不可能短路旧层**（这正是 TASK-06 踩过的坑）。
2. **受既有总开关约束**：`injection_guard_enabled() = CP_GUARDRAILS_GUARD_TOOL ∧ CP_TOOL_GATE_ENABLED`
   —— 直接吸取了 TASK-06 的 D-1 教训（新层不受总开关约束会导致**回滚失效**）。

### 13.2 我的独立验证（**全部亲自复测**）

| 验证项 | 我的结果 |
|---|---|
| **SSRF 12 类** | ✅ **12/12 符合预期** —— 拒绝 `169.254.169.254`（审计 Top1 原始场景）、环回、私网 A/C、十进制 `2130706433`、十六进制 `0x7f000001`、`::ffff:127.0.0.1`、`::1`、`0.0.0.0`、`file:`；放行 `example.com` / `api.tavily.com`（**无误伤**） |
| **沙箱拦在 spawn 前** | ✅ `rm -rf /`（权限系统黑名单）、`drop table users`（`\bdrop\s+table\b`）被拦并报出匹配规则；`echo hello` 放行 |
| **污点账** | ✅ `mark_tool_result` 入账后 `check_text(allowed)=False`；**对照组**普通文本 `=True`（证明不是恒拒） |
| 测试 | ✅ **747 passed / 16 skipped / 0 failed** |

**打红既有测试 13 处，全部为契约更新、无一条是实现真缺陷**；其中 9 处子代理**不改测试而是让代码保留稳定文案**（补精度不破契约，符合 D2）。

### 13.3 我修的一处**我自己造成的**工具缺陷

**任务书里写的 `python scripts/run_full_pytest.py --mode fast` 会直接崩** ——
该脚本原只读位置参数（`sys.argv[1..3]`），照抄任务书得到
`ValueError: invalid literal for int(): '--mode'`。**子代理照抄后撞上、浪费一轮。**

⇒ 已修 `scripts/run_full_pytest.py::_parse_argv`：**两种写法都接受**（8 组解析用例实测通过），
并更正 `TASK-00` 里的错误命令 + 写下教训「**任务书里的命令必须实跑一遍再写进去**」。

### 13.4 TASK-07 的 5 处分歧裁定（已落盘 `docs/closeout/TASK-07_裁定记录_20260920.md`）

| # | 分歧 | 裁定 |
|---|---|---|
| 1 | SSRF 用独立守卫 vs 改 `data/policies/policies.json` | **接受独立守卫** —— 更可靠（策略文件损坏时仍生效）、职责更清（IP 归一化是网络层事实不是配置） |
| 2 | 保留 `bash -c` vs 改白名单 argv | **接受保留** —— 一换就废（管道/重定向是 LLM 主要用法），且已有 `validate_command` + `run_sandboxed` + 三档开关纵深防御；登记白名单未接 |
| 3 | 边界词无 token 通路 ⇒ 硬拒绝 | **接受硬拒绝** —— "当前没有任何授权路径"就**应拒绝**，把缺失基础设施当默认许可是危险的；登记词面过宽需影子采集误报率 |
| 4 | 每轮 2 条 401 的归属 | **判为后端请求，非测试** —— 涉及该路径的测试都不产生 401（反证成立），且 D13 明确警告不要误判后端写入 |
| 5 | chunk_0 超时杀进程 | **接受（规模/争用）** —— 单跑该文件 43s / 冷进程全量走链 6.7s 远未到线；保留为未完全归因项，`_walk_chain` 规模退化登记给 TASK-08 观测 |

### 13.5 子代理纠正了我**两处**（都成立，已更正记录）

1. **TASK-07 文件里并没有我声称的「开工前必读」预检节** —— 我只把它写进了**派单消息**，
   没写进文件。我先前报告里说"已写入 TASK-07"是**记错了**。
2. **`run_full_pytest.py --mode fast` 会崩**（见 §13.3）。

---

## 14. TASK-08 交付与独立验证（2026-09-21，提交 `ab42a6b4`）

### 14.1 交付

| 项 | 内容 |
|---|---|
| 新增模块 | `agent/capregistry/toolset_hash.py`（能力集指纹）、`agent/capregistry/pruning.py`（工具定义裁剪保护）、`agent/startup_diagnostics.py`、`agent/timeout_budget.py` |
| 新增脚本 | **`scripts/check_perf_regression.py`（性能回归门禁，可非零退出）**、`scripts/check_handler_timeouts.py`（handler 超时上界扫描器）、4 个 bench 脚本、`scripts/repro_race_lost_update.py` |
| 文档 | `docs/perf/{既有性能数据盘点,可观测性实测,容量压测,toolset_hash_与裁剪保护}.md`（共约 187 KB）+ `baseline.json` |
| 待办登记 | `docs/task-08-d-待办登记.md`（**7 项"判定不在本任务修"的量化理由与建议修法**） |
| 测试 | 10 个新测试文件 |

**性能门禁的方法论**（`baseline.json` 的 `threshold_basis`，四步校准，均为本机实测）：
① 绝对 p50 跨 3 次运行波动 **1.3x~23x**（yaml 装载 22.94x、prune 14.04x）⇒ 无法定阈值；
② 参照负载分段归一化无效（参照自身波动 2.61x）；③ best-of-N(min) 绝对值 1.42x~1.56x；
④ **best-of-N(min) + 交错比值 1.16x~1.54x ⇒ 采用，比朴素 p50 稳 15 倍**。
阈值取 **1.60**（高于并发负载下最差观测 1.54x），并注明"安静机器上应重采基线并收紧到 1.25"。

### 14.2 我的独立验证

| 验证项 | 结果 |
|---|---|
| TASK-08 新增测试（10 文件） | ✅ **210 passed / 0 failed** |
| 设置注册表零缺口守卫（D5） | ✅ **27 passed** |
| 门禁 `--selftest` | ✅ **PASS** —— 证明"注入 20% 退化 ⇒ 必判 REGRESSION"（场景 a）与"无退化 ⇒ PASS"（场景 b） |
| 门禁默认跑 | ✅ `verdict: PASS`、`regressions: []` |
| 语法 | ✅ 33 个改动 .py **零失败** |

### 14.3 我修的一处真实缺陷

**`scripts/check_perf_regression.py --help` 直接崩溃**：
argparse 的 `_expand_help()` 对**每个** help 字符串做 `%` 格式化，而中文 help 含裸 `%`
⇒ `ValueError: unsupported format character '?' (0x9000)`。
已加 `_RawHelpFormatter` 关闭插值（本文件不需要 `%(default)s`）⇒ 修复后 `--help` 正常、
功能不变，且**今后任何 help 写 `%` 也不会再崩**。

### 14.4 未能独立验证的一项（如实标注）

**E9 可观测性的线上 `/metrics` 复测未做** —— 用户后端当前**未运行**（`127.0.0.1:5678` 连接被拒）。
我**不启动替代服务**（`app_server.py` 会 taskkill 抢占 5678，且这不是我该启的进程）。
⇒ E9 以 TASK-08 的 `docs/perf/可观测性实测.md`（72 KB）为准，
并保留审计期的既有结论：**`yunshu_http_request_duration_seconds` 确实存在且已填充**
⇒ **不得写"没有任何延迟直方图"**。

---

## 15. 🏁 六个任务全部完成（2026-09-21）

| 提交 | 任务 | 状态 |
|---|---|---|
| `807401ba` | TASK-04 能力规格正式化 + `location` | ✅ 我的 E2/E4 独立验证通过 |
| `a53194ed` | TASK-05 Registry + Loader + 非 LLM 入口 | ✅ **战略判据达标**（关掉 LLM 三链路可用） |
| `843db0d0` | TASK-09 P0 补测第二批（5 模块） | ✅ 覆盖率 99%/99%/100%/100%/100% |
| `61f53a96` | TASK-06 身份体系 + 工具侧 L0–L3 | ✅ 原 56 条失败集转 362 passed |
| `ecdcafe4` | TASK-07 SSRF + 注入隔离 + 沙箱 | ✅ 我的 SSRF 12/12、沙箱、污点账复测通过 |
| `ab42a6b4` | TASK-08 性能容量 + Router + 可观测性 | ✅ 门禁 --selftest PASS |

**全程纪律**：未 push；未运行 `app_server.py`；未用任何清理命令；
每个任务提交前都由我**独立复测**（不是转述子代理自评）。

### 15.1 累计产出的真缺陷（子代理抓到 + 我抓到）

**子代理抓到 15 个**（其中 **5 个是它们自己新写测试发现的，任务书未列出**）：
TASK-04 的 `kind` 归并缺陷（**我提交前修的**）、TASK-05 更正我两处审计结论、
TASK-06 的 D-1~D-5、TASK-07 的路径逃逸"文件不存在即绕过"、TASK-08 的 7 项待办。

**我抓到并修掉 6 个**：
`models.py::kind`（TASK-04 遗留）、`importlib.reload` 顺序污染（**我自己引入的**）、
`TestPerformance` 缺 `serial`、全仓扫描守卫缺 `slow`、`llm_monitor_singleton` 补丁泄漏、
`run_full_pytest.py` 参数崩溃（**我的 bug**）、`check_perf_regression.py --help` 崩溃。

### 15.2 遗留（已登记，未做）

1. **生产审计链污染未清理** —— 20,074 条含 253 条 `probe_approval_e2e_tool` 等夹具数据；
   根因已修（`get_audit_chain()` 不遵守 `AUDIT_DB_PATH`），但**清理需重建哈希链，超出授权**
2. **TASK-08 的 7 项待办**（见 `docs/task-08-d-待办登记.md`）：`task_timeout` 语义缺口、
   reranker `stderr.read()` 无上界、MCP 可重试异常集合过窄、`register_all_routes` 死代码引用已删模块、
   检查器未接 CI、81 个 handler 仅靠全局上界、丢更新复现未纳例行
3. **TASK-08 未覆盖项**：E9 线上 `/metrics` 未复测（后端未运行）
4. **`CP_GUARDRAILS_FOREIGN_TAINT` 注册表默认值（False）与代码默认值（True）不一致**
5. **`tests/unit/test_audit_logger_comprehensive.py:200`** 每跑一次全量就往生产审计链 +1 条
6. **`stash@{0}`** 存有 TASK-06 历史 WIP 安全副本（可 `git stash list` 查看）

---

## 12. TASK-06 交付与独立验证（2026-09-20，提交 `61f53a96`）

### 12.1 核心设计（可回滚、可影子）

| 项 | 内容 |
|---|---|
| **L0–L3 派生单一真相源** | `agent/lines/models.py::derive_confirm_level`；`tool_gate.py:361-362` 明写【D1】只做惰性转出、**不复制判定分支** |
| **开关从属** | 总开关 `CP_TOOL_GATE_APPROVAL_ENFORCE` **⊃** 分级开关 `CP_TOOL_CONFIRM_LEVEL_ENFORCE`；另有影子模式 `CP_TOOL_CONFIRM_LEVEL_SHADOW` |
| **派生规则**（从严者先判） | `risk:critical`\|`effect:extend`\|`plane:govern` ⇒ **L3**（10 个）；`risk:high` ⇒ **L2**（10）；`effect:write`\|`execute`\|`risk:medium` ⇒ **L1**（37）；其余 ⇒ **L0**（34） |
| **91 个 YAML 零改动** | 裁决**派生**而非显式声明：`confirm_level` 是 `risk/effect/plane` 的函数，再写一份即第二口径；override 机制已就位且**禁止静默降级**（"更宽且无理由"的声明直接丢弃并留痕） |
| **13 个 `risk:high` 归类** | 10 个 L2（write_file/edit/apply_patch/decompress/workspace_delete/git/run_program/fan_out/ext_send_channel/schedule_task）+ **3 个抬到 L3**（`connect_mcp`/`ext_install`/`ext_uninstall` —— 均为 `plane=govern` + `effect=extend`，改云枢自身能力集） |
| **SA（第四类主体）** | `agent/security/service_account.py`（956 行）+ `actor_matrix.py` 补 `service_account` **整列**（此前缺 39/52 格）；`app_server.py` 显式安装预授权钩子 |

### 12.2 修复的 5 个真缺陷（**其中 3 个是新写测试抓到的，任务书未列出**）

| ID | 缺陷 | 严重性 |
|---|---|---|
| **D-1** | `CP_TOOL_GATE_APPROVAL_ENFORCE=0` **管不住**新分级层 ⇒ **既有回滚开关失效**、`tests/conftest.py` 会话基线被穿透（**30 条既有测试连锁变红**） | 高（回滚能力） |
| **D-2** | **SA 预授权从未生效**：同一个 `None` 既表示"放行"又表示"继续挂单" ⇒ SA 命中 scope 后**照样被拒**，却**先落了 `decision=preauthorized` 的审计**（**审计与事实相反**，比没有审计更坏） | 高 |
| **D-3** | **确认决策审计从未落链**：`append(source="tool_gate")` 非合法来源 ⇒ 每次抛错被宽 except 吞成一行 ERROR（E9 **表面已实现、实际 0 条记录**） | 高 |
| D-4 | 能力面误用 workspace-hash ⇒ `/capabilities/tools` 返回 **0 条**、`describe` 全 404 | 中 |
| D-5 | `scripts/backfill_tool_callability.py` 手写**第二份**审批规则（D1 违规）；而唯一能"修好" `--check` 的动作是**把 10 个 YAML 回滚成 `internal`** ⇒ **用过期副本撤销安全收紧** | 中 |

**另修**：伦理硬规则被新层**短路成死代码**（分级层排在伦理兜底之前，而伦理只作用于 `execute`/`extend`，
这两类至少 L1）⇒ 把伦理命中**合并进**确认理由：拦截结果不变，但人看到的理由从"常规写文件"
变成"**这是关机命令**"。这正是 `TestEthicsRulesAreLive` 原本要防的事。

### 12.3 我的独立验证

| 验证项 | 方式 | 结果 |
|---|---|---|
| 原 56 条失败集 | 我自己跑 | **362 passed / 6 skipped / 0 failed** ✅ |
| 广面（身份/权限/安全/注册/规格/设置） | 我自己跑 10 个文件 | **441 passed / 0 failed** ✅ |
| **审计链隔离修复** | 跑 113 条审批/审计测试前后对比生产链 | **行数 20074 不变、mtime 不变** ✅（修复前每次测试都会写进生产链） |

### 12.4 🔴 发现并登记的遗留：**生产审计链被测试数据污染**

`data/audit/audit_chain.db` **20,074 条**记录中含大量**测试夹具数据**：

| 类型 | 条数 |
|---|---|
| `tool_call:probe_approval_e2e_tool` | **253** |
| `probe_*` | 35 |
| `my-skill` / `skill-p` / `env:SAME_KEY` / `env:CONCURRENT_KEY_*` 等 | 各约 **240** |
| `__sample__`（**本次子代理为生成 E9 样本追加**） | 3 |

**根因**：`get_audit_chain()` 无参时落到**硬编码默认路径**，不遵守 `AUDIT_DB_PATH`
⇒ 测试的审计隔离**一直是失效的**。子代理已修（改走唯一入口 `facade.record()`），
我实测修复有效。

**遗留**：链中**已积累**的污染记录**未清理** —— `audit_chain` 是 **append-only 哈希链**，
删除中间记录会破坏 `prev_hash`/`self_hash` 链式完整性。**清理需重建链，超出本次授权**
⇒ 登记为待办，交由仓库负责人决定。
（子代理为此**主动披露**了它追加的 3 条记录，做法正确。）

### 11.4 本会话新增纪律（`TASK-00` 已扩到 D16）

| # | 内容 |
|---|---|
| **D11** | 禁止 `-o addopts=""`（会抹掉 `--import-mode=importlib` ⇒ 假 `import file mismatch`、跑 0 条用例） |
| **D12** | 不得用夹具形状代替生产形状（"测试夹具冒充生产"：93 条补测全绿却漏检真实 100% 失效） |
| **D13** | pytest 必须串行，且不得误伤用户运行数据（归属用 `(size, mtime_ns)` 窗口差集） |
| **D14** | 不要用裸 `pytest` 单进程跑全量（实测退化到 **7.5 分钟/1%**、句柄 34,380、116 分钟无结果）⇒ 用分块 runner |
| **D15** | 🔴 禁止 `git clean` / `checkout -- .` / `restore .` / `reset --hard` / 删他人文件 / 跑清理脚本（**工作丢失事故后新增**） |
| **D16** | "串行"只约束全量/覆盖率，**不约束小集合**（有子代理因误用该纪律**空等 60 分钟**） |

### 11.5 工具与流程改进（本轮落地）

| 项 | 内容 |
|---|---|
| `.gitignore` | 补 `pytest_chunks/`（runner 产物，一次全量留 3.3MB 日志且原不被忽略） |
| `scripts/run_full_pytest.py` | 两处 pytest 调用补 `-p no:randomly`（本仓装了 `pytest-randomly`，会打乱文件内顺序） |
| `TestPerformance` | 补 `@pytest.mark.serial`（并行下 33× 膨胀假红） |
| 全仓扫描守卫 | 标 `@pytest.mark.slow`（曾是"首轮全量杀手"：余量仅 2.6×，被杀则丢 580 文件） |
| 熔断器边界用例 ×3 | 改注入时间（余量 0.1~0.2s ⇒ 并发假红） |
| 独立 worktree | **TASK-09 在 `.worktrees/task09` 执行**，产出与主工作区物理隔离（事故后措施，已验证有效） |
| 抢救备份 | `_ci_logs/_rescue_20260920_1010/`（TASK-04）、`_ci_logs/_rescue_task06/`（TASK-06） |

---

## 16. 🔴 我在收尾阶段自己造成的两次中断（如实记录）

**背景**：六个任务全部提交后，我启动了一次全量回归做最终确认。**两次都因我自己的操作失败**。

### 16.1 第一次：我删除 `_t08_logs/` 与 pytest 收集期发生竞态

我在清理临时文件时执行了 `Remove-Item _t08_logs -Recurse`，随后启动的全量回归在**收集期**报错：

```
ERROR collecting test session
FileNotFoundError: [WinError 2] 系统找不到指定的文件。: 'C:\Users\Administrator\agent\_t08_logs'
```

**后果**：四个分块**全部只跑 37 秒、`collected 0 items / 1 error`、0 条用例执行**
⇒ 一次"看似全量、实则空跑"的回归。**若我没看分块摘要，会误以为"全量跑完了"**。

**性质**：瞬时报错（重跑不再复现）⇒ 我的删除动作与 pytest 收集期的目录扫描发生**竞态**。
`_t08_logs/` 是 TASK-08 基准脚本的输出目录（`scripts/bench_router_complexity.py` 的 docstring 引用它）。

**处置**：已重建该目录并写入 `README.md` 说明（原始 JSON 属可再生中间产物；
正式结论已归档到 `docs/perf/可观测性实测.md` 与 `baseline.json`）。

> **教训**：**在 pytest 可能扫描的路径下删除目录是有风险的**。
> 这与 D15 同源（D15 禁的是删**他人**文件；这次是我删**自己**的临时目录，但落在了收集面内）。
> ⇒ **清理只应针对明确的产物目录**（如 `pytest_chunks/`、`_ci_logs/`），
> 且**应在启动测试之前完成、或测试之后再做**，不要与测试并行。

### 16.2 第二次：我的工具调用 600 秒超时把回归打断

第二次重跑我用普通 `pwsh` 调用（工具超时上限 600 秒），而全量需约 45 分钟
⇒ **命令在第 600 秒被中断**，各 chunk 都停在半途、无摘要、`_retry.txt` 未产出。

**处置**：改为**后台任务**（`run_in_background: true`，不受工具超时限制）重跑。

> **教训**：**预计超过 10 分钟的命令一律用后台任务**。
> 这条与 D14（别用裸 pytest 单进程跑全量）是同一族：**都是"用错的执行方式导致拿不到结果"**。

### 16.3 这两次中断的共同点（值得写进方法纪律）

| 次 | 我的动作 | 造成的假象/损失 |
|---|---|---|
| 1 | 删了收集面内的目录 | **"全量跑完了"的假象**（实为 0 条执行） |
| 2 | 用普通调用跑 45 分钟的任务 | 600 秒被中断，浪费一轮 |

⇒ **两次都是"执行方式"问题，不是代码问题**；两次都**在分块摘要里暴露**
⇒ 印证本会话反复出现的纪律：**必须逐块校验"是否正常收尾"，不能只看"命令返回了"**。

---

## 17. 全量回归发现的两个真问题（已修，提交 `e61634b3`）

### 17.1 两处调用点未登记（**重构导致登记锚点漂移**）

| 处 | 事实 | 处置 |
|---|---|---|
| `agent/skills_mgmt/mcp_adapter.py::_build` | **TASK-08** 给 MCP 加超时/重试预算时，把 SDK 调用路径重构成了 `_build()` 内层闭包（`:372`）⇒ 扫描器识别为新调用点（TASK-05 登记的旧符号 `_call_tool` 已不存在） | 登记为 `reachable=False` —— **实测 `import mcp` 抛 ImportError**（`No module named 'mcp'`）⇒「潜在风险、当前不可达」，并注明"一旦装上 SDK 即生效" |
| `mcp_services/test_mcp_windows.py::test_error_mode` | 手工 MCP 客户端兼容脚本；**自 2026-06-29 起未被改过** ⇒ 不是新增直调，而是判定条件变化后才被识别 | 登记，并**如实说明"原漏检原因未逐行追查"**；`pytest.ini testpaths=tests` ⇒ 不被收集，且 `mcp_services/` 不在硬失败范围 |

### 17.2 🔴 一个**静默失效的负例**（本次最值得记的一条）

`test_capregistry_callpaths_routes.py::test_负例_未登记的直调_非零退出` 原以
`agent/skills_mgmt/mcp_adapter.py::_call_tool` 为"受害条目"，**但该符号已被 TASK-08 的重构删除**
⇒ 用例变成"删掉一个**本就不存在的**条目" ⇒ 未登记数仍为 0 ⇒
**负例测不出非零退出、静默失效**（实测 `assert 0 == 1`）。

**已改用当前真实存在且会被扫到的 `_build`。**

> **教训**：**负例必须锚在"扫描器当前真能命中的符号"上。**
> 重构会让负例**静默失效** —— 而"负例失效"恰恰是最危险的：它让人以为防护还在。
> 这与本会话早前那批"假绿"（测试夹具冒充生产、探测分支不可达）是同一族：
> **防护机制本身也需要有"它是否还在生效"的证据。**

### 17.3 修复后的验证

| 验证项 | 结果 |
|---|---|
| `audit_call_paths.py --check` | ✅ **✓ 无未登记直调**（扫 39 条路径、例外 12 条、无腐化）、**exit 0** |
| `test_capregistry_callpaths_routes.py` | ✅ **23 passed / 0 failed** |
| `test_retry_budget.py` | ✅ **17 passed**（全量里那条失败是负载下的瞬时，隔离跑全绿） |

---

## 18. ⚠️ 未修的性能项：AST 全仓扫描在 4 路争用下超时（**已量化并登记**）

### 18.1 现象（两次全量复现）

`run_full_pytest.py 4 4 fast` 两轮都在 **chunk_0 与 chunk_3** 出现
`+++++++++ Timeout +++++++++`（`pytest-timeout` 的 `os._exit(1)`）：

| chunk | 超时处 | 调用栈 |
|---|---|---|
| chunk_0 | `scripts/audit_call_paths.py:510 module_bindings` | `ast.walk` → `generic_visit` |
| chunk_3 | `agent/lines/location.py:750 _walk_chain` → `:376 _module_boundary_primitives` | `ast.walk` |

### 18.2 量化（**我的实测**，不是推测）

| 度量 | 值 |
|---|---|
| `audit_call_paths.scan()` 单次（单进程、空载） | **14.9 s** |
| 同一进程第二次 scan | **14.6 s** ⇒ **无缓存**（每次全仓 AST 解析） |
| 全仓扫描文件数 | 按 `_SCAN_ROOTS`（`agent`/`mcp_services`/`plugins`/`scripts`/`cloudshu`）计 |
| 4 路并行时的放大 | 实测超过 **120 s** 预算 ⇒ 被 `os._exit(1)` 杀进程 |

### 18.3 为什么**未修**

1. **不是正确性问题**：`--check` 单独跑正常（exit 0）、相关测试隔离跑 23 passed；
   两次全量**均 0 条 FAILED**（chunk_1 `6099 passed`、chunk_2 `5516 passed`）。
2. **改 `scan()` 加缓存是独立改动**：需要"按仓库状态（mtime 集）失效"的设计与验证，
   属行为变更；**本轮已接近尾声，不宜在未充分验证的情况下改扫描器语义**。
3. **runner 的"正常收尾"判定是误判**：它在 chunk_0/3 报"已跑完"，
   实际是超时被杀、**没有摘要**。这是 runner 的**真缺陷**（`chunk_log_status` 未识别 `Timeout` 标记）。

### 18.4 建议修法（登记为待办）

1. **给 `scan()` 加进程级缓存**，按"被扫描文件的 (path, mtime, size) 集合"失效 ——
   同一进程内重复 scan 可从 14.9s 降到接近 0（`:44` 的 module 级 fixture 与本文件的
   多次 `main()` 调用都会受益）；
2. **修 runner 的收尾判定**：`chunk_log_status()` 应把 `Timeout` 标记视为**未正常收尾**
   ⇒ 触发逐文件补跑或至少如实报出"该块未跑完"；
3. 可选：把 `audit_call_paths.scan` 与 `location.walk_chain_all_executors` 纳入
   TASK-08 的性能门禁基线（当前 `baseline.json` 已有 `walk_chain_all_executors` 指标，
   但默认未测；`--with-walk-chain` 才测）。
