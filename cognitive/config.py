# cognitive/config.py
import os
import copy
import logging

logger = logging.getLogger(__name__)

# 内置默认翻译规则
DEFAULT_RULES = {
    "cpu_temperature": {
        "unit": "°C",
        "description": "CPU 温度（我的大脑温度）",
        "thresholds": [
            {"min": 80, "max": float("inf"), "severity": "critical",
             "message": "我感觉发烧了，浑身发烫"},
            {"min": 70, "max": 80, "severity": "warning",
             "message": "有点热，需要透透气"},
            {"min": float("-inf"), "max": 70, "severity": "normal",
             "message": "体温正常，感觉舒服"},
        ]
    },
    "battery_percentage": {
        "unit": "%",
        "description": "电池电量",
        "thresholds": [
            {"max": 10, "severity": "critical",
             "message": "我太饿了，急需补充能量"},
            {"min": 10, "max": 20, "severity": "warning",
             "message": "我开始饿了，记得给我充电"},
            {"min": 20, "severity": "normal",
             "message": "能量充足，随时待命"},
        ]
    },
    "memory_usage": {
        "unit": "%",
        "description": "内存使用率",
        "thresholds": [
            {"min": 90, "severity": "critical",
             "message": "我的脑子快装不下了，需要整理一下"},
            {"min": 70, "max": 90, "severity": "warning",
             "message": "有点拥挤，但还能工作"},
            {"max": 70, "severity": "normal",
             "message": "头脑清晰，思维敏捷"},
        ]
    },
    "network_latency": {
        "unit": "ms",
        "description": "网络延迟",
        "thresholds": [
            {"min": 500, "severity": "critical",
             "message": "我听不太清你说话，信号不太好"},
            {"min": 200, "max": 500, "severity": "warning",
             "message": "网络有点延迟"},
            {"max": 200, "severity": "normal",
             "message": "网络通畅，沟通无阻"},
        ]
    },
    "disk_space_usage": {
        "unit": "%",
        "description": "磁盘空间使用率",
        "thresholds": [
            {"min": 90, "severity": "critical",
             "message": "存储空间快用完了"},
            {"min": 75, "max": 90, "severity": "warning",
             "message": "存储空间不多了，需要清理一下"},
            {"max": 75, "severity": "normal",
             "message": "存储空间充足"},
        ]
    },
}


class PromptConfig:
    """阈值和规则配置管理。

    支持三层配置加载：内置默认 → YAML 文件覆盖 → 编程覆盖。
    """

    def __init__(self, config_path: str = None):
        self._rules = {}
        self._load_defaults()
        if config_path and os.path.exists(config_path):
            self.load_from_file(config_path)
        logger.info("PromptConfig 初始化完成，共 %d 条规则", len(self._rules))

    def _load_defaults(self):
        self._rules = {}
        for name, rule in DEFAULT_RULES.items():
            self._rules[name] = copy.deepcopy(rule)

    def get_rule(self, sensor_name: str) -> dict:
        return copy.deepcopy(self._rules.get(sensor_name, {}))

    def register_rule(self, sensor_name: str, rule: dict):
        if not isinstance(rule, dict) or "thresholds" not in rule:
            raise ValueError(f"规则必须包含 'thresholds' 字段: {rule}")
        if not isinstance(rule["thresholds"], list):
            raise ValueError(f"'thresholds' 必须是列表: {rule}")
        self._rules[sensor_name] = copy.deepcopy(rule)
        logger.info("注册规则: %s", sensor_name)

    def get_all_rules(self) -> dict:
        return {name: copy.deepcopy(rule) for name, rule in self._rules.items()}

    def load_from_file(self, path: str):
        """从 YAML 文件加载配置覆盖"""
        try:
            import yaml
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data and "translations" in data:
                for name, rule in data["translations"].items():
                    self.register_rule(name, rule)
                logger.info("从 %s 加载了 %d 条规则覆盖", path, len(data["translations"]))
        except ImportError:
            logger.warning("yaml 未安装，跳过配置文件加载")
        except Exception as e:
            logger.error("加载配置文件失败: %s", e)
