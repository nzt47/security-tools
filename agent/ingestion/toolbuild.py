"""工具草稿 AI 实现（LLM 写代码 + 静态校验 + 登记为动态工具）。

背景：推荐形态=tool 时，吞噬只生成骨架（NotImplementedError）。本模块让云枢把骨架补成
可注册的工具：读取来源 SKILL.md / 脚本 / 骨架，要求 LLM 输出完整实现，做静态校验后写入
草稿；登记复用现有动态工具链路（tool_generator.generate_persistent + persist_dynamic_tool），
登记后重启仍在，并沿用风险门控。

安全边界：
- 只写草稿目录 / 动态工具目录；不改审批策略、不自动调用；
- 静态校验不过就拒绝登记（语法、顶层语句、TOOL_SCHEMA、危险调用、硬编码密钥）；
- 登记时的 exec 只定义函数（generate_persistent 自带 AST + 子进程超时校验）。
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import drafts

MAX_PROMPT_CHARS = 32000
MAX_TOKENS = 4000
MAX_SCRIPT_CHARS = 6000

FORBIDDEN_SNIPPETS = ("eval(", "exec(", "__import__", "shell=True", "os.system(", "pickle.loads")
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{12,}|password\s*=\s*['\"])")

SYSTEM_PROMPT = (
    "你是资深 Python 工具工程师。根据给定材料实现一个云枢工具，只输出一个 Python 代码块。"
    "不得编造材料里没有的 API 端点或命令；不确定的细节在 docstring 里标注；不得硬编码密钥。"
)


class BuildError(Exception):
    """实现/登记失败（输入不合法 / 模型输出不可用 / 校验不过）。"""

    def __init__(self, message: str, *, status: int = 400, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status = int(status)
        self.detail = detail or message


def _call_llm(client: Any, prompt: str) -> str:
    messages = [{"role": "user", "content": prompt}]
    if hasattr(client, "chat"):
        try:
            return str(client.chat(messages, system_prompt=SYSTEM_PROMPT,
                                   max_tokens=MAX_TOKENS, temperature=0.1))
        except TypeError:
            pass
        try:
            return str(client.chat(prompt, max_tokens=MAX_TOKENS))
        except TypeError:
            pass
        return str(client.chat(messages))
    return str(client.complete(prompt))


def load_materials(draft_id: str, *, draft_dir: Any = None) -> Dict[str, Any]:
    """读取草稿 + 来源材料（SKILL.md / scripts / 现有骨架）。"""
    data = drafts.load_manifest(draft_id, draft_dir=draft_dir)
    root, manifest = data["root"], data["manifest"]
    name = str(manifest.get("skill_id") or "")
    if not name:
        raise BuildError("草稿缺少 skill_id，无法实现")
    skill_md = ""
    scripts: List[Tuple[str, str]] = []
    ref = str(manifest.get("source_ref") or "")
    if ref.startswith("path:"):
        src = Path(ref[5:].strip())
        md = src / "SKILL.md"
        if md.is_file():
            skill_md = md.read_text(encoding="utf-8", errors="replace")
        sdir = src / "scripts"
        if sdir.is_dir():
            for f in sorted(sdir.glob("*")):
                if f.is_file():
                    try:
                        scripts.append((f.name, f.read_text(encoding="utf-8", errors="replace")))
                    except OSError:
                        continue
    if not scripts:  # 草稿内复制的来源脚本兜底
        sdir = root / "source" / "scripts"
        if sdir.is_dir():
            for f in sorted(sdir.glob("*")):
                if f.is_file():
                    try:
                        scripts.append((f.name, f.read_text(encoding="utf-8", errors="replace")))
                    except OSError:
                        continue
    tool_file = root / "agent" / "tools" / (name + ".py")
    skeleton = tool_file.read_text(encoding="utf-8", errors="replace") if tool_file.is_file() else ""
    return {"root": root, "manifest": manifest, "name": name,
            "description": str(manifest.get("skill_name") or name),
            "skill_md": skill_md, "scripts": scripts, "skeleton": skeleton}


def build_prompt(materials: Dict[str, Any]) -> str:
    name = materials["name"]
    parts = [
        "把下面工具骨架补成可用的 Python 实现。\n",
        "工具名：" + name + "\n",
        "说明：" + str(materials.get("description") or "") + "\n\n",
    ]
    if materials.get("skill_md"):
        parts.append("### 来源 SKILL.md\n" + str(materials["skill_md"])[:12000] + "\n\n")
    for fname, text in materials.get("scripts") or []:
        parts.append("### 来源脚本 " + fname + "\n~~~\n" + str(text)[:MAX_SCRIPT_CHARS] + "\n~~~\n\n")
    if materials.get("skeleton"):
        parts.append("### 现有骨架（含 TOOL_SCHEMA 草案）\n~~~python\n"
                     + str(materials["skeleton"])[:4000] + "\n~~~\n\n")
    parts.append(
        "要求：\n"
        "1. 只输出一个 python 代码块，不要解释；\n"
        "2. 顶层只允许 import / 简单赋值 / 函数定义；不要 from __future__；\n"
        "3. 必须定义 TOOL_SCHEMA（type=object，properties 写出真实参数与说明，required 合理）；\n"
        "4. 必须定义函数 " + name + "(**kwargs) -> dict，缺参返回 {\"ok\": false, \"error\": \"...\"}；\n"
        "5. 返回统一结构 {\"ok\": bool, \"data\": ..., \"error\": \"\"}；不要 print / sys.exit；\n"
        "6. 优先调用来源脚本（subprocess 列表参数、shell=False、timeout），"
        "或使用材料中出现的 HTTP 端点/签名；材料里没有的 API 不要编造；\n"
        "7. 凭据只从环境变量（BYTEPLUS_ACCESS_KEY / BYTEPLUS_SECRET_KEY）或 secrets 文件读，"
        "禁止写进代码；网络调用必须设 timeout；\n"
        "8. 对写操作/部署类动作，先校验输入（如目录存在、含 index.html），失败要回可读错误。\n"
    )
    prompt = "".join(parts)
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS]
    return prompt


def _extract_code(text: str) -> str:
    raw = str(text or "").strip()
    m = re.search(r"~~~(?:python)?\s*(.*?)~~~", raw, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"\x60\x60\x60(?:python)?\s*(.*?)\x60\x60\x60", raw, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return raw


def validate_code(code: str, name: str) -> Dict[str, Any]:
    """静态校验：语法 / 顶层语句 / 函数 / TOOL_SCHEMA / 危险调用 / 密钥。"""
    errors: List[str] = []
    warnings: List[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {"ok": False, "errors": ["语法错误: " + str(exc)], "warnings": [], "schema": None}
    allowed = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
               ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign)
    for node in tree.body:
        if isinstance(node, allowed):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        errors.append("不允许的顶层语句: " + type(node).__name__)
    if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
               for n in tree.body):
        errors.append("缺少函数 " + name)
    schema = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "TOOL_SCHEMA":
                    try:
                        schema = ast.literal_eval(node.value)
                    except Exception:  # noqa: BLE001
                        errors.append("TOOL_SCHEMA 必须是字面量 dict")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        errors.append("TOOL_SCHEMA 必须是 type=object 的对象")
    else:
        props = schema.get("properties")
        if not isinstance(props, dict) or not props:
            errors.append("TOOL_SCHEMA.properties 不能为空（要写出真实参数）")
        required = schema.get("required")
        if required and not set(required) <= set((props or {}).keys()):
            errors.append("TOOL_SCHEMA.required 必须都是 properties 里的键")
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and str(node.module or "") == "__future__":
            errors.append("不要使用 from __future__（代码会被包进 register_all 内）")
    for bad in FORBIDDEN_SNIPPETS:
        if bad in code:
            errors.append("禁止使用 " + bad)
    if SECRET_RE.search(code):
        errors.append("疑似硬编码密钥（凭据必须走环境变量/配置文件）")
    if "NotImplementedError" in code:
        errors.append("实现里仍有 NotImplementedError")
    if not errors and len(code) < 200:
        warnings.append("实现偏短，建议复核是否只做了占位返回")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "schema": schema}


def build(draft_id: str, llm: Any, *, draft_dir: Any = None) -> Dict[str, Any]:
    """调用 LLM 生成实现（不写盘）。"""
    if llm is None:
        raise BuildError("未配置 LLM：无法自动实现工具", status=503)
    materials = load_materials(draft_id, draft_dir=draft_dir)
    prompt = build_prompt(materials)
    if len(prompt) > MAX_PROMPT_CHARS:
        raise BuildError("材料过长（> " + str(MAX_PROMPT_CHARS) + " 字符），请精简来源脚本")
    try:
        text = _call_llm(llm, prompt)
    except Exception as exc:  # noqa: BLE001
        raise BuildError("LLM 调用失败", status=502, detail=type(exc).__name__) from exc
    code = _extract_code(text)
    validation = validate_code(code, materials["name"])
    try:
        from . import rewrite
        model = rewrite.model_name(llm)
    except Exception:  # noqa: BLE001
        model = ""
    return {"code": code, "validation": validation, "model": model,
            "prompt_chars": len(prompt)}


def apply(draft_id: str, code: str, validation: Dict[str, Any], *,
          draft_dir: Any = None) -> Dict[str, Any]:
    """把实现写回草稿（工具文件 / YAML schema / 测试骨架 / manifest 警告）。"""
    data = drafts.load_manifest(draft_id, draft_dir=draft_dir)
    root, manifest = data["root"], data["manifest"]
    name = str(manifest.get("skill_id") or "")
    if not name:
        raise BuildError("草稿缺少 skill_id")
    (root / "agent" / "tools" / (name + ".py")).write_text(code.rstrip() + "\n", encoding="utf-8")
    schema = validation.get("schema") or {"type": "object", "properties": {}}
    yaml_path = root / "data" / "tool_definitions" / (name + ".yaml")
    if yaml_path.is_file():
        import yaml
        meta = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        meta["schema"] = schema
        meta["version"] = "1.0.0"
        meta["tags"] = sorted(set(list(meta.get("tags") or []) + ["ai_implemented"]))
        yaml_path.write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False),
                             encoding="utf-8")
    test_py = (
        "import pytest\n"
        "from agent.tools." + name + " import " + name + ", TOOL_SCHEMA\n\n\n"
        "def test_tool_schema_is_object():\n"
        "    assert TOOL_SCHEMA.get('type') == 'object'\n"
        "    assert TOOL_SCHEMA.get('properties')\n\n\n"
        "@pytest.mark.skip(reason='AI 实现：真实调用依赖外部服务/凭据，需人工启用后联调')\n"
        "def test_tool_behavior():\n"
        "    assert " + name + "() is not None\n"
    )
    (root / "tests" / "unit" / ("test_" + name + ".py")).write_text(test_py, encoding="utf-8")
    ok = bool(validation.get("ok"))
    manifest["warnings"] = (
        ["AI 已生成实现并通过静态校验；登记后才进入工具注册表（风险门控默认 critical）。",
         "测试中的真实调用被跳过：需要凭据/外部环境，启用前请先做一次受控联调。"]
        if ok else ["AI 生成实现未通过静态校验：" + "；".join(validation.get("errors") or [])[:300]]
    )
    manifest["implemented"] = True
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"draft_dir": str(root), "tool_file": str(root / "agent" / "tools" / (name + ".py")),
            "ok": ok}


def register(draft_id: str, *, draft_dir: Any = None, generator: Any = None) -> Dict[str, Any]:
    """登记为动态工具（写 custom/ + 台账 + 治理 YAML），失败抛 BuildError。"""
    data = drafts.load_manifest(draft_id, draft_dir=draft_dir)
    root, manifest = data["root"], data["manifest"]
    name = str(manifest.get("skill_id") or "")
    tool_file = root / "agent" / "tools" / (name + ".py")
    if not tool_file.is_file():
        raise BuildError("还没有实现文件，先生成实现")
    code = tool_file.read_text(encoding="utf-8", errors="replace")
    validation = validate_code(code, name)
    if not validation.get("ok"):
        raise BuildError("实现未通过静态校验，拒绝登记",
                         detail="；".join(validation.get("errors") or []))
    schema = validation.get("schema") or {}
    description = str(manifest.get("skill_name") or name)
    if generator is None:
        from agent.tools.tool_generator import ToolGenEngine
        generator = ToolGenEngine()
    ok = bool(generator.generate_persistent(name, description, code, schema, category="ingested"))
    if not ok:
        raise BuildError("动态工具注册失败（AST/子进程校验未通过）", status=500)
    from agent.tools.persistence import persist_dynamic_tool
    persist_dynamic_tool(name, description, schema, module_path=str(tool_file),
                         source="ingestion", category="ingested", verify=False)
    return {"tool_name": name, "module": str(tool_file), "schema": schema,
            "validation": validation}
