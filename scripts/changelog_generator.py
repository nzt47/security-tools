"""
云枢智能体 - CHANGELOG 自动生成工具
从 Git 提交历史和代码变更自动生成变更日志
"""

import os
import re
import json
import subprocess
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class ChangeEntry:
    """变更条目"""
    type: str
    scope: str
    description: str
    author: str
    commit_hash: str
    date: str
    breaking: bool = False


@dataclass
class ChangelogSection:
    """变更日志章节"""
    title: str
    emoji: str
    entries: List[ChangeEntry] = field(default_factory=list)


class ChangelogGenerator:
    """CHANGELOG 生成器"""

    # 约定式提交类型映射
    TYPE_MAP = {
        "feat": ("✨ 新功能", "新功能"),
        "fix": ("🐛 Bug 修复", "Bug 修复"),
        "perf": ("⚡ 性能优化", "性能优化"),
        "refactor": ("♻️ 重构", "代码重构"),
        "docs": ("📝 文档", "文档更新"),
        "test": ("🧪 测试", "测试相关"),
        "style": ("💄 样式", "代码风格"),
        "build": ("📦 构建", "构建系统"),
        "ci": ("👷 CI/CD", "持续集成"),
        "chore": ("🔧 杂项", "杂项变更"),
        "revert": ("⏪ 回退", "代码回退"),
        "security": ("🔒 安全", "安全相关"),
    }

    # 章节排序
    SECTION_ORDER = [
        "feat", "fix", "perf", "security",
        "refactor", "docs", "test", "style",
        "build", "ci", "chore", "revert",
    ]

    CONVENTIONAL_COMMIT_REGEX = re.compile(
        r"^(?P<type>[a-zA-Z]+)"
        r"(?:\((?P<scope>[^)]+)\))?"
        r"(?P<breaking>!)?"
        r":\s*"
        r"(?P<description>.+)"
    )

    def __init__(self, repo_path: str = "."):
        self.repo_path = repo_path

    def _run_git_command(self, args: List[str]) -> str:
        """执行 Git 命令"""
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            print(f"Git 命令执行失败: {e.stderr}")
            return ""

    def get_commits(self, since_tag: Optional[str] = None, until: str = "HEAD") -> List[Dict]:
        """获取提交历史"""
        format_str = (
            "%H%n"  # commit hash
            "%an%n"  # author name
            "%ae%n"  # author email
            "%aI%n"  # author date ISO
            "%s%n"  # subject
            "%b%n"  # body
            "---COMMIT_END---"
        )

        args = ["log", f"--pretty=format:{format_str}"]
        if since_tag:
            args.append(f"{since_tag}..{until}")
        else:
            args.append(until)

        output = self._run_git_command(args)
        if not output:
            return []

        commits = []
        raw_commits = output.split("---COMMIT_END---")

        for raw_commit in raw_commits:
            raw_commit = raw_commit.strip()
            if not raw_commit:
                continue

            lines = raw_commit.strip().split("\n")
            if len(lines) < 5:
                continue

            commit = {
                "hash": lines[0].strip(),
                "author": lines[1].strip(),
                "email": lines[2].strip(),
                "date": lines[3].strip(),
                "subject": lines[4].strip(),
                "body": "\n".join(lines[5:]).strip() if len(lines) > 5 else "",
            }
            commits.append(commit)

        return commits

    def parse_commit_message(self, commit: Dict) -> Optional[ChangeEntry]:
        """解析约定式提交消息"""
        subject = commit["subject"]
        match = self.CONVENTIONAL_COMMIT_REGEX.match(subject)

        if not match:
            # 非约定式提交，尝试作为杂项处理
            return ChangeEntry(
                type="chore",
                scope="",
                description=subject,
                author=commit["author"],
                commit_hash=commit["hash"][:7],
                date=commit["date"],
                breaking=False,
            )

        commit_type = match.group("type").lower()
        scope = match.group("scope") or ""
        description = match.group("description").strip()
        breaking = match.group("breaking") is not None

        # 检查正文中是否有 BREAKING CHANGE
        if "BREAKING CHANGE" in commit["body"]:
            breaking = True

        if commit_type not in self.TYPE_MAP:
            commit_type = "chore"

        return ChangeEntry(
            type=commit_type,
            scope=scope,
            description=description,
            author=commit["author"],
            commit_hash=commit["hash"][:7],
            date=commit["date"],
            breaking=breaking,
        )

    def get_latest_tag(self) -> Optional[str]:
        """获取最新的标签"""
        tags = self._run_git_command(["tag", "-l", "--sort=-v:refname"])
        if not tags:
            return None
        return tags.split("\n")[0].strip()

    def get_all_tags(self) -> List[str]:
        """获取所有标签（按版本排序）"""
        tags = self._run_git_command(["tag", "-l", "--sort=-v:refname"])
        if not tags:
            return []
        return [t.strip() for t in tags.split("\n") if t.strip()]

    def generate_changelog(
        self,
        version: str,
        since_tag: Optional[str] = None,
        output_file: str = "CHANGELOG.md",
    ) -> str:
        """生成 CHANGELOG 内容"""
        commits = self.get_commits(since_tag=since_tag)

        # 解析提交
        entries = []
        for commit in commits:
            entry = self.parse_commit_message(commit)
            if entry:
                entries.append(entry)

        # 按类型分组
        sections: Dict[str, ChangelogSection] = {}
        breaking_changes: List[ChangeEntry] = []

        for entry in entries:
            if entry.breaking:
                breaking_changes.append(entry)

            if entry.type not in sections:
                title, _ = self.TYPE_MAP.get(entry.type, ("杂项", "chore"))
                sections[entry.type] = ChangelogSection(title=title, emoji=title.split()[0])

            sections[entry.type].entries.append(entry)

        # 生成 Markdown
        today = datetime.now().strftime("%Y-%m-%d")
        lines = []

        lines.append(f"## 版本 {version} - {today}")
        lines.append("")

        # 破坏性变更
        if breaking_changes:
            lines.append("### ⚠️ 破坏性变更")
            lines.append("")
            for entry in breaking_changes:
                scope = f"**{entry.scope}**: " if entry.scope else ""
                lines.append(f"- {scope}{entry.description} ({entry.commit_hash})")
            lines.append("")

        # 各类型变更
        for type_key in self.SECTION_ORDER:
            if type_key not in sections:
                continue

            section = sections[type_key]
            if not section.entries:
                continue

            lines.append(f"### {section.title}")
            lines.append("")

            # 按 scope 分组
            scoped_entries: Dict[str, List[ChangeEntry]] = {}
            for entry in section.entries:
                scope = entry.scope or "其他"
                if scope not in scoped_entries:
                    scoped_entries[scope] = []
                scoped_entries[scope].append(entry)

            for scope, scope_entries in sorted(scoped_entries.items()):
                if scope != "其他":
                    lines.append(f"#### {scope}")
                    lines.append("")
                for entry in scope_entries:
                    lines.append(f"- {entry.description} ({entry.commit_hash}, @{entry.author})")
                if scope != "其他":
                    lines.append("")

            if "其他" in scoped_entries and len(scoped_entries) == 1:
                pass  # 已在上面处理
            lines.append("")

        # 统计信息
        total_changes = len(entries)
        if total_changes > 0:
            lines.append("### 📊 变更统计")
            lines.append("")
            lines.append(f"- 总变更数: **{total_changes}**")
            for type_key in self.SECTION_ORDER:
                if type_key in sections:
                    count = len(sections[type_key].entries)
                    title = self.TYPE_MAP.get(type_key, ("杂项",))[0]
                    lines.append(f"- {title}: {count}")
            lines.append("")

        changelog_text = "\n".join(lines)

        # 写入文件（前置内容）
        if output_file:
            self._write_changelog(changelog_text, output_file)

        return changelog_text

    def _write_changelog(self, new_content: str, output_file: str):
        """写入 CHANGELOG 文件（前置新内容）"""
        file_path = os.path.join(self.repo_path, output_file)

        existing_content = ""
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                existing_content = f.read()

        # 移除标题（如果有的话）
        if existing_content.startswith("# 更新日志"):
            lines = existing_content.split("\n")
            # 跳过标题和空行
            i = 0
            while i < len(lines) and (lines[i].startswith("#") or lines[i].strip() == ""):
                i += 1
            existing_content = "\n".join(lines[i:])

        full_content = "# 更新日志 (CHANGELOG)\n\n"
        full_content += new_content + "\n"
        full_content += "---\n\n"
        full_content += existing_content

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(full_content)


def main():
    """命令行入口"""
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="云枢 CHANGELOG 生成工具")
    parser.add_argument("version", nargs="?", help="版本号，如 1.2.3")
    parser.add_argument("--since", help="从哪个标签开始")
    parser.add_argument("--output", default="CHANGELOG.md", help="输出文件")
    parser.add_argument("--list-tags", action="store_true", help="列出所有标签")
    parser.add_argument("--dry-run", action="store_true", help="仅显示，不写入文件")

    args = parser.parse_args()

    generator = ChangelogGenerator()

    if args.list_tags:
        tags = generator.get_all_tags()
        print("标签列表:")
        for tag in tags:
            print(f"  - {tag}")
        return

    if not args.version:
        print("错误: 请提供版本号")
        print("用法: python changelog_generator.py <version> [--since TAG]")
        sys.exit(1)

    output = None if args.dry_run else args.output
    changelog = generator.generate_changelog(
        version=args.version,
        since_tag=args.since,
        output_file=output,
    )

    print(changelog)
    if not args.dry_run:
        print(f"\n✅ CHANGELOG 已写入: {args.output}")


if __name__ == "__main__":
    main()
