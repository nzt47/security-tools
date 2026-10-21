"""Trae 适配器（P1；实现见 `clawhub.py`）。

Trae（含 trae-cn）的技能目录格式实测与 ClawHub 相同：SKILL.md + _meta.json +
skill-card.md；front matter 没有 Trae 独有键，因此识别依据是**默认扫描位置**
（`~/.trae*/skills`）。这与「生态目录只作为默认扫描位置提示」的约定一致。
"""

from __future__ import annotations

from .clawhub import TraeAdapter

__all__ = ["TraeAdapter"]
