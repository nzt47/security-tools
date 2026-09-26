"""工具混合检索器 — BM25 + Embedding 双路融合

【不易】
  - 复用 tool_router.TOOL_ALIASES 合并 + 优先级去重 + 25 上限逻辑
    (通过 _apply_alias_merge_and_priority_sort helper)
  - 复用 memory/vector_store 的 HAS_SENTENCE_TRANSFORMERS 延迟检测机制
  - 任何异常都返回 None,让调用方回退到 get_tools_for_input(关键词分类)
  - 不破坏 workflow_learning/matcher.py 的 TF-IDF 索引(独立模块)
【变易】
  - alpha 可配,默认 0.5(BM25 与 Embedding 等权)
  - 索引重建:工具 YAML 变更时,通过 sync_tool_index.py 重生成 tool_index.json,
    HybridRetriever 重新加载即可
【简易】
  - EmbeddingIndex 用 SentenceTransformer 直连,内存存 numpy 数组(80×384≈122KB)
    偏离字面「复用 VectorStore」:VectorStore.search() 不返回分数(融合必需),
    .add() 自动生成 mem_ID 不支持工具名作主键。复用其延迟检测机制即可。
  - 降级链清晰:Hybrid → 纯 BM25 → None(调用方回退到关键词分类)

性能预算(80 工具):
  - 模型加载 ~2-3 秒(后台 daemon thread,不阻塞)
  - Query 编码 ~10-20ms + BM25 <1ms + 余弦相似度 <1ms = <25ms(满足 50ms)

原生崩溃隔离:
  - torch/SentenceTransformer 在部分环境(Windows 0xC0000005 / Linux SIGILL)
    加载模型时会触发原生访问违规,Python try/except 无法捕获。
  - 解决方案:子进程探测 + 结果缓存。探测在子进程运行,崩溃不影响主进程。
  - 探测结果缓存到 data/.embedding_probe(含 probed_at 时间戳,**带 TTL**,
    见 _PROBE_CACHE_TTL),未过期的后续启动直接读取,过期则重新探测 ——
    缓存只代表"最近一次探测的结论",不代表永久结论。
"""

from __future__ import annotations

import os
import re
import sys
import json
import math
import time
import atexit
import logging
import weakref
import subprocess
import threading
from typing import Optional
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  路径与默认配置
# ════════════════════════════════════════════════════════════

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INDEX_PATH = os.path.join(_PROJECT_ROOT, "data", "tool_index.json")
_PROBE_CACHE = os.path.join(_PROJECT_ROOT, "data", ".embedding_probe")

# 与 memory/vector_store/vector_store.py L277 一致(多语言 MiniLM,384 维)
_DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

_DEFAULT_ALPHA = 0.5      # BM25 与 Embedding 等权
_DEFAULT_TOP_K = 40       # 默认候选池大小（**不是**最终返回数）
# 【不易】为什么从 10 提到 40：`top_k` 是**检索候选池**，最终返回数由
#         `max_tools`（默认 25）+ 类别优先级排序 + PINNED_TOOLS 补回决定。
#         原值 10 < max_tools 25 ⇒ 截断分支**永不触发**，返回的永远是检索器给的
#         ≤10 个，`_apply_alias_merge_and_priority_sort` 的优先级排序等同虚设。
#         实测后果：复合请求里除检索命中者外一个工具都补不进来。
#         候选池必须 ≥ max_tools，排序与截断才有意义。
_COSINE_CUTOFF = 0.2      # Embedding 余弦剪枝阈值(低于此值不进入融合)；**同时是余弦路的校准跨度参数**（见下）

# ── 【W5/L27】融合分数的"校准参考尺度"（查询无关常量，不是 per-query 极值）──
# 【不易】它必须是**查询无关**的。旧融合走 `_min_max_normalize`：每路的 max 被
#         强映射为 1.0、min 被强映射为 0.0 ⇒ 只要某路给出了候选，该路 top1 恒为 1.0。
#         实测（TASK-10，2026-09-21）`fused_top_scores=[1.0]`：这条分数上做 ECE /
#         阈值拒识**等价于"永不拒识"**，语义层的"可验证可靠性"结构性不可实现。
#         参考尺度一旦随查询漂移（min/max 就是），分数便不可跨查询比较，
#         ECE 分箱与拒识阈值同时失去意义 ⇒ 必须换成常量。
# 【适用域·W5 复核 A5】上述"恒为 1.0"的读数取自 **BM25-only 降级路**
#         （`semantic_layer_status`：degraded_bm25_only=true /
#         embedding_available=false / worker_alive=false）。**真混合（α 融合）路在
#         本次改动前没有任何已提交证据**。故本改动只声明：降级路的融合分已可校准；
#         α 路仅由受控 Embedding 桩验证了融合公式与权重语义（见新增单测），
#         不声明其 ECE 已可用。
# 【变易·W5 复核 A2/A3】取值可复算（口径 = **CJK 相邻二元组 + 对数 idf**，
#         且**只用 calib 划分**、不含 test 划分 ⇒ 不构成 ECE 泄漏）：
#           证据：eval/routing_baseline/bm25_raw_top1.json（受跟踪产物，逐条 raw BM25 top1）
#                 stats.calib_all_layers = n=11 / p10=3.0472 / p50=5.5375 /
#                 p90=10.9457 / max=11.408；calib 划分 33 条、test 42 条（与 report.json 一致）
#           取中位数为"半饱和点"⇒ 典型查询的 BM25 校准分落在 0.5 附近，
#           接近 1.0 只留给显著高于基线的证据。
#           自检：tests/unit/test_tool_router_hybrid_fusion_calibration.py::TestS0Provenance
#                 （每次运行重算 n/p10/p50/p90/max 并与产物比对；分词/idf/索引一变即红）
# 【锚点不一致·W5 复核附注】两路的 0.5 锚点**来源不同**：BM25 路锚在"数据导出的
#         中位数"，余弦路锚在人工阈值 `_COSINE_CUTOFF`（cos=0.6 时 p=0.5）。
#         因此 `alpha=0.5` 是**融合权重**等权，**不等价于两路同等置信** ——
#         不得把它表述为"原理性等权"。
# 【简易】p = s / (s + S0) 单调、有界、保留 raw 量纲；比 min-max 多一个常量、零依赖。
_BM25_HALF_SATURATION = 5.5375

# ── 【W5/L28-A8】候选的"idf 证据下限"（查询相对，不是绝对分阈值）──
# Why（对数 idf 的必要补充）: 取对数后，稀有词的权重优势被压缩（df=1 的 59.67 → 4.09），
#     这正是修 L28 所需要的；但同一枚硬币的另一面是**常见词变得有竞争力**——
#     只命中一个高 df 查询词的文档会挤进 top-5（实测负样本
#     G9 q22「检索 lifetrace 中的历史对话」：search_memory 仅命中"检索"即列第 4，
#     而它并非该查询的目标工具）。旧式未取对数的 idf 是靠"稀有词压倒一切"隐式挡住它的，
#     取消该隐式先验后，必须**显式**补一条下限。
# 【变易】判据 = 候选命中的查询词 idf 质量 / 全部命中索引的查询词 idf 质量。
#     该比值与文档长度、绝对分尺度无关，只问"这条候选覆盖了查询的多少证据"。
# 【C3 可回滚·单点关闭】`_MIN_IDF_COVERAGE = 0.0` **即等价于不启用本护栏**
#     （代码里按 `> 0.0` 判定，0.0 时整段过滤被跳过，行为与"无护栏"逐位相同）。
#     这是本波次第三处机制，必须能被单独撤销而不牵扯 idf/分词/校准任一处。
# 【简易】0.2 的取值窗口（实测，2026-09-21）：
#     泄漏项 search_memory 的覆盖 = 0.159，期望项 search_lifetrace = 0.233
#     ⇒ 可用区间 (0.159, 0.233)，宽 1.47×。**窗口窄是已知脆弱点**：
#     0.3 会把期望项本身滤掉（召回缺失，q22 反而失败），0.4/0.5 会连带滤掉
#     q03/q04 的目标工具。因此它只作为"最低证据"护栏，不得当作排序主信号。
_MIN_IDF_COVERAGE = 0.2
_PROBE_TIMEOUT = 60       # 子进程探测超时(秒)

# worker ready 信号读取超时(秒) —— **可配置**，代码级默认 120.0
#
# 【E1-F1-A/优先级 3：30 → 120 的取值理由（不是拍脑袋）】
#   E1-F1 实测（docs/audit_skill_governance/E1-F1.md）：走生产函数 `_ensure_worker()`、
#   生产默认超时、未抬任何常量的前提下，向量腿起来耗时 **23.2 s**（其中模型加载 4.0 s）。
#   也就是说旧默认 30 s 的余量只有 23.2/30 = **1.29×** —— 任何一次冷页缓存、磁盘抖动
#   或并发争抢都可能把这次启动推过线，而**过线的后果是静默降级为 BM25-only**（不是报错）。
#   1.29× 的余量，对一个「过线即静默失去能力」的判定来说太薄。
#   120 s 的选法：① ≥ E1-F1 实测就绪时间的 5×；② 仍远小于「在线重试整条链」的量级
#   （E1-F1 实测在线路径 >300 s~>600 s 重尾）—— 即它**不是**用来兜住在线路径的
#   （抬超时已被 E1 三次实验证伪为无效修法，那是优先级 2「缓存优先」的职责）；
#   ③ 它只在**预热线程**里生效，不在请求路径上 ⇒ 拉长它不增加任何一次查询的时延。
#
# 【变易】它是启动等待的**唯一数值来源**:EmbeddingIndex._WORKER_STARTUP_TIMEOUT
#         只是它的别名(历史上那处独立写着 60,从未被读取,与实际生效的值不一致)。
#         环境变量 AGENT_HYBRID_WORKER_READY_TIMEOUT 可覆盖（已在
#         agent/settings/registry.py 登记为 A 级；**导入时读取一次** ⇒ needs_restart）。
def _resolve_worker_ready_timeout_from_env() -> float:
    """解析 worker 就绪超时:环境变量 AGENT_HYBRID_WORKER_READY_TIMEOUT 覆盖,非法值回退默认

    Why: E1-F1-A 实测余量仅 1.29×(23.2 s / 30 s),而慢机/冷缓存下这个余量会被吃掉;
        把判定上限收敛成一个**可配置数值**,让运维能在不改代码的前提下放宽,
        而不是让「降级为 BM25-only」这条静默路径由硬编码常数单方面决定。
    口径:非法/非正数一律回退代码级默认并留 WARNING(不静默接受垃圾值)。
    """
    raw = os.environ.get("AGENT_HYBRID_WORKER_READY_TIMEOUT", "").strip()
    if not raw:
        return _WORKER_READY_TIMEOUT_DEFAULT
    try:
        val = float(raw)
    except ValueError:
        logger.warning(
            "[tool_router_hybrid] AGENT_HYBRID_WORKER_READY_TIMEOUT=%r 非数字,回退 %.1f",
            raw, _WORKER_READY_TIMEOUT_DEFAULT,
        )
        return _WORKER_READY_TIMEOUT_DEFAULT
    if val <= 0:
        logger.warning(
            "[tool_router_hybrid] AGENT_HYBRID_WORKER_READY_TIMEOUT=%r 非正数,回退 %.1f",
            raw, _WORKER_READY_TIMEOUT_DEFAULT,
        )
        return _WORKER_READY_TIMEOUT_DEFAULT
    return val


#: 代码级默认值（env 未设/非法时生效）。注册表登记的就是这个值。
_WORKER_READY_TIMEOUT_DEFAULT = 120.0
_WORKER_READY_TIMEOUT = _resolve_worker_ready_timeout_from_env()
# 单次 encode 响应读取超时(秒)
# 【变易】此处模型已加载完毕,等待的只剩一次批量编码(索引重建时 ≤ 全部工具描述,
#         实测 query 编码 10-20ms 量级)⇒ 30s ≈ 数十倍余量,慢机不误杀,
#         而 encode 死锁(worker 卡住不再回包)不会再让检索链路永久挂起。
_WORKER_ENCODE_TIMEOUT = 30.0

# ── 【W2/TASK-03 新增】embedding worker 崩溃后的退避重启 ────────────────
# 【不易】为什么**必须**有上限:worker 的原生崩溃(0xC0000005 / SIGILL)源于进程内
#         的原生 DLL 加载/地址空间冲突,重启**不一定**能修好。无上限重启 =
#         崩溃-重启风暴(2026-09-19 实测一轮 9 次服务重启)。故以三次为限,
#         退避按 5s→10s→20s 递增;用尽后**明确放弃**并保持 BM25-only。
# 【变易】三个数值均可调;当前退避与剩余次数会如实出现在
#         embedding.worker.* 日志与 EmbeddingIndex.worker_health() 出口里。
_WORKER_MAX_RESTARTS = 3
_WORKER_RESTART_BACKOFF_BASE_SEC = 5.0
_WORKER_RESTART_BACKOFF_MAX_SEC = 300.0

# 探测结果缓存的有效期(秒)
# 【变易】为什么需要 TTL:探测结果是**能力快照**,不是永久事实。原实现只读
#         bool(data["available"])、完全忽略 probed_at ⇒ 一次负结果被永久固化
#         (实测 data/.embedding_probe 里 available=false 写于 2026-07-23,之后
#         再没被复核过),依赖装好了/探测当时瞬时失败,Embedding 都不会再启用 ——
#         这是一次静默的能力损失,而且日志上看不出任何异常。
# 【不易】正结果同样要过期:Env 会漂移(依赖被卸载/模型缓存被清/磁盘满),
#         陈旧的正结果会让 _ensure_st_checked() 继续放行,真正的问题推迟到
#         EmbeddingIndex 起 worker 时才暴露,反而更难定位。缓存应表达
#         "最近一次探测如此",而不是"一直如此"。
# 【简易】7 天:探测本身很贵(要起解释器并加载模型,上限 _PROBE_TIMEOUT=60s),
#         TTL 太短会把冷启动成本摊到每次进程启动上;7 天把开销压到
#         "每进程最多一次 / 每周一次",又保证能力最多滞后一周被复核。
#         进程内 _PROBE_RESULT 仍会短路重复探测 ⇒ 不会出现重探风暴。
_PROBE_CACHE_TTL = 7 * 24 * 3600.0


def _resolve_alpha_from_env() -> float:
    """解析融合权重:环境变量 AGENT_HYBRID_ALPHA 覆盖,非法值回退默认 0.5

    Why: 生产配置统一走 .env(守项目配置契约),代码不硬编码运行时值。
         alpha ∈ [0,1]:0=纯语义,1=纯字面,0.5=等权。
    """
    raw = os.environ.get("AGENT_HYBRID_ALPHA", "").strip()
    if not raw:
        return _DEFAULT_ALPHA
    try:
        val = float(raw)
    except ValueError:
        logger.warning(
            "[tool_router_hybrid] AGENT_HYBRID_ALPHA=%r 非数字,回退 %.1f",
            raw, _DEFAULT_ALPHA,
        )
        return _DEFAULT_ALPHA
    if not (0.0 <= val <= 1.0):
        logger.warning(
            "[tool_router_hybrid] AGENT_HYBRID_ALPHA=%r 超出 [0,1],回退 %.1f",
            raw, _DEFAULT_ALPHA,
        )
        return _DEFAULT_ALPHA
    return val

# 原生崩溃退出码(用于诊断 Embedding 子进程崩溃原因)
_WIN_ACCESS_VIOLATION = -1073741819   # 0xC0000005
_WIN_STACK_OVERFLOW = -1073741571     # 0xC00000FD
_WIN_ILLEGAL_INSTRUCTION = -1073741795 # 0xC000001D
_LINUX_SIGSEGV = -11
_LINUX_SIGILL = -4


def _diagnose_crash(returncode: Optional[int]) -> str:
    """根据子进程退出码诊断原生崩溃原因

    Why: 0xC0000005 / SIGILL 等原生崩溃不会抛 Python 异常,
        只能通过 returncode 识别。诊断信息写入日志便于排查。
    """
    if returncode is None or returncode == 0:
        return ""
    if returncode == _WIN_ACCESS_VIOLATION:
        return ("Windows ACCESS_VIOLATION (0xC0000005) - 原生内存访问违规,"
                "常见于 PyTorch C 扩展或 SentenceTransformer 加载大模型")
    if returncode == _WIN_STACK_OVERFLOW:
        return "Windows STACK_OVERFLOW (0xC00000FD) - 栈溢出,常见于递归过深"
    if returncode == _WIN_ILLEGAL_INSTRUCTION:
        return ("Windows ILLEGAL_INSTRUCTION (0xC000001D) - 非法指令,"
                "常见于 CPU 不支持 AVX/AVX2")
    if returncode == _LINUX_SIGSEGV:
        return "Linux SIGSEGV - 段错误,常见于 PyTorch C 扩展内存访问违规"
    if returncode == _LINUX_SIGILL:
        return "Linux SIGILL - 非法指令,常见于 CPU 不支持 AVX/AVX2"
    return f"未知退出码: {returncode}"


class _ReadlineTimedOut:
    """stdout.readline() 超时哨兵值"""


_READLINE_TIMED_OUT = _ReadlineTimedOut()


def _readline_with_timeout(stream, timeout: float) -> str | _ReadlineTimedOut:
    """带超时的 stdout.readline()。

    Windows 上 select 不支持普通管道,无法用 select 实现超时读,
    故用 daemon 线程 + join(timeout):
      - reader 线程阻塞 readline()
      - 主线程等待 timeout 秒,超时返回哨兵 _READLINE_TIMED_OUT
    """
    result: dict = {}

    def _reader():
        try:
            result["line"] = stream.readline()
        except Exception as e:
            result["error"] = e

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return _READLINE_TIMED_OUT
    if "error" in result:
        raise result["error"]
    return result.get("line")

# 安全导入 ToolTraceRecorder(不可用时降级)
try:
    from agent.observability.tool_trace import ToolTraceRecorder
except ImportError:
    ToolTraceRecorder = None  # type: ignore[assignment]

# 安全导入 helper(不可用时 hybrid 不可用)
try:
    from agent.tool_router import (
        _apply_alias_merge_and_priority_sort,
        TOOL_CATEGORIES,
        classify_user_input as _classify_user_input,
    )
    _HELPER_AVAILABLE = True
except ImportError:
    _HELPER_AVAILABLE = False
    _apply_alias_merge_and_priority_sort = None  # type: ignore[assignment]
    TOOL_CATEGORIES = {}  # type: ignore[assignment]

    def _classify_user_input(_text: str) -> set:  # type: ignore[misc]
        """classify_user_input 不可用时的空实现（类别兜底降级为"无候选"）"""
        return set()

# 安全导入 numpy(EmbeddingIndex 必需)
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


# ════════════════════════════════════════════════════════════
#  SentenceTransformer 可用性探测(子进程隔离 + 结果缓存)
# ════════════════════════════════════════════════════════════

# 模块级状态:None=未探测, True=可用, False=不可用
_PROBE_RESULT: Optional[bool] = None
_PROBE_LOCK = threading.Lock()


def _read_probe_cache() -> Optional[bool]:
    """读取持久化的探测结果缓存(带 TTL 校验,过期或无法判定新鲜度即视为未命中)

    返回 None 表示"没有可信的缓存",调用方(_ensure_st_checked)会重新探测并
    用 _write_probe_cache 覆盖为新鲜值。

    Why(为什么必须校验 probed_at,而不是直接信任 available):
        探测结果是能力快照。原实现忽略 probed_at ⇒ 一次负结果被永久固化,
        依赖后来装好了也不会重新探测,Embedding 被**静默**永久禁用;
        正结果同样会随环境漂移失效。故只信任"足够新、且时间戳可解释"的记录。

    【不易】缺 probed_at / 非数字 / 时间戳在未来的旧格式文件一律判为过期:
            既然无法证明它是"最近一次探测",就不能拿它当证据 —— 宁可贵一次
            (进程内 _PROBE_RESULT 保证同进程只重探一次),不可错一辈子。
    """
    try:
        if os.path.exists(_PROBE_CACHE):
            with open(_PROBE_CACHE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "available" in data:
                probed_at = data.get("probed_at")
                # bool 是 int 的子类,必须显式排除(probed_at: true/false 不是时间戳)
                if (not isinstance(probed_at, (int, float))
                        or isinstance(probed_at, bool)):
                    logger.info(
                        "[tool_router_hybrid] 探测缓存缺少可用的 probed_at(%r),"
                        "视为过期并重新探测", probed_at,
                    )
                    return None
                age_sec = time.time() - float(probed_at)
                # 负 age = 时间戳在未来(时钟回拨或文件被手改)⇒ 不可信
                if age_sec < 0 or age_sec > _PROBE_CACHE_TTL:
                    logger.info(
                        "[tool_router_hybrid] 探测缓存已过期(age=%.1fs, ttl=%.0fs),"
                        "重新探测", age_sec, _PROBE_CACHE_TTL,
                    )
                    return None
                return bool(data["available"])
    except Exception:
        pass
    return None


def _write_probe_cache(available: bool) -> None:
    """写入探测结果缓存"""
    try:
        os.makedirs(os.path.dirname(_PROBE_CACHE), exist_ok=True)
        with open(_PROBE_CACHE, "w", encoding="utf-8") as f:
            json.dump({"available": available, "probed_at": time.time()}, f)
    except Exception:
        pass


def _run_embedding_probe(model_name: str) -> bool:
    """在子进程中探测 SentenceTransformer 模型加载是否安全

    Why: torch 在部分环境(Windows 0xC0000005 / Linux SIGILL)加载模型时
         触发原生访问违规,Python try/except 无法捕获,会终止整个进程。
         子进程隔离确保主进程不受影响。

    Returns:
        True=模型可安全加载; False=加载失败或崩溃
    """
    probe_script = (
        "import sys; "
        f"from sentence_transformers import SentenceTransformer; "
        f"m = SentenceTransformer({model_name!r}); "
        "m.encode(['probe test']); "
        "print('PROBE_OK')"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe_script],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
            cwd=_PROJECT_ROOT,
        )
        if result.returncode == 0 and "PROBE_OK" in (result.stdout or ""):
            return True
        # 非零退出(含崩溃)或输出不含 PROBE_OK
        logger.warning(
            "[tool_router_hybrid] Embedding 探测失败(退出码 %d): %s",
            result.returncode,
            (result.stderr or "")[:200],
        )
        return False
    except subprocess.TimeoutExpired:
        logger.warning("[tool_router_hybrid] Embedding 探测超时(%ds)", _PROBE_TIMEOUT)
        return False
    except Exception as e:
        logger.warning("[tool_router_hybrid] Embedding 探测异常: %s", e)
        return False


def _resolve_embedding_env_override() -> Optional[bool]:
    """AGENT_HYBRID_EMBEDDING 的**唯一**解析口 ⇒ True(强制启用) / False(关闭) / None(未表态)

    【E1-D：为什么必须收成一个函数】
      这个 env 原先有**两处各判一次**的读点：
        · `_ensure_st_checked()` 里的"0=禁用 / 1=强制启用"分支 —— 而该函数
          **全仓无任何生产调用点**（Q3 §11 / E1-F1 已证），于是"能关掉向量腿"这条
          声明是一条**没有调用点的假通路**：登记表据此把它写成"向量模型"、默认 ""，
          而代码事实是布尔开关 —— 一个看着权威、语义却说反了的旋钮；
        · `HybridRetriever.__init__` 里的预热 gate —— 这才是真正生效的那一处。
      两处各写一份判断，就是"同一语义两份实现"的温床（改一处漏一处 ⇒ 声明与事实漂移）。
      收成一个函数后：**生产路径调它**（下方 HybridRetriever.__init__），
      `_ensure_st_checked()` 也调它 ⇒ 「0 = 关」这条语义不再是无调用点的死分支，
      而是生产路径上唯一实现的一份事实（由 tests/unit/test_settings_registry_e1d.py
      与 tests/unit/test_tool_router_hybrid_e1d_determinism.py 双向钉住）。

    【关到什么程度（写给运维，与注册表描述同源）】
      False ⇒ HybridRetriever 构造时**不启动 preheat 子进程** ⇒ 本进程里向量 worker
      从不被拉起，retriever.degraded=True、检索退化为纯 BM25（下发集相应变小）；
      但这**不是硬禁用**：`EmbeddingIndex.search/preheat` 内部会 `_ensure_worker()`，
      任何直接调用它们的路径仍会把向量腿拉起来。
      认下的写法：0/false/no/off ⇒ False；1/true/yes/on ⇒ True；
      其余（未设置 / 空串 / 历史上被误填成的模型名）⇒ None（交回探针与默认路径）。
    """
    env_val = os.environ.get("AGENT_HYBRID_EMBEDDING", "").strip().lower()
    if env_val in ("0", "false", "no", "off"):
        return False
    if env_val in ("1", "true", "yes", "on"):
        return True
    return None


def _ensure_st_checked() -> bool:
    """检测 sentence_transformers + 模型加载是否安全可用(子进程探测 + 缓存)

    优先级:
      1. 环境变量 AGENT_HYBRID_EMBEDDING 强制覆盖(0=禁用, 1=启用)
      2. 内存缓存(_PROBE_RESULT)
      3. 文件缓存(data/.embedding_probe,**仅在 _PROBE_CACHE_TTL 内有效**)
      4. 子进程探测(首次或缓存过期/不可信时,结果写回文件缓存)

    【E1-D · 本函数在生产路径上仍然没有调用点 —— 这是**有意保留**的，不是遗漏】
      下面的探针链（文件缓存 + 子进程探测）**不得**接进生产：`data/.embedding_probe`
      里躺着一条 2026-07-23 的陈旧 `available=false`，把它接上等于让一条过期读数
      决定向量腿生死（E1-F1 的根因形态）。生产真正的开关是
      `_resolve_embedding_env_override()`（`HybridRetriever.__init__` 调用）——
      本函数**只**复用同一个解析口，不另立一份判断。
    """
    global _PROBE_RESULT
    if _PROBE_RESULT is not None:
        return _PROBE_RESULT

    with _PROBE_LOCK:
        if _PROBE_RESULT is not None:
            return _PROBE_RESULT

        # 1. 环境变量强制覆盖（**唯一实现** _resolve_embedding_env_override：
        #    生产路径 HybridRetriever.__init__ 调的就是它 ⇒ 这条分支的语义在生产上真实生效）
        _override = _resolve_embedding_env_override()
        if _override is False:
            _PROBE_RESULT = False
            logger.info("[tool_router_hybrid] AGENT_HYBRID_EMBEDDING=0,禁用 Embedding(纯 BM25)")
            return False
        if _override is True:
            _PROBE_RESULT = True
            logger.info("[tool_router_hybrid] AGENT_HYBRID_EMBEDDING=1,强制启用 Embedding")
            return True

        # 2. 文件缓存
        cached = _read_probe_cache()
        if cached is not None:
            _PROBE_RESULT = cached
            logger.info(
                "[tool_router_hybrid] Embedding 探测结果(缓存): available=%s", cached
            )
            return cached

        # 3. 子进程探测
        logger.info("[tool_router_hybrid] 首次启动,子进程探测 Embedding 可用性...")
        available = _run_embedding_probe(_DEFAULT_MODEL)
        _PROBE_RESULT = available
        _write_probe_cache(available)
        if not available:
            logger.warning(
                "[tool_router_hybrid] Embedding 不可用,降级到纯 BM25(缓存已写入 %s)",
                _PROBE_CACHE,
            )
        return available


# ════════════════════════════════════════════════════════════
#  分词器(借鉴 workflow_learning/matcher.py:27,CJK+英文混合)
# ════════════════════════════════════════════════════════════

# 【W5/L28】CJK 段按 `+` 整段取出（再切相邻二元组），不再按单字取出
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+")


def _tokenize(text: str) -> list[str]:
    """CJK 相邻二元组 + 英文单词混合分词（2026-09-21 起，见 L28）

    Why（单字切分的实测后果）: 旧实现按**单字**切中文 ⇒ 每个汉字都是一枚完整 term。
         "解析pdf" 因此被切成 解 / 析 / pdf，而真实索引里 解 只出现在 run_lint
         一条描述中（df=1），与 idf 未取对数叠加后，run_lint 在"解析pdf"上得
         BM25=72.47 高居 top1，真正的 read_pdf 仅第 4（24.34）。
         即：查询的**相邻两字**是同一意图的碎片，却各自独立计分并被无界 idf 放大。
    【不易】用相邻二元组而不是引第三方分词器：零新依赖，且与仓库既有的同尺度实现
         一致 —— agent/skills_mgmt/loader.py（其 _tokenize 于 2026-08-12 改 bigram）
         与 agent/skills_mgmt/bm25_searcher.py:57-68 就是为修同一类
         "中文单字激进命中" 缺陷（"费马小定理证明" 误命中元技能）而改成 bigram 的。
    【变易】纯 ASCII 段仍按完整词切分（不切 bigram），故英文查询/英文描述行为不变；
         单字成段（长度 1 的 CJK 串）仍保留该单字，避免短查询被切成空。
    """
    tokens: list[str] = []
    for seg in _TOKEN_RE.findall((text or "").lower()):
        if len(seg) > 1 and not seg.isascii():
            tokens.extend(seg[i:i + 2] for i in range(len(seg) - 1))
        else:
            tokens.append(seg)
    return tokens


# ════════════════════════════════════════════════════════════
#  BM25Index — 倒排索引 + BM25 评分
# ════════════════════════════════════════════════════════════


class BM25Index:
    """BM25 倒排索引 — 索引工具 name + parameter_names + description

    【不易】BM25 算法参数 k1=1.5, b=0.75 与 vector_store.InvertedIndex 一致
    【变易】CJK+英文混合分词,支持中文工具描述检索
    【简易】纯内存倒排表,RLock 保护并发读写
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self._k1 = k1
        self._b = b
        # term -> [(doc_id, term_freq), ...]
        self._index: dict[str, list[tuple[str, int]]] = {}
        self._doc_lengths: dict[str, int] = {}  # doc_id -> token count
        self._total_docs = 0
        # 【不易·TASK-08 E1p】所有文档的 token 总数,**增量维护**
        # Why: 旧实现在 add_document / _remove_document_locked 里都执行
        #      `sum(self._doc_lengths.values())` ⇒ 单次插入 O(n)、构建 n 篇 O(n²)。
        #      实测 10,000 条时仅这一项就吃掉约 0.36s（见 scripts/bench_bm25_add_document.py）。
        #      改成累加字段后单次插入 O(1),对外行为完全不变（_avg_doc_length 的取值逐位相同）。
        # 【变易】它必须与 _doc_lengths 的增删**成对**更新；不变量由
        #      tests/unit/test_bm25_incremental_total_len.py 逐操作序列断言。
        self._total_doc_len = 0
        self._avg_doc_length = 0.0
        self._lock = threading.RLock()
        # 【W5/L28-A8 G1】最近一次 search 的召回过滤读数（供可观测性透出）
        self._last_filtered_count = 0
        self._last_considered_count = 0
        self._last_filtered_preview: list = []

    def add_document(self, doc_id: str, content: str) -> None:
        """添加文档到索引(doc_id 重复时覆盖旧文档)"""
        tokens = _tokenize(content)
        term_counts: dict[str, int] = {}
        for token in tokens:
            term_counts[token] = term_counts.get(token, 0) + 1

        with self._lock:
            # 覆盖语义:先移除旧文档(若存在)
            if doc_id in self._doc_lengths:
                self._remove_document_locked(doc_id)

            for term, freq in term_counts.items():
                if term not in self._index:
                    self._index[term] = []
                self._index[term].append((doc_id, freq))

            doc_len = len(tokens)
            self._doc_lengths[doc_id] = doc_len
            self._total_docs += 1
            # 【TASK-08 E1p】增量累加,不再 sum 全表(见 __init__ 的 _total_doc_len 注释)
            self._total_doc_len += doc_len
            self._avg_doc_length = (
                self._total_doc_len / self._total_docs if self._total_docs > 0 else 0.0
            )

    def _remove_document_locked(self, doc_id: str) -> None:
        """从索引移除文档(调用方持锁)"""
        if doc_id not in self._doc_lengths:
            return
        for term in list(self._index.keys()):
            self._index[term] = [(did, freq) for did, freq in self._index[term] if did != doc_id]
            if not self._index[term]:
                del self._index[term]
        # 【TASK-08 E1p】先按被删文档的长度**减**掉,再删记录 —— 顺序不能反
        # （反了就取不到长度,总长会永久偏大,而 _avg_doc_length 会静默算错）
        self._total_doc_len -= self._doc_lengths[doc_id]
        del self._doc_lengths[doc_id]
        self._total_docs -= 1
        if self._total_docs > 0:
            self._avg_doc_length = self._total_doc_len / self._total_docs
        else:
            # 空索引时把总长也归零:否则"删光再加"的序列会让总长带着历史残留,
            # 而 _total_docs=1 时的 _avg_doc_length 就会算成历史总和
            self._total_doc_len = 0
            self._avg_doc_length = 0.0

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """搜索查询,返回 [(doc_id, score)] 列表(按分数降序)

        【W5/L28-A8】候选先过"idf 证据下限"（`_MIN_IDF_COVERAGE`）：只命中查询里
        一个常见词的文档不再进入结果。判据是**查询相对**的（覆盖了查询多少 idf 质量），
        不是绝对分阈值，故与文档长度、分数量纲无关。
        """
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores: dict[str, float] = {}
        covered: dict[str, float] = {}
        idf_total = 0.0
        filtered_count = 0
        with self._lock:
            # 先按**唯一**查询词累计 idf 与每篇文档覆盖到的 idf 质量
            for token in set(query_tokens):
                if token not in self._index:
                    continue
                idf_of = self._term_idf(token)
                idf_total += idf_of
                for doc_id, _freq in self._index[token]:
                    if self._doc_lengths.get(doc_id, 0) > 0:
                        covered[doc_id] = covered.get(doc_id, 0.0) + idf_of
            # 打分循环保持原样（重复查询词按原语义重复累加）
            for token in query_tokens:
                if token not in self._index:
                    continue
                for doc_id, freq in self._index[token]:
                    doc_length = self._doc_lengths.get(doc_id, 0)
                    if doc_length > 0:
                        scores[doc_id] = scores.get(doc_id, 0.0) + self._compute_bm25(
                            token, freq, doc_length
                        )

        # 【C3】_MIN_IDF_COVERAGE = 0.0 ⇒ 整段跳过（单点关闭，等价于不启用护栏）
        if idf_total > 0.0 and _MIN_IDF_COVERAGE > 0.0:
            # 至少覆盖查询 idf 质量的 _MIN_IDF_COVERAGE，否则不作为候选
            kept = {
                doc_id: score for doc_id, score in scores.items()
                if covered.get(doc_id, 0.0) / idf_total >= _MIN_IDF_COVERAGE
            }
            filtered_count = len(scores) - len(kept)
            # 被滤候选的**预览**（按分降序取前 5 个 id）：只有计数看不出"被吃掉的
            # 是谁"，而护栏吃掉的很可能正是高分长尾（R4 实测里 read_pdf 就是这样被吃的）。
            dropped = sorted(((d, s) for d, s in scores.items() if d not in kept),
                             key=lambda x: x[1], reverse=True)
            self._last_filtered_preview = [d for d, _ in dropped[:5]]
            scores = kept
        else:
            self._last_filtered_preview = []

        # 【W5/L28-A8 G1】把"被下限滤掉的候选数"留在实例上，供
        #     HybridRetriever._query_locked 透出到 _last_query_stats 与路由事件。
        #     Why 必须可观测：护栏改变的是**召回集合**，窗口又只有 1.47×，
        #     一旦分数分布漂移（新增工具/改写描述/再改分词）它可能开始吃掉合法候选
        #     而完全没有信号 —— 静默的召回过滤正是本波次要根除的失效型。
        self._last_filtered_count = filtered_count
        self._last_considered_count = len(covered)

        return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

    def _term_idf(self, term: str) -> float:
        """单查询词的 idf（对数形式，**唯一实现**：打分与证据下限共用同一口径）"""
        doc_count = len(self._index.get(term, []))
        if doc_count <= 0:
            return 0.0
        return math.log(1.0 + (self._total_docs - doc_count + 0.5) / (doc_count + 0.5))

    def _compute_bm25(self, term: str, term_freq: int, doc_length: int) -> float:
        """计算 BM25 评分

        【W5/L28】idf 取对数。Robertson-Sparck-Jones 的标准形式是
            idf = log(1 + (N - df + 0.5) / (df + 0.5))
        本类原实现直接返回**未取对数的比值**，于是 df 很小时权重无界：
        N=90、df=1 ⇒ idf=59.67，是一枚只偶然出现一次的中文单字碎片的权重，
        足以压过整条真实证据链（实测 "解析pdf"：run_lint 72.47 vs read_pdf 24.34）。
        取对数后同一例降到 6.30，稀有 term 仍有优势但不再具有支配量级。
        【不易】本改动改变分数尺度 ⇒ knowledge/search.py:176 那句"与本函数一致"
        自本次起不再成立（该文件不在本任务所有权内，已在报告中登记为跨任务请求）。
        """
        if term not in self._index:
            return 0.0
        idf = self._term_idf(term)
        if idf <= 0:
            return 0.0
        numerator = term_freq * (self._k1 + 1)
        denominator = term_freq + self._k1 * (
            1 - self._b + self._b * doc_length / (self._avg_doc_length or 1)
        )
        return idf * numerator / denominator

    def clear(self) -> None:
        """清空索引"""
        with self._lock:
            self._index.clear()
            self._doc_lengths.clear()
            self._total_docs = 0
            # 【TASK-08 E1p】增量字段必须与 _doc_lengths 一起归零（漏掉这里，
            # 下一次 rebuild 的 _avg_doc_length 会带上上一轮的历史总长）
            self._total_doc_len = 0
            self._avg_doc_length = 0.0

    @property
    def total_doc_length(self) -> int:
        """所有已索引文档的 token 总数（增量维护值）

        【TASK-08 E1p 新增】只读观测口：修复前这个值由 `sum(_doc_lengths.values())`
        现算现用、没有对外句柄；改成增量字段后必须留一个可读入口，否则"增量维护的
        值是否始终等于重算值"这条不变量在运行时无法被检查（测试只能读私有字段）。
        """
        with self._lock:
            return self._total_doc_len

    @property
    def size(self) -> int:
        """已索引文档数"""
        with self._lock:
            return self._total_docs


# ════════════════════════════════════════════════════════════
#  EmbeddingIndex — 子进程隔离 + 二进制序列化 + LRU 缓存
# ════════════════════════════════════════════════════════════

# Embedding worker 脚本(子进程隔离,通过 python -c 启动)
# 【不易】JSON Lines 通信协议,encode 请求 → embeddings 响应
# 【变易】二进制序列化:base64(numpy.tobytes()) 替代 JSON float 列表(省 ~2ms/次)
_WORKER_SCRIPT_EMBEDDING = """
import json, os, sys, base64, threading
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# ── 【E1-F1-A/优先级 4】父进程存活看门狗：父进程一消失就自杀，不给系统留孤儿 ──
# 【不易·为什么必须由子进程自己做】父进程被强杀（或本进程的 daemon 预热线程随解释器
#   退出而止）时，没有任何一方会去 wait() 这个子进程；此时子进程唯一的死亡信号就是
#   "父进程没了"。而原实现把这件事交给 stdin 的 EOF —— 可 stdin 的读取在**模型加载
#   之后**，加载期间（在线 HF 重试链里可 >300 s）EOF 根本轮不到被读到 ⇒ 父进程早已
#   消失、子进程还占着 450 MB 活着。E1-F1 实测抓到 2 个这样的孤儿 EMBED worker。
# 【不易·为什么不用"后台线程读 stdin"】本卡实测（X3/X4/W1/W2）：在 `import
#   sentence_transformers`（= torch 栈）**期间**，只要有另一个线程阻塞在 stdin 管道的
#   读上，整个解释器会被卡死（>45 s 无任何输出、无任何 stderr）；而同一条读放在导入
#   完成之后则完全正常。故：
#     · 不用后台线程碰 stdin（协议行继续由主线程读，**stdin 只有一个读者**）；
#     · 看门狗改成**监视父进程句柄**（Windows: OpenProcess(SYNCHRONIZE) +
#       WaitForSingleObject(INFINITE)，父进程一死立即返回；POSIX 与兜底：轮询 ppid
#       变化），并且**在导入之后、加载之前**才启动 —— 既避开导入期死锁，又覆盖了
#       真正长的那个窗口（模型加载）。
def _start_orphan_guard():
    '''父进程消失 ⇒ os._exit(0)（在**导入之后、加载之前**调用，见上方实测说明）'''
    ppid = os.getppid()

    def _watch():
        if os.name == 'nt':
            try:
                import ctypes
                k32 = ctypes.windll.kernel32
                k32.OpenProcess.restype = ctypes.c_void_p
                k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
                k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
                k32.CloseHandle.argtypes = [ctypes.c_void_p]
                SYNCHRONIZE = 0x00100000
                INFINITE = 0xFFFFFFFF
                h = k32.OpenProcess(SYNCHRONIZE, 0, int(ppid))
                if h:
                    try:
                        k32.WaitForSingleObject(h, INFINITE)
                        os._exit(0)          # 父进程句柄已 signal ⇒ 父进程没了
                    finally:
                        k32.CloseHandle(h)
            except Exception:
                pass                             # 回退到下面的 ppid 轮询
        while True:
            try:
                if os.getppid() != ppid:
                    os._exit(0)
            except Exception:
                os._exit(0)
            import time as _t
            _t.sleep(1.0)

    threading.Thread(target=_watch, name='embedding-orphan-guard', daemon=True).start()


def _cache_candidates(model_name):
    '''模型名 → 「可能命中本地 HF 缓存的 repo id」候选表（**有序**，先命中先用）

    【不易·本函数存在的唯一理由（返工 ①，主审计实测）】
      `snapshot_download` **不会**像 `SentenceTransformer` 那样替你补命名空间：
        snapshot_download('paraphrase-multilingual-MiniLM-L12-v2', local_files_only=True)
            -> FAILED 0.00s  LocalEntryNotFoundError
        snapshot_download('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', ...)
            -> OK     0.00s  <hub>/models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2/snapshots/<sha>
      而生产默认模型名正是**裸名**（agent/tool_router_hybrid.py 的 _DEFAULT_MODEL）
      ⇒ 只查一次裸名会让缓存分支**永远抛异常**、每次都静默回退在线 ——
      即「缓存优先」写成了一句不生效的注释（返工前的实测：worker 仍 120 s 超时降级）。
    【变易】判据：
            · 名字里**带** '/' ⇒ 它本身已是 repo id，只试它自己；
            · 名字里**不带** '/' ⇒ 先试 sentence-transformers/<name>（HF 官方组织的惯例），
              再试裸名（自建/本地注册的仓库可能就是裸名）。
      两条都未命中 ⇒ 才回退在线（回退在 _load_model 里，**永远不取消**）。
    '''
    name = str(model_name or '').strip()
    if not name:
        return []
    if '/' in name:
        return [(name, 'hit_namespaced')]
    return [('sentence-transformers/' + name, 'hit_namespaced'),
            (name, 'hit_bare')]


def _load_model(model_name):
    '''加载模型（**缓存优先**）→ 返回 (model, load_source, cache_probe)

    【E1-F1-A/优先级 2】为什么必须缓存优先（根因链，E1-F1 已实测逐条成立）：
      `SentenceTransformer(model_name)` 传的是 **repo id**，huggingface_hub 会走
      **在线**解析：本机 hf-mirror.com 与 huggingface.co 两个端点 TCP 握手各 21 s
      超时（WinError 10060），且**每个文件**重试 5 次并退避 ⇒ 就绪时间进入
      >300 s~>600 s 的重尾，生产超时必然降级 ⇒ 向量腿恒为 bm25_only。
      而本机缓存**完整且瞬时可用** —— 代码却从不走缓存。
    【不易】**必须有 except 回退在线**：缓存优先一旦失败（新模型 / 缓存被清 / 首次
      部署）就永久 bm25_only，那等于把「换模型」变成「静默关掉向量腿」。
      故：本地命中 ⇒ 用本地路径；每个候选都未命中或本地加载失败 ⇒ 回退 repo id（在线）。
    【变易】cache_probe 四态，随 ready 消息一起回给父进程（父进程再透出到
      worker_health()）：
        hit_namespaced / hit_bare —— 缓存命中（并如实记录是哪条候选命中的）；
        load_failed             —— 快照目录在、但 SentenceTransformer 加载失败 ⇒ 已回退在线；
        miss                    —— 全部候选未命中（或未装 huggingface_hub）⇒ 已回退在线。
      这样「快是因为缓存命中，还是因为网络恰好通」**永远可区分**（返工要求）。
    '''
    try:
        from huggingface_hub import snapshot_download
    except Exception:
        snapshot_download = None          # 未装 huggingface_hub ⇒ 直接走在线，不算错

    cache_probe = 'miss'
    if snapshot_download is not None:
        for repo_id, probe in _cache_candidates(model_name):
            try:
                local_path = snapshot_download(repo_id=repo_id, local_files_only=True)
            except Exception:
                continue                  # 该候选未命中缓存 → 试下一个候选
            try:
                from sentence_transformers import SentenceTransformer
                return (SentenceTransformer(local_path),
                        'local_cache:' + str(local_path), probe)
            except Exception:
                # 快照目录在、但加载失败（半截缓存 / 文件损坏）⇒ 记下并继续试下一个候选，
                # 最终仍会回退在线 —— **不因为一次坏缓存就永久失去向量腿**。
                cache_probe = 'load_failed'
                continue

    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(model_name), 'online:' + str(model_name), cache_probe


def main():
    model_name = sys.argv[1] if len(sys.argv) > 1 else "paraphrase-multilingual-MiniLM-L12-v2"
    # 【不易·顺序是实测出来的，不是随手写的（本卡四次对照实验）】
    #   ① 看门狗**最先**启动（X6：进程句柄等待与 torch 导入**不冲突**，导入耗时
    #      与无看门狗对照完全一致 15.0 s）；
    #   ② 然后才导入 sentence_transformers —— 这期间**绝不能**有别的线程阻塞在
    #      stdin 的读上（X3/W1/W2：那样会把整个解释器卡死 >45 s，连 stderr 都没有）；
    #   ③ 最后加载模型（尤其回退在线时）—— 真正的长窗口，已被看门狗完全盖住。
    #   即：孤儿窗口从「整个导入 + 加载」缩到 **0**。
    _start_orphan_guard()
    try:
        import time
        t0 = time.time()
        from sentence_transformers import SentenceTransformer  # noqa: F401（随后 _load_model 复用）
        model, load_source, cache_probe = _load_model(model_name)
        load_time = time.time() - t0
        print(json.dumps({"type": "ready", "load_time_sec": round(load_time, 2),
                          "load_source": load_source,
                          "cache_probe": cache_probe}), flush=True)
    except Exception as e:
        print(json.dumps({"type": "init_failed", "error": str(e)}), flush=True)
        sys.exit(1)

    import numpy as np
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if req.get("type") == "exit":
                break
            if req.get("type") == "encode":
                texts = req.get("texts", [])
                vecs = model.encode(texts, show_progress_bar=False)
                arr = np.array(vecs, dtype=np.float32)
                # 二进制序列化:base64(bytes) 比 JSON float 列表快 ~5x
                raw_bytes = arr.tobytes()
                b64_data = base64.b64encode(raw_bytes).decode("ascii")
                print(json.dumps({
                    "type": "embeddings",
                    "data": b64_data,
                    "shape": list(arr.shape),
                    "dtype": "float32",
                }), flush=True)
        except Exception as e:
            print(json.dumps({"type": "error", "error": str(e)}), flush=True)

if __name__ == "__main__":
    main()
"""


# ── 【E1-F1-A/优先级 4】活着的 EmbeddingIndex 登记表（弱引用，不阻止 GC）──────
# 【不易】为什么要一条**代码里的**清理路径，而不是靠解释器退出：worker 是
#   `subprocess.Popen` 起的**独立进程**，父进程退出（正常或被杀）都不会替它收尸。
#   E1-F1 实测抓到 2 个孤儿 EMBED worker（父进程已消失，各 451~454 MB）——
#   根因是子进程当时卡在模型加载里，还没轮到读 stdin，EOF 也唤不醒它。
#   这里两处一起补（纵深防御，缺一不可）：
#     ① **子进程侧**：worker 在加载模型**之前**起 stdin 抽水线程，EOF 即 os._exit(0)
#        ⇒ 父进程被强杀（任务管理器结束进程 / SIGKILL）时也能自灭；
#     ② **父进程侧**：本表 + atexit ⇒ 正常退出时主动 `close()`（比等操作系统关
#        句柄更快、更确定，且能在日志里留痕）。
# 【变易】用 WeakSet：登记不延长任何实例的寿命，测试里成千上万个临时实例不会
#   被这条钩子「钉」在内存里。
_LIVE_INDEXES: "weakref.WeakSet" = weakref.WeakSet()


def _shutdown_all_workers() -> None:
    """进程退出钩子：回收所有仍活着的 embedding worker 子进程。

    Why: 不回收 ⇒ 每跑一次进程就多一个 450 MB 的常驻僵尸（E1-F1 实测 2 个）。
    安全降级：任何单个实例清理失败都不得影响其余实例，也不得抛（atexit 里抛异常
    会被解释器打印成噪声，却依然无法阻止退出）。
    """
    try:
        live = list(_LIVE_INDEXES)
    except Exception:  # pragma: no cover - WeakSet 迭代在解释器收尾期可能失败
        return
    for idx in live:
        try:
            idx.close()
        except Exception:
            pass


atexit.register(_shutdown_all_workers)


class EmbeddingIndex:
    """子进程隔离的 SentenceTransformer 语义索引

    【不易】模型加载失败时 available=False,hybrid 降级到纯 BM25
    【变易】子进程隔离:避免 SentenceTransformer 原生崩溃(0xC0000005/SIGILL)影响主进程
    【变易】二进制序列化:base64+numpy.tobytes() 替代 JSON float 列表(省 ~2ms/次)
    【变易】query embedding LRU 缓存:重复查询跳过子进程通信
    【简易】Worker 只负责 encode,主进程存 numpy 数组 + 计算 cosine similarity
    """

    # worker 启动(ready 信号)读取上限。
    # 【不易】此处历史上独立写死 60,而读取点用的是模块级 _WORKER_READY_TIMEOUT=30:
    #        两个数不一致,且 60 从未被任何代码读取 —— 典型的"声明与行为不符"
    #        (同一个缺陷在 reranker 里表现为常量完全没人用)。保留字段名(向后兼容),
    #        数值改为与模块级常量同源,使"启动超时"只有一处真相。
    _WORKER_STARTUP_TIMEOUT = _WORKER_READY_TIMEOUT
    _DEFAULT_QUERY_CACHE_SIZE = 128

    def __init__(self, model_name: str = _DEFAULT_MODEL,
                 query_cache_size: int = _DEFAULT_QUERY_CACHE_SIZE) -> None:
        self._model_name = model_name
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        # ── 【E1-F1-A/优先级 1】「就绪」的**唯一判据** = 真的读到过 ready 信号 ──
        # 【不易】为什么不能用 `self._proc.poll() is None` 代替：poll() 只回答
        #   「进程还活着吗」，而**存活 ≠ 就绪**。模型加载要 4~300 s，在这段窗口里
        #   worker 进程一直是活的、却一行 ready 都还没打印。旧实现在这个窗口里
        #   返回 True ⇒ 调用方（search）以为可以往管道写 encode ⇒ 读到的第一行是
        #   `{"type": "ready"...}` ⇒ ① 本次查询静默退回 BM25（不是报错，是静默）；
        #   ② 真正的 embeddings 响应留在管道里，会被**下一次**查询误当成自己的答复。
        #   本标志由握手循环在解析到 ready 的那一行时置位，任何失败路径清位。
        self._worker_ready = threading.Event()
        # 【不易】握手（Popen + 等 ready + 编码 pending）必须**串行**：
        #   两个线程同时握手 = 两个 worker 抢同一根 stdout（响应必然错位），
        #   或一个线程在另一个线程刚 Popen、还没 ready 时抢先去写管道。
        #   请求路径用非阻塞 acquire（拿不到就本次降级），后台重启线程可以等。
        self._startup_lock = threading.Lock()
        # 【优先级 4】登记进「进程退出要回收」的弱引用表（见 _shutdown_all_workers）
        try:
            _LIVE_INDEXES.add(self)
        except TypeError:  # pragma: no cover - 不可弱引用时降级（不阻断构造）
            pass
        # 【W2/TASK-03 新增】崩溃可观测性 + 退避重启状态
        # Why(不可省):原实现里 `_init_failed` 一旦置 True 就**终身粘住**
        #   (_ensure_worker 的首行守卫),被 0xC0000005 打死的 worker 此后永不
        #   再被拉起,进程余生只能 BM25-only —— 而"语义检索能力已丢失"这条事实
        #   既无计数、也无只读出口,日志里只剩一串互相独立的 warn。
        self._worker_failure_total = 0
        self._worker_restart_attempts = 0
        self._next_restart_at = 0.0
        self._retry_exhausted = False
        self._restarting = False
        self._restart_lock = threading.RLock()
        self._last_worker_failure: dict = {}
        self._doc_ids: list[str] = []
        self._embeddings = None
        self._pending: list[tuple[str, str]] = []
        # 【TASK-08 E1p·第二处 O(n²)】`_pending` 的 doc_id 集合(与 _pending 同步维护)
        # Why: 旧实现每次都执行 `self._pending = [(d, c) for d, c in self._pending if d != doc_id]`
        #      去重 ⇒ 单次 add_document O(pending)。`HybridRetriever.rebuild` 会为每个工具
        #      调它一次 ⇒ 构建 n 个工具的索引是 O(n²)。实测 10,000 条时该项同样在数百毫秒
        #      量级（见 scripts/bench_capacity_scaling.py 的 rebuild 列）。
        #      有了这个集合就可以先判存在性：**不存在（绝大多数情况）直接 append**，
        #      只有真出现重复 doc_id 时才走原来的过滤分支 ⇒ 语义逐字不变、复杂度降到 O(1)。
        # 【变易】它必须与 `_pending` 的任何改动成对更新（append / filter / clear 三处）。
        self._pending_ids: set[str] = set()
        # 直写私有名:绕过下面的 property setter(此时 _restart_lock 尚未用到,
        # 但保持"初值不触发留痕"这一语义更清晰 —— 初始 False 本就不是上升沿)。
        self.__init_failed = False
        self._load_time_sec: Optional[float] = None
        self._load_source: Optional[str] = None
        # 【返工 ①】worker 自报的缓存判定（hit_namespaced / hit_bare / miss /
        # load_failed）。它回答的是"就绪快是因为缓存命中，还是因为网络恰好通"——
        # E1-F1 的整条根因链都建立在这个区分上，故必须一路透出到 worker_health()。
        self._load_cache_probe: Optional[str] = None
        self._project_root = _PROJECT_ROOT
        # query embedding LRU 缓存
        self._query_cache_size = max(query_cache_size, 1)
        self._query_cache: dict = {}
        self._cache_hits = 0
        self._cache_misses = 0

    # ════════════════════════════════════════════════════════════
    #  【W2/TASK-03】worker 不可用态的唯一入口 + 退避重启 + 只读健康出口
    # ════════════════════════════════════════════════════════════

    @property
    def _init_failed(self) -> bool:
        """worker 是否已判定不可用(粘滞标志)。"""
        return self.__init_failed

    @_init_failed.setter
    def _init_failed(self, value: bool) -> None:
        """把"置位不可用"收敛成**唯一入口**(计数 + 留痕 + 退避排期)。

        Why 用属性而不是逐个调用点插桩:本类共有 12 处 `self._init_failed = True`
        (Popen 失败 / ready 超时 / stdout 读失败 / 启动 EOF / 非法 JSON /
        init_failed 消息 / 未知消息 / encode 时进程已死 / 写失败 / 读失败 /
        读超时 / encode EOF)。逐点插桩**必然漏一处**,而漏掉的那处正是
        "崩溃不留痕"重现的地方;属性 setter 使任何新增置位点自动获得留痕。

        判据取 False→True 的**上升沿**:只有首次进入不可用态才计数,否则重复
        置位会把同一次崩溃数成多次(退避也会被无谓推后)。
        """
        rising = bool(value) and not self.__init_failed
        self.__init_failed = bool(value)
        if value:
            # 【E1-F1-A/优先级 1】进入不可用态 ⇒ 就绪标志必须同时熄灭。
            #   Why: 两者是**两个独立事实**（进程是否被判死 / 是否真的 ready 过），
            #   漏掉这一处就会出现「已判死但仍显示就绪」的组合态，任何读 _worker_ready
            #   的调用点都会踩到它。
            self._worker_ready.clear()
        if rising:
            self._on_worker_unusable()

    def _on_worker_unusable(self) -> None:
        """worker 进入不可用态:计数 + 排下次退避 + **显式声明降级为 BM25-only**。

        【不易】为什么必须显式写"降级为 BM25-only":原先只有一句
        embedding.worker.crash/encode.eof 说明**原因**,没有任何一行说明
        **后果**。运维要从"有一个 warn"推断出"语义检索已整条失效、
        直到进程重启都不会回来",这一步推断不该由人来做。
        """
        now = time.monotonic()
        with self._restart_lock:
            self._worker_failure_total += 1
            failure_no = self._worker_failure_total
            if self._worker_restart_attempts >= _WORKER_MAX_RESTARTS:
                self._retry_exhausted = True
                delay = None
            else:
                delay = min(
                    _WORKER_RESTART_BACKOFF_BASE_SEC
                    * (2 ** self._worker_restart_attempts),
                    _WORKER_RESTART_BACKOFF_MAX_SEC,
                )
                self._next_restart_at = now + delay
            self._last_worker_failure = {
                "failure_no": failure_no,
                "at_monotonic": round(now, 3),
                "restart_attempts": self._worker_restart_attempts,
                "next_restart_in_sec": delay,
                "retry_exhausted": self._retry_exhausted,
            }
        logger.warning(log_dict({
            'module_name': 'tool_router_hybrid',
            'action': 'embedding.worker.unusable',
            'degrade_to': 'bm25_only',
            'failure_no': failure_no,
            'restart_attempts': self._worker_restart_attempts,
            'max_restart_attempts': _WORKER_MAX_RESTARTS,
            'next_restart_in_sec': delay,
            'retry_exhausted': self._retry_exhausted,
            'model': self._model_name,
        }))

    def _maybe_restart_in_background(self) -> None:
        """退避到期则在**后台**重试拉起 worker(不阻塞调用方)。

        【不易】为什么不就地同步重试:_ensure_worker 位于 search() 的请求路径上
        (见 search 首段),同步重试会让一次普通检索阻塞整个模型加载、上限
        `_WORKER_READY_TIMEOUT`=30s。后台重试把这份代价移出请求路径 ——
        本次请求照旧立刻 BM25-only 降级,恢复成功后**后续**请求自然重新用上
        语义检索。这同时是对"崩溃-重启风暴"的第二道约束:同一时刻至多一个
        重启线程,且必须等退避窗口。
        """
        with self._restart_lock:
            if self._restarting:
                return
            if self._worker_restart_attempts >= _WORKER_MAX_RESTARTS:
                return
            if time.monotonic() < self._next_restart_at:
                return
            self._restarting = True
            self._worker_restart_attempts += 1
            attempt = self._worker_restart_attempts
        logger.warning(log_dict({
            'module_name': 'tool_router_hybrid',
            'action': 'embedding.worker.restart.scheduled',
            'attempt': attempt,
            'max_attempts': _WORKER_MAX_RESTARTS,
            'model': self._model_name,
        }))
        threading.Thread(
            target=self._restart_worker,
            args=(attempt,),
            name="embedding-worker-restart",
            daemon=True,
        ).start()

    def _restart_worker(self, attempt: int) -> None:
        """后台重试拉起 worker(唯一调用方是 _maybe_restart_in_background)。"""
        t0 = time.perf_counter()
        ok = False
        try:
            # 先清粘滞标志,_ensure_worker 才会真的去 Popen
            self._init_failed = False
            ok = self._ensure_worker(_from_restart=True)
        except Exception as e:  # pragma: no cover - 仅异常环境走到
            logger.warning(log_dict({
                'module_name': 'tool_router_hybrid',
                'action': 'embedding.worker.restart.error',
                'attempt': attempt,
                'error': str(e),
            }))
            self._init_failed = True
        finally:
            with self._restart_lock:
                self._restarting = False
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.warning(log_dict({
            'module_name': 'tool_router_hybrid',
            'action': 'embedding.worker.restart.success' if ok
                      else 'embedding.worker.restart.failed',
            'attempt': attempt,
            'max_attempts': _WORKER_MAX_RESTARTS,
            'elapsed_ms': elapsed_ms,
            'recovered_from_bm25_only': bool(ok),
            'model': self._model_name,
        }))

    def worker_health(self) -> dict:
        """【W2/TASK-03】worker 崩溃/降级的**只读**可观测出口。

        Why:降级状态此前只能靠 private 字段与日志措辞间接判断。
        本出口是给 /api/health 之类上层探针用的稳定契约(见交付报告的
        「跨任务请求」:接线 app_server.py 归 TASK-04,本任务不改该文件)。
        """
        with self._restart_lock:
            failures = self._worker_failure_total
            attempts = self._worker_restart_attempts
            restarting = self._restarting
            exhausted = self._retry_exhausted
            last = dict(self._last_worker_failure)
            if self._init_failed and not exhausted:
                remaining = max(0.0, round(self._next_restart_at - time.monotonic(), 3))
            else:
                remaining = None
        alive = self._proc is not None and self._proc.poll() is None
        # 【E1-F1-A/优先级 5】mode 不再撒谎。
        #   旧口径 "bm25_only" if _init_failed else "hybrid"：只要没被判死就报
        #   hybrid —— 于是 spawn 后 6 s 的采样会得到 mode="hybrid" 而
        #   available=false（worker 还在加载、一条向量都没有）。裸读该字段的
        #   消费者（app_server 用并集兜住了，但那是"靠另一个字段补救"）会得出
        #   「向量腿正常」这个**与事实相反**的结论。
        #   新口径：mode = 向量腿**此刻能不能真的供数**（= available：进程活着
        #   + 真的 ready + 已有向量 + 未判死）。保持**二值**（不新增 "starting"
        #   之类的第三态），因为任何按 == 比较的既有消费者都会自动得到诚实答案；
        #   启动中的事实由新增的 worker_ready / available 两个布尔字段如实表达。
        ready = bool(self._worker_ready.is_set() and alive
                     and not self._init_failed)
        return {
            "mode": "hybrid" if self.available else "bm25_only",
            "init_failed": self._init_failed,
            "worker_alive": alive,
            "worker_ready": ready,
            "load_cache_probe": self._load_cache_probe,
            "available": self.available,
            "failure_total": failures,
            "restart_attempts": attempts,
            "max_restart_attempts": _WORKER_MAX_RESTARTS,
            "restarting": restarting,
            "retry_exhausted": exhausted,
            "next_restart_in_sec": remaining,
            "last_failure": last,
        }

    @property
    def available(self) -> bool:
        """**真的能供数**才叫 available：进程存活 + 已收到 ready + 向量已算好 + 未判死

        【E1-F1-A/优先级 5 补强】旧口径只要求"进程活着 + _embeddings 非空"，漏掉了
        「**当前的** worker 是否真的 ready」这一个事实。后果有两处：
          · 首次启动窗口（_embeddings 还是 None）它恰好是对的；
          · 但**重启窗口**是错的：老 worker 留下的 _embeddings 还在，新 worker 还在
            加载 ⇒ available=True、degraded=False、mode="hybrid"，而此刻向量腿**一条
            查询都供不了**（查询编码必须经过新 worker）。那就是谎报。
        故把 _worker_ready 纳入判据：available 的语义收敛为「向量腿此刻能不能供数」。
        对使用方是**更严**的方向：degraded 在启动/重启窗口里变为 True（如实），
        _query_locked 会跳过 embed 路（与 search() 的既有行为一致）。
        """
        if self._init_failed:
            return False
        # 【不易】就绪判据必须与 _ensure_worker 完全同源（同一个 Event），
        #   否则"能不能供数"与"能不能写管道"会变成两套事实。
        if not self._worker_ready.is_set():
            return False
        if self._proc is None or self._proc.poll() is not None:
            return False
        return self._embeddings is not None and len(self._doc_ids) > 0

    def add_document(self, doc_id: str, content: str) -> None:
        """添加文档到 pending 列表(延迟编码)"""
        with self._lock:
            if doc_id in self._doc_ids:
                idx = self._doc_ids.index(doc_id)
                self._doc_ids.pop(idx)
                if self._embeddings is not None:
                    self._embeddings = np.delete(self._embeddings, idx, axis=0)
            # 【TASK-08 E1p】先判存在性：不存在 ⇒ 直接 append（O(1)）；
            # 存在 ⇒ 才走原来的过滤分支（O(pending)）。语义与原实现逐字等价
            # （原地过滤 + append 的净效果就是"删旧同名项再追加到末尾"）。
            if doc_id in self._pending_ids:
                self._pending = [(d, c) for d, c in self._pending if d != doc_id]
                self._pending_ids.discard(doc_id)
            self._pending.append((doc_id, content))
            self._pending_ids.add(doc_id)

    def _ensure_worker(self, _from_restart: bool = False) -> bool:
        """启动子进程 worker + 等待 ready 信号 + 编码 pending 文档

        【W2/TASK-03 变更】不可用态不再"终身粘住":退避到期后由后台线程
        (_restart_worker)重试拉起。`_from_restart` 仅供该线程使用 ——
        否则重启线程自己会被下面的 `_restarting` 守卫挡掉。
        """
        if self._init_failed:
            # 保持本次调用**立即**降级(不动请求路径的时延),只在退避到期时
            # 排一个后台重试。
            self._maybe_restart_in_background()
            return False
        if self._restarting and not _from_restart:
            # 后台重启进行中:本次调用保持降级,避免与重启线程**重复拉起**子进程
            # (双 Popen 会让两个 worker 抢同一根 stdout,响应必然错位)。
            return False

        # ── 快路径：**真的读到过 ready** 且进程仍活 ⇒ 才算就绪 ──────────────
        # 【E1-F1-A/优先级 1】旧实现这里写的是 `self._proc.poll() is None`：
        #   那回答的是「进程还活着吗」，而不是「它准备好了吗」。模型加载要几秒到
        #   几百秒，这段时间里 worker 活着、却一行 ready 都没打印 —— 旧实现照样
        #   返回 True，调用方于是往管道写 encode，读到的第一行必然是 ready 那一行：
        #   ① 本次查询静默退回 BM25（无异常、无告警，只是没有向量腿）；
        #   ② 真正的 embeddings 响应留在管道里，被**下一次**查询当成自己的答复
        #      （查询↔向量错位）。
        if (self._worker_ready.is_set()
                and self._proc is not None and self._proc.poll() is None):
            return True

        # ── 握手权：同一时刻只允许**一个**线程 Popen / 读 stdout ────────────
        # 【不易】为什么必须是排他锁而不是"双检 + 各拉各的"：
        #   两个线程同时 Popen 会让两个 worker 抢同一根 stdout（响应必然错位）；
        #   一个线程 Popen、另一个线程在它 ready 之前就去写管道，同样错位。
        # 【变易】请求路径用非阻塞 acquire：拿不到锁说明**别人正在握手**，
        #   本次立即降级（不排队、不碰管道、不增加请求时延）；
        #   后台重启线程（_from_restart）可以等，它不在请求路径上。
        if _from_restart:
            acquired = self._startup_lock.acquire(timeout=self._WORKER_STARTUP_TIMEOUT)
        else:
            acquired = self._startup_lock.acquire(blocking=False)
        if not acquired:
            logger.info(log_dict({
                'module_name': 'tool_router_hybrid',
                'action': 'embedding.worker.startup_in_progress',
                'degrade_to': 'bm25_only',
                'note': '另一线程正在拉起/等待 worker：本次查询不碰管道，直接走 BM25',
            }))
            return False
        try:
            # 双检：等锁期间可能已被握手线程拉起来并置为就绪
            if (self._worker_ready.is_set()
                    and self._proc is not None and self._proc.poll() is None):
                return True
            if self._init_failed:
                self._maybe_restart_in_background()
                return False
            if self._proc is not None and self._proc.poll() is None:
                # 进程活着、却从未 ready（上一次握手被中断/没收干净）⇒ 状态未知。
                # 【不易】这里**不能**乐观地返回 True（那正是本卡要根除的失效型），
                #   也不能留着它继续跑：先回收再重新拉起，让"就绪"重新变成可证事实。
                logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.worker.unready_reclaim', 'pid': getattr(self._proc, 'pid', None), 'model': self._model_name}))
                self._cleanup_proc()
            self._worker_ready.clear()
            return self._handshake_locked()
        finally:
            self._startup_lock.release()

    def _handshake_locked(self) -> bool:
        """Popen worker + 等 ready + 编码 pending（**调用方必须持 _startup_lock**）

        【不易】本方法是唯一允许"读 worker stdout 直到 ready"的地方：在此之前
        stdout 上出现的任何一行都只可能是 ready（协议里 worker 在模型加载完成前
        不打印别的东西）⇒ 把这段收进一个持锁函数，"读到第一行"与"置就绪"
        之间就不可能再插进另一个线程的 encode 请求。
        """
        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-c", _WORKER_SCRIPT_EMBEDDING, self._model_name],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self._project_root,
            )
        except (OSError, ValueError) as e:
            logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.worker.popen_failed', 'error': str(e)}))
            self._init_failed = True
            return False

        # 等待 ready 信号(带超时)
        json_errors = 0
        while True:
            try:
                line = _readline_with_timeout(self._proc.stdout, self._WORKER_STARTUP_TIMEOUT)
            except OSError as e:
                logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.worker.stdout_read_failed', 'error': str(e)}))
                self._init_failed = True
                return False

            if line is _READLINE_TIMED_OUT:
                # worker 未在超时时间内输出 ready 信号:kill 并降级为纯 BM25
                logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.worker.ready_timeout', 'timeout_sec': self._WORKER_STARTUP_TIMEOUT, 'model': self._model_name}))
                self._proc.kill()
                try:
                    self._proc.wait(timeout=3)
                except Exception:
                    pass
                self._proc = None
                self._worker_ready.clear()
                self._init_failed = True
                return False

            if not line:
                rc = self._proc.poll()
                diag = _diagnose_crash(rc)
                stderr_msg = ""
                try:
                    stderr_msg = self._proc.stderr.read()[:500] if self._proc.stderr else ""
                except Exception:
                    pass
                logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.worker.crash', 'returncode': rc, 'diagnosis': diag, 'stderr_preview': stderr_msg}))
                self._init_failed = True
                return False

            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                json_errors += 1
                if json_errors >= 3:
                    logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.worker.invalid_json', 'consecutive_errors': json_errors}))
                    self._init_failed = True
                    return False
                continue

            msg_type = msg.get("type")
            if msg_type == "ready":
                self._load_time_sec = msg.get("load_time_sec")
                self._load_source = msg.get("load_source")
                # 【返工 ①】cache_probe 必须**透出到父进程**：
                #   "就绪只用了 5 s"这件事有两种完全不同的解释 —— 缓存命中，
                #   或者网络恰好通。不记录它就分不清，而 E1-F1 的整个根因就是
                #   "快/慢由在线与否决定"。四态见 worker 脚本 _load_model 。
                self._load_cache_probe = msg.get("cache_probe")
                # ★ 就绪的**置位点**：只有真的读到 ready 这一行，才认为就绪。
                self._worker_ready.set()
                logger.info(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.worker.ready', 'model': self._model_name, 'load_time_sec': self._load_time_sec, 'load_source': self._load_source, 'cache_probe': self._load_cache_probe}))
                # 编码 pending 文档
                if self._pending:
                    self._encode_pending_locked()
                return True
            elif msg_type == "init_failed":
                logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.worker.init_failed', 'error': msg.get('error', 'unknown')}))
                self._init_failed = True
                return False
            else:
                logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.worker.unknown_message', 'msg_type': msg_type}))
                self._init_failed = True
                return False

    def _encode_via_worker(self, texts: list[str]) -> "list | None":
        """通过子进程编码文本,返回向量列表"""
        if self._init_failed or self._proc is None:
            return None
        if self._proc.poll() is not None:
            rc = self._proc.poll()
            diag = _diagnose_crash(rc)
            logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.encode.proc_dead', 'returncode': rc, 'diagnosis': diag}))
            self._init_failed = True
            return None

        try:
            req = json.dumps({"type": "encode", "texts": texts})
            self._proc.stdin.write(req + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            rc = self._proc.poll()
            diag = _diagnose_crash(rc)
            logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.encode.write_failed', 'error': str(e), 'returncode': rc, 'diagnosis': diag}))
            self._init_failed = True
            return None

        try:
            line = _readline_with_timeout(self._proc.stdout, _WORKER_ENCODE_TIMEOUT)
        except OSError as e:
            logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.encode.read_failed', 'error': str(e)}))
            self._init_failed = True
            return None

        if line is _READLINE_TIMED_OUT:
            # 【不易】模型早已加载,这里超时只能是 worker 卡死/死锁。必须回收子进程:
            #        否则超时后仍阻塞在 readline 上的孤儿读线程会抢走**下一次**请求的
            #        响应行,造成请求-响应错位(比直接降级更隐蔽)。
            logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.encode.read_timeout', 'timeout_sec': _WORKER_ENCODE_TIMEOUT, 'n_texts': len(texts), 'model': self._model_name}))
            self._init_failed = True
            self._cleanup_proc()
            return None

        if not line:
            rc = self._proc.poll()
            diag = _diagnose_crash(rc)
            logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.encode.eof', 'returncode': rc, 'diagnosis': diag}))
            self._init_failed = True
            return None

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.encode.invalid_json'}))
            return None

        if msg.get("type") != "embeddings":
            logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.encode.unexpected_msg', 'msg_type': msg.get('type'), 'error': msg.get('error', '')}))
            return None

        # 优先解析二进制序列化(base64+numpy),fallback 到 JSON 列表
        import base64
        if "data" in msg and _HAS_NUMPY:
            try:
                raw = base64.b64decode(msg["data"])
                arr = np.frombuffer(raw, dtype=np.float32)
                shape = msg.get("shape")
                if shape:
                    arr = arr.reshape(shape)
                return arr.tolist()
            except Exception:
                pass
        return msg.get("vectors")

    def _encode_pending_locked(self) -> None:
        """编码所有 pending 文档

        【E1-F1-A/优先级 1】原 docstring 写"调用方持锁"，但**握手线程其实没有持
        _lock**（_ensure_worker 在 search() 里是在取 _lock **之前**被调用的）。
        也就是说：握手线程的 pending 编码与某个请求线程的 query 编码会**同时**
        写同一根 stdin、同时读同一根 stdout —— 两个读者抢响应行 ⇒ 错位。
        本方法自己取 _lock（RLock，可重入），把"一次 encode 请求=一次写+一次读"
        变成原子的：管道在同一时刻只有一个编码者。
        """
        with self._lock:
            self._encode_pending_holding_lock()

    def _encode_pending_holding_lock(self) -> None:
        """_encode_pending_locked 的实现体（**必须已持 _lock**）"""
        if not self._pending:
            return
        contents = [c for _, c in self._pending]
        vectors = self._encode_via_worker(contents)
        if vectors is None or len(vectors) == 0:
            logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.encode_pending.failed', 'n_pending': len(self._pending)}))
            return
        new_embeddings = np.array(vectors, dtype=np.float32)
        if self._embeddings is None:
            self._embeddings = new_embeddings
            self._doc_ids = [d for d, _ in self._pending]
        else:
            self._embeddings = np.vstack([self._embeddings, new_embeddings])
            self._doc_ids.extend(d for d, _ in self._pending)
        logger.info(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.encode_pending.complete', 'n_pending': len(self._pending), 'total_docs': len(self._doc_ids), 'shape': list(new_embeddings.shape)}))
        self._pending.clear()
        # 【TASK-08 E1p】pending 清空时 id 集合必须同步清空，否则后续同名 add 会走
        # "已存在 ⇒ 过滤" 分支去删一个不存在的条目（结果虽相同，但会白白 O(n) 扫描）
        self._pending_ids.clear()

    def _cleanup_proc(self) -> None:
        """清理子进程资源"""
        # 【E1-F1-A/优先级 1】回收子进程 ⇒ 必须同时熄灭「已就绪」标志：
        #   否则一个进程已死（或已被回收）的 worker 仍被判定为就绪，
        #   后续请求会继续往一根死管道写 encode。
        self._worker_ready.clear()
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None:
                try:
                    self._proc.stdin.write(json.dumps({"type": "exit"}) + "\n")
                    self._proc.stdin.flush()
                    self._proc.wait(timeout=5)
                except Exception:
                    self._proc.kill()
                    self._proc.wait(timeout=3)
        except Exception as e:
            logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.cleanup.error', 'error': str(e)}))
        finally:
            self._proc = None

    def close(self) -> None:
        """关闭子进程 worker（**公开清理口**；E1-F1-A/优先级 4）。

        Why 需要它：原实现**没有任何对外的关闭入口** —— 唯一的回收路径
        `_cleanup_proc` 是私有方法、且只在「worker 自己出错」时才被调用，于是
        「进程正常退出」这条最常见的路径**一条回收语句都没有**：每跑一次进程
        就留一个 450 MB 的常驻 worker（E1-F1 实测抓到 2 个孤儿，各 451~454 MB）。
        本方法 + `_shutdown_all_workers`（atexit 钩子）把这条路径补齐。

        【不易】不在这里置 `_init_failed`：正常关机**不是故障**，置位会污染
        `failure_total` 并触发一次假的「降级为 BM25-only」告警，使运维无法区分
        「我们主动关机」与「worker 崩了」。再次查询会重新拉起 worker（冷启动）。
        """
        with self._lock:
            self._cleanup_proc()

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """搜索查询,返回 [(doc_id, cosine_similarity)] 列表(按相似度降序)

        【变易】query embedding LRU 缓存:重复查询跳过子进程通信
        """
        if not _HAS_NUMPY:
            return []
        if not self._ensure_worker():
            return []
        with self._lock:
            # 【E1-F1-A/优先级 1 防御纵深】取锁后**再判一次就绪**：
            #   _ensure_worker() 返回到这里之间存在窗口（worker 可能在窗口里
            #   崩溃/被回收）。判据与 _ensure_worker 完全同源 —— 只有真的读到过
            #   ready 才允许写管道，避免任何路径把 encode 发进一个"还没 ready"
            #   的 worker（那会让 ready 行被当成 embeddings，错位）。
            if not self._worker_ready.is_set():
                return []
            if self._embeddings is None or len(self._doc_ids) == 0:
                return []

            # LRU 缓存查找
            cache_hit = query in self._query_cache
            t_encode_start = time.perf_counter()
            if cache_hit:
                query_emb = self._query_cache.pop(query)
                self._query_cache[query] = query_emb
                self._cache_hits += 1
                t_encode_ms = (time.perf_counter() - t_encode_start) * 1000
                logger.debug(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.cache.hit', 'query_preview': query[:60], 'query_len': len(query), 'encode_ms': round(t_encode_ms, 4), 'cache_size': len(self._query_cache), 'cumulative_hits': self._cache_hits, 'cumulative_misses': self._cache_misses}))
            else:
                self._cache_misses += 1
                logger.debug(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.cache.miss', 'query_preview': query[:60], 'query_len': len(query), 'cache_size_before': len(self._query_cache)}))
                query_vectors = self._encode_via_worker([query])
                t_encode_ms = (time.perf_counter() - t_encode_start) * 1000
                if query_vectors is None or len(query_vectors) == 0:
                    logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.search.encode_failed', 'encode_ms': round(t_encode_ms, 2), 'query_len': len(query)}))
                    return []
                query_emb = np.array(query_vectors[0], dtype=np.float32)
                self._query_cache[query] = query_emb
                # LRU 淘汰:缓存满时移除最久未使用的条目
                if len(self._query_cache) > self._query_cache_size:
                    evicted_key = next(iter(self._query_cache))
                    self._query_cache.pop(evicted_key)
                    logger.debug(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.cache.evict', 'evicted_preview': evicted_key[:60], 'cache_size': len(self._query_cache), 'cache_capacity': self._query_cache_size}))
                logger.debug(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.cache.miss_complete', 'query_preview': query[:60], 'encode_ms': round(t_encode_ms, 2), 'cache_size_after': len(self._query_cache), 'cumulative_hits': self._cache_hits, 'cumulative_misses': self._cache_misses}))

            try:
                t_cosine_start = time.perf_counter()
                norms = np.linalg.norm(self._embeddings, axis=1)
                query_norm = np.linalg.norm(query_emb)
                if query_norm < 1e-9:
                    return []
                denom = norms * query_norm
                denom = np.where(denom < 1e-9, 1e-9, denom)
                sims = self._embeddings @ query_emb / denom
                top_indices = np.argsort(-sims)[:top_k]
                results = [(self._doc_ids[i], float(sims[i])) for i in top_indices]
                t_cosine_ms = (time.perf_counter() - t_cosine_start) * 1000
                total_cached = self._cache_hits + self._cache_misses
                hit_rate = self._cache_hits / max(total_cached, 1)
                logger.info(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.search.complete', 'encode_ms': round(t_encode_ms, 2), 'cosine_ms': round(t_cosine_ms, 2), 'total_ms': round(t_encode_ms + t_cosine_ms, 2), 'n_docs': len(self._doc_ids), 'top_k': top_k, 'returned': len(results), 'top1_score': round(results[0][1], 4) if results else 0.0, 'cache_hit': cache_hit, 'cache_hit_rate': round(hit_rate, 4), 'cache_size': len(self._query_cache)}))
                return results
            except Exception as e:
                logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.search.failed', 'error': f'{type(e).__name__}: {e}', 'encode_ms': round(t_encode_ms, 2), 'query_len': len(query)}))
                return []

    def clear(self) -> None:
        """清空索引(不关闭子进程)"""
        with self._lock:
            self._doc_ids.clear()
            self._embeddings = None
            self._pending.clear()
            self._pending_ids.clear()
            self._query_cache.clear()
            self._cache_hits = 0
            self._cache_misses = 0

    def get_cache_stats(self) -> dict:
        """返回 LRU query 缓存统计信息(用于运行时缓存效率验证)

        Returns:
            {"hits", "misses", "hit_rate", "cache_size", "cache_capacity"}
        """
        with self._lock:
            total = self._cache_hits + self._cache_misses
            return {
                "hits": self._cache_hits,
                "misses": self._cache_misses,
                "hit_rate": round(self._cache_hits / max(total, 1), 4),
                "cache_size": len(self._query_cache),
                "cache_capacity": self._query_cache_size,
            }

    def preheat(self) -> None:
        """预热:启动子进程 + 编码 pending 文档"""
        t0 = time.perf_counter()
        pending_count = len(self._pending)
        logger.info(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.preheat.start', 'pending_docs': pending_count}))
        try:
            ok = self._ensure_worker()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            if ok:
                logger.info(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.preheat.success', 'elapsed_ms': round(elapsed_ms, 2), 'pending_docs': pending_count, 'encoded_docs': len(self._doc_ids), 'load_time_sec': self._load_time_sec}))
            else:
                logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.preheat.failed', 'elapsed_ms': round(elapsed_ms, 2), 'init_failed': self._init_failed}))
        except Exception as e:
            logger.warning(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.preheat.error', 'error': str(e)}))


# ════════════════════════════════════════════════════════════
#  HybridRetriever — BM25 + Embedding 分数融合
# ════════════════════════════════════════════════════════════


def _calibrate_bm25_scores(scores: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """BM25 raw 分 → 校准分 [0,1)（查询无关、单调、**不把 max 钉在 1.0**）

    Why（W5/L27）: 旧融合用 min-max 归一化，每路 top1 恒为 1.0 ⇒ 融合分对任何查询
        都取同一个值（实测 [1.0]），在其上做 ECE/拒识等价于"永不拒识"。
        改用 `p = s / (s + _BM25_HALF_SATURATION)`：单调（**保序**⇒不改变 BM25 路
        的排序）、有界（上确界 1.0 但不可达）、查询无关（可跨查询比较）。
    【不易】单调性是本改动"不扰动既有排序"的**证明**：任何严格单调映射都保持
        raw 分的顺序，故 Embedding 不可用的降级路上，融合结果的顺序与改前逐位相同。
    """
    if not scores:
        return []
    out: list[tuple[str, float]] = []
    for doc_id, raw in scores:
        if raw <= 0.0:
            # 非正分不是"证据"：映射为 0 而不是被 min/max 拉成 0.5 之类的中间值
            out.append((doc_id, 0.0))
        else:
            out.append((doc_id, raw / (raw + _BM25_HALF_SATURATION)))
    return out


def _calibrate_cosine_scores(scores: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Embedding 余弦 [-1,1] → 校准分 [0,1]（查询无关、单调、保留余弦量纲）

    Why（W5/L27）: 与 BM25 路同理，min-max 会把该路 top1 钉成 1.0。
        余弦本身已有可解释量纲，故直接用"剪枝阈值 → 1.0"的线性映射：
        低于 _COSINE_CUTOFF 的候选本就不进入融合（在 _query_locked 已剪枝），
        映射后在 [0,1] 内可比，且随查询变化（不恒为 1.0）。
    【W5 复核附注】`_COSINE_CUTOFF` 因此从"纯剪枝阈值"**升格为尺度参数**：
        它决定本路映射跨度（cos=0.6 ⇒ p=0.5）。调它会整体改变融合分的尺度，
        不只是改变谁能进融合。它与 BM25 路的锚点来源不同（人工阈值 vs 数据中位数），
        故两路的 0.5 不代表同等置信。
    """
    if not scores:
        return []
    span = 1.0 - _COSINE_CUTOFF
    out: list[tuple[str, float]] = []
    for doc_id, cos in scores:
        v = (cos - _COSINE_CUTOFF) / span if span > 1e-9 else 0.0
        # 截断到 [0,1]：余弦可 >1（浮点误差）或 <cutoff（调用方未剪枝时）
        out.append((doc_id, min(1.0, max(0.0, v))))
    return out


def _min_max_normalize(scores: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """min-max 归一化到 [0,1]

    Why: BM25 分数无界,Embedding 余弦在 [-1,1],需归一化才能融合。
         min-max 保留"最高分=1,最低分=0"语义,多候选时能拉开差距。

    【W5/L27 起不再用于融合】它把每路 max 强映射为 1.0 ⇒ 融合 top1 恒为 1.0，
        分数不可校准（这正是 L27）。融合改走 _calibrate_bm25_scores /
        _calibrate_cosine_scores。本函数与其单元测试保留（对外符号不删，
        避免破坏仍 import 它的调用方），但**不得**再用于分数融合。
    """
    if not scores:
        return []
    values = [s for _, s in scores]
    min_v, max_v = min(values), max(values)
    if max_v - min_v < 1e-9:
        # 所有分数相同:归一化为 1.0(避免除零,保留候选)
        return [(doc_id, 1.0) for doc_id, _ in scores]
    return [(doc_id, (v - min_v) / (max_v - min_v)) for doc_id, v in scores]


class HybridRetriever:
    """混合检索器 — BM25 + Embedding 分数融合

    【不易】单例 + 双重检查锁,线程安全
    【变易】alpha 可配,候选合并后过 TOOL_ALIASES 合并 + 优先级去重 + 25 上限
    【简易】查询路径 <25ms,后台 daemon thread 预热 EmbeddingIndex
    """

    def __init__(
        self,
        alpha: Optional[float] = None,
        index_path: str = _INDEX_PATH,
    ):
        # alpha 优先级:显式参数 > 环境变量 AGENT_HYBRID_ALPHA > 默认 0.5
        self._alpha = _resolve_alpha_from_env() if alpha is None else alpha
        self._index_path = index_path
        self._bm25 = BM25Index()
        self._embedding = EmbeddingIndex()
        self._lock = threading.RLock()
        self._tools_loaded = False
        self._all_categories: set = set()
        # 上次查询的中间统计(bm25/embed/fused 召回数),供 hybrid_select_tools 读取
        self._last_query_stats: dict = {}

        # 加载工具定义并构建双索引
        self._load_and_build_index()

        # 启动后台 daemon thread 预热 EmbeddingIndex
        # 【不易】AGENT_HYBRID_EMBEDDING=0/false/no/off 时禁用预热:CI 无 HF 网络时
        # 子进程加载模型 30s×N 超时,拖累采样性能断言误报(2026-08-07 三次复现)。
        # 【E1-D】判据收归 _resolve_embedding_env_override()（**唯一实现**）：
        #   原先这里有自己的一份 env 解析，而 _ensure_st_checked 里另有一份
        #   ——"能关向量腿"这条声明落在了一个**无生产调用点**的函数里，
        #   登记表据此把它写成"向量模型/默认空串"（语义说反）。
        #   现在两处共用同一个解析口：此处是**生产调用点**，_ensure_st_checked
        #   复用它 ⇒ 那条"0=禁用"分支不再是假通路。
        #   关到什么程度：只抑制预热（本进程不拉 worker）；不是硬禁用
        #   （EmbeddingIndex.search/preheat 内部仍会 _ensure_worker）。
        _embed_override = _resolve_embedding_env_override()
        _embed_disabled = _embed_override is False
        if self._tools_loaded and self._embedding is not None and not _embed_disabled:
            t = threading.Thread(
                target=self._embedding.preheat,
                name="hybrid-embedding-preheat",
                daemon=True,
            )
            t.start()

    def _load_and_build_index(self) -> None:
        """从 tool_index.json 加载工具定义,构建 BM25 + Embedding 双索引"""
        if not os.path.exists(self._index_path):
            logger.warning(
                "[tool_router_hybrid] tool_index.json 不存在: %s(hybrid 不可用)",
                self._index_path,
            )
            return

        try:
            with open(self._index_path, "r", encoding="utf-8") as f:
                index_data = json.load(f)
        except Exception as e:
            logger.warning("[tool_router_hybrid] tool_index.json 加载失败: %s", e)
            return

        tools = index_data.get("tools", [])
        if not tools:
            logger.warning("[tool_router_hybrid] tool_index.json 无工具定义")
            return

        # 收集所有类别(用于 helper 优先级查询)
        if TOOL_CATEGORIES:
            self._all_categories = set(TOOL_CATEGORIES.keys())

        self.rebuild(tools)
        self._tools_loaded = True
        logger.info(
            "[tool_router_hybrid] 索引构建完成: %d 个工具(BM25=%d, Embedding pending=%d)",
            len(tools),
            self._bm25.size,
            len(self._embedding._pending) if self._embedding._pending else 0,
        )

    def rebuild(self, tools: list[dict]) -> None:
        """重建双索引

        Args:
            tools: 工具定义列表,每项含 name/description/parameter_names(可选)
        """
        with self._lock:
            self._bm25.clear()
            self._embedding.clear()
            for tool in tools:
                name = tool.get("name", "")
                if not name:
                    continue
                description = tool.get("description", "")
                # parameter_names 可能缺失(旧索引),兜底为空列表
                param_names = tool.get("parameter_names", []) or []
                if not isinstance(param_names, list):
                    param_names = []

                # BM25 索引内容:name + parameter_names + description
                bm25_content = name + " " + " ".join(param_names) + " " + description
                self._bm25.add_document(name, bm25_content)

                # Embedding 索引内容:description(语义匹配)
                self._embedding.add_document(name, description)

    @property
    def available(self) -> bool:
        """BM25 必须可用,Embedding 可选"""
        return self._tools_loaded and self._bm25.size > 0

    def query(
        self,
        text: str,
        top_k: int = _DEFAULT_TOP_K,
    ) -> Optional[list[tuple[str, float]]]:
        """混合检索:BM25 + Embedding 分数融合

        Args:
            text: 查询文本
            top_k: 返回候选数

        Returns:
            [(tool_name, fused_score)] 列表(按分数降序);None 表示检索失败
        """
        if not text or not text.strip():
            return []
        if not self.available:
            return None

        # 重建期间不阻塞查询:try acquire,失败返回 None
        if not self._lock.acquire(blocking=False):
            return None
        try:
            return self._query_locked(text, top_k)
        except Exception as e:
            logger.warning("[tool_router_hybrid] 查询异常: %s", e)
            return None
        finally:
            self._lock.release()

    def _query_locked(self, text: str, top_k: int) -> list[tuple[str, float]]:
        """执行查询(调用方持锁)"""
        # 候选扩展:取 top_k*2 避免融合后丢失相关结果
        candidate_k = max(top_k * 2, top_k + 5)
        degraded = not self._embedding.available

        # [logger] 查询开始:打印 query + 参数 + 降级标志(排查退化问题用)
        logger.info(
            "[tool_router_hybrid] query 开始: text=%r top_k=%d candidate_k=%d degraded=%s alpha=%.2f",
            text, top_k, candidate_k, degraded, self._alpha,
        )

        # BM25 检索
        bm25_results = self._bm25.search(text, top_k=candidate_k)
        # 【W5/L27】校准（**不是** min-max）：查询无关 ⇒ 分数可跨查询比较，
        # top1 不再被钉在 1.0。单调 ⇒ 本路排序与改前逐位一致。
        bm25_norm = _calibrate_bm25_scores(bm25_results)

        # [logger] BM25 召回结果 top-5(排查召回缺失型退化)
        logger.info(
            "[tool_router_hybrid] BM25 召回: total=%d top5=%s",
            len(bm25_results),
            [(d, round(s, 4)) for d, s in bm25_results[:5]],
        )

        # Embedding 检索(可选)
        embed_results: list[tuple[str, float]] = []
        embed_norm: list[tuple[str, float]] = []
        if self._embedding.available:
            embed_results = self._embedding.search(text, top_k=candidate_k)
            # cosine 剪枝:低于阈值的候选不进入融合
            embed_results = [(d, s) for d, s in embed_results if s >= _COSINE_CUTOFF]
            # 【W5/L27】同 BM25 路：查询无关的单调校准，保留余弦量纲
            embed_norm = _calibrate_cosine_scores(embed_results)

            # [logger] Embedding 召回结果 top-5(排查 Embedding 路径退化)
            logger.info(
                "[tool_router_hybrid] Embedding 召回: total=%d top5=%s",
                len(embed_results),
                [(d, round(s, 4)) for d, s in embed_results[:5]],
            )

        # 分数融合
        # 【E1-D · 生产非确定性修复 ①/②】候选汇合**必须是有确定次序的序列**。
        #   原来是 set：字符串哈希随机化 ⇒ set 的迭代序随进程变，而
        #   fused.sort 是**稳定排序** ⇒ 分数并列的候选，其先后直接等于哈希序
        #   ⇒ 候选池截断(fused[:top_k])落在并列块内部时，**成员**随进程变。
        #   实测（合成并列索引，AGENT_HYBRID_EMBEDDING=0 纯 BM25，5 个种子）：
        #   融合 top1 = tieprobe06 / tieprobe30 / tieprobe22 / tieprobe02 / tieprobe23，
        #   下发集对称差最多 24/25 —— 同一查询、同一索引、同一份代码。
        #   归并次序取「BM25 路序 → Embedding 路序」：两路各自都是确定的
        #   （BM25Index.search 是稳定排序 + 索引插入序；EmbeddingIndex.search 是
        #   np.argsort），故汇合序确定；且该次序与降级路既有契约"融合顺序必须与
        #   raw BM25 顺序逐位一致"相容（见 _cand_pos 处的说明）。
        candidate_order: list[str] = []
        _seen_candidates: set[str] = set()
        for _doc_id, _score in bm25_norm:
            if _doc_id not in _seen_candidates:
                _seen_candidates.add(_doc_id)
                candidate_order.append(_doc_id)
        for _doc_id, _score in embed_norm:
            if _doc_id not in _seen_candidates:
                _seen_candidates.add(_doc_id)
                candidate_order.append(_doc_id)
        # 成员判定/计数仍用集合（语义与改前一致）；**迭代**一律走 candidate_order
        all_candidates: set[str] = _seen_candidates

        # 记录中间统计(供 hybrid_select_tools 写入 trace)
        self._last_query_stats = {
            "bm25_candidates": len(bm25_results),
            "embed_candidates": len(embed_results),
            "fused_candidates": len(all_candidates),
            # 【W5/L27】把**归一化前**的 raw 分与其量纲一并透出：
            #   校准分必须可回溯到原始证据，否则"可校准"只是换了个 0~1 的数
            #   （BM25 无界正数；余弦 ∈ [-1,1]，融合前已按 _COSINE_CUTOFF 剪枝）。
            "raw_bm25_top5": [[d, round(s, 4)] for d, s in bm25_results[:5]],
            "raw_embed_top5": [[d, round(s, 4)] for d, s in embed_results[:5]],
            "bm25_half_saturation": _BM25_HALF_SATURATION,
            "cosine_floor": _COSINE_CUTOFF,
            "alpha": self._alpha,
            # 【W5/L28-A8 G1】召回过滤读数：护栏滤掉多少候选、共考虑多少候选。
            #   取值来自 BM25Index.search 的实例读数（同一次检索，无二次计算）。
            "bm25_filtered_by_min_coverage": int(getattr(self._bm25, "_last_filtered_count", 0)),
            "bm25_considered": int(getattr(self._bm25, "_last_considered_count", 0)),
            "bm25_filtered_preview": list(getattr(self._bm25, "_last_filtered_preview", [])),
            "min_idf_coverage": _MIN_IDF_COVERAGE,
        }

        bm25_map = dict(bm25_norm)
        embed_map = dict(embed_norm)

        fused: list[tuple[str, float]] = []
        for doc_id in candidate_order:          # 【E1-D】确定次序（原来是 set 迭代序）
            bm25_score = bm25_map.get(doc_id, 0.0)
            embed_score = embed_map.get(doc_id, 0.0)
            # 若 Embedding 不可用,只用 BM25(alpha=1.0 等效)
            if not self._embedding.available or not embed_norm:
                final = bm25_score
            else:
                final = self._alpha * bm25_score + (1 - self._alpha) * embed_score
            fused.append((doc_id, final))

        # 【E1-D · 修复 ②/②】主键**不变**（分数降序），只补一个**确定的次级键**。
        #   为什么次级键取"候选汇合序"而不是工具名字典序：
        #     · 字典序会在**分数并列处重排 BM25 本路**，而"降级路上融合顺序必须与
        #       raw BM25 顺序逐位一致"是既有回归的明文契约
        #       （tests/unit/test_tool_router_hybrid_fusion_calibration.py::
        #        test_degraded_path_order_matches_raw_bm25）—— 那等于把"修非确定性"
        #       做成"改本路排序"；
        #     · 汇合序（BM25 路序 → Embedding 路序）本身**确定**（两路各自稳定），
        #       且与上述契约**逐位相容**：并列时谁在 BM25 路里靠前，谁就靠前。
        #   效果：给定输入（同一索引 + 同一查询 + 同一向量腿状态）⇒ 同一序列。
        _cand_pos = {doc_id: i for i, doc_id in enumerate(candidate_order)}
        fused.sort(key=lambda x: (-x[1], _cand_pos[x[0]]))

        # [logger] 融合结果 top-5(最终返回,排查整体退化)
        logger.info(
            "[tool_router_hybrid] 融合结果: total=%d top5=%s",
            len(fused),
            [(d, round(s, 4)) for d, s in fused[:5]],
        )

        return fused[:top_k]

    @property
    def degraded(self) -> bool:
        """是否降级到纯 BM25(Embedding 不可用)"""
        return self._tools_loaded and not self._embedding.available

    def embedding_health(self) -> dict:
        """【W2/TASK-03】把 embedding worker 的崩溃/降级状态透出到检索器层。

        Why:上层健康探针拿到的句柄是 HybridRetriever(见 get_hybrid_retriever),
        不是内部的 EmbeddingIndex;没有这层透传,`worker_health()` 就无法被
        /api/health 之类的探针读取(接线动作归 TASK-04,本任务只提供出口)。
        """
        health = self._embedding.worker_health()
        health["retriever_degraded"] = self.degraded
        return health

    def close(self) -> None:
        """关闭向量腿 worker 子进程（**公开清理口**；E1-F1-A/优先级 4）。

        Why: HybridRetriever 是上层持有的句柄（get_hybrid_retriever 单例），
        而 worker 的回收口原本只在 EmbeddingIndex 内部、且没有对外暴露。
        这里补一条从"上层句柄"直达"子进程回收"的路，使调用方（应用关闭、测试收尾）
        能确定性地收干净，而不是把回收交给操作系统或 atexit 兜底。
        """
        self._embedding.close()


# ════════════════════════════════════════════════════════════
#  模块级单例 + 公共入口
# ════════════════════════════════════════════════════════════

_hybrid_instance: Optional[HybridRetriever] = None
_hybrid_lock = threading.Lock()


def get_hybrid_retriever() -> Optional[HybridRetriever]:
    """获取 HybridRetriever 单例(双重检查锁,线程安全)

    Returns:
        HybridRetriever 实例;初始化失败返回 None
    """
    global _hybrid_instance
    if _hybrid_instance is not None:
        return _hybrid_instance
    with _hybrid_lock:
        if _hybrid_instance is not None:
            return _hybrid_instance
        try:
            _hybrid_instance = HybridRetriever()
        except Exception as e:
            logger.warning("[tool_router_hybrid] HybridRetriever 初始化失败: %s", e)
            _hybrid_instance = None
        return _hybrid_instance


def reset_hybrid_retriever() -> None:
    """重置单例(测试用)

    Why: 测试间需隔离单例状态,避免索引残留
    """
    global _hybrid_instance
    with _hybrid_lock:
        _hybrid_instance = None


# ════════════════════════════════════════════════════════════════════════════
# 【B3-W】早退分支可观测接线（trace_id 关联 + 零召回计数）
#
# 背景：hybrid_select_tools 有 7 条 `return None` 早退分支，此前**全部静默**
#   —— 既无计数也无事件，无法回答"混合检索到底多久没给出工具、为什么"。
#
# 【不易·语义铁律】zero_recall_total 只记「**本来该召回却没召回**」。
#   能力未就绪（helper/索引没起来）、调用方白名单约束、异常路径
#   **一律不计入** —— 否则该指标会被永久污染成噪声，失去验收价值。
#
# 逐分支归类（理由见 docs/audit_skill_governance/B3W_REPORT.md §1）：
#   :1656 helper_unavailable     能力未就绪 -> 只留痕，不计数
#   :1660 retriever_unavailable  能力未就绪 -> 只留痕，不计数
#   :1698 results_none           异常降级   -> 只留痕，不计数
#   :1700 results_empty          零召回     -> 计数 + 事件
#   :1742 whitelist_empty        正常回退   -> 只留痕，不计数
#   :1757 sort_empty             零召回     -> 计数 + 事件
#   :1764 except 异常路径         异常降级   -> 不计数（已有 WARNING 留痕）
# ════════════════════════════════════════════════════════════════════════════

# 归入 zero_recall_total 的 reason —— **稳定短标签，禁止拼动态字符串**
# （动态串会让标签/事件爆炸且无法枚举告警）
ZERO_RECALL_REASONS = ("results_empty", "sort_empty")

# 【不易】惰性取依赖：埋点是旁路，绝不让 prometheus / orchestrator 成为本模块
#   的**导入期硬依赖**（沿用 routing_observability._record_route_fn 的负结果缓存写法）。
_ZERO_RECALL_FN = None
_ZERO_RECALL_PROBED = False
_TRACE_ID_FN = None
_TRACE_ID_PROBED = False


def _zero_recall_fn():
    """惰性取 agent.monitoring.prometheus.record_zero_recall（不可用 -> None）"""
    global _ZERO_RECALL_FN, _ZERO_RECALL_PROBED
    if not _ZERO_RECALL_PROBED:
        _ZERO_RECALL_PROBED = True
        try:
            from agent.monitoring.prometheus import record_zero_recall as _fn
            _ZERO_RECALL_FN = _fn
        except Exception:
            _ZERO_RECALL_FN = None
    return _ZERO_RECALL_FN


def _retrieval_trace_id() -> str:
    """当前请求的路由 trace_id（无请求上下文 -> ""）

    Why 必须取 routing_observability.current_trace_id()：只有与
        emit_route_decision 用**同源同名**的值（trace_id_ctx），"检索决策"
        与"路由决策"两条日志才能被同一个 trace 串起来；在工具侧另生成 id
        等于没接。
    """
    global _TRACE_ID_FN, _TRACE_ID_PROBED
    if not _TRACE_ID_PROBED:
        _TRACE_ID_PROBED = True
        try:
            from agent.orchestrator.routing_observability import current_trace_id as _fn
            _TRACE_ID_FN = _fn
        except Exception:
            _TRACE_ID_FN = None
    if _TRACE_ID_FN is None:
        return ""
    try:
        return _TRACE_ID_FN() or ""
    except Exception:
        return ""


def _note_retrieval_early_exit(reason: str) -> None:
    """早退分支的**唯一出口**：所有分支留痕；仅"零召回"分支计入 zero_recall_total。

    Args:
        reason: 稳定短标签（见 ZERO_RECALL_REASONS 与 B3W_REPORT.md §1 分类表）

    【不易】埋点失败静默：绝不影响"返回 None 让调用方回退"这条主路径。
    """
    try:
        if reason in ZERO_RECALL_REASONS:
            _fn = _zero_recall_fn()
            if _fn is not None:
                # record_zero_recall 自己完成 counter +1 与 action=tool.zero_recall 事件
                _fn(reason, trace_id=_retrieval_trace_id())
                return
        logger.debug(log_dict({
            'module_name': 'tool_router_hybrid',
            'action': 'tool.retrieval.early_exit',
            'message': '混合检索早退: reason=%s（未计入 zero_recall_total）' % reason,
            'reason': reason,
            'zero_recall': reason in ZERO_RECALL_REASONS,
            'trace_id_ctx': _retrieval_trace_id(),
        }))
    except Exception:
        logger.debug("[tool_router_hybrid] 早退埋点失败(忽略)", exc_info=True)


def hybrid_select_tools(
    user_input: str,
    enabled_whitelist: Optional[list[str]] = None,
    max_tools: int = 25,
    top_k: int = _DEFAULT_TOP_K,
    alpha: Optional[float] = None,
) -> Optional[list[str]]:
    """混合检索选择工具 — 失败返回 None 让调用方回退

    【不易】任何异常都返回 None,让调用方回退到 get_tools_for_input(关键词分类)
    【变易】alpha 可配:显式参数 > 环境变量 AGENT_HYBRID_ALPHA > 默认 0.5;top_k 默认 10
    【简易】调用方 1 行改造:`hybrid_select_tools(...) or get_tools_for_input(...)`

    Args:
        user_input: 用户原始输入文本
        enabled_whitelist: 启用工具白名单,None 表示不限制
        max_tools: 返回工具数上限,默认 25
        top_k: 检索候选数,默认 10
        alpha: BM25/Embedding 融合权重,None 表示用环境变量/默认 0.5

    Returns:
        排序+截断后的工具名列表;None 表示本次未启用/检索失败/无候选
    """
    # helper 不可用 → 直接返回 None
    if not _HELPER_AVAILABLE:
        # 【B3-W】能力未就绪：检索helper没导入起来，**不是**零召回（不计数）
        _note_retrieval_early_exit("helper_unavailable")
        return None

    retriever = get_hybrid_retriever()
    if retriever is None or not retriever.available:
        # 【B3-W】能力未就绪：索引未加载/检索器初始化失败，**不是**零召回（不计数）
        _note_retrieval_early_exit("retriever_unavailable")
        return None

    start_time = time.perf_counter()
    bm25_count = 0
    embed_count = 0
    fused_count = 0
    # 【W5/L29】原始分量必须在 try 之前初始化：finally 里无条件引用它们，
    # 否则"try 中提前抛异常"时事件记录会 NameError（被 except 吞掉 ⇒ 静默丢事件）。
    raw_bm25_top5 = None
    raw_embed_top5 = None
    bm25_half_saturation = None
    cosine_floor = None
    filtered_by_min_coverage = None
    bm25_considered = None
    min_idf_coverage = None
    filtered_preview = None
    degraded = retriever.degraded
    tools_preview: list[str] = []

    try:
        # 覆盖 alpha(调用方显式指定时;否则沿用 retriever 默认=环境变量/0.5)
        if alpha is not None:
            retriever._alpha = alpha
        effective_alpha = retriever._alpha

        # 【不易】候选池必须 ≥ max_tools：否则截断分支永不触发，返回的只是检索器
        #         给的 top_k 个，下面的类别优先级排序与 PINNED_TOOLS 补回全部失效
        #         （历史缺陷：top_k=10 < max_tools=25 ⇒ 复合请求里补不进任何工具）。
        #         这里只放大**检索候选池**，不改变调用方返回的上限语义。
        pool = int(top_k) if top_k and top_k > 0 else int(_DEFAULT_TOP_K)
        if max_tools and max_tools > 0 and pool < max_tools:
            logger.debug(
                "[tool_router_hybrid] 候选池 %d < max_tools %d,自动放大到 %d（否则截断/补回失效）",
                pool, max_tools, max_tools)
            pool = int(max_tools)

        results = retriever.query(user_input, top_k=pool)
        if results is None:
            # 【B3-W】异常降级：query() 的 None 语义是"检索失败"（索引重建期
            # 抢锁失败 / 内部异常），不是"搜了但没有" ⇒ 不计零召回。
            _note_retrieval_early_exit("results_none")
            return None
        if not results:
            # 【B3-W】零召回：检索**已执行成功**且候选为 0 ⇒ 该召回却没召回。
            _note_retrieval_early_exit("results_empty")
            return None  # 空结果让调用方回退

        # 候选工具集合：检索命中 ∪ 关键词分类命中的类别工具
        # 【为什么必须取并集（2026-09-17 实测）】
        #   只有检索命中：BM25 的中文弱点会漏（例：「帮我写代码并运行测试」召回不到
        #     write_file/edit/grep ⇒ 命中却不给出，实测 5 个必需工具缺 3 个）。
        #   只有类别命中：没有相关度，`shell_execute` 会被同类低相关工具挤掉。
        #   两者并集 = 检索提供**精度与排序**，类别提供**召回兜底**。
        selected: set[str] = {tool_name for tool_name, _ in results}
        try:
            _cats = _classify_user_input(user_input)
            for _c in _cats:
                _info = TOOL_CATEGORIES.get(_c)
                if _info:
                    selected.update(_info["tools"])
        except Exception as _ce:  # noqa: BLE001 类别兜底失败不影响检索主路径
            logger.debug("[tool_router_hybrid] 类别兜底候选失败(忽略): %s", _ce)

        # 统计从 HybridRetriever._query_locked 写入的中间统计读取
        # Why: results 是融合后 top_k,无法反映 BM25/Embedding 各自召回数;
        #      HybridRetriever._query_locked 在融合前已记录到 _last_query_stats
        stats = getattr(retriever, "_last_query_stats", {}) or {}
        bm25_count = int(stats.get("bm25_candidates", 0))
        embed_count = int(stats.get("embed_candidates", 0))
        fused_count = int(stats.get("fused_candidates", 0))
        # 【W5/L29】原始分量随事件落盘：只有它们在事件流里，第三方才能反推
        # 一条融合分是怎么算出来的（此前只走到"暂存"，消费方只读三个计数 ⇒ 可用 ≠ 已记录）。
        raw_bm25_top5 = stats.get("raw_bm25_top5")
        raw_embed_top5 = stats.get("raw_embed_top5")
        bm25_half_saturation = stats.get("bm25_half_saturation")
        cosine_floor = stats.get("cosine_floor")
        # 【A8-G1】召回过滤读数（被下限滤掉的候选数 / 参与判定的候选数 / 生效阈值）
        filtered_by_min_coverage = stats.get("bm25_filtered_by_min_coverage")
        bm25_considered = stats.get("bm25_considered")
        min_idf_coverage = stats.get("min_idf_coverage")
        filtered_preview = stats.get("bm25_filtered_preview")

        # 白名单交集
        if enabled_whitelist is not None:
            whitelist_set = set(enabled_whitelist)
            selected &= whitelist_set
            if not selected:
                # 【B3-W】正常回退：检索**已召回**，是调用方白名单把它们全过滤掉
                # ⇒ 属调用方约束导致的空集，不计零召回（否则白名单调用会污染指标）。
                _note_retrieval_early_exit("whitelist_empty")
                return None  # 白名单过滤后无候选,让调用方回退

        # 【E1-D · 生产非确定性修复】下发阶的候选必须按**确定次序**交给 helper。
        #   为什么：helper 内部是 sorted(selected, key=priority)（**稳定排序**），
        #   同优先级并列项的先后 = 输入迭代序；原来传的是 set ⇒ 迭代序随
        #   字符串哈希随机化变 ⇒ 当 max_tools 截断点落在并列块内部时，
        #   下发的**成员**随进程变。实测（真实索引、AGENT_HYBRID_EMBEDDING=0、
        #   种子 0/1/2）：rc-007 下发集对称差 2（run_tests / git / data_format_detect
        #   之间换位）、rc-046 同样差 2（cancel_task / list_async_tasks），
        #   而**决策层**（融合 top10、n_ranked）三种子逐位相同 —— 即 E1-C 观察到的
        #   "决策层全同、下发层差 1~3 条"。
        #   次序取：相关度序（融合序，已确定）在前 → 类别兜底按 TOOL_CATEGORIES 声明序。
        ordered_candidates: list[str] = []
        _ordered_seen: set[str] = set()
        for _name, _score in results:
            if _name in selected and _name not in _ordered_seen:
                _ordered_seen.add(_name)
                ordered_candidates.append(_name)
        for _cat_key in TOOL_CATEGORIES:
            for _t in (TOOL_CATEGORIES.get(_cat_key) or {}).get("tools", []):
                if _t in selected and _t not in _ordered_seen:
                    _ordered_seen.add(_t)
                    ordered_candidates.append(_t)
        # 兜底：白名单/其它来源引入、且不在上述两处的候选（排序只为可复现，不为取序）
        for _t in sorted(selected - _ordered_seen):
            ordered_candidates.append(_t)

        # 相关度优先 + 类别优先级补位 + 数量截断(复用 tool_router helper)
        # 传入所有类别,确保每个工具取到正确 priority
        # 【关键】preferred_order 传检索器的**相关度序**（融合后仍按分数降序）：
        #   hybrid 有真实相关度，若一律按类别 priority 重排，会出现"相关度被优先级覆盖"
        #   —— 例如「读取 PDF 的内容」里 pdf 类别 priority=6，web(1)/file(2) 会吃光名额，
        #   read_pdf 被挤到 max_tools 之外，结果是"命中了却拿不到"。
        #   传相关度序后：相关且命中的工具优先保留，类别序只用来**补位**。
        # 【E1-D】类别集合也改为**确定次序**（TOOL_CATEGORIES 声明序）：
        #   helper 里 matched_cats 按 priority 稳定排序，而本表存在**同优先级**
        #   的两个类别（code=5 / knowledge=5）⇒ 传 set 时这两类的先后随哈希变，
        #   而它们决定 floors（类别保底）的先后，进而影响截断点上的成员。
        _cat_set = retriever._all_categories or set(TOOL_CATEGORIES.keys())
        categories = [c for c in TOOL_CATEGORIES if c in _cat_set]
        relevance_order = [name for name, _ in results]
        # 【E1-D】第一个实参传**有序候选序列**（不是 set）：helper 只对它做成员判定
        #   （t in selected 成员判定）与 sorted(...)，故序列完全兼容其契约，而并列项的
        #   次序从此确定。**未改 helper 一行**（agent/tool_router.py 不在本卡范围）。
        result = _apply_alias_merge_and_priority_sort(
            ordered_candidates, categories, max_tools, preferred_order=relevance_order)

        if not result:
            # 【B3-W】零召回：候选集合非空，但合并/别名/优先级排序把候选全丢了
            # ⇒ 该召回却没召回（漏斗末端故障）。
            _note_retrieval_early_exit("sort_empty")
            return None

        tools_preview = result[:10]
        return result
    except Exception as e:
        logger.warning("[tool_router_hybrid] hybrid_select_tools 异常: %s", e)
        return None
    finally:
        # 记录检索指标(安全降级:recorder 不可用不影响主路径)
        latency_ms = (time.perf_counter() - start_time) * 1000
        if ToolTraceRecorder is not None:
            try:
                ToolTraceRecorder.instance().record_tool_retrieval(
                    query=user_input,
                    top_k=top_k,
                    latency_ms=latency_ms,
                    bm25_candidates=bm25_count,
                    embed_candidates=embed_count,
                    fused_candidates=fused_count,
                    alpha=effective_alpha,
                    degraded=degraded,
                    tools_preview=tools_preview,
                    # 【W5/L29】原始 BM25/Embedding 分量与校准量纲（可选参数，默认 None）
                    raw_bm25_top5=raw_bm25_top5,
                    raw_embed_top5=raw_embed_top5,
                    bm25_half_saturation=bm25_half_saturation,
                    cosine_floor=cosine_floor,
                    # 【A8-G1】护栏的召回过滤读数同样随事件落盘（静默过滤不可接受）
                    bm25_filtered_by_min_coverage=filtered_by_min_coverage,
                    bm25_considered=bm25_considered,
                    min_idf_coverage=min_idf_coverage,
                    bm25_filtered_preview=filtered_preview,
                    # 【B3-W】trace 关联：与 routing_observability 的 route decision
                    # 同源（current_trace_id），使"检索决策"与"本次请求路由决策"
                    # 能用同一个 trace_id_ctx 串起来。
                    trace_id=_retrieval_trace_id(),
                )
            except Exception:
                pass


__all__ = [
    "BM25Index",
    "EmbeddingIndex",
    "HybridRetriever",
    "get_hybrid_retriever",
    "reset_hybrid_retriever",
    "hybrid_select_tools",
]
