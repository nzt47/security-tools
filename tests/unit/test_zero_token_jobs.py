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



class TestLearningDrafts:
    def test_学习作业只报草稿数不打印记忆内容(self, monkeypatch):
        """学习抽象作业：请求体固定（days/max_skills），结果只回计数（隐私）。"""
        captured: dict = {}

        def fake_http(method, parts, timeout=0, body=None):
            captured['method'] = method
            captured['parts'] = parts
            captured['body'] = body
            return {'status': 200, 'body': {'data': {
                'ok': True, 'total_clusters': 3, 'passed_clusters': 2,
                'drafts': [{'drafted': True}, {'drafted': False}, {'drafted': True}],
            }}}

        monkeypatch.setattr(z, '_http_json', fake_http)
        job = z._BY_KEY['learning_drafts']
        ok, detail = z._job_learning_drafts(job)
        assert ok is True
        assert captured['method'] == 'POST'
        assert captured['body'] == {'days': 7, 'max_skills': 3}
        assert '草稿=2' in detail
        assert '聚类=3' in detail

    def test_学习作业在清单里且零_token(self):
        job = z._BY_KEY['learning_drafts']
        assert job['kind'] == 'http' and job['method'] == 'POST'
        assert job['endpoint'][-1] == 'abstract-from-memory'


class TestDescribe:
    def test_describe可JSON化(self):
        payload = json.loads(json.dumps(z.describe(), ensure_ascii=False))
        assert len(payload) == len(z.JOBS)
        assert all(item["at"].count(":") == 1 for item in payload)


class TestRegister:
    def test_注册条数与时刻一致(self, monkeypatch):
        monkeypatch.delenv("ZERO_TOKEN_JOBS_ENABLED", raising=False)
        calls = []

        intervals: list = []

        class FakeScheduler:
            def add_cron_task(self, **kwargs):
                calls.append(kwargs)

            def add_interval_task(self, **kwargs):
                intervals.append(kwargs)

        keys = z.register_zero_token_jobs(FakeScheduler())
        assert keys == [j["key"] for j in z.JOBS]
        assert [(c["hour"], c["minute"]) for c in calls] == [
            (j["hour"], j["minute"]) for j in z.JOBS
        ]
        assert all(c["day_of_week"] is None for c in calls)
        assert all(callable(c["func"]) for c in calls)
        # 2026-11-13：多注册一条「维护·空闲补跑」（空闲闸门的补跑线程）
        # 2026-11-13：再注册一条「维护·记忆整理」（6h 空闲轮询）
        assert [i["name"] for i in intervals] == ["维护·记忆整理", "维护·空闲补跑"]
        by_name = {i["name"]: i for i in intervals}
        assert by_name["维护·空闲补跑"]["interval_seconds"] == z.DRAIN_INTERVAL_SEC
        assert by_name["维护·记忆整理"]["interval_seconds"] == z.CONSOLIDATION_INTERVAL_SEC
        assert all(i["zero_token"] is True for i in intervals)

    def test_开关可关闭(self, monkeypatch):
        monkeypatch.setenv("ZERO_TOKEN_JOBS_ENABLED", "0")

        class FakeScheduler:
            def add_cron_task(self, **kwargs):
                raise AssertionError("关闭时不应注册")

            def add_interval_task(self, **kwargs):
                raise AssertionError("关闭时不应注册补跑任务")

        assert z.register_zero_token_jobs(FakeScheduler()) == []


class TestRunJob:
    def test_未知作业不抛(self):
        result = z.run_job("no-such-job")
        assert result["ok"] is False
        assert result["job"] == "no-such-job"

    def test_shell成功路径(self, monkeypatch):
        monkeypatch.setattr(z, "_run_shell", lambda argv: (True, "ok"))
        result = z.run_job("route_coverage_check", force=True)
        assert result["ok"] is True
        assert result["elapsed_ms"] >= 0

    def test_shell失败被如实回报(self, monkeypatch):
        monkeypatch.setattr(z, "_run_shell", lambda argv: (False, "exit=1 boom"))
        result = z.run_job("capability_manifest_check", force=True)
        assert result["ok"] is False
        assert "boom" in result["detail"]

    def test_子进程异常不外溢(self, monkeypatch):
        def boom(argv):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(z, "_run_shell", boom)
        result = z.run_job("knowledge_audit", force=True)
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


class TestIdleGate:
    """空闲闸门（2026-11-13）：到点不空闲就排队，空闲/兜底时补跑。

    为什么这些用例重要：闸门的判据（agent/system_idle）如果退化成"恒空闲"，
    这一整套就白做（§11.103 F：原 IdleDetector 就是恒空闲）。这里直接替换 z.idle_state，
    把"空闲/不空闲/强制"三种分支都钉住。
    """

    @staticmethod
    def _idle(is_idle: bool):
        return lambda: {"is_idle": is_idle, "reason": "test", "idle_source": "test",
                        "degraded": False, "idle_seconds": 600.0, "cpu_percent": 1.0}

    def test_到点不空闲就排队而不是执行(self, monkeypatch, tmp_path):
        monkeypatch.setattr(z, "REPO_ROOT", str(tmp_path))
        monkeypatch.setattr(z, "idle_state", self._idle(False))
        ran = []
        monkeypatch.setattr(z, "_run_shell", lambda argv: (ran.append(argv), (True, "ok"))[1])

        result = z.run_job("route_coverage_check")

        assert result["ok"] is True and result["deferred"] is True
        assert ran == [], "不空闲时不许真的跑"
        pending = z._load_pending()
        assert pending["keys"] == ["route_coverage_check"]

    def test_空闲时直接执行(self, monkeypatch, tmp_path):
        monkeypatch.setattr(z, "REPO_ROOT", str(tmp_path))
        monkeypatch.setattr(z, "idle_state", self._idle(True))
        monkeypatch.setattr(z, "_run_shell", lambda argv: (True, "ok"))

        result = z.run_job("route_coverage_check")

        assert result.get("deferred") is not True and result["ok"] is True

    def test_补跑在空闲时执行并清空队列(self, monkeypatch, tmp_path):
        monkeypatch.setattr(z, "REPO_ROOT", str(tmp_path))
        monkeypatch.setattr(z, "idle_state", self._idle(True))
        monkeypatch.setattr(z, "_run_shell", lambda argv: (True, "ok"))
        z.mark_pending("route_coverage_check")
        z.mark_pending("knowledge_audit")

        out = z.drain_pending()

        assert len(out["ran"]) == 2 and out["waiting"] == 0
        assert z._load_pending()["keys"] == []

    def test_不空闲且未到兜底时刻则继续等(self, monkeypatch, tmp_path):
        monkeypatch.setattr(z, "REPO_ROOT", str(tmp_path))
        monkeypatch.setattr(z, "idle_state", self._idle(False))
        monkeypatch.setattr(z, "FALLBACK_HOUR", 99)  # 永远不到兜底
        z.mark_pending("route_coverage_check")
        monkeypatch.setattr(z, "_run_shell", lambda argv: (_ for _ in ()).throw(AssertionError("不应执行")))

        out = z.drain_pending()

        assert out["ran"] == [] and out["waiting"] == 1
        assert z._load_pending()["keys"] == ["route_coverage_check"]

    def test_到兜底时刻强制跑(self, monkeypatch, tmp_path):
        monkeypatch.setattr(z, "REPO_ROOT", str(tmp_path))
        monkeypatch.setattr(z, "idle_state", self._idle(False))
        monkeypatch.setattr(z, "FALLBACK_HOUR", 0)  # 任何时刻都算"已过兜底"
        monkeypatch.setattr(z, "_run_shell", lambda argv: (True, "ok"))
        z.mark_pending("route_coverage_check")

        out = z.drain_pending()

        assert len(out["ran"]) == 1 and out["forced"] is True
        assert z._load_pending()["keys"] == []

    def test_跨天的待办作废(self, monkeypatch, tmp_path):
        monkeypatch.setattr(z, "REPO_ROOT", str(tmp_path))
        path = tmp_path / z.PENDING_REL_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"date": "2000-01-01", "keys": ["route_coverage_check"]}', encoding="utf-8")

        assert z._load_pending()["keys"] == []

    def test_未知作业不入队(self, monkeypatch, tmp_path):
        monkeypatch.setattr(z, "REPO_ROOT", str(tmp_path))
        path = tmp_path / z.PENDING_REL_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"date": "%s", "keys": ["no-such-job"]}' % z._today(), encoding="utf-8")

        assert z._load_pending()["keys"] == []
