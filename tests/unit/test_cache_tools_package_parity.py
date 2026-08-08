"""yunshu-cache-tools 独立包与 agent 内部实现的行为一致性（parity）测试。

【不易】护城河：packages/yunshu_cache_tools 是提取发布的独立 PyPI 包，
agent 内部实现（agent/knowledge/link_cache.py + agent/utils/periodic_sampler.py）
保留为上游参考实现。两者若漂移，本文件用例立即失败：

  - LinkCache：同一份卡片数据集 → 逐卡 expanded_links 结果完全相等
    （含断链/归档→None 的容错语义）
  - PeriodicSampler：同一 rate → 相同调用次数下采样布尔序列完全相等
  - 静态约束：独立包源码零 agent.* 依赖（防把耦合悄悄塞回包内）
  - 独立可导入：无 agent 导入环境下包可独立构建缓存（其他项目零感知）
"""

import logging
import sys
from pathlib import Path

import pytest

# 抑制 agent 端 resolve_link INFO 日志（构建缓存时的解析明细非本测试关注点）。
logging.getLogger("agent.knowledge.links").setLevel(logging.WARNING)

# 【变易】monorepo 内测试直接消费源码目录；发布场景（pip 安装后）模块已在
# sys.path，此处 insert 是幂等 no-op。
_PKG_SRC = Path(__file__).resolve().parent.parent.parent / "packages" / "yunshu_cache_tools" / "src"
if str(_PKG_SRC) not in sys.path:
    sys.path.insert(0, str(_PKG_SRC))

import yunshu_cache_tools
from agent.knowledge import LinkCache as AgentLinkCache
from agent.utils.periodic_sampler import PeriodicSampler as AgentPeriodicSampler
from yunshu_cache_tools import LinkCache as PkgLinkCache
from yunshu_cache_tools import PeriodicSampler as PkgPeriodicSampler

from agent.knowledge import Card


def _card(title, slug=None, links=None, status="current", type="concepts", content=""):
    """构造合法卡片（slug 默认等于 title，通过 schema 校验）。"""
    return Card(
        title=title,
        slug=slug or title,
        status=status,
        type=type,
        source="parity-test",
        date="2026-08-07",
        content=content,
        insight=f"{title} 的核心洞见",
        links=links or [],
    )


@pytest.fixture(scope="module")
def parity_cards() -> dict:
    """复杂链接图数据集：正常链 / 断链 / 归档 / 快照外新增目标 全覆盖。"""
    cards = [
        _card("a", slug="a", links=["b", "c"]),
        _card("b", slug="b", links=["a"]),                    # 互引
        _card("c", slug="c", links=["b", "archives/old"]),    # 归档链
        _card("d", slug="d", links=["ghost"]),                # 断链
        _card("e", slug="e", links=["a", "ghost", "b"]),      # 混合
        _card("f", slug="f", links=["a", "a"]),               # 重复目标
        _card("g", slug="g", links=["archives/old", "ghost"]),  # 全失效
        _card("h", slug="h", links=[]),                       # 无链
    ]
    return {c.slug: c for c in cards}


class TestLinkCacheParity:
    def test_expanded_links_identical(self, parity_cards):
        agent_cache = AgentLinkCache(parity_cards)
        pkg_cache = PkgLinkCache(parity_cards)
        assert agent_cache.size == pkg_cache.size == len(parity_cards)
        # total_links 为独立包扩展属性（agent 内部实现无此属性，仅校验包侧）。
        assert pkg_cache.total_links == sum(len(c.links) for c in parity_cards.values())
        for slug in parity_cards:
            assert pkg_cache.expanded_links(slug) == agent_cache.expanded_links(slug), f"卡 {slug} 双链解析结果漂移"

    def test_none_semantics_broken_and_archived(self, parity_cards):
        """断链/归档/快照外目标一律解析为 None（容错语义等价）。"""
        pkg_cache = PkgLinkCache(parity_cards)
        resolved = dict(pkg_cache.expanded_links("d"))  # d -> [ghost]
        assert resolved == {"ghost": None}
        resolved_g = dict(pkg_cache.expanded_links("g"))
        assert resolved_g == {"archives/old": None, "ghost": None}

    def test_duplicate_target_preserved(self, parity_cards):
        """重复目标保留原始顺序与条数（不合并，与 agent 一致）。"""
        pkg_cache = PkgLinkCache(parity_cards)
        assert pkg_cache.expanded_links("f") == [("a", "a"), ("a", "a")]

    def test_unknown_seed_returns_empty(self, parity_cards):
        pkg_cache = PkgLinkCache(parity_cards)
        assert pkg_cache.expanded_links("not-exist") == []


class TestPeriodicSamplerParity:
    @pytest.mark.parametrize("rate", [1.0, 0.1, 0.3])
    def test_sampling_sequence_identical(self, rate):
        agent_sampler = AgentPeriodicSampler(rate)
        pkg_sampler = PkgPeriodicSampler(rate)
        assert pkg_sampler.period == agent_sampler.period
        agent_seq = [agent_sampler.should_sample() for _ in range(40)]
        pkg_seq = [pkg_sampler.should_sample() for _ in range(40)]
        assert pkg_seq == agent_seq


class TestPackageIndependence:
    def test_source_has_no_agent_import(self):
        """静态约束：独立包源码不得出现 agent.* 导入（防耦合回渗）。"""
        pkg_root = _PKG_SRC / "yunshu_cache_tools"
        for py_file in pkg_root.glob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            assert "import agent" not in text, f"{py_file.name} 混入 agent 依赖"
            assert "from agent" not in text, f"{py_file.name} 混入 agent 依赖"

    def test_importable_without_agent(self):
        """仅靠标准库即可导入并构建缓存（其他项目零感知云枢知识层）。"""
        import subprocess
        import sys as _sys

        code = (
            "import sys; sys.path.insert(0, r'%s');\n"
            "from yunshu_cache_tools import LinkCache, PeriodicSampler;\n"
            "from types import SimpleNamespace;\n"
            "cards = {'x': SimpleNamespace(slug='x', links=['y']), "
            "'y': SimpleNamespace(slug='y', links=[])};\n"
            "cache = LinkCache(cards);\n"
            "assert cache.expanded_links('x') == [('y', 'y')];\n"
            "s = PeriodicSampler(rate=1.0);\n"
            "assert [s.should_sample() for _ in range(3)] == [True, True, True];\n"
            "print('PACKAGE_STANDALONE_OK')"
        ) % (_PKG_SRC,)
        result = subprocess.run(
            [_sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, f"独立导入失败: {result.stderr}"
        assert "PACKAGE_STANDALONE_OK" in result.stdout

    def test_version_exposed(self):
        assert isinstance(yunshu_cache_tools.__version__, str)
        assert "LinkCache" in yunshu_cache_tools.__all__
        assert "PeriodicSampler" in yunshu_cache_tools.__all__
