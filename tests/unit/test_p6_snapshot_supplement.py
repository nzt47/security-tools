"""P6 Snapshot 补充测试"""
import pytest
from agent.p6_snapshot import StateSnapshotManager


class TestP6SnapshotSupplement:
    """P6 StateSnapshot 补充测试"""

    def test_import_snapshot_manager(self):
        assert StateSnapshotManager is not None

    def test_create_manager(self):
        mgr = StateSnapshotManager()
        assert mgr is not None
