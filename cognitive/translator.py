# cognitive/translator.py
import logging
import re

logger = logging.getLogger(__name__)


def _strip_ws(s: str) -> str:
    """去掉所有空白字符。

    用途：比较"描述里已含的值"与 `value` 字段。两者书写常有一处空格之差，实测例如
        desc='页错误 #1: node.exe (PID 19784) 539649695 次'   value=539649695
        desc='系统已运行: 12天 2小时 41分钟'                    value='12天 2小时 41分钟'
    直接字符串比较会把它们判为"不同"从而重复追加，故统一去空白后再比。
    """
    return "".join(s.split())


class Translator:
    """拟人化翻译引擎。

    将传感器数值根据配置规则翻译为第一人称拟人化描述。
    """

    def __init__(self, config):
        self.config = config

    def translate(self, reading: dict) -> str:
        """将单条传感器读数翻译为拟人化描述。

        匹配逻辑：按 sensor_name 查找规则 → 遍历 thresholds 找值所在区间 → 返回 message。
        区间约定：min <= value < max（左闭右开）。

        【`reading` 的类型不兼容 · 2026-09-19 实测】
        本方法首行原是 `if not isinstance(reading, dict): return "传感器读数未识别"`。
        而 `BodySensor.collect_all()` 返回的是 **`SensorReading` 对象列表**
        ⇒ **该守卫把全部真实读数拦在函数入口**，规则查找与 thresholds 遍历从未执行。

        实测（本机，非推测）：
            `BodySensor().collect_all()` -> 652 条，类型分布 `{'SensorReading': 652}`
            `translate_all(同 652 条)`    -> 652 条**全部**为 "传感器读数未识别"（100%）
            首条实际值：sensor_name='behavior_disk_total_read'
                        value=68150101  unit='次'  description='磁盘总读取次数'
            这些字段**全部存在且完好**，只是装在对象上而不是 dict 里。

        故 `reading` 现可传 **dict**，也可传 `SensorReading` 之类的**对象**（见 `_coerce`）。

        【噪声的成因是**两层**，不要只归因于一层】
          第 1 层（决定性、此前被漏掉）：**类型不兼容**。对象撞守卫 ⇒ 100% 走"未识别"，
                 与规则多少无关。修好这层后未识别率由 100% 降到 **0.15%**（652 → 1 条）。
          第 2 层：**规则覆盖率低**。真实采集有 640+ 个不同 `sensor_name`，
                 而 `PromptConfig` 默认只有 5 条规则 ⇒ 绝大多数读数无规则可命中。
                 此时若不调 `_fallback`，即便类型修好了也仍会返回固定串。
        ⇒ 只修任一层都不够，这就是二者必须同时处理的原因。

        【`_fallback()` 从未被调用的缺陷（第 2 层的修法）】
        `_fallback()` 会保留 `description` / `sensor_name` 与 `value`，但 `translate()`
        的 6 个兜底出口此前全部返回硬编码 `"传感器读数未识别"`，从不调用它。

        两层叠加的后果：`PromptInjector.inject()` 拼进 LLM 系统提示词的 `body_status`
        被"传感器读数未识别"刷满。按**真实生产路径**
        （`agent/digital_life_persona.py::_build_body_status`：`to_dict()` → `inject()` → 截断 800 字符）
        复测：修复前那 800 字符**全部是噪声行**；修复后为 **20 行真实读数、0 噪声**。

        Why 分情况而不是无条件走 `_fallback`：
            `_fallback` 对**残缺输入**会产生垃圾输出（实测）：
                `{"sensor_name": "", "value": 50.0}`   -> `": 50.0"`
                `{"sensor_name": None, "value": 50.0}` -> `"None: 50.0"`
                `{"sensor_name": "cpu_temperature"}`   -> `"cpu_temperature: "`（缺 value）
            这类输入应当**保持"未识别"**——它是有意义的**探测信号**（说明上游给的数据本身残缺），
            而不是可以编出人话的场景。`tests/test_cognitive_boundary.py` 的 9 处断言
            正是在锁定这条语义，**它们是合理契约**，故本修复不触碰它们。
            真正要修的是"**有名字、有值、只是没有匹配规则**"这一类 —— 那才是真实场景的全貌。

        【诊断教训（写下来防止重犯）】
        单测此前全绿，是因为测试夹具**传 dict**，而生产路径**传对象**——
        典型的"测试夹具冒充生产"。任何"修复已生效"的结论都必须用
        `BodySensor().collect_all()` 的真实产物复测一次，不能只看单测。
        """
        reading = self._coerce(reading)
        if reading is None:
            return "传感器读数未识别"

        sensor_name = reading.get("sensor_name", "")

        if not sensor_name or not str(sensor_name).strip():
            return "传感器读数未识别"

        value = reading.get("value", 0)

        if value is None:
            return "传感器读数未识别"

        # 【不易·2026-09-19】NaN 必须显式判掉，不能留给下游比较：
        #   `float('nan')` 与任何值比较都是 False，既不会命中任何 threshold 区间、
        #   也不会触发 TypeError（所以原有的 `except TypeError` 兜不住它），
        #   于是会一路落到"无规则命中"分支 —— 而那里现在会调 `_fallback`，
        #   产出 `'cpu_temperature: nan'` 这种**看着像人话、其实是垃圾**的文本。
        #   实测：`tests/test_cognitive_boundary.py::TestEdgeCases::test_nan_value`
        #   正是锁定这条语义（NaN ⇒ 未识别），它是合理契约，故在此显式保持。
        if isinstance(value, float) and value != value:  # NaN 的唯一可靠判据
            return "传感器读数未识别"

        # 有名字有值但**没有匹配规则** ⇒ 走 `_fallback` 给出可读描述（本次修复的核心）
        # 注意：这里不看 `description` 是否存在 —— `_fallback` 在缺 description 时会回落到
        # `sensor_name`，同样比"未识别"更有信息量。
        rule = self.config.get_rule(sensor_name)
        if not rule or "thresholds" not in rule:
            return self._fallback(reading)

        if isinstance(value, str):
            try:
                value = float(value)
            except (ValueError, TypeError):
                # 有值但无法转数 ⇒ 仍是"残缺输入"，保留探测信号
                return "传感器读数未识别"

        try:
            for threshold in rule["thresholds"]:
                lo = threshold.get("min", float("-inf"))
                hi = threshold.get("max", float("inf"))
                if hi == float("inf"):
                    if lo <= value:
                        return threshold["message"]
                elif lo == float("-inf"):
                    if value < hi:
                        return threshold["message"]
                else:
                    if lo <= value < hi:
                        return threshold["message"]
        except TypeError:
            return "传感器读数未识别"

        # 有规则、有值，但没有任何 threshold 区间命中 ⇒ 同样是"未被任何规则覆盖"
        return self._fallback(reading)
    
    @staticmethod
    def _coerce(reading):
        """把一条读数统一成"只含本模块所需字段的 dict"；不可处理时返回 None。

        为什么需要它（实测根因，不是设计洁癖）：
            `BodySensor.collect_all()` 产出 **`SensorReading` 对象**（本机实测 652/652 条），
            而本模块一律用 `reading.get(...)` 取值 ⇒ 对象会直接撞上 `AttributeError`。
            此前靠 `isinstance(reading, dict)` 守卫"挡住"了崩溃，代价是**全部真实读数
            都被降级成"未识别"**——一个静默失效（silent failure），比崩溃更难发现。

        接受范围（**刻意收窄**，为保住既有契约）：
            · dict                       -> 原样返回（深拷无必要，本方法只读不改）
            · 有 `__dict__` 的对象        -> 提取必需字段，构造**新的最小 dict**
                                           （不整对象 dump、不 `vars()` 全量搬运，
                                             避免把 metadata 等宿主引用带进后续流程）
            其它（str / int / list / None / 标量…）-> None
            —— 这条边界必须保留：`translate_all("abc")` 依赖 `str.get` 抛 TypeError
               （`tests/test_cognitive_boundary.py::test_non_list_input` 锁定），
               `test_list_with_non_dict` 亦要求非 dict 元素不产生垃圾描述。
        """
        if isinstance(reading, dict):
            return reading

        # `__dict__` 也存在于函数/模块等对象上，故仅接受带实例属性表的普通对象；
        # 显式排除内建标量与序列，防止 str/list 之类意外混入。
        if reading is None or isinstance(reading, (str, bytes, int, float, bool, list, tuple, set)):
            return None

        data = getattr(reading, "__dict__", None)
        if not isinstance(data, dict):
            return None

        # 【不易·2026-09-19】**只搬真实存在的字段，不注入默认值**。
        # 起初我写成 `data.get("value", 0)` 想"兜底"，实测反而制造出垃圾：
        # 缺 `value` 的对象被补成 0 后，`_fallback` 会产出 `'x: '`（值实际为 None，
        # 格式化出空串），比"未识别"更难诊断。忠实搬运才能让
        # "缺字段"这一残缺信号原样传到 `translate()` 的守卫里。
        out = {}
        for key in ("sensor_name", "value", "unit", "description", "severity", "tags"):
            if key in data:
                out[key] = data[key]
        return out

    def _is_valid_value(self, value):
        """检查值是否为有效的数值类型"""
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def translate_all(self, readings: list[dict]) -> list[str]:
        """批量翻译多条传感器读数"""
        return [self.translate(r) for r in readings]

    def get_status_line(self, readings: list[dict]) -> str:
        """生成一句话综合状态摘要"""
        descriptions = self.translate_all(readings)
        alerts = []
        normals = []
        for r, desc in zip(readings, descriptions):
            if r.get("severity") in ("warning", "critical"):
                alerts.append(desc)
            else:
                normals.append(desc)
        parts = []
        if alerts:
            parts.append("；".join(alerts[:3]))
        if normals:
            parts.append("；".join(normals[:2]))
        return "，".join(parts) if parts else "一切正常"

    # 【不易·2026-09-19 实测】`unit` 字段在真实采集里并不总是"单位"：
    #   658 条读数中有 3 条 `unit='bool'`（机箱物理锁定 / HDMI 音频 / 变更检测基准），
    #   直接拼接会产出 `'变更检测基准已建立（免疫系统初始化）: Truebool'` 这种
    #   **类型名冒充单位**的垃圾 —— 且它看起来"像人话"，比显式报错更难发现。
    # 处置：宁可省略单位，也不注入类型名。
    _GENERIC_UNITS = frozenset({
        "bool", "int", "str", "list", "dict", "float", "NoneType", "tuple", "set", "object",
    })

    # 【不易·2026-09-19】"描述里是否已含该值"的**三种真实形态**（均取自实测输出）：
    #   1) 单位紧贴：  `'用户态 CPU: 11.0%'`                      value=11.0
    #   2) 单位带空格：`'页错误 #1: node.exe (PID 19784) 539649695 次'` value=539649695
    #   3) 反向包含：  `'系统已运行: 12天 2小时 41分钟'`              value='12天 2小时 41分钟'
    #   4) 纯数值：    `'磁盘总读取次数: 68272432'`                 value=68272432
    # 用右锚定正则**搜索**（而非匹配）以取"最右侧"的数值——这恰好避开
    # `'CPU 负载平均 (1/5/15分钟): 0.00 / 0.00 / 0.00'` 这类取首个数会误判的坑。
    _NUMBER = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
    # `_NUMBER` 是**正则源串**，供下面 `compile` 复用；需要直接 `findall` 时用这个已编译版本
    _NUMBER_RE = re.compile(_NUMBER)
    _UNIT = r"(?:[%℃℉°]|[A-Za-z]+|[\u4e00-\u9fff])"
    # 数值 + 可选单位（单位前后允许空格），要求其后不再接数字/字/单位符
    _TAIL_WITH_UNIT = re.compile(
        r"(" + _NUMBER + r")\s*(" + _UNIT + r")?(?![\w.%℃℉°])"
    )
    # `value` 是否本身就是一个纯数值（复合值如 '843480906/0' 不适用数值比较）
    _PURE_NUMBER = re.compile(r"^\s*[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\s*$")

    @staticmethod
    def _already_carries(desc: str, value) -> bool:
        """`description` 是否**已经**表达了该值，从而无需再追加。

        【不易·2026-09-19 实测规模】真实采集里大量 `description` 已含数值（宽松口径约四成）：
            desc='用户态 CPU: 11.0%'                        value=11.0
            desc='磁盘总读取数据量: 4237.57 GB'              value=4237.57  unit='GB'
            desc='页错误 #1: node.exe (PID 19784) 539649695 次'  value=539649695
            desc='系统已运行: 12天 2小时 41分钟'              value='12天 2小时 41分钟'
        不处理的话 `_fallback` 会渲染成 `'用户态 CPU: 11.0%: 11.0%'`、
        `'系统已运行: 12天 2小时 41分钟: 12天 2小时 41分钟'` —— 整段重复。

        判据刻意保守：只认"描述**结尾**那一段与 `value` 相等"，**不用**
        `str(value) in desc` 这种子串判断（`value=41` 会误命中 `'12天 2小时 41分钟'`，
        `value=3` 会误命中 `'CPU 负载: 0.30'`）。比较用去空白的归一化形式，
        以兼容 `'539649695 次'` 与 `'539649695次'` 的书写差异。
        """
        if value is None or value == "":
            return False
        vstr = str(value).strip()
        if not vstr:
            return False
        vnorm = _strip_ws(vstr)

        d = desc.rstrip()

        # (3) 反向包含（**最强判据，优先**）：去空白后 `value` 整体出现在描述末尾。
        #     能覆盖复合值：`'系统已运行: 12天 2小时 41分钟'`（value='12天 2小时 41分钟'）、
        #     `'页错误 #1: … 539649695 次'`（value=539649695，只差一个空格）。
        #     以 `endswith` 锚定结尾，避免 `value=41` 误命中描述中段的 `'12天 … 41分钟'`。
        if len(vnorm) >= 2 and _strip_ws(d).endswith(vnorm):
            return True

        # (1) 描述最右数值 == value 的最右数值。
        #     **仅在 `value` 本身是纯数值时启用**：复合值（`'843480906/0'`）的最右数值是
        #     `'0'`，会与 `'… 非自愿 0'` 这类描述误配 —— 那属于误判，不能用来抑制追加。
        if Translator._PURE_NUMBER.match(vstr):
            m = None
            for m in Translator._TAIL_WITH_UNIT.finditer(d):  # 取最右一个匹配
                pass
            if m and m.group(1) == vstr:
                return True

        # (5) 数字序列末尾对齐：把 `value` 与描述都拆成数字 token，比较**末尾相同长度**的序列。
        #     覆盖数值比较覆盖不到的复合值（`'849111235/0'` vs `'自愿 849111235 / 非自愿 0'`）
        #     与版本号（`'10.0.19045'` vs `'Windows 10 版本 10.0.19045'`）。
        #     要求至少 2 个数字 token 才启用：单个数字太容易与描述末尾的**无关数字**撞上
        #     （实测反例：`'串口: 通信端口 (COM1)'` 里的 '1'、`'键盘: 增强型(101 或 102 键)'`），
        #     那种情况下宁可重复一次，也不能把真实数值吞掉。
        vdigits = Translator._NUMBER_RE.findall(vstr)
        if len(vdigits) >= 2:
            ddigits = Translator._NUMBER_RE.findall(d)
            if len(ddigits) >= len(vdigits) and ddigits[-len(vdigits):] == vdigits:
                return True

        # (6) "冒号之后那一段"里已含 `value`（**不含**额外限定后缀的情形）。
        #     实测剩余 7 条全是这一形态 —— 描述在值之后又追加了限定语：
        #         '主板芯片组: Intel H410（我的躯干骨架型号）'  value='Intel H410'
        #         '串口: 通信端口 (COM1)'                      value='COM1'
        #         '打印机 #2: OneNote for Windows 10 (Unknown)' value='OneNote for Windows 10'
        #     故只取**最后一个冒号之后**的尾段做包含判断，而不是全串 `in`
        #     （全串判断会让 `value='1'` 命中 '页错误 #1: …' 这类编号）。
        #     要求该段长度不小于 `value`，避免仅部分重合就抑制真实值。
        sep = max(d.rfind(":"), d.rfind("："))
        if sep >= 0:
            tail = _strip_ws(d[sep + 1:])
            if len(tail) >= len(vnorm) >= 2 and vnorm in tail:
                return True
        return False

    def _clean_unit(self, unit) -> str:
        """返回可安全拼接的单位；类型名/非字符串一律视为"没有单位"。

        【不易·2026-09-19 实测】`unit` 字段在真实采集里并不总是"单位"：
        657 条中有 3 条 `unit='bool'`（机箱物理锁定 / HDMI 音频 / 变更检测基准），
        直接拼接会产出 `'变更检测基准已建立（免疫系统初始化）: Truebool'` ——
        **类型名冒充单位**，且看起来"像人话"，比显式报错更难发现。
        """
        if not isinstance(unit, str):
            return ""
        u = unit.strip()
        if not u or u in self._GENERIC_UNITS:
            return ""
        # 形如 `<class 'bool'>` / `<type 'int'>` 的 repr 泄漏
        if u.startswith("<") and u.endswith(">"):
            return ""
        return u

    def _fallback(self, reading: dict) -> str:
        """无匹配规则时的通用描述：`"{description or sensor_name}: {value}{unit}"`。

        【不易·2026-09-19 契约】三条实测判据共同决定输出，缺一条都会产出噪声：
          1. `description` / `sensor_name` **都为空** ⇒ 返回 `""`（空描述会被
             `PromptInjector` 过滤掉），**不得**产出 `": 50.0"` 这种半截文本。
             —— `translate()` 对残缺输入的取值逻辑保持原样，此判据仅为防御性兜底。
          2. `description` 已含该值 ⇒ 不再追加值/单位（见 `_already_carries`，覆盖 267 条）。
          3. `unit` 是类型名（`bool` 等）⇒ 视为无单位（见 `_clean_unit`，覆盖 3 条）。
        """
        desc = reading.get("description")
        if not desc or not str(desc).strip():
            desc = reading.get("sensor_name")
        desc = ("" if desc is None else str(desc)).strip()
        # 描述自身可能以 ':' 结尾，避免拼出 'xxx:: 1'
        desc = desc.rstrip(":：").rstrip()
        if not desc:
            return ""

        value = reading.get("value", "")
        if self._already_carries(desc, value):
            return desc

        unit = self._clean_unit(reading.get("unit", ""))
        return f"{desc}: {value}{unit}"
