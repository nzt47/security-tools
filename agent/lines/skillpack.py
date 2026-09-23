"""主线技能包 —— L2 档案的 `skills:` 在**注入侧**的身份减法

【这条链此前缺的那一环（为什么新增本模块）】
    `LineProfile.skills` 长期只活在「存储 + UI」里：7 条主线都写了
    `skills:`，`agent/server_routes/routes_agent_lines.py` 也把 L2 描述成
    「含技能包」，但**运行时没有任何消费方** —— 哪些技能生效完全由全局
    `SkillRegistry().list_enabled_ids()` 与
    `DigitalLifePersonaMixin._SKILL_PROMPTS` 求交决定，档案里的声明写了等于
    没写（写错技能 id 也只是静默失效）。本模块把这份声明变成一条**可判定的
    事实**：`SkillPack`（本线允许注入哪些技能）。

【为什么技能的「减法」落在注入侧，不能照抄工具侧的 fail-closed】
    领域口径见 `data/skill_callability.yaml` 开头：技能**不是模型发起的
    工具调用** —— 提示词型技能由 `ContextInjector` 按意图注入、带脚本技能
    由 `SkillExecutor` 显式执行。工具侧的 fail-closed（无声明一律不给）
    收窄的是「模型手里的调用面」；照抄到技能侧则会连「系统按意图注入的内容」
    一起清空 —— 而 7 条主线当前的 `skills:` 里**没有一条**写的是 persona
    指令型技能 id，一旦照抄，所有装线场景的技能段会**瞬间清空**。故：
        - 未装线 / 声明为空 ⇒ **不限制**（技能侧没有「身份」可减，保持旧行为）
        - 只有**显式列出**技能 id 时，才按白名单做减法
    这是全仓接线纪律「不装线 = 等于没改过」在技能侧的对应物。

【策略顺序（顺序敏感：先判「有没有声明」，再判「声明了什么」）】
    1. `profile is None`（未装线 / 档案缺失 / 停用）
           ⇒ `mode="unrestricted"`, `source="no-line"`
    2. `profile.skills` 为空（含只写空白串 / 非序列）
           ⇒ `mode="unrestricted"`, `source="empty-declaration"`
    3. 非空 ⇒ `mode="whitelist"`, `source="whitelist"`
           - `allowed` = 按 YAML 声明顺序**去重后** ∩ known（保序）
           - `unknown` = 声明了但 known 里没有的 id
             —— **如实报告，不静默吞掉，也不放进 allowed**（写错 id 静默失效
             正是本次要修的缺陷，`unknown` 是它在界面/校验上的可见面）

【known 的唯一权威（为什么不用 loader / store）】
    运行时技能目录 = `agent.skills_mgmt.registry.SkillRegistry.list_skill_ids()`：
    它是仓库里**唯一**把「主轨 `data/skills_mgmt.json`」与「文件轨
    `data/skills_repo/<id>/skill.md` front matter」合并成一份视图的入口。
      - `store.list_all()` 只覆盖主轨 ⇒ 看不到 persona 内置技能
        （`self_reflection` / `voice_interaction` 等）
      - `loader.list_all_metadata()` 只覆盖文件轨 ⇒ 看不到内容内联在
        `skills_mgmt.json` 的指令型技能（`engineering-test-delivery` /
        `code-observability` / `testing-anti-patterns` —— 恰是 7 条主线声明的那批）
      - `list_enabled_ids()` 是「启用状态」视图，不是「目录」视图：拿它当
        known 会让「全局停用一个技能」顺手把主线档案判成引用未知技能，保存直接失败
    故 known 用 `list_skill_ids()`（全部已知，与启用状态解耦）。

【为什么 known 还要并上 tracked 的技能声明表（唯一的一处「两份来源」）】
    运行时目录里有几个技能，取决于**进程环境**：`data/skills_mgmt.json` 被 .gitignore
    忽略，干净 checkout 里不存在，而 7 条内置主线声明的指令型技能恰只活在那份台账里。
    若 known 只认运行时目录，同一份档案在开发机（装了）与 CI（没装）会得到**不同的**
    unknown / allowed 判定，`LineRegistry.save` 的校验就成了环境相关的行为。故并上
    `data/skill_callability.yaml`（**tracked** 的技能侧 L1 声明表）里的 id：它只补
    「这个 id 有出处」，不改变「实际装了哪些」（注入仍由运行时启用集合决定）。

【依赖纪律】
    `agent.lines` **不得在模块级 import `agent.skills_mgmt`**（惰性 import）。
    理由与 `agent/lines/callability.py` 顶部 `agent.lines → agent.tools` 那条
    同款：模块级双向导入会被 `scripts/check_circular_deps.py` 与
    `.importlinter` 判违规。本模块只在函数体内 import，且**任何异常都不抛出**，
    退化为「已经拿到的那部分」。

【零副作用】
    纯数据 + 只读探测：不读 config.yaml、不写盘、不改全局状态（唯一的模块级状态
    是 `_KNOWN_CACHE` 这一份 memo，见 `invalidate_known_skills_cache()`）。

【known 的 memo 为什么会自己失效（L6：跨用例污染）】
    该 memo 此前只认「算过一次」这一个事实：**输入变了它不知道**。测试用
    monkeypatch 把技能目录指向 tmp_path 之后，那个"临时目录里的 known"会一直
    留在进程里，后面基于**真实目录**的断言就拿到旧值 —— 表现为「单独跑绿、连跑红」，
    两条并行工作流不得不在各自的测试文件里加 autouse 的
    `invalidate_known_skills_cache()` 来规避。缺陷不在缓存本身（单次全量解析中位
    ~13.4ms，见 `_known_fingerprint` 顶部实测），而在**缓存没有跟着输入走**。
    故 memo 现在带一份**输入快照指纹**（`_known_fingerprint()`，亚毫秒级：~0.5ms），
    指纹变了就自动重算：显式 invalidate 仍在（契约不变），但不再是正确性的前提 ——
    上面那两处 autouse 规避夹具因此已经删掉（守门用例见
    `tests/unit/test_line_skillpack.py::TestKnownCacheFollowsInputs`）。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 不限制：本线对技能注入不设边界（= 旧行为）
MODE_UNRESTRICTED = "unrestricted"
#: 白名单：只有本线声明的技能允许注入
MODE_WHITELIST = "whitelist"
MODES: Tuple[str, ...] = (MODE_UNRESTRICTED, MODE_WHITELIST)

#: `source` 的人读文案（单一来源：UI 不再自造中文说明，避免两处各说各话）
SOURCE_LABELS: Dict[str, str] = {
    "no-line": "未装线（没有生效的主线）⇒ 不限制",
    "empty-declaration": "本线未声明技能（skills 为空）⇒ 不限制",
    "whitelist": "本线按 skills 声明做白名单",
    "line-disabled": "主线已停用 ⇒ 回退不限制",
    "profile-missing": "主线档案不存在 ⇒ 回退不限制",
    "profile-error": "主线档案不可读 ⇒ 回退不限制",
    "resolve-error": "解析技能包异常 ⇒ 回退不限制",
}

#: 磁盘事实（运行时目录不可用时的兜底；顺序即优先级）
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLS_REPO_DIR = os.path.join(_ROOT, "data", "skills_repo")
SKILLS_MGMT_JSON = os.path.join(_ROOT, "data", "skills_mgmt.json")
SKILLS_LEGACY_JSON = os.path.join(_ROOT, "data", "skills.json")
#: 技能侧 L1 声明表（**tracked**）：`skills:` 的键即"这个 id 被正式声明过"
SKILL_CALLABILITY_YAML = os.path.join(_ROOT, "data", "skill_callability.yaml")


def _clean_ids(raw: Any) -> Tuple[str, ...]:
    """把任意声明值规范化成「去重、保序、非空」的 id 元组

    Why 宽容：`skills` 来自 YAML / REST，可能是 None、字符串、含空白
    的串、甚至非序列。这里只做**规范化**，不做合法性判定（那是
    `LineProfile.validate(known_skills=…)` 的活），
    以免解析层与校验层各说各话。
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        items: Iterable[Any] = [raw]
    elif isinstance(raw, (list, tuple, set, frozenset)):
        items = raw
    else:
        return ()
    out: List[str] = []
    seen: set = set()
    for item in items:
        sid = str(item).strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
    return tuple(out)


@dataclass(frozen=True)
class SkillPack:
    """一条主线的技能包判定结果（纯数据，可哈希、可序列化）

    【为什么 frozen】技能包是**判定结果**，不是可变状态：装配/注入方只读它。
    【为什么 allowed 是元组而不是集合】保序 —— 注入顺序应可复现（与 YAML 声明
    顺序一致），集合会把它变成随机序。
    """

    #: 生效主线 id；None = 未装线（不限制）
    line_id: Optional[str] = None
    #: "unrestricted" | "whitelist"
    mode: str = MODE_UNRESTRICTED
    #: YAML 里声明的技能 id（去重后，保序；未装线时为空）
    requested: Tuple[str, ...] = ()
    #: 本线允许的技能 id（白名单模式下 = requested ∩ known，保序）
    allowed: Tuple[str, ...] = ()
    #: 声明了但运行时目录里不存在的 id（如实报告，不静默吞掉）
    unknown: Tuple[str, ...] = ()
    #: 人读原因串（取值见 SOURCE_LABELS）
    source: str = "no-line"

    # ── 判定 ──

    @property
    def unrestricted(self) -> bool:
        return self.mode != MODE_WHITELIST

    def allows(self, skill_id: str) -> bool:
        """本线是否允许注入该技能（不限制模式恒真）"""
        if self.unrestricted:
            return True
        return str(skill_id) in self.allowed

    def filter(self, ids: Iterable[str]) -> List[str]:
        """按本包过滤 id 序列：**保序**（保持入参顺序，不重排）

        保序的理由：调用方（`_build_skill_instructions`）拿的是
        `_SKILL_PROMPTS` 的声明顺序，注入文本的顺序必须可复现。
        """
        if self.unrestricted:
            return [str(i) for i in ids]
        allowed = set(self.allowed)
        return [str(i) for i in ids if str(i) in allowed]

    def to_dict(self) -> Dict[str, Any]:
        """给 UI / 状态面板的投影（前端只呈现，不重算）"""
        return {
            "line_id": self.line_id,
            "mode": self.mode,
            "unrestricted": self.unrestricted,
            "requested": list(self.requested),
            "allowed": list(self.allowed),
            "unknown": list(self.unknown),
            "source": self.source,
            "source_label": SOURCE_LABELS.get(self.source, self.source),
        }


def resolve_skill_pack(profile: Any, *, known: Optional[Iterable[str]] = None) -> SkillPack:
    """把一条主线档案解析成技能包（策略顺序见模块 docstring，**顺序敏感**）

    Args:
        profile: `LineProfile`（或任何有 `.id` / `.skills` 的对象）。
            **None = 未装线 / 档案缺失 / 停用 ⇒ 不限制**（保持旧行为）。
        known: 已知技能 id 集合；None（默认）⇒ 走 `known_skill_ids()`。
            显式传入空集合表示「目录里确实什么都没有」⇒ 白名单下全部进 unknown。

    Returns:
        `SkillPack`。除"传入对象的属性访问"外，本函数不做任何会抛异常的事
        （`known=None` 时的目录探测自身就是 fail-soft）。
    """
    if profile is None:
        # ① 未装线：技能侧没有「身份」可减 ⇒ 不限制（全仓接线纪律）
        return SkillPack(line_id=None, mode=MODE_UNRESTRICTED, source="no-line")

    line_id = str(getattr(profile, "id", "") or "").strip() or None
    requested = _clean_ids(getattr(profile, "skills", None))
    if not requested:
        # ② 空声明 = 不限制。
        # 【不易·这条绝不能被工具侧的 fail-closed 覆盖】技能不是模型发起的工具
        # 调用（见 data/skill_callability.yaml 开头），其内容由 ContextInjector
        # 按意图注入；把「没写 skills」当成「一个技能都不给」，会让 7 条主线
        # （当前没有一条声明 persona 指令型技能 id）在装线瞬间清空技能段，
        # 且这种清空是静默的。空 = 未表态 ≠ 表态为「空集」。
        return SkillPack(line_id=line_id, mode=MODE_UNRESTRICTED,
                         requested=(), source="empty-declaration")

    pool = known_skill_ids() if known is None else frozenset(str(i) for i in known)
    if not pool:
        # 目录整体不可用：如实记一笔（allowed 会全空、unknown 会全量，见下）
        logger.warning("[skillpack] 技能目录不可用（known 为空），本线声明的技能将全部判为未知")
    allowed = tuple(sid for sid in requested if sid in pool)
    unknown = tuple(sid for sid in requested if sid not in pool)
    if unknown:
        # 【诚实报告】不静默吞掉：写错的 id 必须能被界面与校验看见
        logger.info("[skillpack] 主线 %s 声明了不存在的技能 id: %s", line_id, list(unknown))
    return SkillPack(
        line_id=line_id,
        mode=MODE_WHITELIST,
        requested=requested,
        allowed=allowed,
        unknown=unknown,
        source=MODE_WHITELIST,
    )


def unrestricted_pack(source: str = "no-line",
                      line_id: Optional[str] = None) -> SkillPack:
    """构造一个「不限制」包（接线层回退用；source 见 SOURCE_LABELS）"""
    return SkillPack(line_id=line_id, mode=MODE_UNRESTRICTED, source=source)


# ════════════════════════════════════════════════════════════
#  技能目录（known）
# ════════════════════════════════════════════════════════════

#: 已知技能 id 的 memo；None = 尚未计算。**失败不缓存**（见 known_skill_ids）
_KNOWN_CACHE: Optional[FrozenSet[str]] = None
#: 与 `_KNOWN_CACHE` 配套的**输入快照指纹**；两者必须同时更新（见 known_skill_ids）
_KNOWN_FP: Optional[Tuple[Any, ...]] = None


def _path_sig(path: str) -> Tuple[Any, ...]:
    """一个技能来源路径的**廉价**指纹（stat/listdir 级，绝不解析内容）

    Why 目录用「清单」而不是 mtime：技能 id 来自**目录名**，所以"清单变了"正是
    "已知集合可能变了"的充分信号；而目录 mtime 在部分文件系统上粒度粗（且改
    `skill.md` 内容会动 mtime 却**不动 id 集合**，那属于不必要的失效）。
    文件用 (size, mtime_ns)：`skills_mgmt.json` / `skill_callability.yaml` 的
    **键集合**就是它们的全部贡献，重建这两份文件必然改变 size 或 mtime。
    任何 OSError 都不抛，退化成"读不到"这一种同样可比较的状态。
    """
    try:
        st = os.stat(path)
    except OSError:
        return (path, "missing")
    if os.path.isdir(path):
        try:
            names = tuple(sorted(
                n for n in os.listdir(path) if not n.startswith((".", "_"))))
        except OSError:
            return (path, "dir-unreadable")
        return (path, "dir", names)
    return (path, "file", st.st_size, st.st_mtime_ns)


def _registry_entry() -> Any:
    """运行时目录入口（`SkillRegistry.list_skill_ids`）的身份，取不到给 None"""
    try:
        from agent.skills_mgmt.registry import SkillRegistry
        return getattr(SkillRegistry, "list_skill_ids", None)
    except Exception:  # noqa: BLE001 入口取不到不影响其余指纹分量
        return None


def _known_fingerprint() -> Tuple[Any, ...]:
    """known 的**输入快照指纹**：输入变了 ⇒ 指纹变 ⇒ memo 自动失效

    【为什么要指纹，而不是去掉 memo（方案 B 用数据否掉了）】
        本机实测（timeit，20 次取中位，先预热）：
            - 全量重算 `known_skill_ids()`：**13.4ms**（冷启动首次 ~0.5s：要 import
              `agent.skills_mgmt`、扫 skills_repo、读三份 JSON/YAML）
            - 指纹命中：**0.51ms**（其中指纹本身 0.50ms：四次 stat 各 ~0.07–0.11ms、
              skills_repo 的 listdir+sort ~0.21ms —— Windows 上 stat 偏贵）
        即"去掉 memo"要给**每一次**调用加 13.4ms 的磁盘/import 税（远超 10ms 的
        可接受线），而指纹只花 0.5ms 就买到"跟着输入走"。故取 A（指纹）。

    【指纹里放什么，为什么这四类刚好够】
        1. **来源路径本身**（4 个常量）：monkeypatch 换目录**不动磁盘一个字节**，
            任何基于 stat 的指纹都看不见它 —— 路径串进指纹才能覆盖这一手；
        2. **每个路径的廉价指纹**（见 `_path_sig`）：真实增删技能目录 / 改台账；
        3. **三个真值来源函数的身份**（`_registry_skill_ids` / `_disk_skill_ids` /
            `_declared_skill_ids`）：测试里对它们的 `monkeypatch.setattr`（"换数据
            源"）同样不留磁盘痕迹，身份比较零成本地覆盖它；
        4. **注册表入口类方法的身份**：`SkillRegistry.list_skill_ids` 被替换
            （`test_技能目录抛异常时退回磁盘事实` 就是这么做的）时同样要失效。

    【边界（如实写明，不假装全覆盖）】
        只覆盖"**技能 id 集合**的输入"：改 `skill.md` 正文/front matter 的非 id
        字段不会失效（本来也不影响 id 集合）；绕过本模块的路径常量直接改
        `file_store._DEFAULT_REPO_PATH` 之类的**模块内部常量**不在覆盖范围内 ——
        那种情况下调用方应显式 `invalidate_known_skills_cache()`（契约保留）。
    """
    try:
        return (
            _path_sig(SKILLS_REPO_DIR),
            _path_sig(SKILLS_MGMT_JSON),
            _path_sig(SKILLS_LEGACY_JSON),
            _path_sig(SKILL_CALLABILITY_YAML),
            _registry_skill_ids,
            _disk_skill_ids,
            _declared_skill_ids,
            _registry_entry(),
        )
    except Exception as e:  # noqa: BLE001 指纹只服务于缓存，绝不向上抛
        logger.debug("[skillpack] 计算 known 指纹失败: %s", e)
        return ()


def _registry_skill_ids() -> FrozenSet[str]:
    """运行时技能目录（惰性 import；失败返回空集，不抛）

    Why `list_skill_ids()`：见模块 docstring「known 的唯一权威」。
    """
    try:
        # 惰性 import：agent.lines 不得在模块级依赖 agent.skills_mgmt（循环依赖红线）
        from agent.skills_mgmt.registry import SkillRegistry
        return frozenset(str(s) for s in SkillRegistry().list_skill_ids() if str(s).strip())
    except Exception as e:  # noqa: BLE001 目录不可用不得让装配/对话挂掉
        logger.debug("[skillpack] 运行时技能目录不可用: %s", e)
        return frozenset()


def _disk_skill_ids() -> FrozenSet[str]:
    """磁盘事实兜底：skills_repo 目录名 ∪ skills_mgmt.json 键 ∪ skills.json 的 id

    只在运行时目录不可用时才算这一份。任何一步失败都返回**已得到的部分**
    （逐个 `try`，不因一个数据源损坏而丢掉另外两个）。
    """
    import json

    ids: set = set()

    # ① data/skills_repo/<id>/（文件轨事实）；跳过 .index/.vector_index 等隐藏目录
    try:
        for name in os.listdir(SKILLS_REPO_DIR):
            if name.startswith((".", "_")):
                continue
            if os.path.isdir(os.path.join(SKILLS_REPO_DIR, name)):
                ids.add(name)
    except OSError as e:
        logger.debug("[skillpack] 读 skills_repo 失败: %s", e)

    # ② data/skills_mgmt.json：顶层键即技能 id（主轨）
    try:
        with open(SKILLS_MGMT_JSON, "r", encoding="utf-8") as f:
            doc = json.load(f)
        if isinstance(doc, dict):
            ids.update(str(k) for k in doc.keys() if str(k).strip())
    except (OSError, ValueError) as e:
        logger.debug("[skillpack] 读 skills_mgmt.json 失败: %s", e)

    # ③ legacy data/skills.json：{"skills": [{"id": ...}, ...]}（只读兼容快照）
    try:
        with open(SKILLS_LEGACY_JSON, "r", encoding="utf-8") as f:
            doc = json.load(f)
        rows = doc.get("skills") if isinstance(doc, dict) else doc
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and str(row.get("id") or "").strip():
                    ids.add(str(row["id"]).strip())
    except (OSError, ValueError) as e:
        logger.debug("[skillpack] 读 skills.json 失败: %s", e)

    return frozenset(ids)


def _declared_skill_ids() -> FrozenSet[str]:
    """技能侧 L1 声明表（data/skill_callability.yaml）里出现过的 id

    Why 要把它并进 known（**这是 known 里唯一的一处「两份来源」，理由必须写清**）：
      运行时目录里有几个技能，取决于**进程环境**：data/skills_mgmt.json 被 .gitignore
      忽略，干净 checkout 里不存在，而 7 条内置主线声明的指令型技能恰只活在那份台账里。
      若 known 只认运行时目录，同一份档案在开发机（装了）与 CI（没装）会得到**不同的**
      unknown / allowed 判定 —— 「保存会被校验拦下」于是成了一个环境相关的行为。
      声明表是 **tracked** 的（技能侧 L1 权威，与 data/tool_definitions/*.yaml 同层），
      用它兜住「这个 id 确实有出处、只是当前环境没装」，判定就与仓库可复现口径一致
      （data/capability_manifest.json 的清单口径同样把该文件算作输入）。
      注意：它只补「id 存在性」，**不**改变「实际装了哪些」—— 注入仍由运行时启用集合决定。
    """
    try:
        import yaml  # 惰性：本模块在只需要判定的路径上不引入新依赖
        with open(SKILL_CALLABILITY_YAML, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        table = doc.get("skills") if isinstance(doc, dict) else None
        if isinstance(table, dict):
            return frozenset(str(k).strip() for k in table if str(k).strip())
    except Exception as e:  # noqa: BLE001 声明表不可读不影响其余来源
        logger.debug("[skillpack] 读 skill_callability.yaml 失败: %s", e)
    return frozenset()


def known_skill_ids() -> FrozenSet[str]:
    """已知技能 id 的**唯一权威**入口（fail-soft，永不抛）

    口径 = 运行时技能目录（主） ∪ 磁盘事实（主来源不可用时的退路）
         ∪ 技能侧声明表 data/skill_callability.yaml（补 id 存在性，见 _declared_skill_ids）。

    **缓存跟着输入走**：memo 命中要同时满足「算过」与「输入指纹没变」
    （`_known_fingerprint()`，见其 docstring）。跨用例污染就是"只判前者"来的：
    上一用例把目录指向 tmp_path 算出的集合会一直留在进程里。
    **空结果不缓存**：目录暂时不可用时不能把它钉死成「没有技能」，
    下一次调用必须还有机会恢复（这正是 fail-soft 与「缓存一次失败」的区别）。
    指纹算不出来（理论上不可能，已 fail-soft）时**退化为不缓存**：宁可多算一次，
    也不能拿一份来路不明的旧值。
    """
    global _KNOWN_CACHE, _KNOWN_FP
    fp = _known_fingerprint()
    if _KNOWN_CACHE is not None and fp and fp == _KNOWN_FP:
        return _KNOWN_CACHE
    ids = set(_registry_skill_ids())
    if not ids:
        # 主来源不可用 ⇒ 退回磁盘事实（声明表随后仍会并进来）
        ids = set(_disk_skill_ids())
    ids |= _declared_skill_ids()
    out = frozenset(ids)
    if out:
        _KNOWN_CACHE = out
        _KNOWN_FP = fp      # 与缓存**同时**落盘，否则下一轮会拿旧指纹比新缓存
    else:
        _KNOWN_CACHE = None
        _KNOWN_FP = None
        logger.warning("[skillpack] 技能目录、磁盘事实与声明表都读不到内容（known 为空）")
    return out


def invalidate_known_skills_cache() -> None:
    """清空 known 的 memo（测试与治理脚本用）

    Why 仍然需要它：与 `models.invalidate_tool_meta_cache()` 同款 —— 指纹覆盖的是
    「本模块看得见的输入」（来源路径 / 目录清单 / 三个来源函数与注册表入口的身份，
    见 _known_fingerprint）；若测试绕过这些去改**别人的模块级常量**
    （如 `file_store._DEFAULT_REPO_PATH`），仍应显式失效。
    **它不再是正确性的前提**：常规的"换目录 ⇒ known 跟着变"已由指纹自动覆盖。
    """
    global _KNOWN_CACHE, _KNOWN_FP
    _KNOWN_CACHE = None
    _KNOWN_FP = None


__all__ = [
    "MODE_UNRESTRICTED",
    "MODE_WHITELIST",
    "MODES",
    "SOURCE_LABELS",
    "SKILLS_REPO_DIR",
    "SKILLS_MGMT_JSON",
    "SKILLS_LEGACY_JSON",
    "SKILL_CALLABILITY_YAML",
    "SkillPack",
    "resolve_skill_pack",
    "unrestricted_pack",
    "known_skill_ids",
    "invalidate_known_skills_cache",
]
