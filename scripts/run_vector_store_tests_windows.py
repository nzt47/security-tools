"""Windows 环境下安全运行 vector_store 测试的脚本

问题：
    test_memory_vector_store.py 导入 memory.vector_store.VectorStore
    VectorStore.__init__ → _check_chroma_available() → import sentence_transformers → import torch
    在 Windows CPU 环境下，torch C 扩展会触发 0xC0000005 (ACCESS_VIOLATION) 原生崩溃，
    try/except 无法捕获（原生崩溃不是 Python 异常）。

方案：
    在 pytest 收集前，在 sys.modules 中注入 mock 模块，让 torch/sentence_transformers
    的 import 失败（返回 None 或抛 ImportError），迫使 VectorStore 使用 JSON fallback 路径。
    这样测试只验证 JSON fallback 逻辑，不触发模型加载，避免崩溃。

    sqlite-vec 后端的测试在 test_vector_store_sqlite_vec.py 中独立覆盖，
    在 Linux Docker 环境下运行（docker-compose.linux-test.yml）。

用法：
    python scripts/run_vector_store_tests_windows.py
    python scripts/run_vector_store_tests_windows.py --verbose
    python scripts/run_vector_store_tests_windows.py --test tests/unit/test_memory_vector_store.py

环境变量：
    VECTOR_STORE_TEST_BACKEND: 强制指定后端（json/sqlite_vec/auto）
        auto（默认）= Windows 用 json，Linux 用 auto
"""
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

# 添加项目根目录到 path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _is_windows() -> bool:
    """检测是否为 Windows 环境"""
    return sys.platform == 'win32'


def _should_mock_torch() -> bool:
    """判断是否需要 mock torch/sentence_transformers

    条件：
    1. Windows 环境（Linux 上 torch 通常稳定）
    2. 未设置 VECTOR_STORE_TEST_BACKEND=sqlite_vec（用户显式要求测试 sqlite-vec 后端时不 mock）
    3. 未设置 SKIP_TORCH_MOCK=true（用户显式跳过 mock）
    """
    if os.environ.get('SKIP_TORCH_MOCK', '').lower() == 'true':
        return False
    if os.environ.get('VECTOR_STORE_TEST_BACKEND', 'auto') == 'sqlite_vec':
        return False
    return _is_windows()


def _make_mock_module(name: str, raise_on_instantiate: dict = None):
    """创建支持子模块访问的 mock 模块

    [健壮性改进] 解决以下问题：
    1. 子模块访问（torch.nn / torch.cuda）自动返回 mock，不报 AttributeError
    2. 指定类在实例化时抛 ImportError，让 _check_chroma_available 捕获
    3. 任何属性访问都返回 MagicMock，不会触发真实加载

    Args:
        name: 模块名（如 'torch'）
        raise_on_instantiate: {类名: 异常消息}，这些类实例化时抛 ImportError
    """
    mock = types.ModuleType(name)
    mock.__version__ = 'mocked-windows-skip'
    mock.__file__ = f'<mock:{name}>'

    # [修复] 设置 __getattr__ 让任何子模块/属性访问都返回 mock
    mock.__all__ = []  # 声明空 __all__ 避免星号导入

    if raise_on_instantiate:
        for class_name, msg in raise_on_instantiate.items():
            def _make_raising_class(msg):
                class _RaisingClass:
                    def __init__(self, *args, **kwargs):
                        raise ImportError(msg)
                return _RaisingClass
            setattr(mock, class_name, _make_raising_class(msg))

    # [修复] 使用 __getattr__ 处理动态属性/子模块访问
    def __getattr__(attr):
        # 子模块访问（如 torch.nn）返回 MagicMock，不报错
        return MagicMock()
    mock.__getattr__ = __getattr__

    return mock


def _install_torch_mock():
    """在 sys.modules 中注入 mock，阻止重量级 ML 依赖加载

    [健壮性改进] 覆盖更全面的依赖列表 + 子模块访问支持：
    - torch（含子模块 torch.nn / torch.cuda / torch.functional）
    - sentence_transformers（SentenceTransformer 实例化抛 ImportError）
    - transformers（sentence_transformers 的依赖）
    - chromadb（含子模块 chromadb.config）
    - onnxruntime（备选推理后端）
    - faiss / hnswlib（备选向量索引）

    原理：
    - sys.modules 中预先放入 mock 模块
    - 当 vector_store.py 执行 `import sentence_transformers` 时，
      Python 发现模块已在 sys.modules 中，直接返回 mock，不会触发真实加载
    - mock 的 SentenceTransformer 类在实例化时抛 ImportError，
      让 _check_chroma_available() 认为 sentence_transformers 不可用
    - VectorStore 回退到 JSON fallback 路径
    """
    _crash_msg = (
        "mocked on Windows to avoid 0xC0000005 crash. "
        "Use Linux Docker (docker-compose.linux-test.yml) for real model tests."
    )

    # [修复] 创建支持子模块访问的 mock 模块
    mock_torch = _make_mock_module('torch')
    # 显式设置常用属性（确保 is_available 返回 False）
    mock_torch.cuda = MagicMock()
    mock_torch.cuda.is_available = lambda: False
    mock_torch.set_num_threads = lambda n: None
    mock_torch.set_num_interop_threads = lambda n: None

    mock_sentence_transformers = _make_mock_module(
        'sentence_transformers',
        raise_on_instantiate={'SentenceTransformer': _crash_msg}
    )

    # [修复] 新增 transformers mock（sentence_transformers 依赖 transformers）
    mock_transformers = _make_mock_module('transformers')

    mock_chromadb = _make_mock_module('chromadb')
    mock_chromadb.config = _make_mock_module('chromadb.config')
    mock_chromadb.config.Settings = MagicMock()

    # [修复] 新增其他可能的 ML 依赖 mock
    mock_onnxruntime = _make_mock_module('onnxruntime')
    mock_faiss = _make_mock_module('faiss')
    mock_hnswlib = _make_mock_module('hnswlib')

    # 注入到 sys.modules
    mocks = {
        'torch': mock_torch,
        'torch.nn': MagicMock(),  # [修复] 显式 mock 常用子模块
        'torch.cuda': mock_torch.cuda,
        'torch.functional': MagicMock(),
        'sentence_transformers': mock_sentence_transformers,
        'transformers': mock_transformers,
        'chromadb': mock_chromadb,
        'chromadb.config': mock_chromadb.config,
        'onnxruntime': mock_onnxruntime,
        'faiss': mock_faiss,
        'hnswlib': mock_hnswlib,
    }
    for name, mock in mocks.items():
        sys.modules[name] = mock

    print(f"[FIX] 已注入 {len(mocks)} 个 mock 模块（含子模块）")
    print(f"[FIX] 平台: {sys.platform}")
    print(f"[FIX] Mock 列表: {', '.join(sorted(mocks.keys()))}")
    print(f"[FIX] VectorStore 将使用 JSON fallback 路径")
    print(f"[FIX] sqlite-vec 后端测试请在 Linux Docker 中运行:")
    print(f"[FIX]   docker-compose -f docker-compose.linux-test.yml run --rm test-sqlite-vec")
    print()


def run_tests(test_path: str = None, verbose: bool = False):
    """运行 vector_store 测试

    Args:
        test_path: 测试文件路径（默认 tests/unit/test_memory_vector_store.py）
        verbose: 是否显示详细输出
    """
    if _should_mock_torch():
        _install_torch_mock()
    else:
        print(f"[INFO] 平台 {sys.platform}，未注入 mock（torch 将真实加载）")
        print()

    # 构造 pytest 参数
    if test_path is None:
        test_path = "tests/unit/test_memory_vector_store.py"

    pytest_args = [test_path]
    if verbose:
        pytest_args.extend(['-v', '--tb=long'])
    else:
        pytest_args.extend(['--tb=short', '-q'])

    # 添加超时（防止意外卡死）
    pytest_args.extend(['--timeout=30', '--timeout-method=thread'])

    print(f"[RUN] pytest {' '.join(pytest_args)}")
    print("=" * 60)

    import pytest
    return pytest.main(pytest_args)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Windows 安全运行 vector_store 测试（绕过 torch 0xC0000005 崩溃）'
    )
    parser.add_argument(
        '--test', default=None,
        help='测试文件路径（默认 tests/unit/test_memory_vector_store.py）'
    )
    parser.add_argument(
        '--verbose', '-v', action='store_true',
        help='显示详细输出'
    )
    parser.add_argument(
        '--skip-mock', action='store_true',
        help='跳过 torch mock（仅在 Linux 或确认不会崩溃时使用）'
    )
    args = parser.parse_args()

    if args.skip_mock:
        os.environ['SKIP_TORCH_MOCK'] = 'true'

    exit_code = run_tests(test_path=args.test, verbose=args.verbose)
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
