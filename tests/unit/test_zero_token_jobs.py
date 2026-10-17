"""零 token 维护作业：清单约束 / 注册 / 执行器（2026-10-17）。

守卫目标（都可证伪）：
1. 所有作业都落在 09:00-18:00 工作时段，且时刻不撞车；
2. 注册函数给每条作业各注册一次，时刻与清单一致；
3. 开关能整体关掉；
4. 执行器对"未知作业/子进程失败/子进程抛异常"一律返回 ok=False 而不外溢
   —— 调度线程被一条坏作业带崩是不可接受的。
"""

import json

import agent.maintenance.zero_token_jobs as z


class TestCatalog:
    def test_全部落在工作时段(self):
        for job in z.JOBS:
            assert z.WORK_WINDOW_START <= job["hour"] < z.WORK_WINDOW_END, job
            assert 0 <= job["minute"] <= 59, job

    def test_key唯一且非空(self):
        keys = [j["key"] for j in z.JOBS]
        assert len(keys) == len(set(keys))
        assert all(str(k).strip() for k in keys)

    def test_时刻不撞车(self):
        slots = [(j["hour"], j["minute"]) for j in z.JOBS]
        assert len(slots) == len(set(slots)), slots

    def test_作业类型受支持(self):
        for job in z.JOBS:
            assert job["kind"] in {"shell", "http"}, job
            if job["kind"] == "shell":
                assert job["argv"] and str(job["argv"][0])
            if job["kind"] == "http":
                # 端点以分段形式声明（避免源码出现完整字面量，见模块头说明）
                assert job["endpoint"] and all(
                    seg and "/" not in seg for seg in job["endpoint"]
                ), job

    def test_describe可JSON化(self):
        payload = json.loads(json.dumps(z.describe(), ensure_ascii=False))
        assert len(payload) == len(z.JOBS)
        assert all(item["at"].count(":") == 1 for item in payload)


class TestRegister:
    def test_注册条数与时刻一致(self, monkeypatch):
        monkeypatch.delenv("ZERO_TOKEN_JOBS_ENABLED", raising=False)
        calls = []

        class FakeScheduler:
            def add_cron_task(self, **kwargs):
                calls.append(kwargs)

        keys = z.register_zero_token_jobs(FakeScheduler())
        assert keys == [j["key"] for j in z.JOBS]
        assert [(c["hour"], c["minute"]) for c in calls] == [
            (j["hour"], j["minute"]) for j in z.JOBS
        ]
        assert all(c["day_of_week"] is None for c in calls)
        assert all(callable(c["func"]) for c in calls)

    def test_开关可关闭(self, monkeypatch):
        monkeypatch.setenv("ZERO_TOKEN_JOBS_ENABLED", "0")

        class FakeScheduler:
            def add_cron_task(self, **kwargs):
                raise AssertionError("关闭时不应注册")

        assert z.register_zero_token_jobs(FakeScheduler()) == []


class TestRunJob:
    def test_未知作业不抛(self):
        result = z.run_job("no-such-job")
        assert result["ok"] is False
        assert result["job"] == "no-such-job"

    def test_shell成功路径(self, monkeypatch):
        monkeypatch.setattr(z, "_run_shell", lambda argv: (True, "ok"))
        result = z.run_job("route_coverage_check")
        assert result["ok"] is True
        assert result["elapsed_ms"] >= 0

    def test_shell失败被如实回报(self, monkeypatch):
        monkeypatch.setattr(z, "_run_shell", lambda argv: (False, "exit=1 boom"))
        result = z.run_job("capability_manifest_check")
        assert result["ok"] is False
        assert "boom" in result["detail"]

    def test_子进程异常不外溢(self, monkeypatch):
        def boom(argv):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(z, "_run_shell", boom)
        result = z.run_job("knowledge_audit")
        assert result["ok"] is False
        assert "kaboom" in result["detail"]

class TestApiToken:
    """令牌来源守卫：环境优先、.env 兜底、都没有时为空串；任何分支都不打印令牌。"""

    def test_令牌优先取环境变量(self, monkeypatch):
        monkeypatch.setenv("FLASK_API_TOKEN", "  env-token  ")
        assert z._api_token() == "env-token"

    def test_环境缺失时回退仓库env文件(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FLASK_API_TOKEN", raising=False)
        (tmp_path / ".env").write_text('FLASK_API_TOKEN="file-token"', encoding="utf-8")
        monkeypatch.setattr(z, "REPO_ROOT", str(tmp_path))
        assert z._api_token() == "file-token"

    def test_两处都没有时返回空串(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FLASK_API_TOKEN", raising=False)
        monkeypatch.setattr(z, "REPO_ROOT", str(tmp_path))
        assert z._api_token() == ""

