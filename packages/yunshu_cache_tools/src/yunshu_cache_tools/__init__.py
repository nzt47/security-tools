"""yunshu-cache-tools：云枢通用缓存与采样工具集。

提供两个零第三方依赖的组件：
- LinkCache：预计算卡片双链解析缓存（快照式，查询零文件 I/O）
- PeriodicSampler：按调用次数周期确定性采样（多线程安全）
"""

from yunshu_cache_tools.link_cache import ARCHIVES_PREFIX, CardLike, LinkCache, resolve_slug
from yunshu_cache_tools.periodic_sampler import PeriodicSampler

__version__ = "0.1.0"

__all__ = [
    "LinkCache",
    "PeriodicSampler",
    "CardLike",
    "resolve_slug",
    "ARCHIVES_PREFIX",
    "__version__",
]
