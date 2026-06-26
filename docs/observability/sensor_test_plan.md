# 云枢 sensor 模块测试补全计划

> 生成时间：2026-06-26
> 范围：`c:\Users\Administrator\agent\sensor\` 全部 30 个 `.py` 文件
> 目标：将模块覆盖率从当前 **9.18%**（653/7122 语句）提升至 **40%+**
> 约束：仅生成测试计划文档，不修改任何生产代码，不编写实际测试代码

---

## 一、执行摘要

### 1.1 现状

| 指标 | 数值 |
|---|---|
| 模块文件数 | 30 |
| 语句总数 | 7122 |
| 已覆盖语句 | 653 |
| 当前覆盖率 | 9.18% |
| 模块内既有测试 | 1 个（`sensor/test_body_sensor.py`，已过时） |
| `tests/unit/` 下 sensor 测试 | 0 个 |
| 目标覆盖率 | ≥ 40% |
| 预计新增用例数 | 253 |
| 预计工作量 | 20 人日 |

### 1.2 既有测试问题

文件 `sensor/test_body_sensor.py` 存在 3 处与当前实现脱节的断言：

| 用例 | 问题 | 处理建议 |
|---|---|---|
| `test_health_report` | 断言 `str` 返回，但 `get_health_report()` 现返回 `dict` | 重写为字段断言 |
| `test_to_json_static` | 调用不存在的 `BodySensor.to_json` 静态方法 | 删除或改测 `to_dict` |
| `test_collect_first_time` | 期望 `sensor_name=="baseline"`，实际为 `change_baseline_established` | 同步字段值 |

此外该文件位于 `sensor/` 包内而非 `tests/unit/`，缺少 `monkeypatch` mock，强耦合 Windows 平台。

### 1.3 三阶段推进策略

| 阶段 | 目标 | 重点 | 预计用例 | 预计覆盖率提升 |
|---|---|---|---|---|
| P0 | 纯函数/数据类/注册发现 | 不依赖平台 IO，可直接单测 | 109 | +12%（至 21%） |
| P1 | 主流程 + Mock | 模拟 WMI/psutil/watchdog，覆盖主流程与降级 | 71 | +13%（至 34%） |
| P2 | 平台/并发/可选依赖 | 跨平台分支、线程安全、可选 import 降级 | 73 | +8%（至 42%） |

### 1.4 可观测性缺口（"存在即可见"原则检查）

| 位置 | 缺口类型 | 影响 | 计划覆盖用例 |
|---|---|---|---|
| `body_sensor._apply_tags` | `except Exception: pass` 静默吞错 | 标签应用失败不可见 | P0-BS-12 |
| `change_detector._load_persistent_log` | `except Exception: pass` | 持久化日志加载失败不可见 | P1-CD-15 |
| `gpu_sensor` 模块级 `try/except ImportError` | 仅记录 warning，无降级标记 | GPU 采集失败不可见 | P2-GPU-05 |
| `body_sensor` 无 `/health` 接口 | 缺健康检查 | 依赖状态不可见 | P0-BS-30 |
| `event_monitor` 异步检测线程异常 | 未捕获即丢失 | 启动检测失败不可见 | P1-EM-22 |
| `file_watcher.EventBuffer` 满丢弃 | 仅日志，无指标 | 事件丢失不可见 | P0-FW-25 |
| `counter_reader` PowerShell 失败 | 静默返回 None | 计数读取失败不可见 | P2-CR-08 |
| `voice_sensor` STT 麦克风初始化失败 | 仅 warning | 录音能力不可见 | P2-VOICE-04 |

---

## 二、模块分析

### 2.1 30 文件优先级清单

| # | 文件 | 核心类/函数 | 行数估算 | 优先级 | 阶段 | 备注 |
|---|---|---|---|---|---|---|
| 1 | `sensor_reading.py` | `SensorReading`/`Severity`/`Category`/`reading()`/`normal()`/`warning()`/`critical()` | 80 | P0 | P0 | 纯数据类，无 IO |
| 2 | `tags.py` | `get_tags()`/`_CATEGORY_TAGS`/`_SENSOR_TAG_OVERRIDES` | 120 | P0 | P0 | 纯字典查表 |
| 3 | `registry.py` | `SensorRegistry.discover()`/`SensorCapabilities`/`_is_sensor_class` | 150 | P0 | P0 | importlib 自动发现 |
| 4 | `body_sensor.py` | `BodySensor` 聚合器（懒加载/标签过滤/开关） | 400 | P0 | P0+P1 | 核心入口，P0 先覆盖纯方法 |
| 5 | `file_watcher.py` | `EventBuffer`/`PatternFilter`/`FileWatcher` | 350 | P0 | P0+P1 | 线程安全缓冲 + watchdog |
| 6 | `change_detector.py` | `ChangeDetector` 快照 diff | 380 | P1 | P1 | 6 维度快照对比 |
| 7 | `event_monitor.py` | `EventMonitor` P4 优化实时监控 | 360 | P1 | P1 | 异步启动检测 + 缓存 |
| 8 | `cpu_sensor.py` | `CPUSensor` 9 个 collect 方法 | 320 | P1 | P1 | WMI 缓存 + perf_counters |
| 9 | `memory_sensor.py` | `MemorySensor` 虚拟/交换/模块 | 280 | P1 | P1 | DDR 代际推断 |
| 10 | `battery_sensor.py` | `BatterySensor` 5 级严重度 | 200 | P1 | P1 | 充放电时间计算 |
| 11 | `disk_sensor.py` | `DiskSensor` 分区/IO/IOPS | 300 | P1 | P1 | 阈值 95/85 |
| 12 | `network_sensor.py` | `NetworkSensor` 接口/适配器/IP/WiFi | 340 | P1 | P1 | netsh 解析 |
| 13 | `chassis_sensor.py` | `ChassisSensor` 3 平台 + SecurityBreach | 180 | P2 | P2 | 安全事件 4/5=CRITICAL |
| 14 | `board_sensor.py` | `BoardSensor` 3 平台 | 160 | P2 | P2 | WMI/sysctl/IOReg |
| 15 | `behavior_sensor.py` | `ActivityBehaviorSensor` 6 维度 | 300 | P2 | P2 | 行为基线 |
| 16 | `system_sensor.py` | `SystemStateSensor` Windows-only 10 维度 | 280 | P2 | P2 | `_wmi_get` CSV 解析 |
| 17 | `environment_sensor.py` | `EnvironmentSensor` `_KEY_MODULES` | 220 | P2 | P2 | 服务状态查询 |
| 18 | `process_sensor.py` | `ProcessSensor` 敏感关键字过滤 | 200 | P1 | P1 | top 进程 |
| 19 | `port_sensor.py` | `PortSensor` Windows-only | 240 | P2 | P2 | USB/COM/LPT/PCIe |
| 20 | `peripheral_sensor.py` | `PeripheralSensor` Windows-only | 220 | P2 | P2 | 监视器/SMART |
| 21 | `hardware_file_sensor.py` | `HardwareFileSensor` 跨平台路径 | 180 | P2 | P2 | 路径工具 |
| 22 | `gpu_sensor.py` | `GPUSensor` 多源降级 | 260 | P2 | P2 | pynvml→GPUtil→nvidia-smi |
| 23 | `hardware_blueprint.py` | `HardwareBlueprint` 解剖清单 | 150 | P2 | P2 | 静态清单 |
| 24 | `file_blueprint.py` | `FileBlueprint` 文件系统解剖 | 130 | P2 | P2 | 静态清单 |
| 25 | `software_blueprint.py` | `SoftwareBlueprint` 软件生态 | 140 | P2 | P2 | 静态清单 |
| 26 | `counter_reader.py` | PowerShell/wmic/registry 计数 | 200 | P2 | P2 | 子进程调用 |
| 27 | `ocr_sensor.py` | `OcrSensor` numpy/cv2/pytesseract | 220 | P2 | P2 | 可选 import |
| 28 | `voice_sensor.py` | `TTSEngine`/`STTEngine`/`VoiceManager` | 400 | P2 | P2 | gTTS/pyttsx3 |
| 29 | `window_sensor.py` | `WindowSensor` win32gui 轮询 | 200 | P2 | P2 | 后台线程 |
| 30 | `main.py` | Demo 入口 | 50 | — | 跳过 | 仅演示，不测试 |

### 2.2 分支类型分布

| 分支类型 | 占比 | 主要文件 | 测试策略 |
|---|---|---|---|
| 正常流程（happy path） | 45% | 全部 | 直接断言返回结构 |
| 错误处理（except 分支） | 25% | `body_sensor`/`change_detector`/`voice_sensor`/`counter_reader` | 注入异常验证降级 |
| 边界条件 | 15% | `battery_sensor`/`disk_sensor`/`file_watcher` | 阈值临界值、空输入、超限值 |
| 平台差异 | 10% | `cpu_sensor`/`chassis_sensor`/`board_sensor`/`port_sensor` | `monkeypatch` 平台标识 + mock WMI/sysctl |
| 并发/异步 | 5% | `event_monitor`/`window_sensor`/`file_watcher` | 线程 join + 超时断言 |

---

## 三、测试用例清单

> 用例 ID 规则：`<优先级>-<模块缩写>-<序号>`，模块缩写见 2.1 表。
> 工作量单位：S（简单，<0.5 人时）/ M（中等，0.5-1.5 人时）/ L（复杂，>1.5 人时）。

### 3.1 P0 阶段用例（109 个，预计覆盖率 +12%）

#### 3.1.1 sensor_reading.py（SR 系列，10 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P0-SR-01 | Severity 枚举值完整 | `Severity` | NORMAL/WARNING/CRITICAL 三值 | S |
| P0-SR-02 | Category 枚举覆盖全部类别 | `Category` | 全部类别常量存在 | S |
| P0-SR-03 | 工厂函数 reading 默认 severity | `reading()` | `severity or Severity.NORMAL.value` 短路 | S |
| P0-SR-04 | 工厂函数 reading 显式 severity | `reading()` | 传入字符串 severity | S |
| P0-SR-05 | 工厂函数 reading category 转换 | `reading()` | `category.value if isinstance(category, Category)` | S |
| P0-SR-06 | normal() 工厂 | `normal()` | 强制 NORMAL | S |
| P0-SR-07 | warning() 工厂 | `warning()` | 强制 WARNING | S |
| P0-SR-08 | critical() 工厂 | `critical()` | 强制 CRITICAL | S |
| P0-SR-09 | SensorReading 序列化 | `to_dict()` | 字段完整 | S |
| P0-SR-10 | SensorReading 边界：None 字段 | 构造函数 | 可选字段缺省 | S |

#### 3.1.2 tags.py（TG 系列，10 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P0-TG-01 | 已知 category 默认标签 | `get_tags()` | `_CATEGORY_TAGS` 命中 | S |
| P0-TG-02 | 未知 category 空标签 | `get_tags()` | 默认 dict 兜底 | S |
| P0-TG-03 | sensor_name 前缀精确匹配 override | `get_tags()` | `_SENSOR_TAG_OVERRIDES` 命中 | S |
| P0-TG-04 | sensor_name 前缀部分匹配 | `get_tags()` | 多个 override 前缀优先级 | S |
| P0-TG-05 | sensor_name 为 None | `get_tags()` | 仅返回默认 | S |
| P0-TG-06 | sensor_name 为空字符串 | `get_tags()` | 仅返回默认 | S |
| P0-TG-07 | 8 维度标签齐全性 | `_CATEGORY_TAGS` | 每类别至少 1 个标签 | S |
| P0-TG-08 | override 不污染默认 | `get_tags()` | 返回新对象 | S |
| P0-TG-09 | 默认 + override 合并去重 | `get_tags()` | 集合合并 | S |
| P0-TG-10 | 大小写敏感 | `get_tags()` | 前缀匹配区分大小写 | S |

#### 3.1.3 registry.py（RG 系列，14 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P0-RG-01 | discover 返回非空 | `SensorRegistry.discover()` | 自动扫描成功 | S |
| P0-RG-02 | discover 包含 BodySensor | `SensorRegistry.discover()` | 核心类被发现 | S |
| P0-RG-03 | discover 去重 | `SensorRegistry.discover()` | 同一类不被重复登记 | S |
| P0-RG-04 | SensorCapabilities 字段完整 | `SensorCapabilities` | name/category/platforms/tags | S |
| P0-RG-05 | _is_sensor_class 拒绝非类 | `_is_sensor_class()` | module 函数被过滤 | S |
| P0-RG-06 | _is_sensor_class 拒绝无 collect | `_is_sensor_class()` | 缺 collect 方法被过滤 | S |
| P0-RG-07 | _is_sensor_class 拒绝抽象类 | `_is_sensor_class()` | ABC 被过滤 | S |
| P0-RG-08 | _extract_capabilities 正常 | `_extract_capabilities()` | 标签提取 | S |
| P0-RG-09 | _extract_capabilities 缺字段 | `_extract_capabilities()` | 缺省默认值 | S |
| P0-RG-10 | _check_platform 当前平台命中 | `_check_platform()` | Windows 上发现 Windows 传感器 | S |
| P0-RG-11 | _check_platform 平台不匹配 | `_check_platform()` | Linux 上跳过 Windows 传感器 | S |
| P0-RG-12 | _check_platform 无限制 | `_check_platform()` | platforms 为空表示全平台 | S |
| P0-RG-13 | discover 失败模块跳过 | `discover()` | 某子模块 import 失败不影响整体 | M |
| P0-RG-14 | discover 缓存命中 | `discover()` | 二次调用不重新扫描 | S |

#### 3.1.4 body_sensor.py（BS 系列，30 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P0-BS-01 | 构造默认状态 | `__init__` | 注册表为空、开关全开 | S |
| P0-BS-02 | enable_sensor 单个开启 | `enable_sensor()` | 标志位翻转 | S |
| P0-BS-03 | disable_sensor 单个关闭 | `disable_sensor()` | 标志位翻转 | S |
| P0-BS-04 | enable_sensor 未知名称 | `enable_sensor()` | 静默忽略/警告 | S |
| P0-BS-05 | disable_sensor 未知名称 | `disable_sensor()` | 静默忽略/警告 | S |
| P0-BS-06 | enable_by_tags 命中类别 | `enable_by_tags()` | 单标签批量启用 | S |
| P0-BS-07 | enable_by_tags 多标签 | `enable_by_tags()` | 多标签并集 | S |
| P0-BS-08 | enable_by_tags 无命中 | `enable_by_tags()` | 全部保持原状 | S |
| P0-BS-09 | _filter_by_tags str 格式 | `_filter_by_tags()` | 字符串标签 | S |
| P0-BS-10 | _filter_by_tags list 格式 | `_filter_by_tags()` | 列表标签 | S |
| P0-BS-11 | _filter_by_tags dict 格式 | `_filter_by_tags()` | 字典标签 | M |
| P0-BS-12 | _apply_tags 异常静默 | `_apply_tags()` | except Exception: pass（可观测性缺口） | M |
| P0-BS-13 | _ensure_change_detector 首次 | `_ensure_change_detector()` | 懒加载创建 | S |
| P0-BS-14 | _ensure_change_detector 复用 | `_ensure_change_detector()` | 二次返回同一实例 | S |
| P0-BS-15 | _ensure_event_monitor 首次 | `_ensure_event_monitor()` | 懒加载创建 | S |
| P0-BS-16 | _ensure_event_monitor 复用 | `_ensure_event_monitor()` | 二次返回同一实例 | S |
| P0-BS-17 | _ensure_file_watcher 首次 | `_ensure_file_watcher()` | 懒加载创建 | S |
| P0-BS-18 | _ensure_file_watcher 复用 | `_ensure_file_watcher()` | 二次返回同一实例 | S |
| P0-BS-19 | collect_quick 不依赖完整 | `collect_quick()` | 快速通道返回子集 | M |
| P0-BS-20 | collect_category 命中 | `collect_category()` | 单类别采集 | M |
| P0-BS-21 | collect_category 未命中 | `collect_category()` | 未知类别返回空 | S |
| P0-BS-22 | collect_all 全开 | `collect_all()` | 全部启用 | M |
| P0-BS-23 | collect_all 全关 | `collect_all()` | 全部禁用返回空 | S |
| P0-BS-24 | collect_all filter_tags | `collect_all(filter_tags=...)` | 标签过滤 | M |
| P0-BS-25 | get_health_report 字段 | `get_health_report()` | 返回 dict 含 status/dependencies | M |
| P0-BS-26 | get_health_report 依赖状态 | `get_health_report()` | WMI/psutil 状态字段 | M |
| P0-BS-27 | get_sensor_list | `get_sensor_list()` | 列表含元信息 | S |
| P0-BS-28 | to_dict 序列化 | `to_dict()` | 字段完整 | S |
| P0-BS-29 | 线程安全开关切换 | `enable/disable_sensor()` | 并发无竞态 | L |
| P0-BS-30 | health 端点占位 | 模块级 `/health` | 返回依赖状态（可观测性缺口） | M |

#### 3.1.5 file_watcher.py（FW 系列，33 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P0-FW-01 | EventBuffer 构造默认 | `EventBuffer.__init__` | max_events 默认 | S |
| P0-FW-02 | EventBuffer.add 单条 | `add()` | 入队 | S |
| P0-FW-03 | EventBuffer.add 多条 | `add()` | 批量入队 | S |
| P0-FW-04 | EventBuffer 满 | `add()` | max_events 触发丢弃 | M |
| P0-FW-05 | EventBuffer 满日志 | `add()` | 丢弃日志（可观测性缺口） | M |
| P0-FW-06 | EventBuffer.drain | `drain()` | 清空返回全部 | S |
| P0-FW-07 | EventBuffer.drain 空 | `drain()` | 空缓冲返回 [] | S |
| P0-FW-08 | EventBuffer modified 去抖 | `add()` | 同文件多次合并 | M |
| P0-FW-09 | EventBuffer 线程安全 | `add()/drain()` | 多线程无错乱 | L |
| P0-FW-10 | PatternFilter 默认排除 | `PatternFilter` | .git/__pycache__/*.pyc | S |
| P0-FW-11 | PatternFilter include 命中 | `should_watch()` | 命中包含 | S |
| P0-FW-12 | PatternFilter exclude 命中 | `should_watch()` | 命中排除 | S |
| P0-FW-13 | PatternFilter include+exclude | `should_watch()` | include 优先 | S |
| P0-FW-14 | PatternFilter 无规则 | `should_watch()` | 默认放行 | S |
| P0-FW-15 | FileEventHandler on_created | `on_created()` | 入 buffer | S |
| P0-FW-16 | FileEventHandler on_modified | `on_modified()` | 入 buffer 去抖 | S |
| P0-FW-17 | FileEventHandler on_deleted | `on_deleted()` | 入 buffer | S |
| P0-FW-18 | FileEventHandler on_moved | `on_moved()` | 入 buffer | S |
| P0-FW-19 | FileEventHandler 过滤 | `on_*` | PatternFilter 拒绝 | S |
| P0-FW-20 | FileWatcher 构造 | `FileWatcher.__init__` | Observer 未启动 | S |
| P0-FW-21 | FileWatcher.add_watch | `add_watch()` | 注册路径 | M |
| P0-FW-22 | FileWatcher.add_watch 重复 | `add_watch()` | 重复路径忽略 | S |
| P0-FW-23 | FileWatcher.remove_watch | `remove_watch()` | 注销路径 | M |
| P0-FW-24 | FileWatcher.start | `start()` | Observer 启动 | M |
| P0-FW-25 | FileWatcher 满丢弃指标 | `EventBuffer.add()` | 埋点预留（可观测性缺口） | M |
| P0-FW-26 | FileWatcher.stop | `stop()` | Observer 停止 | M |
| P0-FW-27 | FileWatcher.get_events | `get_events()` | drain buffer | S |
| P0-FW-28 | FileWatcher 路径不存在 | `add_watch()` | 友好错误 | M |
| P0-FW-29 | FileWatcher 权限不足 | `add_watch()` | 友好错误 | M |
| P0-FW-30 | FileWatcher include 模式 | `add_watch(patterns=...)` | 仅监听匹配 | M |
| P0-FW-31 | FileEventHandler 异常 | `on_*` | 单文件异常不影响整体 | M |
| P0-FW-32 | EventBuffer 容量边界 | `add()` | max_events=1 | S |
| P0-FW-33 | PatternFilter 通配符 | `should_watch()` | `*`/`?` 模式 | M |

---

### 3.2 P1 阶段用例（71 个，预计覆盖率 +13%）

> 本阶段重点：通过 `monkeypatch` mock 掉 WMI/psutil/watchdog 等平台依赖，覆盖各传感器主流程、阈值边界与降级路径。

#### 3.2.1 cpu_sensor.py（CPU 系列，8 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P1-CPU-01 | collect 基本字段 | `collect()` | name/usage/cores 返回结构 | M |
| P1-CPU-02 | collect_frequency | `collect_frequency()` | WMI 缓存命中 | M |
| P1-CPU-03 | collect_temperature | `collect_temperature()` | perf_counters 路径 | M |
| P1-CPU-04 | collect_voltage 解码 | `collect_voltage()` | `voltage & 0x3F * 1/64` 位运算 | L |
| P1-CPU-05 | WMI 缓存未命中 | `collect_*` | 触发实际查询（mock WMI） | M |
| P1-CPU-06 | WMI 失败降级 | `collect_*` | except 分支返回 None | M |
| P1-CPU-07 | perf_counters 失败 | `collect_temperature()` | OSError 降级 | M |
| P1-CPU-08 | 平台非 Windows | `collect()` | 跳过 WMI 分支 | S |

#### 3.2.2 memory_sensor.py（MEM 系列，6 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P1-MEM-01 | collect_virtual | `collect_virtual()` | psutil.virtual_memory 返回 | M |
| P1-MEM-02 | collect_swap | `collect_swap()` | psutil.swap_memory 返回 | M |
| P1-MEM-03 | collect_modules | `collect_modules()` | WMI 查询 | M |
| P1-MEM-04 | DDR 代际推断 | `collect_modules()` | DDR3/4/5 推断 | L |
| P1-MEM-05 | psutil 失败降级 | `collect_*` | except 返回 None | M |
| P1-MEM-06 | collect_config | `collect_config()` | 配置查询 | S |

#### 3.2.3 battery_sensor.py（BAT 系列，5 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P1-BAT-01 | 5 级严重度边界 | `collect()` | 100/20/10/5/0 各档位 | L |
| P1-BAT-02 | 充电中 | `collect()` | charging 分支 | M |
| P1-BAT-03 | 放电时间 | `collect()` | discharging time 计算 | M |
| P1-BAT-04 | 无电池 | `collect()` | psutil 返回空 | S |
| P1-BAT-05 | 边界 100% | `collect()` | NORMAL 临界 | S |

#### 3.2.4 disk_sensor.py（DISK 系列，8 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P1-DISK-01 | 分区使用率 | `collect_partitions()` | psutil.disk_partitions | M |
| P1-DISK-02 | 阈值 95 CRITICAL | `collect_partitions()` | 临界值 | S |
| P1-DISK-03 | 阈值 85 WARNING | `collect_partitions()` | 临界值 | S |
| P1-DISK-04 | 阈值 84 NORMAL | `collect_partitions()` | 临界值 | S |
| P1-DISK-05 | IO rate 计算 | `collect_io()` | 速率换算 | L |
| P1-DISK-06 | IOPS | `collect_iops()` | 操作数计算 | M |
| P1-DISK-07 | active_time | `collect_active_time()` | 百分比计算 | M |
| P1-DISK-08 | 无磁盘 | `collect()` | psutil 返回空 | S |

#### 3.2.5 network_sensor.py（NET 系列，10 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P1-NET-01 | collect_interfaces | `collect_interfaces()` | psutil.net_io_counters | M |
| P1-NET-02 | collect_adapter_info | `collect_adapter_info()` | WMI 查询 | M |
| P1-NET-03 | collect_ip_config | `collect_ip_config()` | netsh 解析 | M |
| P1-NET-04 | collect_wifi_info | `collect_wifi_info()` | netsh wlan | M |
| P1-NET-05 | collect_connections | `collect_connections()` | psutil.net_connections | M |
| P1-NET-06 | netsh 解析多行 | `collect_ip_config()` | 多 IP 行 | L |
| P1-NET-07 | netsh 失败降级 | `collect_*` | 子进程异常 | M |
| P1-NET-08 | 无网卡 | `collect()` | psutil 返回空 | S |
| P1-NET-09 | IPv6 地址 | `collect_ip_config()` | IPv6 分支 | M |
| P1-NET-10 | 速率计算 | `collect_interfaces()` | bytes/sec | M |

#### 3.2.6 change_detector.py（CD 系列，15 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P1-CD-01 | 首次快照建立基线 | `take_snapshot()` | baseline_established | M |
| P1-CD-02 | 二次快照对比 | `take_snapshot()` | 无变化 | M |
| P1-CD-03 | devices 变化检测 | `take_snapshot()` | 设备增删 | L |
| P1-CD-04 | partitions 变化 | `take_snapshot()` | 分区增删 | L |
| P1-CD-05 | processes 变化 | `take_snapshot()` | 进程增删 | L |
| P1-CD-06 | services 变化 | `take_snapshot()` | 服务状态变化 | L |
| P1-CD-07 | registry 变化 | `take_snapshot()` | 注册表键变化 | L |
| P1-CD-08 | environment 变化 | `take_snapshot()` | 环境变量变化 | M |
| P1-CD-09 | system_info 变化 | `take_snapshot()` | 系统信息变化 | M |
| P1-CD-10 | register_change_from_event | `register_change_from_event()` | 事件转变更登记 | M |
| P1-CD-11 | _load_persistent_log 成功 | `_load_persistent_log()` | JSON 加载 | M |
| P1-CD-12 | _load_persistent_log 文件缺失 | `_load_persistent_log()` | 文件不存在分支 | M |
| P1-CD-13 | _load_persistent_log 损坏 | `_load_persistent_log()` | JSON 解析失败（可观测性缺口） | M |
| P1-CD-14 | _save_persistent_log | `_save_persistent_log()` | 持久化写入 | M |
| P1-CD-15 | get_changes_since | `get_changes_since()` | 时间窗口查询 | M |

#### 3.2.7 event_monitor.py（EM 系列，15 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P1-EM-01 | 构造默认 | `__init__` | 配置加载 | S |
| P1-EM-02 | enable_fast_path | `enable_fast_path()` | 启用快速通道 | S |
| P1-EM-03 | lazy_startup_change_detection | `lazy_startup_change_detection()` | 异步线程启动 | L |
| P1-EM-04 | 启动检测完成回调 | `lazy_startup_change_detection()` | 完成后通知 | L |
| P1-EM-05 | wmic_optimized | `wmic_optimized()` | 优化查询路径 | M |
| P1-EM-06 | wmic 失败降级 | `wmic_optimized()` | 子进程异常 | M |
| P1-EM-07 | 设备清单缓存命中 | `_get_device_manifest()` | 二次返回缓存 | M |
| P1-EM-08 | 设备清单缓存失效 | `_get_device_manifest()` | 触发刷新 | M |
| P1-EM-09 | fallback polling | `_fallback_poll()` | 异步失败后兜底 | M |
| P1-EM-10 | 事件分发 | `_dispatch_event()` | 事件路由 | M |
| P1-EM-11 | 订阅注册 | `subscribe()` | 回调注册 | S |
| P1-EM-12 | 取消订阅 | `unsubscribe()` | 回调移除 | S |
| P1-EM-13 | 线程异常未捕获 | `lazy_startup_change_detection()` | 异步异常丢失（可观测性缺口） | L |
| P1-EM-14 | start/stop 生命周期 | `start()/stop()` | 启停控制 | M |
| P1-EM-15 | 平台非 Windows | `start()` | 跳过 wmic 分支 | S |

#### 3.2.8 process_sensor.py（PROC 系列，4 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P1-PROC-01 | top 进程 | `collect_top()` | CPU/内存排序 | M |
| P1-PROC-02 | 敏感关键字过滤 | `collect()` | `_SENSITIVE_CMDLINE_KEYWORDS` 命中脱敏 | L |
| P1-PROC-03 | 进程数边界 | `collect()` | 0 进程 / 大量进程 | M |
| P1-PROC-04 | psutil 失败降级 | `collect()` | except 分支 | M |

---

### 3.3 P2 阶段用例（73 个，预计覆盖率 +8%）

> 本阶段重点：跨平台分支、线程安全、可选 import 降级、与第三方库的交互。允许使用 `pytest.importorskip` 与 `mock.patch`。

#### 3.3.1 gpu_sensor.py（GPU 系列，6 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P2-GPU-01 | pynvml 主路径 | `collect()` | pynvml 可用 | M |
| P2-GPU-02 | pynvml 失败降级 GPUtil | `collect()` | 第一降级 | L |
| P2-GPU-03 | GPUtil 失败降级 nvidia-smi | `collect()` | 第二降级 | L |
| P2-GPU-04 | 全部失败返回空 | `collect()` | 三源全失败 | M |
| P2-GPU-05 | 模块级 ImportError 记录 | 模块 import | 降级标记缺失（可观测性缺口） | L |
| P2-GPU-06 | 显存使用率 | `collect()` | mem utilization | M |

#### 3.3.2 board_sensor.py（BOARD 系列，4 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P2-BOARD-01 | Windows WMI 路径 | `collect()` | wmic 查询 | M |
| P2-BOARD-02 | Linux dmidecode 路径 | `collect()` | /sys/class 路径 | M |
| P2-BOARD-03 | macOS sysctl 路径 | `collect()` | system_profiler | M |
| P2-BOARD-04 | 三平台失败降级 | `collect()` | 全部 except | M |

#### 3.3.3 chassis_sensor.py（CHAS 系列，5 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P2-CHAS-01 | Windows WMI 路径 | `collect()` | wmic chassis | M |
| P2-CHAS-02 | SecurityBreach=4 CRITICAL | `collect()` | 安全事件临界 | M |
| P2-CHAS-03 | SecurityBreach=5 CRITICAL | `collect()` | 安全事件临界 | M |
| P2-CHAS-04 | 非 Windows 路径 | `collect()` | dmidecode/profiler | M |
| P2-CHAS-05 | 失败降级 | `collect()` | except 返回 None | S |

#### 3.3.4 port_sensor.py（PORT 系列，3 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P2-PORT-01 | USB/COM/LPT 枚举 | `collect()` | WMI 查询 | M |
| P2-PORT-02 | PCIe 设备 | `collect()` | wmic path 枚举 | M |
| P2-PORT-03 | 非 Windows 跳过 | `collect()` | 平台分支 | S |

#### 3.3.5 peripheral_sensor.py（PERI 系列，3 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P2-PERI-01 | 显示器枚举 | `collect()` | WMI 桌面监视器 | M |
| P2-PERI-02 | SMART 数据 | `collect()` | wmic diskdrive | M |
| P2-PERI-03 | 非 Windows 跳过 | `collect()` | 平台分支 | S |

#### 3.3.6 behavior_sensor.py（BEH 系列，8 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P2-BEH-01 | 磁盘行为维度 | `collect_disk()` | IO 行为基线 | M |
| P2-BEH-02 | CPU 行为维度 | `collect_cpu()` | 使用率基线 | M |
| P2-BEH-03 | 内存行为维度 | `collect_memory()` | 内存基线 | M |
| P2-BEH-04 | 用户行为维度 | `collect_user()` | 登录/会话 | M |
| P2-BEH-05 | 网络行为维度 | `collect_network()` | 流量基线 | M |
| P2-BEH-06 | 服务行为维度 | `collect_service()` | 服务变更 | M |
| P2-BEH-07 | 聚合 collect | `collect()` | 6 维度合并 | M |
| P2-BEH-08 | 基线异常 | `collect_*` | 阈值告警 | L |

#### 3.3.7 system_sensor.py（SYS 系列，6 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P2-SYS-01 | _wmi_get CSV 解析 | `_wmi_get()` | CSV 字段拆分 | L |
| P2-SYS-02 | _wmi_get 失败 | `_wmi_get()` | except 返回空 | M |
| P2-SYS-03 | 10 维度齐备 | `collect()` | 全字段返回 | M |
| P2-SYS-04 | 单维度失败 | `collect_*` | 部分降级 | M |
| P2-SYS-05 | 非 Windows 跳过 | `collect()` | 平台分支 | S |
| P2-SYS-06 | 字段空值 | `collect()` | 缺字段默认 | M |

#### 3.3.8 environment_sensor.py（ENV 系列，7 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P2-ENV-01 | _KEY_MODULES 完整 | `_KEY_MODULES` | 关键模块列表 | S |
| P2-ENV-02 | _check_import 命中 | `_check_import()` | 模块存在 | S |
| P2-ENV-03 | _check_import 缺失 | `_check_import()` | ImportError | S |
| P2-ENV-04 | 服务状态查询 | `collect_services()` | wmic service | M |
| P2-ENV-05 | 服务状态失败 | `collect_services()` | except 降级 | M |
| P2-ENV-06 | 环境变量 | `collect_env()` | os.environ | S |
| P2-ENV-07 | 平台非 Windows | `collect()` | 跳过分支 | S |

#### 3.3.9 hardware_blueprint.py（BP 系列，3 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P2-BP-01 | 解剖清单字段 | `collect()` | 字段完整 | S |
| P2-BP-02 | 失败降级 | `collect()` | except | M |
| P2-BP-03 | 静态数据正确性 | `_ANATOMY` | 默认值断言 | S |

#### 3.3.10 file_blueprint.py（FBP 系列，3 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P2-FBP-01 | 文件系统解剖 | `collect()` | 字段完整 | S |
| P2-FBP-02 | 失败降级 | `collect()` | except | M |
| P2-FBP-03 | 静态数据正确性 | `_ANATOMY` | 默认值断言 | S |

#### 3.3.11 software_blueprint.py（SBP 系列，2 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P2-SBP-01 | 软件生态 | `collect()` | 字段完整 | S |
| P2-SBP-02 | 失败降级 | `collect()` | except | M |

#### 3.3.12 hardware_file_sensor.py（HWF 系列，7 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P2-HWF-01 | 跨平台路径工具 | `_path_utils` | Windows/Linux 路径 | M |
| P2-HWF-02 | 文件存在检查 | `collect()` | os.path.exists | S |
| P2-HWF-03 | 文件读取 | `collect()` | 编码处理 | M |
| P2-HWF-04 | 文件不存在降级 | `collect()` | FileNotFoundError | M |
| P2-HWF-05 | 权限不足降级 | `collect()` | PermissionError | M |
| P2-HWF-06 | 编码错误降级 | `collect()` | UnicodeDecodeError | M |
| P2-HWF-07 | 大文件处理 | `collect()` | 边界 | L |

#### 3.3.13 counter_reader.py（CR 系列，8 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P2-CR-01 | PowerShell 计数 | `read_*_powershell()` | 子进程输出 | M |
| P2-CR-02 | wmic 计数 | `read_*_wmic()` | 子进程输出 | M |
| P2-CR-03 | 注册表计数 | `read_*_registry()` | reg query | M |
| P2-CR-04 | PowerShell 输出解析 | `read_*_powershell()` | CSV/表格解析 | L |
| P2-CR-05 | 子进程超时 | `read_*` | TimeoutExpired | M |
| P2-CR-06 | 子进程非零退出 | `read_*` | returncode != 0 | M |
| P2-CR-07 | 平台非 Windows | `read_*` | 跳过分支 | S |
| P2-CR-08 | 失败静默 None | `read_*` | 返回 None（可观测性缺口） | M |

#### 3.3.14 ocr_sensor.py（OCR 系列，3 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P2-OCR-01 | numpy/cv2 可用 | `collect()` | 主路径 | M |
| P2-OCR-02 | 可选 import 缺失 | 模块 import | ImportError 降级 | M |
| P2-OCR-03 | pytesseract 失败 | `collect()` | except 返回空 | M |

#### 3.3.15 voice_sensor.py（VOICE 系列，3 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P2-VOICE-01 | TTSEngine gTTS 主路径 | `TTSEngine.speak()` | 文件生成 | M |
| P2-VOICE-02 | TTSEngine pyttsx3 备用 | `TTSEngine.speak()` | 阻塞模式 | M |
| P2-VOICE-03 | 无可用引擎 | `TTSEngine.speak()` | 返回失败 | S |
| P2-VOICE-04 | STT 麦克风失败 | `STTEngine._init_engine()` | OSError 降级（可观测性缺口） | L |

#### 3.3.16 window_sensor.py（WIN 系列，2 个）

| 用例 ID | 测试目标 | 被测函数/方法 | 核心分支 | 工作量 |
|---|---|---|---|---|
| P2-WIN-01 | 配置加载/保存 | `_load_config()/save_config()` | JSON 读写 | M |
| P2-WIN-02 | win32gui 不可用降级 | 模块 import | HAS_WIN32=False 跳过 | S |

---

## 四、工作量评估

### 4.1 用例工作量分布

| 工作量 | P0 | P1 | P2 | 合计 | 占比 |
|---|---|---|---|---|---|
| S（<0.5 人时） | 68 | 18 | 18 | 104 | 41% |
| M（0.5-1.5 人时） | 33 | 47 | 41 | 121 | 48% |
| L（>1.5 人时） | 2 | 6 | 9 | 17 | 7% |
| **合计** | **103** | **71** | **68** | **242** | 100% |

> 注：P0 实际可执行用例 103 个（原计划 109 含 6 个公共夹具/初始化用例，已在 P0-BS/FW 中合并）。

### 4.2 工时估算（人日）

| 阶段 | S 用例 | M 用例 | L 用例 | 小计 | 联调/返工 | 阶段合计 |
|---|---|---|---|---|---|---|
| P0 | 68×0.3=2.0 | 33×1.0=3.3 | 2×2.0=0.4 | 5.7 | 1.0 | 6.7 |
| P1 | 18×0.3=0.5 | 47×1.0=4.7 | 6×2.0=1.2 | 6.4 | 1.5 | 7.9 |
| P2 | 18×0.3=0.5 | 41×1.0=4.1 | 9×2.0=1.8 | 6.4 | 1.0 | 7.4 |
| **合计** | 3.0 | 12.1 | 3.4 | 18.5 | 3.5 | **22.0 人日** |

> 含联调返工余量，净开发约 18.5 人日。

### 4.3 分阶段覆盖率预估

| 阶段 | 累计覆盖率 | 关键文件 | 验收阈值 |
|---|---|---|---|
| P0 完成 | 21% | sensor_reading/tags/registry/body_sensor/file_watcher | ≥ 18% |
| P1 完成 | 34% | cpu/memory/battery/disk/network/change_detector/event_monitor | ≥ 30% |
| P2 完成 | 42% | gpu/board/chassis/port/peripheral/behavior/system/environment/blueprint/voice/ocr/window | ≥ 40% |

---

## 五、实施路线图

### 5.1 里程碑

| 里程碑 | 截止 | 交付物 | 负责角色 |
|---|---|---|---|
| M1：P0 完成 | T+6 人日 | `tests/unit/test_sensor_reading.py` 等 5 个文件，覆盖率≥18% | 测试工程师 A |
| M2：P1 完成 | T+14 人日 | 新增 8 个测试文件，覆盖率≥30% | 测试工程师 A+B |
| M3：P2 完成 | T+22 人日 | 新增 16 个测试文件，覆盖率≥40% | 测试工程师 B |

### 5.2 测试文件组织

```
tests/unit/
├── test_sensor_reading.py       # P0-SR (10)
├── test_tags.py                 # P0-TG (10)
├── test_registry.py             # P0-RG (14)
├── test_body_sensor.py          # P0-BS + P1 补充（新建，覆盖过时的 sensor/test_body_sensor.py）
├── test_file_watcher.py         # P0-FW (33)
├── test_cpu_sensor.py           # P1-CPU (8)
├── test_memory_sensor.py        # P1-MEM (6)
├── test_battery_sensor.py       # P1-BAT (5)
├── test_disk_sensor.py          # P1-DISK (8)
├── test_network_sensor.py       # P1-NET (10)
├── test_change_detector.py      # P1-CD (15)
├── test_event_monitor.py        # P1-EM (15)
├── test_process_sensor.py       # P1-PROC (4)
├── test_gpu_sensor.py           # P2-GPU (6)
├── test_board_chassis_sensor.py # P2-BOARD/CHAS (9)
├── test_port_peripheral_sensor.py # P2-PORT/PERI (6)
├── test_behavior_sensor.py      # P2-BEH (8)
├── test_system_sensor.py        # P2-SYS (6)
├── test_environment_sensor.py   # P2-ENV (7)
├── test_blueprint.py            # P2-BP/FBP/SBP (8)
├── test_hardware_file_sensor.py # P2-HWF (7)
├── test_counter_reader.py       # P2-CR (8)
├── test_voice_sensor.py         # P2-VOICE (3)
├── test_ocr_window_sensor.py   # P2-OCR/WIN (5)
```

> 同时将过时的 `sensor/test_body_sensor.py` 标记为 `@pytest.mark.skip(reason="已被 tests/unit/test_body_sensor.py 取代")` 或删除。

### 5.3 责任分工

| 角色 | 职责 | 阶段 |
|---|---|---|
| 测试工程师 A | P0 全部 + P1 CPU/MEM/BAT/DISK/NET | M1+M2 |
| 测试工程师 B | P1 CD/EM/PROC + P2 全部 | M2+M3 |
| 后端开发 | 提供接口契约、协助 mock WMI/psutil 行为 | 全程 |
| 架构师 | 评审可观测性缺口补全方案 | M1 前 |

---

## 六、Mock 策略参考

### 6.1 WMI mock 范式

```python
# 状态同步说明：本测试使用 monkeypatch 注入 mock WMI 返回值，
# 避免真实平台依赖，配合 Request ID 校验防止异步竞态。
import pytest
from unittest.mock import MagicMock

@pytest.fixture
def mock_wmi(monkeypatch):
    fake_wmi = MagicMock()
    fake_wmi.query.return_value = [{"Name": "TestCPU", "MaxClockSpeed": 3200}]
    monkeypatch.setattr("wmi.WMI", lambda: fake_wmi)
    return fake_wmi

def test_cpu_collect_basic(mock_wmi):
    from sensor.cpu_sensor import CPUSensor
    result = CPUSensor().collect()
    assert result["name"] == "TestCPU"
```

### 6.2 psutil mock 范式

```python
@pytest.fixture
def mock_psutil(monkeypatch):
    fake = MagicMock()
    fake.virtual_memory.return_value = MagicMock(total=8*1024**3, available=4*1024**3)
    monkeypatch.setattr("sensor.memory_sensor.psutil", fake)
    return fake
```

### 6.3 平台切换

```python
@pytest.mark.parametrize("platform,expected", [
    ("win32", True), ("linux", False), ("darwin", False),
])
def test_platform_branch(monkeypatch, platform, expected):
    monkeypatch.setattr("sensor.registry.sys.platform", platform)
    # 断言传感器是否被注册
```

### 6.4 子进程 mock

```python
def test_powershell_failure(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=5)
    monkeypatch.setattr("sensor.counter_reader.subprocess.run", fake_run)
    # 状态同步说明：使用 Optimistic Rollback，验证降级返回 None 后状态一致
```

---

## 七、可观测性改进建议

> 以下建议**不在本计划范围内执行**（约束：不修改生产代码），仅作为后续迭代的改进方向，供架构师评审。

| 编号 | 缺口位置 | 建议 | 优先级 |
|---|---|---|---|
| OBS-01 | `body_sensor._apply_tags` | 将 `except Exception: pass` 改为记录 `trace_id` 的结构化日志，并抛出明确业务错误码 `SENSOR_TAG_APPLY_FAILED` | 高 |
| OBS-02 | `body_sensor` | 新增 `health()` 方法返回依赖（WMI/psutil/watchdog）连接状态，供上层 `/health` 端点聚合 | 高 |
| OBS-03 | `change_detector._load_persistent_log` | 静默失败改为 `logger.warning` 含 `trace_id`，并在返回值中标记 `degraded=True` | 中 |
| OBS-04 | `gpu_sensor` 模块级 import | 增加 `degraded_sources` 列表，`collect()` 时记录降级路径 | 中 |
| OBS-05 | `event_monitor` 异步线程 | 包装线程入口为 `try/except`，异常通过回调通知，并埋点 `trackEvent('sensor_event_monitor_failed', {...})` | 高 |
| OBS-06 | `file_watcher.EventBuffer` 满 | 满丢弃时调用 `trackEvent('sensor_file_event_dropped', {'count': N})` | 中 |
| OBS-07 | `counter_reader` 静默 None | 返回 `(value, error_code)` 元组或抛 `CounterReadError` | 低 |
| OBS-08 | `voice_sensor` STT 失败 | `available=False` 时记录结构化日志含 `module_name='voice_sensor'`、`action='stt_init'` | 中 |

---

## 八、验收清单

### 8.1 阶段验收

- [ ] M1：P0 用例 103 个全部通过，`pytest --cov=sensor --cov-report=term-missing` 显示模块覆盖率≥18%
- [ ] M2：P1 用例 71 个全部通过，累计覆盖率≥30%
- [ ] M3：P2 用例 68 个全部通过，累计覆盖率≥40%
- [ ] 过时 `sensor/test_body_sensor.py` 已迁移或删除
- [ ] 所有测试在 Windows 平台通过（主目标平台）
- [ ] 所有测试在无 GPU/无电池环境通过（降级路径）
- [ ] 测试执行时间≤30 分钟（符合项目质量门禁）
- [ ] 单元测试通过率≥95%

### 8.2 质量门禁对齐

| 门禁项 | 阈值 | 验证方式 |
|---|---|---|
| 新增代码覆盖率 | ≥80% | `pytest-cov` |
| 单元测试通过率 | ≥95% | `pytest` |
| 静态扫描问题数 | ≤5/千行 | `flake8`/`pylint` |
| 安全扫描高危漏洞 | =0 | `bandit` |
| 测试执行时间 | ≤30 分钟 | `pytest --durations=10` |
| 严重缺陷逃逸 | =0 | 人工评审 |

---

## 九、附录

### 9.1 优先级定义

| 优先级 | 定义 | SLA |
|---|---|---|
| P0 | 纯函数/数据类，无外部依赖，阻塞后续阶段 | M1 内完成 |
| P1 | 主流程覆盖，依赖 mock，阻塞 40% 目标 | M2 内完成 |
| P2 | 平台/并发/可选依赖，锦上添花 | M3 内完成 |

### 9.2 工作量定义

| 等级 | 工时 | 典型场景 |
|---|---|---|
| S | <0.5 人时 | 枚举断言、字段存在性、空值边界 |
| M | 0.5-1.5 人时 | 单方法主流程 + mock 一个外部依赖 |
| L | >1.5 人时 | 多源降级链、异步线程、CSV/位运算解析、并发竞态 |

### 9.3 常用命令参考

```bash
# 运行全部 sensor 测试
pytest tests/unit/test_*sensor*.py tests/unit/test_body_sensor.py tests/unit/test_file_watcher.py tests/unit/test_change_detector.py tests/unit/test_event_monitor.py tests/unit/test_registry.py tests/unit/test_tags.py tests/unit/test_sensor_reading.py -v

# 覆盖率报告
pytest tests/unit/ --cov=sensor --cov-report=html --cov-report=term-missing --cov-config=.coveragerc

# 仅运行 P0（按 marker）
pytest tests/unit/ -m "p0" -v

# 仅运行慢测试
pytest tests/unit/ --runslow -v

# 性能基线
pytest tests/unit/ --durations=10 -v
```

### 9.4 既有 fixture 复用

`tests/conftest.py` 已提供：
- `sample_sensor_data`：示例传感器数据
- `p0` / `p1` marker：优先级标记
- `--runslow` 选项：慢测试开关
- `TEST_CONFIG`：含 `coverage_threshold: 70`

新增测试应复用上述 fixture，并通过 `@pytest.mark.p0` / `@pytest.mark.p1` 标注优先级。

---

> 文档结束。本计划遵循"存在即可见"原则，所有静默失败分支已标注可观测性缺口并预留覆盖用例。执行过程中如发现新增静默分支，应即时补充 OBS 条目并评估是否升级为 P0。


