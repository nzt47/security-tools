"""JS 插件包适配器（P1）：DSH / ClawHub 等 JS 插件包**只抽取不执行**

本适配器只认「有插件标记」的目录：`package.json` + (`plugin.json` /
`commands/` / `skills/` / package.json 里的 openclaw|dsh|claude 键)。普通的
Node 项目不会被误认。

抽取内容仅限：package 元数据、README、`commands/` 与 `skills/` 的**文件名清单**。
不 import、不运行、不读取 `node_modules`。产物一律 L2（需 LLM 重写），
因为 JS 插件包不能直接作为云枢技能执行。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .base import (
    Adapter,
    ExtractedAsset,
    first_paragraph,
    read_json_file,
    read_text_file,
)


def _list_names(directory: Path, *, suffixes: tuple, max_items: int = 20) -> List[str]:
    if not directory.is_dir():
        return []
    out: List[str] = []
    try:
        for child in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
            if len(out) >= max_items:
                break
            if child.name.startswith(".") or child.name == "node_modules":
                continue
            if child.is_dir():
                out.append(child.name + "/")
            elif child.is_file() and child.suffix.lower() in suffixes:
                out.append(child.name)
    except OSError:
        pass
    return out


class JsPluginAdapter(Adapter):
    name = "js_plugin"
    priority = 25

    def _inspect(self, path: Path) -> Dict[str, Any]:
        pkg_path = path / "package.json"
        if not pkg_path.is_file():
            return {}
        data, warnings = read_json_file(pkg_path)
        if not isinstance(data, dict):
            return {"warnings": warnings or ["package.json 不是对象"]}
        markers: List[str] = []
        if (path / "plugin.json").is_file():
            markers.append("plugin.json")
        if _list_names(path / "commands", suffixes=(".md", ".js", ".ts", ".py", ".sh")):
            markers.append("commands/")
        if _list_names(path / "skills", suffixes=(".md", ".json")):
            markers.append("skills/")
        for key in ("openclaw", "dsh", "claude", "contributes"):
            if key in data:
                markers.append("package.json:" + key)
        return {"package": data, "warnings": warnings, "markers": markers}

    def sniff(self, source: Any) -> int:
        if getattr(source, "kind", "") != "path":
            return 0
        path = Path(source.path)
        if not path.is_dir():
            return 0
        info = self._inspect(path)
        if not info.get("markers"):
            return 0
        score = 55
        package = info.get("package") or {}
        if package.get("name"):
            score += 5
        if package.get("description"):
            score += 5
        if "plugin.json" in info["markers"]:
            score += 5
        return min(score, 70)

    def extract(self, source: Any) -> ExtractedAsset:
        path = Path(source.path)
        info = self._inspect(path)
        package: Dict[str, Any] = info.get("package") or {}
        warnings = list(info.get("warnings") or [])
        warnings.append("JS 插件包不能执行：只抽取 README/commands/skills 清单，产物需 LLM 重写")

        readme = ""
        for name in ("README.md", "readme.md", "README", "README.txt"):
            candidate = path / name
            if candidate.is_file():
                readme, warns, _ = read_text_file(candidate, max_bytes=64 * 1024)
                warnings.extend(warns)
                break
        description = str(package.get("description") or "").strip()
        if not description:
            description = first_paragraph(readme, limit=300)

        commands = _list_names(path / "commands", suffixes=(".md", ".js", ".ts", ".py", ".sh"))
        skills = _list_names(path / "skills", suffixes=(".md", ".json"))
        bin_map = package.get("bin")
        tools = sorted(bin_map) if isinstance(bin_map, dict) else []
        if not tools and isinstance(package.get("keywords"), list):
            tools = [str(k) for k in package["keywords"][:10]]

        lines = ["# " + str(package.get("name") or path.name), ""]
        if description:
            lines.extend([description, ""])
        lines.extend([
            "> 来源：JS 插件包（只抽取，不执行）。以下清单来自 package.json / 目录名，",
            "> 不是云枢能力声明；提交前需 LLM 重写成 markdown 技能。",
            "",
        ])
        if commands:
            lines.extend(["## Commands", ""] + [f"- `{name}`" for name in commands] + [""])
        if skills:
            lines.extend(["## Skills", ""] + [f"- `{name}`" for name in skills] + [""])
        if tools:
            lines.extend(["## Tools / bin", ""] + [f"- `{name}`" for name in tools] + [""])
        if readme.strip():
            lines.extend(["## README", "", readme.strip()])
        body = "\n".join(lines).strip() + "\n"

        return ExtractedAsset(
            adapter=self.name,
            source_kind="directory",
            body=body,
            warnings=warnings,
            canonical_layout=False,
            primary_file="package.json",
            meta={
                "js_plugin": True,
                "package": {
                    key: package.get(key)
                    for key in ("name", "version", "description", "main")
                    if package.get(key) is not None
                },
                "markers": list(info.get("markers") or []),
                "commands": commands,
                "skills": skills,
                "tools": tools,
            },
        )
