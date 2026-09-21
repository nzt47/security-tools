"""L32 · 收口回归：非 editable 节的 custom_content 不得被静默丢弃

缺陷形态（收口前实测）
------------------------------------------------------------------
SystemPromptConfigManager.update_section 按白名单写字段，白名单含
custom_content 且**对任何节都不校验可编辑性**。但注册表里只有
identity / principles 标了 editable=True，全文件也只有
_render_identity / _render_principles 会读 custom_content
（由 tests/unit/test_prompt_section_editable_invariant.py 行为守护）。

⇒ 给 skill_instructions / tool_status / memory_context / current_status 等节写
custom_content，会**落盘成永远不会被读取的死数据**，且调用方拿到的返回值是
True —— 与 TASK-01「接口返回成功、数据没落盘、且无任何痕迹」同一类
「返回信号与事实不符」的缺陷，只是方向相反（写了不用）。

收口形态（本次选定）
------------------------------------------------------------------
**显式报错**：update_section 返回 False + 结构化日志
（action=update_section.rejected），且**一个字段都不写**（fail-closed）。
不选「忽略该键但继续写其余字段」，是因为该方法返回 bool：调用方在返回值里
看不到被丢的键，忽略即等于静默丢弃；要让调用方看到 ignored_keys，必须改
返回类型或另设出口 —— 超出本次收口授权，故不改 API（形态 a 无需改 API）。

本文件的断言分三组
------------------------------------------------------------------
一、合法节写入行为**完全不变**（回归护栏，防收口误伤）
二、非 editable 节的 custom_content **不被静默丢弃**（核心）
三、与 L31 不变量绑定 + 防空转（防止收口与注册表 editable 标志漂移）
"""
from __future__ import annotations

import logging
from dataclasses import asdict

import pytest

from agent.system_prompt_config import (
    SECTION_REGISTRY,
    SystemPromptConfigData,
    SystemPromptConfigManager,
    is_section_editable,
)

# 哨兵：独一无二，便于在整份落盘 JSON 里搜「死数据是否真的写进去了」
SENTINEL = "ZZ_L32_CUSTOM_CONTENT_GUARD_SENTINEL_ZZ"

_MOD_LOGGER_NAME = "agent.system_prompt_config"


# ══════════════════════════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def config_file(tmp_path):
    """临时配置文件路径（绝不触碰 data/system_prompt_config.json）"""
    return str(tmp_path / "system_prompt_config.json")


@pytest.fixture
def manager(monkeypatch, config_file):
    """CONFIG_FILE 指向 tmp 的 manager（与 test_system_prompt_config_cache 同技法）"""
    monkeypatch.setattr("agent.system_prompt_config.CONFIG_FILE", config_file)
    return SystemPromptConfigManager()


@pytest.fixture
def mod_logs():
    """直接挂在模块 logger 上收集 record

    为何不只用 caplog：tests/unit/conftest.py 的 _unit_isolate_logger 会清空所有
    非 pytest logger 的 handler 并把 propagate 复位到 True。挂在本模块 logger 上的
    自有 handler 不经过 root，捕获与 propagate / root handler 无关，判定稳定。
    """
    logger = logging.getLogger(_MOD_LOGGER_NAME)
    records = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collector(level=logging.DEBUG)
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)


def _default_section_keys():
    """默认配置里的全部节 key（不落盘、不读磁盘）"""
    return sorted(asdict(SystemPromptConfigData())["sections"].keys())


ALL_SECTION_KEYS = _default_section_keys()
EDITABLE_SECTION_KEYS = [k for k in ALL_SECTION_KEYS if is_section_editable(k)]
NON_EDITABLE_SECTION_KEYS = [k for k in ALL_SECTION_KEYS if not is_section_editable(k)]


def _raw_config_text(config_file):
    with open(config_file, "r", encoding="utf-8") as f:
        return f.read()


def _rejection_records(records):
    """挑出结构化拒绝日志（log_dict 生成的 dict 载荷）

    未知节走的是 logger.warning("未知的配置组件: %s") —— msg 是 str，不会命中；
    这条筛选保证「测试通过」不是因为落进了未知节分支。
    """
    out = []
    for rec in records:
        payload = rec.msg
        if isinstance(payload, dict) and payload.get("action") == "update_section.rejected":
            out.append(payload)
    return out


# ══════════════════════════════════════════════════════════════════════════
#  一、合法节写入行为完全不变
# ══════════════════════════════════════════════════════════════════════════

class TestEditableSectionsUnchanged:
    """identity / principles 的 custom_content 仍照写不误（防收口误伤）"""

    def test_identity_custom_content_still_applies(self, manager, config_file):
        ok = manager.update_section("identity", {"custom_content": SENTINEL})
        assert ok is True

        # 落盘（新建 manager 绕过缓存，真读文件）
        reloaded = SystemPromptConfigManager()
        assert reloaded.load()["sections"]["identity"]["custom_content"] == SENTINEL
        assert SENTINEL in _raw_config_text(config_file)

        # 真发出（走生产路径 build_template，而非直接调渲染函数）
        assert SENTINEL in reloaded.build_template()

    def test_principles_custom_content_still_applies(self, manager, config_file):
        ok = manager.update_section("principles", {"custom_content": SENTINEL})
        assert ok is True

        reloaded = SystemPromptConfigManager()
        assert reloaded.load()["sections"]["principles"]["custom_content"] == SENTINEL
        assert SENTINEL in reloaded.build_template()

    def test_editable_section_mixed_payload_still_applies(self, manager):
        """可编辑节带多个字段的整包写入仍全部生效"""
        ok = manager.update_section("identity", {
            "custom_content": SENTINEL,
            "enabled": True,
            "label": "L32 标签",
        })
        assert ok is True
        sec = manager.load()["sections"]["identity"]
        assert sec["custom_content"] == SENTINEL
        assert sec["label"] == "L32 标签"

    @pytest.mark.parametrize("key", NON_EDITABLE_SECTION_KEYS)
    def test_legal_keys_on_non_editable_sections_still_work(self, manager, key):
        """非 editable 节的**合法**字段（enabled/token_limit/label/extra_params）行为不变"""
        ok = manager.update_section(key, {
            "enabled": False,
            "token_limit": 4242,
            "label": "L32 合法改写",
            "extra_params": {"l32_probe": True},
        })
        assert ok is True
        sec = manager.load()["sections"][key]
        assert sec["enabled"] is False
        assert sec["token_limit"] == 4242
        assert sec["label"] == "L32 合法改写"
        assert sec["extra_params"]["l32_probe"] is True

    def test_unknown_section_still_rejected(self, manager):
        """未知节行为不变（仍返回 False，且不得被误判为本次收口路径）"""
        assert manager.update_section("no_such_section_l32", {"enabled": True}) is False


# ══════════════════════════════════════════════════════════════════════════
#  二、非 editable 节的 custom_content 不被静默丢弃
# ══════════════════════════════════════════════════════════════════════════

class TestNonEditableCustomContentNotSilentlyDropped:
    """收口核心：显式拒绝（False）+ 结构化日志 + 零写入"""

    def test_non_editable_keys_are_non_vacuous(self):
        """防空转：确有非 editable 节可供断言之"""
        assert NON_EDITABLE_SECTION_KEYS, "没有非 editable 节，本片断言会空转通过"
        assert EDITABLE_SECTION_KEYS, "没有 editable 节，无法验证合法路径未被误伤"

    @pytest.mark.parametrize("key", NON_EDITABLE_SECTION_KEYS)
    def test_custom_content_rejected_not_stored(self, manager, config_file, mod_logs, key):
        """① 返回 False ② 整份落盘 JSON 里搜不到哨兵 ③ 有结构化日志"""
        ok = manager.update_section(key, {"custom_content": SENTINEL})

        # ① 显式失败信号（不是静默 True）
        assert ok is False, "[" + key + "] 非 editable 节写 custom_content 竟返回成功"

        # ② 死数据没落盘（整份文件扫描，不只看该节）
        assert SENTINEL not in _raw_config_text(config_file), (
            "[" + key + "] custom_content 被写进了配置文件 —— 正是要收口的死数据"
        )
        assert manager.load()["sections"][key].get("custom_content", "") == ""

        # ③ 结构化日志留痕（log_dict 载荷，可被日志系统检索）
        rejections = _rejection_records(mod_logs)
        assert len(rejections) == 1, "[" + key + "] 未产生（或产生了多条）拒绝日志"
        payload = rejections[0]
        assert payload["section_key"] == key
        assert payload["rejected_keys"] == ["custom_content"]
        assert payload["editable"] is False
        assert payload["written"] is False
        assert payload["module_name"] == "system_prompt_config"
        assert payload["level"] == "ERROR"
        assert payload["custom_content_length"] == len(SENTINEL)

        # 日志级别确为 ERROR（可被 ERROR 级告警采集）
        assert [r for r in mod_logs if r.levelno == logging.ERROR], (
            "[" + key + "] 拒绝未以 ERROR 级别记录"
        )

    @pytest.mark.parametrize("key", NON_EDITABLE_SECTION_KEYS)
    def test_blank_custom_content_also_rejected(self, manager, mod_logs, key):
        """空白值同样显式拒绝（口径钉死：非 editable 节**不得出现该键**）

        选择「连空串也拒绝」而非「空串视同未设置」：后者又是一次静默容忍，
        且会让「有没有写」取决于值的形状；统一拒绝后契约只有一条，可被本测试钉住。
        """
        assert manager.update_section(key, {"custom_content": "   "}) is False
        assert _rejection_records(mod_logs), "[" + key + "] 空白 custom_content 被静默接受"

    def test_mixed_payload_is_rejected_whole(self, manager, config_file, mod_logs):
        """fail-closed：整包拒绝，enabled 等合法字段也**一个都不写**

        避免「一半写了一半被丢」的模糊状态 —— 调用方拿到 False 即知需拆包重发。
        """
        before = manager.load()["sections"]["skill_instructions"]["enabled"]
        ok = manager.update_section("skill_instructions", {
            "enabled": not before,
            "custom_content": SENTINEL,
        })
        assert ok is False

        after = manager.load()["sections"]["skill_instructions"]
        assert after["enabled"] == before, "整包拒绝后 enabled 竟被改写（半写状态）"
        assert SENTINEL not in _raw_config_text(config_file)
        assert _rejection_records(mod_logs), "整包拒绝未留痕"

    def test_editable_section_emits_no_rejection_log(self, manager, mod_logs):
        """反向对照：合法节写 custom_content 绝不产生拒绝日志（排除假阳性）"""
        assert manager.update_section("identity", {"custom_content": SENTINEL}) is True
        assert _rejection_records(mod_logs) == []


# ══════════════════════════════════════════════════════════════════════════
#  三、与 L31 不变量绑定 + 防空转
# ══════════════════════════════════════════════════════════════════════════

def test_guard_matches_render_behaviour():
    """收口的判据（is_section_editable）必须与渲染函数**实际行为**一致

    L31 已断言「editable ⟺ 渲染函数读 custom_content」。本片再加一条：收口用的
    is_section_editable 必须等于该行为 —— 否则收口会与前端/渲染漂移
    （例如某节渲染开始读 custom 却没标 editable ⇒ 合法写入被误拒）。
    """
    readers, allowed = set(), set()
    for key, fn, meta in SECTION_REGISTRY:
        if meta.get("editable") is True:
            allowed.add(key)
        out = fn({key: {"enabled": True, "custom_content": SENTINEL}})
        if SENTINEL in str(out):
            readers.add(key)

    assert readers == allowed, (
        "收口判据与渲染行为漂移：渲染读取=" + repr(sorted(readers))
        + " / editable 放行=" + repr(sorted(allowed))
    )
    for key in ALL_SECTION_KEYS:
        assert is_section_editable(key) is (key in readers), (
            "[" + key + "] is_section_editable 与渲染行为不一致"
        )


def test_rejection_is_observable_via_return_value_not_only_logs():
    """「不静默」的最低保证：失败必须体现在**返回值**上，而不只在日志里

    调用方即使不看日志，也拿不到「成功」的假信号（TASK-01 的教训）。
    """
    import inspect

    src = inspect.getsource(SystemPromptConfigManager.update_section)
    assert "is_section_editable" in src, "update_section 未接入可编辑性收口"
    assert "return False" in src, "收口未返回显式失败信号"
