"""Docker 构建容错工具集（可复用）

【不易】Docker 构建不阻断是核心不变量——网络问题绝不阻止镜像构建
【变易】支持任意模型/资源列表，参数化超时和降级回调，适配不同服务
【简易】三层防护封装为单一模块，提供函数 + 装饰器 + 上下文管理器三种调用方式

提取自 docs/TLM_DOCKER_FAULT_TOLERANCE_RETROSPECTIVE.md 第 5 章容错机制设计。

三层防护架构：
    Layer 1 (脚本层):  try/except 捕获异常 → 返回 False → sys.exit(0)
    Layer 2 (构建层):  Dockerfile `|| echo` 兜底 → 构建继续
    Layer 3 (运行时层): HEALTHCHECK 检查缓存目录 → 标记 unhealthy

用法示例：
    # 方式 1: 函数调用（最简）
    from agent.utils.docker_fault_tolerance import safe_download_resources

    results = safe_download_resources(
        resources=["model-a", "model-b"],
        download_fn=lambda r: _do_download(r),
        timeout=300,
    )

    # 方式 2: 装饰器（保护已有下载函数）
    from agent.utils.docker_fault_tolerance import fault_tolerant_download

    @fault_tolerant_download(timeout=300)
    def download_model(name: str) -> bool:
        ...

    # 方式 3: 上下文管理器（批量操作）
    from agent.utils.docker_fault_tolerance import FaultTolerantBatch

    with FaultTolerantBatch(timeout=300) as batch:
        batch.add("model-a", download_fn=_download_a)
        batch.add("model-b", download_fn=_download_b)
        results = batch.execute()
"""
import os
import sys
import time
import functools
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# =====================================================================
# 数据结构
# =====================================================================

@dataclass
class DownloadResult:
    """单个资源下载结果"""
    resource: str
    success: bool
    elapsed: float
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        status = "OK" if self.success else "FAILED"
        err = f" ({self.error})" if self.error else ""
        return f"  [{status}] {self.resource} ({self.elapsed:.1f}s){err}"


@dataclass
class BatchResult:
    """批量下载结果"""
    results: List[DownloadResult] = field(default_factory=list)
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    elapsed: float = 0.0

    @property
    def all_succeeded(self) -> bool:
        return self.failed == 0

    @property
    def failed_resources(self) -> List[str]:
        return [r.resource for r in self.results if not r.success]

    def summary(self) -> str:
        lines = [
            "=" * 60,
            f"批量下载完成: {self.succeeded}/{self.total} 成功",
        ]
        if self.failed_resources:
            lines.append(f"失败资源: {self.failed_resources}")
            lines.append("[WARN] 部分资源下载失败，运行时可能需要网络访问")
        lines.append("=" * 60)
        return "\n".join(lines)


# =====================================================================
# Layer 1: 脚本层容错——try/except + 不阻断
# =====================================================================

def safe_download_single(
    resource: str,
    download_fn: Callable[[str], Any],
    timeout: int = 300,
    timeout_env_var: str = "HF_HUB_DOWNLOAD_TIMEOUT",
    on_success: Optional[Callable[[str], None]] = None,
    on_failure: Optional[Callable[[str, Exception], None]] = None,
) -> DownloadResult:
    """安全下载单个资源（Layer 1 脚本层容错）

    【不易】任何异常都返回 DownloadResult，绝不抛出——保证不阻断构建
    【变易】timeout 通过环境变量传递，支持不同库的超时机制
    【简易】单一职责：下载一个资源，返回结果

    Args:
        resource: 资源标识符（如模型名）
        download_fn: 下载函数，接收 resource 参数
        timeout: 超时秒数（通过环境变量传递给下载库）
        timeout_env_var: 控制超时的环境变量名
        on_success: 成功回调（可选）
        on_failure: 失败回调（可选）

    Returns:
        DownloadResult: 下载结果（始终返回，不抛异常）
    """
    start = time.time()
    print(f"  [下载] {resource} ...", end=" ", flush=True)

    try:
        # 设置超时环境变量（HF_HUB_DOWNLOAD_TIMEOUT / 其他库自定义）
        if timeout_env_var:
            os.environ[timeout_env_var] = str(timeout)

        download_fn(resource)

        elapsed = time.time() - start
        print(f"OK ({elapsed:.1f}s)")

        if on_success:
            on_success(resource)

        return DownloadResult(
            resource=resource,
            success=True,
            elapsed=elapsed,
        )

    except Exception as e:
        elapsed = time.time() - start
        print(f"FAILED ({elapsed:.1f}s): {e}")

        if on_failure:
            on_failure(resource, e)

        return DownloadResult(
            resource=resource,
            success=False,
            elapsed=elapsed,
            error=str(e),
        )


def safe_download_resources(
    resources: List[str],
    download_fn: Callable[[str], Any],
    timeout: int = 300,
    timeout_env_var: str = "HF_HUB_DOWNLOAD_TIMEOUT",
    exit_zero_on_partial_failure: bool = True,
    on_success: Optional[Callable[[str], None]] = None,
    on_failure: Optional[Callable[[str, Exception], None]] = None,
) -> BatchResult:
    """安全批量下载资源（Layer 1 完整实现）

    【不易】即使全部失败也不抛异常，可选 sys.exit(0) 不阻断 Docker 构建
    【变易】支持任意资源列表和下载函数
    【简易】遍历 + 收集结果，最简实现

    Args:
        resources: 资源列表
        download_fn: 下载函数
        timeout: 每个资源的超时秒数
        timeout_env_var: 超时环境变量名
        exit_zero_on_partial_failure: 部分失败时是否 sys.exit(0)（Docker 构建场景）
        on_success/on_failure: 单个资源回调

    Returns:
        BatchResult: 批量下载结果
    """
    batch_start = time.time()
    results: List[DownloadResult] = []

    for resource in resources:
        result = safe_download_single(
            resource=resource,
            download_fn=download_fn,
            timeout=timeout,
            timeout_env_var=timeout_env_var,
            on_success=on_success,
            on_failure=on_failure,
        )
        results.append(result)

    batch_result = BatchResult(
        results=results,
        total=len(results),
        succeeded=sum(1 for r in results if r.success),
        failed=sum(1 for r in results if not r.success),
        elapsed=time.time() - batch_start,
    )

    print()
    print(batch_result.summary())

    # Layer 1 核心不变量：部分失败不阻断构建
    if exit_zero_on_partial_failure and batch_result.failed > 0:
        print("[INFO] 部分资源下载失败，但不阻断构建（exit 0）")
        # 注意：调用方应自行决定是否 sys.exit(0)
        # 此处不直接 exit，让调用方有更多控制权

    return batch_result


# =====================================================================
# 装饰器方式——保护已有下载函数
# =====================================================================

def fault_tolerant_download(
    timeout: int = 300,
    timeout_env_var: str = "HF_HUB_DOWNLOAD_TIMEOUT",
):
    """装饰器：为下载函数添加容错保护

    用法:
        @fault_tolerant_download(timeout=300)
        def download_model(model_name: str) -> bool:
            from sentence_transformers import SentenceTransformer
            SentenceTransformer(model_name)
            return True

    被装饰的函数异常时返回 False 而非抛出，保证不阻断构建。
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(resource: str, *args, **kwargs) -> bool:
            result = safe_download_single(
                resource=resource,
                download_fn=lambda r: func(r, *args, **kwargs),
                timeout=timeout,
                timeout_env_var=timeout_env_var,
            )
            return result.success
        return wrapper
    return decorator


# =====================================================================
# 上下文管理器方式——批量操作
# =====================================================================

class FaultTolerantBatch:
    """批量容错下载上下文管理器

    用法:
        with FaultTolerantBatch(timeout=300) as batch:
            batch.add("model-a", download_fn=_download_a)
            batch.add("model-b", download_fn=_download_b)
            results = batch.execute()
    """

    def __init__(self, timeout: int = 300, timeout_env_var: str = "HF_HUB_DOWNLOAD_TIMEOUT"):
        self.timeout = timeout
        self.timeout_env_var = timeout_env_var
        self._tasks: List[Tuple[str, Callable, tuple, dict]] = []

    def add(self, resource: str, download_fn: Callable, *args, **kwargs) -> "FaultTolerantBatch":
        """添加下载任务（支持链式调用）"""
        self._tasks.append((resource, download_fn, args, kwargs))
        return self

    def execute(self) -> BatchResult:
        """执行所有下载任务"""
        batch_start = time.time()
        results: List[DownloadResult] = []

        for resource, fn, args, kwargs in self._tasks:
            result = safe_download_single(
                resource=resource,
                download_fn=lambda r: fn(r, *args, **kwargs),
                timeout=self.timeout,
                timeout_env_var=self.timeout_env_var,
            )
            results.append(result)

        return BatchResult(
            results=results,
            total=len(results),
            succeeded=sum(1 for r in results if r.success),
            failed=sum(1 for r in results if not r.success),
            elapsed=time.time() - batch_start,
        )

    def __enter__(self) -> "FaultTolerantBatch":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        # 吞掉所有异常，保证不阻断
        if exc_type is not None:
            logger.warning(f"批量下载上下文退出时捕获异常: {exc_val}")
            return True  # 抑制异常
        return False


# =====================================================================
# Layer 3: 运行时健康检查——检查缓存目录
# =====================================================================

def health_check_resources(
    cache_dir: str,
    expected_resources: List[str],
    resource_name_template: str = "models--{resource}",
) -> Dict[str, bool]:
    """运行时健康检查：验证资源缓存是否完整（Layer 3）

    【不易】仅检查不阻断——返回状态字典，由调用方决定后续行为
    【变易】支持自定义缓存目录结构和资源命名模板
    【简易】检查目录是否存在且非空

    用于 Docker HEALTHCHECK 指令：
        HEALTHCHECK --interval=30s --timeout=10s \\
            CMD python -c "from agent.utils.docker_fault_tolerance import health_check_resources; ..."

    Args:
        cache_dir: 缓存根目录
        expected_resources: 预期资源列表
        resource_name_template: 缓存目录命名模板（{resource} 为占位符）

    Returns:
        Dict[str, bool]: 每个资源是否存在且有效的状态
    """
    cache_path = Path(cache_dir)
    status: Dict[str, bool] = {}

    for resource in expected_resources:
        # 标准化资源名为目录名（如 "BAAI/bge-small" → "models--BAAI--bge-small"）
        normalized = resource.replace("/", "--")
        dir_name = resource_name_template.format(resource=normalized)
        resource_path = cache_path / dir_name

        # 检查目录存在且包含文件
        if resource_path.exists() and any(resource_path.rglob("*")):
            # 进一步检查是否有实际文件（非空目录）
            has_files = any(f.is_file() for f in resource_path.rglob("*"))
            status[resource] = has_files
        else:
            status[resource] = False

    return status


def generate_healthcheck_command(
    cache_dir: str,
    expected_resources: List[str],
    python_path: str = "python",
) -> str:
    """生成 Docker HEALTHCHECK 指令

    Args:
        cache_dir: 容器内缓存目录
        expected_resources: 预期资源列表
        python_path: Python 可执行文件路径

    Returns:
        str: HEALTHCHECK 指令字符串
    """
    resources_str = ",".join(repr(r) for r in expected_resources)
    return (
        f"HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 "
        f"CMD {python_path} -c \""
        f"from agent.utils.docker_fault_tolerance import health_check_resources; "
        f"status = health_check_resources('{cache_dir}', [{resources_str}]); "
        f"import sys; sys.exit(0 if all(status.values()) else 1)"
        f"\" || echo '[WARN] 模型缓存不完整'"
    )


# =====================================================================
# Layer 2 辅助：生成 Dockerfile 容错指令
# =====================================================================

def generate_dockerfile_run_line(
    command: str,
    warn_message: str = "资源下载失败",
) -> str:
    """生成 Dockerfile RUN 指令（带 Layer 2 兜底）

    【不易】`|| echo` 确保即使命令失败，RUN 指令也返回 0
    【简易】单一职责：生成容错 RUN 行

    Args:
        command: 实际执行的命令
        warn_message: 失败时的警告消息

    Returns:
        str: 带 `|| echo` 兜底的 RUN 指令
    """
    return f"RUN {command} || echo \"[WARN] {warn_message}\""


# =====================================================================
# 完整示例：HuggingFace 模型预下载
# =====================================================================

def download_hf_models_safely(
    models: List[str],
    cache_dir: str = "/app/.hf_cache",
    timeout: int = 300,
) -> BatchResult:
    """HuggingFace 模型安全预下载（完整三层防护示例）

    这是 predownload_models.py 的可复用版本，其他服务可直接调用。

    Args:
        models: HuggingFace 模型名列表
        cache_dir: 缓存目录
        timeout: 每个模型的下载超时秒数

    Returns:
        BatchResult: 下载结果
    """
    os.environ["HF_HOME"] = cache_dir
    os.environ["TRANSFORMERS_CACHE"] = cache_dir
    os.environ["SENTENCE_TRANSFORMERS_HOME"] = cache_dir

    def _download_model(model_name: str):
        """单个模型下载函数"""
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(model_name)
        # 验证模型可用
        model.encode(["test"])

    print("=" * 60)
    print(f"预下载 HuggingFace 模型 (timeout={timeout}s)")
    print(f"缓存目录: {cache_dir}")
    print(f"模型列表: {models}")
    print("=" * 60)
    print()

    return safe_download_resources(
        resources=models,
        download_fn=_download_model,
        timeout=timeout,
        timeout_env_var="HF_HUB_DOWNLOAD_TIMEOUT",
    )
