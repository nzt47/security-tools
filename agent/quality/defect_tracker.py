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
