"""感知文件监听的 watch_dirs 解析（env > config.yaml sensor.watch_dirs > 代码默认）。

【为什么要有这个模块（2026-11-12 核实）】
`config.py` 的 `Config` **从不解析 config.yaml**（取值链只有 DEFAULT → 环境变量 →
overrides），而 `LifecycleManager._initialize_core_systems` 恰恰是用
`self._config.get("sensor", {})` 取 `watch_dirs` 的 ⇒ **在 config.yaml 里写
sensor.watch_dirs 永远不会生效**，FileWatcher 永远不会启动（这正是"感知侧学习开了
但近乎空转"的直接原因之一）。修法沿用本仓既有惯例（`agent/learning/novelty_hooks.py`、
`agent/retention/policy.py`、`agent/monitoring/slo_report_scheduler.py` 同款）：
**消费方直读 config.yaml**，并让 env 优先。

【纪律（别在这层破）】
- **纯函数 + 只读**：不建目录、不起线程、不写盘、不 import agent.*（叶子模块，防循环依赖）。
- 监听目录**不得**包含 `data/learning/`：学习产物（记忆/审计/建议草稿）写在那里，
  把它纳入监听 = 自己写自己收 = 自激（每轮感知都给自己造一条"文件变更"）。
- 仓库根目录本身也不许监听（递归全仓会淹没事件缓冲区，且必然自激）。
- 目录不存在**保留**并交给 `FileWatcher` 记 warning 跳过：静默丢弃会让人以为"配了没生效"。
- 默认值只列"外部输入面"：`knowledge/`（知识库，人/吞噬管线写）与 `data/inbox/`
  （人工投递箱）。**不要**把 `data/` 整个塞进来——那是日志/数据库/运行期状态的高频写入面。

配置路径：`sensor.watch_dirs`（config.yaml；可写成列表或逗号/分号分隔字符串）
环境变量：`SENSOR_WATCH_DIRS`（逗号或分号分隔，覆盖 config.yaml）
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

#: 仓库根（本文件位于 <repo>/agent/ 下）
REPO_ROOT = Path(__file__).resolve().parent.parent

#: 覆盖用环境变量（已进 agent/settings/registry.py，零缺口门）
ENV_WATCH_DIRS = "SENSOR_WATCH_DIRS"

#: 默认监听面：只放"外部输入"，不放运行期高频写入面（见模块 docstring）
DEFAULT_WATCH_DIRS: Tuple[str, ...] = ("knowledge", "data/inbox")

#: 禁止纳入监听的子树（相对仓库根）：学习产物目录，监听它必自激
FORBIDDEN_SUBDIRS: Tuple[str, ...] = ("data/learning",)

#: 目录分隔符：Windows 习惯分号、运维脚本习惯逗号，两种都认
_DIR_SEPARATORS = (",", ";")


def _split_dirs(raw: Any) -> List[str]:
    """把"列表 / 字符串"归一成去空白后的目录清单（不解析、不校验）。"""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        items: Iterable[Any] = raw
    else:
        text = str(raw)
        for sep in _DIR_SEPARATORS[1:]:
            text = text.replace(sep, _DIR_SEPARATORS[0])
        items = text.split(_DIR_SEPARATORS[0])
    out: List[str] = []
    for item in items:
        text = str(item).strip().strip('"').strip("'")
        if text:
            out.append(text)
    return out


def _config_yaml_watch_dirs(repo_root: Path) -> List[str]:
    """读 config.yaml 的 sensor.watch_dirs（失败/缺失返回空；不抛异常）。"""
    cfg_path = repo_root / "config.yaml"
    if not cfg_path.exists():
        return []
    try:
        import yaml as _yaml  # 延迟导入：本模块在启动早期被 lifecycle 调用
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = _yaml.safe_load(f) or {}
    except Exception as exc:  # noqa: BLE001 配置解析失败按"没配"处理
        logger.warning("[SensorConfig] config.yaml 解析失败（%s），watch_dirs 回退默认",
                       type(exc).__name__)
        return []
    if not isinstance(cfg, dict):
        return []
    sensor = cfg.get("sensor")
    if not isinstance(sensor, dict):
        return []
    return _split_dirs(sensor.get("watch_dirs"))


def _normalize(raw_dir: str, repo_root: Path) -> str:
    """相对路径按仓库根展开；~ 展开；统一成绝对路径字符串（不要求已存在）。"""
    path = Path(raw_dir).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    try:
        path = path.resolve()
    except OSError:  # pragma: no cover - resolve 失败（极端路径）按字面值
        path = Path(os.path.abspath(str(path)))
    return str(path)


def _is_forbidden(path: str, repo_root: Path) -> Optional[str]:
    """命中禁止面时返回原因，否则 None（判断用 resolve 后的真实路径）。"""
    try:
        target = Path(path).resolve()
        root = repo_root.resolve()
    except OSError:  # pragma: no cover
        return None
    if target == root:
        return "仓库根目录（递归全仓会淹没事件缓冲区）"
    for rel in FORBIDDEN_SUBDIRS:
        forbidden = (root / rel).resolve()
        if target == forbidden or forbidden in target.parents:
            return "学习产物目录 %s（监听它会自激）" % rel
    return None


def resolve_watch_dirs(cfg_sensor: Optional[dict] = None, *,
                       repo_root: Optional[Path] = None) -> List[str]:
    """算出最终监听目录（去重 + 绝对路径 + 剔除禁止面）。

    优先级：`SENSOR_WATCH_DIRS` > `cfg_sensor["watch_dirs"]`（Config overrides）
    > `config.yaml: sensor.watch_dirs` > `DEFAULT_WATCH_DIRS`。

    Args:
        cfg_sensor: `Config().get("sensor", {})` 的结果（Config 不读 YAML，通常只有默认 None；
            保留这一层是为了让 `Config(overrides=...)` 注入仍然有效）。
        repo_root: 仓库根（测试传 tmp 路径；默认本仓根）。

    Returns:
        绝对路径字符串列表（保持输入顺序、大小写不敏感去重）。
    """
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    source = "默认值"
    raw = _split_dirs(os.environ.get(ENV_WATCH_DIRS))
    if raw:
        source = "环境变量 %s" % ENV_WATCH_DIRS
    if not raw and isinstance(cfg_sensor, dict):
        raw = _split_dirs(cfg_sensor.get("watch_dirs"))
        if raw:
            source = "Config overrides"
    if not raw:
        raw = _config_yaml_watch_dirs(root)
        if raw:
            source = "config.yaml: sensor.watch_dirs"
    if not raw:
        raw = list(DEFAULT_WATCH_DIRS)

    resolved: List[str] = []
    seen = set()
    dropped: List[str] = []
    for item in raw:
        path = _normalize(item, root)
        reason = _is_forbidden(path, root)
        if reason:
            dropped.append("%s（%s）" % (path, reason))
            continue
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)

    if dropped:
        logger.warning("[SensorConfig] 已剔除 %d 个禁止的监听目录：%s",
                       len(dropped), "；".join(dropped))
    logger.info("[SensorConfig] watch_dirs 来源=%s，生效 %d 个：%s",
                source, len(resolved), ", ".join(resolved) or "（无）")
    return resolved


__all__: Sequence[str] = [
    "ENV_WATCH_DIRS", "DEFAULT_WATCH_DIRS", "FORBIDDEN_SUBDIRS",
    "resolve_watch_dirs",
]
