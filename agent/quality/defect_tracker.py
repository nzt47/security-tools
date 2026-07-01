import json
import os
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple

class DefectSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class DefectType(str, Enum):
    FUNCTIONAL = "functional"
    PERFORMANCE = "performance"
    SECURITY = "security"
    COMPATIBILITY = "compatibility"
    USABILITY = "usability"
    OTHER = "other"

class DefectStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    FIXED = "fixed"
    VERIFIED = "verified"
    CLOSED = "closed"

class Defect:
    def __init__(
        self,
        id: str,
        title: str,
        description: str,
        severity: DefectSeverity,
        defect_type: DefectType,
        status: DefectStatus = DefectStatus.OPEN,
        created_at: Optional[datetime] = None,
        fixed_at: Optional[datetime] = None,
        root_cause: Optional[str] = None,
        test_missing: bool = False,
        escaped_in_version: Optional[str] = None,
        detected_by: str = "unknown",
    ):
        self.id = id
        self.title = title
        self.description = description
        self.severity = severity
        self.defect_type = defect_type
        self.status = status
        self.created_at = created_at or datetime.now()
        self.fixed_at = fixed_at
        self.root_cause = root_cause
        self.test_missing = test_missing
        self.escaped_in_version = escaped_in_version
        self.detected_by = detected_by

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity.value,
            "defect_type": self.defect_type.value,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "fixed_at": self.fixed_at.isoformat() if self.fixed_at else None,
            "root_cause": self.root_cause,
            "test_missing": self.test_missing,
            "escaped_in_version": self.escaped_in_version,
            "detected_by": self.detected_by,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Defect":
        return cls(
            id=data["id"],
            title=data["title"],
            description=data["description"],
            severity=DefectSeverity(data["severity"]),
            defect_type=DefectType(data["defect_type"]),
            status=DefectStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
            fixed_at=datetime.fromisoformat(data["fixed_at"]) if data.get("fixed_at") else None,
            root_cause=data.get("root_cause"),
            test_missing=data.get("test_missing", False),
            escaped_in_version=data.get("escaped_in_version"),
            detected_by=data.get("detected_by", "unknown"),
        )

class DefectTracker:
    def __init__(self, storage_path: str = "data/quality/defects.json"):
        self.storage_path = storage_path
        self.defects: List[Defect] = []
        self._ensure_storage()
        self._load_defects()

    def _ensure_storage(self):
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)

    def _load_defects(self):
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.defects = [Defect.from_dict(d) for d in data]
            except (json.JSONDecodeError, IOError):
                self.defects = []

    def _save_defects(self):
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump([d.to_dict() for d in self.defects], f, ensure_ascii=False, indent=2)

    def add_defect(
        self,
        title: str,
        description: str,
        severity: DefectSeverity,
        defect_type: DefectType,
        escaped_in_version: Optional[str] = None,
        detected_by: str = "unknown",
    ) -> Defect:
        defect_id = f"DEF-{len(self.defects) + 1:04d}"
        defect = Defect(
            id=defect_id,
            title=title,
            description=description,
            severity=severity,
            defect_type=defect_type,
            escaped_in_version=escaped_in_version,
            detected_by=detected_by,
        )
        self.defects.append(defect)
        self._save_defects()
        return defect

    def update_defect(
        self,
        defect_id: str,
        **kwargs,
    ) -> Optional[Defect]:
        for defect in self.defects:
            if defect.id == defect_id:
                for key, value in kwargs.items():
                    if hasattr(defect, key):
                        setattr(defect, key, value)
                self._save_defects()
                return defect
        return None

    def get_defect(self, defect_id: str) -> Optional[Defect]:
        return next((d for d in self.defects if d.id == defect_id), None)

    def get_defects_by_status(self, status: DefectStatus) -> List[Defect]:
        return [d for d in self.defects if d.status == status]

    def get_defects_by_type(self, defect_type: DefectType) -> List[Defect]:
        return [d for d in self.defects if d.defect_type == defect_type]

    def get_defects_by_severity(self, severity: DefectSeverity) -> List[Defect]:
        return [d for d in self.defects if d.severity == severity]

    def get_escaped_defects(self, version: Optional[str] = None) -> List[Defect]:
        if version:
            return [d for d in self.defects if d.escaped_in_version == version]
        return [d for d in self.defects if d.escaped_in_version is not None]

    def calculate_escape_rate(self, period_days: int = 30) -> float:
        """计算逃逸率

        Args:
            period_days: 统计周期天数（0 ≤ period_days ≤ 36500）

        Raises:
            ValueError: period_days 为负数或超过 36500 时抛出
        """
        # 边界显性化：校验 period_days 参数，防止 OverflowError
        if not isinstance(period_days, int) or period_days < 0:
            raise ValueError(f"period_days 必须为非负整数，得到: {period_days!r}")
        if period_days > 36500:
            raise ValueError(f"period_days 超过上限 36500，得到: {period_days}")

        now = datetime.now()
        start_date = now - timedelta(days=period_days)
        total_defects = [d for d in self.defects if d.created_at >= start_date]
        escaped_defects = [d for d in total_defects if d.escaped_in_version is not None]
        if len(total_defects) == 0:
            return 0.0
        return (len(escaped_defects) / len(total_defects)) * 100

    def calculate_severity_breakdown(self) -> Dict[str, int]:
        breakdown = {}
        for defect in self.defects:
            key = defect.severity.value
            breakdown[key] = breakdown.get(key, 0) + 1
        return breakdown

    def calculate_type_breakdown(self) -> Dict[str, int]:
        breakdown = {}
        for defect in self.defects:
            key = defect.defect_type.value
            breakdown[key] = breakdown.get(key, 0) + 1
        return breakdown

    def get_escape_rate_trend(self, days: int = 90) -> List[Dict[str, Any]]:
        """获取逃逸率趋势

        Args:
            days: 趋势统计总天数（0 ≤ days ≤ 36500）

        Raises:
            ValueError: days 为负数或超过 36500 时抛出
        """
        # 边界显性化：校验 days 参数，防止 OverflowError
        if not isinstance(days, int) or days < 0:
            raise ValueError(f"days 必须为非负整数，得到: {days!r}")
        if days > 36500:
            raise ValueError(f"days 超过上限 36500，得到: {days}")

        now = datetime.now()
        trend = []
        for i in range(days, 0, -7):
            end_date = now - timedelta(days=i - 7)
            start_date = now - timedelta(days=i)
            period_defects = [d for d in self.defects if start_date <= d.created_at < end_date]
            escaped = [d for d in period_defects if d.escaped_in_version is not None]
            rate = (len(escaped) / len(period_defects)) * 100 if period_defects else 0.0
            trend.append({
                "period": f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}",
                "total_defects": len(period_defects),
                "escaped_defects": len(escaped),
                "escape_rate": round(rate, 2),
            })
        return trend

    def analyze_root_cause(self, defect_id: str) -> Dict[str, Any]:
        defect = self.get_defect(defect_id)
        if not defect:
            raise ValueError(f"缺陷不存在: {defect_id}")
        analysis = {
            "defect_id": defect.id,
            "title": defect.title,
            "severity": defect.severity.value,
            "defect_type": defect.defect_type.value,
            "root_cause": defect.root_cause or "未分析",
            "test_missing": defect.test_missing,
            "recommendations": [],
        }
        if defect.test_missing:
            analysis["recommendations"].append("建议添加单元测试覆盖此场景")
        if defect.severity in [DefectSeverity.CRITICAL, DefectSeverity.HIGH]:
            analysis["recommendations"].append("建议进行回归测试验证修复")
        if defect.defect_type == DefectType.SECURITY:
            analysis["recommendations"].append("建议进行安全扫描验证")
        return analysis

    def generate_test_case_suggestions(self, defect_id: str) -> List[str]:
        defect = self.get_defect(defect_id)
        if not defect:
            raise ValueError(f"缺陷不存在: {defect_id}")
        suggestions = []
        if defect.defect_type == DefectType.FUNCTIONAL:
            suggestions.append(f"测试用例: 验证 {defect.title} 的正常流程")
            suggestions.append(f"测试用例: 验证 {defect.title} 的边界条件")
            suggestions.append(f"测试用例: 验证 {defect.title} 的异常输入")
        elif defect.defect_type == DefectType.PERFORMANCE:
            suggestions.append(f"性能测试: 验证 {defect.title} 的响应时间")
            suggestions.append(f"性能测试: 验证 {defect.title} 的负载能力")
        elif defect.defect_type == DefectType.SECURITY:
            suggestions.append(f"安全测试: 验证 {defect.title} 的漏洞修复")
            suggestions.append(f"安全测试: 验证类似安全漏洞")
        elif defect.defect_type == DefectType.COMPATIBILITY:
            suggestions.append(f"兼容性测试: 验证 {defect.title} 在不同环境下的表现")
        if defect.test_missing:
            suggestions.append("建议添加集成测试覆盖相关模块")
        return suggestions

    def verify_fix(self, defect_id: str) -> bool:
        defect = self.get_defect(defect_id)
        if not defect:
            raise ValueError(f"缺陷不存在: {defect_id}")
        if defect.status != DefectStatus.FIXED:
            raise ValueError(f"缺陷状态不是已修复: {defect.status.value}")
        defect.status = DefectStatus.VERIFIED
        defect.fixed_at = datetime.now()
        self._save_defects()
        return True

    def get_defects_needing_test_update(self) -> List[Defect]:
        return [d for d in self.defects if d.test_missing and d.status == DefectStatus.FIXED]

    def get_metrics(self) -> Dict[str, Any]:
        now = datetime.now()
        today_defects = [d for d in self.defects if d.created_at.date() == now.date()]
        week_defects = [d for d in self.defects if d.created_at >= now - timedelta(days=7)]
        month_defects = [d for d in self.defects if d.created_at >= now - timedelta(days=30)]
        open_defects = self.get_defects_by_status(DefectStatus.OPEN)
        critical_open = [d for d in open_defects if d.severity == DefectSeverity.CRITICAL]
        return {
            "total_defects": len(self.defects),
            "open_defects": len(open_defects),
            "critical_open_defects": len(critical_open),
            "today_defects": len(today_defects),
            "week_defects": len(week_defects),
            "month_defects": len(month_defects),
            "escape_rate_30d": round(self.calculate_escape_rate(30), 2),
            "escape_rate_7d": round(self.calculate_escape_rate(7), 2),
            "severity_breakdown": self.calculate_severity_breakdown(),
            "type_breakdown": self.calculate_type_breakdown(),
            "defects_needing_test_update": len(self.get_defects_needing_test_update()),
        }

    def generate_report(self, report_type: str = "summary") -> Dict[str, Any]:
        if report_type == "weekly":
            return self._generate_weekly_report()
        elif report_type == "monthly":
            return self._generate_monthly_report()
        return self._generate_summary_report()

    def _generate_summary_report(self) -> Dict[str, Any]:
        return {
            "report_type": "summary",
            "generated_at": datetime.now().isoformat(),
            "metrics": self.get_metrics(),
        }

    def _generate_weekly_report(self) -> Dict[str, Any]:
        now = datetime.now()
        week_start = now - timedelta(days=now.weekday())
        week_defects = [d for d in self.defects if d.created_at >= week_start]
        escaped_this_week = [d for d in week_defects if d.escaped_in_version is not None]
        return {
            "report_type": "weekly",
            "generated_at": datetime.now().isoformat(),
            "week_start": week_start.isoformat(),
            "week_end": now.isoformat(),
            "total_defects_this_week": len(week_defects),
            "escaped_defects_this_week": len(escaped_this_week),
            "escape_rate_this_week": round(
                (len(escaped_this_week) / len(week_defects)) * 100 if week_defects else 0.0, 2
            ),
            "type_breakdown": self.calculate_type_breakdown(),
            "severity_breakdown": self.calculate_severity_breakdown(),
            "defects_needing_test_update": [
                d.to_dict() for d in self.get_defects_needing_test_update()
            ],
        }

    def _generate_monthly_report(self) -> Dict[str, Any]:
        now = datetime.now()
        month_start = now.replace(day=1)
        month_defects = [d for d in self.defects if d.created_at >= month_start]
        escaped_this_month = [d for d in month_defects if d.escaped_in_version is not None]
        return {
            "report_type": "monthly",
            "generated_at": datetime.now().isoformat(),
            "month_start": month_start.isoformat(),
            "month_end": now.isoformat(),
            "total_defects_this_month": len(month_defects),
            "escaped_defects_this_month": len(escaped_this_month),
            "escape_rate_this_month": round(
                (len(escaped_this_month) / len(month_defects)) * 100 if month_defects else 0.0, 2
            ),
            "escape_rate_trend": self.get_escape_rate_trend(90),
            "type_breakdown": self.calculate_type_breakdown(),
            "severity_breakdown": self.calculate_severity_breakdown(),
            "critical_defects": [
                d.to_dict() for d in self.get_defects_by_severity(DefectSeverity.CRITICAL)
            ],
            "defects_needing_test_update": [
                d.to_dict() for d in self.get_defects_needing_test_update()
            ],
        }