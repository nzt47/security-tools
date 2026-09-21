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
import logging
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
# worker ready 信号读取超时(秒)
# 【变易】它是启动等待的**唯一数值来源**:EmbeddingIndex._WORKER_STARTUP_TIMEOUT
#         只是它的别名(历史上那处独立写着 60,从未被读取,与实际生效的 30 不一致)。
#         MiniLM(约 470MB)冷启动远快于 reranker 的 2.3GB 模型,故 30s 足够。
_WORKER_READY_TIMEOUT = 30.0
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


def _ensure_st_checked() -> bool:
    """检测 sentence_transformers + 模型加载是否安全可用(子进程探测 + 缓存)

    优先级:
      1. 环境变量 AGENT_HYBRID_EMBEDDING 强制覆盖(0=禁用, 1=启用)
      2. 内存缓存(_PROBE_RESULT)
      3. 文件缓存(data/.embedding_probe,**仅在 _PROBE_CACHE_TTL 内有效**)
      4. 子进程探测(首次或缓存过期/不可信时,结果写回文件缓存)
    """
    global _PROBE_RESULT
    if _PROBE_RESULT is not None:
        return _PROBE_RESULT

    with _PROBE_LOCK:
        if _PROBE_RESULT is not None:
            return _PROBE_RESULT

        # 1. 环境变量强制覆盖
        env_val = os.environ.get("AGENT_HYBRID_EMBEDDING", "").strip().lower()
        if env_val in ("0", "false", "no", "off"):
            _PROBE_RESULT = False
            logger.info("[tool_router_hybrid] AGENT_HYBRID_EMBEDDING=0,禁用 Embedding(纯 BM25)")
            return False
        if env_val in ("1", "true", "yes", "on"):
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
import json, os, sys, base64
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

def main():
    model_name = sys.argv[1] if len(sys.argv) > 1 else "paraphrase-multilingual-MiniLM-L12-v2"
    try:
        from sentence_transformers import SentenceTransformer
        import time
        t0 = time.time()
        model = SentenceTransformer(model_name)
        load_time = time.time() - t0
        print(json.dumps({"type": "ready", "load_time_sec": round(load_time, 2),
                          "load_source": model_name}), flush=True)
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
        return {
            "mode": "bm25_only" if self._init_failed else "hybrid",
            "init_failed": self._init_failed,
            "worker_alive": alive,
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
        """子进程存活 + embeddings 已计算 + doc_ids 非空 + 未失败"""
        if self._init_failed:
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
        if self._proc is not None and self._proc.poll() is None:
            return True

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
                logger.info(log_dict({'module_name': 'tool_router_hybrid', 'action': 'embedding.worker.ready', 'model': self._model_name, 'load_time_sec': self._load_time_sec, 'load_source': self._load_source}))
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
        """编码所有 pending 文档(调用方持锁)"""
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

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """搜索查询,返回 [(doc_id, cosine_similarity)] 列表(按相似度降序)

        【变易】query embedding LRU 缓存:重复查询跳过子进程通信
        """
        if not _HAS_NUMPY:
            return []
        if not self._ensure_worker():
            return []
        with self._lock:
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
        # 注意:tool_router_hybrid._ensure_st_checked 无调用点,此处为实际 env gate。
        _embed_env = os.environ.get("AGENT_HYBRID_EMBEDDING", "").strip().lower()
        _embed_disabled = _embed_env in ("0", "false", "no", "off")
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
        all_candidates: set[str] = set()
        all_candidates.update(d for d, _ in bm25_norm)
        all_candidates.update(d for d, _ in embed_norm)

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
        for doc_id in all_candidates:
            bm25_score = bm25_map.get(doc_id, 0.0)
            embed_score = embed_map.get(doc_id, 0.0)
            # 若 Embedding 不可用,只用 BM25(alpha=1.0 等效)
            if not self._embedding.available or not embed_norm:
                final = bm25_score
            else:
                final = self._alpha * bm25_score + (1 - self._alpha) * embed_score
            fused.append((doc_id, final))

        fused.sort(key=lambda x: x[1], reverse=True)

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
        return None

    retriever = get_hybrid_retriever()
    if retriever is None or not retriever.available:
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
            return None
        if not results:
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
                return None  # 白名单过滤后无候选,让调用方回退

        # 相关度优先 + 类别优先级补位 + 数量截断(复用 tool_router helper)
        # 传入所有类别,确保每个工具取到正确 priority
        # 【关键】preferred_order 传检索器的**相关度序**（融合后仍按分数降序）：
        #   hybrid 有真实相关度，若一律按类别 priority 重排，会出现"相关度被优先级覆盖"
        #   —— 例如「读取 PDF 的内容」里 pdf 类别 priority=6，web(1)/file(2) 会吃光名额，
        #   read_pdf 被挤到 max_tools 之外，结果是"命中了却拿不到"。
        #   传相关度序后：相关且命中的工具优先保留，类别序只用来**补位**。
        categories = retriever._all_categories or set(TOOL_CATEGORIES.keys())
        relevance_order = [name for name, _ in results]
        result = _apply_alias_merge_and_priority_sort(
            selected, categories, max_tools, preferred_order=relevance_order)

        if not result:
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
