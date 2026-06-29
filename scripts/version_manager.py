"""
云枢智能体 - 语义化版本管理工具
支持 SemVer 规范，提供版本号解析、递增、验证等功能
"""

import re
import json
import os
from datetime import datetime
from typing import Optional, Tuple, Dict, Any


class SemanticVersion:
    """语义化版本号类"""

    SEMVER_REGEX = re.compile(
        r"^(?P<major>0|[1-9]\d*)"
        r"\.(?P<minor>0|[1-9]\d*)"
        r"\.(?P<patch>0|[1-9]\d*)"
        r"(?:-(?P<prerelease>"
        r"(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
        r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*"
        r"))?"
        r"(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
    )

    def __init__(self, version_str: str):
        """初始化版本号"""
        match = self.SEMVER_REGEX.match(version_str)
        if not match:
            raise ValueError(f"无效的版本号格式: {version_str}")

        self.major = int(match.group("major"))
        self.minor = int(match.group("minor"))
        self.patch = int(match.group("patch"))
        self.prerelease = match.group("prerelease") or ""
        self.buildmetadata = match.group("buildmetadata") or ""

    def to_string(self) -> str:
        """转换为字符串"""
        version = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            version += f"-{self.prerelease}"
        if self.buildmetadata:
            version += f"+{self.buildmetadata}"
        return version

    def __str__(self) -> str:
        return self.to_string()

    def __repr__(self) -> str:
        return f"SemanticVersion('{self.to_string()}')"

    def _compare_tuple(self) -> Tuple[int, int, int, int, str, str]:
        """获取用于比较的元组"""
        prerelease_order = 0 if self.prerelease else 1
        return (
            self.major,
            self.minor,
            self.patch,
            prerelease_order,
            self.prerelease,
            self.buildmetadata,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return self._compare_tuple() == other._compare_tuple()

    def __lt__(self, other: "SemanticVersion") -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return self._compare_tuple() < other._compare_tuple()

    def __le__(self, other: "SemanticVersion") -> bool:
        return self == other or self < other

    def __gt__(self, other: "SemanticVersion") -> bool:
        return not self <= other

    def __ge__(self, other: "SemanticVersion") -> bool:
        return not self < other

    def bump_major(self) -> "SemanticVersion":
        """递增主版本号"""
        new_version = SemanticVersion(self.to_string())
        new_version.major += 1
        new_version.minor = 0
        new_version.patch = 0
        new_version.prerelease = ""
        new_version.buildmetadata = ""
        return new_version

    def bump_minor(self) -> "SemanticVersion":
        """递增次版本号"""
        new_version = SemanticVersion(self.to_string())
        new_version.minor += 1
        new_version.patch = 0
        new_version.prerelease = ""
        new_version.buildmetadata = ""
        return new_version

    def bump_patch(self) -> "SemanticVersion":
        """递增修订号"""
        new_version = SemanticVersion(self.to_string())
        new_version.patch += 1
        new_version.prerelease = ""
        new_version.buildmetadata = ""
        return new_version

    def bump_prerelease(self, identifier: str = "alpha") -> "SemanticVersion":
        """递增预发布版本"""
        new_version = SemanticVersion(self.to_string())

        if not new_version.prerelease:
            new_version.prerelease = f"{identifier}.1"
        else:
            parts = new_version.prerelease.split(".")
            if len(parts) >= 2 and parts[-1].isdigit():
                parts[-1] = str(int(parts[-1]) + 1)
                new_version.prerelease = ".".join(parts)
            else:
                new_version.prerelease = f"{new_version.prerelease}.1"

        return new_version

    def set_prerelease(self, prerelease: str) -> "SemanticVersion":
        """设置预发布标识"""
        new_version = SemanticVersion(self.to_string())
        new_version.prerelease = prerelease
        return new_version

    def set_buildmetadata(self, buildmetadata: str) -> "SemanticVersion":
        """设置构建元数据"""
        new_version = SemanticVersion(self.to_string())
        new_version.buildmetadata = buildmetadata
        return new_version

    def is_prerelease(self) -> bool:
        """是否为预发布版本"""
        return bool(self.prerelease)

    @staticmethod
    def is_valid(version_str: str) -> bool:
        """验证版本号是否有效"""
        return bool(SemanticVersion.SEMVER_REGEX.match(version_str))


class VersionManager:
    """版本管理器"""

    VERSION_FILE = "VERSION"
    VERSION_HISTORY_FILE = ".backups/versions/version_history.json"

    def __init__(self, base_dir: str = "."):
        self.base_dir = base_dir
        self.version_file = os.path.join(base_dir, self.VERSION_FILE)
        self.history_file = os.path.join(base_dir, self.VERSION_HISTORY_FILE)

    def get_current_version(self) -> SemanticVersion:
        """获取当前版本"""
        if not os.path.exists(self.version_file):
            return SemanticVersion("0.1.0")

        with open(self.version_file, "r", encoding="utf-8") as f:
            version_str = f.read().strip()

        return SemanticVersion(version_str)

    def set_version(self, version: SemanticVersion, reason: str = "") -> SemanticVersion:
        """设置版本号"""
        os.makedirs(os.path.dirname(self.version_file), exist_ok=True)

        with open(self.version_file, "w", encoding="utf-8") as f:
            f.write(version.to_string() + "\n")

        self._record_version_change(version, reason)

        return version

    def bump_version(self, bump_type: str = "patch", reason: str = "") -> SemanticVersion:
        """递增版本号"""
        current = self.get_current_version()

        if bump_type == "major":
            new_version = current.bump_major()
        elif bump_type == "minor":
            new_version = current.bump_minor()
        elif bump_type == "patch":
            new_version = current.bump_patch()
        elif bump_type == "prerelease":
            new_version = current.bump_prerelease()
        else:
            raise ValueError(f"不支持的版本递增类型: {bump_type}")

        return self.set_version(new_version, reason)

    def _record_version_change(self, version: SemanticVersion, reason: str):
        """记录版本变更历史"""
        os.makedirs(os.path.dirname(self.history_file), exist_ok=True)

        history = []
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except (json.JSONDecodeError, IOError):
                history = []

        history.append(
            {
                "version": version.to_string(),
                "timestamp": datetime.now().isoformat(),
                "reason": reason,
                "user": os.environ.get("USER", "unknown"),
            }
        )

        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)

    def get_version_history(self, limit: int = 10) -> list:
        """获取版本历史"""
        if not os.path.exists(self.history_file):
            return []

        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                history = json.load(f)
            return history[-limit:]
        except (json.JSONDecodeError, IOError):
            return []


def main():
    """命令行入口"""
    import sys

    if len(sys.argv) < 2:
        print("用法:")
        print("  python version_manager.py current        # 显示当前版本")
        print("  python version_manager.py bump <type>   # 递增版本 (major/minor/patch/prerelease)")
        print("  python version_manager.py set <version> # 设置版本")
        print("  python version_manager.py validate <v>  # 验证版本号")
        print("  python version_manager.py history       # 版本历史")
        sys.exit(1)

    command = sys.argv[1]
    manager = VersionManager()

    if command == "current":
        version = manager.get_current_version()
        print(f"当前版本: {version}")
    elif command == "bump" and len(sys.argv) >= 3:
        bump_type = sys.argv[2]
        reason = sys.argv[3] if len(sys.argv) >= 4 else ""
        new_version = manager.bump_version(bump_type, reason)
        print(f"版本已更新: {new_version}")
    elif command == "set" and len(sys.argv) >= 3:
        version_str = sys.argv[2]
        if not SemanticVersion.is_valid(version_str):
            print(f"错误: 无效的版本号格式: {version_str}")
            sys.exit(1)
        version = SemanticVersion(version_str)
        reason = sys.argv[3] if len(sys.argv) >= 4 else ""
        manager.set_version(version, reason)
        print(f"版本已设置: {version}")
    elif command == "validate" and len(sys.argv) >= 3:
        version_str = sys.argv[2]
        if SemanticVersion.is_valid(version_str):
            print(f"✓ 版本号有效: {version_str}")
            sys.exit(0)
        else:
            print(f"✗ 版本号无效: {version_str}")
            sys.exit(1)
    elif command == "history":
        history = manager.get_version_history()
        print("版本历史:")
        for record in reversed(history):
            print(f"  {record['version']} - {record['timestamp']} - {record.get('reason', '')}")
    else:
        print(f"未知命令: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
