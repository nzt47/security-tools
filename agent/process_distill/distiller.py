"""并行子代理蒸馏 — 每条素材派一个隔离 LLM worker 提取可复现步骤序列。

子代理语义：worker = 独立 LLM 调用（线程池并行），各自拿到一条素材的
只读快照与独立上下文，只回传结构化 JSON 结果——主代理只消费合并产物，
不共享上下文（同 agent/subagent 的隔离思想；本模块是"真执行"版本，
不依赖 subagent/container.py 的占位骨架）。

降级铁律：
    - LLM 缺失/调用失败/JSON 解析失败 → 该素材退回规则提取骨架
      （extract_rule_steps），绝不抛异常；
    - 仅参数错误（无素材、无 llm 且无可降级内容）抛 ValueError。
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re
from typing import Any, Dict, List, Optional

from agent.process_distill.models import DistillMaterial
from agent.process_distill.prompts import (
    DISTILL_SYSTEM_PROMPT,
    DISTILL_USER_TEMPLATE,
    build_tool_hint,
    extract_rule_steps,
)

logger = logging.getLogger(__name__)

# 素材超过该长度直接规则提取（防 prompt 爆炸）
RULE_ONLY_CHARS = 40000
# 单素材 LLM 超时（秒）
_LLM_TIMEOUT = 120


def _strip_md_fence(text: str) -> str:
    """去掉 ```json ... ``` 围栏后返回。"""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s


def _parse_worker_json(raw: str) -> Dict[str, Any]:
    """解析 worker 输出 JSON；容忍围栏/前后杂文。失败抛 ValueError。"""
    s = _strip_md_fence(str(raw))
    # 取第一个 { 到最后一个 }
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM 输出中无 JSON 对象")
    data = json.loads(s[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM 输出不是 JSON 对象")
    return data


def _clean_step(raw: Any, seq: int, source: str) -> Optional[Dict[str, Any]]:
    """把 worker 返回的单步原始 dict 清洗为规范步骤 dict。"""
    if not isinstance(raw, dict):
        return None
    action = str(raw.get("action") or "").strip()
    if not action or len(action) < 2:
        return None
    step: Dict[str, Any] = {
        "seq": seq,
        "action": action[:500],
        "source": source,
        "confidence": 1.0,
    }
    tool = str(raw.get("tool") or "").strip()
    if tool and tool != "none":
        step["tool"] = tool
        params = raw.get("params")
        step["params"] = params if isinstance(params, dict) else {}
    cond = str(raw.get("condition") or "").strip()
    if cond:
        step["condition"] = cond[:300]
    note = str(raw.get("note") or "").strip()
    if note:
        step["note"] = note[:500]
    return step


def _steps_from_payload(payload: Dict[str, Any],
                        source: str) -> List[Dict[str, Any]]:
    """payload（worker JSON）→ 规范步骤列表。"""
    steps: List[Dict[str, Any]] = []
    raw_steps = payload.get("steps") or []
    if isinstance(raw_steps, dict):  # 容忍 {1: ..., 2: ...} 形式
        raw_steps = [v for _, v in sorted(raw_steps.items(),
                                          key=lambda kv: str(kv[0]))]
    if not isinstance(raw_steps, list):
        return steps
    for i, rs in enumerate(raw_steps, start=1):
        step = _clean_step(rs, i, source)
        if step:
            steps.append(step)
    return steps


def _rule_fallback(material: DistillMaterial) -> Dict[str, Any]:
    """规则提取降级：从素材编号行 → 骨架步骤（tool 全空）。"""
    raw_steps = extract_rule_steps(material.content, source=material.id)
    desc = material.description or f"（规则提取降级，来源 {material.id}）"
    return {
        "name": (material.title or material.id)[:200],
        "description": desc[:300],
        "steps": [
            {"seq": i, "action": s.get("action", ""), "source": material.id,
             "confidence": 1.0}
            for i, s in enumerate(raw_steps, start=1)
        ],
        "expected_output": "",
        "trigger_patterns": [],
        "_method": "rule",
    }


# ═══════════════════════════════════════════════════════════════
#  worker（单素材蒸馏；可被线程池并行）
# ═══════════════════════════════════════════════════════════════

def extract_text_steps(text: str, source: str = "") -> List[Dict[str, Any]]:
    """从 LLM 返回的编号文本（如 "1. **轮询空闲窗口**：…"）提取步骤骨架。

    用于模型未服从 JSON 指令但给出了编号步骤文本的中间情形——
    优于直接规则降级（保留了 LLM 的提炼质量，仅丢失结构化字段）。
    """
    steps: List[Dict[str, Any]] = []
    marker = re.compile(
        r"^\s*(?:[-*]|\d+[.、)]|步骤\s*\d+)\s*[:：]?\s*(.+?)\s*$")
    for line in (text or "").splitlines():
        m = marker.match(line)
        if not m:
            continue
        action = m.group(1).strip()
        # 去掉 markdown 加粗/链接标记，取纯文本
        action = re.sub(r"\*\*(.+?)\*\*", r"\1", action)
        action = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", action)
        action = action.strip("`#* ")
        if not action or len(action) < 3:
            continue
        steps.append({"seq": len(steps) + 1, "action": action[:500],
                      "source": source, "confidence": 1.0})
        if len(steps) >= 50:
            break
    return steps

# LLM 空响应/坏 JSON 的内部重试次数与退避（实测 deepseek 端点间歇性
# 空响应约 25%，重试可将其降到 <2%；LLMService.chat 本身不重试空响应，
# 属公共组件，职责上由本蒸馏层吸收）
_LLM_RETRIES = 3
_LLM_RETRY_DELAY = 1.0


def _call_llm_with_retry(llm: Any, user_prompt: str) -> str:
    """调用 llm.chat，空响应/异常时按指数退避重试。

    返回最后一次的原始输出（可能为空串，由调用方判定降级）。
    """
    import time as _time

    last_raw = ""
    for attempt in range(_LLM_RETRIES):
        try:
            raw = llm.chat(
                [{"role": "user", "content": user_prompt}],
                system_prompt=DISTILL_SYSTEM_PROMPT,
            )
            raw = (raw or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.warning("[PD] 素材 LLM 调用异常(第 %d/%d 次): %s",
                           attempt + 1, _LLM_RETRIES, e)
            raw = ""
        if raw:
            return raw
        if attempt < _LLM_RETRIES - 1:
            _time.sleep(_LLM_RETRY_DELAY * (attempt + 1))
        last_raw = raw
    return last_raw


def distill_one(material: DistillMaterial, llm: Any,
                tool_hint: str = "") -> Dict[str, Any]:
    """蒸馏一条素材，返回 {material_id, ok, method, payload}。

    llm 为 None → 规则降级；LLM 空响应/坏 JSON（重试后）→ 规则降级
    （不抛异常）。
    """
    if llm is None or len(material.content) > RULE_ONLY_CHARS:
        payload = _rule_fallback(material)
        return {"material_id": material.id, "ok": True,
                "method": "rule", "payload": payload}
    user_prompt = DISTILL_USER_TEMPLATE.format(
        title=material.title,
        source=material.source_ref or material.id,
        tool_hint=tool_hint or "（未提供）",
        content=material.content[:20000],
    )
    try:
        raw = _call_llm_with_retry(llm, user_prompt)
        if not raw:
            raise ValueError("LLM 连续空响应（重试 %d 次后仍为空）"
                             % _LLM_RETRIES)
        payload = _parse_worker_json(raw)
        steps = _steps_from_payload(payload, material.id)
        payload["steps"] = steps
        payload.setdefault("name", material.title[:200])
        payload.setdefault("_method", "llm")
        if not steps:
            # LLM 成功但没提取到结构化步骤 → 尝试文本步骤，再无则规则降级
            text_steps = extract_text_steps(raw, source=material.id)
            if text_steps:
                payload["steps"] = text_steps
                payload["_method"] = "llm_text"
            else:
                payload = _rule_fallback(material)
                payload["_method"] = "llm_fallback_rule"
        return {"material_id": material.id, "ok": True,
                "method": "llm", "payload": payload}
    except Exception as e:  # noqa: BLE001  降级铁律
        logger.warning("[PD] 素材 %s LLM 蒸馏失败，规则降级: %s",
                       material.id, e)
        payload = _rule_fallback(material)
        return {"material_id": material.id, "ok": True,
                "method": "rule", "payload": payload,
                "warning": str(e)[:300]}


def distill_parallel(materials: List[DistillMaterial], llm: Any,
                     available_tools: Optional[List[str]] = None,
                     max_workers: int = 4,
                     ) -> Dict[str, Any]:
    """并行蒸馏全部素材。

    Returns: {results: [...], method_summary: {llm, rule}, warnings: [...]}
    """
    if not materials:
        raise ValueError("无可蒸馏素材")
    tool_hint = build_tool_hint(available_tools or [])
    workers = max(1, min(int(max_workers), len(materials), 16))

    results: List[Dict[str, Any]] = []
    if workers == 1:
        for m in materials:
            results.append(distill_one(m, llm, tool_hint))
    else:
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="pd-distill") as pool:
            futs = {pool.submit(distill_one, m, llm, tool_hint): m
                    for m in materials}
            for fut in concurrent.futures.as_completed(futs):
                try:
                    results.append(fut.result())
                except Exception as e:  # noqa: BLE001  线程级兜底
                    m = futs[fut]
                    payload = _rule_fallback(m)
                    results.append({
                        "material_id": m.id, "ok": True,
                        "method": "rule", "payload": payload,
                        "warning": str(e)[:300],
                    })
    # 保序（按素材出现顺序）
    order = {m.id: i for i, m in enumerate(materials)}
    results.sort(key=lambda r: order.get(r.get("material_id"), 0))

    summary = {"llm": 0, "rule": 0}
    warnings: List[str] = []
    for r in results:
        summary[r.get("method", "rule")] = summary.get(r.get("method", "rule"), 0) + 1
        if r.get("warning"):
            warnings.append(f"{r['material_id']}: {r['warning']}")
    return {"results": results, "method_summary": summary,
            "warnings": warnings}
