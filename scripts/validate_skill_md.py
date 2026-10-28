#!/usr/bin/env python
"""skill.md 形状门禁（文件轨体检）—— error 必须为 0，warning 只提示。

【为什么是脚本而不是只靠用例】交接单 §11.15 的结论：文件轨 36 份 skill.md 的格式
完全由导入通道决定，解析器对坏 front matter 是宽容的（`---# 标题` 会让整段元数据
被静默跳过），而 CI 里没有任何一处会因此变红。这里把判定做成可单独跑的体检：

    python scripts/validate_skill_md.py                # 人类可读
    python scripts/validate_skill_md.py --json         # 机器可读
    python scripts/validate_skill_md.py --fail-on-warn # 把 warning 也当失败（收敛用）

退出码：0 无 error / 1 有 error（或 --fail-on-warn 时有 warning）/ 2 参数或路径错误。
判定实现只有一处：agent/skills_mgmt/skill_md_schema.py（用例与本脚本共用）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.skills_mgmt.skill_md_schema import scan_root  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILLS_REPO = ROOT / "data" / "skills_repo"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="skill.md 形状门禁（必填 9 键 + 结构小节）")
    parser.add_argument("--root", default="", help="文件轨根（默认 data/skills_repo）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--fail-on-warn", action="store_true", help="warning 也当失败")
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else DEFAULT_SKILLS_REPO
    if not root.is_dir():
        print("目录不存在：%s" % root)
        return 2
    report = scan_root(root)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print("文件轨 %s：%d 份 · error %d · 带 warning %d"
              % (root, report["checked"], report["error_count"], report["warning_count"]))
        for item in report["items"]:
            if item["errors"]:
                print("[ERROR] %s" % item["skill_id"])
                for err in item["errors"]:
                    print("    - %s" % err)
        for item in report["items"]:
            if item["warnings"]:
                print("[warn] %s：%s" % (item["skill_id"], "；".join(item["warnings"])))
        if not report["error_count"]:
            print("[OK] 0 error（必填 9 键齐全且 id 与目录名一致）")
        if report["error_count"]:
            print("修法：补 front matter 的必填键；id 必须等于目录名；分隔符必须独占一行")

    if report["error_count"]:
        return 1
    if args.fail_on_warn and report["warning_count"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
