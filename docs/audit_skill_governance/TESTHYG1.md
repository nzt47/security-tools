# TESTHYG-1 · 测试基础设施：跨用例污染（5 条）与负载敏感计时（1 条）

- **卡号**：TESTHYG-1（测试基础设施卡）
- **HEAD**：5c9ace10（分支未动；全程未 git add / git commit）
- **工作区**：44 张卡的未提交改动照旧存在
- **零出网预算**：本卡全程未主动访问网络；**但发现并修掉了一处会真的发起外网连接**的测试基础设施缺陷（见 §6.4）
- **改动文件（只有两个）**：tests/conftest.py（+104 / −1）、tests/unit/test_tools_prompt_alignment.py（+83 / −6）；另新增本报告
- **交付物**：本文件

---

## 0. 结论速览

| # | 项 | 结论 |
|---|---|---|
| ① | A 的污染源 | tests/unit/test_server_routes_registration_inventory.py:153（module 级夹具 real_url_paths）等 **5 处 module 级夹具** import app_server → app_server.py:50 get_env_config_manager().reload() → agent/env_config_manager.py:387 os.environ[k] = v，把**整份 .env（实测 140 个键）**灌进 os.environ |
| ② | 根因（不是「某条用例脆弱」） | CP_ENV_FILE 重定向此前**只在函数级夹具**里设置，而 pytest **高 scope 夹具先于低 scope 夹具** setup ⇒ module 级夹具里那次 import app_server 时重定向尚未生效 |
| ③ | 修法 | 把重定向**提前到 conftest 导入期**（会话级「地板」）+ 会话级自证夹具；再补齐 CI 本来就有的离线基线两条键。**污染源自己不再写** |
| ④ | 5 条红的归属 | **全部由同一处污染解释**（ORCHESTRATOR_REJECT_ENABLED / SKILLS_FUSION_WEIGHT_BM25 / ERROR_REPORTING_WEBHOOK_URL / EVOLUTION_DEFAULT_EVALUATOR 四个键，值全部逐字来自 .env） |
| ⑤ | 改前红 / 改后绿 | 5 failed, 108 passed → 113 passed（同一最小文件组合、同一顺序） |
| ⑥ | 反向自证 | 去掉修复 ⇒ 精确复现同 5 条红 + 钩子重新记到 2 次受关注键的写入 |
| ⑦ | B（计时 flaky） | 本地预算改为**校准式有界余量**（∈[1,3]，与 CI 的 3× 同口径）：空载逐字不变、满载最多 ×3。仿真「整机慢 8×」：**改前红 62.3ms/50ms → 改后绿**；慢 25× 仍红（护栏没被放宽到失效） |
| ⑧ | 定向回归 | **285 passed**（10 个文件，含污染源文件、3 个受害文件、隔离契约、reload 契约、B 文件） |
| ⑨ | 未验证 | **全量 22637 条跑**（被硬约束禁止）；B 的「全量不再红」只能给出机制与仿真证据 |
| ⑩ | 残留物 | 仓库内：只有上述 2 个被改文件 + 本报告；探针与日志全部在仓库外 %TEMP%\testhyg1\；**本卡启动的进程已全部结束**（另有 1 个并发 pytest 属别的卡，非本卡启动、未干预）；已清理发现的地板目录残留 |

---

## 1. 第 1 步：逐个定位污染源（证据链，不是推断）

### 1.1 方法：给 os.environ.__setitem__ 打脏写钩子

按卡片建议做**最直接**的那一条：包一层 os._Environ.__setitem__，记录**首次**写入某键的完整调用栈。

探针在仓库外：C:\Users\Administrator\AppData\Local\Temp\testhyg1\envdirty.py
（用 PYTHONPATH + pytest -p envdirty 装载，**不改仓库任何文件**）。

先排除「污染发生在收集期导入」这一假设（tests/unit --collect-only，22994 条，73s，
钩子全程只记到 conftest 自己的 6 个基线键）⇒ **污染不在收集期**。

### 1.2 证据链（原始调用栈，节选）

    $ python -m pytest -p envdirty tests/unit/test_server_routes_registration_inventory.py -q -p no:randomly

    ### WATCH FIRST-SET SKILLS_FUSION_WEIGHT_BM25 = '0.2'
      File "...\_pytest\fixtures.py", line 1005, in call_fixture_func
        fixture_result = fixturefunc(**kwargs)
      File "C:\Users\Administrator\agent\tests\unit\test_server_routes_registration_inventory.py", line 153, in real_url_paths
        import app_server  # noqa: PLC0415 真实入口，与生产同一份注册代码
      ...
      File "C:\Users\Administrator\agent\app_server.py", line 50, in <module>
        get_env_config_manager().reload()
      File "C:\Users\Administrator\agent\agent\env_config_manager.py", line 387, in reload
        os.environ[k] = v

    ### WATCH FIRST-SET ORCHESTRATOR_REJECT_ENABLED = 'false'
      （同一条栈，逐字相同）

同一次运行里钩子记录了 **140 个「首次出现的新键」**（即整份 .env 被搬进 os.environ）。

### 1.3 一句话链条

> **tests/unit/test_server_routes_registration_inventory.py:153（scope="module" 夹具 real_url_paths）
> → import app_server → app_server.py:50 get_env_config_manager().reload()
> → agent/env_config_manager.py:387 os.environ[k] = v（整份 .env）**

### 1.4 为什么原有的 CP_ENV_FILE 重定向挡不住（这条是关键）

tests/conftest.py:880 的 _isolate_dotenv_target 是 scope="function" 的 autouse 夹具，
它把 CP_ENV_FILE 指向**本用例 tmp 目录**；而 pytest 的**高 scope 夹具先于低 scope 夹具 setup**。于是：

| 触发点 | 触发时的 scope | 触发时 CP_ENV_FILE | 后果 |
|---|---|---|---|
| test_background_tasks_routes.py:140（**函数体内**）import app_server | function | 已指向 tmp 空文件 | OK：reload() 读到空文件，不污染 |
| test_server_routes_registration_inventory.py:153（**module 级夹具**） | module | **尚未设置** | 坏：读到**仓库真实 .env** |

**同一形状共 5 处**（全部是 module 级夹具，全部会污染）：

| 文件:行 | 夹具 |
|---|---|
| tests/unit/test_server_routes_registration_inventory.py:153 | real_url_paths（实测触发点） |
| tests/unit/test_graceful_shutdown_persist.py:28 | app_server_mod |
| tests/unit/test_health_retrieval_endpoint.py:31 | flask_app |
| tests/unit/test_legacy_memory_routes.py:94 | real_app |
| tests/unit/test_tool_callability.py:567 | real_app（slow 车道） |

### 1.5 「污染源」与「受害用例」对账（四个键全部来自 .env）

| 受害用例 | 被换掉的判据 | .env 里的值 |
|---|---|---|
| test_settings_registry.py::TestConfigPathDrivesDisplayNotRuntime::test_no_declared_key_is_env_pinned_here | pinned == [] | ORCHESTRATOR_REJECT_ENABLED=false（.env:221）、SKILLS_FUSION_WEIGHT_BM25=0.2（.env:148） |
| 同上 ::test_every_declared_path_flips_default_to_config | 来源必须是 default | 同上（实测报 'env' == 'default' 失败） |
| test_error_reporting_config.py::TestErrorReportingConfig::test_get_config_default | config["webhook"]["url"] == "" | ERROR_REPORTING_WEBHOOK_URL=https://hooks.slack.com/test（.env:80） |
| test_evolver_real_eval.py::TestRealEvalDistinguishable ×2 | 「不传 evaluator ⇒ 走启发式」 | EVOLUTION_DEFAULT_EVALUATOR=real（.env:337） |

**⇒ 5 条红是同一个根因，不是 5 个问题。**
（与前序卡上报的线索一致，但前序卡猜的 settings.bootstrap.apply_overrides() **不是**本例机制：
实测栈落在 reload() 上；apply_overrides() 在覆盖层文件不存在时是 no-op，且 CP_UI_SETTINGS_PATH
已被会话级隔离 —— 见 tests/conftest.py:263-271 的既有先例。）

---

## 2. 第 2 步：治本修法（不动生产代码）

### 2.1 改了哪三处（全在 tests/conftest.py）

1. **会话级「地板」**（tests/conftest.py:83-86，conftest **导入期**执行，早于收集、早于一切夹具）：

       _DOTENV_FLOOR_DIR = tempfile.mkdtemp(prefix="pytest_dotenv_floor_")
       _DOTENV_FLOOR_FILE = os.path.join(_DOTENV_FLOOR_DIR, "isolated.env")
       os.environ["CP_ENV_FILE"] = _DOTENV_FLOOR_FILE
       atexit.register(shutil.rmtree, _DOTENV_FLOOR_DIR, ignore_errors=True)

   ⇒ reload() 读到的是**空的隔离 .env**，**污染源自己不再写 os.environ**。
   函数级 _isolate_dotenv_target 原样保留（逐用例隔离不丢），它的 teardown 还原到的 prev
   现在正是这个地板值。

2. **会话级自证夹具** _assert_dotenv_redirect_floor（tests/conftest.py:846-876）：按 scope 规则它
   **先于 module 级夹具** setup，在 import app_server 之前就钉住不变量 —— 断言 CP_ENV_FILE 已设置、
   且 EnvConfigManager()._env_file **不是**仓库 .env。若有人把地板删掉，它会在**第一个用例**上
   直接失败并点名（实测见 §5.1），而不是等到全量跑里以「5 条单跑全绿的红」浮现。
   会话收尾时它再删一次地板目录（与 atexit 双保险）。

3. **离线基线**（tests/conftest.py:88-102）：HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1。
   理由见 §6.4 —— 这是地板生效后**必须**补齐的一环，也是本卡最意外的一处发现。

### 2.2 为什么这是「治本」而不是「掩盖」

| 判据 | 本修法 |
|---|---|
| 是否让**污染源自己**不再污染？ | 是。地板让 reload() 的目标是**空文件**，那 140 次 os.environ[k] = v **根本不再发生**（钩子日志 140 → 30，受关注键 2 → 0，见 §4） |
| 是否改断言 / 放宽断言绕过？ | 没有。**没有动任何一条断言**；5 条受害用例的判据逐字未改 |
| 是否用 -p no:xxx 或跳过绕开？ | 没有 |
| 是否只是「把值又改回去」（写后还原）？ | 比那更强：**写本身没有发生**。写后还原只是退路，本卡走的是「源头不写」 |
| 是否与仓库既有口径一致？ | 一致。这正是 _isolate_dotenv_target 已确立的「**重定向真实 I/O，而不是 mock 掉写入**」口径（tests/conftest.py:808-826；tests/unit/test_env_isolation_p0.py 明文禁止「把写入变成 no-op」的假修法）。本卡只是把生效时机从「逐用例」提前到「会话开始前」 |
| 改动面 | 只动 tests/conftest.py 与 B 的测试文件；**生产代码一行未动**（app_server.py 的 .env 加载是生产语义，不该为测试让步 —— 沿用 tests/conftest.py 已写明的既定判断） |

**为什么用「无条件覆盖」而不是 setdefault**：不变量是「测试进程任何时刻都不得把仓库 .env 读进
os.environ」。继承来的 CP_ENV_FILE 完全可能就是仓库 .env 本身（那正是本缺陷的形态），setdefault
会把缺陷原样留下。函数级 _isolate_dotenv_target 本来就是无条件覆盖，故不引入新语义。

**为什么现在可以把本地对齐 CI**：仓库根 .env 是 **.gitignore:12 忽略的未跟踪文件**
⇒ CI 的干净 checkout 里**根本没有 .env**，CI 上的测试进程从来没见过这 140 个键。
本修法是让本地更接近 CI，而不是更远。

---

## 3. 第 3 步①②：改前红 / 改后绿（同一最小文件组合，原始输出）

**最小复现组合**（1 个污染源文件 + 3 个受害文件，-p no:randomly 固定顺序，污染源在前）：

    python -m pytest tests/unit/test_server_routes_registration_inventory.py ^
                     tests/unit/test_settings_registry.py ^
                     tests/unit/test_error_reporting_config.py ^
                     tests/unit/test_evolver_real_eval.py -q -p no:randomly

### 3.1 改前（before_red.log，原始输出）

    E   AssertionError: 本进程环境里已设置这些开关的 env：['ORCHESTRATOR_REJECT_ENABLED', 'SKILLS_FUSION_WEIGHT_BM25'] —— 此时 env 层遮蔽 config 层，逐键对拍无意义（须在干净环境跑）
    E   assert ['ORCHESTRATO..._WEIGHT_BM25'] == []
    E   AssertionError: ORCHESTRATOR_REJECT_ENABLED 在配置层缺位时来源不是 default：env
    E   AssertionError: assert 'https://hooks.slack.com/test' == ''
    E   AssertionError: assert 0.0 > 0.0

    FAILED tests/unit/test_settings_registry.py::TestConfigPathDrivesDisplayNotRuntime::test_no_declared_key_is_env_pinned_here
    FAILED tests/unit/test_settings_registry.py::TestConfigPathDrivesDisplayNotRuntime::test_every_declared_path_flips_default_to_config
    FAILED tests/unit/test_error_reporting_config.py::TestErrorReportingConfig::test_get_config_default
    FAILED tests/unit/test_evolver_real_eval.py::TestRealEvalDistinguishable::test_heuristic_path_backward_compatible
    FAILED tests/unit/test_evolver_real_eval.py::TestRealEvalDistinguishable::test_real_eval_distinguishable_from_heuristic
    ============ 5 failed, 108 passed, 8 warnings in 93.93s =============

> **与卡片给的失败清单逐条对齐**：5 条，文件与用例名完全一致 ⇒ 复现成立。

### 3.2 改后（after_green2.log，同一条命令、同一顺序）

    ================= 113 passed, 8 warnings in 86.02s (0:01:26) =================

同一组合：5 failed, 108 passed → 113 passed。

### 3.3 单独口径（对照卡片所述「单跑 56 passed 全绿」）

| 运行 | 结果 |
|---|---|
| 改前：test_settings_registry.py **单文件** | 56 passed（污染只在组合/全量下显形，与卡片描述一致） |
| 改后：同上 | 仍 56 passed（无回归） |

---

## 4. 第 3 步④：污染源「不再写」的直接证据（钩子日志）

同一个脏写钩子、同一个最小组合，**唯一变量 = 地板在不在**：

| 运行 | 钩子记录的新键总数 | 受关注键（ORCHESTRATOR_REJECT_ENABLED / SKILLS_FUSION_WEIGHT_BM25）被写次数 |
|---|---|---|
| 改前 | **140** | **2** |
| 改后 | **30** | **0** |

改后剩下 30 条全部是 conftest 自己的基线键（PYTHONUTF8 / OMP_NUM_THREADS / CP_UI_SETTINGS_PATH /
AUDIT_*_PATH / HF_HUB_OFFLINE …），**没有一条来自 .env**。

另有一条**常驻**自证（不依赖外部探针）：会话级夹具 _assert_dotenv_redirect_floor 在每个会话的
第一个用例之前断言 EnvConfigManager()._env_file != <repo>/.env。它不是「断言测试自己写的东西」：
EnvConfigManager 的目标正是污染源 app_server.py:50 → env_config_manager.py:387 读取的那一个值
⇒ 该断言 = 「污染源读不到真实 .env」的机器可校验形式。

---

## 5. 第 3 步：反向自证（去掉修复 ⇒ 重新变红）

### 5.1 只去掉「地板」那一行

结果：**113 errors**（不是 5 failed）—— 因为**自证夹具当场点名**：

    （111 个用例 setup ERROR：CP_ENV_FILE 未设置 / 地板失效）
    ============================= 113 errors in 5.07s =============================

⇒ 说明「地板 + 自证」这一对确实构成**护栏**：破坏它不会静默退化成「5 条偶发红」。

### 5.2 把修复**整段**去掉（地板 + 自证夹具同时停用）—— reverse_red2.log

    FAILED tests/unit/test_settings_registry.py::TestConfigPathDrivesDisplayNotRuntime::test_no_declared_key_is_env_pinned_here
    FAILED tests/unit/test_settings_registry.py::TestConfigPathDrivesDisplayNotRuntime::test_every_declared_path_flips_default_to_config
    FAILED tests/unit/test_error_reporting_config.py::TestErrorReportingConfig::test_get_config_default
    FAILED tests/unit/test_evolver_real_eval.py::TestRealEvalDistinguishable::test_heuristic_path_backward_compatible
    FAILED tests/unit/test_evolver_real_eval.py::TestRealEvalDistinguishable::test_real_eval_distinguishable_from_heuristic
    ============ 5 failed, 108 passed, 8 warnings in 126.26s (0:02:06) ============
    PROBE_WATCH=2      （钩子同时重新记到那 2 次受关注键的写入）

**⇒ 精确复现改前的 5 条红，且污染重新发生。修复是有效的因果环节，不是「碰巧绿了」。**
（自证夹具已恢复启用，恢复后 113 passed，见 §7。）

---

## 6. 第 3 步③：B —— 负载敏感的计时 flaky（1 条）

### 6.1 处置：本地预算改为**校准式有界余量**（不是「直接把 50ms 调大」）

tests/unit/test_tools_prompt_alignment.py 的 TestPerformance：

- 新增 _perf_calibration_workload()（:619-628）：一段**与本用例被测代码无关**的固定文本工作量
  （正则扫描 + 按行切分 ~480KB 纯文本）。
- 新增 PERF_MAX_ENV_ALLOWANCE = 3.0、PERF_CALIB_NOMINAL_MS = 7.9（:706 / :709）。
- 新增 _calib_ms()（:726）与 _env_allowance()（:741）：

      allowance = max(1.0, min(calib_ms / PERF_CALIB_NOMINAL_MS, PERF_MAX_ENV_ALLOWANCE))
      budget    = PERF_BUDGET_MS * allowance        # 50ms × ∈[1,3]

- **断言一条没删、没弱化**：仍是 assert best_ms < budget，仍是「5 次取最优」，仍是同一条
  「防退化成超线性」的护栏；失败信息里多了校准值与余量，便于下次一眼定位。

### 6.2 为什么这不是「简单调大」

| 质疑 | 回应 |
|---|---|
| 你把 50ms 调大了？ | **空载时没有**：机器不慢时 calib ≈ 标称 ⇒ 余量 ×1.0，预算仍是 **50ms**，判据与改前**逐字一致**。只有「当场量到机器确实慢了」才放宽，且**最多 ×3.0** |
| ×3 的口径哪来的？ | **就是本用例自己给 CI 的口径**（PERF_CI_ALLOWANCE = 3.0，原 docstring 写明「3× 有界环境余量 …… 不是无上限放宽」）。本卡把它从「只看 CI 环境变量」扩展到「看当场实测的机器速度」，**上限不变** |
| 会不会把「函数退化」也一起吸收掉？ | **不会**。校准载荷**不调用被测函数**：被测函数自己变慢不会让校准值变大 ⇒ 余量不涨、预算不涨、照样红。只有拿被测函数自身当参照才会互相抵消（那才是掩盖），代码注释里写明了这一点 |
| 有界性与检测能力 | 阈值 ÷ 空载基线 6.9ms = 「退化倍率」：**本地空载 ≈7×（50ms）／本地满载与 CI ≈21×（150ms）**；要防的 O(n²) 在 100KB 输入上是**百倍级**（≈10^10 字符操作 ⇒ 秒级），仍在检测能力内。类 docstring 的「退化倍率」三行已同步更新 |

### 6.3 改前红 / 改后绿（仿真「整机慢 k 倍」，含「没被放宽到失效」的反向验证）

**仿真手法**（仓库既有手法：「注入固定延迟的变异探针」，见类 docstring 2026-09-21 那条实测）：
把「整机慢 k 倍」如实仿真成**被测调用与校准载荷同时 ×k** —— 被测调用每次注入 (k−1)×6.9ms，
校准结果 ×k。探针在仓库外：%TEMP%\testhyg1\envslow.py（TESTSLOW_K=k pytest -p envslow …）。

| k | 改前代码 | 改后代码 |
|---|---|---|
| **8**（≈本仓全量验收实测的膨胀量级） | **红**：100KB 提示词对齐**最优**耗时 62.3ms 超过预算 50.0ms（5 次采样 min=62.3ms，CI 余量 ×1.0；样本 78.3 62.3 78.1 78.0 62.3） | **绿**（余量 ×3.0 ⇒ 预算 150ms） |
| **25**（超出 21× 上限） | — | **仍红** ⇒ 护栏**没有被放宽到失去意义** |
| 空载（无仿真） | 绿 | 绿（余量 ×1.0，预算 50ms） |

同时用**真实负载**量化了「环境变慢会不会被正确感知」（探针 %TEMP%\testhyg1\probe_perf.py）：

| 负载 | 被测调用最优耗时 | 校准载荷最优耗时 | 折算余量 |
|---|---|---|---|
| 空载 | 6.49ms | 7.91ms（= 标称） | ×1.00 |
| 6 个 CPU 占用进程 | 12.40ms（1.80×） | 15.42ms（1.90×） | ×1.90 |
| 128MB 级内存带宽压测 ×8 | 19.35ms（2.98×） | 25.71ms（3.25×） | ×3.00（触上限） |
| BLAS 满载 ×6 | 14.25ms（2.20×） | 20.31ms（2.57×） | ×2.57 |

⇒ 校准值**跟着环境一起涨**（且略偏保守），而它不随被测函数变化 ⇒ 余量确实在量「环境」而不是「函数」。
**诚实说明**：这些真实负载都没能把最优耗时推过 50ms（本机 12 逻辑核，且 min-of-5 对突发抢占
天然免疫）—— 全量验收那次 ≈8× 的**持续**整机变慢（4 分块并行 + 另一全量进程）无法在做卡约束内
无损复现，故 §6.3 的 k=8 用的是「注入延迟」仿真，并在 §8 计入未验证项。

### 6.4 副产物（重要）：地板生效后暴露出**测试会真的出网**

把 .env 从测试进程里摘掉后，第一次「改后」验证跑了 **13 分钟以上并以 exit 1 硬退出**（无 summary），
日志里是：

    huggingface_hub.utils._http: '[WinError 10060] …' thrown while requesting HEAD
      https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/resolve/main/adapter_config.json
    Retrying in 8s [Retry 5/5].   （× 多个文件）
    ~~~ Stack of Thread-18 ~~~      ← pytest-timeout(120s) 的线程栈转储后对整个进程硬退出

原因：import app_server 会真的实例化 sentence-transformers 编码器，而「只读本地缓存、不出网」的前提
HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE 恰好是 .env:153-154 提供的 —— 也就是说，**此前是全量跑里
那次污染「顺带」提供了这两个键**（一层掩蔽关系）。

处置：在 conftest 里**显式补齐**这两条基线。它们**本来就该在**：CI 的干净 checkout 没有 .env，
故三份 workflow 都显式设了它们（.github/workflows/test.yml:301-302、daily_regression.yml:57-58、
observability-ci.yml）。这两条不在开关注册表（agent/settings/registry.py）里、也没有任何用例断言
其默认值 ⇒ 不改变任何用例的判据语义。补齐后同一组合回到 **86s / 113 passed**。

---

## 7. 第 3 步⑦：回归结果（定向子集）

**没有跑全量 tests/unit**（硬约束：124 分钟）。跑的定向子集与结果：

    python -m pytest tests/unit/test_server_routes_registration_inventory.py ^
                     tests/unit/test_settings_registry.py ^
                     tests/unit/test_error_reporting_config.py ^
                     tests/unit/test_evolver_real_eval.py ^
                     tests/unit/test_env_isolation_p0.py ^
                     tests/unit/test_env_hot_reload.py ^
                     tests/unit/test_dotenv_no_uuid_pollution.py ^
                     tests/unit/test_settings_resolver.py ^
                     tests/unit/test_background_tasks_routes.py ^
                     tests/unit/test_tools_prompt_alignment.py -q -p no:randomly

    ================= 285 passed, 8 warnings in 93.96s (0:01:33) =================

选这些文件的理由：

| 文件 | 为什么必须在回归集里 |
|---|---|
| test_server_routes_registration_inventory.py | **污染源本体**（module 级 import app_server） |
| test_settings_registry.py / test_error_reporting_config.py / test_evolver_real_eval.py | 5 条受害用例 |
| test_env_isolation_p0.py | **隔离契约护栏**（「重定向真实 I/O，而不是 mock 掉写入」）—— 证明本修法没把写入变成 no-op |
| test_env_hot_reload.py | EnvConfigManager.reload() 的**真实语义契约**（reload 仍把 .env 内容写进 os.environ）—— 证明本修法没把 reload 变成空操作 |
| test_dotenv_no_uuid_pollution.py | 直接读仓库 .env 的现状锁 |
| test_settings_resolver.py | 解析器 / 覆盖层（会读写 os.environ） |
| test_background_tasks_routes.py | **函数级** import app_server 路径（另一种 scope；同时确认补齐离线基线后不再出网） |
| test_tools_prompt_alignment.py | B 的全部用例（含性能两条 + 线性度那条） |

---

## 8. 第 3 步⑧：未验证项与残留风险

| # | 未验证 / 风险 | 说明与缓解 |
|---|---|---|
| **U1** | **全量 22637 条跑未跑** | 硬约束禁止。故「5 条在全量跑里不再红」是**推断**：最小复现组合已逐字复现这 5 条，修复后同一组合全绿。风险：若还有**第二个**污染源，仍可能红 —— 但钩子实测显示受关注键只被这**一条**路径写过 |
| **U2** | **回归集之外的载荷面未跑** | 修法改变「测试进程是否看得见 .env」，理论影响面是全量。缓解论据：①.env 被 .gitignore 忽略 ⇒ CI 从来没有它，CI 能过的用例不可能依赖它；②被改的只是「是否加载 .env」，而 reload 的真实语义仍由 test_env_hot_reload.py 覆盖；③定向集 285 passed。**仍属残留风险**，建议在合并前的下一次全量验收里确认 |
| **U3** | **§6.4 的离线基线是否穷尽** | 只补了 HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE（= .env 提供、且 CI 也提供的两条）。若还有别的 .env 键是 import app_server 的前提，会在**冷启动首跑**表现为变慢/出网；定向集里已含 3 个会 module 级 import app_server 的文件，实测 94s 正常 ⇒ 现有证据下没有第二处 |
| **U4** | **B 的「全量不再红」未直接验证** | 只能给机制证据 + k=8 仿真。风险：若真实满载膨胀超过 21×（>150ms）仍会红 —— 那是本卡**故意保留**的上界（有界余量的定义），不是遗漏 |
| **U5** | **校准标称是机器相关常量** | PERF_CALIB_NOMINAL_MS = 7.9 在本机（i5-10500 / CPython 3.12.0）空载实测。换更慢的机器 ⇒ 因子 >1 ⇒ 余量最多 ×3（与 CI 同）；换更快的机器 ⇒ 因子 <1 被夹到 1.0，判据不收紧于 50ms |
| **U6** | **另外 4 处 module 级 import app_server 的夹具未逐一复跑** | 实测触发点已复现并修复；其余 4 处是**同一形状**（同一行 reload()），由同一处地板覆盖；只对 test_background_tasks_routes.py（函数级）做了回归 |
| **U7** | **异常退出会留下 %TEMP%\pytest_dotenv_floor_* 空目录** | 本卡自己的会话结束（含 113 passed / 5 failed 两种）都已自动清掉；被硬杀（pytest-timeout 的 os._exit）或进程仍在运行时会残留一个空目录。仓库外、无敏感内容。排查中实测到的 1 个残留目录创建于 18:47:35，**属于另一个并发 pytest 进程**（PID 10084，另一张卡的 test_bm25_skill_searcher），非本卡启动 |
| **U8** | **本卡改动会立即影响并发运行的会话** | 工作区里有别的卡在**同一份 conftest** 上跑 pytest（实测 PID 10084）。本卡的 conftest 改动对它们是立即生效的：好处是**若它们撞上那 5 条污染红，本卡一并修掉了**；风险是若其断言隐含依赖 .env 的某个键（见 U2/U3），表现会变。**本卡未干预该进程**，也未验证它的结果 |

---

## 9. 第 3 步⑨：回滚

**改动只有两个文件，且都是「纯新增 + 一处替换」，回滚即撤销本卡的两处编辑：**

1. tests/conftest.py
   - 删掉 tests/conftest.py:43-102（会话级地板 + 离线基线两个注释块与其 5 行代码）；
   - 删掉 tests/conftest.py:846-876 的 _assert_dotenv_redirect_floor 夹具；
   - 把 _isolate_dotenv_target 的 docstring 恢复为一行（被本卡扩写）；
   - 顶部删掉 import atexit / import shutil 两行（若其它改动不再需要）。
2. tests/unit/test_tools_prompt_alignment.py
   - 删掉 _CALIB_TEXT / _CALIB_RE / _perf_calibration_workload()（:612-628）；
   - 删掉 PERF_MAX_ENV_ALLOWANCE / PERF_CALIB_NOMINAL_MS / PERF_CALIB_REPEAT（:704-710）
     与 _calib_ms / _env_allowance（:725-749）；
   - 把测试体恢复成 budget = self.PERF_BUDGET_MS * self.PERF_CI_ALLOWANCE（其余断言不动）；
   - 类 docstring 里 TESTHYG-1 那一节与「退化倍率」三行一并删回两行。

**验证回滚是否干净**：git diff --numstat tests/conftest.py tests/unit/test_tools_prompt_alignment.py
本卡改后为 104 1 / 83 6；回滚后应为 0 0（若这两文件在本卡之前确无未提交改动）。

**约束遵守**：全程**未** git add、**未** git commit、**未**对任何文件做整文件 git checkout。
回滚靠人工编辑（如上），不靠 git 命令 ⇒ 不会误伤 44 张卡的未提交改动。

---

## 10. 第 3 步⑩：残留物自证

| 项 | 状态 |
|---|---|
| 仓库内被改文件 | **只有 2 个**：tests/conftest.py、tests/unit/test_tools_prompt_alignment.py，外加**本报告**（新增未跟踪文件）。git status 条目数 148（本卡开工时）→ 153：**+2 = 本卡改的两个文件、+1 = 本报告**，其余差额是**其它卡在本卡作业期间的并发改动**（工作区有并发会话在跑）。RET-1 正在改的 agent/skills_mgmt/ **一行未动** |
| 禁区文件 | agent/skills_mgmt/、agent/tool_router*.py、agent/audit/、plugins/、yunshu-ui/、config.yaml、data/、prompt 装配四件套 —— **均未改动** |
| 探针 / 脚本 | 全部在仓库外 C:\Users\Administrator\AppData\Local\Temp\testhyg1\（envdirty.py、envslow.py、probe_perf.py、probe_import_noenv.py、burn*.py、memburn.py、blasburn.py 及各次运行的 .log）。**按卡片要求写在仓库外绝对路径** |
| 临时目录 | %TEMP%\pytest_dotenv_floor_*：本卡自己的会话（113 passed 与 5 failed 两种收尾）都**已自动清掉**（atexit + 会话级收尾双保险）。排查中发现过 2 个空残留目录并已删除；当前计数 1，其创建时间 18:47:35 **对应另一个并发 pytest 进程**（见上），非本卡产物 |
| python 残留进程 | **本卡启动的：0**。压测用的 CPU/内存负载进程全部是**自终止**的定时脚本，**未使用 taskkill**，收尾已确认归零（中途多次确认计数回到 0）。**另有 1 个并发 pytest**（PID 10084，另一张卡的 test_bm25_skill_searcher）—— 非本卡启动，本卡**未干预、未 taskkill** |
| 常驻服务 | **未起**任何服务；未监听新端口 |
| 网络 | **未**主动使用网络；压测与仿真全部本地。§6.4 记录的那次出网是**被修复暴露出来的缺陷**（HF 下载重试），已在测试侧补齐离线基线消除 |
| .env / data/ 生产数据 | 未改；test_env_isolation_p0.py 的「仓库 .env 逐字节未变 + 隔离目标真的收到写入」两条判据在回归集里**通过** |

---

## 附：本卡用到的全部原始证据文件（仓库外）

| 文件 | 内容 |
|---|---|
| %TEMP%\testhyg1\env_writes.log | 脏写钩子日志（最后一次运行为「修复后」：仅 30 条基线键、受关注键 0 次） |
| %TEMP%\testhyg1\before_red.log | 改前红（5 failed, 108 passed） |
| %TEMP%\testhyg1\after_green2.log | 改后绿（113 passed） |
| %TEMP%\testhyg1\reverse_red.log / reverse_red2.log | 反向自证（先 113 errors（护栏点名），再 5 failed, 108 passed + PROBE_WATCH=2） |
| %TEMP%\testhyg1\regression.log | 定向回归（285 passed） |
| %TEMP%\testhyg1\run_reginv.log | 单文件复现污染（钩子首次记录到两个受关注键的完整调用栈） |
| %TEMP%\testhyg1\collect_unit.log | tests/unit --collect-only（22994 条，73s）—— 用于排除「污染发生在收集期导入」这一假设 |
