"""system_prompt_config · 发出信息（emit_text / emit_order / emit_stage）单元测试

对应需求：
  1. 「身份提示词（系统提示词 · 线上配置）」下各面板项**启用**后，其对应内容应
     显示在提示词区域（例：技能指令启用 → {skill_instructions}）；
  2. 面板项**按配置项被拼进 system message 的先后顺序**（发出顺序）排列。

不变量（本文件锁死）：
  - 启用且渲染非空的模板节 → emitted=True 且 emit_order = 内容在模板中的位置（严格递增）；
  - 未启用的模板节 → emitted=False，排在同组末尾，但 emit_text 仍给出「若启用会发出的内容」；
  - 聚合节的子节（body_status / mode_info）紧随父节发出；
  - 非模板节的额外组件（tools 参数 / 用户消息前置 / 运行期注入）给出阶段与事实描述。
"""
from __future__ import annotations

import json

import pytest

import agent.system_prompt_config as spc


@pytest.fixture
def sections() -> dict:
    """最小可用配置（不读真实 data/system_prompt_config.json）"""
    return {
        "identity": {"enabled": True, "label": "身份设定", "custom_content": "你是云枢。"},
        "principles": {"enabled": True, "label": "核心原则",
                       "custom_content": "## 核心原则\n先调工具再说话。"},
        "skill_instructions": {"enabled": True, "label": "技能指令", "custom_content": ""},
        "memory_context": {"enabled": True, "label": "记忆上下文", "custom_content": ""},
        "body_status": {"enabled": False, "label": "身体状态"},
        "mode_info": {"enabled": False, "label": "行为模式"},
        "tool_status": {"enabled": False, "label": "工具与技能状态"},
        "working_memory": {"enabled": True, "label": "工作记忆"},
        "tool_definitions": {"enabled": False, "label": "工具定义"},
    }


@pytest.fixture
def template() -> str:
    """与真实组装一致的模板（仅启用节，按注册表渲染顺序拼接）"""
    return "\n\n".join([
        "你是云枢。",
        "## 核心原则\n先调工具再说话。",
        "{skill_instructions}",
        "## 记忆线索\n{memory_context}",
    ])


class TestEmitOrder:
    def test_启用节按模板出现位置排序(self, sections, template):
        info = spc.compute_emit_info(sections, template)
        # 只有模板里真正出现的节算「system message 注入」
        emitted = {k: v for k, v in info.items()
                   if v["emitted"] and v["emit_stage"] == "system_prompt"}
        assert set(emitted) == {"identity", "principles", "skill_instructions", "memory_context"}
        order = [info[k]["emit_order"] for k in
                 ("identity", "principles", "skill_instructions", "memory_context")]
        assert order == sorted(order)
        assert order[0] == template.index("你是云枢。")

    def test_未启用节排在同组末尾且标记未发出(self, sections, template):
        info = spc.compute_emit_info(sections, template)
        for key in ("tool_status", "body_status", "mode_info"):
            assert info[key]["emitted"] is False
            assert info[key]["emit_order"] >= 10_000
        assert info["skill_instructions"]["emit_order"] < info["tool_status"]["emit_order"]

    def test_额外组件按阶段排在模板节之后(self, sections, template):
        info = spc.compute_emit_info(sections, template)
        assert info["working_memory"]["emit_order"] < 10_000
        assert info["working_memory"]["emit_order"] > info["memory_context"]["emit_order"]
        assert info["tool_definitions"]["emit_stage"] == "tools"
        assert info["working_memory"]["emit_stage"] == "runtime"
        assert "tools 字段" in info["tool_definitions"]["emit_text"]

    def test_聚合节子节紧随父节(self, sections):
        enabled_body = {**sections,
                        "body_status": {**sections["body_status"], "enabled": True},
                        "mode_info": {**sections["mode_info"], "enabled": True}}
        template = "\n\n".join(["你是云枢。", "## 当前状态\n{body_status}\n当前处于「{mode_name}」"])
        info = spc.compute_emit_info(enabled_body, template)
        assert info["body_status"]["emitted"] is True
        assert info["mode_info"]["emitted"] is True
        assert info["body_status"]["emit_order"] < info["mode_info"]["emit_order"]

    def test_模板缺省时按注册表现场渲染(self, sections):
        """不传 template 时函数自己渲染一份（调用方漏传也不会算错顺序）"""
        info = spc.compute_emit_info(sections)
        assert info["identity"]["emitted"] is True
        assert info["identity"]["emit_order"] == 0


class TestEmitText:
    def test_技能指令启用后给占位符内容(self, sections, template):
        info = spc.compute_emit_info(sections, template)
        assert info["skill_instructions"]["emit_text"] == "{skill_instructions}"

    def test_未启用节仍给若启用会发出的内容(self, sections, template):
        """面板项刚点开开关、尚未保存时也要能显示发出内容（探测渲染）"""
        info = spc.compute_emit_info(sections, template)
        assert info["tool_status"]["emitted"] is False
        assert "{tool_status}" in info["tool_status"]["emit_text"]

    def test_子节未启用也给占位符(self, sections, template):
        info = spc.compute_emit_info(sections, template)
        assert info["body_status"]["emit_text"] == "{body_status}"
        assert "{mode_name}" in info["mode_info"]["emit_text"]

    def test_自定义内容优先于默认渲染(self, sections, template):
        info = spc.compute_emit_info(sections, template)
        assert info["identity"]["emit_text"] == "你是云枢。"
        assert info["principles"]["emit_text"].startswith("## 核心原则")


class TestConfigWithStats:
    @pytest.fixture
    def isolated_config(self, tmp_path, monkeypatch, sections):
        cfg = tmp_path / "system_prompt_config.json"
        cfg.write_text(json.dumps({"version": 2, "sections": sections}, ensure_ascii=False),
                       encoding="utf-8")
        monkeypatch.setattr(spc, "CONFIG_FILE", str(cfg))
        manager = spc.SystemPromptConfigManager()
        return manager

    def test_stats_携带发出字段(self, isolated_config):
        res = isolated_config.get_config_with_stats()
        assert res["stats"]["skill_instructions"]["emit_text"] == "{skill_instructions}"
        assert res["stats"]["skill_instructions"]["emit_order"] >= 0
        assert res["stats"]["skill_instructions"]["emit_stage_label"] == "system message 注入"
        assert res["stats"]["tool_status"]["emitted"] is False

    def test_响应携带全量_emit_info_与阶段标签(self, isolated_config):
        res = isolated_config.get_config_with_stats()
        assert "emit_info" in res and "emit_stage_labels" in res
        # 子节不在 get_all_registry_keys() 里，必须由 emit_info 覆盖（面板需要排序）
        assert "body_status" in res["emit_info"]
        assert "mode_info" in res["emit_info"]
        assert res["emit_stage_labels"]["system_prompt"]

    def test_面板排序即发出顺序(self, isolated_config):
        """模拟前端排序：按 emit_order 升序 → 前若干项应与模板段序一致"""
        res = isolated_config.get_config_with_stats()
        rows = sorted(res["emit_info"].items(), key=lambda kv: kv[1]["emit_order"])
        emitted = [k for k, v in rows if v["emitted"] and v["emit_stage"] == "system_prompt"]
        assert emitted[:4] == ["identity", "principles", "skill_instructions", "memory_context"]
