"""恶意技能绕过下载阶段直接修改本地文件的防护单元测试

攻击前提：恶意技能已绕过「下载 → 安全扫描 → 评分」闭环（例如本地 zip
直接解压、或已被审核放行的社区技能包），直接获得调用本地存储 API
（SkillFileStore）的机会，试图通过路径穿越把文件写入技能仓库之外。

本测试验证 file_store 层（三层架构的物理落盘边界）是最后一道防线，
所有写入/读取入口都必须挡住路径穿越：

    攻击面                    入口                    预期拦截
    ─────────────────────────────────────────────────────────────
    skill_id 注入             create/delete            INVALID_SKILL_ID
    脚本名注入(创建)          create(scripts=)        INVALID_SCRIPT_NAME
    脚本名注入(追加)          add_script              INVALID_SCRIPT_NAME
    模板名注入(创建)          create(temp_files=)     静默跳过（不落盘）
    模板名注入(追加)          add_temp_file           INVALID_FILENAME
    模板名注入(读取)          get_temp_path           PATH_TRAVERSAL
    脚本越界读取              get_script_path         INVALID_SCRIPT_NAME
    仓库边界完整性            全攻击面串联            tmp_path 外零新增文件

设计要点：
    - 每个用例独立 tmp_path 隔离仓库，避免 create 校验失败后目录残留串扰
    - 断言同时覆盖「抛业务错误码」与「仓库外无文件落盘」两个维度
"""
import pytest

from agent.skills_mgmt.exceptions import (
    ErrorCode,
    SkillFileError,
    SkillValidationError,
)
from agent.skills_mgmt.file_store import SkillFileStore


@pytest.fixture
def store(tmp_path):
    """隔离仓库：SkillFileStore 直接落 tmp_path，绕过安装/审核流程"""
    return SkillFileStore(repo_path=str(tmp_path))


# ═══════════════════════════════════════════════════════════════════
#  1. skill_id 注入 — 攻击者试图把技能目录创建到仓库外
# ═══════════════════════════════════════════════════════════════════

class TestSkillIdInjection:
    """skill_id 必须是 kebab_case，路径穿越/绝对路径一律拒绝"""

    @pytest.mark.parametrize("evil_id", [
        "../evil",              # 相对穿越
        "../../outside",        # 多级穿越
        "/tmp/evil",            # 绝对路径（unix）
        "C:\\evil",             # 绝对路径（windows）
        "evil skill",           # 空格非法
        "EvilSkill",            # 大写非法（kebab_case 约束）
        ".hidden",              # 点开头非法
        "a..b",                 # 内嵌 ..
        "evil/../escape",       # 斜杠+穿越组合
    ], ids=lambda s: s.replace("/", "_").replace("\\", "_").replace("..", "dotdot"))
    def test_create_rejects_evil_skill_id(self, store, tmp_path, evil_id):
        with pytest.raises(SkillValidationError) as ei:
            store.create(skill_id=evil_id, meta={"name": "evil"})
        assert ei.value.code == ErrorCode.INVALID_SKILL_ID
        # 仓库内不残留任何技能目录
        assert not list(tmp_path.iterdir())

    def test_delete_rejects_evil_skill_id(self, store):
        with pytest.raises(SkillValidationError) as ei:
            store.delete("../evil")
        assert ei.value.code == ErrorCode.INVALID_SKILL_ID

    def test_legal_skill_id_resolves_inside_repo(self, store, tmp_path):
        """合法 id 的目录必须解析在仓库内（resolve + relative_to 边界）"""
        store.create("ok-skill", meta={"name": "ok"})
        skill_dir = (tmp_path / "ok-skill").resolve()
        skill_dir.relative_to(tmp_path.resolve())  # 不抛 ValueError 即通过


# ═══════════════════════════════════════════════════════════════════
#  2. 脚本名注入 — 攻击者试图把 .py 写入仓库外目录
# ═══════════════════════════════════════════════════════════════════

class TestScriptNameInjection:
    """scripts/ 脚本名必须匹配 ^[a-zA-Z_][a-zA-Z0-9_]*\.py$"""

    @pytest.mark.parametrize("evil_script", [
        "../evil.py",           # 相对穿越
        "../../../tmp/evil.py", # 多级穿越
        "..\\evil.py",          # windows 分隔符
        "/tmp/evil.py",         # 绝对路径
        "evil.sh",              # 非 .py 扩展名
        "evil",                 # 无扩展名
        "a/b.py",               # 目录分隔符
        ".py",                  # 空文件基名
    ], ids=lambda s: s.replace("/", "_").replace("\\", "_").replace("..", "dotdot"))
    def test_create_scripts_rejects_evil_name(self, store, tmp_path, evil_script):
        with pytest.raises(SkillValidationError) as ei:
            store.create("good-skill", meta={"name": "g"},
                         scripts={evil_script: "evil code"})
        assert ei.value.code == ErrorCode.INVALID_SCRIPT_NAME
        # 仓库外与仓库内均不得落盘该脚本
        assert not (tmp_path.parent / "evil.py").exists()
        assert not (tmp_path / "good-skill" / "scripts" / "evil.py").exists()

    def test_add_script_rejects_evil_name(self, store, tmp_path):
        store.create("good-skill", meta={"name": "g"})
        with pytest.raises(SkillValidationError) as ei:
            store.add_script("good-skill", "../../evil.py", "evil code")
        assert ei.value.code == ErrorCode.INVALID_SCRIPT_NAME
        assert not (tmp_path.parent / "evil.py").exists()

    def test_get_script_path_rejects_traversal(self, store):
        """越界脚本名在读取侧同样被校验层拦截"""
        store.create("good-skill", meta={"name": "g"})
        with pytest.raises(SkillValidationError) as ei:
            store.get_script_path("good-skill", "../../etc/passwd")
        assert ei.value.code == ErrorCode.INVALID_SCRIPT_NAME

    def test_get_script_path_legal_name_missing_raises_not_found(self, store):
        """合法名但脚本不存在 → SkillNotFoundError（非穿越，边界正确）"""
        store.create("good-skill", meta={"name": "g"})
        from agent.skills_mgmt.exceptions import SkillNotFoundError
        with pytest.raises(SkillNotFoundError):
            store.get_script_path("good-skill", "missing.py")


# ═══════════════════════════════════════════════════════════════════
#  3. 模板名注入 — 攻击者试图把二进制模板写入仓库外
# ═══════════════════════════════════════════════════════════════════

class TestTempFileNameInjection:
    """temp/ 模板文件名拒绝 /、\、..（与 get_temp_path 同一套检查）"""

    def test_create_temp_files_silently_skips_evil_name(self, store, tmp_path):
        """create 写 temp_files：非法名静默跳过（不抛错、不落盘），合法名正常写入"""
        store.create("good-skill", meta={"name": "g"},
                     temp_files={"../evil.bin": b"payload", "ok.txt": b"ok"})
        assert (tmp_path / "good-skill" / "temp" / "ok.txt").exists()
        assert not (tmp_path / "good-skill" / "temp" / "evil.bin").exists()
        assert not (tmp_path.parent / "evil.bin").exists()

    @pytest.mark.parametrize("evil_name", [
        "../evil.bin",
        "a/b.bin",
        "a\\b.bin",
        "..",
        "a/../b.bin",
    ])
    def test_add_temp_file_rejects_evil_name(self, store, tmp_path, evil_name):
        store.create("good-skill", meta={"name": "g"})
        with pytest.raises(SkillValidationError) as ei:
            store.add_temp_file("good-skill", evil_name, b"payload")
        assert ei.value.code == ErrorCode.INVALID_FILENAME
        assert not (tmp_path.parent / "evil.bin").exists()

    @pytest.mark.parametrize("evil_name", [
        "../outside.txt",
        "a/b.txt",
        "a\\b.txt",
        "..",
        "a/../b",
    ])
    def test_get_temp_path_rejects_traversal(self, store, evil_name):
        store.create("good-skill", meta={"name": "g"})
        with pytest.raises(SkillFileError) as ei:
            store.get_temp_path("good-skill", evil_name)
        assert ei.value.code == ErrorCode.PATH_TRAVERSAL


# ═══════════════════════════════════════════════════════════════════
#  4. 仓库边界完整性 — 全攻击面串联后仓库外零新增文件
# ═══════════════════════════════════════════════════════════════════

class TestRepoBoundaryIntegrity:
    """模拟恶意技能绕过下载阶段后的连番攻击，验证仓库外文件系统未被触碰"""

    def test_attacks_leave_no_files_outside_repo(self, tmp_path):
        # 只追踪仓库边界外（tmp_path 之外）的文件，仓库内的合法技能目录不算新增
        outside = tmp_path.parent
        repo_prefix = str(tmp_path.resolve())

        def _outside_paths():
            return {p for p in outside.rglob("*")
                    if not str(p).startswith(repo_prefix)}

        before = _outside_paths()

        store = SkillFileStore(repo_path=str(tmp_path))

        # ① skill_id 注入
        for evil_id in ("../evil", "x/../../evil"):
            with pytest.raises(SkillValidationError):
                store.create(evil_id, meta={})

        # ② 脚本注入（创建 + 追加）
        store.create("good-skill", meta={"name": "g"})
        with pytest.raises(SkillValidationError):
            store.create("good-skill-2", meta={"name": "g2"},
                         scripts={"../evil.py": "evil"})
        for evil_script in ("../../evil.py", "..\\evil.py"):
            with pytest.raises(SkillValidationError):
                store.add_script("good-skill", evil_script, "evil")

        # ③ 模板注入（追加被拒、创建被静默跳过）
        with pytest.raises(SkillValidationError):
            store.add_temp_file("good-skill", "../evil.bin", b"evil")
        store.create("good-skill-3", meta={"name": "g3"},
                     temp_files={"../evil.bin": b"evil"})

        # ④ 越界读取（只读，不应产生文件）
        for evil_name in ("../outside.txt", ".."):
            with pytest.raises((SkillFileError, SkillValidationError)):
                store.get_temp_path("good-skill", evil_name)
        with pytest.raises(SkillValidationError):
            store.get_script_path("good-skill", "../../evil.py")

        after = _outside_paths()
        assert after == before, "仓库边界外产生了新的文件/目录，路径穿越防护失效"
