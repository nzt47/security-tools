"""
云枢智能体 - 发布工程单元测试
覆盖版本管理、功能开关、发布检查清单等模块
"""

import os
import sys
import json
import tempfile
import pytest
from datetime import datetime

SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
sys.path.insert(0, SCRIPTS_DIR)

from version_manager import SemanticVersion, VersionManager
from feature_flags import (
    FeatureFlagManager,
    FeatureFlag,
    FeatureStatus,
    RolloutStrategy,
    ReleaseManager,
)
from release_checklist import ReleaseChecklist, CheckStatus


class TestSemanticVersion:
    """语义化版本号测试"""

    def test_valid_version_parsing(self):
        """测试有效版本号解析"""
        v = SemanticVersion("1.2.3")
        assert v.major == 1
        assert v.minor == 2
        assert v.patch == 3
        assert v.prerelease == ""
        assert v.buildmetadata == ""

    def test_prerelease_version(self):
        """测试预发布版本"""
        v = SemanticVersion("1.2.3-alpha.1")
        assert v.major == 1
        assert v.minor == 2
        assert v.patch == 3
        assert v.prerelease == "alpha.1"

    def test_build_metadata(self):
        """测试构建元数据"""
        v = SemanticVersion("1.2.3+build.123")
        assert v.buildmetadata == "build.123"

    def test_full_version(self):
        """测试完整版本号"""
        v = SemanticVersion("1.2.3-beta.2+sha.abc123")
        assert v.major == 1
        assert v.minor == 2
        assert v.patch == 3
        assert v.prerelease == "beta.2"
        assert v.buildmetadata == "sha.abc123"

    def test_invalid_version_raises(self):
        """测试无效版本号抛出异常"""
        with pytest.raises(ValueError):
            SemanticVersion("invalid")

        with pytest.raises(ValueError):
            SemanticVersion("1.2")

        with pytest.raises(ValueError):
            SemanticVersion("1.2.3.4")

    def test_is_valid_static_method(self):
        """测试静态验证方法"""
        assert SemanticVersion.is_valid("1.2.3")
        assert SemanticVersion.is_valid("1.2.3-alpha.1")
        assert SemanticVersion.is_valid("0.0.0")
        assert not SemanticVersion.is_valid("invalid")
        assert not SemanticVersion.is_valid("1.2")

    def test_version_comparison(self):
        """测试版本比较"""
        v1 = SemanticVersion("1.2.3")
        v2 = SemanticVersion("1.2.4")
        v3 = SemanticVersion("1.3.0")
        v4 = SemanticVersion("2.0.0")

        assert v1 < v2
        assert v2 < v3
        assert v3 < v4
        assert v1 == SemanticVersion("1.2.3")
        assert v1 != v2
        assert v1 <= v2
        assert v2 >= v1

    def test_prerelease_less_than_release(self):
        """测试预发布版本小于正式版本"""
        prerelease = SemanticVersion("1.0.0-alpha.1")
        release = SemanticVersion("1.0.0")
        assert prerelease < release

    def test_bump_major(self):
        """测试主版本递增"""
        v = SemanticVersion("1.2.3")
        new_v = v.bump_major()
        assert new_v.major == 2
        assert new_v.minor == 0
        assert new_v.patch == 0
        assert new_v.prerelease == ""

    def test_bump_minor(self):
        """测试次版本递增"""
        v = SemanticVersion("1.2.3")
        new_v = v.bump_minor()
        assert new_v.major == 1
        assert new_v.minor == 3
        assert new_v.patch == 0
        assert new_v.prerelease == ""

    def test_bump_patch(self):
        """测试修订号递增"""
        v = SemanticVersion("1.2.3")
        new_v = v.bump_patch()
        assert new_v.major == 1
        assert new_v.minor == 2
        assert new_v.patch == 4
        assert new_v.prerelease == ""

    def test_bump_prerelease(self):
        """测试预发布版本递增"""
        v = SemanticVersion("1.2.3-alpha.1")
        new_v = v.bump_prerelease()
        assert new_v.prerelease == "alpha.2"

    def test_bump_prerelease_from_release(self):
        """测试从正式版创建预发布版本"""
        v = SemanticVersion("1.2.3")
        new_v = v.bump_prerelease("beta")
        assert new_v.prerelease == "beta.1"

    def test_to_string(self):
        """测试字符串转换"""
        assert str(SemanticVersion("1.2.3")) == "1.2.3"
        assert str(SemanticVersion("1.2.3-alpha.1")) == "1.2.3-alpha.1"
        assert str(SemanticVersion("1.2.3+build.1")) == "1.2.3+build.1"

    def test_is_prerelease(self):
        """测试是否为预发布版本"""
        assert SemanticVersion("1.0.0-alpha.1").is_prerelease()
        assert SemanticVersion("1.0.0-beta").is_prerelease()
        assert not SemanticVersion("1.0.0").is_prerelease()


class TestVersionManager:
    """版本管理器测试"""

    def test_get_current_version_default(self):
        """测试获取默认版本号"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = VersionManager(base_dir=tmpdir)
            version = manager.get_current_version()
            assert str(version) == "0.1.0"

    def test_set_and_get_version(self):
        """测试设置和获取版本号"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = VersionManager(base_dir=tmpdir)
            new_version = SemanticVersion("2.0.0")
            result = manager.set_version(new_version, "测试设置版本")
            assert str(result) == "2.0.0"

            version = manager.get_current_version()
            assert str(version) == "2.0.0"

    def test_bump_version(self):
        """测试版本递增"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = VersionManager(base_dir=tmpdir)
            manager.set_version(SemanticVersion("1.2.3"))

            # 测试 patch 递增
            v = manager.bump_version("patch")
            assert str(v) == "1.2.4"

            # 测试 minor 递增
            v = manager.bump_version("minor")
            assert str(v) == "1.3.0"

            # 测试 major 递增
            v = manager.bump_version("major")
            assert str(v) == "2.0.0"

    def test_bump_invalid_type_raises(self):
        """测试无效递增类型抛出异常"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = VersionManager(base_dir=tmpdir)
            with pytest.raises(ValueError):
                manager.bump_version("invalid")

    def test_version_history(self):
        """测试版本历史记录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = VersionManager(base_dir=tmpdir)
            manager.set_version(SemanticVersion("1.0.0"), "初始版本")
            manager.bump_version("patch", "修复 bug")

            history = manager.get_version_history()
            assert len(history) >= 2


class TestFeatureFlagManager:
    """功能开关管理器测试"""

    def test_add_feature(self):
        """测试添加功能开关"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "feature_flags.json")
            manager = FeatureFlagManager(config_file=config_file)

            flag = manager.add_feature("test_feature", "测试功能")
            assert flag.name == "test_feature"
            assert flag.status == FeatureStatus.DISABLED

    def test_remove_feature(self):
        """测试移除功能开关"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "feature_flags.json")
            manager = FeatureFlagManager(config_file=config_file)

            manager.add_feature("test_feature")
            assert manager.remove_feature("test_feature")
            assert not manager.remove_feature("nonexistent")

    def test_set_status(self):
        """测试设置功能状态"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "feature_flags.json")
            manager = FeatureFlagManager(config_file=config_file)

            manager.add_feature("test_feature")
            flag = manager.set_status("test_feature", FeatureStatus.ENABLED)
            assert flag.status == FeatureStatus.ENABLED

            # 测试不存在的功能
            assert manager.set_status("nonexistent", FeatureStatus.ENABLED) is None

    def test_set_percentage(self):
        """测试设置灰度百分比"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "feature_flags.json")
            manager = FeatureFlagManager(config_file=config_file)

            manager.add_feature("test_feature")

            # 0% -> disabled
            flag = manager.set_percentage("test_feature", 0)
            assert flag.percentage == 0
            assert flag.status == FeatureStatus.DISABLED

            # 50% -> rollout
            flag = manager.set_percentage("test_feature", 50)
            assert flag.percentage == 50
            assert flag.status == FeatureStatus.ROLLOUT

            # 100% -> enabled
            flag = manager.set_percentage("test_feature", 100)
            assert flag.percentage == 100
            assert flag.status == FeatureStatus.ENABLED

            # 测试边界值
            flag = manager.set_percentage("test_feature", 150)
            assert flag.percentage == 100

            flag = manager.set_percentage("test_feature", -10)
            assert flag.percentage == 0

    def test_whitelist(self):
        """测试白名单功能"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "feature_flags.json")
            manager = FeatureFlagManager(config_file=config_file)

            manager.add_feature("test_feature")
            manager.set_percentage("test_feature", 0)  # 先关闭

            # 添加白名单用户
            assert manager.add_to_whitelist("test_feature", "user1")
            assert manager.is_enabled("test_feature", user_id="user1")
            assert not manager.is_enabled("test_feature", user_id="user2")

            # 移除白名单
            assert manager.remove_from_whitelist("test_feature", "user1")
            assert not manager.is_enabled("test_feature", user_id="user1")

    def test_is_enabled_disabled(self):
        """测试禁用状态"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "feature_flags.json")
            manager = FeatureFlagManager(config_file=config_file)

            manager.add_feature("test_feature")
            assert not manager.is_enabled("test_feature")

    def test_is_enabled_fully_enabled(self):
        """测试完全启用状态"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "feature_flags.json")
            manager = FeatureFlagManager(config_file=config_file)

            manager.add_feature("test_feature")
            manager.set_status("test_feature", FeatureStatus.ENABLED)
            assert manager.is_enabled("test_feature")
            assert manager.is_enabled("test_feature", user_id="anyuser")

    def test_nonexistent_feature_uses_default(self):
        """测试不存在的功能使用默认值"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "feature_flags.json")
            manager = FeatureFlagManager(config_file=config_file)

            assert not manager.is_enabled("nonexistent")
            assert manager.is_enabled("nonexistent", default=True)

    def test_percentage_consistency(self):
        """测试百分比判断的一致性"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "feature_flags.json")
            manager = FeatureFlagManager(config_file=config_file)

            manager.add_feature("test_feature")
            manager.set_percentage("test_feature", 50)

            # 同一用户应该得到一致的结果
            results = [
                manager.is_enabled("test_feature", user_id="user123")
                for _ in range(10)
            ]
            # 所有结果应该相同
            assert all(r == results[0] for r in results)

    def test_get_all_flags(self):
        """测试获取所有功能开关"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "feature_flags.json")
            manager = FeatureFlagManager(config_file=config_file)

            manager.add_feature("feature1")
            manager.add_feature("feature2")

            all_flags = manager.get_all_flags()
            assert len(all_flags) == 2
            assert "feature1" in all_flags
            assert "feature2" in all_flags

    def test_status_summary(self):
        """测试状态摘要"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "feature_flags.json")
            manager = FeatureFlagManager(config_file=config_file)

            manager.add_feature("enabled_feature")
            manager.set_status("enabled_feature", FeatureStatus.ENABLED)

            manager.add_feature("disabled_feature")

            summary = manager.get_status_summary()
            assert summary["total"] == 2
            assert summary["enabled"] == 1
            assert summary["disabled"] == 1


class TestReleaseManager:
    """发布管理器测试"""

    def test_start_release(self):
        """测试开始发布"""
        with tempfile.TemporaryDirectory() as tmpdir:
            release_file = os.path.join(tmpdir, "releases.json")
            manager = ReleaseManager(release_file=release_file)

            release = manager.start_release("1.0.0", "测试发布", rollout_percentage=10)
            assert release["version"] == "1.0.0"
            assert release["status"] == "rolling_out"
            assert release["rollout_percentage"] == 10

    def test_update_rollout(self):
        """测试更新灰度比例"""
        with tempfile.TemporaryDirectory() as tmpdir:
            release_file = os.path.join(tmpdir, "releases.json")
            manager = ReleaseManager(release_file=release_file)

            manager.start_release("1.0.0")
            release = manager.update_rollout("1.0.0", 50)
            assert release["rollout_percentage"] == 50
            assert release["status"] == "rolling_out"

    def test_full_rollout_completes(self):
        """测试全量发布完成"""
        with tempfile.TemporaryDirectory() as tmpdir:
            release_file = os.path.join(tmpdir, "releases.json")
            manager = ReleaseManager(release_file=release_file)

            manager.start_release("1.0.0")
            release = manager.update_rollout("1.0.0", 100)
            assert release["status"] == "completed"
            assert release["completed_at"] is not None

    def test_rollback(self):
        """测试回滚"""
        with tempfile.TemporaryDirectory() as tmpdir:
            release_file = os.path.join(tmpdir, "releases.json")
            manager = ReleaseManager(release_file=release_file)

            manager.start_release("1.0.0")
            release = manager.rollback("1.0.0", "出现严重 bug")
            assert release["status"] == "rolled_back"
            assert release["rolled_back_at"] is not None
            assert release["rollback_reason"] == "出现严重 bug"

    def test_get_current_release(self):
        """测试获取当前发布"""
        with tempfile.TemporaryDirectory() as tmpdir:
            release_file = os.path.join(tmpdir, "releases.json")
            manager = ReleaseManager(release_file=release_file)

            assert manager.get_current_release() is None

            manager.start_release("1.0.0")
            current = manager.get_current_release()
            assert current["version"] == "1.0.0"

    def test_release_history(self):
        """测试发布历史"""
        with tempfile.TemporaryDirectory() as tmpdir:
            release_file = os.path.join(tmpdir, "releases.json")
            manager = ReleaseManager(release_file=release_file)

            manager.start_release("1.0.0")
            manager.start_release("1.1.0")

            history = manager.get_release_history()
            assert len(history) == 2
            # 最新的排在前面
            assert history[0]["version"] == "1.1.0"


class TestReleaseChecklist:
    """发布检查清单测试"""

    def test_init_checks(self):
        """测试初始化检查项"""
        checklist = ReleaseChecklist()
        assert len(checklist.checks) > 0

        check_ids = [c.id for c in checklist.checks]
        assert "code_quality_lint" in check_ids
        assert "test_unit" in check_ids
        assert "build_docker" in check_ids
        assert "security" in check_ids or "code_security" in check_ids

    def test_run_all_checks(self):
        """测试运行所有检查"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建一些基本文件
            os.makedirs(os.path.join(tmpdir, "tests", "unit"), exist_ok=True)
            with open(os.path.join(tmpdir, "tests", "unit", "test_example.py"), "w") as f:
                f.write("# test file")

            with open(os.path.join(tmpdir, "Dockerfile"), "w") as f:
                f.write("FROM python:3.11")

            with open(os.path.join(tmpdir, "pyproject.toml"), "w") as f:
                f.write('version = "1.0.0"')

            with open(os.path.join(tmpdir, "CHANGELOG.md"), "w") as f:
                f.write("# CHANGELOG")

            with open(os.path.join(tmpdir, ".gitignore"), "w") as f:
                f.write(".env")

            checklist = ReleaseChecklist(base_dir=tmpdir)
            all_passed, checks = checklist.run_all_checks()

            # 检查结果是合理的
            assert len(checks) > 0
            # 至少有一些检查不是 pending 状态
            pending_count = sum(1 for c in checks if c.status == CheckStatus.PENDING)
            assert pending_count < len(checks)

    def test_generate_report(self):
        """测试生成报告"""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = os.path.join(tmpdir, "report.md")
            checklist = ReleaseChecklist(base_dir=tmpdir)
            report = checklist.generate_report(output_file)

            assert "云枢发布检查清单报告" in report
            assert "检查统计" in report
            assert os.path.exists(output_file)

            with open(output_file, "r", encoding="utf-8") as f:
                content = f.read()
            assert "云枢发布检查清单报告" in content

    def test_check_categories(self):
        """测试检查分类"""
        checklist = ReleaseChecklist()
        categories = set(c.category for c in checklist.checks)

        expected_categories = [
            "code_quality",
            "security",
            "testing",
            "build",
            "configuration",
            "documentation",
            "release",
        ]
        for cat in expected_categories:
            assert cat in categories, f"缺少分类: {cat}"

    def test_check_severity_levels(self):
        """测试检查严重级别"""
        checklist = ReleaseChecklist()
        severities = set(c.severity for c in checklist.checks)

        # 应该有不同级别的检查
        assert "critical" in severities
        assert "high" in severities
        assert "medium" in severities


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
