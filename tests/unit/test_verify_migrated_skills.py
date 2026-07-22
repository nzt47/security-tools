"""verify_migrated_skills.py 的单元测试

重点覆盖 verify_skill_l3() 的两条路径:
    - 无脚本路径: 预期抛 SCRIPT_NOT_FOUND
    - 有脚本路径: 参数注入 + 执行 + 结果校验

加载方式说明:
    - verify_migrated_skills.py 位于 scripts/ 目录, 不在包路径中
    - 用 importlib 从文件路径加载, 避免污染 pytest 收集
      (直接 import 会让 verify_skill_l3 进入测试模块命名空间被收集)

同步机制说明:
    - 每个测试用例独立使用临时仓库目录, 互不干扰
    - tearDown 清理临时目录
"""
import os
import sys
import shutil
import tempfile
import importlib.util
import unittest
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent

from agent.skills_mgmt.skill_manager import SkillManager

# 从文件路径加载 verify_migrated_skills 模块 (避免污染 pytest 收集)
_SPEC = importlib.util.spec_from_file_location(
    "verify_migrated_skills",
    PROJECT_ROOT / "scripts" / "verify_migrated_skills.py",
)
_VERIFY_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VERIFY_MOD)
verify_skill_l3 = _VERIFY_MOD.verify_skill_l3
# pytest.ini 配置 python_functions = test_* verify_*,
# 会被收集为测试用例 (因参数 mgr 触发 fixture 报错).
# 标记 __test__ = False 告知 pytest 跳过此函数.
verify_skill_l3.__test__ = False


def _make_skill_md(skill_id, name="测试技能", description="测试描述",
                   default_params_yaml="", body="# 说明"):
    """生成 skill.md 内容 (可选 default_params)"""
    params_block = f"default_params:\n{default_params_yaml}\n" if default_params_yaml else ""
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
content_type: markdown
{params_block}---

{body}
"""


def _make_script_ok():
    """正常脚本: 读 stdin JSON, 输出 JSON 结果"""
    return (
        "import sys, json\n"
        "params = json.loads(sys.stdin.read() or '{}')\n"
        "print(json.dumps({'ok': True, 'echo': params.get('greeting', 'hi')}))\n"
    )


def _make_script_no_json():
    """异常脚本: 不输出 JSON (只输出纯文本)"""
    return "print('not a json')\n"


def _make_script_fail():
    """异常脚本: exit code 非0"""
    return "import sys; sys.exit(1)\n"


class TestVerifySkillL3(unittest.TestCase):
    """verify_skill_l3 单元测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="verify_l3_test_")
        self.repo = Path(self.tmpdir) / "repo"
        self.repo.mkdir()
        self.mgr = SkillManager(repo_path=str(self.repo))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _install_skill(self, skill_id, skill_md_content, script_code=None):
        """在临时仓库创建技能 (不通过 install_from_dir, 直接写文件)"""
        skill_dir = self.repo / skill_id
        skill_dir.mkdir(parents=True)
        (skill_dir / "skill.md").write_text(skill_md_content, encoding="utf-8")
        if script_code is not None:
            scripts_dir = skill_dir / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / "main.py").write_text(script_code, encoding="utf-8")
        # 刷新索引
        self.mgr.file_store.load_metadata_index(refresh=True)
        return self.mgr.file_store.get_metadata(skill_id)

    # ════════════════════════════════════════════════════════════
    # 无脚本路径
    # ════════════════════════════════════════════════════════════

    def test_no_script_returns_ok(self):
        """无脚本技能 — 预期返回 (True, code=SCRIPT_NOT_FOUND)"""
        meta = self._install_skill(
            "no-script-skill",
            _make_skill_md("no-script-skill", "无脚本技能"),
        )
        ok, msg = verify_skill_l3(self.mgr, "no-script-skill", meta or {})
        self.assertTrue(ok, f"预期 ok=True, msg={msg}")
        self.assertIn("SCRIPT_NOT_FOUND", msg)

    def test_no_script_meta_empty(self):
        """无脚本技能 + meta 为空 dict — 兜底 params={} 不报错"""
        # 不刷新索引, meta 传空 dict
        self._install_skill(
            "empty-meta-skill",
            _make_skill_md("empty-meta-skill", "空meta技能"),
        )
        ok, msg = verify_skill_l3(self.mgr, "empty-meta-skill", {})
        self.assertTrue(ok, f"预期 ok=True, msg={msg}")

    # ════════════════════════════════════════════════════════════
    # 有脚本路径 — 正常场景
    # ════════════════════════════════════════════════════════════

    def test_with_script_default_params(self):
        """有脚本技能 + default_params — 预期 success, 参数注入生效"""
        meta = self._install_skill(
            "scripted-ok",
            _make_skill_md(
                "scripted-ok", "正常脚本技能",
                default_params_yaml="  greeting: hello",
            ),
            script_code=_make_script_ok(),
        )
        ok, msg = verify_skill_l3(self.mgr, "scripted-ok", meta or {})
        self.assertTrue(ok, f"预期 ok=True, msg={msg}")
        self.assertIn("success", msg)
        self.assertIn("exit=0", msg)

    def test_with_script_no_default_params(self):
        """有脚本技能 + 无 default_params — params 兜底为 {}, 脚本默认值生效"""
        meta = self._install_skill(
            "scripted-no-params",
            _make_skill_md("scripted-no-params", "无参数脚本技能"),
            script_code=_make_script_ok(),
        )
        ok, msg = verify_skill_l3(self.mgr, "scripted-no-params", meta or {})
        self.assertTrue(ok, f"预期 ok=True, msg={msg}")
        # 脚本兜底用 'hi' (params.get('greeting', 'hi'))
        self.assertIn("success", msg)

    # ════════════════════════════════════════════════════════════
    # 有脚本路径 — 异常场景
    # ════════════════════════════════════════════════════════════

    def test_with_script_exit_nonzero(self):
        """有脚本技能 + 脚本 exit code 非0 — 预期 (False, exit_code 检查失败)"""
        meta = self._install_skill(
            "scripted-fail",
            _make_skill_md("scripted-fail", "失败脚本技能"),
            script_code=_make_script_fail(),
        )
        ok, msg = verify_skill_l3(self.mgr, "scripted-fail", meta or {})
        self.assertFalse(ok, f"预期 ok=False, msg={msg}")
        # exit code 非0, success=False 在前
        self.assertTrue("success=False" in msg or "exit_code" in msg,
                        f"msg 应含 success=False 或 exit_code, 实际: {msg}")

    def test_with_script_no_json_output(self):
        """有脚本技能 + 脚本未输出 JSON — 预期 (False, result 为空)"""
        meta = self._install_skill(
            "scripted-no-json",
            _make_skill_md("scripted-no-json", "无JSON输出技能"),
            script_code=_make_script_no_json(),
        )
        ok, msg = verify_skill_l3(self.mgr, "scripted-no-json", meta or {})
        self.assertFalse(ok, f"预期 ok=False, msg={msg}")
        self.assertIn("result 为空", msg)

    def test_skill_not_exist_treated_as_no_script(self):
        """技能 ID 不存在 — list_scripts 返回 [], 被当作无脚本技能容错处理

        verify_skill_l3 对不存在 ID 的行为:
            - list_scripts 返回 [] (不抛异常)
            - 走无脚本路径, execute 抛 SCRIPT_NOT_FOUND
            - 返回 (True, code=SCRIPT_NOT_FOUND)

        这是容错特性, 不抛异常 (而非契约保证的行为)
        """
        ok, msg = verify_skill_l3(self.mgr, "nonexistent-skill", {})
        self.assertTrue(ok, f"预期 ok=True (容错为无脚本), msg={msg}")
        self.assertIn("SCRIPT_NOT_FOUND", msg)


if __name__ == "__main__":
    unittest.main()
