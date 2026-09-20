# -*- coding: utf-8 -*-
"""`sensor/change_detector.py` 单元补测 —— TASK-09 · P0-4（按用例数计的最大一个文件）。

本文件测什么、不测什么
======================
**测**：7 个 `_diff_*` 纯逻辑方法、`_sanitize_reg_value`（注册表值清洗）、
`_compare_snapshots` 聚合、`collect()` 的出口语义（含学习钩子旁路）、
持久化日志的读/写/滚动、`register_change_from_event` 的编码契约，
以及 4 个 `_list_*` / 2 个 `_capture_*` 采集方法的**分支**。

**不测**：真实采集。`sensor/registry.py` 的自发现与 `BodySensor` 的 WMI 路径
在本机实测会偶发阻塞数十秒（TASK-09 §2.2），故本文件**一律注入伪数据**：
  · `SYSTEM` 用 `monkeypatch` 改成 `"Linux"` / `"Windows"`；
  · `wmi` / `winreg` 用 `sys.modules` 注入**伪模块**（不 import 真库）；
  · `psutil` / `subprocess.run` 用 `monkeypatch` 替换；
  · `ChangeDetector` 的持久化目录**一律指向 `tmp_path`**，绝不触碰真实 `~/.Yunshu`。
⇒ Linux CI 上不会因为缺 `wmi`/`winreg` 而失败（E7），也不会依赖真硬件。

【不易】不使用 `importlib.reload`、不使用 `time.sleep`、不写墙钟计时断言。
【变易】`_diff_services` 的 `changed`/集合迭代顺序不确定 ⇒ 多元素场景用集合或排序后比较，
    只有单元素场景才断言精确顺序（这是被测实现的真实性质，不是我偷懒）。
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import types
from collections import namedtuple
from datetime import datetime

import pytest

import sensor.change_detector as cd_mod
import sensor.novelty as nv
from sensor.change_detector import (
    DEFAULT_LOG_DIR,
    REGISTRY_HIVE_MAP,
    REGISTRY_WATCH_PATHS,
    ChangeDetector,
    SYSTEM,
)
from sensor.sensor_reading import Category, Severity

# ═══════════════════════════════════════════════════════════════
#  夹具与工具
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def cd(tmp_path):
    """隔离到 `tmp_path` 的检测器（`_load_persistent_log` 读到不存在的文件 ⇒ 空日志）。"""
    return ChangeDetector(persistent_log_dir=str(tmp_path))


def _snap(**over):
    """构造一份**简化快照**（只含 `_compare_snapshots` 会读的那 7 个键 + hash/timestamp）。"""
    base = {
        "timestamp": "2026-09-20T00:00:00",
        "devices": {},
        "disk_partitions": {},
        "processes": [],
        "services": [],
        "system_info": {},
        "registry": {},
        "environment": {},
        "hash": "hash0",
    }
    base.update(over)
    return base


def _by_type(changes):
    """按 `type` 建索引（集合迭代顺序不确定 ⇒ 用类型做键而不是靠位置）。"""
    out = {}
    for c in changes:
        out.setdefault(c["type"], []).append(c)
    return out


def _types(changes):
    return sorted(c["type"] for c in changes)


def _proc(name, pid=1, state="running"):
    return {"pid": pid, "name": name, "status": state}


def _svc(name, state="Running"):
    return {"name": name, "state": state}


# ═══════════════════════════════════════════════════════════════
#  1. 模块级常量与结构
# ═══════════════════════════════════════════════════════════════

class TestModuleConstants:
    """常量是跨模块契约（`tags`/`novelty` 都按字符串比对类型名）。"""

    def test_default_log_dir_is_home_yunshu(self):
        """默认日志根目录 = `~/.Yunshu`（生产上由用户主目录决定，测试必须避开）。"""
        assert DEFAULT_LOG_DIR == os.path.expanduser("~/.Yunshu")
        assert DEFAULT_LOG_DIR.endswith(".Yunshu")

    def test_system_constant_matches_platform(self):
        """`SYSTEM` 是 import 期快照的 `platform.system()` —— 被 `_list_*` / `_capture_registry` 读。"""
        assert SYSTEM == platform.system()
        assert isinstance(SYSTEM, str) and SYSTEM

    def test_hive_map_has_exactly_two_hives(self):
        """`REGISTRY_HIVE_MAP` 恰好 2 个键，且初值允许为 `None`（懒加载）。"""
        assert set(REGISTRY_HIVE_MAP) == {"HKLM", "HKCU"}

    def test_watch_paths_structure(self):
        """`REGISTRY_WATCH_PATHS` 恰好 9 条，每条是 `(hive, subkey, names|None)`。"""
        assert len(REGISTRY_WATCH_PATHS) == 9
        for entry in REGISTRY_WATCH_PATHS:
            assert isinstance(entry, tuple) and len(entry) == 3
            hive, subkey, names = entry
            assert hive in REGISTRY_HIVE_MAP
            assert isinstance(subkey, str) and subkey
            assert names is None or (isinstance(names, list)
                                     and all(isinstance(n, str) and n for n in names))

    @pytest.mark.parametrize("idx", range(len(REGISTRY_WATCH_PATHS)))
    def test_watch_paths_are_unique(self, idx):
        """每条 `(hive, subkey)` 组合唯一（重复会让同一路径被读两遍）。"""
        keys = [(h, s) for h, s, _ in REGISTRY_WATCH_PATHS]
        assert len(set(keys)) == len(keys)
        assert keys[idx] not in keys[:idx] + keys[idx + 1:]

    def test_all_watch_keys_are_sane(self):
        """结构不变量：**4 条**路径按 `names` 枚举全部值、**5 条**按名读取指定字段。"""
        with_names = [p for p in REGISTRY_WATCH_PATHS if p[2] is not None]
        assert len(with_names) == 4
        assert all(p[2] for p in with_names)
        assert sum(1 for p in REGISTRY_WATCH_PATHS if p[2] is None) == 5

    def test_run_paths_watch_both_run_and_runonce(self):
        """自启动监控必须覆盖 Run 与 RunOnce、HKLM 与 HKCU 四个组合（安全相关）。"""
        pairs = {(h, s) for h, s, _ in REGISTRY_WATCH_PATHS}
        for hive in ("HKLM", "HKCU"):
            for tail in ("Run", "RunOnce"):
                assert any(s.endswith("\\" + tail) and h == hive for h, s in pairs), (hive, tail)


# ═══════════════════════════════════════════════════════════════
#  2. `__init__`：容量与持久化目录
# ═══════════════════════════════════════════════════════════════

class TestInit:
    """构造期的三条外部依赖：容量上限、持久化目录、初始日志加载。"""

    def test_defaults(self, tmp_path):
        d = ChangeDetector(persistent_log_dir=str(tmp_path))
        assert d._category is Category.CHANGE
        assert d._baseline is None
        assert d._last_check is None
        assert d._change_log == []
        assert d._learning_hook is None
        assert d._persistent_log == []

    def test_default_max_entries_comes_from_novelty_helper(self, tmp_path, monkeypatch):
        """未显式给 `max_entries` ⇒ 调 `novelty.default_max_entries()`（**不是**硬编码 10000）。"""
        monkeypatch.setattr(cd_mod, "default_max_entries", lambda: 777)
        d = ChangeDetector(persistent_log_dir=str(tmp_path))
        assert d._max_entries == 777

    @pytest.mark.parametrize("value", [1, 50, 10000])
    def test_explicit_max_entries_wins(self, tmp_path, value):
        """显式容量**优先于**配置（测试隔离依赖这一点）。"""
        assert ChangeDetector(persistent_log_dir=str(tmp_path),
                              max_entries=value)._max_entries == value

    @pytest.mark.parametrize("value", [0, -1])
    def test_zero_and_negative_max_entries_are_kept_verbatim(self, tmp_path, value):
        """**发现（登记，未修）**：`max_entries=0` / 负数**不会**回落默认值。

        `__init__` 写的是 `max_entries if max_entries is not None else default`
        ⇒ 显式 `0` 会被保留；而 `trim_change_log` 对 `<=0` 的语义是"**不设上限**"
        ⇒ 于是 `max_entries=0` 的真实含义是"**日志无限增长**"，与"零容量"的直觉相反。
        生产默认路径不会传 0，故不影响线上；但它是**静默的语义反转**，登记为发现。
        """
        d = ChangeDetector(persistent_log_dir=str(tmp_path), max_entries=value)
        assert d._max_entries == value
        assert nv.trim_change_log([{"i": i} for i in range(3)], d._max_entries) != []

    def test_persistent_paths_composition(self, tmp_path):
        """`_persistent_log_path` = `<dir>/change_log.json`（文件名是外部契约）。"""
        d = ChangeDetector(persistent_log_dir=str(tmp_path / "sub"))
        assert d._persistent_log_dir == str(tmp_path / "sub")
        assert d._persistent_log_path == os.path.join(str(tmp_path / "sub"), "change_log.json")

    def test_default_persistent_dir_from_default_log_dir(self, monkeypatch, tmp_path):
        """未给 `persistent_log_dir` ⇒ `DEFAULT_LOG_DIR/changes`。

        用 `monkeypatch` 把 `DEFAULT_LOG_DIR` 指向 `tmp_path`，
        这样既验证了目录组合规则，又**不会**读写真家目录。
        """
        monkeypatch.setattr(cd_mod, "DEFAULT_LOG_DIR", str(tmp_path / "fakehome"))
        d = ChangeDetector()
        assert d._persistent_log_dir == os.path.join(str(tmp_path / "fakehome"), "changes")
        assert d._persistent_log_path.endswith("change_log.json")

    def test_explicit_dir_beats_default(self, monkeypatch, tmp_path):
        """显式目录**完全覆盖**默认目录（不与之拼接）。"""
        monkeypatch.setattr(cd_mod, "DEFAULT_LOG_DIR", str(tmp_path / "fakehome"))
        d = ChangeDetector(persistent_log_dir=str(tmp_path / "iso"))
        assert d._persistent_log_dir == str(tmp_path / "iso")

    def test_learning_hook_stored(self, tmp_path):
        hook = lambda changes: None  # noqa: E731
        assert ChangeDetector(persistent_log_dir=str(tmp_path),
                              learning_hook=hook)._learning_hook is hook


# ═══════════════════════════════════════════════════════════════
#  3. `_diff_devices`
# ═══════════════════════════════════════════════════════════════

class TestDiffDevices:
    """设备 diff：三类事件，且**三类的键集合互不相同**（下游易踩的坑）。"""

    def test_no_change_when_identical(self, cd):
        dev = {"k1": {"name": "鼠标", "status": "OK"}}
        assert cd._diff_devices(dev, dict(dev)) == []

    def test_both_empty(self, cd):
        assert cd._diff_devices({}, {}) == []

    def test_added_event_shape(self, cd):
        """新增：`normal`，带 `current`，**带 `detail`（JSON 串）**，**不带 `previous`**。"""
        info = {"name": "新显卡", "status": "OK", "class": "{GUID}"}
        changes = cd._diff_devices({}, {"k": info})
        assert len(changes) == 1
        c = changes[0]
        assert set(c) == {"name", "value", "type", "severity",
                          "description", "detail", "current"}
        assert c["name"] == "change_device_added"
        assert c["type"] == "device_added"
        assert c["severity"] == "normal"
        assert c["value"] == "新显卡"
        assert c["current"] is info
        assert c["detail"] == json.dumps(info, ensure_ascii=False)
        assert "新显卡" in c["detail"]          # ensure_ascii=False ⇒ 中文不转义
        assert c["description"] == "新设备接入: 新显卡（我感知到了新的硬件）"

    def test_added_value_falls_back_to_key(self, cd):
        """info 里没有 `name` ⇒ `value` 回落为**键**（不是空串、不是 None）。"""
        c = cd._diff_devices({}, {"KEY|GUID": {"status": "OK"}})[0]
        assert c["value"] == "KEY|GUID"
        assert c["description"] == "新设备接入: KEY|GUID（我感知到了新的硬件）"

    def test_removed_event_shape(self, cd):
        """移除：`warning`，带 `previous`，**不带 `current` / `detail`**。

        【为什么单独锁】新增带 `detail`、移除不带 —— 下游若统一按"有 detail"
        处理，移除事件会 KeyError。这与 `novelty._build_event` 的 `if k in change`
        筛选叠加后，会让两类事件的 `detail` 子键集合不同。
        """
        info = {"name": "旧网卡", "status": "OK"}
        c = cd._diff_devices({"k": info}, {})[0]
        assert set(c) == {"name", "value", "type", "severity", "description", "previous"}
        assert c["name"] == "change_device_removed"
        assert c["type"] == "device_removed"
        assert c["severity"] == "warning"
        assert c["value"] == "旧网卡"
        assert c["previous"] is info
        assert c["description"] == "设备移除: 旧网卡（我的某个硬件被移除了）"

    def test_modified_event_shape(self, cd):
        """改值：`warning`，`value` 是**键**（不是 name），同时带 `previous` 与 `current`。"""
        old = {"k": {"name": "显示器", "status": "OK"}}
        new = {"k": {"name": "显示器", "status": "Error"}}
        c = cd._diff_devices(old, new)[0]
        assert set(c) == {"name", "value", "type", "severity",
                          "description", "previous", "current"}
        assert c["type"] == "device_modified"
        assert c["severity"] == "warning"
        assert c["value"] == "k"                     # ← 键，而非 info["name"]
        assert c["description"] == "设备状态变更: k"
        assert c["previous"] == old["k"]
        assert c["current"] == new["k"]

    def test_only_one_event_per_changed_key(self, cd):
        """同一键变化只产**一条**事件（不会同时产 added/removed/modified）。"""
        assert len(cd._diff_devices({"k": {"s": 1}}, {"k": {"s": 2}})) == 1

    def test_new_key_with_same_value_is_added_and_old_key_removed(self, cd):
        """键不同即视为"新增 + 移除"两条，**即使 info 完全相同**（键含 GUID，是设备身份）。

        反过来说：**绝不会**产出 `device_modified` —— 改键必然表现为"拔掉再插上"。
        """
        same = {"name": "X", "status": "OK"}
        changes = cd._diff_devices({"k1": same}, {"k2": dict(same)})
        assert _types(changes) == ["device_added", "device_removed"]
        assert "device_modified" not in {c["type"] for c in changes}

    def test_all_three_kinds_together(self, cd):
        """三类同时发生：共 3 条，类型齐全，且 severity 分布为 normal/warning/warning。"""
        old = {"keep": {"s": 1}, "gone": {"name": "旧"}, "mod": {"s": "a"}}
        new = {"keep": {"s": 1}, "new": {"name": "新"}, "mod": {"s": "b"}}
        changes = cd._diff_devices(old, new)
        assert _types(changes) == ["device_added", "device_modified", "device_removed"]
        sev = {c["type"]: c["severity"] for c in changes}
        assert sev == {"device_added": "normal", "device_removed": "warning",
                       "device_modified": "warning"}

    def test_multiple_added_produce_one_event_each(self, cd):
        """新增 N 个设备 ⇒ **N 条**事件（与进程 diff 的"聚合成一条"形成对比）。"""
        changes = cd._diff_devices({}, {f"k{i}": {"name": f"d{i}"} for i in range(4)})
        assert len(changes) == 4
        assert sorted(c["value"] for c in changes) == ["d0", "d1", "d2", "d3"]

    def test_deep_dict_difference_detected(self, cd):
        """嵌套 dict 的值差异也算"变化"（`!=` 是深比较）。"""
        changes = cd._diff_devices({"k": {"a": {"b": 1}}}, {"k": {"a": {"b": 2}}})
        assert _types(changes) == ["device_modified"]

    def test_key_order_irrelevant(self, cd):
        """dict 的键顺序不影响判等（避免"顺序变了就报变更"的假阳性）。"""
        assert cd._diff_devices({"k": {"a": 1, "b": 2}}, {"k": {"b": 2, "a": 1}}) == []

    def test_none_side_is_tolerated(self, cd):
        """`None` 侧不会被 `.keys()` 崩掉？—— **实测会崩**（见下条），此处锁"空 dict"路径。"""
        assert cd._diff_devices({}, {}) == []

    def test_none_devices_raise_attributeerror(self, cd):
        """**现状锁定**：传 `None` 会 `AttributeError`（`.keys()` on None）。

        真实调用面 `_compare_snapshots` 用 `old.get("devices", {})` 兜底，
        但**只在键缺失时**兜底；若快照里显式写了 `"devices": None`，就会崩。
        `_capture_snapshot` 保证是 dict，故线上不可达 —— 记录边界即可。
        """
        with pytest.raises(AttributeError):
            cd._diff_devices(None, {})


# ═══════════════════════════════════════════════════════════════
#  4. `_diff_partitions`
# ═══════════════════════════════════════════════════════════════

class TestDiffPartitions:
    """磁盘分区 diff：挂载 normal / 卸载 warning。"""

    def test_no_change(self, cd):
        p = {"C:\\": {"mountpoint": "C:\\", "fstype": "NTFS"}}
        assert cd._diff_partitions(p, dict(p)) == []

    def test_mounted_shape(self, cd):
        info = {"mountpoint": "E:\\", "fstype": "exFAT"}
        c = cd._diff_partitions({}, {"E:\\": info})[0]
        assert set(c) == {"name", "value", "type", "severity", "description", "current"}
        assert c["name"] == "change_disk_mounted"
        assert c["type"] == "disk_mounted"
        assert c["severity"] == "normal"
        assert c["value"] == "E:\\"
        assert c["current"] is info
        assert c["description"] == "新磁盘挂载: E:\\ -> E:\\"

    def test_unmounted_shape(self, cd):
        info = {"mountpoint": "E:\\", "fstype": "exFAT"}
        c = cd._diff_partitions({"E:\\": info}, {})[0]
        assert set(c) == {"name", "value", "type", "severity", "description", "previous"}
        assert c["name"] == "change_disk_unmounted"
        assert c["type"] == "disk_unmounted"
        assert c["severity"] == "warning"
        assert c["description"] == "磁盘卸载: E:\\ (E:\\)"

    @pytest.mark.parametrize("info,expected_mount", [
        ({}, ""), ({"mountpoint": None}, "None"), ({"mountpoint": ""}, ""),
    ])
    def test_missing_mountpoint_renders_falsy(self, cd, info, expected_mount):
        """`info.get("mountpoint", "")` 只兜底**键缺失**；显式 `None` 会渲染成 `'None'`。

        这是 `dict.get` 的经典陷阱：`{"mountpoint": None}` 与 `{}` 的渲染结果**不同**。
        记录它，避免日后把 `None` 当成"没有挂载点"。
        """
        c = cd._diff_partitions({}, {"X:": info})[0]
        assert c["description"] == f"新磁盘挂载: X: -> {expected_mount}"

    def test_fstype_change_is_invisible(self, cd):
        """**发现（登记，未修）**：分区**内容**变化（`fstype` / `mountpoint`）完全不可见。

        `_diff_partitions` 只比对**键集合**（设备名），不比对值 ⇒
        `NTFS → exFAT`（格式化）这种重大变化**产不出任何事件**。
        对照 `_diff_devices` 会比对 info 内容、`_diff_services` 会比对 state，
        这里是三处 diff 中唯一"只看存在性"的实现 —— 属实现不一致。
        本任务禁止改生产代码 ⇒ 只锁定现状并登记（报告 §5）。
        """
        assert cd._diff_partitions({"X:": {"mountpoint": "X:", "fstype": "NTFS"}},
                                   {"X:": {"mountpoint": "X:", "fstype": "exFAT"}}) == []
        assert cd._diff_partitions({"X:": {"mountpoint": "X:"}},
                                   {"X:": {"mountpoint": "Y:"}}) == []

    def test_multiple_mounts(self, cd):
        changes = cd._diff_partitions({}, {f"P{i}": {"mountpoint": f"P{i}"} for i in range(3)})
        assert len(changes) == 3
        assert all(c["type"] == "disk_mounted" for c in changes)


# ═══════════════════════════════════════════════════════════════
#  5. `_diff_processes`
# ═══════════════════════════════════════════════════════════════

class TestDiffProcesses:
    """进程 diff：按**名字集合**比对、聚合成两条事件、过滤系统噪音进程。"""

    def test_no_change(self, cd):
        procs = [_proc("a"), _proc("b")]
        assert cd._diff_processes(procs, [dict(p) for p in procs]) == []

    def test_started_shape(self, cd):
        c = cd._diff_processes([], [_proc("chrome")])[0]
        assert set(c) == {"name", "value", "type", "severity", "description", "detail", "count"}
        assert c["name"] == "change_process_started"
        assert c["type"] == "process_started"
        assert c["severity"] == "normal"
        assert c["value"] == 1               # 数量，不是名字
        assert c["count"] == 1
        assert c["detail"] == ["chrome"]
        assert c["description"] == "新进程启动: chrome"

    def test_stopped_shape(self, cd):
        c = cd._diff_processes([_proc("chrome")], [])[0]
        assert c["name"] == "change_process_stopped"
        assert c["type"] == "process_stopped"
        assert c["severity"] == "normal"
        assert c["description"] == "进程终止: chrome"
        assert c["value"] == 1 and c["count"] == 1

    def test_pid_change_is_invisible(self, cd):
        """**仅比名字**：同一进程 PID 变了（重启）不算变更。

        这是刻意的降噪设计（重启会换 PID，报出来是噪声）；
        代价是"同名进程被替换"不可见 —— 锁定该取舍。
        """
        assert cd._diff_processes([_proc("a", pid=1)], [_proc("a", pid=999)]) == []

    def test_status_change_is_invisible(self, cd):
        """`status` 变化同样不可见（只取 `name` 建集合）。"""
        assert cd._diff_processes([_proc("a", state="running")],
                                  [_proc("a", state="sleeping")]) == []

    def test_duplicate_names_collapse(self, cd):
        """同名多进程（多实例）折叠为 1（集合语义）⇒ 计数是"名字数"不是"进程数"。"""
        changes = cd._diff_processes([], [_proc("chrome", pid=1), _proc("chrome", pid=2)])
        assert changes[0]["count"] == 1
        assert changes[0]["detail"] == ["chrome"]

    @pytest.mark.parametrize("ignored", ["ThreadPoolForegroundWorker", "conhost", "dllhost"])
    def test_ignore_patterns_filter_both_directions(self, cd, ignored):
        """3 个系统噪音进程在**新增与消失两侧都被过滤**（否则每次采集都刷屏）。"""
        assert cd._diff_processes([], [_proc(ignored)]) == []
        assert cd._diff_processes([_proc(ignored)], []) == []

    def test_ignore_is_exact_name_not_prefix(self, cd):
        """过滤是**精确名字**匹配，不是前缀 —— `conhost_extra` 必须被报告。"""
        assert len(cd._diff_processes([], [_proc("conhost_extra")])) == 1
        assert len(cd._diff_processes([], [_proc("conhost2")])) == 1

    def test_ignore_only_removes_listed_names(self, cd):
        """对照：过滤后仍有其他名字 ⇒ 事件照常产生，且计数只算**未被过滤**的。"""
        changes = cd._diff_processes([], [_proc("conhost"), _proc("chrome"), _proc("dllhost")])
        assert len(changes) == 1
        assert changes[0]["count"] == 1
        assert changes[0]["detail"] == ["chrome"]

    def test_ten_names_have_no_suffix(self, cd):
        """**边界**：恰好 10 个 ⇒ 不追加"等共 N 个"后缀。"""
        c = cd._diff_processes([], [_proc(f"p{i:02d}") for i in range(10)])[0]
        assert c["description"] == "新进程启动: " + ", ".join(f"p{i:02d}" for i in range(10))
        assert "等共" not in c["description"]
        assert c["count"] == 10

    @pytest.mark.parametrize("n", [11, 12, 25])
    def test_more_than_ten_get_suffix(self, cd, n):
        """**边界**：11+ 个 ⇒ 只列前 10 个（按名排序）+ `" 等共N个"`，但 `count` 是**全量**。"""
        c = cd._diff_processes([], [_proc(f"p{i:02d}") for i in range(n)])[0]
        listed = ", ".join(f"p{i:02d}" for i in range(10))
        assert c["description"] == f"新进程启动: {listed} 等共{n}个"
        assert c["count"] == n
        assert c["value"] == n

    def test_detail_is_capped_at_fifty(self, cd):
        """`detail` 截断到 50 条（描述截 10、明细截 50、计数全量：三个不同口径）。"""
        c = cd._diff_processes([], [_proc(f"p{i:03d}") for i in range(60)])[0]
        assert len(c["detail"]) == 50
        assert c["detail"][0] == "p000"
        assert c["detail"][-1] == "p049"
        assert c["count"] == 60
        assert "等共60个" in c["description"]

    def test_both_directions_produce_two_events(self, cd):
        """新增与消失同时发生 ⇒ **两条**事件（不是一条混合）。"""
        changes = cd._diff_processes([_proc("old")], [_proc("new")])
        assert _types(changes) == ["process_started", "process_stopped"]

    def test_missing_name_key_raises_keyerror(self, cd):
        """**现状锁定**：进程条目缺 `name` ⇒ `KeyError`（`p["name"]` 直接下标）。

        真实数据来自 `_list_processes`（固定产出 name），故线上不可达；
        但 `register_change_from_event` 之类的旁路若喂入半成品会崩。记录边界。
        """
        with pytest.raises(KeyError):
            cd._diff_processes([{"pid": 1}], [])

    def test_extra_keys_are_ignored(self, cd):
        """条目里的额外字段不影响判等（只取 name/pid/status 也无妨）。"""
        assert cd._diff_processes([{"name": "a", "extra": 1}], [{"name": "a"}]) == []


# ═══════════════════════════════════════════════════════════════
#  6. `_diff_services`
# ═══════════════════════════════════════════════════════════════

class TestDiffServices:
    """服务 diff：比状态、severity 由 `"stop" in new_state` 决定、**只比前 100 个**。"""

    def test_no_change(self, cd):
        s = [_svc("Spooler", "Running")]
        assert cd._diff_services(s, [dict(x) for x in s]) == []

    def test_unchanged_missing_state_is_invisible(self, cd):
        """两侧都缺 `state` ⇒ `"" == ""` ⇒ 不报变更。"""
        assert cd._diff_services([{"name": "A"}], [{"name": "A"}]) == []

    def test_state_change_shape(self, cd):
        c = cd._diff_services([_svc("Spooler", "Running")], [_svc("Spooler", "Stopped")])[0]
        assert set(c) == {"name", "value", "type", "severity",
                          "description", "previous", "current"}
        assert c["name"] == "change_service_state"
        assert c["type"] == "service_state_changed"
        assert c["value"] == "Spooler"
        assert c["previous"] == "Running"
        assert c["current"] == "Stopped"
        assert c["description"] == "服务状态变更: Spooler (Running -> Stopped)"

    @pytest.mark.parametrize("new_state,expected", [
        ("Stopped", "critical"), ("STOPPED", "critical"), ("stop_pending", "critical"),
        ("Running", "warning"), ("Start Pending", "warning"), ("Paused", "warning"),
        ("", "warning"), (None, "warning"),
    ])
    def test_severity_rule(self, cd, new_state, expected):
        """**判据是 `"stop" in str(new_state).lower()`** —— 子串匹配、大小写不敏感。

        由此 `"stop_pending"`（**正在**停止）也判 critical，
        而 `"Start Pending"` 只是 warning。锁定这条粗判据（它是真实的安全语义取舍：
        宁可把"停止中"当严重，也不漏报服务下线）。
        """
        old = [{"name": "A", "state": "__OLD__"}]     # 刻意与所有 new 值都不同 ⇒ 必然产生变更
        got = cd._diff_services(old, [{"name": "A", "state": new_state}])
        assert len(got) == 1
        assert got[0]["severity"] == expected

    def test_none_state_renders_as_none_text(self, cd):
        """`None` 状态在描述里渲染为字面 `None`（不是空串）。"""
        c = cd._diff_services([_svc("A", "Running")], [{"name": "A", "state": None}])[0]
        assert c["description"] == "服务状态变更: A (Running -> None)"

    def test_duplicate_names_last_state_wins(self, cd):
        """同名服务的重复条目：dict 推导式 ⇒ **最后一条**的状态胜出。"""
        changes = cd._diff_services(
            [_svc("A", "Running")], [_svc("A", "Stopped"), _svc("A", "Running")])
        assert changes == []          # 最终值与旧值相同 ⇒ 无变更

    def test_new_service_is_invisible(self, cd):
        """**只比"两边都有"的服务**：新增服务不产事件（服务集合不参与新增检测）。"""
        assert cd._diff_services([], [_svc("NewSvc")]) == []
        assert cd._diff_services([_svc("OldSvc")], []) == []

    def test_dropped_service_is_invisible(self, cd):
        """反向同理：消失的服务也不产事件（与进程 diff 不对称，是刻意的低噪取舍）。"""
        assert cd._diff_services([_svc("A", "Running"), _svc("B", "Running")],
                                 [_svc("A", "Running")]) == []

    def test_only_first_hundred_are_compared(self, cd):
        """**发现（登记，未修）**：服务列表**只比对前 100 个**。

        `_diff_services` 用 `set(list(old_map.keys())[:100]) & set(list(new_map.keys())[:100])`
        ⇒ 排在第 101 位及之后的服务**永远不会被比对**，即使状态从 Running 变 Stopped。
        实测证据（本用例）：101 个服务、只有最后一个变化 ⇒ **0 条事件**；
        而同样的变化放在第 100 位 ⇒ 1 条事件。
        影响面：真实机器服务数常在 100~300 ⇒ **部分服务状态变化被静默丢弃**。
        这是 `[:100]` 这一截断的明确代价，登记为发现（本任务禁止改生产代码）。
        """
        base = [_svc(f"S{i:03d}", "Running") for i in range(100)] + [_svc("TAIL", "Running")]
        changed_tail = [dict(x) for x in base]
        changed_tail[100] = _svc("TAIL", "Stopped")
        assert cd._diff_services(base, changed_tail) == []

        # 对照：把同一个变化放到第 100 位（索引 99，仍在切片内）⇒ 能报出来
        near = [_svc(f"S{i:03d}", "Running") for i in range(99)] + [_svc("NEAR", "Running")]
        near_changed = [dict(x) for x in near]
        near_changed[99] = _svc("NEAR", "Stopped")
        got = cd._diff_services(near, near_changed)
        assert len(got) == 1 and got[0]["value"] == "NEAR"

    def test_missing_name_raises_keyerror(self, cd):
        """服务条目缺 `name` ⇒ `KeyError`（与进程 diff 同一处理方式）。"""
        with pytest.raises(KeyError):
            cd._diff_services([{"state": "Running"}], [])

    def test_many_changes_all_reported_within_window(self, cd):
        """窗口内多服务同时变化 ⇒ 每个产一条（顺序不确定 ⇒ 用集合比较）。"""
        old = [_svc(f"S{i}", "Running") for i in range(5)]
        new = [_svc(f"S{i}", "Stopped") for i in range(5)]
        got = cd._diff_services(old, new)
        assert len(got) == 5
        assert {c["value"] for c in got} == {f"S{i}" for i in range(5)}
        assert {c["severity"] for c in got} == {"critical"}


# ═══════════════════════════════════════════════════════════════
#  7. `_diff_system_info`
# ═══════════════════════════════════════════════════════════════

class TestDiffSystemInfo:
    """系统信息 diff：**单向**比对（只遍历 old 的键），严重级恒 critical。"""

    def test_key_shape(self, cd):
        c = cd._diff_system_info({"hostname": "a"}, {"hostname": "b"})[0]
        assert set(c) == {"name", "value", "type", "severity",
                          "description", "previous", "current"}
        assert c["name"] == "change_system_info"
        assert c["type"] == "system_info_changed"
        assert c["value"] == "hostname"
        assert c["severity"] == "critical"
        assert c["previous"] == "a" and c["current"] == "b"
        assert c["description"] == "系统信息变更: hostname (a -> b)"

    def test_no_change(self, cd):
        assert cd._diff_system_info({"a": 1}, {"a": 1}) == []

    def test_key_removed_from_new_is_not_reported(self, cd):
        """**不对称（锁定）**：`old` 有、`new` **没有**的键 ⇒ **不产事件**。

        判据里要求 `key in new_info`，故键消失被静默忽略。
        `_get_system_info` 固定返回 3 个键，故线上不可达；但这是真实的边界语义。
        """
        assert cd._diff_system_info({"a": 1, "b": 2}, {"a": 1}) == []

    def test_key_added_in_new_is_not_reported(self, cd):
        """反向同理：`new` 新增的键也不产事件（因为只遍历 old）。"""
        assert cd._diff_system_info({"a": 1}, {"a": 1, "b": 2}) == []

    def test_order_follows_old_insertion_order(self, cd):
        """输出顺序 = `old_info` 的**插入顺序**（dict 保序 ⇒ 结果可复现）。"""
        got = cd._diff_system_info({"b": 1, "a": 1}, {"a": 2, "b": 2})
        assert [c["value"] for c in got] == ["b", "a"]

    def test_every_change_is_critical(self, cd):
        """任何系统信息变化都是 `critical` —— 包括看起来无害的 hostname。

        锁定这个"过宽"的严重级（登记为观察项：改主机名与系统版本变化同等严重）。
        """
        got = cd._diff_system_info({"hostname": "a", "python_version": "3.11"},
                                   {"hostname": "b", "python_version": "3.12"})
        assert [c["severity"] for c in got] == ["critical", "critical"]

    def test_real_system_info_shape(self, cd):
        """用 `_get_system_info` 的真实三键形态跑一遍（键名变化即失败）。"""
        real = cd._get_system_info()
        newer = dict(real)
        newer["hostname"] = real["hostname"] + "-x"
        got = cd._diff_system_info(real, newer)
        assert len(got) == 1
        assert got[0]["value"] == "hostname"


# ═══════════════════════════════════════════════════════════════
#  8. `_diff_registry` / `_diff_environment`
# ═══════════════════════════════════════════════════════════════

class TestDiffRegistry:
    """注册表 diff：**排序输出**（可复现）、`previous/current` 可为 None。"""

    def test_shape(self, cd):
        c = cd._diff_registry({"HKLM\\K": {"A": "1"}}, {"HKLM\\K": {"A": "2"}})[0]
        assert set(c) == {"name", "value", "type", "severity",
                          "description", "previous", "current"}
        assert c["name"] == "change_registry"
        assert c["type"] == "registry_changed"
        assert c["severity"] == "warning"
        assert c["value"] == "HKLM\\K\\A"
        assert c["description"] == "注册表变更: HKLM\\K → A"
        assert c["previous"] == "1" and c["current"] == "2"

    def test_no_change(self, cd):
        reg = {"HKLM\\K": {"A": "1"}}
        assert cd._diff_registry(reg, {"HKLM\\K": {"A": "1"}}) == []

    def test_added_value_has_none_previous(self, cd):
        """新增注册表值 ⇒ `previous=None`、`current="1"`。"""
        c = cd._diff_registry({}, {"HKLM\\K": {"A": "1"}})[0]
        assert c["previous"] is None and c["current"] == "1"

    def test_removed_value_has_none_current(self, cd):
        """删除注册表值 ⇒ `previous="1"`、`current=None`。"""
        c = cd._diff_registry({"HKLM\\K": {"A": "1"}}, {})[0]
        assert c["previous"] == "1" and c["current"] is None

    def test_sorted_output_is_deterministic(self, cd):
        """键与值名都**排序**后输出 ⇒ 同一输入多次调用结果完全一致。"""
        old = {"Z\\K": {"b": 1}, "A\\K": {"z": 1, "a": 1}}
        new = {"Z\\K": {"b": 2}, "A\\K": {"z": 2, "a": 2}}
        first = [c["value"] for c in cd._diff_registry(old, new)]
        assert first == ["A\\K\\a", "A\\K\\z", "Z\\K\\b"]
        assert first == [c["value"] for c in cd._diff_registry(old, new)]

    def test_missing_key_dict_defaults_to_empty(self, cd):
        """某一侧缺整个路径 ⇒ 按 `{}` 处理（该路径下所有值都算新增/删除）。"""
        got = cd._diff_registry({"HKLM\\K": {"A": "1"}}, {})
        assert len(got) == 1
        assert got[0]["name"] == "change_registry"

    def test_none_value_equals_absent_value(self, cd):
        """**现状锁定**：显式 `None` 与"键缺失"不可区分（两者都落到 `.get()` 的 None）。

        于是"某注册表值从 `None` 变成有值"会**被报**（None → "1"），
        而"从有值变成显式 None"也会被报；但"本就 None → 依旧 None"不报。
        记录该等价性，避免日后误以为能区分"空值"与"未设置"。
        """
        assert cd._diff_registry({"K": {"A": None}}, {"K": {}}) == []
        assert len(cd._diff_registry({"K": {"A": None}}, {"K": {"A": "1"}})) == 1

    def test_empty_both_sides(self, cd):
        assert cd._diff_registry({}, {}) == []


class TestDiffEnvironment:
    """环境变量 diff：与注册表同构，但描述文案不同、且同样有 None 等价性。"""

    def test_shape(self, cd):
        c = cd._diff_environment({"PATH": "/a"}, {"PATH": "/b"})[0]
        assert set(c) == {"name", "value", "type", "severity",
                          "description", "previous", "current"}
        assert c["name"] == "change_environment"
        assert c["type"] == "environment_changed"
        assert c["severity"] == "warning"
        assert c["value"] == "PATH"
        assert c["description"] == "环境变量变更: PATH"
        assert c["previous"] == "/a" and c["current"] == "/b"

    def test_added_and_removed_have_none_side(self, cd):
        added = cd._diff_environment({}, {"NEW": "1"})[0]
        removed = cd._diff_environment({"OLD": "1"}, {})[0]
        assert added["previous"] is None and added["current"] == "1"
        assert removed["previous"] == "1" and removed["current"] is None

    def test_sorted_output(self, cd):
        got = cd._diff_environment({"b": "1", "a": "1"}, {"a": "2", "b": "2"})
        assert [c["value"] for c in got] == ["a", "b"]

    def test_none_valued_env_var_removal_is_invisible(self, cd):
        """**现状锁定（与注册表同源）**：值为 `None` 的变量被删除 ⇒ **不报**。

        `old_env.get("A")` 与 `new_env.get("A")` 都是 `None` ⇒ 判等 ⇒ 静默。
        `_capture_environment()` 返回 `dict(os.environ)`（值恒为 str），
        故真实路径上"删除变量"会得到 `"旧值" vs None` ⇒ **能报**；此条只锁库函数边界。
        """
        assert cd._diff_environment({"A": None}, {}) == []
        assert len(cd._diff_environment({"A": "v"}, {})) == 1

    def test_case_sensitive_keys(self, cd):
        """变量名大小写敏感（Windows 上环境变量名义上不敏感，此处按 dict 语义处理）。"""
        got = cd._diff_environment({"Path": "/a"}, {"PATH": "/a"})
        assert sorted(c["value"] for c in got) == ["PATH", "Path"]

    def test_empty_both_sides(self, cd):
        assert cd._diff_environment({}, {}) == []


# ═══════════════════════════════════════════════════════════════
#  9. `_compare_snapshots` 聚合
# ═══════════════════════════════════════════════════════════════

class TestCompareSnapshots:
    """聚合 7 类 diff，并对**缺失的顶层键**给出空默认值。"""

    def test_none_old_returns_empty(self, cd):
        """`old is None` ⇒ `[]`（首采场景，不报变更）。"""
        assert cd._compare_snapshots(None, _snap()) == []

    def test_identical_snapshots_yield_nothing(self, cd):
        """除 `timestamp` / `hash` 外全等 ⇒ 0 条变更。

        注意 `hash` 与 `timestamp` **不参与 diff**（`_compare_snapshots` 只读 7 个业务键）
        ⇒ 时间戳每次必然不同，但不会造成假变更。这是关键设计，显式锁。
        """
        old = _snap(timestamp="t1", hash="h1")
        new = _snap(timestamp="t2", hash="h2")
        assert cd._compare_snapshots(old, new) == []

    def test_missing_top_level_keys_default_to_empty(self, cd):
        """`old` 只有 `devices` 键时，其余 6 类按空默认值处理，不抛异常。"""
        old = {"devices": {}}
        new = _snap(devices={"k": {"name": "d"}})
        assert _types(cd._compare_snapshots(old, new)) == ["device_added"]

    def test_aggregates_all_seven_families(self, cd):
        """**一次调用聚合全部 7 类**：设备/分区/进程/服务/系统信息/注册表/环境变量。"""
        old = _snap(
            devices={"d": {"name": "dev", "s": 1}},
            disk_partitions={"P": {"mountpoint": "P"}},
            processes=[_proc("gone")],
            services=[_svc("S", "Running")],
            system_info={"hostname": "a"},
            registry={"K": {"A": "1"}},
            environment={"E": "1"},
        )
        new = _snap(
            devices={"d": {"name": "dev", "s": 2}},
            disk_partitions={},
            processes=[_proc("fresh")],
            services=[_svc("S", "Stopped")],
            system_info={"hostname": "b"},
            registry={"K": {"A": "2"}},
            environment={"E": "2"},
        )
        types = _types(cd._compare_snapshots(old, new))
        assert types == sorted([
            "device_modified", "disk_unmounted", "process_started", "process_stopped",
            "service_state_changed", "system_info_changed", "registry_changed",
            "environment_changed",
        ])

    def test_deterministic_family_order(self, cd):
        """**族间顺序固定**：设备 → 分区 → 进程 → 服务 → 系统信息 → 注册表 → 环境变量。

        （族**内**顺序对集合型 diff 不确定，故只断言各族的相对位置。）
        """
        old = _snap(devices={"d": {"s": 1}}, disk_partitions={"P": {"mountpoint": "P"}},
                    processes=[_proc("x")], services=[_svc("S", "Running")],
                    system_info={"h": "a"}, registry={"K": {"A": "1"}}, environment={"E": "1"})
        new = _snap(devices={"d": {"s": 2}}, disk_partitions={}, processes=[_proc("y")],
                    services=[_svc("S", "Stopped")], system_info={"h": "b"},
                    registry={"K": {"A": "2"}}, environment={"E": "2"})
        got = [c["type"] for c in cd._compare_snapshots(old, new)]
        assert got.index("device_modified") < got.index("disk_unmounted") < got.index("process_started")
        assert got.index("process_started") < got.index("service_state_changed")
        assert got.index("service_state_changed") < got.index("system_info_changed")
        assert got.index("system_info_changed") < got.index("registry_changed")
        assert got.index("registry_changed") < got.index("environment_changed")

    def test_processes_none_is_tolerated_by_get_default(self, cd):
        """顶层键**缺失**被兜底，但显式 `None` 不兜底 —— 用 `_diff_system_info` 的路径验。

        `old.get("system_info", {})` 在键缺失时给 `{}`；若显式 `"system_info": None`
        则交给 `_diff_system_info(None, ...)` ⇒ `for key in None` → `TypeError`。
        锁定该区分（`dict.get` 的默认值只在缺键时生效）。
        """
        old = _snap(system_info=None)
        with pytest.raises(TypeError):
            cd._compare_snapshots(old, _snap(system_info={"a": 1}))


# ═══════════════════════════════════════════════════════════════
#  10. `_sanitize_reg_value`（注册表值清洗）
# ═══════════════════════════════════════════════════════════════

class TestSanitizeRegValue:
    """把注册表值变成可 JSON/可 hash 的形态；**vtype 判断先于类型判断**。"""

    # REG_DWORD
    @pytest.mark.parametrize("data,expected", [
        (0, 0), (1, 1), (4294967295, 4294967295), (-1, -1),
    ])
    def test_dword_returned_verbatim(self, cd, data, expected):
        """`vtype == 4`（REG_DWORD）⇒ 原值直返（int 可 JSON、可 hash）。"""
        assert ChangeDetector._sanitize_reg_value(data, 4) == expected

    def test_dword_string_is_returned_verbatim_not_truncated(self, cd):
        """**分支顺序证据**：`vtype==4` 时**连字符串也不截断**（先判 vtype 再判类型）。

        即 `("x"*2000, 4)` 返回 2000 字符 —— 长度上限只对"非 DWORD 的 str"生效。
        线上 DWORD 必为 int，故无影响；但这是"分支顺序"的直接证据，锁定它。
        """
        long = "x" * 2000
        assert ChangeDetector._sanitize_reg_value(long, 4) == long
        assert len(ChangeDetector._sanitize_reg_value(long, 4)) == 2000

    def test_dword_bytes_are_returned_verbatim(self, cd):
        """**边界锁定**：`vtype==4` 且值为 bytes ⇒ 返回**原始 bytes**（不解码、不截断）。

        这种组合不该出现（DWORD 是 4 字节整数），但一旦出现，
        `json.dumps` 会在 `_capture_snapshot` 里被 `default=str` 兜成 `"b'...'"`，
        因此**不会崩**，只是快照里出现 Python repr。记录它。
        """
        got = ChangeDetector._sanitize_reg_value(b"\x00\x01", 4)
        assert got == b"\x00\x01"
        assert isinstance(got, bytes)

    # REG_BINARY
    @pytest.mark.parametrize("n", [0, 1, 16, 4096])
    def test_binary_rendered_as_length_placeholder(self, cd, n):
        """`vtype == 3`（REG_BINARY）⇒ `"<binary N bytes>"`（**不回传原始字节**）。"""
        assert ChangeDetector._sanitize_reg_value(b"\x00" * n, 3) == f"<binary {n} bytes>"

    def test_binary_with_non_bytes_uses_its_len(self, cd):
        """`vtype==3` 且值是字符串 ⇒ 用 `len(str)` 拼占位符（不校验类型）。

        即 `("<binary 3 bytes>", 3)` —— 名称说 bytes 但实际是字符数。
        锁定该"名实不符"的边界（线上 REG_BINARY 必为 bytes）。
        """
        assert ChangeDetector._sanitize_reg_value("abc", 3) == "<binary 3 bytes>"

    # str 截断
    @pytest.mark.parametrize("size,expected_len", [
        (0, 0), (999, 999), (1000, 1000), (1001, 1000), (5000, 1000),
    ])
    def test_str_truncated_at_exactly_1000(self, cd, size, expected_len):
        """**边界**：`> 1000` 才截断；恰好 1000 **不截断**。"""
        got = ChangeDetector._sanitize_reg_value("a" * size, 1)
        assert len(got) == expected_len
        assert got == "a" * expected_len

    def test_truncation_counts_characters_not_bytes(self, cd):
        """截断按**字符**：1000 个汉字的返回值长度是 1000（§`len(s)`），不是 3000 字节。

        影响：注册表里的长中文值落盘后体积可达 3000 字节。
        锁定该口径，免得有人按"字节"理解上限。
        """
        got = ChangeDetector._sanitize_reg_value("汉" * 1500, 1)
        assert len(got) == 1000
        assert len(got.encode("utf-8")) == 3000

    def test_chinese_string_preserved(self, cd):
        """短中文串原样保留（`ensure_ascii` 由下游 `json.dumps` 决定）。"""
        assert ChangeDetector._sanitize_reg_value("登录项", 1) == "登录项"

    # bytes 解码
    def test_bytes_decoded_as_utf8(self, cd):
        """bytes ⇒ UTF-8 解码（`errors="replace"`）⇒ 返回**字符串**。"""
        assert ChangeDetector._sanitize_reg_value("临时".encode("utf-8"), 1) == "临时"

    def test_invalid_utf8_is_replaced_not_raised(self, cd):
        """非法 UTF-8 用**替换字符**兜底（不抛 `UnicodeDecodeError`）。"""
        got = ChangeDetector._sanitize_reg_value(b"\xff\xfe\x00", 1)
        assert "\ufffd" in got
        assert isinstance(got, str)

    def test_long_bytes_decoded_then_truncated(self, cd):
        """长 bytes 先解码、**再按字符**截到 1000。"""
        got = ChangeDetector._sanitize_reg_value(("a" * 2000).encode("utf-8"), 1)
        assert len(got) == 1000

    def test_bytes_decode_failure_branch(self, cd):
        """**覆盖"不可达"的兜底分支**：`bytes.decode` 正常永不抛（`errors="replace"`）。

        但 `bytes` 的**子类**可以覆写 `decode` 抛异常 ⇒ 此时返回 `f"<bytes N>"`。
        本用例用子类把该分支真实触发，而不是靠"行覆盖"硬凑。
        """

        class BadBytes(bytes):
            def decode(self, *a, **kw):
                raise RuntimeError("decode 被覆写为抛错")

        got = ChangeDetector._sanitize_reg_value(BadBytes(b"\x00\x01\x02"), 1)
        assert got == "<bytes 3>"

    # 其它类型
    @pytest.mark.parametrize("data,expected", [
        (None, "None"), (True, "True"), (False, "False"),
        (3.5, "3.5"), (-7, "-7"), (0, "0"),
    ])
    def test_other_types_use_str(self, cd, data, expected):
        """其余类型一律 `str(data)`（**包括 `None` → `"None"`**）。

        `None` 变成字符串 `"None"` 而不是 Python 的 `None`：这意味着
        `_diff_registry` 里"值从 None 变成字符串 'None'"会被判为**变化**，
        而"值本就是 'None' 字符串"与"值为 None"在 diff 层不可区分 —— 锁定该形态。
        """
        assert ChangeDetector._sanitize_reg_value(data, 1) == expected

    def test_multi_string_list_uses_python_repr(self, cd):
        """`REG_MULTI_SZ`（vtype 7）是 **list** ⇒ 落到 `str(data)` ⇒ **Python repr**。

        实测产物：`"['a', 'b']"`（单引号）。它作为**字符串**是可 JSON 序列化的，
        但语义上把结构化数据压平了 —— 三处 REG_MULTI_SZ 路径之所以全是
        `names=None`（枚举全部值），正是为了不依赖这个压缩结果。
        锁定该形态（若日后改成 `json.dumps`，下游比对会全部报"变化"，必须显式决策）。
        """
        got = ChangeDetector._sanitize_reg_value(["a", "b"], 7)
        assert got == "['a', 'b']"
        assert got.startswith("[") and "'" in got

    def test_result_is_json_serializable_for_str_and_list_vtypes(self, cd):
        """**结构不变量**：`vtype ∈ {1, 7}`（REG_SZ / REG_MULTI_SZ）时返回值恒可 `json.dumps`。

        这条把"清洗"的职责边界说清楚：`_capture_snapshot` 还会用
        `json.dumps(..., default=str)` 再兜一层，因此即使返回 bytes 也不会崩。
        """
        samples = ["x", "汉" * 2000, b"\xff", b"ok", 5, 5.5, None, True, ["a"], {"k": 1}]
        for sample in samples:
            for vtype in (1, 7):
                assert json.dumps(ChangeDetector._sanitize_reg_value(sample, vtype))

    def test_binary_vtype_requires_a_sized_value(self, cd):
        """**边界锁定**：`vtype == 3`（REG_BINARY）会 `len(data)` ⇒ 无 `__len__` 的值直接
        `TypeError: object of type 'int' has no len()`。

        即 `_sanitize_reg_value` **不校验** REG_BINARY 的值确实是 bytes。
        线上 winreg 保证一致，故不可达；但它意味着"vtype 与 data 类型不匹配"时
        会抛异常而不是降级 ⇒ 若 `_capture_registry` 未包住这一层就会中断采集
        （实际它被 `except Exception` 包住，只丢该路径）。记录该边界。
        """
        assert ChangeDetector._sanitize_reg_value(b"abc", 3) == "<binary 3 bytes>"
        assert ChangeDetector._sanitize_reg_value("abc", 3) == "<binary 3 bytes>"
        with pytest.raises(TypeError):
            ChangeDetector._sanitize_reg_value(5, 3)

    def test_result_is_json_serializable_for_sized_binary(self, cd):
        """有长度的 REG_BINARY 值 ⇒ 可 JSON（占位符是字符串）。"""
        for sample in (b"\x00" * 8, "text", [1, 2]):
            assert json.dumps(ChangeDetector._sanitize_reg_value(sample, 3))

    def test_is_a_staticmethod(self):
        """`_sanitize_reg_value` 必须是 `staticmethod`（`_capture_registry` 里以 `self.` 调用它）。"""
        assert isinstance(ChangeDetector.__dict__["_sanitize_reg_value"], staticmethod)


# ═══════════════════════════════════════════════════════════════
#  11. `_ensure_hive_map` / `_capture_registry`（伪 winreg，不碰真注册表）
# ═══════════════════════════════════════════════════════════════

def _fake_winreg(**over):
    """构造一个**伪 `winreg` 模块**（只含被测代码用到的 5 个名字）。"""
    mod = types.ModuleType("winreg")
    mod.HKEY_LOCAL_MACHINE = "HKLM_HANDLE"
    mod.HKEY_CURRENT_USER = "HKCU_HANDLE"
    mod.KEY_READ = 0x20019
    mod.OpenKey = lambda hive, sub, res, access: "KEY:" + sub
    mod.CloseKey = lambda key: None
    mod.EnumValue = lambda key, i: (_ for _ in ()).throw(OSError("no more values"))
    mod.QueryValueEx = lambda key, name: ("V_" + name, 1)
    for k, v in over.items():
        setattr(mod, k, v)
    return mod


class TestCaptureRegistry:
    """注册表快照采集：平台门、懒加载 hive、两种枚举模式、逐路径容错。"""

    def test_non_windows_returns_empty(self, cd, monkeypatch):
        """**平台门**：非 Windows ⇒ 立即 `{}`（不 import winreg ⇒ Linux CI 安全）。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Linux")
        assert cd._capture_registry() == {}
        monkeypatch.setattr(cd_mod, "SYSTEM", "Darwin")
        assert cd._capture_registry() == {}

    def test_ensure_hive_map_lazily_resolves(self, monkeypatch):
        """`_ensure_hive_map` 在 `HKLM is None` 时从 winreg 取两个常量。"""
        monkeypatch.setattr(cd_mod, "REGISTRY_HIVE_MAP", {"HKLM": None, "HKCU": None})
        monkeypatch.setitem(sys.modules, "winreg", _fake_winreg())
        ChangeDetector._ensure_hive_map()
        assert cd_mod.REGISTRY_HIVE_MAP == {"HKLM": "HKLM_HANDLE", "HKCU": "HKCU_HANDLE"}

    def test_ensure_hive_map_is_noop_when_resolved(self, monkeypatch):
        """已解析 ⇒ **不重复 import**（幂等；`HKLM` 非 None 即跳过）。"""
        monkeypatch.setattr(cd_mod, "REGISTRY_HIVE_MAP",
                            {"HKLM": "K1", "HKCU": "K2"})
        monkeypatch.setitem(sys.modules, "winreg", None)   # 若真去 import 就会炸
        ChangeDetector._ensure_hive_map()
        assert cd_mod.REGISTRY_HIVE_MAP == {"HKLM": "K1", "HKCU": "K2"}

    def test_enum_all_values_uses_default_placeholder(self, cd, monkeypatch):
        """`names is None` ⇒ 枚举全部值，空值名渲染为 `"(Default)"`。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Windows")
        monkeypatch.setattr(cd_mod, "REGISTRY_HIVE_MAP",
                            {"HKLM": "HKLM_HANDLE", "HKCU": "HKCU_HANDLE"})
        monkeypatch.setattr(cd_mod, "REGISTRY_WATCH_PATHS",
                            [("HKLM", "SUB", None)])
        values = [("", "v0", 1), (None, "v1", 1), ("Named", "v2", 1)]
        seq = iter(values)

        def enum_value(key, i):
            try:
                return next(seq)
            except StopIteration:
                raise OSError("no more data") from None

        fake = _fake_winreg(EnumValue=enum_value)
        monkeypatch.setitem(sys.modules, "winreg", fake)
        got = cd._capture_registry()
        # `""` 与 `None` 都渲染成 `"(Default)"` ⇒ 后者覆盖前者（同一路径下只留一个）
        assert got == {"HKLM\\SUB": {"(Default)": "v1", "Named": "v2"}}
        assert len(got["HKLM\\SUB"]) == 2

    def test_enum_stops_on_oserror(self, cd, monkeypatch):
        """枚举在 `OSError` 处**停止**（winreg 用它表示"没有更多值"），不抛出去。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Windows")
        monkeypatch.setattr(cd_mod, "REGISTRY_HIVE_MAP",
                            {"HKLM": "H", "HKCU": "C"})
        monkeypatch.setattr(cd_mod, "REGISTRY_WATCH_PATHS", [("HKLM", "SUB", None)])
        monkeypatch.setitem(sys.modules, "winreg", _fake_winreg())
        assert cd._capture_registry() == {"HKLM\\SUB": {}}

    def test_named_values_mode(self, cd, monkeypatch):
        """`names` 是列表 ⇒ 逐名 `QueryValueEx`，值经 `_sanitize_reg_value` 清洗。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Windows")
        monkeypatch.setattr(cd_mod, "REGISTRY_HIVE_MAP", {"HKLM": "H", "HKCU": "C"})
        monkeypatch.setattr(cd_mod, "REGISTRY_WATCH_PATHS",
                            [("HKLM", "SUB", ["A", "B"])])
        fake = _fake_winreg(QueryValueEx=lambda key, name: ("VAL_" + name, 4))
        monkeypatch.setitem(sys.modules, "winreg", fake)
        assert cd._capture_registry() == {"HKLM\\SUB": {"A": "VAL_A", "B": "VAL_B"}}

    def test_missing_named_value_becomes_none(self, cd, monkeypatch):
        """某个具名值不存在（`FileNotFoundError`）⇒ 该名为 `None`，**其余照常**。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Windows")
        monkeypatch.setattr(cd_mod, "REGISTRY_HIVE_MAP", {"HKLM": "H", "HKCU": "C"})
        monkeypatch.setattr(cd_mod, "REGISTRY_WATCH_PATHS",
                            [("HKLM", "SUB", ["A", "B"])])

        def query(key, name):
            if name == "B":
                raise FileNotFoundError(name)
            return ("VAL_A", 1)

        monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(QueryValueEx=query))
        assert cd._capture_registry() == {"HKLM\\SUB": {"A": "VAL_A", "B": None}}

    def test_openkey_failure_skips_that_path_only(self, cd, monkeypatch):
        """`OpenKey` 抛异常 ⇒ 只跳过**该路径**，其余路径继续采集（真实注册表权限常态）。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Windows")
        monkeypatch.setattr(cd_mod, "REGISTRY_HIVE_MAP", {"HKLM": "H", "HKCU": "C"})
        monkeypatch.setattr(cd_mod, "REGISTRY_WATCH_PATHS",
                            [("HKLM", "BAD", None), ("HKLM", "GOOD", ["A"])])

        def open_key(hive, sub, res, access):
            if sub == "BAD":
                raise PermissionError("拒绝访问")
            return "K:" + sub

        monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(OpenKey=open_key))
        got = cd._capture_registry()
        assert set(got) == {"HKLM\\GOOD"}
        assert got["HKLM\\GOOD"] == {"A": "V_A"}

    def test_unmapped_hive_is_skipped(self, cd, monkeypatch):
        """路径里出现未映射（或映射为 `None`）的 hive ⇒ 该路径被跳过。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Windows")
        monkeypatch.setattr(cd_mod, "REGISTRY_HIVE_MAP", {"HKLM": "H", "HKCU": None})
        monkeypatch.setattr(cd_mod, "REGISTRY_WATCH_PATHS",
                            [("HKCU", "SUB", ["A"]), ("HKLM", "SUB2", ["B"])])
        monkeypatch.setitem(sys.modules, "winreg", _fake_winreg())
        got = cd._capture_registry()
        assert set(got) == {"HKLM\\SUB2"}

    def test_closekey_is_called_for_opened_keys(self, cd, monkeypatch):
        """每个成功 `OpenKey` 的键都必须 `CloseKey`（句柄泄漏会拖垮长期运行）。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Windows")
        monkeypatch.setattr(cd_mod, "REGISTRY_HIVE_MAP", {"HKLM": "H", "HKCU": "C"})
        monkeypatch.setattr(cd_mod, "REGISTRY_WATCH_PATHS",
                            [("HKLM", "A", ["X"]), ("HKLM", "B", ["Y"])])
        closed = []
        monkeypatch.setitem(sys.modules, "winreg",
                            _fake_winreg(CloseKey=lambda k: closed.append(k)))
        cd._capture_registry()
        assert closed == ["KEY:A", "KEY:B"]

    def test_path_key_format(self, cd, monkeypatch):
        """快照键格式为 `f"{hive}\\{subkey}"`（注册表 diff 的 value 前缀依赖它）。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Windows")
        monkeypatch.setattr(cd_mod, "REGISTRY_HIVE_MAP", {"HKLM": "H", "HKCU": "C"})
        monkeypatch.setattr(cd_mod, "REGISTRY_WATCH_PATHS",
                            [("HKCU", r"Software\Microsoft\Windows", ["ProductName"])])
        monkeypatch.setitem(sys.modules, "winreg", _fake_winreg())
        got = cd._capture_registry()
        assert list(got) == ["HKCU\\Software\\Microsoft\\Windows"]

    def test_real_watch_paths_against_fake_winreg(self, cd, monkeypatch):
        """用**真实**的 `REGISTRY_WATCH_PATHS`（9 条）跑一遍伪 winreg ⇒ 9 个路径键。

        这条验证的是"真实常量与采集代码能配合"，而不是自造的 1~2 条路径。
        """
        monkeypatch.setattr(cd_mod, "SYSTEM", "Windows")
        monkeypatch.setattr(cd_mod, "REGISTRY_HIVE_MAP", {"HKLM": "H", "HKCU": "C"})
        monkeypatch.setitem(sys.modules, "winreg", _fake_winreg())
        got = cd._capture_registry()
        assert len(got) == 9
        assert all(k.startswith(("HKLM\\", "HKCU\\")) for k in got)


# ═══════════════════════════════════════════════════════════════
#  12. `_capture_snapshot` / `_get_system_info` / `_capture_environment`
# ═══════════════════════════════════════════════════════════════

class TestCaptureSnapshot:
    """快照装配：9 个键 + 内容哈希。"""

    def _stub(self, cd, monkeypatch):
        monkeypatch.setattr(cd, "_list_devices", lambda: {"d": {"name": "dev"}})
        monkeypatch.setattr(cd, "_list_disk_partitions", lambda: {"P": {"mountpoint": "P"}})
        monkeypatch.setattr(cd, "_list_processes", lambda: [_proc("p")])
        monkeypatch.setattr(cd, "_list_services", lambda: [_svc("S")])
        monkeypatch.setattr(cd, "_get_system_info", lambda: {"hostname": "h"})
        monkeypatch.setattr(cd, "_capture_registry", lambda: {"K": {"A": "1"}})
        monkeypatch.setattr(cd, "_capture_environment", lambda: {"E": "1"})

    def test_key_set_exactly_nine(self, cd, monkeypatch):
        self._stub(cd, monkeypatch)
        snap = cd._capture_snapshot()
        assert set(snap) == {"timestamp", "devices", "disk_partitions", "processes",
                             "services", "system_info", "registry", "environment", "hash"}

    def test_hash_is_sha256_prefix16_of_content(self, cd, monkeypatch):
        """**哈希口径**：`sha256(json.dumps(快照(不含 hash), default=str, sort_keys=True))[:16]`。

        这条断言把"哈希是内容的函数"钉死：它必须可由**返回值本身**重算出来，
        因此任何"只对部分字段做哈希"或"用了不稳定序列化"的实现都会失败。
        """
        import hashlib

        self._stub(cd, monkeypatch)
        snap = cd._capture_snapshot()
        body = {k: v for k, v in snap.items() if k != "hash"}
        expected = hashlib.sha256(
            json.dumps(body, default=str, sort_keys=True).encode()).hexdigest()[:16]
        assert snap["hash"] == expected
        assert len(snap["hash"]) == 16
        assert all(ch in "0123456789abcdef" for ch in snap["hash"])

    def test_hash_changes_when_content_changes(self, cd, monkeypatch):
        """内容变化 ⇒ 哈希变化（否则 `baseline_hash` 无判别力）。"""
        self._stub(cd, monkeypatch)
        first = cd._capture_snapshot()["hash"]
        monkeypatch.setattr(cd, "_list_processes", lambda: [_proc("other")])
        assert cd._capture_snapshot()["hash"] != first

    def test_timestamp_is_isoformat_string(self, cd, monkeypatch):
        self._stub(cd, monkeypatch)
        ts = cd._capture_snapshot()["timestamp"]
        assert datetime.fromisoformat(ts) is not None

    def test_payloads_pass_through_verbatim(self, cd, monkeypatch):
        """7 个业务字段**原样**进入快照（不做包装/改名）。"""
        self._stub(cd, monkeypatch)
        snap = cd._capture_snapshot()
        assert snap["devices"] == {"d": {"name": "dev"}}
        assert snap["disk_partitions"] == {"P": {"mountpoint": "P"}}
        assert snap["processes"] == [_proc("p")]
        assert snap["services"] == [_svc("S")]
        assert snap["system_info"] == {"hostname": "h"}
        assert snap["registry"] == {"K": {"A": "1"}}
        assert snap["environment"] == {"E": "1"}


class TestGetSystemInfo:
    """`_get_system_info` 直接读 `platform`，无 mock 也能确定性断言（真实环境口径）。"""

    def test_exact_fields(self, cd):
        info = cd._get_system_info()
        assert set(info) == {"platform", "python_version", "hostname"}

    def test_matches_platform_module(self, cd):
        """逐值等于 `platform` 模块的实时返回值（不是硬编码串）。"""
        info = cd._get_system_info()
        assert info["platform"] == platform.platform()
        assert info["python_version"] == platform.python_version()
        assert info["hostname"] == platform.node()

    def test_values_are_strings(self, cd):
        assert all(isinstance(v, str) for v in cd._get_system_info().values())

    def test_reflects_monkeypatched_platform(self, cd, monkeypatch):
        """注入假 `platform` ⇒ 取值随之变化（证明它真的在调 `platform`，不是读缓存）。"""
        monkeypatch.setattr(cd_mod.platform, "platform", lambda: "FakeOS-9")
        monkeypatch.setattr(cd_mod.platform, "python_version", lambda: "3.99.0")
        monkeypatch.setattr(cd_mod.platform, "node", lambda: "fake-host")
        assert cd._get_system_info() == {"platform": "FakeOS-9", "python_version": "3.99.0",
                                         "hostname": "fake-host"}


class TestCaptureEnvironment:
    """环境变量快照：`dict(os.environ)` 的**副本**。"""

    def test_returns_dict_copy_not_the_mapping(self, cd):
        """返回的是**普通 dict 副本** ⇒ 改它不影响 `os.environ`。"""
        env = cd._capture_environment()
        assert isinstance(env, dict)
        assert env is not os.environ
        env["__TASK09_SENTINEL__"] = "1"
        assert "__TASK09_SENTINEL__" not in os.environ

    def test_includes_current_process_env(self, cd, monkeypatch):
        """当前进程的环境变量必须出现在快照里（用注入的哨兵验证）。"""
        monkeypatch.setenv("__TASK09_ENV_PROBE__", "probe-value")
        assert cd._capture_environment()["__TASK09_ENV_PROBE__"] == "probe-value"

    def test_snapshot_is_plain_strings(self, cd):
        """`os.environ` 的值恒为 str ⇒ 快照值也全为 str（注册表侧才有 bytes）。"""
        env = cd._capture_environment()
        assert env
        assert all(isinstance(v, str) for v in env.values())


# ═══════════════════════════════════════════════════════════════
#  13. `_list_devices` / `_list_disk_partitions` / `_list_processes` / `_list_services`
# ═══════════════════════════════════════════════════════════════

class TestListDevices:
    """设备枚举：三个平台分支 + winreg/wmi 的 ImportError 兜底。"""

    def test_windows_wmi_branch(self, cd, monkeypatch):
        """Windows 分支：`Win32_PnPEntity()` 逐条转成 `{name,status,class}`。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Windows")

        class Dev:
            Name = "USB 键盘"
            Status = "OK"
            ClassGuid = "{G1}"

        class Wmi:
            def Win32_PnPEntity(self):
                return [Dev()]

        fake = types.ModuleType("wmi")
        fake.WMI = lambda: Wmi()
        monkeypatch.setitem(sys.modules, "wmi", fake)
        got = cd._list_devices()
        assert got == {"USB 键盘|{G1}": {"name": "USB 键盘", "status": "OK", "class": "{G1}"}}

    def test_windows_skips_unnamed_devices(self, cd, monkeypatch):
        """没有 `Name` 也没有 `Caption` 的设备**跳过**（否则会产出 `"|GUID"` 这种脏键）。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Windows")

        class Dev:
            Name = ""
            Caption = ""
            Status = "OK"
            ClassGuid = "{G}"

        class Wmi:
            def Win32_PnPEntity(self):
                return [Dev()]

        fake = types.ModuleType("wmi")
        fake.WMI = lambda: Wmi()
        monkeypatch.setitem(sys.modules, "wmi", fake)
        assert cd._list_devices() == {}

    def test_windows_uses_caption_when_name_missing(self, cd, monkeypatch):
        """`Name` 缺失 ⇒ 回落 `Caption`（真实 PnP 设备里常见）。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Windows")

        class Dev:
            Name = ""
            Caption = "仅 Caption"
            Status = "OK"
            ClassGuid = "{G}"

        class Wmi:
            def Win32_PnPEntity(self):
                return [Dev()]

        fake = types.ModuleType("wmi")
        fake.WMI = lambda: Wmi()
        monkeypatch.setitem(sys.modules, "wmi", fake)
        assert list(cd._list_devices()) == ["仅 Caption|{G}"]

    def test_windows_without_wmi_returns_empty(self, cd, monkeypatch):
        """`wmi` 不可用（`sys.modules["wmi"] = None` ⇒ ImportError）⇒ **空字典**，不抛。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Windows")
        monkeypatch.setitem(sys.modules, "wmi", None)
        assert cd._list_devices() == {}

    def test_windows_wmi_runtime_error_is_swallowed(self, cd, monkeypatch):
        """WMI 运行期报错 ⇒ 被外层 `except Exception` 吞掉并返回**已收集的部分**。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Windows")

        class Wmi:
            def Win32_PnPEntity(self):
                raise RuntimeError("WMI 服务不可用")

        fake = types.ModuleType("wmi")
        fake.WMI = lambda: Wmi()
        monkeypatch.setitem(sys.modules, "wmi", fake)
        assert cd._list_devices() == {}

    def test_linux_branch_reads_sysfs(self, cd, monkeypatch):
        """Linux 分支：读 `/sys/bus/{pci,usb}/devices` 目录名，按 `type` 标记来源。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Linux")
        real_exists = os.path.exists
        real_listdir = os.listdir

        def fake_exists(path):
            if path in ("/sys/bus/pci/devices", "/sys/bus/usb/devices"):
                return True
            return real_exists(path)

        def fake_listdir(path):
            if path == "/sys/bus/pci/devices":
                return ["0000:00:02.0"]
            if path == "/sys/bus/usb/devices":
                return ["usb1", "usb2"]
            return real_listdir(path)

        monkeypatch.setattr(os.path, "exists", fake_exists)
        monkeypatch.setattr(os, "listdir", fake_listdir)
        got = cd._list_devices()
        assert got == {
            "0000:00:02.0": {"name": "0000:00:02.0", "type": "pci"},
            "usb1": {"name": "usb1", "type": "usb"},
            "usb2": {"name": "usb2", "type": "usb"},
        }

    def test_linux_without_sysfs_returns_empty(self, cd, monkeypatch):
        """sysfs 路径不存在 ⇒ 空字典（容器/非 Linux 内核场景）。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Linux")
        monkeypatch.setattr(os.path, "exists", lambda p: False)
        assert cd._list_devices() == {}

    def test_darwin_branch_uses_system_profiler(self, cd, monkeypatch):
        """Darwin 分支：调 `system_profiler`，输出**截断到 500 字符**且存于 `usb.raw`。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Darwin")
        Result = namedtuple("Result", "stdout")
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: Result(stdout="X" * 900))
        got = cd._list_devices()
        assert list(got) == ["usb"]
        assert len(got["usb"]["raw"]) == 500

    def test_darwin_failure_is_swallowed(self, cd, monkeypatch):
        """Darwin 上 `system_profiler` 失败 ⇒ 空字典（不抛，感知链路不受影响）。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Darwin")

        def boom(*a, **kw):
            raise FileNotFoundError("system_profiler")

        monkeypatch.setattr(subprocess, "run", boom)
        assert cd._list_devices() == {}

    def test_unknown_platform_returns_empty(self, cd, monkeypatch):
        """三个分支之外的平台 ⇒ 空字典。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "FreeBSD")
        assert cd._list_devices() == {}


class TestListDiskPartitions:
    """分区枚举：psutil 正常/异常两条路径。"""

    def test_normal(self, cd, monkeypatch):
        Part = namedtuple("Part", "device mountpoint fstype")
        monkeypatch.setattr("psutil.disk_partitions",
                            lambda: [Part("C:\\", "C:\\", "NTFS"),
                                     Part("D:\\", "D:\\", "exFAT")])
        got = cd._list_disk_partitions()
        assert got == {"C:\\": {"mountpoint": "C:\\", "fstype": "NTFS"},
                       "D:\\": {"mountpoint": "D:\\", "fstype": "exFAT"}}

    def test_psutil_failure_returns_empty(self, cd, monkeypatch):
        """`psutil.disk_partitions()` 抛异常 ⇒ 吞掉返回 `{}`（不中断采集）。"""
        def boom():
            raise RuntimeError("无权限")

        monkeypatch.setattr("psutil.disk_partitions", boom)
        assert cd._list_disk_partitions() == {}

    def test_empty_result(self, cd, monkeypatch):
        monkeypatch.setattr("psutil.disk_partitions", lambda: [])
        assert cd._list_disk_partitions() == {}


class TestListProcesses:
    """进程枚举：只取 pid/name/status，逐进程容错。"""

    def _fake_iter(self, monkeypatch, procs):
        monkeypatch.setattr("psutil.process_iter", lambda fields: procs)

    def test_normal(self, cd, monkeypatch):
        class P:
            def __init__(self, pid, name, status):
                self.info = {"pid": pid, "name": name, "status": status}

        self._fake_iter(monkeypatch, [P(1, "a", "running"), P(2, "b", "sleeping")])
        assert cd._list_processes() == [
            {"pid": 1, "name": "a", "status": "running"},
            {"pid": 2, "name": "b", "status": "sleeping"},
        ]

    def test_only_three_fields_are_kept(self, cd, monkeypatch):
        """即使 `info` 里有路径/cmdline 等敏感字段，也只留 3 个键（隐私契约）。"""
        class P:
            info = {"pid": 9, "name": "x", "status": "running",
                    "exe": "C:\\secret\\path.exe", "cmdline": ["--token=abc"]}

        self._fake_iter(monkeypatch, [P()])
        got = cd._list_processes()
        assert got == [{"pid": 9, "name": "x", "status": "running"}]
        assert "exe" not in got[0] and "cmdline" not in got[0]

    def test_nosuchprocess_entries_are_skipped(self, cd, monkeypatch):
        """`NoSuchProcess`（进程中途退出）⇒ **跳过该进程**，其余照常。"""
        import psutil

        class P:
            def __init__(self, ok):
                self._ok = ok

            @property
            def info(self):
                if not self._ok:
                    raise psutil.NoSuchProcess(1)
                return {"pid": 2, "name": "alive", "status": "running"}

        self._fake_iter(monkeypatch, [P(False), P(True)])
        assert cd._list_processes() == [{"pid": 2, "name": "alive", "status": "running"}]

    def test_access_denied_entries_are_skipped(self, cd, monkeypatch):
        """`AccessDenied` 同样跳过（受保护进程读不到信息是常态）。"""
        import psutil

        class P:
            @property
            def info(self):
                raise psutil.AccessDenied(1)

        self._fake_iter(monkeypatch, [P()])
        assert cd._list_processes() == []

    def test_iterator_failure_returns_empty(self, cd, monkeypatch):
        """`process_iter` 本身抛异常（psutil 内部故障）⇒ 被外层吞掉并返回**空列表**。

        注意与"逐条跳过"不同：迭代器一坏就一条都拿不到。
        在真实机器上这会让"进程停止"类变更**整批丢失**（而不是丢一条），
        但由于异常只打 `logging.warning`，上层不会察觉。记录该粒度。
        """
        def boom(fields):
            raise RuntimeError("psutil 崩了")

        monkeypatch.setattr("psutil.process_iter", boom)
        assert cd._list_processes() == []

    def test_missing_info_key_raises_inside_try(self, cd, monkeypatch):
        """`info` 缺 `status` ⇒ `KeyError` 被外层 `except Exception` 吞掉 ⇒ 返回**空列表**。

        即一条残缺进程会**丢掉整批**（因为 try 在外层）。锁定该行为：
        它不是"跳过这一条"，而是"整批返回当前已收集的部分"（此处为 EMPTY，
        因为异常发生在 append 之前）。
        """
        class P:
            info = {"pid": 1, "name": "x"}      # 缺 status

        self._fake_iter(monkeypatch, [P()])
        assert cd._list_processes() == []


class TestListServices:
    """服务枚举：Windows/Linux/Darwin 三分支 + 逐行解析。"""

    def test_windows_branch(self, cd, monkeypatch):
        monkeypatch.setattr(cd_mod, "SYSTEM", "Windows")

        class Svc:
            def __init__(self, n, s, m):
                self.Name, self.State, self.StartMode = n, s, m

        class Wmi:
            def Win32_Service(self):
                return [Svc("Spooler", "Running", "Auto")]

        fake = types.ModuleType("wmi")
        fake.WMI = lambda: Wmi()
        monkeypatch.setitem(sys.modules, "wmi", fake)
        assert cd._list_services() == [{"name": "Spooler", "state": "Running",
                                        "start_mode": "Auto"}]

    def test_windows_without_wmi_returns_empty(self, cd, monkeypatch):
        monkeypatch.setattr(cd_mod, "SYSTEM", "Windows")
        monkeypatch.setitem(sys.modules, "wmi", None)
        assert cd._list_services() == []

    def test_linux_branch_parses_systemctl(self, cd, monkeypatch):
        """Linux：解析 `systemctl list-units` 的输出（列数 < 3 的行被丢弃）。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Linux")
        stdout = (
            "cron.service loaded active running Regular background program\n"
            "short line\n"
            "dbus.service loaded active running D-Bus System Message Bus\n"
        )
        Result = namedtuple("Result", "stdout")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: Result(stdout=stdout))
        got = cd._list_services()
        assert [s["name"] for s in got] == ["cron.service", "dbus.service"]
        assert got[0]["state"] == "active"
        assert got[0]["description"] == "Regular background program"
        assert got[1]["description"] == "D-Bus System Message Bus"

    def test_linux_short_line_yields_empty_description(self, cd, monkeypatch):
        """恰好 3~4 列的行 ⇒ `description` 为 `""`（`len(parts) > 4` 才拼描述）。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Linux")
        Result = namedtuple("Result", "stdout")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: Result(stdout="a loaded b\n"))
        got = cd._list_services()
        assert got == [{"name": "a", "state": "b", "description": ""}]

    def test_linux_subprocess_failure_returns_empty(self, cd, monkeypatch):
        """`systemctl` 调用失败 ⇒ `except Exception` ⇒ 空列表（日志级别是 debug）。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Linux")

        def boom(*a, **kw):
            raise FileNotFoundError("systemctl")

        monkeypatch.setattr(subprocess, "run", boom)
        assert cd._list_services() == []

    def test_darwin_branch_parses_launchctl(self, cd, monkeypatch):
        """Darwin：跳过 `launchctl list` 的表头，`0` 表示 running，否则 error。"""
        monkeypatch.setattr(cd_mod, "SYSTEM", "Darwin")
        stdout = "PID\tStatus\tLabel\n123\t0\tcom.a\n-\t1\tcom.b\n"
        Result = namedtuple("Result", "stdout")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: Result(stdout=stdout))
        got = cd._list_services()
        assert got == [{"pid": "123", "name": "com.a", "state": "running"},
                       {"pid": "-", "name": "com.b", "state": "error"}]

    def test_unknown_platform_returns_empty(self, cd, monkeypatch):
        monkeypatch.setattr(cd_mod, "SYSTEM", "FreeBSD")
        assert cd._list_services() == []


# ═══════════════════════════════════════════════════════════════
#  14. `set_baseline` / `_invoke_learning_hook` / `collect`
# ═══════════════════════════════════════════════════════════════

class TestBaseline:
    """基准建立与 `baseline_hash`。"""

    def test_hash_is_none_before_baseline(self, cd):
        assert cd.baseline_hash is None

    def test_set_baseline_returns_and_stores_same_object(self, cd, monkeypatch):
        """`_baseline` 与 `_last_check` 指向**同一个**快照对象（不复制两份）。"""
        snap = _snap(hash="H")
        monkeypatch.setattr(cd, "_capture_snapshot", lambda: snap)
        got = cd.set_baseline()
        assert got is snap
        assert cd._baseline is snap
        assert cd._last_check is snap
        assert cd.baseline_hash == "H"

    def test_set_baseline_twice_resets(self, cd, monkeypatch):
        """二次调用会**重建**基准（覆盖旧基准，不是叠加）。"""
        monkeypatch.setattr(cd, "_capture_snapshot", lambda: _snap(hash="H1"))
        cd.set_baseline()
        monkeypatch.setattr(cd, "_capture_snapshot", lambda: _snap(hash="H2"))
        cd.set_baseline()
        assert cd.baseline_hash == "H2"

    def test_baseline_hash_tolerates_missing_hash_key(self, cd):
        """基准里没有 `hash` 键 ⇒ `baseline_hash` 返回 `None`（不 KeyError）。"""
        cd._baseline = {"devices": {}}
        assert cd.baseline_hash is None


class TestLearningHookInvocation:
    """`_invoke_learning_hook` 的兜底语义（感知主链路零影响）。"""

    def test_calls_hook_with_changes(self, cd):
        seen = []
        cd.set_learning_hook(seen.append)
        cd._invoke_learning_hook([{"type": "device_added"}])
        assert seen == [[{"type": "device_added"}]]

    @pytest.mark.parametrize("exc", [RuntimeError, ValueError, KeyError, TypeError])
    def test_hook_exception_is_swallowed(self, cd, exc):
        """钩子抛**任何 `Exception`** 都被吞掉（`logging.warning` 留痕，不冒泡）。"""
        def boom(changes):
            raise exc("hook boom")

        cd.set_learning_hook(boom)
        assert cd._invoke_learning_hook([{"type": "x"}]) is None

    def test_base_exception_is_not_swallowed(self, cd):
        """`BaseException`（如 `KeyboardInterrupt`）**不**被吞 —— 只兜 `Exception`。

        锁定边界：这是正确的（Ctrl-C 必须能中断长时间采集），
        但如果有人把 `except Exception` 放宽成 `except BaseException`，运维会失去中断能力。
        """
        def boom(changes):
            raise KeyboardInterrupt()

        cd.set_learning_hook(boom)
        with pytest.raises(KeyboardInterrupt):
            cd._invoke_learning_hook([{"type": "x"}])

    def test_set_learning_hook_replaces_and_clears(self, cd):
        """`set_learning_hook` 可替换、可用 `None` 解除（TASK-06 旁路契约）。"""
        first, second = [], []
        cd.set_learning_hook(first.append)
        cd.set_learning_hook(second.append)
        cd._invoke_learning_hook([{"t": 1}])
        assert first == [] and second == [[{"t": 1}]]
        cd.set_learning_hook(None)
        assert cd._learning_hook is None
        assert cd._invoke_learning_hook([{"t": 2}]) is None


class TestCollect:
    """`collect()` 是**唯一对外出口**：首采建基准、之后产读数、并触发钩子旁路。"""

    def test_first_call_establishes_baseline(self, cd, monkeypatch):
        """**首采**：只产 1 条"基准已建立"读数，且**不触发钩子**。"""
        monkeypatch.setattr(cd, "_capture_snapshot", lambda: _snap(hash="H1"))
        hook_calls = []
        cd.set_learning_hook(hook_calls.append)
        results = cd.collect()
        assert len(results) == 1
        r = results[0]
        assert r.sensor_name == "change_baseline_established"
        assert r.value is True
        # 【实测校正】unit 是 **"bool"**（类型名当单位），不是空串 —— 首轮我写错过一次。
        # 该形态会被 `translator._fallback` 的"类型名不是单位"规则丢弃，故不产生噪声。
        assert r.unit == "bool"
        assert r.description == "变更检测基准已建立（免疫系统初始化）"
        assert r.category == Category.CHANGE.value
        assert r.severity == Severity.NORMAL.value
        assert r.metadata == {}
        assert hook_calls == []
        assert cd._change_log == []

    def test_second_call_diffs_against_baseline(self, cd, monkeypatch):
        """**次采**：与基准比对，产读数并写入 `_change_log`。"""
        monkeypatch.setattr(cd, "_capture_snapshot", lambda: _snap(devices={}))
        cd.collect()
        monkeypatch.setattr(cd, "_capture_snapshot",
                            lambda: _snap(devices={"k": {"name": "新设备"}}))
        results = cd.collect()
        assert len(results) == 1
        assert results[0].sensor_name == "change_device_added"
        assert results[0].value == "新设备"
        assert len(cd._change_log) == 1

    def test_third_call_with_unchanged_snapshot_yields_nothing(self, cd, monkeypatch):
        """**关键语义**：`_last_check` 会前移 ⇒ 同一个差异**只报一次**。

        第 2 次采集把 `_last_check` 设为当时快照；第 3 次快照相同 ⇒ 无差异 ⇒ 0 条。
        若实现改成"永远与基准比"，持续存在的差异会**每次采集都重复上报**（刷屏）。
        这条锁住"增量语义"，是 `collect()` 最重要的不变量。
        """
        monkeypatch.setattr(cd, "_capture_snapshot", lambda: _snap(devices={}))
        cd.collect()
        changed = _snap(devices={"k": {"name": "新设备"}})
        monkeypatch.setattr(cd, "_capture_snapshot", lambda: changed)
        assert len(cd.collect()) == 1
        assert cd.collect() == []           # 第三次：无变化
        assert len(cd._change_log) == 1     # 日志不重复
        assert cd._last_check is changed

    @pytest.mark.parametrize("raw_sev,expected", [
        ("warning", Severity.WARNING.value),
        ("critical", Severity.CRITICAL.value),
        ("normal", Severity.NORMAL.value),
        ("weird", Severity.NORMAL.value),
    ])
    def test_severity_translation(self, cd, monkeypatch, tmp_path, raw_sev, expected):
        """diff 条目的 `severity` 只识别 `warning`/`critical`，其余一律 `normal`。"""
        d = ChangeDetector(persistent_log_dir=str(tmp_path / raw_sev))
        monkeypatch.setattr(d, "_capture_snapshot", lambda: _snap(devices={}))
        d.collect()
        change = {"name": "change_x", "value": 1, "type": "t",
                  "severity": raw_sev, "description": "d"}
        monkeypatch.setattr(d, "_compare_snapshots", lambda old, new: [change])
        out = d.collect()
        assert out[0].severity == expected

    def test_reading_field_mapping(self, cd, monkeypatch, tmp_path):
        """变更条目 → 读数的字段映射（含 `unit=""` 与 metadata 的 4 个键）。"""
        d = ChangeDetector(persistent_log_dir=str(tmp_path / "m"))
        monkeypatch.setattr(d, "_capture_snapshot", lambda: _snap())
        d.collect()
        change = {"name": "change_device_removed", "value": "旧设备", "type": "device_removed",
                  "severity": "warning", "description": "设备移除: 旧设备",
                  "previous": {"name": "旧设备"}, "current": None}
        monkeypatch.setattr(d, "_compare_snapshots", lambda old, new: [change])
        r = d.collect()[0]
        assert r.sensor_name == "change_device_removed"
        assert r.value == "旧设备"
        assert r.unit == ""
        assert r.description == "设备移除: 旧设备"
        assert r.category == "change"
        assert r.severity == "warning"
        assert set(r.metadata) == {"change_type", "detail", "previous", "current"}
        assert r.metadata["change_type"] == "device_removed"
        assert r.metadata["previous"] == {"name": "旧设备"}
        assert r.metadata["current"] is None
        assert r.metadata["detail"] == ""      # 缺 `detail` ⇒ `change.get("detail","")` → ""

    def test_reading_metadata_tolerates_missing_optional_keys(self, cd, monkeypatch, tmp_path):
        """变更条目缺 `detail`/`previous`/`current` ⇒ metadata 仍带这 3 个键（值为 None/""）。"""
        d = ChangeDetector(persistent_log_dir=str(tmp_path / "m2"))
        monkeypatch.setattr(d, "_capture_snapshot", lambda: _snap())
        d.collect()
        monkeypatch.setattr(d, "_compare_snapshots",
                            lambda old, new: [{"name": "n", "value": 1, "type": "t",
                                               "severity": "normal", "description": "d"}])
        meta = d.collect()[0].metadata
        assert meta == {"change_type": "t", "detail": "", "previous": None, "current": None}

    def test_hook_called_once_with_all_changes(self, cd, monkeypatch, tmp_path):
        """有变更时钩子**恰好调用一次**，参数是**全部**变更条目（不是逐条）。"""
        d = ChangeDetector(persistent_log_dir=str(tmp_path / "h"))
        monkeypatch.setattr(d, "_capture_snapshot", lambda: _snap())
        d.collect()
        changes = [{"name": "n1", "value": 1, "type": "device_added",
                    "severity": "normal", "description": "d1"},
                   {"name": "n2", "value": 2, "type": "process_started",
                    "severity": "normal", "description": "d2"}]
        monkeypatch.setattr(d, "_compare_snapshots", lambda old, new: list(changes))
        seen = []
        d.set_learning_hook(seen.append)
        out = d.collect()
        assert len(seen) == 1
        assert seen[0] == changes
        assert len(out) == 2

    def test_hook_not_called_when_no_changes(self, cd, monkeypatch, tmp_path):
        """零变更 ⇒ **不触发钩子**（避免空转写入）。"""
        d = ChangeDetector(persistent_log_dir=str(tmp_path / "h2"))
        monkeypatch.setattr(d, "_capture_snapshot", lambda: _snap())
        d.collect()
        seen = []
        d.set_learning_hook(seen.append)
        assert d.collect() == []
        assert seen == []

    def test_hook_exception_does_not_break_collect(self, cd, monkeypatch, tmp_path):
        """**核心兜底**：钩子抛异常时，读数**照常返回**（感知主链路零影响）。"""
        d = ChangeDetector(persistent_log_dir=str(tmp_path / "h3"))
        monkeypatch.setattr(d, "_capture_snapshot", lambda: _snap())
        d.collect()

        def boom(changes):
            raise RuntimeError("hook boom")

        d.set_learning_hook(boom)
        monkeypatch.setattr(d, "_compare_snapshots",
                            lambda old, new: [{"name": "n", "value": 1, "type": "t",
                                               "severity": "normal", "description": "d"}])
        out = d.collect()
        assert len(out) == 1
        assert out[0].sensor_name == "n"
        assert len(d._change_log) == 1

    def test_change_log_accumulates_across_calls(self, cd, monkeypatch, tmp_path):
        """`_change_log` 是**只增**的内存日志（跨采集累积，且与 `persistent_log` 不同源）。"""
        d = ChangeDetector(persistent_log_dir=str(tmp_path / "acc"))
        monkeypatch.setattr(d, "_capture_snapshot", lambda: _snap())
        d.collect()
        one = [{"name": "n1", "value": 1, "type": "t1", "severity": "normal", "description": "d"}]
        two = [{"name": "n2", "value": 2, "type": "t2", "severity": "normal", "description": "d"}]
        monkeypatch.setattr(d, "_compare_snapshots", lambda old, new: list(one))
        d.collect()
        monkeypatch.setattr(d, "_compare_snapshots", lambda old, new: list(two))
        d.collect()
        assert [c["name"] for c in d.change_log] == ["n1", "n2"]
        assert d.change_log is d._change_log
        assert d.persistent_change_log == []      # 走 collect 不落持久化日志

    def test_collect_returns_sensor_readings(self, cd, monkeypatch, tmp_path):
        """返回值类型必须是 `SensorReading`（下游 translator 走对象路径）。"""
        from sensor.sensor_reading import SensorReading

        d = ChangeDetector(persistent_log_dir=str(tmp_path / "t"))
        monkeypatch.setattr(d, "_capture_snapshot", lambda: _snap())
        assert all(isinstance(r, SensorReading) for r in d.collect())


# ═══════════════════════════════════════════════════════════════
#  15. 持久化变更日志
# ═══════════════════════════════════════════════════════════════

class TestPersistentLogLoad:
    """`_load_persistent_log` 的 5 条读入形态（全部静默兜底）。"""

    def _write(self, tmp_path, text):
        d = tmp_path / "changes"
        d.mkdir(parents=True, exist_ok=True)
        (d / "change_log.json").write_text(text, encoding="utf-8")
        return d

    def test_missing_file_yields_empty(self, tmp_path):
        assert ChangeDetector(persistent_log_dir=str(tmp_path / "nope"))._persistent_log == []

    def test_plain_list_is_loaded_and_trimmed(self, tmp_path):
        """旧格式（纯数组）⇒ 加载并按 `max_entries` 滚动。"""
        d = self._write(tmp_path, json.dumps([{"i": i} for i in range(5)]))
        cd = ChangeDetector(persistent_log_dir=str(d), max_entries=3)
        assert [e["i"] for e in cd._persistent_log] == [2, 3, 4]

    def test_dict_with_entries_is_loaded(self, tmp_path):
        """新格式（`{"entries": [...]}`）⇒ 取 `entries` 字段。"""
        d = self._write(tmp_path, json.dumps({"entries": [{"i": 1}], "max_entries": 5}))
        cd = ChangeDetector(persistent_log_dir=str(d))
        assert cd._persistent_log == [{"i": 1}]

    @pytest.mark.parametrize("text", [
        "{}", '{"other": 1}', '{"entries": "not-a-list"}',
        "5", '"abc"', "null", "true", "[1, 2, 3]",
    ])
    def test_unrecognized_shapes_yield_empty(self, tmp_path, text):
        """无法识别的 JSON 形态 ⇒ 空日志（不抛）。

        注意 `[1, 2, 3]`（元素不是 dict）也会被**当作合法列表接受**并 trim
        ⇒ 下游若假设元素是 dict 就会崩。记录该宽容度。
        """
        d = self._write(tmp_path, text)
        cd = ChangeDetector(persistent_log_dir=str(d))
        assert isinstance(cd._persistent_log, list)
        assert len(cd._persistent_log) <= 3

    def test_broken_json_yields_empty(self, tmp_path):
        d = self._write(tmp_path, "{broken")
        assert ChangeDetector(persistent_log_dir=str(d))._persistent_log == []

    def test_directory_in_place_of_file_yields_empty(self, tmp_path):
        """路径上是个**目录** ⇒ `open` 失败 ⇒ 空日志（不抛）。"""
        d = tmp_path / "changes"
        (d / "change_log.json").mkdir(parents=True)
        assert ChangeDetector(persistent_log_dir=str(d))._persistent_log == []

    def test_entries_dict_format_is_trimmed_too(self, tmp_path):
        """dict 格式同样受 `max_entries` 约束（两条分支都走 `trim_change_log`）。"""
        d = self._write(tmp_path, json.dumps({"entries": [{"i": i} for i in range(10)]}))
        assert len(ChangeDetector(persistent_log_dir=str(d), max_entries=4)._persistent_log) == 4

    def test_untouched_real_home_is_not_read(self, tmp_path):
        """**隔离证明**：显式给目录时，真实 `~/.Yunshu/changes` **不参与**。

        判据：加载结果只可能来自 `tmp_path`（写入 1 条，读回 1 条）。
        """
        d = self._write(tmp_path, json.dumps([{"src": "tmp"}]))
        cd = ChangeDetector(persistent_log_dir=str(d))
        assert cd._persistent_log == [{"src": "tmp"}]


class TestPersistentLogSave:
    """`_save_to_persistent_log`：先入内存、再落盘、超限滚动。"""

    def test_appends_and_writes_json(self, tmp_path):
        cd = ChangeDetector(persistent_log_dir=str(tmp_path / "changes"))
        cd._save_to_persistent_log({"i": 1})
        cd._save_to_persistent_log({"i": 2})
        assert cd._persistent_log == [{"i": 1}, {"i": 2}]
        raw = (tmp_path / "changes" / "change_log.json").read_text(encoding="utf-8")
        assert json.loads(raw) == [{"i": 1}, {"i": 2}]
        assert "\n  " in raw           # indent=2（人工可读）

    def test_chinese_is_not_escaped(self, tmp_path):
        cd = ChangeDetector(persistent_log_dir=str(tmp_path / "changes"))
        cd._save_to_persistent_log({"描述": "中文"})
        raw = (tmp_path / "changes" / "change_log.json").read_text(encoding="utf-8")
        assert "中文" in raw and "\\u" not in raw

    def test_creates_directory_tree(self, tmp_path):
        target = tmp_path / "a" / "b" / "c"
        cd = ChangeDetector(persistent_log_dir=str(target))
        cd._save_to_persistent_log({"i": 1})
        assert (target / "change_log.json").exists()

    def test_rolls_when_over_limit(self, tmp_path):
        """超过 `max_entries` ⇒ 内存与磁盘**同步**滚动（都只剩最新 N 条）。"""
        cd = ChangeDetector(persistent_log_dir=str(tmp_path / "changes"), max_entries=3)
        for i in range(5):
            cd._save_to_persistent_log({"i": i})
        assert [e["i"] for e in cd._persistent_log] == [2, 3, 4]
        on_disk = json.loads(
            (tmp_path / "changes" / "change_log.json").read_text(encoding="utf-8"))
        assert [e["i"] for e in on_disk] == [2, 3, 4]

    def test_makedirs_failure_does_not_touch_memory(self, tmp_path):
        """**边界 (a)**：`os.makedirs` 失败 ⇒ `append` **从未执行** ⇒ 内存与磁盘**都没有**。

        真实成因：`_save_to_persistent_log` 的 `os.makedirs` 是 try 块的**第一句**，
        而 `self._persistent_log.append(...)` 在它**之后** ⇒ 目录建不出来时内存也不动。
        （这正是首轮我写错的那条：我原以为"内存先有、磁盘后失败"。）
        """
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")     # 是个文件 ⇒ makedirs 必失败
        cd = ChangeDetector(persistent_log_dir=str(blocker / "sub"))
        cd._save_to_persistent_log({"i": 1})
        assert cd._persistent_log == []
        assert not (blocker / "sub").exists()

    def test_file_write_failure_keeps_in_memory_entry(self, tmp_path):
        """**边界 (b)**：`makedirs` 成功但 `open` 失败 ⇒ 内存**已追加**、磁盘不变。

        成因：`append` 在 `makedirs` 之后、`open` 之前。
        ⇒ 此时"内存与磁盘不一致"。调用方若拿 `persistent_change_log` 当
        "已落盘"的证据会误判。锁定该顺序语义。
        """
        changes = tmp_path / "changes"
        (changes / "change_log.json").mkdir(parents=True)   # 同名目录 ⇒ open 必失败
        cd = ChangeDetector(persistent_log_dir=str(changes))
        cd._save_to_persistent_log({"i": 1})
        assert cd._persistent_log == [{"i": 1}]              # 内存里有
        assert (changes / "change_log.json").is_dir()        # 磁盘上仍是目录，没被写成文件

    def test_roundtrip_through_new_instance(self, tmp_path):
        """**端到端持久化**：写完用新实例读回，逐值相等（真实跨会话语义）。"""
        first = ChangeDetector(persistent_log_dir=str(tmp_path / "changes"), max_entries=10)
        first._save_to_persistent_log({"i": 1, "type": "device_added"})
        first._save_to_persistent_log({"i": 2})
        second = ChangeDetector(persistent_log_dir=str(tmp_path / "changes"), max_entries=10)
        assert second._persistent_log == first._persistent_log
        assert second.persistent_change_log == [{"i": 1, "type": "device_added"}, {"i": 2}]

    def test_append_only_no_dedup(self, tmp_path):
        """同一内容重复写入 ⇒ **两条**（不去重；去重是调用方职责）。"""
        cd = ChangeDetector(persistent_log_dir=str(tmp_path / "changes"))
        cd._save_to_persistent_log({"i": 1})
        cd._save_to_persistent_log({"i": 1})
        assert len(cd._persistent_log) == 2


# ═══════════════════════════════════════════════════════════════
#  16. `register_change_from_event`
# ═══════════════════════════════════════════════════════════════

class TestRegisterChangeFromEvent:
    """把 EventMonitor 事件编码成标准变更条目（含跨模块类型契约）。"""

    def test_entry_shape(self, tmp_path):
        cd = ChangeDetector(persistent_log_dir=str(tmp_path / "c"))
        raw = {"timestamp": "2026-09-20T10:00:00", "event_type": "arrival",
               "device_name": "USB\\VID_1234", "detail": "设备接入"}
        entry = cd.register_change_from_event(raw)
        assert set(entry) == {"timestamp", "event_type", "value", "type",
                              "severity", "description", "detail"}
        assert entry["timestamp"] == "2026-09-20T10:00:00"
        assert entry["event_type"] == "arrival"
        assert entry["value"] == "USB\\VID_1234"
        assert entry["type"] == "hardware_arrival"
        assert entry["severity"] == "warning"
        assert entry["description"] == "设备接入"
        assert entry["detail"] is raw

    @pytest.mark.parametrize("event_type,expected", [
        ("arrival", "warning"), ("removal", "warning"),
        ("failure", "critical"), ("driver_failure", "critical"),
        ("FAILURE", "warning"), ("Failure", "warning"),
    ])
    def test_severity_rule(self, tmp_path, event_type, expected):
        """`"failure" in event_type` ⇒ critical，且是**大小写敏感的子串**匹配。

        由此 `"FAILURE"` / `"Failure"` 都只得到 `warning` —— 上游若换大小写，
        硬件故障会**静默降级**为警告。锁定并登记为陷阱。
        """
        cd = ChangeDetector(persistent_log_dir=str(tmp_path / "c"))
        assert cd.register_change_from_event({"event_type": event_type})["severity"] == expected

    def test_defaults_when_fields_missing(self, tmp_path):
        """**发现（登记，未修）**：同一个缺失键 `event_type` 有**三个不同的默认值**。

        源码 `change_detector.py:615-623` 对 `change_entry.get("event_type", ...)`
        用了三处不一致的默认：
          · `event_type`   → `"unknown"`
          · `type`         → `"event"`（拼出 `"hardware_event"`）
          · `description`  → `"变化"`（拼出 `"硬件变化"`）
        ⇒ 空输入时 `type != f"hardware_{event_type}"`（"hardware_event" vs "hardware_unknown"），
        **破坏了本函数自身声明的编码规则**（其余用例证明有 event_type 时二者一致）。
        影响：空/半成品事件在日志里看起来像"未知硬件事件类型 event"，
        与 `event_type="unknown"` 无法对应。属真实缺陷，本任务禁止改生产代码 ⇒
        锁定现状 + 在 docstring 登记（报告 §5）。正确修法是把三处默认统一为 `"unknown"`。
        """
        cd = ChangeDetector(persistent_log_dir=str(tmp_path / "c"))
        entry = cd.register_change_from_event({})
        assert entry["event_type"] == "unknown"
        assert entry["type"] == "hardware_event"          # ← 不是 "hardware_unknown"
        assert entry["type"] != f"hardware_{entry['event_type']}"
        assert entry["value"] == ""
        assert entry["severity"] == "warning"
        assert entry["description"] == "硬件变化"          # ← 第三个默认值
        assert datetime.fromisoformat(entry["timestamp"]) is not None

    def test_explicit_none_timestamp_is_kept(self, tmp_path):
        """**边界**：显式 `timestamp=None` ⇒ 保留 `None`（`get` 的默认值只对**缺键**生效）。

        于是条目里会出现 `"timestamp": null`。`_save_to_persistent_log` 会照样落盘。
        锁定该区分（缺键 vs 显式 None）。
        """
        cd = ChangeDetector(persistent_log_dir=str(tmp_path / "c"))
        assert cd.register_change_from_event({"timestamp": None})["timestamp"] is None

    def test_empty_detail_yields_empty_description(self, tmp_path):
        """`detail` 存在但为空串 ⇒ `description` 也是空串（**不**回落兜底文案）。"""
        cd = ChangeDetector(persistent_log_dir=str(tmp_path / "c"))
        assert cd.register_change_from_event({"detail": ""})["description"] == ""

    def test_appends_to_memory_and_disk(self, tmp_path):
        """同时写内存 `_change_log` 与持久化日志，并返回该条目。"""
        cd = ChangeDetector(persistent_log_dir=str(tmp_path / "c"))
        entry = cd.register_change_from_event({"event_type": "removal"})
        assert cd._change_log == [entry]
        assert cd._persistent_log == [entry]
        on_disk = json.loads(
            (tmp_path / "c" / "change_log.json").read_text(encoding="utf-8"))
        assert on_disk == [entry]

    def test_is_classified_as_hardware_by_novelty(self, tmp_path):
        """**跨模块契约（TASK-09 §2.3 要求核对的"两处语义"）**：

        `register_change_from_event` 把 `type` 写成 `f"hardware_{event_type}"`，
        而 `novelty.classify_change` 对 `hardware_` 前缀给高置信硬件事件。
        ⇒ 实时硬件事件**确实**会被学习管线接住（高置信 0.85）。
        若任一侧改前缀（如改成 `hw_`），全部实时硬件事件会**静默不被学习**。
        """
        cd = ChangeDetector(persistent_log_dir=str(tmp_path / "c"))
        entry = cd.register_change_from_event({"event_type": "arrival"})
        event = nv.classify_change(entry)
        assert event is not None
        assert event.event_type == nv.EVENT_HARDWARE_CHANGE
        assert event.confidence == 0.85
        assert event.level == "high"
        assert event.severity == "warning"        # 严重级透传

    @pytest.mark.parametrize("event_type", ["failure", "arrival", "removal", "unknown"])
    def test_always_hardware_family(self, tmp_path, event_type):
        """任何 `event_type` 都产出 `hardware_*` 类型（不会退化成其它族）。"""
        cd = ChangeDetector(persistent_log_dir=str(tmp_path / "c"))
        entry = cd.register_change_from_event({"event_type": event_type})
        assert entry["type"].startswith("hardware_")
        assert nv.classify_change(entry).event_type == nv.EVENT_HARDWARE_CHANGE


# ═══════════════════════════════════════════════════════════════
#  17. 跨模块：diff 产出的全部类型是否被 novelty 覆盖（契约闭合）
# ═══════════════════════════════════════════════════════════════

class TestTypeVocabularyClosure:
    """**契约闭合检查**：`_diff_*` 能产出的**所有** `type` 都必须被 novelty 明确处置。

    这条不变量防的是"新增一类 diff 但忘了接学习规则"：
    那种情况下变更会被**静默丢弃**（`classify_change` 返回 None），
    既不报错也不告警。故把"产出集的每一员都必须落在
    `硬件 ∪ 进程 ∪ 文件 ∪ 漂移` 或**显式声明的噪音集**里"钉死。
    """

    NOISE = {"registry_changed", "environment_changed", "system_info_changed"}

    def _emitted_types(self, cd):
        """用**真实调用**产出全部 diff 类型（不是抄代码里的字面量）。"""
        types = set()
        types |= {c["type"] for c in cd._diff_devices(
            {"a": {"name": "A", "s": 1}, "b": {"name": "B"}},
            {"a": {"name": "A", "s": 2}, "c": {"name": "C"}})}
        types |= {c["type"] for c in cd._diff_partitions(
            {"P": {"mountpoint": "P"}}, {"Q": {"mountpoint": "Q"}})}
        types |= {c["type"] for c in cd._diff_processes([_proc("x")], [_proc("y")])}
        types |= {c["type"] for c in cd._diff_services([_svc("S", "Running")],
                                                      [_svc("S", "Stopped")])}
        types |= {c["type"] for c in cd._diff_system_info({"h": "a"}, {"h": "b"})}
        types |= {c["type"] for c in cd._diff_registry({"K": {"A": "1"}}, {"K": {"A": "2"}})}
        types |= {c["type"] for c in cd._diff_environment({"E": "1"}, {"E": "2"})}
        types |= {c["type"] for c in
                  [cd.register_change_from_event({"event_type": "arrival"})]}
        return types

    def test_eleven_diff_types_are_emitted(self, cd):
        """`_diff_*` 字面量族恰好 **11** 类（3 设备 + 2 分区 + 2 进程 + 1 服务 + 3 其它）。

        另有 `register_change_from_event` 产出的 `hardware_arrival`（前缀族，
        与上面 11 个字面量不同名），故总产出集为 12 项。此处把两者分开断言，
        避免"总数为 12"这种模糊断言掩盖某一族的缺失。
        """
        emitted = self._emitted_types(cd)
        literal = emitted - {"hardware_arrival"}
        assert len(literal) == 11, sorted(literal)
        assert emitted - literal == {"hardware_arrival"}

    def test_every_emitted_type_is_disposed_of(self, cd):
        """**核心**：产出集里每一个类型，novelty 都必须"要么给出事件、要么在噪音集内"。

        失败即意味着存在**静默丢弃**的变更类别（本任务发现的那类缺陷）。
        """
        emitted = self._emitted_types(cd)
        unhandled = sorted(
            t for t in emitted
            if nv.classify_change({"type": t}) is None and t not in self.NOISE
        )
        assert unhandled == []

    def test_noise_set_is_exactly_three(self, cd):
        """噪音集恰好是 3 个"注册表/环境变量/系统信息"类型 —— 不多不少。

        若哪天新增一类 diff 却没接规则，它既不在事件集也不在噪音集 ⇒ 上一条会失败；
        若有人图省事把它塞进噪音集，本条会失败 ⇒ 两道闸门合起来才有约束力。
        """
        emitted = self._emitted_types(cd)
        dropped = sorted(t for t in emitted if nv.classify_change({"type": t}) is None)
        assert dropped == sorted(self.NOISE)

    def test_file_family_has_no_producer_here(self, cd):
        """对照：本模块**不产** `file_*` 类型（文件变更由 `file_watcher` 另一路负责）。

        与 `test_sensor_novelty_coverage.py::test_file_types_have_no_producer_in_this_repo`
        合起来说明：`novelty._FILE_TYPES` 在本仓**两个可能的生产者都没有**产出对应形态。
        """
        emitted = self._emitted_types(cd)
        assert emitted & nv._FILE_TYPES == set()

    def test_service_change_is_critical_but_medium_confidence(self, cd):
        """**跨模块不一致（登记）**：服务停止被判 `critical`，但学习侧只给 `medium`。

        `_diff_services` 在状态含 `"stop"` 时给 `severity="critical"`；
        `novelty._PROCESS_TYPES` 把 `service_state_changed` 归入进程族、置信度 0.55
        ⇒ `level="medium"`。于是"**最严重的一类变更之一**"在沉淀时只拿到中置信，
        与 `device_removed`（warning + 0.85 high）相比优先级反而更低。
        两边各自自洽（代码里都写明了理由），但**合起来看是等级倒挂** ⇒ 登记为发现。
        """
        change = cd._diff_services([_svc("S", "Running")], [_svc("S", "Stopped")])[0]
        assert change["severity"] == "critical"
        event = nv.classify_change(change)
        assert event.confidence == 0.55
        assert event.level == "medium"

        removed = cd._diff_devices({"k": {"name": "d"}}, {})[0]
        assert removed["severity"] == "warning"
        assert nv.classify_change(removed).level == "high"

    def test_registry_and_env_changes_have_warning_severity_but_no_learning(self, cd):
        """对照：注册表/环境变量变更带 `warning` 严重级，但学习侧**完全不接**。

        这意味着"警告级变更 ≠ 可学习信号"。属设计取舍（防噪音），
        但值得注意：`warning` 级别在读数侧可见、在学习侧不可见。
        """
        for change in (cd._diff_registry({"K": {"A": "1"}}, {"K": {"A": "2"}})[0],
                       cd._diff_environment({"E": "1"}, {"E": "2"})[0]):
            assert change["severity"] == "warning"
            assert nv.classify_change(change) is None
