"""BOM 编码契约与 pre-commit hook 配置回归测试用例

背景(2026-08-05 BOM 事故复盘): .ps1/.psm1 被叠加 UTF-8 BOM(EF BB BF x2)
破坏 PS 5.1 块注释解析, 导致 hook 加载失败与 `Missing expression after
unary operator` 错误。本次将修复后的拦截逻辑与 hook 配置打包为独立回归
用例, 供后续重构一键验证(不易护城河):

  1. BOM 契约基础函数   count_leading_bom / is_utf8 / hex_head
  2. 检测判定规则       check_ps1_encoding.py 的 BLOCK/WARN 分级语义
  3. 修复公式           fix_ps_bom.py 去叠加 BOM / 补 BOM
  4. hook 模板完整性    hook_fail_safe.psm1 run_check 四段拦截链 + 合并编码段 + pre-push
  5. 稳定性测试契约     verify_bom_hook_stability.py 归因标记 / 文件构造 / 归因判定
  6. 端到端回归         真实子进程检出叠加 BOM → exit 1; detect_direct 归因

运行: pytest tests/unit/test_bom_encoding_hook.py -v
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import check_ps1_encoding as enc  # noqa: E402
import fix_ps_bom as fix  # noqa: E402
import verify_bom_hook_stability as vhs  # noqa: E402

BOM = b"\xef\xbb\xbf"
HOOK_PSM1 = SCRIPTS_DIR / "dev" / "hook_fail_safe.psm1"

# hook 模板必须包含的拦截链段标记与跳过开关(与 hook_fail_safe.psm1 契约一致)
# 2026-08-05 P0: ENCODING_CHECK 已合并原 BOMFIX 段(单一编码契约检查),
# BOMFIX=/SKIP_BOM_FIX_CHECK 不再存在于模板, 由合并守卫测试保证不复活。
HOOK_SEGMENTS = (
    "ENCODING_CHECK=", "CI_GUARD=", "INVARIANT=", "WORKFLOW_SIM=",
)
HOOK_SKIP_SWITCHES = (
    "SKIP_ENCODING_CHECK", "SKIP_CI_GUARD",
    "SKIP_INVARIANT", "SKIP_WORKFLOW_SIM",
)


# ──────────────────────────────────────────────────────────────
# 1. BOM 契约基础函数
# ──────────────────────────────────────────────────────────────

def test_bom_constant_contract():
    """两个检查脚本的 BOM 常量必须一致(契约同源)。"""
    assert enc.BOM == b"\xef\xbb\xbf"
    assert fix.BOM == b"\xef\xbb\xbf"


def test_count_leading_bom_zero():
    assert enc.count_leading_bom(b"Write-Output 'x'\n") == 0


def test_count_leading_bom_one():
    assert enc.count_leading_bom(BOM + b"Write-Output 'x'\n") == 1


def test_count_leading_bom_stacked():
    """叠加 BOM x3 必须计为 3(事故场景 EF BB BF 连续出现)。"""
    assert enc.count_leading_bom(BOM + BOM + BOM + b"x") == 3


def test_is_utf8_valid_and_invalid():
    assert enc.is_utf8("中文内容".encode("utf-8"))
    assert not enc.is_utf8(b"\xff\xfe\x00invalid")


def test_hex_head_format():
    assert enc.hex_head(BOM + b"A") == "EF BB BF 41"


# ──────────────────────────────────────────────────────────────
# 2. 检测判定规则(check_ps1_encoding 业务语义)
# ──────────────────────────────────────────────────────────────

def test_stacked_bom_is_utf8_but_block():
    """叠加 BOM 仍是合法 UTF-8, 必须靠 n_bom>1 判定拦截(非解码错误)。"""
    data = BOM + BOM + b"Write-Output 'x'\n"
    assert enc.is_utf8(data)          # 解码层不报错
    assert enc.count_leading_bom(data) > 1  # 契约层必须拦截


def test_invalid_utf8_is_block():
    assert not enc.is_utf8(b"\xff\xfe\x00" + BOM)


def test_require_bom_default_contract():
    """关键契约文件清单在两个脚本中必须一致(单一事实源)。"""
    assert "scripts/dev/hook_fail_safe.psm1" in enc.REQUIRE_BOM_DEFAULT
    assert enc.REQUIRE_BOM_DEFAULT == fix.REQUIRE_BOM_DEFAULT


def test_iter_ps_files_scans_scripts_and_packages(tmp_path):
    """仅扫描 .ps1/.psm1, 忽略其他后缀。"""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "packages").mkdir()
    (tmp_path / "scripts" / "a.ps1").write_bytes(BOM + b"Write-Output 'a'\n")
    (tmp_path / "packages" / "b.psm1").write_bytes(BOM + b"function B {}\n")
    (tmp_path / "scripts" / "c.txt").write_text("not ps")
    files = list(enc.iter_ps_files(tmp_path))
    assert {f.name for f in files} == {"a.ps1", "b.psm1"}


# ──────────────────────────────────────────────────────────────
# 3. 修复公式(fix_ps_bom 逻辑)
# ──────────────────────────────────────────────────────────────

def test_fix_dedupe_stacked_bom(tmp_path):
    """去叠加: 保留恰好 1 个 BOM, 内容无损。"""
    p = tmp_path / "bad.ps1"
    payload = b"Write-Output 'x'\n"
    p.write_bytes(BOM + BOM + BOM + payload)
    n = fix.count_leading_bom(p.read_bytes())
    repaired = fix.BOM + p.read_bytes()[n * 3:]  # 复现 fix_ps_bom --apply 分支
    assert fix.count_leading_bom(repaired) == 1
    assert repaired[3:] == payload


def test_fix_fill_missing_bom(tmp_path):
    """补 BOM: 关键契约文件缺 BOM 时前置 BOM。"""
    p = tmp_path / "contract.psm1"
    payload = b"function Get-X {}\n"
    p.write_bytes(payload)
    repaired = fix.BOM + p.read_bytes()  # 复现 补 BOM 分支
    p.write_bytes(repaired)
    assert p.read_bytes().startswith(BOM)
    assert p.read_bytes()[3:] == payload


# ──────────────────────────────────────────────────────────────
# 4. hook 模板完整性(hook_fail_safe.psm1 run_check 四段拦截链)
# ──────────────────────────────────────────────────────────────

def _hook_source() -> str:
    if not HOOK_PSM1.exists():
        pytest.skip("scripts/dev/hook_fail_safe.psm1 不存在")
    return HOOK_PSM1.read_text(encoding="utf-8-sig")


def test_hook_template_has_all_segments():
    src = _hook_source()
    for seg in HOOK_SEGMENTS:
        assert seg in src, f"hook 模板缺失段标记: {seg}"


def test_hook_template_has_all_skip_switches():
    src = _hook_source()
    for sw in HOOK_SKIP_SWITCHES:
        assert sw in src, f"hook 模板缺失跳过开关: {sw}"


def test_hook_template_prepush_invariant():
    """pre-push 必须含 INVARIANT 段(推送前不变量校验)。"""
    src = _hook_source()
    assert "pre-push" in src
    assert "INVARIANT=" in src


def test_hook_template_encoding_command():
    """ENCODING_CHECK 段必须调用 check_ps1_encoding.py(与 hook 部署一致)。"""
    src = _hook_source()
    assert "check_ps1_encoding.py" in src
    assert "--repo-root" in src


def test_hook_template_encoding_merged_no_bomfix():
    """P0 合并守卫: BOMFIX 独立段必须彻底移除, 编码检查统一走 run_check。"""
    src = _hook_source()
    assert "BOMFIX=" not in src, "BOMFIX 段应已合并进 ENCODING_CHECK"
    assert "SKIP_BOM_FIX_CHECK" not in src, "SKIP_BOM_FIX_CHECK 开关应已移除"
    assert "run_check" in src, "四段检查必须由 run_check 函数封装"


# ──────────────────────────────────────────────────────────────
# 5. verify_bom_hook_stability 契约
# ──────────────────────────────────────────────────────────────

def test_stability_block_markers_cover_attribution():
    """归因标记必须覆盖合并后 ENCODING_CHECK 段的拦截文案。"""
    for marker in ("编码检查(UTF-8 BOM 契约) 未通过", "叠加 BOM", vhs.TEMP_PREFIX):
        assert marker in vhs.BOM_BLOCK_MARKERS


def test_write_stacked_bom_file_produces_double_bom(tmp_path):
    """稳定性测试文件构造器必须产出叠加 BOM x2(事故复现)。"""
    p = tmp_path / (vhs.TEMP_PREFIX + "case.ps1")
    vhs.write_stacked_bom_file(p)
    assert enc.count_leading_bom(p.read_bytes()) == 2


def test_analyze_commit_detects_encoding_block():
    """提交被 ENCODING_CHECK 段拦截 → 归因成功。"""
    proc = _fake_proc(1, "", "[BLOCK] scripts/x.ps1: 叠加 BOM x2 (head: EF BB BF)")
    ok, reason = vhs.analyze_commit(proc, "both")
    assert ok is True
    assert "叠加 BOM" in reason


def test_analyze_commit_detects_merged_encoding_block():
    """提交被合并后 ENCODING_CHECK 段拦截 → 归因成功(标记按 BOM_BLOCK_MARKERS 顺序取首个)。"""
    proc = _fake_proc(1, "[pre-commit][ERROR] 编码检查(UTF-8 BOM 契约) 未通过, 提交被阻止", "")
    ok, reason = vhs.analyze_commit(proc, "both")
    assert ok is True
    assert reason.startswith("归因标记: ")
    assert "编码检查" in reason


def test_analyze_commit_unattributed_is_false():
    """无归因标记的失败 → 不判为 BOM 拦截(避免误报)。"""
    proc = _fake_proc(1, "", "docs 链接预检失败")
    ok, reason = vhs.analyze_commit(proc, "both")
    assert ok is False


# ──────────────────────────────────────────────────────────────
# 6. 端到端回归(真实子进程)
# ──────────────────────────────────────────────────────────────

def test_end_to_end_check_stacked_bom_exit_1(tmp_path):
    """真实运行 check_ps1_encoding.py: 叠加 BOM → exit 1(BLOCK)。"""
    (tmp_path / "scripts").mkdir()
    bad = tmp_path / "scripts" / "bad.ps1"
    bad.write_bytes(BOM + BOM + b"Write-Output 'x'\n")
    r = _run_check(tmp_path)
    assert r.returncode == 1
    # 修复为单 BOM 后 → exit 0
    bad.write_bytes(BOM + b"Write-Output 'x'\n")
    assert _run_check(tmp_path).returncode == 0


def test_end_to_end_detect_direct_attribute(tmp_path):
    """detect_direct: 叠加 BOM 文件必须被双检查脚本同时归因检出。"""
    (tmp_path / "scripts").mkdir()
    bad = tmp_path / "scripts" / "bad.ps1"
    bad.write_bytes(BOM + BOM + b"Write-Output 'x'\n")
    ok, detail = vhs.detect_direct(tmp_path, "bad.ps1")
    assert ok is True
    assert "check_ps1_encoding" in detail


def _run_check(repo_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "check_ps1_encoding.py"),
         "--quiet", "--repo-root", str(repo_root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _fake_proc(rc: int, stdout: str, stderr: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)
