"""pytest 插件：把"今天"整体平移 N 天（判定"日期定时炸弹"的实验工具）

用法：
    CP_DATE_SHIFT_DAYS=400 python -m pytest tests/unit/xxx.py -p tests._date_shift_plugin

原理：`datetime.date` / `datetime.datetime` 是 C 类型，无法直接改 `today`；
故用 Python 子类替换 `datetime` 模块属性，并同步**已导入模块**里 `from datetime
import date` 造成的模块级绑定（freezegun 的核心手法，这里只实现所需子集）。

已知局限（会在报告里如实披露，避免假绿）：
  · 只用 `time.time()` / 直接用系统时钟的代码**不会**被平移 ⇒ 可能漏判（假阴性）；
  · 闭包内已捕获真实类的引用不会被替换；
  · `datetime.datetime` 被替换为子类后，少数 C 扩展（pandas 等）可能行为异常
    ⇒ 差异只作**候选**，必须逐条人工判定，不作结论。
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

import datetime as _dt

_SHIFT = int(os.environ.get("CP_DATE_SHIFT_DAYS", "0") or "0")

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


def _make_fakes():
    import datetime as _d

    delta = _d.timedelta(days=_SHIFT)

    class FakeDate(_REAL_DATE):
        @classmethod
        def today(cls):
            return _REAL_DATE.today() + delta

    class FakeDatetime(_REAL_DATETIME):
        @classmethod
        def now(cls, tz=None):
            return _REAL_DATETIME.now(tz) + delta

        @classmethod
        def utcnow(cls):
            return _REAL_DATETIME.utcnow() + delta

        @classmethod
        def today(cls):
            return _REAL_DATETIME.today() + delta

    return FakeDate, FakeDatetime


def _patch_module_attrs(FakeDate, FakeDatetime) -> None:
    """把已导入模块里指向真实 date/datetime 的模块级名字换掉"""
    for mod in list(sys.modules.values()):
        if mod is None or mod is _dt:
            continue
        d = getattr(mod, "__dict__", None)
        if not isinstance(d, dict):
            continue
        for name, real, fake in (("date", _REAL_DATE, FakeDate),
                                 ("datetime", _REAL_DATETIME, FakeDatetime)):
            cur = d.get(name)
            if cur is real:
                _saved.append((mod, name, real))
                try:
                    setattr(mod, name, fake)
                except Exception:  # noqa: BLE001
                    _saved.pop()


def pytest_configure(config):
    if _SHIFT == 0:
        return
    FakeDate, FakeDatetime = _make_fakes()
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
        fh.write(f"shift={_SHIFT} unshifted_agent_modules={len(blind)}\n")
        for k, v in blind.items():
            fh.write(f"{k}\t{','.join(v)}\n")


def pytest_unconfigure(config):
    if _SHIFT == 0:
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
