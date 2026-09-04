"""技能自动分类引擎（分类/折叠/新技能自动建类）单元测试

覆盖用户要求：
- 规则打分：常见技能自动归入种子类（交流人格/记忆知识/语音/邮件/代码/文档…）
- 零命中但名称给出新概念 → 自动创建新类（冥想引导器 → 新类"冥想引导器"）
- 通用/测试垃圾（mock_skill_*）不建类、落"未分类"
- 注册表持久化：首次判定落盘、重复 resolve 幂等、人工移动不被自动重判覆盖、
  已有分类不因内容暂时不足而降级
- 服务集成：创建/安装自动归类（不改 draft 状态）；classes/run-auto/move 契约
"""
import pytest

from agent.skills_mgmt import SkillsMgmtService
from agent.skills_mgmt.categorizer import (
    SEED_NAMES, UNCLASSIFIED, SkillClassRegistry, classify_fields,
)
from agent.skills_mgmt.exceptions import SkillMgmtError


@pytest.fixture
def svc(tmp_path):
    return SkillsMgmtService(
        store_path=str(tmp_path / "store.json"),
        class_registry_path=str(tmp_path / "classes.json"))


@pytest.fixture
def reg(tmp_path):
    return SkillClassRegistry(path=str(tmp_path / "classes.json"))


def _data(name, desc="", content="# 内容", content_type="markdown", **kw):
    data = dict(
        id=name, name=name, description=desc, content=content,
        content_type=content_type, category="custom",
        tags=["t"], author="tester", enabled=False,
    )
    data.update(kw)
    return data


# ═══════════════════════════════════════════════════════════════
#  规则打分（纯函数）
# ═══════════════════════════════════════════════════════════════

class TestClassifyFields:
    def test_common_domains_map_to_seed_classes(self):
        assert classify_fields("自省反思", "每次交互后反思自身状态", "")["class"] == "交流与人格"
        assert classify_fields("语音交互", "通过语音与用户交互", "")["class"] == "语音与多媒体"
        assert classify_fields("记忆摘要", "定期压缩对话为摘要", "")["class"] == "记忆与知识"
        assert classify_fields("email-helper", "邮件处理助手", "")["class"] == "邮件与通讯"
        assert classify_fields("代码审查", "检查 python 代码质量", "")["class"] == "代码与工程"
        assert classify_fields("resume-craft", "生成简历初稿", "")["class"] == "文档与办公"

    def test_junk_no_keyword_falls_to_unclassified(self):
        v = classify_fields("mock_skill_b", "Mock Skill", "# mock\nprint(1)")
        assert v["class"] is None and v["auto_name"] is None

    def test_novel_name_auto_creates_class(self):
        v = classify_fields("冥想引导器", "专注放松的呼吸引导", "")
        assert v["class"] is None
        assert v["auto_name"] == "冥想引导器"  # 名称里没有种子关键词 → 建议新类

    def test_seed_cover_does_not_create_duplicate(self):
        # 名称含种子关键词（语音）→ 不会走 auto 新建
        v = classify_fields("语音提醒", "定时语音播报", "")
        assert v["auto_name"] is None
        assert v["class"] == "语音与多媒体"

    def test_english_auto_name_via_topic_map(self):
        # meditation 无种子命中 → 名称 token 经 TOPIC_NAMES 映射为中文类
        v = classify_fields("meditation-guide", "relax breath focus", "")
        assert v["auto_name"] == "冥想"

    def test_content_only_keyword_too_weak(self):
        v = classify_fields("some-tool", "", "内容里偶然提到一次 email 处理")
        assert v["score"] < 2  # 不足阈值
        assert v["class"] is None or v["auto_name"] != UNCLASSIFIED


# ═══════════════════════════════════════════════════════════════
#  注册表（落盘/幂等/人工覆盖/不降级）
# ═══════════════════════════════════════════════════════════════

class TestRegistry:
    def test_resolve_persists_and_is_idempotent(self, reg):
        c1 = reg.resolve("asset:a", name="代码助手", description="写 python 代码", content="")
        assert c1 == "代码与工程"
        c2 = reg.resolve("asset:a", name="代码助手", description="写 python 代码", content="")
        assert c2 == c1
        st = reg.snapshot()
        assert st["assignments"]["asset:a"] == "代码与工程"
        assert "asset:a" not in st.get("manual", [])

    def test_unclassified_resolve_persists(self, reg):
        cls = reg.resolve("rt:mock_skill_b", name="mock_skill_b",
                          description="Mock Skill", content="print(1)")
        assert cls == UNCLASSIFIED
        assert reg.snapshot()["assignments"]["rt:mock_skill_b"] == UNCLASSIFIED

    def test_manual_move_survives_auto_reresolve(self, reg):
        reg.resolve("asset:x", name="邮件帮手", description="处理邮件", content="")
        assert reg.assignment("asset:x") == "邮件与通讯"
        reg.assign("asset:x", "代码与工程")  # 人工移动
        # 即使内容回到邮件主题，人工选择保留
        again = reg.resolve("asset:x", name="邮件帮手", description="处理邮件", content="")
        assert again == "代码与工程"
        assert "asset:x" in reg.snapshot().get("manual", [])

    def test_no_downgrade_on_content_change(self, reg):
        reg.resolve("asset:y", name="某技能", description="", content="import requests 抓取网页")
        assert reg.assignment("asset:y") == "网络与搜索"
        # 内容暂时不足也不踢回未分类
        stayed = reg.resolve("asset:y", name="某技能", description="", content="")
        assert stayed == "网络与搜索"

    def test_auto_class_created_and_counted(self, reg):
        cls = reg.resolve("asset:z", name="冥想引导器", description="专注放松", content="")
        assert cls == "冥想引导器"
        st = reg.snapshot()
        assert "冥想引导器" in st["auto_classes"]

    def test_run_auto_only_fills_gaps_and_keeps_manual(self, reg):
        reg.resolve("asset:a", name="代码助手", description="python", content="")
        reg.assign("asset:b", "未分类")  # 人工钉住
        skills = [
            {"id": "a", "name": "代码助手", "description": "python", "content": ""},
            {"id": "b", "name": "xx", "description": "", "content": ""},
            {"id": "c", "name": "语音播报", "description": "通过语音朗读", "content": ""},
        ]
        out = reg.run_auto(skills, ns="asset")
        assert out["processed"] == 3
        assert out["classified"] == 1  # 仅 c 需要归类
        assert reg.assignment("asset:b") == UNCLASSIFIED
        assert reg.assignment("asset:c") == "语音与多媒体"

    def test_run_auto_force_reclassifies_unclassified_keeps_manual(self, reg):
        # 自动判定落「未分类」的存量，在 force 下可被重新归类
        reg.resolve("asset:x", name="xx", description="", content="print(1)")
        assert reg.assignment("asset:x") == UNCLASSIFIED
        reg.assign("asset:y", "未分类")  # 人工钉住未分类
        skills = [
            {"id": "x", "name": "语音播报", "description": "通过语音朗读", "content": ""},
            {"id": "y", "name": "yy", "description": "", "content": ""},
        ]
        out = reg.run_auto(skills, ns="asset", force_unclassified=True)
        assert out["classified"] == 1
        assert reg.assignment("asset:x") == "语音与多媒体"  # 自动未分类被重判
        assert reg.assignment("asset:y") == UNCLASSIFIED    # 人工保留
        # 非 force 时不再重判
        out2 = reg.run_auto(skills, ns="asset")
        assert out2["classified"] == 0

    def test_group_summary(self, reg):
        reg.resolve("asset:a", name="语音助手", description="语音交互", content="")
        reg.resolve("asset:b", name="mock", description="Mock", content="print(1)")
        skills = [{"id": "a", "name": "语音助手"}, {"id": "b", "name": "mock"}]
        out = reg.group_summary(skills, ns="asset")
        names = {g["name"] for g in out["groups"]}
        assert names == {"语音与多媒体", UNCLASSIFIED}
        v = next(g for g in out["groups"] if g["name"] == "语音与多媒体")
        assert v["count"] == 1 and v["auto"] is False
        assert out["total"] == 2

    def test_mirror_asset_to_runtime_when_not_manual(self, reg):
        reg.resolve("asset:x", name="语音助手", description="语音交互", content="")
        reg.resolve("rt:x", name="邮件工具", description="处理邮件", content="")
        assert reg.assignment("rt:x") == "邮件与通讯"
        assert reg.mirror("asset:x", "rt:x") is True
        assert reg.assignment("rt:x") == "语音与多媒体"  # 资产侧胜出（自动跟随）

    def test_runtime_weak_verdict_falls_back_to_asset_class(self, reg):
        reg.resolve("asset:y", name="易之三义", description="Yi-Jing Coding Agent",
                    content="coding agent")
        assert reg.assignment("asset:y") == "代码与工程"
        # 运行时行缺正文/描述 → 弱判定未分类 → 回退资产分类
        cls = reg.resolve("rt:y", name="易之三义", description="", content="")
        assert cls == "代码与工程"
        assert reg.assignment("rt:y") == "代码与工程"
        # 运行时人工钉住的不回退
        reg.assign("rt:y", "未分类")
        assert reg.resolve("rt:y", name="易之三义", description="", content="") == "未分类"

    def test_mirror_keeps_runtime_manual(self, reg):
        reg.resolve("asset:x", name="语音助手", description="语音交互", content="")
        reg.assign("rt:x", "翻译与写作")  # 人工钉住运行时
        assert reg.mirror("asset:x", "rt:x") is False
        assert reg.assignment("rt:x") == "翻译与写作"

    def test_auto_class_names_reports_created(self, reg):
        reg.resolve("asset:z", name="冥想引导器", description="专注放松", content="")
        assert "冥想引导器" in reg.auto_class_names()
        assert "语音与多媒体" not in reg.auto_class_names()  # 种子类不算自动建类


# ═══════════════════════════════════════════════════════════════
#  服务集成（创建/安装自动归类 + 路由契约方法）
# ═══════════════════════════════════════════════════════════════

class TestServiceIntegration:
    def test_create_manual_auto_classifies_without_touching_status(self, svc):
        skill = svc.create_manual(_data("svc-mail", "邮件处理助手", content="# 起草与整理邮件"))
        assert svc.get(skill.id).status == "draft"  # 守原契约
        assert svc._class_registry.assignment(f"asset:{skill.id}") == "邮件与通讯"

    def test_install_auto_classifies(self, svc, tmp_path):
        # install 需要可解析源；用 create_manual 等价路径不可行 → 直接注入 creator
        from agent.skills_mgmt.models import Skill as SK
        svc.store.upsert(SK.from_storage_dict(
            _data("ext-voice", "语音技能", content="通过语音朗读文本")))
        svc._auto_classify("ext-voice")
        assert svc._class_registry.assignment("asset:ext-voice") == "语音与多媒体"

    def test_skill_classes_view(self, svc):
        svc.create_manual(_data("v1", "语音助手", content="语音交互"))
        svc.create_manual(_data("v2", description="Mock Skill", content="# mock"))
        view = svc.skill_classes()
        assert view["total"] == 2
        groups = {g["name"]: g for g in view["groups"]}
        assert "语音与多媒体" in groups
        assert groups["语音与多媒体"]["count"] == 1
        assert groups[UNCLASSIFIED]["count"] == 1
        # 组内技能带 id/status/enabled 便于前端行渲染
        assert groups["语音与多媒体"]["skills"][0]["id"] == "v1"

    def test_move_class_manual_and_invalid(self, svc):
        svc.create_manual(_data("m1", "语音助手", content="语音"))
        out = svc.move_class("m1", "代码与工程")
        assert out["class_name"] == "代码与工程"
        assert svc._class_registry.assignment("asset:m1") == "代码与工程"
        # 之后内容更新/自动重判不再改
        svc.update("m1", {"description": "处理邮件的助手"})
        assert svc._class_registry.assignment("asset:m1") == "代码与工程"
        with pytest.raises(SkillMgmtError):
            svc.move_class("m1", "不存在之分类")

    def test_run_auto_classify(self, svc):
        svc.create_manual(_data("a1", "语音助手", content="语音"))
        svc.create_manual(_data("a2", description="Mock", content="# mock"))
        svc.move_class("a2", "未分类")
        out = svc.run_auto_classify()
        assert out["processed"] == 2
        assert out["classified"] == 0  # 都已归类（人工钉住的跳过）
        assert "created_classes" in out

    def test_seed_names_exported(self):
        assert "交流与人格" in SEED_NAMES
        assert UNCLASSIFIED == "未分类"
