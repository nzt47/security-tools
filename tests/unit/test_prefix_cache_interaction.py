# -*- coding: utf-8 -*-
"""F3 · 「工具宣告行（tool_status）vs 前缀缓存」的离线断言

背景：docs/audit_skill_governance/FINDINGS_DURING_IMPL.md 的 **F3** 一节；
结论与实测数据见 docs/audit_skill_governance/F3.md（本卡报告）。

【本文件锁的是**事实**，不是修正案】
    F3 的裁决是「保持现状（方案 A）」，所以这些断言**不是**在为一个改动背书，
    而是把裁决所依赖的三条事实钉住；将来任何一条变了，测试会红，逼人重读 F3.md：
      ① 宣告行在 system prompt 里的**位置**（稳定节）以及"其后还有多少内容"；
      ② 前缀缓存的损失面 = **该行之后**的全部内容（一条与实现无关的不变量）；
      ③ 工作台 SSE 路径的 system prompt **根本不渲染该行** ——
         hybrid_select_tools(question) 变化的是请求体的 tools=，不是提示词。
    若哪天有人把工作台也改成渲染宣告行，③ 会红 —— 那一天的红色含义是正确的：
    「这条不变量变了，请把 F3.md 的裁决重新做一遍」。

全部离线可跑：不启服务、不发出网请求。
"""
from __future__ import annotations

import importlib.util
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_probe():
    """按**文件路径**加载 F3 探针模块（不依赖 scripts 是否是包）

    探针里定位宣告行的口径就是本卡实测口径，直接复用可避免"测试与探针两套偏移算法"。
    """
    p = _ROOT / "scripts" / "probe_prefix_cache_interaction.py"
    assert p.exists(), "F3 探针缺失: %s" % p
    spec = importlib.util.spec_from_file_location("f3_probe", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


probe = _load_probe()
ADVERT = probe.ADVERT_MARKER


# ── ① 偏移口径 ─────────────────────────────────────────────────────────────

def test_advert_location_定位宣告行并算出其后还有多少内容():
    """偏移 = 该行首字符下标；chars_from_line = 其后剩余字符数（损失面上界）"""
    prompt = "AAA\nBBB\n" + ADVERT + "本轮向模型下发(2 个): a, b\nCCC\nDDD"
    loc = probe.advert_location(prompt)

    assert loc["present"] is True
    assert loc["line_index"] == 2
    assert loc["offset"] == len("AAA\nBBB\n")            # 8
    assert loc["chars_from_line"] == len(prompt) - loc["offset"]
    assert loc["chars_after_line"] == len(prompt) - loc["offset"] - len(loc["line"])
    assert loc["chars_from_line"] > loc["chars_after_line"]


def test_advert_location_没有标记行时如实报present_False():
    """没有宣告行就是没有 —— 不能因为"应该有"而虚构一个位置"""
    text = "你是云枢。\n当前日期：2026年9月25日"
    loc = probe.advert_location(text)

    assert loc["present"] is False
    assert loc["offset"] is None and loc["chars_from_line"] is None
    assert loc["sys_chars"] == len(text)


# ── ② 前缀缓存的损失面（与实现无关的不变量）──────────────────────────────

def test_只改宣告行时公共前缀正好止于该行():
    """前缀缓存按最长公共前缀命中 ⇒ 该行一变，可命中的前缀**最多到该行之前**。

    这条不变量是 F3 全部量化（"最多损失多少"）的依据，所以它必须是被测出来的，
    而不是被引用的。
    """
    head = "身份…\n原则…\n技能…\n"
    line_a = ADVERT + "本轮向模型下发(26 个): a, b, c"
    tail = "\n身体状态…\n记忆线索…"
    p1 = head + line_a + tail
    p2 = head + (ADVERT + "本轮向模型下发(1 个): a") + tail

    loc = probe.advert_location(p1)
    n = 0
    while n < min(len(p1), len(p2)) and p1[n] == p2[n]:
        n += 1
    # 破坏边界**一定落在宣告行内部**：
    #   · 行之前的公共头（"【工具】本轮向模型下发(…"）仍然命中 ⇒ n >= offset；
    #   · 差异不可能越过这一行 ⇒ n <= offset + len(line)；
    #   · 因此"必然损失"的内容 >= 该行之后的全部内容。
    assert n >= loc["offset"], "该行之前的内容必须仍然命中（前缀缓存的最长公共前缀语义）"
    assert n <= loc["offset"] + len(loc["line"]), "差异不得越过宣告行"
    assert loc["chars_from_line"] >= len(p1) - n, "必然损失的内容 >= 差异点之后的全部内容"
    


def test_宣告行内容只由下发集决定_下发集不同则行必不同():
    """B1 语义：该行渲染的是**本轮真正下发的 tool_defs**（同源，不是注册表全量）"""
    from agent.tools_prompt_guard import render_tool_advert_line

    d1 = [{"type": "function", "function": {"name": "read_file"}}]
    d2 = [{"type": "function", "function": {"name": "read_file"}},
          {"type": "function", "function": {"name": "write_file"}}]
    l1, l2 = render_tool_advert_line(d1), render_tool_advert_line(d2)

    assert ADVERT in l1 and ADVERT in l2
    assert l1 != l2, "下发集不同 ⇒ 宣告行必须不同（这正是 F3 风险的机制）"
    assert "1 个" in l1 and "2 个" in l2


# ── ③ 位置事实：稳定节、且其后确有内容 ─────────────────────────────────────

def test_配置模板把工具状态放在稳定节且其后仍有易变内容():
    """用**生产模板**（SystemPromptConfigManager.build_template）与**生产渲染器**
    （render_tool_advert_line）实测：宣告行落在稳定节，其后还有身体状态/日期/记忆。
    ⇒ 一旦该行逐请求变化，"损失面"是它之后的**全部**内容，不是只有它自己。
    """
    from dataclasses import asdict

    from agent.system_prompt_config import (SystemPromptConfigData,
                                            SystemPromptConfigManager)
    from agent.tools_prompt_guard import render_tool_advert_line

    template = SystemPromptConfigManager().build_template(
        asdict(SystemPromptConfigData()))
    tool_status = render_tool_advert_line(
        [{"type": "function", "function": {"name": "read_file"}}])
    prompt = template.format(
        current_date="2026年9月25日", body_status="CPU: 10%",
        mode_name="默认", mode_description="默认模式",
        memory_context="（记忆线索）", tool_status=tool_status,
        skill_instructions="（技能指令）")

    loc = probe.advert_location(prompt)
    assert loc["present"] is True

    idx_tool = prompt.index("## 当前工具与技能状态")
    idx_body = prompt.index("## 当前状态")
    idx_mem = prompt.index("## 记忆线索")
    assert idx_tool < idx_body < idx_mem, "工具状态（稳定节）必须在身体状态/记忆之前"
    assert loc["offset"] > idx_tool, "宣告行在工具状态节内部（节标题之后）"
    assert loc["chars_from_line"] > 0, "其后必须有内容，否则 F3 的风险面为 0"


# ── ③b 归组口径（探针的"该行是否逐字相同"判据依赖它）────────────────────

def test_按请求时间线归组_不会把上一组最后一条算进下一组():
    """本卡实测踩过的坑：按"组的开始时间 + 固定窗口"归组时，
    组 A 第 3 次请求的调用记录会被算进组 B（原始输出 ident=2 / vary=4，
    正确是 3 / 3），从而让"该行 3 次是否逐字相同"用错样本。
    时间线（请求级）归组是正确口径。
    """
    timeline = [
        (1, "q1", 100.0, 106.0),
        (2, "q2", 112.0, 118.0),
        (3, "q3", 124.0, 129.0),      # 第 3 次请求的窗口
        (4, "q4", 135.0, 141.0),      # 下一组的第 1 次请求
    ]
    recs = [
        {"timestamp": 104.0, "id": "a1"},
        {"timestamp": 116.0, "id": "a2"},
        {"timestamp": 128.0, "id": "a3"},   # 落在请求 3 的窗口内（旧口径会算给下一组）
        {"timestamp": 139.0, "id": "b1"},
        {"timestamp": 500.0, "id": "stray"},
    ]
    groups, unassigned = probe.assign_records_to_requests(recs, timeline)

    by_idx = {it[0]: [r["id"] for r in part] for it, part in groups}
    assert by_idx == {1: ["a1"], 2: ["a2"], 3: ["a3"], 4: ["b1"]}
    assert [r["id"] for r in unassigned] == ["stray"]


def test_归组对同一次请求的多次调用保持时间升序():
    """一次请求会发多次 LLM 调用（工具循环），组内必须按时间升序，
    否则 gap（调用间隔）算错。"""
    timeline = [(1, "q", 10.0, 30.0)]
    recs = [{"timestamp": 25.0, "id": "c"}, {"timestamp": 12.0, "id": "a"},
            {"timestamp": 19.0, "id": "b"}]
    groups, unassigned = probe.assign_records_to_requests(recs, timeline)

    assert [r["id"] for r in groups[0][1]] == ["a", "b", "c"]
    assert unassigned == []


def test_探针主流程离线跑通并对每组各归到3条记录(monkeypatch, capsys):
    """探针的**主流程**（含改造后的"按请求时间线归组"）在无服务、无出网的情况下跑通。

    这不是重复测试：探针脚本本身是交付物，而它面向真实服务的分支代码
    （wait_ready / metrics_read / _post_json / fetch_records）一旦改坏，
    只有再花一次真实出网预算才会暴露。这里用打桩把这四个边界换掉，
    把"流程能跑通 + 归组正确 + 报告字段齐全"变成一条**离线**回归。
    """
    import time as _t

    class _FakeTime:
        """只替换**探针模块**看到的 time（不动全局 time.time，避免影响 pytest 自身计时）。

        每次取时间前进 30 秒 ⇒ 各请求的窗口互不重叠，模拟真实时序
        （真实运行里请求相隔数秒，窗口天然分离）。
        """
        def __init__(self, real):
            self._real = real
            self.t = 1_700_000_000.0

        def time(self):
            self.t += 30.0
            return self.t

        def sleep(self, _s):
            return None

        def strftime(self, *a, **k):
            return self._real.strftime(*a, **k)

    emitted = []

    def fake_wait_ready(base_url, timeout_s=180.0):
        return True, 0.0, "http=200(打桩)"

    def fake_metrics(base_url):
        return {"status": 200, "lines": {"cache_hit_ratio": "cache_hit_ratio 0.5"}}

    def fake_post(url, payload, timeout=300):
        # 每次请求产生 1 条监控记录，时间戳=此刻（必然落在该请求的窗口内）
        emitted.append({
            "timestamp": probe.time.time(), "timestamp_full": "2026-09-25 00:00:00",
            "session_id": "", "source": "tool_calling", "model": "stub",
            "system_prompt": "身份\n" + ADVERT + "本轮向模型下发(1 个): a\n身体状态",
            "tools": [{"type": "function", "function": {"name": "a"}}],
            "usage_available": True, "usage_prompt_tokens": 100,
            "prompt_cache_hit_tokens": 64, "prompt_cache_miss_tokens": 36,
            "cache_reported": True,
            "messages": [{"role": "user", "content": str(payload.get("message", ""))}],
        })
        return 200, '{"response": "stub"}'

    def fake_records(base_url, limit=200):
        return list(emitted)

    monkeypatch.setattr(probe, "time", _FakeTime(_t))
    monkeypatch.setattr(probe, "wait_ready", fake_wait_ready)
    monkeypatch.setattr(probe, "metrics_read", fake_metrics)
    monkeypatch.setattr(probe, "_post_json", fake_post)
    monkeypatch.setattr(probe, "fetch_records", fake_records)

    rc = probe.main(["--cases", "ident,vary", "--interval", "0", "--tag", "offline"])
    out = capsys.readouterr().out

    assert rc == 0
    # 每组恰好归到**本组**的 3 条（其余组的记录算"未归属"，不会串进本组）
    assert "组 ident（session=f3-ident-offline）：命中 3 条监控记录" in out
    assert "组 vary（session=f3-vary-offline）：命中 3 条监控记录" in out
    assert "未归属 3 条" in out
    # 两次运行都用同一个（逐字相同的）宣告行
    assert out.count("宣告行去重后 1 种；逐字相同=是") == 2
    # 偏移/其后内容/命中率这些裁决要用的字段必须真的印出来了
    assert out.count("advert_present=True advert_off=") == 6
    assert "Sigma hit/(hit+miss)" in out


# ── ④ 工作台 SSE 路径的事实：提示词里**没有**这一行 ────────────────────────

def _chat_src() -> str:
    p = _ROOT / "plugins" / "chat.py"
    assert p.exists()
    return p.read_text(encoding="utf-8")


def test_工作台SSE的system_prompt不含工具宣告行():
    """工作台 SSE（plugins/chat.py::_workbench_real_stream）用的是**字面量**
    SYSTEM_PROMPT，既不调用 _build_tool_status_text，也不含 【工具】 标记。
    ⇒ F3 担心的"该行逐请求变"在这条链路上**根本不成立**（不是"变化很小"，是没有这一行）。
    """
    src = _chat_src()
    seg = src[src.index('SYSTEM_PROMPT = "你是云枢'):]
    seg = seg[:seg.index("\n")]
    assert ADVERT not in seg, "工作台提示词不该有工具宣告行（若改成有，请重做 F3 裁决）"
    assert "_build_tool_status_text" not in src, (
        "工作台若开始渲染工具状态文本，F3 的 (b) 结论需重做")


def test_工作台SSE的工具选择作用于tools而非提示词():
    """hybrid_select_tools(question) 的结果进的是 _get_defs(whitelist=...)，
    即请求体的 tools= 字段；提示词侧不随 question 变化。
    """
    src = _chat_src()
    assert "_smart = hybrid_select_tools(question) or get_tools_for_input(question)" in src
    assert "_whitelist = _smart" in src
    assert "tool_defs = _get_defs(whitelist=_whitelist)" in src
