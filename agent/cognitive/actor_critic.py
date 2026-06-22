"""双 Agent 校验模块——Actor-Critic 架构

高风险任务设置"执行分身"与"审核分身"。
执行分身（Actor）生成结果，审核分身（Critic）审查并提出修改建议。
设计思想：设计文档 4.2（双 Agent 校验）

架构说明：
- 当前为基于规则的高风险操作审核引擎（零 Token 消耗）
- 后续可升级为 LLM 驱动的审核分身：一个 Subagent 执行，另一个 Subagent 审核
- 知识蒸馏：审核发现的问题可以沉淀为规则知识

高风险任务类型：
- execute_shell: shell 命令执行（含危险命令检测）
- write_file: 文件写入（含系统目录检测）
- delete_file: 文件删除
- start_process: 进程启动
- browser_navigate: 浏览器导航
"""
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ReviewResult:
    """审核结果

    Attributes:
        approved: 是否通过审核
        issues: 发现的问题列表
        suggestions: 改进建议列表
        score: 安全评分 0.0~1.0
    """
    approved: bool
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    score: float = 0.0


class ActorCriticReviewer:
    """双 Agent 校验器——基于规则的高风险操作审核引擎

    对高风险工具调用进行自动审核，检测潜在危险操作。
    当前为规则引擎实现，后续可升级为 LLM 驱动的审核 Subagent。

    用法:
        reviewer = ActorCriticReviewer()
        if reviewer.is_high_risk("execute_shell"):
            result = reviewer.review("execute_shell", {"command": "rm -rf /"}, {})
            if not result.approved:
                logger.warning("审核未通过: %s", result.issues)
    """

    # 高风险任务类型——需要双 Agent 校验
    HIGH_RISK_TASKS: list[str] = [
        "execute_shell",
        "write_file",
        "delete_file",
        "start_process",
        "browser_navigate",
    ]

    # 危险命令关键字——执行时需格外小心
    DANGEROUS_COMMANDS: list[str] = [
        "rm", "del", "format", "shutdown", "reboot",
        "mkfs", "dd", "chmod", "chown", "kill",
    ]

    # 系统保护目录——禁止写入
    PROTECTED_DIRS: list[str] = [
        "/etc/", "/boot/", "/sys/", "/proc/",
        "C:\\Windows\\", "C:\\System32\\",
    ]

    def is_high_risk(self, tool_name: str) -> bool:
        """判断是否为高风险工具

        Args:
            tool_name: 工具名称

        Returns:
            True 表示需要双 Agent 校验
        """
        return tool_name in self.HIGH_RISK_TASKS

    def review(self, tool_name: str,
               params: dict,
               result: dict) -> ReviewResult:
        """审核工具调用结果

        根据工具类型执行针对性安全检查。

        Args:
            tool_name: 工具名称
            params: 工具调用参数
            result: 工具执行结果

        Returns:
            ReviewResult 审核结果
        """
        issues = []
        suggestions = []
        score = 1.0

        params = params or {}
        result = result or {}

        # 按工具类型执行专项检查
        if tool_name == "execute_shell":
            score, issues, suggestions = self._review_shell(params, result, issues, suggestions)

        elif tool_name == "write_file":
            score, issues, suggestions = self._review_write_file(params, result, issues, suggestions)

        elif tool_name == "delete_file":
            score, issues, suggestions = self._review_delete(params, result, issues, suggestions)

        elif tool_name == "start_process":
            score, issues, suggestions = self._review_process(params, result, issues, suggestions)

        elif tool_name == "browser_navigate":
            score, issues, suggestions = self._review_browser(params, result, issues, suggestions)

        # 通用检查：执行结果是否包含错误
        if result.get("error"):
            issues.append("执行出错: %s" % result["error"])
            score -= 0.3

        # 分数裁剪
        score = max(0.0, min(1.0, score))
        approved = score > 0.7

        if issues:
            logger.warning("[Cognitive] ActorCritic: tool=%s, approved=%s, "
                          "score=%.2f, issues=%d",
                          tool_name, approved, score, len(issues))

        return ReviewResult(
            approved=approved,
            issues=issues,
            suggestions=suggestions,
            score=score,
        )

    def _review_shell(self, params: dict, result: dict,
                       issues: list, suggestions: list) -> tuple[float, list, list]:
        """审核 shell 命令执行

        Args:
            params: 工具参数
            result: 执行结果
            issues: 问题列表（会追加）
            suggestions: 建议列表（会追加）

        Returns:
            (score, issues, suggestions)
        """
        score = 1.0
        command = params.get("command") or params.get("cmd", "")

        if not command:
            issues.append("shell 命令为空")
            score -= 0.2
            return score, issues, suggestions

        command_lower = command.lower()

        # 检查危险命令
        for dangerous in self.DANGEROUS_COMMANDS:
            pattern = r'\b' + dangerous + r'\b'
            import re
            if re.search(pattern, command_lower):
                issues.append("包含潜在危险命令: %s（命令: %s）"
                              % (dangerous, command[:80]))
                score -= 0.3
                break

        # 检查递归/强制删除
        if "rm -rf" in command_lower or "rm -fr" in command_lower:
            issues.append("递归强制删除操作: %s" % command[:80])
            score -= 0.2

        # 检查管道到危险目标
        if "|" in command and any(d in command_lower for d in ["sh", "bash"]):
            suggestions.append("管道到 shell 可能带来安全风险")
            score -= 0.1

        # 超长命令检查
        if len(command) > 500:
            suggestions.append("命令过长（%d 字符），建议分步执行" % len(command))
            score -= 0.05

        return score, issues, suggestions

    def _review_write_file(self, params: dict, result: dict,
                            issues: list, suggestions: list) -> tuple[float, list, list]:
        """审核文件写入操作

        Args:
            params: 工具参数
            result: 执行结果
            issues: 问题列表（会追加）
            suggestions: 建议列表（会追加）

        Returns:
            (score, issues, suggestions)
        """
        score = 1.0
        path = params.get("path") or params.get("file_path", "")

        if not path:
            issues.append("写入路径为空")
            score -= 0.2
            return score, issues, suggestions

        # 检查是否写入系统保护目录
        for protected in self.PROTECTED_DIRS:
            if protected.lower() in path.lower():
                issues.append("尝试写入系统目录: %s" % path)
                score -= 0.4
                break

        # 检查是否写入 .git 目录
        if ".git" in path:
            issues.append("写入 .git 目录可能破坏版本控制: %s" % path)
            score -= 0.3

        # 检查写入内容是否为空
        content = params.get("content") or params.get("data", "")
        if not content:
            suggestions.append("写入内容为空")
            score -= 0.1

        return score, issues, suggestions

    def _review_delete(self, params: dict, result: dict,
                        issues: list, suggestions: list) -> tuple[float, list, list]:
        """审核文件删除操作

        Args:
            params: 工具参数
            result: 执行结果
            issues: 问题列表（会追加）
            suggestions: 建议列表（会追加）

        Returns:
            (score, issues, suggestions)
        """
        score = 1.0
        path = params.get("path") or params.get("file_path", "")

        if not path:
            issues.append("删除路径为空")
            score -= 0.2
            return score, issues, suggestions

        # 检查删除系统文件
        import re
        if re.search(r'\.(dll|sys|exe|bin|so)$', path.lower()):
            issues.append("尝试删除系统可执行文件: %s" % path)
            score -= 0.4

        # 检查删除 .git
        if ".git" in path:
            issues.append("删除 .git 目录可能导致版本控制丢失")
            score -= 0.3

        # 检查空路径
        if path in ("/", "C:\\", "D:\\"):
            issues.append("尝试删除根目录: %s" % path)
            score -= 0.5

        return score, issues, suggestions

    def _review_process(self, params: dict, result: dict,
                         issues: list, suggestions: list) -> tuple[float, list, list]:
        """审核进程启动操作

        Args:
            params: 工具参数
            result: 执行结果
            issues: 问题列表（会追加）
            suggestions: 建议列表（会追加）

        Returns:
            (score, issues, suggestions)
        """
        score = 1.0
        command = params.get("command") or params.get("cmd", "")

        if not command:
            issues.append("启动命令为空")
            score -= 0.2

        return score, issues, suggestions

    def _review_browser(self, params: dict, result: dict,
                         issues: list, suggestions: list) -> tuple[float, list, list]:
        """审核浏览器导航操作

        Args:
            params: 工具参数
            result: 执行结果
            issues: 问题列表（会追加）
            suggestions: 建议列表（会追加）

        Returns:
            (score, issues, suggestions)
        """
        score = 1.0
        url = params.get("url") or params.get("href", "")

        if not url:
            issues.append("导航 URL 为空")
            score -= 0.2

        return score, issues, suggestions
