"""D7 复现测试：规划引擎未接入主链路（"已建未用"）

缺陷（P1）：orchestrator 聊天主链路仅追加规划引擎工具列表文本（get_stats），
从未调用 _planner.chat()；生产配置 config.yaml 中 planning.enabled: false。

预期失败：重构完成后 planning.enabled 应为 true（接入生产配置）
→ 当前 false → 断言失败即复现成功。
"""
import os

import pytest
import yaml


class TestDefectD7:
    """D7：规划引擎应接入生产配置"""

    @pytest.mark.xfail(reason="已知缺陷 D7：生产配置未启用规划（缺陷看门狗，修复后移除 xfail）", strict=False)
    def test_planning_enabled_in_production_config(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(repo_root, "config.yaml"), encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        # 目标行为：重构完成后规划引擎应接入生产配置（enabled=true）
        assert cfg.get("planning", {}).get("enabled") is True
