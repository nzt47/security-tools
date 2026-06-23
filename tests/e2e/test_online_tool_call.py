"""在线工具调用 E2E 测试——测试 REST API 端点"""
import pytest


class TestFilesystemE2E:
    """文件系统工具 REST API 测试"""

    def test_read_file(self, client, server):
        """测试读文件——通过 /api/filesystem/read 端点"""
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("Hello E2E Test")
            tmp_path = f.name

        resp = client.post(f"{server}/api/filesystem/read",
                          json={"path": tmp_path})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        assert "Hello E2E" in str(data)

    def test_list_directory(self, client, server):
        """测试列出目录——通过 /api/filesystem/list 端点"""
        resp = client.get(f"{server}/api/filesystem/list?path=.")
        assert resp.status_code == 200
        data = resp.json()
        assert "ok" in data
        assert "files" in data or "items" in data or "entries" in data

    def test_api_health(self, client, server):
        """测试健康检查 API——返回传感器数组"""
        resp = client.get(f"{server}/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        first = data[0]
        assert "sensor_name" in first
