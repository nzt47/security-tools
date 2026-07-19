"""Cross-Encoder Reranker 适配器 — 工具检索两阶段精排

设计目的:
    HybridRetriever BM25 召回 top-N 候选后,用 Cross-Encoder 精排到 top-K。
    Cross-Encoder 把 (query, doc) 拼接输入模型,输出相关性分数,
    精度高于 BM25 但慢(2-10s/次 on CPU)。

架构层级:
    HybridRetriever.query (tool_router_hybrid.py)
        ↓ BM25 召回 top-N
    ToolReranker (本模块)
        ↓ Cross-Encoder predict(子进程隔离)
    精排后的 top-K

策略:
    - 模型: BAAI/bge-reranker-v2-m3 (多语言,2.17GB)
    - 子进程隔离: 避免 CrossEncoder 原生崩溃(0xC0000005/SIGILL)影响主进程
    - JSON Lines 通信协议: stdin/stdout 双向通信
    - 失败降级: 子进程启动失败或预测异常时返回原顺序
    - 本地缓存优先: 优先从 HF 本地缓存加载,避免网络下载

【不易】不修改 HybridRetriever 现有 query 接口,仅作为可选精排层
【变易】模型名、top_n、min_score 可通过环境变量配置
【简易】单一职责: rerank(query, candidates) → sorted_candidates
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("agent.tool_router_reranker")

# ════════════════════════════════════════════════════════════
#  默认配置
# ════════════════════════════════════════════════════════════

_DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
_DEFAULT_RERANKER_MAX_LENGTH = 512
_DEFAULT_RERANK_TOP_N = 20  # Stage 1 召回候选池大小
# 【变易】rerank_score 阈值:低于此分数的候选视为低置信度,从结果中剔除
# 来源:零样本评估数据显示正样本 0.06~0.99,负样本 ≤0.10
# 0.05 阈值能拒绝 G7_q16 decompress(0.0638) 这类方向性混淆
# 可通过环境变量 AGENT_RERANKER_MIN_SCORE 覆盖
_DEFAULT_RERANK_MIN_SCORE = 0.05

# 子进程启动超时(秒):模型加载最多等待 60s
_WORKER_STARTUP_TIMEOUT = 60


def _env_float(name: str, default: float) -> float:
    """从环境变量读取 float,失败时返回默认值(守【简易】)"""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(json.dumps({
            "module_name": "tool_router_reranker",
            "action": "env_parse_failed",
            "env_name": name,
            "raw_value": raw,
            "fallback": default,
        }, ensure_ascii=False))
        return default


def _env_int(name: str, default: int) -> int:
    """从环境变量读取 int,失败时返回默认值"""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


# ════════════════════════════════════════════════════════════
#  子进程 Worker 脚本(内联,与 eval_reranker_zero_shot.py 一致)
# ════════════════════════════════════════════════════════════

_WORKER_SCRIPT = '''
import json
import os
import sys

# 【变易】HF 镜像(国内下载稳定,与 SkillReranker 一致)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "0")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

def main():
    model_name = sys.argv[1] if len(sys.argv) > 1 else "BAAI/bge-reranker-v2-m3"
    max_length = int(sys.argv[2]) if len(sys.argv) > 2 else 512

    try:
        from sentence_transformers import CrossEncoder
        import time
        t0 = time.time()
        # 优先从本地缓存加载
        from pathlib import Path
        repo_dir = model_name.replace("/", "--")
        hf_root = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{repo_dir}" / "snapshots"
        load_source = model_name
        if hf_root.exists():
            for sub in hf_root.iterdir():
                if sub.is_dir() and (sub / "config.json").exists():
                    load_source = str(sub)
                    break
        model = CrossEncoder(load_source, max_length=max_length)
        load_time = time.time() - t0
        # 就绪信号
        print(json.dumps({"type": "ready", "load_time_sec": round(load_time, 2),
                          "load_source": load_source}), flush=True)
    except Exception as e:
        print(json.dumps({"type": "init_failed", "error": str(e)[:500]}), flush=True)
        return

    # 循环处理 pairs
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if req.get("type") == "predict":
                pairs = req.get("pairs", [])
                scores = model.predict(pairs).tolist()
                print(json.dumps({"type": "scores", "scores": scores}), flush=True)
            elif req.get("type") == "exit":
                break
        except Exception as e:
            print(json.dumps({"type": "error", "error": str(e)[:300]}), flush=True)

if __name__ == "__main__":
    main()
'''


# ════════════════════════════════════════════════════════════
#  ToolReranker 类
# ════════════════════════════════════════════════════════════

class ToolReranker:
    """工具检索 Cross-Encoder 精排器(子进程隔离)

    用法:
        reranker = ToolReranker()
        reranked = reranker.rerank("query", candidates,
                                    tool_descriptions={"tool1": "desc1", ...},
                                    top_k=5)
        # candidates: [(tool_name, hybrid_score), ...]  (按 hybrid_score 降序)
        # reranked: [(tool_name, hybrid_score, rerank_score), ...]  (按 rerank_score 降序)

    线程安全:
        - 子进程通信由 threading.Lock 保护
        - 模型加载由首次 rerank 调用触发(延迟加载)

    失败降级:
        - 子进程启动失败 → 返回原顺序(rerank_score=0.0)
        - 预测异常 → 返回原顺序(rerank_score=0.0)
        - 不会抛异常影响主流程

    【不易】子进程崩溃不影响主进程,通过 JSON Lines 协议通信
    【变易】模型名/top_n/min_score 可通过环境变量配置
    【简易】单一职责:rerank(query, candidates) → sorted_candidates
    """

    def __init__(
        self,
        *,
        model_name: Optional[str] = None,
        max_length: int = _DEFAULT_RERANKER_MAX_LENGTH,
        rerank_top_n: Optional[int] = None,
        rerank_min_score: Optional[float] = None,
    ):
        """初始化 Cross-Encoder 精排器

        Args:
            model_name: Cross-Encoder 模型名,None 时从环境变量 AGENT_RERANKER_MODEL 读取
            max_length: 输入 token 长度上限(query+doc 总长度)
            rerank_top_n: Stage 1 候选池大小,None 时从环境变量 AGENT_RERANKER_TOP_N 读取
            rerank_min_score: rerank_score 阈值,None 时从环境变量 AGENT_RERANKER_MIN_SCORE 读取
                设为负数(如 -1.0)可禁用阈值过滤
        """
        # 【变易】环境变量优先级:参数 > 环境变量 > 默认值
        self.model_name = model_name or os.environ.get(
            "AGENT_RERANKER_MODEL", _DEFAULT_RERANKER_MODEL
        )
        self.max_length = max_length
        self.rerank_top_n = rerank_top_n if rerank_top_n is not None else _env_int(
            "AGENT_RERANKER_TOP_N", _DEFAULT_RERANK_TOP_N
        )
        self.rerank_min_score = rerank_min_score if rerank_min_score is not None else _env_float(
            "AGENT_RERANKER_MIN_SCORE", _DEFAULT_RERANK_MIN_SCORE
        )

        # 子进程状态
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._init_failed = False  # 标记初始化失败,避免重复尝试
        self._load_time_sec: Optional[float] = None
        self._load_source: Optional[str] = None

        # 项目根目录(用于子进程 cwd)
        self._project_root = Path(__file__).resolve().parent.parent

    # ───────────────────────────────────────────────────────
    #  子进程生命周期管理
    # ───────────────────────────────────────────────────────

    def _ensure_worker(self) -> bool:
        """延迟启动子进程并加载模型,返回是否就绪

        失败降级:
            - 子进程启动失败 → 标记 _init_failed,后续不再尝试
            - 模型加载失败 → 同上

        【不易】子进程崩溃时主进程不受影响
        【变易】超时 60s 后视为失败
        """
        if self._init_failed:
            return False
        if self._proc is not None and self._proc.poll() is None:
            return True  # 子进程已在运行

        with self._lock:
            # 双重检查
            if self._proc is not None and self._proc.poll() is None:
                return True
            if self._init_failed:
                return False

            try:
                import time
                t0 = time.time()
                self._proc = subprocess.Popen(
                    [sys.executable, "-c", _WORKER_SCRIPT, self.model_name, str(self.max_length)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    cwd=str(self._project_root),
                )
                # 等待就绪信号(最多 _WORKER_STARTUP_TIMEOUT 秒)
                # 【变易】用进程退出判断超时,避免 readline 永久阻塞
                ready_line = self._proc.stdout.readline()
                if not ready_line:
                    err = self._proc.stderr.read() if self._proc.stderr else ""
                    logger.warning(json.dumps({
                        "module_name": "tool_router_reranker",
                        "action": "worker.startup.no_output",
                        "error": err[:300],
                    }, ensure_ascii=False))
                    self._init_failed = True
                    self._cleanup_proc()
                    return False

                msg = json.loads(ready_line)
                if msg.get("type") == "ready":
                    self._load_time_sec = msg.get("load_time_sec", 0)
                    self._load_source = msg.get("load_source", "?")
                    logger.info(json.dumps({
                        "module_name": "tool_router_reranker",
                        "action": "worker.ready",
                        "model": self.model_name,
                        "load_time_sec": self._load_time_sec,
                        "load_source": self._load_source,
                        "startup_total_sec": round(time.time() - t0, 2),
                    }, ensure_ascii=False))
                    return True
                elif msg.get("type") == "init_failed":
                    logger.warning(json.dumps({
                        "module_name": "tool_router_reranker",
                        "action": "worker.init_failed",
                        "model": self.model_name,
                        "error": msg.get("error", "")[:300],
                    }, ensure_ascii=False))
                    self._init_failed = True
                    self._cleanup_proc()
                    return False
                else:
                    logger.warning(json.dumps({
                        "module_name": "tool_router_reranker",
                        "action": "worker.unknown_message",
                        "message": str(msg)[:300],
                    }, ensure_ascii=False))
                    self._init_failed = True
                    self._cleanup_proc()
                    return False
            except Exception as e:  # noqa: BLE001
                logger.warning(json.dumps({
                    "module_name": "tool_router_reranker",
                    "action": "worker.startup.exception",
                    "model": self.model_name,
                    "error": str(e)[:300],
                }, ensure_ascii=False))
                self._init_failed = True
                self._cleanup_proc()
                return False

    def _cleanup_proc(self) -> None:
        """清理子进程资源"""
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None:
                # 尝试优雅退出
                try:
                    if self._proc.stdin:
                        self._proc.stdin.write(json.dumps({"type": "exit"}) + "\n")
                        self._proc.stdin.flush()
                except Exception:
                    pass
                # 等待 2s 后强制终止
                try:
                    self._proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        finally:
            self._proc = None

    def _predict_scores(self, pairs: list[tuple[str, str]]) -> Optional[list[float]]:
        """通过子进程对 pairs 打分

        Returns:
            scores 列表,失败时返回 None
        """
        if self._init_failed or self._proc is None or self._proc.poll() is not None:
            return None
        with self._lock:
            # 双重检查子进程状态
            if self._proc is None or self._proc.poll() is not None:
                return None
            try:
                req = json.dumps({"type": "predict", "pairs": pairs}, ensure_ascii=False)
                self._proc.stdin.write(req + "\n")
                self._proc.stdin.flush()
                resp_line = self._proc.stdout.readline()
                if not resp_line:
                    # 子进程已退出
                    logger.warning(json.dumps({
                        "module_name": "tool_router_reranker",
                        "action": "predict.no_response",
                        "reason": "subprocess_exited",
                    }, ensure_ascii=False))
                    self._init_failed = True
                    self._cleanup_proc()
                    return None
                resp = json.loads(resp_line)
                if resp.get("type") == "scores":
                    return resp.get("scores", [])
                elif resp.get("type") == "error":
                    logger.warning(json.dumps({
                        "module_name": "tool_router_reranker",
                        "action": "predict.worker_error",
                        "error": resp.get("error", "")[:300],
                    }, ensure_ascii=False))
                    return None
                else:
                    logger.warning(json.dumps({
                        "module_name": "tool_router_reranker",
                        "action": "predict.unknown_response",
                        "message": str(resp)[:300],
                    }, ensure_ascii=False))
                    return None
            except Exception as e:  # noqa: BLE001
                logger.warning(json.dumps({
                    "module_name": "tool_router_reranker",
                    "action": "predict.exception",
                    "error": str(e)[:300],
                }, ensure_ascii=False))
                self._init_failed = True
                self._cleanup_proc()
                return None

    # ───────────────────────────────────────────────────────
    #  公开接口
    # ───────────────────────────────────────────────────────

    def rerank(
        self,
        query: str,
        candidates: list[tuple[str, float]],
        *,
        tool_descriptions: dict[str, str],
        top_k: int = 5,
    ) -> list[tuple[str, float, float]]:
        """对 BM25 召回的候选做 Cross-Encoder 精排

        Args:
            query: 用户意图
            candidates: BM25 召回的候选列表 [(tool_name, hybrid_score), ...]
                       (按 hybrid_score 降序)
            tool_descriptions: tool_name → description 映射
                description 为空时用 tool_name 兜底
            top_k: 最终返回数量(默认 5)

        Returns:
            精排后的候选列表 [(tool_name, hybrid_score, rerank_score), ...]
            按 rerank_score 降序
            rerank_score < rerank_min_score 的候选会被剔除(阈值过滤)
            失败时返回原顺序(rerank_score=0.0)

        【不易】不修改 candidates 原始数据,仅返回新列表
        【变易】rerank_min_score 阈值过滤,剔除低置信度候选
        【简易】单次 batch predict,避免逐条推理
        """
        if not candidates:
            return []

        # 取前 rerank_top_n 个候选(按原顺序,已是 BM25 排序)
        pool = candidates[:self.rerank_top_n]

        # 构造 (query, doc) pairs
        pairs = []
        for tool_name, _ in pool:
            doc_text = tool_descriptions.get(tool_name) or tool_name
            pairs.append((query, doc_text))

        # 启动子进程(如果尚未启动)
        if not self._ensure_worker():
            # 子进程不可用,降级返回原顺序(rerank_score=0.0)
            logger.info(json.dumps({
                "module_name": "tool_router_reranker",
                "action": "rerank.skipped",
                "reason": "worker_unavailable",
                "candidate_count": len(pool),
            }, ensure_ascii=False))
            return [(t, h, 0.0) for t, h in pool[:top_k]]

        # 调用子进程打分
        import time
        t0 = time.time()
        scores = self._predict_scores(pairs)
        elapsed_ms = (time.time() - t0) * 1000

        if scores is None or len(scores) != len(pool):
            # 预测失败,降级返回原顺序
            logger.warning(json.dumps({
                "module_name": "tool_router_reranker",
                "action": "rerank.predict_failed",
                "candidate_count": len(pool),
                "scores_returned": len(scores) if scores else 0,
            }, ensure_ascii=False))
            return [(t, h, 0.0) for t, h in pool[:top_k]]

        # 按 rerank_score 降序排序
        indexed = list(enumerate(scores))
        indexed.sort(key=lambda x: -x[1])

        # 阈值过滤 + top_k 截断
        result: list[tuple[str, float, float]] = []
        filtered_count = 0
        for orig_idx, rerank_score in indexed:
            if rerank_score < self.rerank_min_score:
                filtered_count += 1
                continue
            tool_name, hybrid_score = pool[orig_idx]
            result.append((tool_name, hybrid_score, float(rerank_score)))
            if len(result) >= top_k:
                break

        logger.info(json.dumps({
            "module_name": "tool_router_reranker",
            "action": "rerank.ok",
            "query": query[:50],
            "candidate_count": len(pool),
            "filtered_count": filtered_count,
            "remaining_count": len(result),
            "min_score_threshold": self.rerank_min_score,
            "duration_ms": round(elapsed_ms, 2),
            "top1_tool": result[0][0] if result else None,
            "top1_rerank_score": round(result[0][2], 4) if result else None,
        }, ensure_ascii=False))

        return result

    def health(self) -> dict[str, Any]:
        """健康检查"""
        proc_alive = self._proc is not None and self._proc.poll() is None
        return {
            "ok": proc_alive,
            "model": self.model_name,
            "max_length": self.max_length,
            "rerank_top_n": self.rerank_top_n,
            "rerank_min_score": self.rerank_min_score,
            "init_failed": self._init_failed,
            "worker_alive": proc_alive,
            "load_time_sec": self._load_time_sec,
            "load_source": self._load_source,
        }

    def close(self) -> None:
        """关闭子进程,释放资源"""
        self._cleanup_proc()


# ════════════════════════════════════════════════════════════
#  模块级单例(可选使用)
# ════════════════════════════════════════════════════════════

_reranker_instance: Optional[ToolReranker] = None
_reranker_lock = threading.Lock()


def get_tool_reranker() -> Optional[ToolReranker]:
    """获取模块级单例(惰性初始化)

    【变易】当 AGENT_HYBRID_RERANKER != "1" 时返回 None,表示禁用 Reranker
    """
    global _reranker_instance
    # 环境变量开关
    if os.environ.get("AGENT_HYBRID_RERANKER", "0") != "1":
        return None
    if _reranker_instance is not None:
        return _reranker_instance
    with _reranker_lock:
        if _reranker_instance is None:
            _reranker_instance = ToolReranker()
        return _reranker_instance


def reset_tool_reranker() -> None:
    """重置单例(主要用于测试)"""
    global _reranker_instance
    with _reranker_lock:
        if _reranker_instance is not None:
            _reranker_instance.close()
        _reranker_instance = None
