# 死配置处置清单（TASK-03 · E11）

> **日期**：2026-09-18 · **HEAD（作业时）**：`82bbe6fd297963148d7a6874a94012a04897b184`
> **范围**：本任务发现并逐条判定的"**看起来在生效、实际从未生效**"的配置
> **纪律**：不越界重构业务代码；每条都给出**为什么**与**实测证据**，而不是"看着像死配置"

---

## 0. 为什么"死配置"值得单独出一份清单

死配置比"没有配置"更危险，因为它**骗的是人的判断**：

* 运维改了 `configs/app.yaml` 的端口，以为改了服务端口 —— 实际服务仍在 5678；
* 开发者改了 `pyproject.toml` 的 pytest 配置，以为改了测试行为 —— 实际被 `pytest.ini` 遮蔽；
* 有人看到 `gunicorn_config.py` 存在，以为生产是 gunicorn 多进程 —— 实际是 waitress 单进程。

TASK-03 §2.6/§2.7 把这类问题与"门禁空转"并列，判据一致：**状态必须诚实**。

---

## 1. pytest 配置双源（**已处置：删除死配置**）

| 项 | 值 |
|---|---|
| 死配置位置 | `pyproject.toml` 原第 181–187 行 `[tool.pytest.ini_options]`（`testpaths` / `addopts` / `filterwarnings`） |
| 真正生效者 | 根目录 `pytest.ini`（11 条 `--ignore=`、`--timeout=120`、`--timeout-method=thread`、`--import-mode=importlib`、`--strict-markers`、`asyncio_mode=auto`、`pythonpath=tests/unit`、markers 表） |
| 判定依据 | pytest 的配置发现顺序：`pytest.ini` > `pyproject.toml`([tool.pytest.ini_options]) > `tox.ini` > `setup.cfg`；根目录存在 `pytest.ini` 即胜出 |

**实测证据**（本机 pytest 9.1.1）：

```
$ python -m pytest tests/unit/test_settings_registry.py --collect-only -q
rootdir: C:\Users\Administrator\agent
configfile: pytest.ini (WARNING: ignoring pytest config in pyproject.toml!)
                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                ← pytest 自己打印的警告，这是最硬的证据
```

**处置**：按 TASK-03 §2.6 的"二选一、保留真正生效者"**删除** `pyproject.toml` 的
`[tool.pytest.ini_options]`，并在原位留一段说明（含上面那条实测证据），防止
后人重新加回来。`pytest.ini` 保持不动。
**风险**：零。该段从未生效，删除不改变任何行为（删除前后 `pytest` 行为逐字节一致）。

### 1b. 追加发现：`pytest.ini` 里的 `[coverage:*]` 两节**同样是死配置**

| 项 | 值 |
|---|---|
| 位置 | `pytest.ini:109-136` 的 `[coverage:run]` / `[coverage:report]` / `[coverage:html]` |
| 真正生效者 | `pyproject.toml` 的 `[tool.coverage.run]` / `[tool.coverage.report]` |
| 依据 | coverage.py 只读 `.coveragerc` / `setup.cfg` / `tox.ini` / `pyproject.toml`，**不读 `pytest.ini`** |

**影响（真实且容易误判）**：`pytest.ini` 里写着 `branch = True`、`parallel = True`、
`source = agent, core, memory, persona, planning, lifetrace`、`omit = ...` ——
这些**全都没生效**。实际生效的是 `pyproject.toml` 的 `source = [agent, sensor, memory,
planning, persona, core, cognitive, lifetrace, utils]`（9 个包）、`omit = */tests/*, */scripts/*`，
且 **`branch` 与 `parallel` 均未开启** ⇒ 历史 `coverage.xml` 里的 `line-rate="0.4908"`
与本次实测值**不是同一个口径**，不可直接比较（详见 `BASELINE_20260918.md` §覆盖率）。

**处置**：**本任务不删**（它不构成"双源冲突"，只是无效段落；且其中注释记录了历史口径
与判定过程，删掉会丢证据）。登记为债务，建议在 TASK-08 统一覆盖率口径时处理。

---

## 2. `configs/app.yaml` 的 `port: 8123` / `workers: 4`（**死配置，零消费方**）

| 项 | 值 |
|---|---|
| 位置 | `configs/app.yaml:6-10`（`server: host: 0.0.0.0 / port: 8123 / workers: 4`） |
| 实际运行时 | `app_server.py:1618` → `waitress.serve(app, host="127.0.0.1", port=5678, threads=16)` |
| 消费方 | **零** |

**实测证据**：

```
$ git grep -n "configs/app.yaml" -- "*.py" "*.bat" "*.ps1" "*.yml" "*.yaml" "*.md"
（无输出 —— 没有任何代码/脚本/文档读它）

$ Select-String app_server.py -Pattern "waitress|serve\(|5678"
1613:     # 使用 Waitress 生产级 WSGI 服务器（替代 Flask 内置开发服务器）
1618:     serve(app, host="127.0.0.1", port=5678, threads=16)
```

**判定**：该文件**从未被读取**，因此 `port: 8123`、`workers: 4`、`host: 0.0.0.0`
三个值与实际运行（`127.0.0.1:5678` 单进程 16 线程）**全部不符**。
`workers: 4` 尤其危险：它暗示这是多进程部署，而实际是**单进程**——这一点直接影响
熔断器/单例/多进程指标等一大类判断。

**处置（为什么不在本任务里删）**：
1. 它是一次性产物级配置，但**不是** TASK-03 §3 步骤 5 列出的清理对象；
2. 删除它属于"配置面"变更，而本任务的范围纪律是"不越界重构"；
3. 更安全的做法是**给它加一条显式声明**，让下一个人不会再被骗。

**已执行的处置**：在 `configs/app.yaml` 头部加一段"**本文件当前无消费方**"的显式声明
（含实测命令与真实运行时口径），而**不删除**文件（避免运维脚本/文档引用断裂。
引用检查：`git grep` 实测零引用，删除其实是安全的，但保留 + 声明更保守，
且保留了对历史配置意图的证据）。

---

## 3. `gunicorn_config.py`（**死配置：不是当前运行时**）

| 项 | 值 |
|---|---|
| 位置 | `gunicorn_config.py`（2205 字节，`workers = min(cpu*2+1, 8)`、`bind = "127.0.0.1:5678"`） |
| 实际运行时 | waitress 单进程 16 线程（`app_server.py:1618`） |
| 消费方 | **无**：`git grep -ln gunicorn` 命中 `deploy/k8s/deployment.yaml`、`gunicorn_config.py` 自身、`pyproject.toml`、`requirements.txt`、以及 6 份 `docs/`（其中 4 份在 `docs/archive/`） |

**判定**：
* `gunicorn_config.py` 文件本身**没有任何调用方**（没有任何脚本 `gunicorn -c gunicorn_config.py`）。
* `pyproject.toml` 声明并 pin 了 `gunicorn`（原 `>=21.2.0`），`requirements.txt:90` 亦
  锁定 `gunicorn==23.0.0`，但**生产运行时（Windows / waitress）用不到它**。
  （`deploy/k8s/deployment.yaml` 引用 gunicorn —— 但 TASK-00 §0.2 已核实
  `deploy/k8s/` **只服务 `skill-retrieval-service`**，不是主平台的部署路径。）

**处置决定：保留依赖，但把口径写清楚（不删除）。**

| 选项 | 取舍 |
|---|---|
| 删除 `gunicorn` 依赖 | 减少一个攻击面（TASK-03 §2.6 的建议），但会让 `deploy/k8s/deployment.yaml` 与 `gunicorn_config.py` 两处配置**悬空**（= TASK-03"不通过"清单里的"删文件却没做引用检查"） |
| **保留 + 标注（本次采纳）** | 零风险；口径写进注释与本文档，后人不会再误判运行时 |

**已执行的处置**：
1. `pyproject.toml` 的 `gunicorn` 加上上限 `>=21.2.0,<24.0.0`（TASK-03 §2.6"无上限依赖"项），
   并加注释说明"当前 Windows 生产运行时是 waitress，gunicorn 属非当前运行时路径"。
2. `gunicorn_config.py` 头部加"**本文件不是当前运行时配置**"的显式声明。

### 3b. 追加发现：`app_server.py:100` 的多进程指标导出器与单进程运行时语义不符

```python
# app_server.py:98-100
try:
    from prometheus_flask_exporter import PrometheusMetrics, Counter, Histogram, Gauge
    from prometheus_flask_exporter.multiprocess import GunicornPrometheusMetrics   # ← 多进程变体
```

`GunicornPrometheusMetrics` 是**为 gunicorn 多 worker 设计的**（依赖 `PROMETHEUS_MULTIPROC_DIR`
共享目录 + 每 worker 独立文件）。而实际运行时是 waitress **单进程 16 线程** ⇒
两者语义不符：多进程变体会把指标写到多进程目录，而 `/metrics` 的聚合语义假定多 worker。

**影响判定（诚实标注）**：`app_server.py` 只 **import** 了它，实际注册指标时用的是
`PrometheusMetrics`（单进程变体，见同段的 `PrometheusMetrics`）。因此
**当前影响面为"一个未被使用的 import"**，不是指标错误。但它会持续误导
"这是多进程部署"的判断，故登记为债务（建议 TASK-08 可观测性收口时清理该 import）。

---

## 4. WSGI 三处口径对照表（供一次性对齐）

| 出处 | 绑定 | 进程/线程模型 | 是否生效 |
|---|---|---|---|
| `app_server.py:1618`（waitress） | `127.0.0.1:5678` | 单进程 + 16 线程 | ✅ **实际生效** |
| `gunicorn_config.py:26-30` | `127.0.0.1:5678` | `min(cpu*2+1, 8)` worker | ❌ 无调用方 |
| `configs/app.yaml:8-10` | `0.0.0.0:8123` | `workers: 4` | ❌ 零消费方 |

**唯一真相源**：`app_server.py:1618`。任何涉及"并发度/进程模型/端口"的判断，
必须以它为准，而不是 `gunicorn_config.py` 或 `configs/app.yaml`。

---

## 5. 其他 §2.6 项的判定（逐条，含"为什么不改"）

| 项 | TASK-03 §2.6 的建议 | 本次判定 | 理由 |
|---|---|---|---|
| `transformers` 未在 pyproject 声明 | 补齐声明 | ✅ **已做** | `pyproject.toml` 新增 `transformers>=5.9.0,<6.0.0`。**但 TASK-03 说"它解释了 10 个基线 ERROR"是错的** —— 见 §6 |
| `gunicorn` 声明并 pin 但未使用 | 判定是否删除 | ⚠️ **保留 + 标注** | 见 §3 |
| Windows 专用依赖缺 `sys_platform` marker（`pyautogui`/`pygetwindow`/`pyperclip`/`keyboard`/`mouse`） | 补 marker | ⛔ **本任务不做，登记债务** | 理由见下 |
| Node 版本口径冲突（CI `NODE_VERSION: '18'` vs Electron 43 / Vite 6 / `@types/node` 22） | 判定并升 | ⛔ **本任务不做，登记债务** | 本机无法验证 Node 构建链；且该判定属于前端工具链范围，越界风险高 |
| 无上限依赖（`flask` / `waitress` / `gunicorn`） | 加"≤ 下一大版本"上限 | ✅ **已做** | `flask>=3.0.0,<4.0.0`、`waitress>=3.0.0,<4.0.0`、`gunicorn>=21.2.0,<24.0.0`；顺带 `prometheus-flask-exporter<1.0.0`、`prometheus-client<1.0.0`、`pypdf<7.0.0` |

### 5a. 为什么不做 Windows marker（**这是一个刻意的范围决定，不是遗漏**）

补 `sys_platform == 'win32'` marker 会让这些包在 **Linux CI 上不再安装**。后果分两种，
而本机（Windows）**无法验证 Linux 侧**：

* 若业务代码对它们是 `try/except ImportError` 降级 → 补 marker 是纯收益；
* 若某处**无保护地 import**（`import pyautogui` / `import keyboard`）→ 补 marker 会让
  Linux CI 从"装了但用不了"变成"**收集期 ImportError**"，即把一批隐性失败变成显式失败。

在没有 Linux 环境验证的前提下做这个改动，等于**在用 CI 的红色来赌一个未知假设**。
按 TASK-03"不要为了全绿而放宽门禁、也不要引入未验证的断言"的同一精神，本任务
**不做**，并把它作为一条**明确登记的债务**（而非沉默跳过）。

---

## 6. ⚠️ 与 TASK-03 原文不符的实测结论（D10：发现相反证据必须上报）

TASK-03 §2.6 写道：

> **`transformers` 未在 `pyproject.toml` 声明** …… **它解释了 `failures_baseline.txt`
> 里 10 个 ERROR 全部是 `No module named 'transformers.configuration_utils'`**
> ⇒ 这一整类基线失败可以在本任务中消除

**实测结论：这条因果推断不成立。** 证据如下。

**证据 1：`transformers` 早就装好了，且模块完好。**

```
$ python -c "import transformers,os;print(transformers.__version__, os.path.dirname(transformers.__file__))"
5.13.1 C:\Users\Administrator\AppData\Local\Programs\Python\Python312\Lib\site-packages\transformers

$ python -c "import importlib;[importlib.import_module(m) for m in ['transformers.configuration_utils','transformers.modeling_utils','transformers.models']];print('OK')"
OK
```

**证据 2：报错文本本身否定了"缺包"假说。** 基线里那 10 条的错误串是：

```
ModuleNotFoundError: No module named 'transformers.configuration_utils'; 'transformers' is not a package
                                                                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
```

`'transformers' is not a package` 是**名字被遮蔽**的典型症状（`sys.modules['transformers']`
里是个没有 `__path__` 的模块对象），而不是"没装"（没装会说
`No module named 'transformers'`，且不会有后半句）。

**证据 3：根因在测试的**顺序污染**，且仓库自己已经写明了。**

* `tests/unit/test_reranker.py` 在**模块级**执行 `sys.modules["transformers"] = MagicMock()`
  （三件套 mock 之一，是防 Windows `0xC0000005` 崩溃的屏障）；
* `tests/conftest.py:980-983` 的清理逻辑里**显式写明不清理它**：

  > `# 注意：不要清理被 mock 污染的 transformers。Why: 真实 import transformers`
  > `# 会加载 torch C 扩展 → Windows 0xC0000005 崩溃（reranker 测试模块级 mock`
  > `# 三件套正是防崩溃屏障）。transformers 的 MagicMock 残留阻止任何后续真实`
  > `# import（import 链返回 Mock，不加载 torch）→ 安全失败模式。`

* 同文件 `:964-965` 的注释也直接点名了这批失败：
  `"随机序 TestVectorStoreSqliteVecIntegration 8 ERROR + backend 1 FAILED 根因"`。

**⇒ 正确结论**：那 10 条 ERROR 是**已知的、被有意保留的**顺序污染"安全失败模式"
（用一批 ERROR 换取 Windows 上的进程不崩溃）。**补齐 `pyproject` 声明不可能消除它们** ——
包本来就在，遮蔽来自测试进程内的 `sys.modules` 污染。

**本次实际处置**：
1. ✅ 仍然补齐了 `transformers` 声明 —— 因为 `agent/` 与 `memory/` **直接 import 它**，
   把它交给 `sentence-transformers` 的传递闭包是真实隐患（上游改依赖树即静默失效）。
   但这与那 10 条 ERROR 无关。
2. ⛔ **不**为了让这 10 条消失而去改 `tests/conftest.py` 的清理策略 —— 那会推翻仓库
   一条有明确崩溃证据的防线（引用的注释里写着 `0xC0000005`，是进程级消失）。
3. 该 10 条 ERROR 的正确处置是"**用 fixture 级隔离替代 session 级 mock 残留**"，
   属于测试基础设施改造，登记为债务，建议在 TASK-08（或专门的测试治理任务）里做。

**这条记录本身就是 TASK-00 D10 的用途**：只读审计结论与实测冲突时，记录并上报，
不静默假定原结论正确。**E12 的判定以本节为准。**
