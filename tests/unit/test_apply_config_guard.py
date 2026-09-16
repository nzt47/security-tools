"""scripts/apply_config_and_test.py「默认不可破坏」守卫的单元测试

覆盖（对应"防误覆盖"改造）：
1. 默认 dry-run：不写文件、不建备份、不跑后续测试流程，退出码 0
2. --apply：写前自动备份 + 写入成功（无损 / 有损 + --force 两种情形）
3. 有损保护：目标已有而模板缺失的关键词会被删除且未给 --force → 拒绝写入、文件不变、退出码非零
4. 失败不破坏：模板缺失 / JSON 损坏 / 结构缺 keywords_config.keywords / 目标文件损坏
   → 非零退出且不写文件
5. 备份失败中止写入（原文件保持不变）
6. dry-run diff 正确列出「将丢失」的关键词名与总词数变化

【不易】绝不触碰真实 data/tool_router_keywords.json：脚本的三条路径常量在 cfg
fixture 里全部 monkeypatch 到 tmp_path（脚本按 importlib 动态加载，技法同
tests/unit/test_tool_definitions_yaml.py:34-43，因为 scripts 不是包）。
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ────────────────────────────────────────────────────────────
#  动态加载 scripts/apply_config_and_test.py（scripts 非包，用 importlib）
# ────────────────────────────────────────────────────────────

def _load_apply_module():
    spec = importlib.util.spec_from_file_location(
        "apply_config_and_test",
        _PROJECT_ROOT / "scripts" / "apply_config_and_test.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[arg-type]
    return mod


apply_mod = _load_apply_module()


# 模板词表（模拟失同步：file 类别缺 grep/检索 等新增词）
TEMPLATE_KEYWORDS = {
    "web": ["搜索", "search"],
    "file": ["文件", "read", "write"],
}
# 目标词表（无损：与模板同集）
LOSSLESS_TARGET_KEYWORDS = {
    "web": ["搜索", "search"],
    "file": ["文件", "read", "write"],
}
# 目标词表（有损：file 多两个词 + 多一个 memory 类别）
LOSSY_TARGET_KEYWORDS = {
    "web": ["搜索", "search"],
    "file": ["文件", "read", "write", "grep", "检索"],
    "memory": ["记忆", "remember"],
}
# 有损覆盖会删掉的关键词
LOST_KEYWORDS = ["grep", "检索", "记忆", "remember"]


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """把脚本三条路径常量指向 tmp_path，并把 --apply 之后的测试流程替换为桩

    返回 ``(template_path, target_path, backup_dir, calls)``；``calls`` 记录
    后续流程是否被调用（验证 --apply 的向后兼容行为）。
    """
    template = tmp_path / "tool_router_default_config.json"
    target = tmp_path / "tool_router_keywords.json"
    backup_dir = tmp_path / ".backups"

    monkeypatch.setattr(apply_mod, "DEFAULT_CONFIG_PATH", str(template))
    monkeypatch.setattr(apply_mod, "TARGET_CONFIG_PATH", str(target))
    monkeypatch.setattr(apply_mod, "BACKUP_DIR", str(backup_dir))

    calls = []
    monkeypatch.setattr(apply_mod, "run_full_test",
                        lambda: calls.append("run_full_test") or True)
    monkeypatch.setattr(apply_mod, "analyze_boundary_conditions",
                        lambda: calls.append("analyze_boundary_conditions") or [])
    return template, target, backup_dir, calls


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_template(path, keywords=None):
    """写模板文件（结构 keywords_config.keywords）"""
    _write_json(path, {"keywords_config": {"keywords": keywords or TEMPLATE_KEYWORDS}})


def _write_target(path, keywords=None):
    """写目标文件（结构 keywords）"""
    _write_json(path, {"keywords": keywords or LOSSY_TARGET_KEYWORDS})


def _backups(backup_dir):
    """列出备份目录里的备份文件（目录不存在则为空表）"""
    if not backup_dir.exists():
        return []
    return sorted(backup_dir.glob("tool_router_keywords.json.bak_*"))


# ════════════════════════════════════════════════════════════
#  0. 路径常量仍指向仓库真实文件（确保守卫保护的是真文件）
# ════════════════════════════════════════════════════════════

class TestPathConstants:

    def test_constants_point_to_repo_files(self):
        assert Path(apply_mod.DEFAULT_CONFIG_PATH) == (
            _PROJECT_ROOT / "data" / "tool_router_default_config.json")
        assert Path(apply_mod.TARGET_CONFIG_PATH) == (
            _PROJECT_ROOT / "data" / "tool_router_keywords.json")
        assert Path(apply_mod.BACKUP_DIR) == _PROJECT_ROOT / ".backups"


# ════════════════════════════════════════════════════════════
#  1. 默认 dry-run —— 只打印差异，绝不写文件
# ════════════════════════════════════════════════════════════

class TestDryRunDefault:

    def test_no_args_does_not_write_file(self, cfg, capsys):
        """不带参数：目标文件逐字节不变、无备份、不跑后续流程、退出码 0"""
        template, target, backup_dir, calls = cfg
        _write_template(template)
        _write_target(target)
        before = target.read_bytes()

        assert apply_mod.main([]) == 0

        assert target.read_bytes() == before
        assert not backup_dir.exists()
        assert calls == []
        out = capsys.readouterr().out
        assert "dry-run" in out
        assert "未写入任何文件" in out

    def test_dry_run_diff_lists_lost_keywords(self, cfg, capsys):
        """dry-run diff 必须逐条列出「将丢失」的关键词名"""
        template, target, _, _ = cfg
        _write_template(template)
        _write_target(target)

        assert apply_mod.main([]) == 0
        out = capsys.readouterr().out

        assert "关键词差异" in out
        assert "将丢失" in out
        for keyword in LOST_KEYWORDS:
            assert keyword in out, f"diff 未列出将丢失的关键词: {keyword}"
        # 整个类别消失 / 逐类别计数与总词数变化都要打印
        assert "整个类别" in out
        assert "file: 5 → 3 个关键词（-2）" in out
        assert "总词数: 9 → 5（-4）" in out
        assert "web: 无变化" in out

    def test_dry_run_with_force_still_does_not_write(self, cfg, capsys):
        """--force 单独使用（无 --apply）仍是 dry-run，不写文件"""
        template, target, backup_dir, _ = cfg
        _write_template(template)
        _write_target(target)
        before = target.read_bytes()

        assert apply_mod.main(["--force"]) == 0

        assert target.read_bytes() == before
        assert _backups(backup_dir) == []
        assert "--force 仅在 --apply 时生效" in capsys.readouterr().out


# ════════════════════════════════════════════════════════════
#  2. --apply —— 备份后写入
# ════════════════════════════════════════════════════════════

class TestApply:

    def test_apply_lossless_writes_and_backs_up(self, cfg):
        """无损 + --apply：写入模板词表、生成 UTC 时间戳备份、后续流程照跑"""
        template, target, backup_dir, calls = cfg
        _write_template(template)
        _write_target(target, LOSSLESS_TARGET_KEYWORDS)
        before = target.read_bytes()

        assert apply_mod.main(["--apply"]) == 0

        assert json.loads(target.read_text(encoding="utf-8"))["keywords"] == TEMPLATE_KEYWORDS
        backups = _backups(backup_dir)
        assert len(backups) == 1, f"应生成 1 个备份，实际: {backups}"
        assert re.match(r"^tool_router_keywords\.json\.bak_\d{8}_\d{6}$", backups[0].name)
        assert backups[0].read_bytes() == before  # 备份 = 写入前的原文件
        # 向后兼容：--apply 时原有的全量测试与边界分析仍执行
        assert calls == ["run_full_test", "analyze_boundary_conditions"]

    def test_apply_lossy_with_force_writes_and_backs_up(self, cfg, capsys):
        """有损 + --force：允许写入，但必须打印警告与丢失清单，且备份照做"""
        template, target, backup_dir, _ = cfg
        _write_template(template)
        _write_target(target)
        before = target.read_bytes()

        assert apply_mod.main(["--apply", "--force"]) == 0

        assert json.loads(target.read_text(encoding="utf-8"))["keywords"] == TEMPLATE_KEYWORDS
        backups = _backups(backup_dir)
        assert len(backups) == 1
        assert backups[0].read_bytes() == before
        out = capsys.readouterr().out
        assert "警告" in out and "--force" in out
        for keyword in LOST_KEYWORDS:
            assert keyword in out, f"丢失清单未列出: {keyword}"

    def test_apply_without_existing_target_writes_without_backup(self, cfg):
        """目标文件不存在：按空词表处理，直接写入且不产生备份（无内容可备份）"""
        template, target, backup_dir, _ = cfg
        _write_template(template)

        assert apply_mod.main(["--apply"]) == 0

        assert json.loads(target.read_text(encoding="utf-8"))["keywords"] == TEMPLATE_KEYWORDS
        assert _backups(backup_dir) == []


# ════════════════════════════════════════════════════════════
#  3. 有损覆盖保护
# ════════════════════════════════════════════════════════════

class TestLossyGuard:

    def test_lossy_without_force_refuses_write(self, cfg, capsys):
        """有损且无 --force：拒绝写入、目标文件不变、退出码非零、不跑后续流程"""
        template, target, backup_dir, calls = cfg
        _write_template(template)
        _write_target(target)
        before = target.read_bytes()

        assert apply_mod.main(["--apply"]) == 1

        assert target.read_bytes() == before
        assert _backups(backup_dir) == []
        assert calls == []
        out = capsys.readouterr().out
        assert "拒绝写入" in out
        assert "--force" in out
        for keyword in LOST_KEYWORDS:
            assert keyword in out, f"丢失清单未逐条打印: {keyword}"

    def test_apply_default_config_returns_false_when_lossy(self, cfg):
        """函数级契约：有损且无 force 时 apply_default_config 返回 False"""
        template, target, _, _ = cfg
        _write_template(template)
        _write_target(target)
        before = target.read_bytes()

        assert apply_mod.apply_default_config(apply=True, force=False) is False
        assert target.read_bytes() == before

    def test_never_deletes_real_keywords_without_force(self, cfg, monkeypatch):
        """端到端语义：模板缺的关键词在拒绝后仍存在于目标文件中"""
        template, target, _, _ = cfg
        _write_template(template)
        _write_target(target)

        assert apply_mod.main(["--apply"]) == 1

        kept = json.loads(target.read_text(encoding="utf-8"))["keywords"]
        assert kept["file"] == LOSSY_TARGET_KEYWORDS["file"]
        assert kept["memory"] == LOSSY_TARGET_KEYWORDS["memory"]


# ════════════════════════════════════════════════════════════
#  4. 失败不破坏 —— 非零退出且不写文件
# ════════════════════════════════════════════════════════════

class TestFailSafe:

    def test_template_missing(self, cfg, capsys):
        template, target, backup_dir, calls = cfg
        _write_target(target)
        before = target.read_bytes()

        assert apply_mod.main(["--apply"]) == 1

        assert target.read_bytes() == before
        assert _backups(backup_dir) == []
        assert calls == []
        assert "模板文件不存在" in capsys.readouterr().out

    def test_template_invalid_json(self, cfg, capsys):
        template, target, _, _ = cfg
        template.write_text("{ 不是合法 JSON", encoding="utf-8")
        _write_target(target)
        before = target.read_bytes()

        assert apply_mod.main(["--apply"]) == 1

        assert target.read_bytes() == before
        assert "JSON 解析失败" in capsys.readouterr().out

    def test_template_missing_keywords_structure(self, cfg, capsys):
        template, target, _, _ = cfg
        _write_json(template, {"keywords_config": {"description": "缺 keywords"}})
        _write_target(target)
        before = target.read_bytes()

        assert apply_mod.main(["--apply"]) == 1

        assert target.read_bytes() == before
        assert "keywords_config.keywords" in capsys.readouterr().out

    def test_target_invalid_json_refuses_write(self, cfg, capsys):
        """目标文件损坏时无法判断会丢失什么 → 即使 --force 也拒绝写入"""
        template, target, _, _ = cfg
        _write_template(template)
        target.write_text("{ 目标已损坏", encoding="utf-8")
        before = target.read_bytes()

        assert apply_mod.main(["--apply", "--force"]) == 1

        assert target.read_bytes() == before
        assert "目标文件 JSON 解析失败" in capsys.readouterr().out

    def test_backup_failure_aborts_write(self, cfg, capsys, monkeypatch):
        """备份失败 → 中止写入，原文件保持不变，退出码非零"""
        template, target, _, _ = cfg
        _write_template(template)
        _write_target(target, LOSSLESS_TARGET_KEYWORDS)
        before = target.read_bytes()

        def _boom(*args, **kwargs):
            raise OSError("磁盘已满（测试注入）")

        monkeypatch.setattr(apply_mod.shutil, "copy2", _boom)

        assert apply_mod.main(["--apply"]) == 1

        assert target.read_bytes() == before
        assert "备份失败，已中止写入" in capsys.readouterr().out
