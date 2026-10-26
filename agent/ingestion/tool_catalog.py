"""工具目录：把 data/tool_definitions/*.yaml 读成可给推荐/LLM 用的紧凑清单。

为什么不用 agent.tools.list_tools()：那个注册表只在 app_server / 插件加载后才有人；
service.recommend() 也会在 CLI、单测、草稿评审里跑，此时注册表是空的（实测 0 条）。
而 data/tool_definitions/ 是落盘真相（91 个 YAML，含 description / schema），
读它既不依赖运行期注册，也能给 LLM 提供参数说明。

边界：只读；不注册、不改任何工具。
"""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

DEFAULT_TOOL_DIR = Path(__file__).resolve().parents[2] / "data" / "tool_definitions"

#: 单个工具描述进 prompt 时的截断长度（91 个工具 × 120 字符 ≈ 11KB，够用又不爆预算）
_DESC_LIMIT = 120


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str
    params: Tuple[str, ...] = ()
    required: Tuple[str, ...] = ()
    category: str = ""

    def compact(self, limit: int = _DESC_LIMIT) -> str:
        desc = re.sub(r"\s+", " ", self.description or "").strip()
        if len(desc) > limit:
            desc = desc[:limit].rstrip() + "…"
        params = f" params={chr(44).join(self.params)}" if self.params else ""
        req = f" required={chr(44).join(self.required)}" if self.required else ""
        return f"- {self.name}: {desc}{params}{req}"


def _parse_one(path: Path) -> Optional[ToolInfo]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    name = str(data.get("name") or path.stem).strip()
    if not name:
        return None
    schema = data.get("schema") if isinstance(data.get("schema"), dict) else {}
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    return ToolInfo(
        name=name,
        description=str(data.get("description") or ""),
        params=tuple(str(k) for k in props.keys()),
        required=tuple(str(x) for x in required),
        category=str(data.get("category") or ""),
    )


@functools.lru_cache(maxsize=8)
def load_catalog(tool_dir: Optional[str] = None) -> Tuple[ToolInfo, ...]:
    """读取工具目录（按目录缓存；工具定义是静态文件）。"""
    base = Path(tool_dir) if tool_dir else DEFAULT_TOOL_DIR
    if not base.is_dir():
        return ()
    items: List[ToolInfo] = []
    for path in sorted(base.glob("*.yaml")):
        info = _parse_one(path)
        if info is not None:
            items.append(info)
    items.sort(key=lambda t: t.name)
    return tuple(items)


def tool_names(tool_dir: Optional[str] = None) -> frozenset:
    return frozenset(t.name for t in load_catalog(tool_dir))


@functools.lru_cache(maxsize=8)
def _name_regex(names: Tuple[str, ...]) -> re.Pattern:
    """一次性编译的"是否提到某个工具名"正则（前后不允许字母数字下划线）。"""
    if not names:
        return re.compile(r"(?!)")  # 永不匹配
    alt = "|".join(sorted((re.escape(n) for n in names), key=len, reverse=True))
    return re.compile(r"(?<![A-Za-z0-9_])(?:%s)(?![A-Za-z0-9_])" % alt, re.IGNORECASE)


def steps_referencing_tools(steps: List[str], names: Optional[frozenset] = None) -> int:
    """有多少个步骤的正文里出现了真实工具名（推荐 workflow 的证据）。

    保守判据：只认 data/tool_definitions 里的名字（snake_case），不做语义猜测。
    误判成 skill 的代价远小于误判成 workflow（后者会生成无法执行的骨架）。
    """
    names = names if names is not None else tool_names()
    if not names:
        return 0
    rx = _name_regex(tuple(sorted(names)))
    return sum(1 for s in steps if rx.search(str(s or "")))


def catalog_prompt(tool_dir: Optional[str] = None, *, limit: int = _DESC_LIMIT) -> str:
    return "\n".join(t.compact(limit) for t in load_catalog(tool_dir))
