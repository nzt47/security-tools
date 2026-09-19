"""LLM API Key 形态判定（单一来源）

【为什么单独成模块】该判定被两处使用，且必须口径一致：
  - 对话流 `plugins/chat.py::_workbench_real_stream`：key 形态不合格 ⇒ 走**演示模式**
    （固定文案），而不是发起一次注定 401 的请求；
  - 诊断自检 `agent/server_routes/routes_logging.py::/api/diagnostics/llm-check`：
    要告诉用户"你的 key 会不会让对话进演示模式"。

【为什么放在 agent/ 而不是 plugins/】依赖方向：`agent/` 是被 `plugins/` 依赖的下层，
反向导入（agent → plugins）会造成分层倒置与潜在循环导入。此处只依赖标准库，
故可被两侧安全共用（原先该判定内联在 plugins/chat.py 的生成器里，无法被复用）。
"""

#: 最低长度：短于此长度的 key 一定是占位/误配（真实 key 通常 32+ 字符）
MIN_API_KEY_LENGTH = 15

#: 常见占位前缀（历史故障：.env 里留下 sk-test… 导致"对话像自说自话"）
PLACEHOLDER_PREFIXES = ("sk-test", "sk-old", "test", "sk-invalid")


def key_usable(api_key: str) -> bool:
    """key 是否"看起来可用"（形态判定，不发起任何网络请求）

    Args:
        api_key: 待判定的 key（None/空串 ⇒ 不可用）

    Returns:
        True 表示形态合格（可发起真实调用）；False 表示应走演示模式/明确报错。
    """
    if not api_key:
        return False
    if len(api_key) < MIN_API_KEY_LENGTH:
        return False
    if api_key.startswith(PLACEHOLDER_PREFIXES):
        return False
    return True
