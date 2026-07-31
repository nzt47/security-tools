"""model_cache_utils.py 的单元测试 — 验证跨平台路径解析 + 环境变量优先级.

覆盖所有优先级分支, 确保路径解析逻辑不随时间退化.
所有模型下载脚本 (BGE/Jina/其他) 复用此工具, 测试是其共用护城河.
"""
import sys
from pathlib import Path

import pytest

# 导入 model_cache_utils (在 agent/scripts/ 下, 非 security-tools/scripts/)
# parents[0]=unit, [1]=tests, [2]=security-tools, [3]=agent
_AGENT_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _AGENT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from model_cache_utils import (
    _model_id_to_subdir,
    _get_hf_cache_base,
    get_hf_cache_base,
    get_hf_model_cache_dir,
    get_modelscope_cache_dir,
)

# 测试用的环境变量名 (与 BGE 脚本一致)
_TEST_ENV_OVERRIDE = "BGE_V2_M3_LOCAL_DIR"

# 相关环境变量清单 (每个测试前清除, 避免污染)
_HF_ENV_VARS = [
    _TEST_ENV_OVERRIDE,
    "HF_HOME",
    "HUGGINGFACE_HUB_CACHE",
    "TRANSFORMERS_CACHE",
    "MODELSCOPE_CACHE",
]


class TestModelIdToSubdir:
    """测试 model_id 到缓存子目录名的转换 (HuggingFace Hub 约定)."""

    def test_bge_reranker(self):
        """BGE reranker: 含组织名."""
        assert _model_id_to_subdir("BAAI/bge-reranker-v2-m3") == "models--BAAI--bge-reranker-v2-m3"

    def test_jina_reranker(self):
        """Jina reranker: 另一个模型, 验证通用性."""
        result = _model_id_to_subdir("jinaai/jina-reranker-v2-base-multilingual")
        assert result == "models--jinaai--jina-reranker-v2-base-multilingual"

    def test_no_org(self):
        """无组织名的模型: 只有模型名."""
        assert _model_id_to_subdir("bert-base-uncased") == "models--bert-base-uncased"


class TestGetHfModelCacheDir:
    """测试 HuggingFace 模型缓存目录解析 — 4 级优先级."""

    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch):
        """每个测试前清除所有 HF 相关环境变量, 确保隔离."""
        for var in _HF_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

    def test_default_path(self):
        """无环境变量时用平台默认路径, 含模型子目录."""
        result = get_hf_model_cache_dir("BAAI/bge-reranker-v2-m3")
        assert "models--BAAI--bge-reranker-v2-m3" in result
        # 平台默认路径应包含 huggingface
        assert "huggingface" in result.lower()

    def test_env_override(self, monkeypatch):
        """优先级 1: 脚本专用环境变量 (完整路径覆盖)."""
        monkeypatch.setenv(_TEST_ENV_OVERRIDE, "/custom/path")
        result = get_hf_model_cache_dir("BAAI/bge-reranker-v2-m3", env_override=_TEST_ENV_OVERRIDE)
        assert result == str(Path("/custom/path").expanduser())

    def test_hf_home(self, monkeypatch):
        """优先级 2: HF_HOME (官方约定, 路径含 hub/)."""
        monkeypatch.setenv("HF_HOME", "/hf/home")
        result = get_hf_model_cache_dir("BAAI/bge-reranker-v2-m3")
        assert "hub" in result
        assert "models--BAAI--bge-reranker-v2-m3" in result

    def test_hub_cache(self, monkeypatch):
        """优先级 3: HUGGINGFACE_HUB_CACHE."""
        monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", "/hf/cache")
        result = get_hf_model_cache_dir("BAAI/bge-reranker-v2-m3")
        assert "models--BAAI--bge-reranker-v2-m3" in result

    def test_transformers_cache_fallback(self, monkeypatch):
        """优先级 3 后备: HUGGINGFACE_HUB_CACHE 未设时用 TRANSFORMERS_CACHE."""
        monkeypatch.setenv("TRANSFORMERS_CACHE", "/tf/cache")
        result = get_hf_model_cache_dir("BAAI/bge-reranker-v2-m3")
        assert "models--BAAI--bge-reranker-v2-m3" in result

    def test_priority_env_over_hf_home(self, monkeypatch):
        """优先级验证: env_override > HF_HOME."""
        monkeypatch.setenv(_TEST_ENV_OVERRIDE, "/override")
        monkeypatch.setenv("HF_HOME", "/hf/home")
        result = get_hf_model_cache_dir("BAAI/bge-reranker-v2-m3", env_override=_TEST_ENV_OVERRIDE)
        assert result == str(Path("/override").expanduser())

    def test_priority_hf_home_over_hub_cache(self, monkeypatch):
        """优先级验证: HF_HOME > HUGGINGFACE_HUB_CACHE."""
        monkeypatch.setenv("HF_HOME", "/hf/home")
        monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", "/hf/cache")
        result = get_hf_model_cache_dir("BAAI/bge-reranker-v2-m3")
        # HF_HOME 路径含 /hub/, HUB_CACHE 路径不含
        assert "hub" in result

    def test_no_env_override_passes_none(self):
        """env_override=None 时不报错, 回退到 HF_HOME/平台默认."""
        result = get_hf_model_cache_dir("BAAI/bge-reranker-v2-m3", env_override=None)
        assert "models--BAAI--bge-reranker-v2-m3" in result

    def test_different_model_ids_different_paths(self):
        """不同 model_id 生成不同路径 (验证通用性)."""
        bge = get_hf_model_cache_dir("BAAI/bge-reranker-v2-m3")
        jina = get_hf_model_cache_dir("jinaai/jina-reranker-v2-base-multilingual")
        assert bge != jina
        assert "bge-reranker" in bge
        assert "jina-reranker" in jina


class TestGetHfCacheBase:
    """测试 get_hf_cache_base() — 缓存基础路径 (不含模型子目录).

    供 huggingface_hub.snapshot_download(cache_dir=...) 使用,
    与 get_hf_model_cache_dir() 优先级一致但返回基础路径.
    """

    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch):
        """每个测试前清除所有 HF 相关环境变量."""
        for var in _HF_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

    def test_default_path(self):
        """无环境变量时用平台默认路径 (含 hub/)."""
        result = get_hf_cache_base()
        assert isinstance(result, Path)
        assert "hub" in str(result).lower()

    def test_env_override(self, monkeypatch):
        """优先级 1: 脚本专用环境变量 (完整路径覆盖)."""
        monkeypatch.setenv(_TEST_ENV_OVERRIDE, "/custom/base")
        result = get_hf_cache_base(env_override=_TEST_ENV_OVERRIDE)
        assert result == Path("/custom/base").expanduser()

    def test_hf_home(self, monkeypatch):
        """优先级 2: HF_HOME (返回 HF_HOME/hub)."""
        monkeypatch.setenv("HF_HOME", "/hf/home")
        result = get_hf_cache_base()
        assert result == Path("/hf/home") / "hub"

    def test_hub_cache(self, monkeypatch):
        """优先级 3: HUGGINGFACE_HUB_CACHE (直接返回, 不加 hub/)."""
        monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", "/hf/cache")
        result = get_hf_cache_base()
        assert result == Path("/hf/cache")

    def test_transformers_cache_fallback(self, monkeypatch):
        """优先级 3 后备: TRANSFORMERS_CACHE."""
        monkeypatch.setenv("TRANSFORMERS_CACHE", "/tf/cache")
        result = get_hf_cache_base()
        assert result == Path("/tf/cache")

    def test_priority_env_over_hf_home(self, monkeypatch):
        """优先级验证: env_override > HF_HOME."""
        monkeypatch.setenv(_TEST_ENV_OVERRIDE, "/override")
        monkeypatch.setenv("HF_HOME", "/hf/home")
        result = get_hf_cache_base(env_override=_TEST_ENV_OVERRIDE)
        assert result == Path("/override")

    def test_priority_hf_home_over_hub_cache(self, monkeypatch):
        """优先级验证: HF_HOME > HUGGINGFACE_HUB_CACHE."""
        monkeypatch.setenv("HF_HOME", "/hf/home")
        monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", "/hf/cache")
        result = get_hf_cache_base()
        # HF_HOME 路径含 /hub, HUB_CACHE 不含
        assert "hub" in str(result)

    def test_returns_path_object(self):
        """返回 Path 对象 (非字符串, 与 get_hf_model_cache_dir 不同)."""
        result = get_hf_cache_base()
        assert isinstance(result, Path)

    def test_no_env_override(self):
        """env_override=None 时不报错."""
        result = get_hf_cache_base(env_override=None)
        assert isinstance(result, Path)


class TestGetModelscopeCacheDir:
    """测试 modelscope 缓存目录解析."""

    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch):
        """每个测试前清除 MODELSCOPE_CACHE."""
        monkeypatch.delenv("MODELSCOPE_CACHE", raising=False)

    def test_default_path(self):
        """无环境变量时用平台默认路径."""
        result = get_modelscope_cache_dir()
        assert "modelscope" in result.lower()

    def test_env_override(self, monkeypatch):
        """MODELSCOPE_CACHE 覆盖默认路径."""
        monkeypatch.setenv("MODELSCOPE_CACHE", "/ms/cache")
        result = get_modelscope_cache_dir()
        assert result == str(Path("/ms/cache").expanduser())

    def test_env_override_priority(self, monkeypatch):
        """环境变量优先于平台默认."""
        monkeypatch.setenv("MODELSCOPE_CACHE", "/custom/ms")
        result = get_modelscope_cache_dir()
        assert "custom" in result


class TestCrossPlatform:
    """跨平台路径分隔符验证 (Windows \\ vs Linux /)."""

    def test_hf_path_uses_correct_separator(self):
        """HF 缓存路径用当前平台的分隔符."""
        result = get_hf_model_cache_dir("BAAI/bge-reranker-v2-m3")
        # Windows 路径含 \, Linux 路径含 /
        if sys.platform == "win32":
            assert "\\" in result
        else:
            assert "/" in result

    def test_modelscope_path_uses_correct_separator(self):
        """modelscope 缓存路径用当前平台的分隔符."""
        result = get_modelscope_cache_dir()
        if sys.platform == "win32":
            assert "\\" in result
        else:
            assert "/" in result

    def test_path_is_absolute(self):
        """所有路径返回绝对路径."""
        hf_path = get_hf_model_cache_dir("BAAI/bge-reranker-v2-m3")
        ms_path = get_modelscope_cache_dir()
        assert Path(hf_path).is_absolute()
        assert Path(ms_path).is_absolute()
