#!/usr/bin/env python3
"""
扩展系统 API 单元测试

测试场景：
1. 扩展列表获取
2. 扩展安装/卸载
3. 扩展状态切换
4. 扩展配置更新
5. 扩展市场搜索
6. 通道消息发送
"""

import unittest
import json
import sys
sys.path.insert(0, '.')

from unittest.mock import Mock, MagicMock, patch
from flask import Flask
from flask.testing import FlaskClient

from agent.server_routes.extensions import register_routes
from agent.extensions.manager import ExtensionManager
from agent.extensions.market import ExtensionMarket


# Mock 数据
MOCK_EXTENSIONS = {
    "skills": [
        {"id": "self_reflection", "name": "自我反思", "description": "帮助智能体进行自我反思", "enabled": True},
        {"id": "web_search", "name": "网页搜索", "description": "搜索互联网信息", "enabled": True},
    ],
    "mcp_services": [
        {"id": "filesystem", "name": "文件系统", "description": "本地文件操作", "enabled": True},
        {"id": "github", "name": "GitHub", "description": "GitHub 操作", "enabled": False},
    ],
    "channels": [
        {"id": "slack_notify", "name": "Slack 通知", "description": "发送消息到 Slack", "enabled": True},
    ],
    "plugins": [],
}

MOCK_MARKET_RESULTS = {
    "builtin": [
        {"id": "calculator", "name": "科学计算器", "description": "数学计算工具", "source": "builtin"},
    ],
    "community": [
        {"id": "weather", "name": "天气预报", "description": "获取天气信息", "source": "community"},
    ],
    "github": [
        {"id": "notion", "name": "Notion 集成", "description": "操作 Notion 数据库", "source": "github:user/notion"},
    ],
}


class MockState:
    """模拟 ServerState"""
    def __init__(self):
        self.extension_mgr = Mock(spec=ExtensionManager)
        self.extension_market = Mock(spec=ExtensionMarket)


class TestExtensionsAPI(unittest.TestCase):
    """扩展系统 API 测试"""

    def setUp(self):
        """设置测试环境"""
        # 创建 Flask 应用
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        
        # 创建 mock state
        self.state = MockState()
        
        # 注册路由
        register_routes(self.app, self.state)
        
        # 创建测试客户端
        self.client = self.app.test_client()
        
        # 设置 mock 返回值
        self._setup_mocks()

    def _setup_mocks(self):
        """设置 mock 对象的返回值"""
        # ExtensionManager mock
        self.state.extension_mgr.list_all.return_value = []
        self.state.extension_mgr.get_installed_by_type.return_value = MOCK_EXTENSIONS
        
        # 安装扩展
        self.state.extension_mgr.install.return_value = {"ok": True, "message": "安装成功"}
        
        # 卸载扩展
        self.state.extension_mgr.uninstall.return_value = {"ok": True, "message": "卸载成功"}
        
        # 切换状态
        self.state.extension_mgr.toggle.return_value = {"ok": True, "message": "已切换", "enabled": True}
        
        # 配置扩展
        self.state.extension_mgr.configure.return_value = {"ok": True, "message": "配置已更新"}
        
        # 发现扩展
        self.state.extension_mgr.discover_all.return_value = {
            "available_skills": [],
            "available_mcp": [],
            "available_channels": [],
            "local_plugins": [],
        }
        
        # 发送通道消息
        self.state.extension_mgr.send_channel_message.return_value = {"ok": True, "message": "消息已发送"}
        
        # ExtensionMarket mock
        self.state.extension_market.search_all.return_value = MOCK_MARKET_RESULTS
        self.state.extension_market.get_recommendations.return_value = MOCK_MARKET_RESULTS["builtin"]
        self.state.extension_market.fetch_community_index.return_value = {"skills": [], "mcp": [], "channels": [], "plugins": []}

    def test_list_extensions(self):
        """测试获取扩展列表"""
        print("[测试] test_list_extensions")
        response = self.client.get("/api/extensions/list")
        data = json.loads(response.data.decode("utf-8"))
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["ok"])
        self.state.extension_mgr.list_all.assert_called_once()
        print("[测试] test_list_extensions - 通过")

    def test_list_extensions_with_type(self):
        """测试按类型筛选扩展列表"""
        print("[测试] test_list_extensions_with_type")
        response = self.client.get("/api/extensions/list?type=skill")
        data = json.loads(response.data.decode("utf-8"))
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["ok"])
        self.state.extension_mgr.list_all.assert_called_once_with("skill")
        print("[测试] test_list_extensions_with_type - 通过")

    def test_installed_extensions(self):
        """测试获取已安装扩展"""
        print("[测试] test_installed_extensions")
        response = self.client.get("/api/extensions/installed")
        data = json.loads(response.data.decode("utf-8"))
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["ok"])
        self.assertIn("skills", data)
        self.assertIn("mcp_services", data)
        self.assertIn("channels", data)
        self.state.extension_mgr.get_installed_by_type.assert_called_once()
        print("[测试] test_installed_extensions - 通过")

    def test_install_extension_success(self):
        """测试安装扩展成功"""
        print("[测试] test_install_extension_success")
        response = self.client.post(
            "/api/extensions/install",
            data=json.dumps({"type": "skill", "source": "self_reflection"}),
            content_type="application/json"
        )
        data = json.loads(response.data.decode("utf-8"))
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["ok"])
        self.state.extension_mgr.install.assert_called_once_with("skill", "self_reflection", **{})
        print("[测试] test_install_extension_success - 通过")

    def test_install_extension_missing_params(self):
        """测试安装扩展缺少参数"""
        print("[测试] test_install_extension_missing_params")
        # 缺少 type
        response = self.client.post(
            "/api/extensions/install",
            data=json.dumps({"source": "self_reflection"}),
            content_type="application/json"
        )
        data = json.loads(response.data.decode("utf-8"))
        
        self.assertEqual(response.status_code, 400)
        self.assertFalse(data["ok"])
        self.assertIn("缺少", data["error"])
        print("[测试] test_install_extension_missing_params - 通过")

    def test_uninstall_extension_success(self):
        """测试卸载扩展成功"""
        print("[测试] test_uninstall_extension_success")
        response = self.client.post(
            "/api/extensions/uninstall",
            data=json.dumps({"type": "skill", "id": "self_reflection"}),
            content_type="application/json"
        )
        data = json.loads(response.data.decode("utf-8"))
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["ok"])
        self.state.extension_mgr.uninstall.assert_called_once_with("skill", "self_reflection")
        print("[测试] test_uninstall_extension_success - 通过")

    def test_toggle_extension(self):
        """测试切换扩展状态"""
        print("[测试] test_toggle_extension")
        response = self.client.post(
            "/api/extensions/toggle",
            data=json.dumps({"type": "mcp", "id": "github", "enabled": True}),
            content_type="application/json"
        )
        data = json.loads(response.data.decode("utf-8"))
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["ok"])
        self.state.extension_mgr.toggle.assert_called_once_with("mcp", "github", True)
        print("[测试] test_toggle_extension - 通过")

    def test_configure_extension(self):
        """测试配置扩展"""
        print("[测试] test_configure_extension")
        response = self.client.post(
            "/api/extensions/configure",
            data=json.dumps({
                "type": "channel",
                "id": "slack_notify",
                "config": {"webhook_url": "https://hooks.slack.com/services/xxx"}
            }),
            content_type="application/json"
        )
        data = json.loads(response.data.decode("utf-8"))
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["ok"])
        self.state.extension_mgr.configure.assert_called_once()
        print("[测试] test_configure_extension - 通过")

    def test_discover_extensions(self):
        """测试发现可用扩展"""
        print("[测试] test_discover_extensions")
        response = self.client.get("/api/extensions/discover")
        data = json.loads(response.data.decode("utf-8"))
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["ok"])
        self.state.extension_mgr.discover_all.assert_called_once()
        print("[测试] test_discover_extensions - 通过")

    def test_market_search(self):
        """测试扩展市场搜索"""
        print("[测试] test_market_search")
        response = self.client.get("/api/extensions/market/search?q=calculator")
        data = json.loads(response.data.decode("utf-8"))
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["ok"])
        self.assertIn("builtin", data)
        self.assertIn("community", data)
        self.assertIn("github", data)
        self.state.extension_market.search_all.assert_called_once()
        print("[测试] test_market_search - 通过")

    def test_market_recommend(self):
        """测试扩展推荐"""
        print("[测试] test_market_recommend")
        response = self.client.get("/api/extensions/market/recommend")
        data = json.loads(response.data.decode("utf-8"))
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["ok"])
        self.assertIn("recommendations", data)
        self.state.extension_market.get_recommendations.assert_called_once()
        print("[测试] test_market_recommend - 通过")

    def test_market_refresh(self):
        """测试刷新市场索引"""
        print("[测试] test_market_refresh")
        response = self.client.post("/api/extensions/market/refresh")
        data = json.loads(response.data.decode("utf-8"))
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["ok"])
        self.state.extension_market.fetch_community_index.assert_called_once()
        print("[测试] test_market_refresh - 通过")

    def test_channel_send_message(self):
        """测试发送通道消息"""
        print("[测试] test_channel_send_message")
        response = self.client.post(
            "/api/extensions/channels/send",
            data=json.dumps({
                "channel_id": "slack_notify",
                "message": "测试消息",
                "params": {"channel": "#general"}
            }),
            content_type="application/json"
        )
        data = json.loads(response.data.decode("utf-8"))
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["ok"])
        self.state.extension_mgr.send_channel_message.assert_called_once_with(
            "slack_notify", "测试消息", **{"channel": "#general"}
        )
        print("[测试] test_channel_send_message - 通过")

    def test_channel_send_missing_params(self):
        """测试发送通道消息缺少参数"""
        print("[测试] test_channel_send_missing_params")
        response = self.client.post(
            "/api/extensions/channels/send",
            data=json.dumps({"channel_id": "slack_notify"}),
            content_type="application/json"
        )
        data = json.loads(response.data.decode("utf-8"))
        
        self.assertEqual(response.status_code, 400)
        self.assertFalse(data["ok"])
        self.assertIn("缺少", data["error"])
        print("[测试] test_channel_send_missing_params - 通过")

    def test_install_extension_with_params(self):
        """测试安装扩展带参数"""
        print("[测试] test_install_extension_with_params")
        response = self.client.post(
            "/api/extensions/install",
            data=json.dumps({
                "type": "skill",
                "source": "custom_skill",
                "params": {"name": "自定义技能", "description": "我的技能"}
            }),
            content_type="application/json"
        )
        data = json.loads(response.data.decode("utf-8"))
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["ok"])
        self.state.extension_mgr.install.assert_called_once_with(
            "skill", "custom_skill", **{"name": "自定义技能", "description": "我的技能"}
        )
        print("[测试] test_install_extension_with_params - 通过")

    def test_api_error_handling(self):
        """测试 API 错误处理"""
        print("[测试] test_api_error_handling")
        # 设置 mock 抛出异常
        self.state.extension_mgr.list_all.side_effect = Exception("数据库连接失败")
        
        response = self.client.get("/api/extensions/list")
        data = json.loads(response.data.decode("utf-8"))
        
        self.assertEqual(response.status_code, 500)
        self.assertFalse(data["ok"])
        self.assertIn("数据库连接失败", data["error"])
        print("[测试] test_api_error_handling - 通过")


if __name__ == "__main__":
    print("=" * 60)
    print("扩展系统 API 单元测试")
    print("=" * 60)
    
    unittest.main(verbosity=2)