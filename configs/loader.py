"""配置加载器——统一配置管理入口

所有配置通过此模块加载，支持：
- YAML 文件配置（优先级最高）
- JSON 文件配置
- 环境变量覆盖
- 默认值
"""
import os
import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 配置根目录
CONFIG_DIR = Path(__file__).parent


class ConfigLoader:
    """配置加载器"""

    def __init__(self):
        self._cache: dict[str, dict] = {}

    def load(self, name: str, default: dict = None) -> dict:
        """加载配置文件

        按优先级加载：
        1. configs/{name}.yaml
        2. configs/{name}.json
        3. 环境变量中的 JSON
        4. 默认值
        """
        if name in self._cache:
            return self._cache[name]

        config = (default or {}).copy()

        # 尝试 yaml
        yaml_path = CONFIG_DIR / f"{name}.yaml"
        if yaml_path.exists():
            try:
                import yaml
                with open(yaml_path, "r", encoding="utf-8") as f:
                    yaml_config = yaml.safe_load(f)
                    if yaml_config:
                        self._deep_merge(config, yaml_config)
                logger.info(f"[Config] 已加载: {yaml_path}")
            except ImportError:
                logger.warning("[Config] PyYAML 未安装，跳过 yaml")
            except Exception as e:
                logger.error(f"[Config] 加载失败: {yaml_path}: {e}")

        # 尝试 json
        json_path = CONFIG_DIR / f"{name}.json"
        if json_path.exists():
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    json_config = json.load(f)
                    if json_config:
                        self._deep_merge(config, json_config)
                logger.info(f"[Config] 已加载: {json_path}")
            except Exception as e:
                logger.error(f"[Config] 加载失败: {json_path}: {e}")

        # 环境变量覆盖（JSON 格式）
        env_key = f"CONFIG_{name.upper()}"
        env_value = os.environ.get(env_key)
        if env_value:
            try:
                env_config = json.loads(env_value)
                self._deep_merge(config, env_config)
                logger.info(f"[Config] 环境变量覆盖: {env_key}")
            except json.JSONDecodeError:
                logger.warning(f"[Config] 环境变量格式错误: {env_key}")

        self._cache[name] = config
        return config

    def get(self, name: str, key: str, default: Any = None) -> Any:
        """获取单个配置项（支持点号路径）"""
        config = self.load(name)
        parts = key.split(".")
        value = config
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return default
        return value if value is not None else default

    def reload(self, name: str):
        """重新加载配置（热更新）"""
        if name in self._cache:
            del self._cache[name]
        return self.load(name)

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        """深度合并字典（override 覆盖 base）"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                ConfigLoader._deep_merge(base[key], value)
            else:
                base[key] = value
        return base


# 全局实例
config = ConfigLoader()
