"""
云枢智能体 - 灰度发布与功能开关管理
支持按用户比例、用户ID、功能开关等方式进行灰度发布
"""

import json
import os
import random
import hashlib
from datetime import datetime
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum


class RolloutStrategy(str, Enum):
    """发布策略"""
    PERCENTAGE = "percentage"  # 按百分比
    USER_ID = "user_id"  # 按用户ID白名单
    SESSION = "session"  # 按会话
    INTERNAL = "internal"  # 仅内部用户


class FeatureStatus(str, Enum):
    """功能状态"""
    DISABLED = "disabled"  # 完全关闭
    ROLLOUT = "rollout"  # 灰度中
    ENABLED = "enabled"  # 全量开启
    DEPRECATED = "deprecated"  # 已废弃


@dataclass
class FeatureFlag:
    """功能开关"""
    name: str
    description: str
    status: FeatureStatus = FeatureStatus.DISABLED
    strategy: RolloutStrategy = RolloutStrategy.PERCENTAGE
    percentage: int = 0
    whitelist: List[str] = field(default_factory=list)
    blacklist: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class FeatureFlagManager:
    """功能开关管理器"""

    def __init__(self, config_file: str = "data/feature_flags.json"):
        self.config_file = config_file
        self._flags: Dict[str, FeatureFlag] = {}
        self._load()

    def _load(self):
        """加载配置"""
        os.makedirs(os.path.dirname(self.config_file), exist_ok=True)

        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                for name, flag_data in data.items():
                    flag = FeatureFlag(
                        name=name,
                        description=flag_data.get("description", ""),
                        status=FeatureStatus(flag_data.get("status", "disabled")),
                        strategy=RolloutStrategy(flag_data.get("strategy", "percentage")),
                        percentage=flag_data.get("percentage", 0),
                        whitelist=flag_data.get("whitelist", []),
                        blacklist=flag_data.get("blacklist", []),
                        created_at=flag_data.get("created_at", ""),
                        updated_at=flag_data.get("updated_at", ""),
                        metadata=flag_data.get("metadata", {}),
                    )
                    self._flags[name] = flag
            except (json.JSONDecodeError, IOError) as e:
                print(f"加载功能开关配置失败: {e}")

    def _save(self):
        """保存配置"""
        os.makedirs(os.path.dirname(self.config_file), exist_ok=True)

        data = {}
        for name, flag in self._flags.items():
            data[name] = {
                "description": flag.description,
                "status": flag.status.value,
                "strategy": flag.strategy.value,
                "percentage": flag.percentage,
                "whitelist": flag.whitelist,
                "blacklist": flag.blacklist,
                "created_at": flag.created_at,
                "updated_at": flag.updated_at,
                "metadata": flag.metadata,
            }

        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def add_feature(self, name: str, description: str = "") -> FeatureFlag:
        """添加新功能开关"""
        now = datetime.now().isoformat()
        flag = FeatureFlag(
            name=name,
            description=description,
            created_at=now,
            updated_at=now,
        )
        self._flags[name] = flag
        self._save()
        return flag

    def remove_feature(self, name: str) -> bool:
        """移除功能开关"""
        if name in self._flags:
            del self._flags[name]
            self._save()
            return True
        return False

    def set_status(self, name: str, status: FeatureStatus) -> Optional[FeatureFlag]:
        """设置功能状态"""
        if name not in self._flags:
            return None

        self._flags[name].status = status
        self._flags[name].updated_at = datetime.now().isoformat()
        self._save()
        return self._flags[name]

    def set_percentage(self, name: str, percentage: int) -> Optional[FeatureFlag]:
        """设置灰度百分比"""
        if name not in self._flags:
            return None

        percentage = max(0, min(100, percentage))
        self._flags[name].percentage = percentage
        self._flags[name].updated_at = datetime.now().isoformat()

        # 自动调整状态
        if percentage == 0:
            self._flags[name].status = FeatureStatus.DISABLED
        elif percentage >= 100:
            self._flags[name].status = FeatureStatus.ENABLED
        else:
            self._flags[name].status = FeatureStatus.ROLLOUT

        self._save()
        return self._flags[name]

    def add_to_whitelist(self, name: str, user_id: str) -> bool:
        """添加用户到白名单"""
        if name not in self._flags:
            return False

        if user_id not in self._flags[name].whitelist:
            self._flags[name].whitelist.append(user_id)
            self._flags[name].updated_at = datetime.now().isoformat()
            self._save()
        return True

    def remove_from_whitelist(self, name: str, user_id: str) -> bool:
        """从白名单移除用户"""
        if name not in self._flags:
            return False

        if user_id in self._flags[name].whitelist:
            self._flags[name].whitelist.remove(user_id)
            self._flags[name].updated_at = datetime.now().isoformat()
            self._save()
        return True

    def is_enabled(
        self,
        name: str,
        user_id: str = "",
        session_id: str = "",
        default: bool = False,
    ) -> bool:
        """检查功能是否启用"""
        if name not in self._flags:
            return default

        flag = self._flags[name]

        # 已废弃 - 始终禁用
        if flag.status == FeatureStatus.DEPRECATED:
            return False

        # 黑名单 - 始终禁用
        if user_id and user_id in flag.blacklist:
            return False

        # 白名单 - 始终启用（用于内部测试）
        if user_id and user_id in flag.whitelist:
            return True

        # 完全关闭
        if flag.status == FeatureStatus.DISABLED:
            return False

        # 全量开启
        if flag.status == FeatureStatus.ENABLED:
            return True

        # 按策略判断
        if flag.strategy == RolloutStrategy.PERCENTAGE:
            return self._check_percentage(flag, user_id)
        elif flag.strategy == RolloutStrategy.USER_ID:
            return user_id in flag.whitelist
        elif flag.strategy == RolloutStrategy.SESSION:
            return self._check_session(flag, session_id)
        elif flag.strategy == RolloutStrategy.INTERNAL:
            return self._check_internal(user_id)

        return False

    def _check_percentage(self, flag: FeatureFlag, user_id: str) -> bool:
        """按百分比判断是否启用"""
        if flag.percentage <= 0:
            return False
        if flag.percentage >= 100:
            return True

        # 使用用户ID哈希确保一致性（同一用户始终看到相同结果）
        if user_id:
            hash_input = f"{flag.name}:{user_id}"
            hash_val = int(hashlib.md5(hash_input.encode()).hexdigest(), 16)
            return (hash_val % 100) < flag.percentage
        else:
            # 无用户ID时随机
            return random.randint(1, 100) <= flag.percentage

    def _check_session(self, flag: FeatureFlag, session_id: str) -> bool:
        """按会话判断"""
        if not session_id:
            return False

        hash_input = f"{flag.name}:{session_id}"
        hash_val = int(hashlib.md5(hash_input.encode()).hexdigest(), 16)
        return (hash_val % 100) < flag.percentage

    def _check_internal(self, user_id: str) -> bool:
        """判断是否为内部用户"""
        return user_id in flag.whitelist

    def get_all_flags(self) -> Dict[str, FeatureFlag]:
        """获取所有功能开关"""
        return self._flags.copy()

    def get_flag(self, name: str) -> Optional[FeatureFlag]:
        """获取单个功能开关"""
        return self._flags.get(name)

    def get_status_summary(self) -> Dict[str, Any]:
        """获取状态摘要"""
        summary = {
            "total": len(self._flags),
            "enabled": 0,
            "disabled": 0,
            "rollout": 0,
            "deprecated": 0,
            "features": [],
        }

        for name, flag in self._flags.items():
            summary[flag.status.value] += 1
            summary["features"].append(
                {
                    "name": name,
                    "status": flag.status.value,
                    "percentage": flag.percentage,
                    "strategy": flag.strategy.value,
                    "description": flag.description,
                }
            )

        return summary


class ReleaseManager:
    """发布管理器"""

    def __init__(self, release_file: str = "data/releases.json"):
        self.release_file = release_file
        self._releases: List[Dict[str, Any]] = []
        self._load()

    def _load(self):
        """加载发布记录"""
        os.makedirs(os.path.dirname(self.release_file), exist_ok=True)

        if os.path.exists(self.release_file):
            try:
                with open(self.release_file, "r", encoding="utf-8") as f:
                    self._releases = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._releases = []

    def _save(self):
        """保存发布记录"""
        os.makedirs(os.path.dirname(self.release_file), exist_ok=True)

        with open(self.release_file, "w", encoding="utf-8") as f:
            json.dump(self._releases, f, indent=2, ensure_ascii=False)

    def start_release(
        self,
        version: str,
        description: str = "",
        rollout_percentage: int = 10,
    ) -> Dict[str, Any]:
        """开始新版本发布"""
        release = {
            "version": version,
            "description": description,
            "status": "rolling_out",
            "rollout_percentage": rollout_percentage,
            "started_at": datetime.now().isoformat(),
            "completed_at": None,
            "rolled_back_at": None,
            "metrics": {
                "error_rate": 0,
                "latency_p99": 0,
                "user_count": 0,
            },
            "checkpoints": [],
        }

        self._releases.insert(0, release)
        self._save()

        self._add_checkpoint(version, "release_started", f"开始发布，灰度比例: {rollout_percentage}%")

        return release

    def update_rollout(self, version: str, percentage: int) -> Optional[Dict]:
        """更新灰度比例"""
        release = self._find_release(version)
        if not release:
            return None

        release["rollout_percentage"] = max(0, min(100, percentage))

        if percentage >= 100:
            release["status"] = "completed"
            release["completed_at"] = datetime.now().isoformat()
            self._add_checkpoint(version, "rollout_complete", "全量发布完成")

        self._add_checkpoint(version, "rollout_update", f"灰度比例调整为: {percentage}%")
        self._save()
        return release

    def rollback(self, version: str, reason: str = "") -> Optional[Dict]:
        """回滚版本"""
        release = self._find_release(version)
        if not release:
            return None

        release["status"] = "rolled_back"
        release["rolled_back_at"] = datetime.now().isoformat()
        release["rollback_reason"] = reason

        self._add_checkpoint(version, "rollback", f"回滚: {reason}")
        self._save()
        return release

    def _find_release(self, version: str) -> Optional[Dict]:
        """查找版本发布记录"""
        for release in self._releases:
            if release["version"] == version:
                return release
        return None

    def _add_checkpoint(self, version: str, checkpoint_type: str, message: str):
        """添加检查点"""
        release = self._find_release(version)
        if release:
            release["checkpoints"].append(
                {
                    "type": checkpoint_type,
                    "message": message,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            self._save()

    def get_current_release(self) -> Optional[Dict]:
        """获取当前发布版本"""
        if self._releases:
            return self._releases[0]
        return None

    def get_release_history(self, limit: int = 10) -> List[Dict]:
        """获取发布历史"""
        return self._releases[:limit]


def main():
    """命令行入口"""
    import sys

    if len(sys.argv) < 2:
        print("用法:")
        print("  python feature_flags.py list                    # 列出所有功能开关")
        print("  python feature_flags.py add <name> [desc]      # 添加功能开关")
        print("  python feature_flags.py enable <name>          # 启用功能")
        print("  python feature_flags.py disable <name>         # 禁用功能")
        print("  python feature_flags.py percent <name> <pct>   # 设置灰度百分比")
        print("  python feature_flags.py check <name> [user_id] # 检查功能是否启用")
        print("  python feature_flags.py releases               # 查看发布历史")
        sys.exit(1)

    command = sys.argv[1]
    ff_manager = FeatureFlagManager()
    release_manager = ReleaseManager()

    if command == "list":
        summary = ff_manager.get_status_summary()
        print(f"功能开关总数: {summary['total']}")
        print(f"  已启用: {summary['enabled']}")
        print(f"  已禁用: {summary['disabled']}")
        print(f"  灰度中: {summary['rollout']}")
        print(f"  已废弃: {summary['deprecated']}")
        print()
        for feat in summary["features"]:
            status_icon = {"enabled": "✅", "disabled": "⭕", "rollout": "🚀", "deprecated": "⚠️"}
            icon = status_icon.get(feat["status"], "❓")
            print(f"  {icon} {feat['name']} ({feat['percentage']}%) - {feat['description']}")

    elif command == "add" and len(sys.argv) >= 3:
        name = sys.argv[2]
        desc = sys.argv[3] if len(sys.argv) >= 4 else ""
        flag = ff_manager.add_feature(name, desc)
        print(f"✅ 已添加功能开关: {name}")

    elif command == "enable" and len(sys.argv) >= 3:
        name = sys.argv[2]
        flag = ff_manager.set_status(name, FeatureStatus.ENABLED)
        if flag:
            print(f"✅ 已启用功能: {name}")
        else:
            print(f"❌ 功能不存在: {name}")

    elif command == "disable" and len(sys.argv) >= 3:
        name = sys.argv[2]
        flag = ff_manager.set_status(name, FeatureStatus.DISABLED)
        if flag:
            print(f"✅ 已禁用功能: {name}")
        else:
            print(f"❌ 功能不存在: {name}")

    elif command == "percent" and len(sys.argv) >= 4:
        name = sys.argv[2]
        try:
            percent = int(sys.argv[3])
        except ValueError:
            print("❌ 百分比必须是数字")
            sys.exit(1)
        flag = ff_manager.set_percentage(name, percent)
        if flag:
            print(f"✅ 灰度比例已设置: {name} = {percent}%")
        else:
            print(f"❌ 功能不存在: {name}")

    elif command == "check" and len(sys.argv) >= 3:
        name = sys.argv[2]
        user_id = sys.argv[3] if len(sys.argv) >= 4 else ""
        enabled = ff_manager.is_enabled(name, user_id=user_id)
        status = "✅ 启用" if enabled else "⭕ 禁用"
        print(f"功能 '{name}' 状态: {status}")
        if user_id:
            print(f"用户: {user_id}")

    elif command == "releases":
        releases = release_manager.get_release_history()
        print("发布历史:")
        for rel in releases:
            status_icon = {"completed": "✅", "rolling_out": "🚀", "rolled_back": "⏪"}
            icon = status_icon.get(rel["status"], "❓")
            print(f"  {icon} {rel['version']} - {rel['status']} ({rel['rollout_percentage']}%)")

    else:
        print(f"未知命令: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
