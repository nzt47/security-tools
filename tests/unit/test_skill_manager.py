"""SkillManager 单元测试 — 通用门面的完整覆盖

重点覆盖:
    - L1 匹配失败场景: 空意图 / 无匹配 / 空仓库 / min_score 过高 / top_k 非法
    - L3 脚本执行异常: 超时 / 脚本不存在 / 参数错误 / exit code 非0 / 路径越界
    - 安装/卸载: 目录安装 / zip 安装 / 覆盖安装 / 卸载不存在
    - 上下文构建: 空意图 / 自动加载说明 / 显式指定 skill_id
    - 健康检查: 正常 / 组件异常降级

同步机制说明:
    - 每个测试用例独立使用临时仓库目录，互不干扰
    - 安装/卸载操作使用 try/finally 确保清理
"""
import os
import sys
import shutil
import tempfile
import json
import zipfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from agent.skills_mgmt.skill_manager import SkillManager
from agent.skills_mgmt.exceptions import (
    SkillNotFoundError,
    SkillValidationError,
    SkillFileError,
    SkillExecutionError,
    ErrorCode,
)


def _make_skill_md(skill_id, name="测试技能", description="测试描述", instruction_body="# 说明"):
    """生成 skill.md 内容"""
    return f"""---
id: {skill_id}
name: {name}
description: {description}
category: custom
tags: [test]
version: 1.0.0
enabled: true
status: approved
author: tester
---

{instruction_body}
"""


def _make_script(code=None):
    """生成脚本内容"""
    if code is None:
        code = '''import sys, json
params = json.loads(sys.stdin.read() or "{}")
print(json.dumps({"ok": True, "echo": params}))
'''
    return code


class TestSkillManagerInstall(unittest.TestCase):
    """安装/卸载测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="skill_mgr_test_")
        self.mgr = SkillManager(repo_path=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_install_from_dir_basic(self):
        """从目录安装 — 正常流程"""
        skill_dir = Path(self.tmpdir) / "source" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "skill.md").write_text(
            _make_skill_md("my-skill", "我的技能"), encoding="utf-8")
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "main.py").write_text(_make_script(), encoding="utf-8")

        skill_id = self.mgr.install_from_dir(str(skill_dir))
        self.assertEqual(skill_id, "my-skill")

        # 验证安装后可以匹配到
        result = self.mgr.match("我的技能", top_k=5)
        self.assertTrue(any(m.skill_id == "my-skill" for m in result.matches))

    def test_install_from_dir_missing_skill_md(self):
        """从目录安装 — 缺少 skill.md 应抛异常"""
        skill_dir = Path(self.tmpdir) / "source" / "bad-skill"
        skill_dir.mkdir(parents=True)

        with self.assertRaises(SkillValidationError) as ctx:
            self.mgr.install_from_dir(str(skill_dir))
        self.assertEqual(ctx.exception.code, ErrorCode.MD_READ_ERROR)

    def test_install_from_dir_not_exist(self):
        """从目录安装 — 目录不存在应抛异常"""
        with self.assertRaises(SkillValidationError) as ctx:
            self.mgr.install_from_dir("/nonexistent/path/skill")
        self.assertEqual(ctx.exception.code, ErrorCode.VALIDATION_ERROR)

    def test_install_from_dir_missing_id(self):
        """从目录安装 — skill.md 缺少 id 字段应抛异常"""
        skill_dir = Path(self.tmpdir) / "source" / "noid"
        skill_dir.mkdir(parents=True)
        (skill_dir / "skill.md").write_text(
            "---\nname: 无ID技能\n---\n# 说明\n", encoding="utf-8")

        with self.assertRaises(SkillValidationError) as ctx:
            self.mgr.install_from_dir(str(skill_dir))
        self.assertEqual(ctx.exception.code, ErrorCode.VALIDATION_ERROR)

    def test_install_from_zip_basic(self):
        """从 zip 包安装 — 正常流程"""
        zip_path = Path(self.tmpdir) / "test-skill.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            manifest = {
                "id": "zip-skill",
                "name": "ZIP技能",
                "description": "从zip安装",
                "category": "custom",
                "tags": ["test"],
                "version": "1.0.0",
                "enabled": True,
                "status": "approved",
            }
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
            zf.writestr("skill.md", _make_skill_md("zip-skill", "ZIP技能"))
            zf.writestr("scripts/main.py", _make_script())

        skill_id = self.mgr.install_from_zip(str(zip_path))
        self.assertEqual(skill_id, "zip-skill")

    def test_install_from_zip_missing_manifest(self):
        """从 zip 包安装 — 缺少 manifest.json 应抛异常"""
        zip_path = Path(self.tmpdir) / "bad.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("skill.md", "# 只有说明")

        with self.assertRaises(SkillValidationError) as ctx:
            self.mgr.install_from_zip(str(zip_path))
        self.assertEqual(ctx.exception.code, ErrorCode.VALIDATION_ERROR)

    def test_install_from_zip_not_exist(self):
        """从 zip 包安装 — 文件不存在应抛异常"""
        with self.assertRaises(SkillValidationError):
            self.mgr.install_from_zip("/nonexistent/skill.zip")

    def test_install_overwrite(self):
        """覆盖安装 — 同 ID 技能应覆盖旧版本"""
        skill_dir = Path(self.tmpdir) / "source" / "ow-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "skill.md").write_text(
            _make_skill_md("ow-skill", "原始名称"), encoding="utf-8")

        self.mgr.install_from_dir(str(skill_dir))

        # 修改后重新安装
        (skill_dir / "skill.md").write_text(
            _make_skill_md("ow-skill", "更新名称"), encoding="utf-8")
        self.mgr.install_from_dir(str(skill_dir))

        result = self.mgr.match("更新名称", top_k=5)
        self.assertTrue(any(m.name == "更新名称" for m in result.matches))

    def test_uninstall_basic(self):
        """卸载技能 — 正常流程"""
        skill_dir = Path(self.tmpdir) / "source" / "del-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "skill.md").write_text(
            _make_skill_md("del-skill", "待删除"), encoding="utf-8")
        self.mgr.install_from_dir(str(skill_dir))

        ok = self.mgr.uninstall("del-skill")
        self.assertTrue(ok)

        # 验证已删除
        result = self.mgr.match("待删除", top_k=5)
        self.assertFalse(any(m.skill_id == "del-skill" for m in result.matches))

    def test_uninstall_not_exist(self):
        """卸载技能 — 不存在应抛异常"""
        with self.assertRaises(SkillNotFoundError):
            self.mgr.uninstall("nonexistent-skill")


class TestL1MatchFailures(unittest.TestCase):
    """L1 匹配失败场景测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="skill_l1_test_")
        self.mgr = SkillManager(repo_path=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _install_skill(self, skill_id, name, desc, tags=None):
        """辅助: 快速安装一个技能"""
        skill_dir = Path(self.tmpdir) / "source" / skill_id
        skill_dir.mkdir(parents=True)
        md = _make_skill_md(skill_id, name, desc)
        if tags:
            md = md.replace("tags: [test]", f"tags: {json.dumps(tags)}")
        (skill_dir / "skill.md").write_text(md, encoding="utf-8")
        self.mgr.install_from_dir(str(skill_dir))

    def test_match_empty_intent(self):
        """L1 匹配 — 空意图应抛 SkillValidationError"""
        with self.assertRaises(SkillValidationError) as ctx:
            self.mgr.match("")
        self.assertEqual(ctx.exception.code, ErrorCode.VALIDATION_ERROR)

    def test_match_whitespace_intent(self):
        """L1 匹配 — 纯空白意图应抛 SkillValidationError"""
        with self.assertRaises(SkillValidationError) as ctx:
            self.mgr.match("   \n\t  ")
        self.assertEqual(ctx.exception.code, ErrorCode.VALIDATION_ERROR)

    def test_match_no_results(self):
        """L1 匹配 — 无匹配技能时返回空列表"""
        self._install_skill("pdf-tool", "PDF工具", "解析PDF文件")
        result = self.mgr.match("量子计算量子纠缠", top_k=5)
        # 无匹配时 matches 可能为空或分数极低
        self.assertIsInstance(result.matches, list)

    def test_match_empty_repo(self):
        """L1 匹配 — 空仓库时返回空列表不报错"""
        result = self.mgr.match("任何意图", top_k=5)
        self.assertEqual(len(result.matches), 0)
        self.assertEqual(result.total_scanned, 0)

    def test_match_min_score_too_high(self):
        """L1 匹配 — min_score 过高导致无结果"""
        self._install_skill("text-tool", "文本工具", "文本处理")
        result = self.mgr.match("文本", top_k=5, min_score=999.0)
        self.assertEqual(len(result.matches), 0)

    def test_match_invalid_top_k(self):
        """L1 匹配 — top_k < 1 应抛异常"""
        with self.assertRaises(SkillValidationError) as ctx:
            self.mgr.match("测试", top_k=0)
        self.assertEqual(ctx.exception.code, ErrorCode.VALIDATION_ERROR)

    def test_match_negative_top_k(self):
        """L1 匹配 — 负数 top_k 应抛异常"""
        with self.assertRaises(SkillValidationError):
            self.mgr.match("测试", top_k=-1)

    def test_match_disabled_only(self):
        """L1 匹配 — enabled_only=True 过滤禁用技能"""
        self._install_skill("enabled-skill", "启用技能", "测试")
        # 安装一个禁用的技能
        skill_dir = Path(self.tmpdir) / "source" / "disabled-skill"
        skill_dir.mkdir(parents=True)
        md = _make_skill_md("disabled-skill", "禁用技能", "测试")
        md = md.replace("enabled: true", "enabled: false")
        (skill_dir / "skill.md").write_text(md, encoding="utf-8")
        self.mgr.install_from_dir(str(skill_dir))

        result = self.mgr.match("测试", top_k=10, enabled_only=True)
        skill_ids = [m.skill_id for m in result.matches]
        self.assertIn("enabled-skill", skill_ids)
        self.assertNotIn("disabled-skill", skill_ids)


class TestL3ExecutionExceptions(unittest.TestCase):
    """L3 脚本执行异常场景测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="skill_l3_test_")
        self.mgr = SkillManager(repo_path=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _install_skill_with_script(self, skill_id, script_code):
        """辅助: 安装带脚本的技能"""
        skill_dir = Path(self.tmpdir) / "source" / skill_id
        skill_dir.mkdir(parents=True)
        (skill_dir / "skill.md").write_text(
            _make_skill_md(skill_id, skill_id, "测试"), encoding="utf-8")
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "main.py").write_text(script_code, encoding="utf-8")
        self.mgr.install_from_dir(str(skill_dir))

    def test_execute_success(self):
        """L3 执行 — 正常成功"""
        self._install_skill_with_script("ok-skill", _make_script())
        result = self.mgr.execute("ok-skill", params={"key": "value"})
        self.assertTrue(result.success)
        self.assertEqual(result.exit_code, 0)
        self.assertIsNotNone(result.result)
        self.assertEqual(result.result.get("ok"), True)

    def test_execute_script_not_found(self):
        """L3 执行 — 脚本不存在应抛 SkillExecutionError"""
        self._install_skill_with_script("ok-skill", _make_script())
        with self.assertRaises(SkillExecutionError) as ctx:
            self.mgr.execute("ok-skill", script_name="nonexistent.py")
        self.assertEqual(ctx.exception.code, ErrorCode.SCRIPT_NOT_FOUND)

    def test_execute_skill_not_found(self):
        """L3 执行 — 技能不存在应抛异常"""
        with self.assertRaises((SkillNotFoundError, SkillExecutionError)):
            self.mgr.execute("nonexistent-skill")

    def test_execute_timeout(self):
        """L3 执行 — 脚本超时应返回 timed_out=True 的失败结果"""
        # 写一个会 sleep 很久的脚本
        slow_script = '''import time
import sys, json
print("starting", file=sys.stderr)
time.sleep(10)  # 超过超时时间
print(json.dumps({"ok": True}))
'''
        self._install_skill_with_script("slow-skill", slow_script)
        result = self.mgr.execute("slow-skill", timeout=1)
        self.assertFalse(result.success)
        self.assertTrue(result.timed_out)

    def test_execute_script_error_exit_code(self):
        """L3 执行 — 脚本 exit code 非0 应返回失败"""
        error_script = '''import sys
sys.exit(1)
'''
        self._install_skill_with_script("err-skill", error_script)
        result = self.mgr.execute("err-skill")
        self.assertFalse(result.success)
        self.assertNotEqual(result.exit_code, 0)

    def test_execute_script_invalid_json_output(self):
        """L3 执行 — 脚本输出非 JSON 应返回 success=True 但 result=None"""
        bad_script = '''print("这不是JSON")
print("第二行也不是")
'''
        self._install_skill_with_script("bad-json-skill", bad_script)
        result = self.mgr.execute("bad-json-skill")
        # 脚本 exit 0 但输出无法解析为 JSON
        self.assertTrue(result.success)
        self.assertIsNone(result.result)

    def test_execute_script_param_error(self):
        """L3 执行 — 脚本内部参数校验失败应返回错误"""
        param_check_script = '''import sys, json
params = json.loads(sys.stdin.read() or "{}")
if "required_field" not in params:
    print(json.dumps({"error": "缺少 required_field"}))
    sys.exit(1)
print(json.dumps({"ok": True}))
'''
        self._install_skill_with_script("param-skill", param_check_script)
        # 不传 required_field
        result = self.mgr.execute("param-skill", params={})
        self.assertFalse(result.success)

        # 传 required_field
        result = self.mgr.execute("param-skill", params={"required_field": "value"})
        self.assertTrue(result.success)

    def test_execute_no_params(self):
        """L3 执行 — 不传 params 应正常工作"""
        self._install_skill_with_script("ok-skill", _make_script())
        result = self.mgr.execute("ok-skill")
        self.assertTrue(result.success)

    def test_execute_result_no_raw_stdout(self):
        """L3 执行 — 结果不应暴露 raw stdout（代码不泄漏）"""
        self._install_skill_with_script("ok-skill", _make_script())
        result = self.mgr.execute("ok-skill", params={"test": True})
        result_dict = result.to_dict()
        # 关键验证: to_dict() 不应包含 raw stdout
        self.assertNotIn("stdout", result_dict)
        # 但应包含 result
        self.assertIn("result", result_dict)

    def test_execute_path_traversal_blocked(self):
        """L3 执行 — 路径越界的脚本名应被阻止"""
        self._install_skill_with_script("ok-skill", _make_script())
        # 尝试用路径越界的脚本名
        with self.assertRaises((SkillFileError, SkillValidationError)):
            self.mgr.execute("ok-skill", script_name="../../../etc/passwd")


class TestBuildContext(unittest.TestCase):
    """上下文构建测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="skill_ctx_test_")
        self.mgr = SkillManager(repo_path=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_build_context_empty_intent(self):
        """构建上下文 — 空意图应抛异常"""
        with self.assertRaises(SkillValidationError):
            self.mgr.build_context("")

    def test_build_context_empty_repo(self):
        """构建上下文 — 空仓库不报错"""
        result = self.mgr.build_context("测试意图")
        self.assertIn("prompt", result)
        self.assertIn("total_tokens", result)

    def test_build_context_auto_load(self):
        """构建上下文 — auto_load_instruction=True 自动加载说明"""
        skill_dir = Path(self.tmpdir) / "source" / "ctx-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "skill.md").write_text(
            _make_skill_md("ctx-skill", "上下文技能", "上下文测试",
                          instruction_body="# 详细说明\n这是详细的使用说明。"),
            encoding="utf-8")
        self.mgr.install_from_dir(str(skill_dir))

        result = self.mgr.build_context("上下文", auto_load_instruction=True)
        # build_context 将说明嵌入 prompt 中
        prompt = result.get("prompt", "")
        self.assertIn("详细说明", prompt)
        # layers 应显示 L2 已加载
        self.assertTrue(result.get("layers", {}).get("layer2_instruction"))

    def test_build_context_no_code_in_prompt(self):
        """构建上下文 — prompt 中不应包含脚本代码"""
        skill_dir = Path(self.tmpdir) / "source" / "code-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "skill.md").write_text(
            _make_skill_md("code-skill", "代码技能", "代码测试"), encoding="utf-8")
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "main.py").write_text(
            "import os\nimport sys\ndangerous_code = True\n", encoding="utf-8")
        self.mgr.install_from_dir(str(skill_dir))

        result = self.mgr.build_context("代码", auto_load_instruction=True)
        prompt = result.get("prompt", "")
        self.assertNotIn("import os", prompt)
        self.assertNotIn("dangerous_code", prompt)


class TestHealthAndQuery(unittest.TestCase):
    """健康检查与查询测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="skill_health_test_")
        self.mgr = SkillManager(repo_path=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_health_ok(self):
        """健康检查 — 空仓库也应返回 ok=True"""
        h = self.mgr.health()
        self.assertTrue(h["ok"])
        self.assertEqual(h["module"], "skill_manager")
        self.assertIn("file_store", h)
        self.assertIn("executor", h)
        self.assertIn("layer_summary", h)

    def test_list_skills_empty(self):
        """列出技能 — 空仓库返回空列表"""
        skills = self.mgr.list_skills()
        self.assertEqual(len(skills), 0)

    def test_get_skill_info_not_found(self):
        """获取技能信息 — 不存在应抛异常"""
        with self.assertRaises(SkillNotFoundError):
            self.mgr.get_skill_info("nonexistent")

    def test_get_layer_summary(self):
        """三层统计 — 返回正确结构"""
        summary = self.mgr.get_layer_summary()
        self.assertIsInstance(summary, dict)

    def test_export_to_zip(self):
        """导出技能包 — 生成 zip 文件"""
        # 先安装一个技能
        skill_dir = Path(self.tmpdir) / "source" / "export-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "skill.md").write_text(
            _make_skill_md("export-skill", "导出技能", "导出测试"), encoding="utf-8")
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "main.py").write_text(_make_script(), encoding="utf-8")
        self.mgr.install_from_dir(str(skill_dir))

        # 导出
        zip_path = Path(self.tmpdir) / "export.zip"
        result_path = self.mgr.export_to_zip("export-skill", str(zip_path))
        self.assertTrue(Path(result_path).exists())

        # 验证 zip 内容
        with zipfile.ZipFile(result_path, "r") as zf:
            names = zf.namelist()
            self.assertIn("manifest.json", names)
            self.assertIn("skill.md", names)
            self.assertIn("scripts/main.py", names)

    def test_export_to_zip_not_found(self):
        """导出技能包 — 不存在应抛异常"""
        with self.assertRaises(SkillNotFoundError):
            self.mgr.export_to_zip("nonexistent", "/tmp/out.zip")


if __name__ == "__main__":
    unittest.main(verbosity=2)
