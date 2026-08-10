"""light_loader 环境兼容性自动校验（CI 用）。

依据 docs/wiki/Home.md 的版本兼容矩阵（light-loader v0.1.0）逐项检测当前
Python 环境是否满足运行要求：

| 依赖项 | 最低版本 | 检测方式 |
|---|---|---|
| Python | 3.10 | sys.version_info |
| PyYAML | 6.0 | importlib.metadata.version("PyYAML") |
| libyaml（C 扩展） | 可选 | yaml.CSafeLoader 是否存在 |

退出码契约：0 = 全部满足（可选项缺失仅提示不失败）；1 = 必选项不满足
（CI 在此失败）。--json 输出结构化结果供 CI 解析。

用法：
    python scripts/dev/check_light_loader_compat.py [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from importlib import metadata

# 兼容矩阵（与 docs/wiki/Home.md §二 保持一致，改需三处同步）
MIN_PYTHON = (3, 10)
MIN_PYYAML = (6, 0)


def _pyaml_version() -> tuple[int, ...] | None:
    try:
        return tuple(int(p) for p in metadata.version("PyYAML").split("."))
    except metadata.PackageNotFoundError:
        return None


def check() -> dict:
    """逐项检测，返回结构化结果（含必需/可选项判定 + 详细错误原因）。

    JSON 契约（向后兼容，只增字段）：
      compatible: bool   — 整体是否满足（必需项全过）
      summary: str       — 一行结论（CI 可直接展示）
      errors: list[str]  — 所有不满足的必需项原因（CI 拼 PR 评论用）
      items: [{name, required, min, current, ok, reason}]
    """
    py_cur = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_min = ".".join(str(v) for v in MIN_PYTHON)
    py_ok = sys.version_info >= MIN_PYTHON
    pyaml_ver = _pyaml_version()
    pyaml_ok = pyaml_ver is not None and pyaml_ver >= MIN_PYYAML
    # libyaml C 扩展为可选项：仅检测并提示，不判定失败
    try:
        import yaml
        c_safe = getattr(yaml, "CSafeLoader", None) is not None
    except ImportError:  # PyYAML 缺失时（必选项已判失败）此处仅记录
        c_safe = False

    items = [
        {"name": "Python", "required": True,
         "min": py_min, "current": py_cur, "ok": py_ok,
         "reason": "" if py_ok else f"版本过低：{py_cur} < {py_min}，需升级 Python {py_min}+"},
        {"name": "PyYAML", "required": True,
         "min": ".".join(str(v) for v in MIN_PYYAML),
         "current": ".".join(str(v) for v in pyaml_ver) if pyaml_ver else "未安装",
         "ok": pyaml_ok,
         "reason": "" if pyaml_ok else (
             "未安装" if pyaml_ver is None else
             f"版本过低：{'.'.join(str(v) for v in pyaml_ver)} < "
             f"{'.'.join(str(v) for v in MIN_PYYAML)}，需升级 PyYAML {'.'.join(str(v) for v in MIN_PYYAML)}+")},
        {"name": "libyaml (C 扩展)", "required": False,
         "min": "可选（加速 ~7.6x）",
         "current": "可用" if c_safe else "不可用（自动回退 SafeLoader）",
         "ok": True,  # 可选项永不判失败
         "reason": "" if c_safe else "libyaml C 扩展缺失，已回退纯 Python SafeLoader（性能约降 7.6x，建议安装，非阻断）"},
    ]
    errors = [f"[{it['name']}] {it['reason']}" for it in items
              if not it["ok"] and it["required"]]
    ok = not errors
    summary = ("环境满足运行要求" if ok
               else f"环境不兼容：{len(errors)} 项必需项不满足")
    return {"compatible": ok, "summary": summary, "errors": errors, "items": items}


def main() -> int:
    parser = argparse.ArgumentParser(description="light_loader 环境兼容性校验")
    parser.add_argument("--json", action="store_true", help="输出 JSON 结果")
    args = parser.parse_args()

    result = check()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("light_loader 环境兼容性矩阵（light-loader v0.1.0）")
        print("=" * 56)
        for it in result["items"]:
            flag = "✓" if it["ok"] else "✗"
            req = "必需" if it["required"] else "可选"
            reason = f"  ← {it['reason']}" if it["reason"] else ""
            print(f"{flag} [{req}] {it['name']:<22} 最低={it['min']:<14} 当前={it['current']}{reason}")
        print("=" * 56)
        print("结论:", result["summary"])
    return 0 if result["compatible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
