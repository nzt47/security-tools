"""PeriodicSampler 周期采样器单元测试（agent/utils/periodic_sampler.py）。

覆盖（防退化）：
- rate=1.0 全量；0<rate<1 按周期确定性抽样（结果可复现，非随机）；
- 越界 rate 钳制；period 属性。
"""

from agent.utils.periodic_sampler import PeriodicSampler


class TestPeriodicSampler:
    def test_rate_one_samples_every_call(self):
        s = PeriodicSampler(1.0)
        assert all(s.should_sample() for _ in range(5))

    def test_rate_point_one_period_ten(self):
        s = PeriodicSampler(0.1)
        got = [s.should_sample() for _ in range(25)]
        assert got.count(True) == 3  # 第 0/10/20 次
        assert got[0] is True and got[9] is False and got[10] is True

    def test_rate_clamped_to_valid_range(self):
        assert PeriodicSampler(0.0).rate == 1e-6
        assert PeriodicSampler(5.0).rate == 1.0

    def test_period_property(self):
        assert PeriodicSampler(0.1).period == 10
        assert PeriodicSampler(0.5).period == 2
        assert PeriodicSampler(1.0).period == 1

    def test_rate_point_three_period_three(self):
        s = PeriodicSampler(0.3)
        assert s.period == 3
        got = [s.should_sample() for _ in range(6)]
        assert got == [True, False, False, True, False, False]
