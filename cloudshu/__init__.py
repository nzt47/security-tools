"""云枢能力层 CLI 包（`python -m cloudshu`）

见 `cloudshu/cli.py` 的模块文档：本包交付 v1.4 战略判据的"人"那一半 ——
**不经过 agent loop、不依赖 LLM** 的能力入口。

⚠️ 本包**必须**登记在 `pyproject.toml` 的
`[tool.setuptools.packages.find] where = [...]`，
否则 `pip install -e .` 之后 `python -m cloudshu` 会 `ModuleNotFoundError`
（`TASK-05` §3 第 4 步第 3 项明确提醒的陷阱）。
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
