"""LLM 输出 JSON 提取与校验（阶段 2 鲁棒性升级）

支持：markdown 围栏 / 裸 JSON / 前后噪音剥离；失败附错误反馈重试 1 次；
仍失败由调用方回退规则分解。轻量手写校验（避免新增 jsonschema 重依赖）。

【不易】提取算法与阶段 1 的 decomposer._extract_json_from_response 等价
（围栏优先 → 大括号区间扫描），行为向后兼容。
【变易】extract_json / validate_subtasks 解耦为公共纯函数，供 decomposer、
  reflector 等任意 LLM 输出解析场景复用。
【简易】纯函数 + 显式错误列表，无隐藏副作用。
"""

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```")


def extract_json(response: str) -> Optional[Dict[str, Any]]:
    """从 LLM 响应中提取 JSON 对象。

    依次尝试：markdown 围栏 → 大括号区间扫描（剥离开头/结尾噪音）→ 数组兜底。

    Returns:
        成功解析返回 dict；失败返回 None（由调用方决定重试/回退）。
    """
    if not response:
        return None

    # 1. markdown 围栏（```json ... ``` / ``` ... ```）
    m = _FENCE_RE.search(response)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 2. 大括号区间扫描：从第一个 { 起，逐个 } 结尾尝试（剥离前后噪音文本）
    brace_start = response.find("{")
    if brace_start != -1:
        for brace_end in range(len(response) - 1, brace_start, -1):
            if response[brace_end] != "}":
                continue
            try:
                return json.loads(response[brace_start:brace_end + 1])
            except json.JSONDecodeError:
                continue

    # 3. 数组兜底（部分模型返回 JSON 数组）
    bracket_start = response.find("[")
    if bracket_start != -1:
        for bracket_end in range(len(response) - 1, bracket_start, -1):
            if response[bracket_end] != "]":
                continue
            try:
                data = json.loads(response[bracket_start:bracket_end + 1])
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                continue

    return None


def validate_subtasks(data: Dict[str, Any]) -> List[str]:
    """轻量校验分解结果结构，返回错误消息列表（空列表 = 通过）。

    检查：subtasks 为列表、每项含 id/description 且非空。
    """
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["解析结果不是 JSON 对象"]
    subtasks = data.get("subtasks")
    if not isinstance(subtasks, list):
        return ["缺少 subtasks 数组"]
    for i, item in enumerate(subtasks):
        if not isinstance(item, dict):
            errors.append(f"subtasks[{i}] 不是对象")
            continue
        if not item.get("id"):
            errors.append(f"subtasks[{i}] 缺少 id")
        if not item.get("description") or not str(item.get("description", "")).strip():
            errors.append(f"subtasks[{i}] 缺少 description")
    return errors


async def extract_json_with_retry(
    response: str,
    llm,
    prompt_builder: Callable[[List[str]], str],
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """提取并校验；失败自动重试 1 次（附错误反馈），仍失败返回 None + 错误。

    Args:
        response: 首次 LLM 原始响应
        llm: 支持 async chat([{"role": "user", "content": prompt}]) 的 LLM 服务
        prompt_builder: 回调（接收错误列表）→ 构造带错误反馈的重试提示词

    Returns:
        (解析成功且校验通过的数据, [])；或 (None, 错误列表) 表示仍需回退。
    """
    data = extract_json(response)
    if data is not None:
        errors = validate_subtasks(data)
        if not errors:
            return data, []
    else:
        errors = ["JSON 解析失败"]

    # 重试 1 次，附错误反馈提示（修正 LLM 输出格式）
    try:
        retry_prompt = prompt_builder(errors)
        retry_response = await llm.chat([{"role": "user", "content": retry_prompt}])
        data = extract_json(retry_response)
        if data is not None:
            errors2 = validate_subtasks(data)
            if not errors2:
                return data, []
            errors = errors2
    except Exception as e:
        errors.append(f"重试调用失败: {e}")
        logger.warning(f"[llm_json] 重试解析失败: {e}")

    return None, errors
