"""日志仪表盘 — get_introspection 单例测试"""
from agent.log_system.dashboard import get_introspection


class TestDashboard:
    """仪表盘功能测试"""

    def test_get_introspection_singleton(self):
        eng1 = get_introspection()
        eng2 = get_introspection()
        assert eng1 is eng2
