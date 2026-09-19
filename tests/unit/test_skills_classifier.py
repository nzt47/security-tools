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
    DOMAIN_ANCHORS, GENERIC_PROBE_WORDS, RETIRED_GENERIC_KEYWORDS, SEED_CLASSES,
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

    def test_coding_skill_not_misclassified_by_generic_words(self):
        """回归：编码方法论技能「易之三义」曾被判成「语音与多媒体」

        【为什么只喂 名称+描述】技能页那行的数据来自 `/api/skills` 的 `installed`
        （只有 `id/name/description/params`，**没有正文**）⇒ 运行时判定实际只用名称+描述。
        该描述里只有两处命中：「约束**识别**」（旧表把它算作语音域关键词）与
        `<san_yi_analysis>`（data 域的 `analysis`），各 2 分并列 ⇒ 旧表按表序把
        「语音与多媒体」判给了一个编码技能。与
        `test_ui_skill_not_misclassified_by_doc_noise` 同一类：**噪音/通用词不得独立决定域**。
        """
        verdict = classify_fields(
            name="易之三义",
            description="1. 编码前必输出 `<san_yi_analysis>`: [不易]约束识别 → "
                        "[变易]扩展性评估 → [简易]最简方案确认。\n"
                        "2. 原子推理，每步经三义校验。\n"
                        "3. 三义冲突时显式说明权衡取舍。\n"
                        "4. 生成后自检，违三义则修正再输出。")
        assert verdict["class"] == "代码与工程"

    def test_bare_identify_word_does_not_claim_voice_domain(self):
        """裸「识别」不再单独决定语音域；真正的语音语义仍能命中

        中文里"识别不变量/识别风险/识别需求"随处可见，把它当语音专属关键词会让
        任何提到"识别"的技能都往语音域跑（实测命中即易之三义）。移除后改由
        「语音识别」及 voice/audio/ocr 等复合词/英文词承担。
        """
        assert classify_fields("需求梳理", "识别用户真实需求并归类", "")["class"] != "语音与多媒体"
        assert classify_fields("语音交互", "通过语音与用户交互", "")["class"] == "语音与多媒体"
        assert classify_fields("语音识别", "把录音转成文字", "")["class"] == "语音与多媒体"

    def test_easy_three_meanings_beats_analysis_tag_noise(self):
        """回归：`<san_yi_analysis>` 这个**标签名**不得再给「数据分析与可视化」送分

        【为什么单列一条】上面那条测试只证明"没被判成语音域"，**不能**证明判得稳：
        2026-09-19 的修复补了「编码」后，该技能运行时行一度是
        「代码与工程 2 : 数据分析与可视化 2」的**平局**，靠 `SEED_CLASSES` 表序才判对
        —— 即"修好了"其实是 0 分差，移除「编码」立刻翻回数据域（审计脚本把它标为单点依赖）。
        根因：`_ASCII_TOKEN` 把 `<san_yi_analysis>` 切成 san/yi/analysis，
        让 `analysis` 在**标识符内部**命中。`_token_count` 收紧后平局消失。
        """
        desc = ("1. 编码前必输出 `<san_yi_analysis>`: [不易]约束识别 → "
                "[变易]扩展性评估 → [简易]最简方案确认。\n"
                "2. 原子推理，每步经三义校验。\n"
                "3. 三义冲突时显式说明权衡取舍。\n"
                "4. 生成后自检，违三义则修正再输出。")
        v = classify_fields(name="易之三义", description=desc)
        assert v["class"] == "代码与工程"
        # 必须是**严格**胜出，不是平局（平局=表序决定=随时可能翻案）
        assert v["score"] >= 2
        assert "数据分析与可视化" not in v["matched"], \
            f"标签名 <san_yi_analysis> 不应贡献数据域分数：{v['matched']}"

    def test_ascii_keyword_does_not_match_inside_identifier(self):
        """【结构性】英文关键词不得在 snake_case 复合标识符内部命中

        实测两处误报：`from_knowledge`（技能 provenance 标签）让多条技能白拿
        「记忆与知识」2 分；`<san_yi_analysis>` 让「易之三义」白拿数据域 2 分。
        """
        # 独立成词 → 命中
        assert classify_fields("x", "", "we need knowledge here")["matched"] \
            == ["记忆与知识"]
        # 作为更长标识符的一段 → 不命中
        assert classify_fields("x", "", "tags: from_knowledge distilled")["matched"] == []
        assert classify_fields("x", "", "输出 <san_yi_analysis> 标签")["matched"] == []

    def test_writing_skills_not_dragged_into_office_domain(self):
        """回归：`writing-skills` 不得被通用词「文档」拖进「文档与办公」

        【用户在问的第二个"为什么"】该技能描述里"流程文档编写"+"SKILL.md 文档"两处命中
        裸「文档」⇒ 3 分，与名称里的 `writing`(3 分) 打平，靠表序判给「文档与办公」。
        裸「文档/报告/文件/整理」已移除（通用词）。
        【定案】台账侧该技能是**人工钉住**的（`manual` 含 asset/rt 两键，指向「代码与工程」）：
        它是把 TDD（RED-GREEN-REFACTOR）应用于 SKILL.md 编写的方法论，tags 全部是
        「创建/编辑/验证技能」。本条断言的是**规则本身**不再把它判成「文档与办公」
        —— 即根因已消除，而不是靠人工钉住遮住。
        """
        desc = ("适用于创建、编辑或验证 agent 技能（SKILL.md）之前或过程中，"
                "核心是将 TDD 应用于流程文档编写。预期产出: 一份经 RED-GREEN-REFACTOR "
                "验证、无已知漏洞且可被其他 agent 正确触发和使用的 SKILL.md 文档。"
                "由 1 份素材蒸馏生成")
        tags = ["创建技能", "external", "验证技能", "编辑技能", "from_knowledge", "distilled"]
        v = classify_fields(name="writing-skills", description=desc,
                            content="# writing-skills\n" + desc, tags=tags)
        assert v["class"] != "文档与办公", \
            f"写作方法论技能不得被通用词「文档」夺进办公域：{v}"
        # 兜底再断言一次纯判定路径（asset 侧正文同样不带「文档」噪音）
        assert classify_fields(name="writing-skills", description=desc)["class"] != "文档与办公"


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

    def test_same_skill_auto_alignment_rt_authoritative(self, reg):
        # 同名双生态：rt(名称/描述=意图)后落盘时，asset 的自动归类自动对齐 rt，
        # 资产库与技能面板两个视图不再分叉
        reg.resolve("asset:x", name="语音助手", description="语音交互", content="")
        assert reg.assignment("asset:x") == "语音与多媒体"
        reg.resolve("rt:x", name="邮件工具", description="处理邮件", content="")
        assert reg.assignment("rt:x") == "邮件与通讯"
        assert reg.assignment("asset:x") == "邮件与通讯"   # 自动对齐 rt（未人工移动）
        assert reg.mirror("asset:x", "rt:x") is False      # 已一致，无需再镜像

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

    def test_ui_skill_not_misclassified_by_doc_noise(self):
        # 回归：self-explanatory-ui 的正文/标签含「外部文档」「markdown」等噪音，
        # 不应把 UI/前端开发技能误归「文档与办公」；且 markdown 不再作为该种子关键词
        verdict = classify_fields(
            name="self-explanatory-ui",
            description="进行界面设计或前端 UI 开发时使用。将功能说明与帮助信息"
                        "直接集成到可视化界面中",
            tags=["external", "imported", "markdown"],
            content="# 自解释 UI 设计规范\n确保 UI 开发遵循该规范，用户无需查阅"
                    "外部文档，仅凭界面展示即可操作；生成前端 UI 组件或页面代码时使用。")
        assert verdict["class"] == "代码与工程"

    def test_late_asset_resolve_stays_aligned_with_rt(self, reg):
        # rt 先归类（意图：代码与工程），asset 后解析（正文带文档噪音）→ 仍以 rt 为准，两侧一致
        reg.resolve("rt:z", name="代码审查助手", description="重构与代码审查",
                    content="")
        assert reg.assignment("rt:z") == "代码与工程"
        cls = reg.resolve("asset:z", name="代码审查助手",
                          description="重构与代码审查",
                          content="输出代码审查报告文档与笔记", tags=["markdown"])
        assert cls == "代码与工程"          # 不再被正文噪音带偏
        assert reg.assignment("asset:z") == reg.assignment("rt:z")

    def test_auto_class_names_reports_created(self, reg):
        reg.resolve("asset:z", name="冥想引导器", description="专注放松", content="")
        assert "冥想引导器" in reg.auto_class_names()
        assert "语音与多媒体" not in reg.auto_class_names()  # 种子类不算自动建类

    # ── 人工指定一致性（TASK-02 修复 2）──────────────────────────────
    def test_assign_pins_both_namespaces(self, reg):
        """人工移动必须**同时钉住 asset: 与 rt:**，否则运行时视图会被静默回滚

        【历史缺陷】`assign()` 原先只钉传入的那一个 key。只钉 asset 的话，
        下一次 `GET /api/skills`（技能库页的数据源）走 `resolve('rt:*')` 会按关键词
        重新打分把人工选择覆盖回旧类 —— 技能中心显示新类、技能库页显示旧类。
        实证：`writing-skills` 曾被移到「代码与工程」，但只有 `asset:` 侧进了 `manual`。
        """
        reg.resolve("asset:x", name="语音助手", description="语音交互", content="")
        reg.resolve("rt:x", name="语音助手", description="语音交互", content="")
        assert reg.assignment("asset:x") == reg.assignment("rt:x") == "语音与多媒体"
        reg.assign("asset:x", "代码与工程")
        st = reg.snapshot()
        assert st["assignments"]["asset:x"] == "代码与工程"
        assert st["assignments"]["rt:x"] == "代码与工程"          # 对侧被一并钉住
        assert {"asset:x", "rt:x"} <= set(st["manual"])            # 且都进了 manual
        # 运行时行按旧语义重打分也不许回滚
        again = reg.resolve("rt:x", name="语音助手", description="语音交互", content="")
        assert again == "代码与工程"

    def test_assign_does_not_invent_counterpart_record(self, reg):
        """对侧没有记录时不凭空造一条（避免把"只有运行时"的技能塞进资产视图）"""
        reg.resolve("rt:only-rt", name="语音助手", description="语音交互", content="")
        reg.assign("rt:only-rt", "代码与工程")
        st = reg.snapshot()
        assert "asset:only-rt" not in st["assignments"]
        assert "asset:only-rt" not in st["manual"]

    def test_inconsistent_pairs_reports_but_does_not_write(self, reg):
        """自愈检查只报告不一致，**不静默自动改**（避免掩盖自动分类的真实缺陷）"""
        reg.resolve("asset:p", name="语音助手", description="语音交互", content="")
        reg.resolve("rt:p", name="邮件工具", description="处理邮件", content="")
        # resolve 的双生态收敛会把 asset 对齐到 rt，故此处构造"钉住后分叉"的现场
        reg.assign("rt:p", "邮件与通讯")   # 双键钉住 → 两侧一致
        assert reg.inconsistent_pairs() == []
        reg.assign("asset:p", "代码与工程")  # 再次钉 asset → 两侧又一致（双键钉住）
        assert reg.inconsistent_pairs() == []
        # 直接改盘模拟"历史遗留分叉"：
        st = reg.snapshot()
        st["assignments"]["asset:p"] = "安全与合规"
        st["manual"] = [k for k in st["manual"] if k != "asset:p"]
        reg._save(st)
        bad = reg.inconsistent_pairs()
        assert len(bad) == 1 and bad[0]["skill_id"] == "p" and not bad[0]["pinned_both"]
        # 只读：再读一次状态没变
        assert reg.snapshot()["assignments"]["asset:p"] == "安全与合规"

    def test_same_name_conflicts_reports_duplicate_hashes(self, reg):
        """同名不同实例（pd-<语义名>-<哈希>-skill）分到不同类 ⇒ 报告出来

        【为什么直接写盘构造】`resolve()` 的双生态收敛会把 asset 对齐到 rt，
        正常路径下很难造出"同名不同哈希分到两类"的现场；而这正是**真实台账**里
        `writing-skills`: 5da20e67→代码与工程 / 7da19002→翻译与写作 的形态。
        """
        reg._save({
            "version": 1, "updated_at": "", "auto_classes": {}, "manual": [],
            "assignments": {
                "asset:pd-writing-skills-aaaaaaaa-skill": "代码与工程",
                "rt:pd-writing-skills-aaaaaaaa-skill": "代码与工程",
                "asset:pd-writing-skills-bbbbbbbb-skill": "翻译与写作",
                "rt:pd-writing-skills-bbbbbbbb-skill": "翻译与写作",
                "asset:unrelated-skill": "代码与工程",
            },
        })
        out = {g["name"]: g["by_class"] for g in reg.same_name_conflicts()}
        assert "writing-skills" in out, out
        assert set(out["writing-skills"]) == {"代码与工程", "翻译与写作"}
        # 不含同名冲突的技能不得出现
        assert "unrelated-skill" not in out


# ═══════════════════════════════════════════════════════════════
#  结构性护栏：关键词角色不变量（TASK-02 §3 第 5 步 4 / §5 E5）
# ═══════════════════════════════════════════════════════════════

class TestKeywordGuardrails:
    """「噪音/通用关键词不得独立决定一个域」的**结构性**不变量。

    【为什么做成这一类断言，而不是"每加一个词就跑一遍台账"】
    `_MIN_SCORE = 2` 恰好等于"一个中文关键词在 description 里出现一次"，
    因此在现有口径下**任何一个关键词单独出现都足以判出一个类** —— 包括所有合法的域锚点词
    （`语音`/`邮件`/`翻译`…）。所以"合成文本里单关键词不得夺域"**无法**表达成一条
    对所有词都成立的可满足断言：要么词表被掏空，要么断言恒真。
    可行的护栏是把它拆成三段（本类 ①②③④），并由审计脚本
    `scripts/audit_skill_classification.py` 在**真实台账**上常驻测"单点依赖/独立夺域词"：

      ① 声明不变量：`DOMAIN_ANCHORS` 覆盖**全部**关键词，且**只有**这些词能独立判回本域
         ⇒ 往 `SEED_CLASSES` 加词的人无法绕过"这个词够不够格"的显式评审；
      ② 退役不变量：已判定的通用词**一个都不能**再独立判出任何域；
      ③ 无重叠不变量：任何关键词不得同时属于两个类（否则归属由表序决定）；
      ④ 注入不变量：把通用词塞进**别的域**的典型文本，归类不得被夺走。
    """

    def _all_pairs(self):
        return [(c["name"], kw) for c in SEED_CLASSES for kw in c["keywords"]]

    def test_every_seed_keyword_is_a_declared_domain_anchor(self):
        """① 声明不变量：`SEED_CLASSES` 的全部关键词 ⇔ `DOMAIN_ANCHORS` 的可独立夺域词"""
        assert DOMAIN_ANCHORS, "DOMAIN_ANCHORS 不得为空"
        declared = {(nm, kw) for nm, kws in DOMAIN_ANCHORS.items() for kw in kws}
        actual = set(self._all_pairs())
        assert actual == declared, (
            f"未登记角色的关键词：{sorted(actual - declared)}；"
            f"登记了但词表里没有：{sorted(declared - actual)}")
        # 每个锚点词必须能"只凭自己"判回它所属的类
        wrong = [(nm, kw, classify_fields("skill-x", kw, "")["class"])
                 for nm, kw in sorted(actual)
                 if classify_fields("skill-x", kw, "")["class"] != nm]
        assert not wrong, f"这些关键词单独出现时判不回自己的类：{wrong}"

    def test_retired_generic_keywords_cannot_claim_any_domain(self):
        """② 退役不变量：被判为通用词的词，一个都不能再独立夺域"""
        seed_kws = {kw for _nm, kw in self._all_pairs()}
        still_in = sorted(set(RETIRED_GENERIC_KEYWORDS) & seed_kws)
        assert not still_in, f"已退役的通用词又回到了词表里：{still_in}"
        claiming = [(w, classify_fields("skill-x", w, "")["class"])
                    for w in sorted(RETIRED_GENERIC_KEYWORDS)
                    if classify_fields("skill-x", w, "")["class"] is not None]
        assert not claiming, f"已退役的通用词仍能独立夺域：{claiming}"

    def test_generic_probe_words_do_not_claim_any_domain(self):
        """③ 通用词探针：TASK-02 §3 第 5 步 3 指定的通用词必须一个域都判不出"""
        claiming = [(w, classify_fields("skill-x", w, "")["class"])
                    for w in GENERIC_PROBE_WORDS
                    if classify_fields("skill-x", w, "")["class"] is not None]
        assert not claiming, f"通用词不应被分到任何具体域（应落「未分类」）：{claiming}"

    def test_no_keyword_belongs_to_two_classes(self):
        """④ 无重叠不变量：一个关键词同时属于两类 ⇒ 归属由 `SEED_CLASSES` 表序决定"""
        seen = {}
        dup = {}
        for nm, kw in self._all_pairs():
            if kw in seen and seen[kw] != nm:
                dup.setdefault(kw, {seen[kw]}).add(nm)
            seen[kw] = nm
        assert not dup, f"同一关键词被多个类共用：{dup}"

    def test_generic_probe_injection_does_not_steal_a_domain(self):
        """④ 注入不变量：把通用词塞进**别的域**的典型文本，归类不得被夺走

        这是对历史 bug 的直接泛化：当年裸「识别」就是"塞进编码技能文本后把域夺走"。
        """
        canon = {
            "交流与人格": "在对话中表达情绪与共情，保持稳定的语气与人格风格",
            "记忆与知识": "把长期记忆压缩成摘要并归档进知识库，便于回忆与检索",
            "安全与合规": "对敏感内容做安全审查与拦截，防止危险与越狱",
            "语音与多媒体": "把语音转成文字并合成音频，处理视频与图像素材",
            "邮件与通讯": "起草邮件，通过消息推送与通知提醒收件人与发件人",
            "文档与办公": "整理表格与起草会议纪要，输出 pdf 与 excel",
            "代码与工程": "编写代码并调试函数与接口，用 git 提交并重构模块",
            "网络与搜索": "从网页抓取内容，用搜索与浏览器联网查询 url",
            "数据分析与可视化": "对数据做统计分析并画出图表与指标看板",
            "工作流与自动化": "编排工作流，定时调度批处理任务与自动化流水线",
            "翻译与写作": "翻译外文并润色文案，改写与校对语法措辞",
        }
        assert set(canon) == set(SEED_NAMES), "典型文本必须覆盖全部种子类"
        for host, text in canon.items():
            assert classify_fields("skill-x", text, "")["class"] == host, \
                f"典型文本自身都判错了：{host}"
        stolen = []
        for host, text in canon.items():
            for w in GENERIC_PROBE_WORDS:
                got = classify_fields("skill-x", f"{text}（{w}）{w}", "")["class"]
                if got != host:
                    stolen.append((host, w, got))
        assert not stolen, f"通用词注入后夺走了别的域：{stolen}"

    def test_seed_names_have_no_duplicates(self):
        """种子类名不得重复（重复会让 `DOMAIN_ANCHORS` 的键相互覆盖）"""
        names = [c["name"] for c in SEED_CLASSES]
        assert len(names) == len(set(names)) and names == SEED_NAMES


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

    def test_move_class_pins_runtime_view_too(self, svc):
        """回归：人工移动必须**同时钉住 rt 键**，否则运行时视图会把类名静默回滚

        【实测链路】`move_class` 原先只 `assign('asset:*')`（落 manual）+ `mirror` 到 rt
        （只写值、不写 manual）；而 `resolve('rt:*')` 对**非 manual** 的既有归类允许
        "置信命中即覆盖" ⇒ 人工移动后，下一次 `GET /api/skills`（技能库页的数据源）
        按关键词重新打分，把 mirror 过去的值覆盖回旧类：
        技能中心（asset 视图）显示新类，技能库页（rt 视图）仍显示旧类。
        实证：把 writing-skills 移到「代码与工程」后两视图分叉。
        """
        svc.create_manual(_data("w1", "writing-skills",
                                description="编写技能文档与规范", content="# 文档写作"))
        svc.move_class("w1", "代码与工程")
        assert svc._class_registry.assignment("asset:w1") == "代码与工程"
        assert svc._class_registry.assignment("rt:w1") == "代码与工程"
        assert "rt:w1" in svc._class_registry.snapshot().get("manual", [])
        # 模拟 GET /api/skills 的解析路径：字段文本明显指向旧类，也不许回滚
        again = svc._class_registry.resolve(
            "rt:w1", name="writing-skills",
            description="编写技能文档与规范", content="")
        assert again == "代码与工程"
        # 资产侧同样不许被回滚（双生态一致）
        assert svc._class_registry.resolve(
            "asset:w1", name="writing-skills",
            description="编写技能文档与规范", content="# 文档写作") == "代码与工程"

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


# ═══════════════════════════════════════════════════════════════
#  人工改类 REST 面（slow 车道：需真实 app）
# ═══════════════════════════════════════════════════════════════

@pytest.mark.slow
class TestClassMoveRest:
    """`/api/skills-mgmt/classes*`（人工改类 / 恢复自动）——**既有端点**上的界面支撑面

    【不易·不要另开一套端点】技能分类的 REST 权威一直是
    `agent/server_routes/routes_skills_mgmt.py`（技能中心页在用）。本次只做两件事：
      ① 给既有 `classes/move` 增加 `{skill_id, auto: true}` 的「恢复自动分类」模式；
      ② 在技能库页（`pages/hub/memory/skills.tsx`）补一个改类入口，**复用**这套端点。
    早期误判"没有改类端点"而在 `plugins/skills.py` 另起了一套 `/api/skills/class*`，
    已删除 —— 同一能力两份端点正是本仓最忌的"第二份口径"。

    【为什么标 slow】需导入 `app_server`（连带 torch/sentence-transformers），
    放进 `-n 2` 的单元测试分片会挤到邻居（见 tests/unit/test_tool_callability.py 同名说明）。
    【为什么只测只读 + 校验路径】成功路径要写真实资产库/分类注册表（`data/skills_classes.json`
    是运行时状态）会污染现场；`unset_manual` 与 `move_class` 的语义由注册表/服务测试覆盖。
    """

    @pytest.fixture(scope="class")
    def client(self):
        import app_server
        rules = {str(r.rule) for r in app_server.app.url_map.iter_rules()}
        for path in ("/api/skills-mgmt/classes", "/api/skills-mgmt/classes/move"):
            assert path in rules, f"{path} 未注册（技能分类的 REST 权威）"
        return app_server.app.test_client()

    def _auth_bypass(self, monkeypatch):
        """关闭共享令牌校验：`routes_skills_mgmt` 用 `agent.server_auth.require_token`

        本机 `.env` 配了 `FLASK_API_TOKEN` ⇒ 不放行就一律 401（实测 `assert 401 == 400`）。
        `app_server._API_TOKEN_ENABLED` 一并关掉，避免端点换装饰器时静默失效。
        不反过来断言"未带令牌必须 401"：CI 可能没配令牌（那时无令牌即放行），会随环境翻转。
        """
        import app_server
        import agent.server_auth as sa
        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)
        monkeypatch.setattr(app_server, "_API_TOKEN_ENABLED", False)

    def test_classes_view_returns_groups(self, client, monkeypatch):
        self._auth_bypass(monkeypatch)
        resp = client.get("/api/skills-mgmt/classes")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert isinstance(body["groups"], list) and body["groups"], "分类视图不应为空"
        names = {g["name"] for g in body["groups"]}
        assert "代码与工程" in names
        assert isinstance(body["total"], int)

    def test_move_requires_skill_id(self, client, monkeypatch):
        self._auth_bypass(monkeypatch)
        r = client.post("/api/skills-mgmt/classes/move", json={"class_name": "代码与工程"})
        assert r.status_code == 400 and "skill_id" in r.get_json()["error"]

    def test_move_rejects_unknown_class(self, client, monkeypatch):
        self._auth_bypass(monkeypatch)
        r = client.post("/api/skills-mgmt/classes/move",
                        json={"skill_id": "whatever", "class_name": "不存在之分类"})
        assert r.status_code == 400                          # move_class 校验未知分类

    def test_auto_mode_on_unpinned_skill_is_rejected(self, client, monkeypatch):
        """未钉住的技能调 auto=true ⇒ 400（幂等语义显式化，且这条路径不写盘）"""
        self._auth_bypass(monkeypatch)
        r = client.post("/api/skills-mgmt/classes/move",
                        json={"skill_id": "__never_pinned__", "auto": True})
        assert r.status_code == 400
        assert "无需恢复" in r.get_json()["error"]



# ═══════════════════════════════════════════════════════════════
#  外部导入队列 / 安装预检（先存草稿逐个放行 + 自身重复预检明示）
# ═══════════════════════════════════════════════════════════════

class TestImportQueueAndPrecheck:
    def _upsert(self, svc, sid, source, status="draft", **extra):
        from agent.skills_mgmt.models import Skill as SK, SkillStatus
        data = dict(
            id=sid, name=sid, description="desc-" + sid,
            content="# " + sid, content_type="markdown",
            category="custom", tags=[], author="tester",
            enabled=False, source=source,
            status=SkillStatus(status),
        )
        data.update(extra)
        svc.store.upsert(SK.from_storage_dict(data))

    def test_import_queue_lists_only_external_drafts(self, svc):
        self._upsert(svc, "ext-1", "external_agent")
        self._upsert(svc, "ext-2", "github:someone/repo")
        self._upsert(svc, "manual-1", "manual")
        self._upsert(svc, "wf-1", "workflow")
        q = svc.import_queue()
        ids = {r["id"] for r in q}
        assert ids == {"ext-1", "ext-2"}
        assert q[0]["review"] is None or isinstance(q[0]["review"], dict)

    def test_precheck_local_native_dup_reports_absorb(self, svc, tmp_path):
        import json
        p = tmp_path / "dup-mem.json"
        p.write_text(json.dumps({
            "id": "mem-x", "name": "记忆摘要器",
            "description": "压缩对话历史为摘要",
            "content": "总结长对话并归档",
        }, ensure_ascii=False), encoding="utf-8")
        out = svc.install_precheck("local:" + str(p))
        assert out["ok"] is True
        # 吸收优先：原生重叠不再是阻断项（blocked=False），提示按增量吸收
        assert out["blocked"] is False
        assert out["overlap_action"] == "absorb"
        assert any(n["id"] == "memory_summary" for n in out["native_dups"])
        assert any(f["code"] == "DUP_NATIVE_FUNC" for f in out["findings"])

    def test_precheck_local_benign_not_blocked(self, svc, tmp_path):
        import json
        p = tmp_path / "pdf-x.json"
        p.write_text(json.dumps({
            "id": "pdf-x", "name": "pdf-extractor",
            "description": "解析 PDF 提取表格",
            "content": "读取 pdf 输出结构化正文",
        }, ensure_ascii=False), encoding="utf-8")
        out = svc.install_precheck("local:" + str(p))
        assert out["ok"] is True
        assert out["blocked"] is False
        assert out["native_dups"] == []

    def test_precheck_local_missing_reports_error(self, svc):
        out = svc.install_precheck("local:C:/nope/nonexistent.json")
        assert out["ok"] is False
        assert "不存在" in out["error"] or "错误" in out["error"]
