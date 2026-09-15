"""pytest 插件：把"今天"整体平移 N 天（判定"日期定时炸弹"的实验工具）

用法：
    CP_DATE_SHIFT_DAYS=400 python -m pytest tests/unit/xxx.py -p tests._date_shift_plugin

原理：`datetime.date` / `datetime.datetime` 是 C 类型，无法直接改 `today`；
故用 Python 子类替换 `datetime` 模块属性，并同步**已导入模块**里 `from datetime
import date` 造成的模块级绑定（freezegun 的核心手法，这里只实现所需子集）。

已知局限（会在报告里如实披露，避免假绿）：
  · 只用 `time.time()` / 直接用系统时钟的代码**不会**被平移 ⇒ 可能漏判（假阴性）；
  · **文件系统时钟不参与平移**：`os.stat().st_mtime` / `os.utime()` 走的是真实 OS 时钟，
    `FILETIME`/`fromtimestamp` 也按真实时间解释。测试若把"Python 的今天"与"文件的
    mtime"当同一口径用，平移会暴露**口径不一致**（不是炸弹，是工具盲区，见 §四纪律）；
  · 闭包内已捕获真实类的引用、模块级默认参数 `def f(t=date.today())`（导入期求值）
    不会被替换；
  · `datetime.datetime` 被替换为子类后，少数 C 扩展（pandas 等）可能行为异常
    ⇒ 差异只作**候选**，必须逐条人工判定，不作结论；
  · `repr()` 差一个模块前缀（CPython 的 `date.__repr__` 用 C 层 `tp_name`，子类的
    `tp_name` 不含模块名）：`date(2026, 9, 14)` vs `datetime.date(2026, 9, 14)`。
    `str()`/`isoformat()`/`f-string` 逐字一致，**不伪造 `__repr__`**（伪造会与 CPython
    实现漂移）。该差异由 `tests/unit/test_date_shift_plugin_guard.py` 钉住。

⚠️ 伪影控制（S11-07 新增，重要）：
  替换 `datetime.datetime` 本身会带来**与被平移天数无关**的副作用 ⇒ 会把"好用例"
  误报成炸弹（假阳性）。已确认并修掉的一类：
    `pickle.dumps(真实 datetime 实例)` 需要按名字解析 `obj.__class__`
    （`save_global` 查 `datetime.datetime`）。替换后该名字指向子类、而实例的类仍是
    真实类 ⇒ `PicklingError: it's not the same object as datetime.datetime`。
    **实测：delta=0（只替换、不平移）也能复现同样的失败** ⇒ 那是工具伪影，不是炸弹。
  两条修法（缺一不可）：
    (1) 假类带 `__module__="datetime"` / `__qualname__` 与原名一致 ⇒ 假类实例可被
        pickle 正常解析（否则假类实例自己也不可 pickle）；
    (2) `now()/utcnow()/today()` 返回**假类实例**（用 `cls(...)` 重建），而不是
        "真实实例 + timedelta" ⇒ 让时间口径贯通，避免"真/假类混用"再触发同类伪影。
  判定纪律：任何"平移后失败"的用例，都必须先跑 **delta=0 对照**
  （`CP_DATE_SHIFT_CONTROL=1`，见下）确认失败只在"真平移"时出现，才可判为炸弹。
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

import datetime as _dt

_SHIFT = int(os.environ.get("CP_DATE_SHIFT_DAYS", "0") or "0")

# ⚠️ 伪影对照开关（S11-07 新增，判定纪律的强制前置）：
#   `CP_DATE_SHIFT_CONTROL=1` ⇒ 照样替换 `datetime.date/datetime`，但 delta 强制为 0。
#   此时**没有任何时间被平移**，任何失败都只可能来自"替换"这个动作本身（工具伪影），
#   不可能是日期炸弹。凡"平移后失败"的用例，都必须先过这一关。
_CONTROL = bool(os.environ.get("CP_DATE_SHIFT_CONTROL", "").strip() not in ("", "0", "false", "False"))
_DELTA_DAYS = 0 if _CONTROL else _SHIFT
_ACTIVE = _CONTROL or _SHIFT != 0

_REAL_DATE = _dt.date
_REAL_DATETIME = _dt.datetime
_REAL_DATETIME_NS = getattr(_dt, "datetime", None)

_saved: list = []


# ════════════════════════════════════════════════════════════════════
#  候选面生成（供"扫清同类面"的差分扫描用）
#  用法: python tests/_date_shift_plugin.py --candidates
# ════════════════════════════════════════════════════════════════════

_ISO = re.compile(r"20\d\d-\d\d-\d\d")
_TODAY_API = re.compile(
    r"rotate|archive|retention|cleanup|expire|expired|stale|idle_days|ttl|prune"
    r"|purge|keep_days|unused_days|archived_days|last_used|hot_days|warm_days"
    r"|cutoff|older_than|since_days|date\.today|datetime\.now|utcnow",
    re.I,
)


def candidate_files(root: str = "tests") -> list:
    """含硬编码 ISO 日期 **且** 含「今日敏感」关键词的测试文件（同类面候选）

    这是**粗筛**：它只用来决定"把哪些文件放进日期平移差分里跑"，
    命中不等于炸弹 —— 是否真依赖"今天"由差分结果判定，见模块 docstring。
    """
    out = []
    for p in sorted(pathlib.Path(root).rglob("*.py")):
        try:
            s = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _ISO.search(s) and _TODAY_API.search(s):
            out.append(p.as_posix())
    return out


def _make_fakes(delta_days: int):
    import datetime as _d

    delta = _d.timedelta(days=delta_days)

    class FakeDate(_REAL_DATE):
        @classmethod
        def today(cls):
            r = _REAL_DATE.today() + delta
            return cls(r.year, r.month, r.day)

    class FakeDatetime(_REAL_DATETIME):
        @classmethod
        def _wrap(cls, r):
            """把真实 datetime 重建为 cls 实例

            必须重建而不是 `真实实例 + delta`：后者返回的是**真实类**的实例，
            与"已替换成假类"的时间口径混用，会再次触发 pickle 伪影（见模块 docstring）。
            重建时保留 tzinfo 与 fold，避免把"日期炸弹"换成"时区/夏令时炸弹"。
            """
            return cls(r.year, r.month, r.day, r.hour, r.minute, r.second,
                       r.microsecond, r.tzinfo, fold=r.fold)

        @classmethod
        def now(cls, tz=None):
            return cls._wrap(_REAL_DATETIME.now(tz) + delta)

        @classmethod
        def utcnow(cls):
            return cls._wrap(_REAL_DATETIME.utcnow() + delta)

        @classmethod
        def today(cls):
            return cls._wrap(_REAL_DATETIME.today() + delta)

    # pickle 的 save_global 会按 `obj.__module__`/`__qualname__` 回查模块属性，
    # 并要求"查到的对象 **就是** 被 pickle 的那个类"。假类不声明这两个属性时，
    # 它自己的实例都不可 pickle（存进快照/缓存即崩）。声明后与真实类同名同址，
    # `repr()` 也与真实类逐字一致，对被测代码透明。
    for cls, name in ((FakeDate, "date"), (FakeDatetime, "datetime")):
        cls.__module__ = "datetime"
        cls.__qualname__ = name
        cls.__name__ = name

    return FakeDate, FakeDatetime


def _patch_module_attrs(FakeDate, FakeDatetime) -> None:
    """把已导入模块里指向真实 date/datetime 的模块级名字换掉

    两层策略（S11-07 起）：
      · **按名**（对所有模块，与 S11-06 行为一致）：名字就叫 `date` / `datetime` 且
        值就是真实类 ⇒ 替换。覆盖 `from datetime import datetime` 这类常规导入。
      · **按值**（仅限本项目模块 `agent.*` / `tests.*` / `__main__`）：值等于真实类的
        **任意名字**都替换。这是为了消掉**别名导入**盲区 ——
        `from datetime import datetime as dt`、`from datetime import date as _date`
        原先完全没被替换 ⇒ "被测代码没被平移、测试被平移" ⇒ 假失败。
    按值只作用于本项目模块，是为了不误碰 C 扩展（pandas/numpy 等）内部持有的
    类型引用，避免把"日期炸弹"换成更难查的第三方行为差异。
    """
    for mod in list(sys.modules.values()):
        if mod is None or mod is _dt:
            continue
        d = getattr(mod, "__dict__", None)
        if not isinstance(d, dict):
            continue
        mod_name = getattr(mod, "__name__", "") or ""
        own = mod_name == "agent" or mod_name.startswith("agent.")
        for name, cur in list(d.items()):
            if cur is _REAL_DATE:
                fake = FakeDate
            elif cur is _REAL_DATETIME:
                fake = FakeDatetime
            else:
                continue
            if not own and name not in ("date", "datetime"):
                continue
            try:
                setattr(mod, name, fake)
            except Exception:  # noqa: BLE001
                continue
            _saved.append((mod, name, cur))


def pytest_configure(config):
    if not _ACTIVE:
        return
    FakeDate, FakeDatetime = _make_fakes(_DELTA_DAYS)
    _dt.date = FakeDate
    _dt.datetime = FakeDatetime
    _patch_module_attrs(FakeDate, FakeDatetime)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """把"未被平移的 agent.* 模块"写出来（量化假阴性面，避免假绿）"""
    out = os.environ.get("CP_DATE_SHIFT_REPORT")
    if not out:
        return
    import datetime as _d

    real_date, real_dt = _REAL_DATE, _REAL_DATETIME
    blind = {}
    for name, mod in sorted(sys.modules.items()):
        if not name.startswith("agent"):
            continue
        d = getattr(mod, "__dict__", None)
        if not isinstance(d, dict):
            continue
        bad = []
        if d.get("date") is real_date:
            bad.append("date")
        if d.get("datetime") is real_dt:
            bad.append("datetime")
        if bad:
            blind[name] = bad
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"shift={_SHIFT} control={int(_CONTROL)} unshifted_agent_modules={len(blind)}\n")
        for k, v in blind.items():
            fh.write(f"{k}\t{','.join(v)}\n")


def pytest_unconfigure(config):
    if not _ACTIVE:
        return
    for mod, name, real in _saved:
        try:
            setattr(mod, name, real)
        except Exception:  # noqa: BLE001
            pass
    _saved.clear()
    _dt.date = _REAL_DATE
    _dt.datetime = _REAL_DATETIME


if __name__ == "__main__":  # pragma: no cover
    if "--candidates" not in sys.argv:
        print("用法: python tests/_date_shift_plugin.py --candidates")
        sys.exit(2)
    files = candidate_files()
    for f in files:
        print(f)
    print(f"# 候选文件数: {len(files)}", file=sys.stderr)
