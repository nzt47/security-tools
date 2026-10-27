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
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import drafts

MAX_PROMPT_CHARS = 32000
# deepseek-flash 先消耗推理 token：实测 4000 时 content 为空、16000 被截断；32000 才 finish=stop
MAX_TOKENS = 32000
MAX_SCRIPT_CHARS = 6000
#: 随工具落盘的来源脚本上限（.sh/.py 等；防止把产物目录/大文件带进仓）
MAX_SOURCE_SCRIPTS = 50
MAX_SOURCE_SCRIPT_BYTES = 2 * 1024 * 1024
#: 生成整段代码可能跑 1-3 分钟；LLMService 默认 30s 会超时（实测 502）
LLM_TIMEOUT_SEC = 600

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
    """长文本生成：优先直连底层 OpenAI 客户端（绕开 LLMService 的两个坑）。

    【为什么不用 client.chat】实测（2026-10-27）：
    1. `LLMService` 客户端 timeout 默认 30s，32k token 生成会超时 → LLMServiceError；
    2. `_do_chat` 直接 `message.content.strip()`，content=None（token 全被 reasoning
       吃掉）时抛 AttributeError，同样变成 LLMServiceError。
    这里在**不修改共享 LLMService** 的前提下，用它的底层客户端 + 600s 超时；失败再回退
    chat()（保持对非 OpenAI 兼容后端的兼容）。
    """
    messages = [{"role": "user", "content": prompt}]
    try:
        if (hasattr(client, "_get_client") and hasattr(client, "model")
                and hasattr(client, "_is_openai_compat") and client._is_openai_compat()):
            raw = client._get_client().chat.completions.create(  # noqa: SLF001 仅取底层客户端
                model=client.model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
                max_tokens=MAX_TOKENS,
                temperature=0.1,
                timeout=LLM_TIMEOUT_SEC,
            )
            return str(raw.choices[0].message.content or "")
    except Exception:  # noqa: BLE001 回退到通用 chat
        pass
    if hasattr(client, "chat"):
        try:
            return str(client.chat(messages, system_prompt=SYSTEM_PROMPT,
                                   max_tokens=MAX_TOKENS, temperature=0.1) or "")
        except TypeError:
            pass
        try:
            return str(client.chat(prompt, max_tokens=MAX_TOKENS) or "")
        except TypeError:
            pass
        return str(client.chat(messages) or "")
    return str(client.complete(prompt) or "")


def safe_name(raw: str) -> str:
    """把 skill_id（可能含 - . 等）归一成合法 Python 标识符（与 drafts._tool_name 同口径）。"""
    base = re.sub(r"[^0-9A-Za-z_]", "_", str(raw or "").strip())
    base = re.sub(r"_+", "_", base).strip("_") or "ingested_tool"
    return base if base[0].isalpha() else "t_" + base


def _safe_dir_name(raw: str) -> str:
    """来源 id → 安全目录名（只留字母数字与 _- .，拒掉路径分隔符与 ..）。"""
    name = re.sub(r"[^0-9A-Za-z_.-]", "_", str(raw or "").strip())
    name = name.replace("..", "_").strip("._-")
    return name or "ingested_source"


def source_script_files(root: Path, manifest: Dict[str, Any]) -> List[Path]:
    """草稿的**来源脚本**：优先 manifest.source_ref 指向的来源目录，其次草稿内 source/scripts。

    与 load_materials 同源同序（先 sdir.glob → 再草稿兜底），保证"喂给 LLM 的脚本"
    和"登记时落盘的脚本"是同一批。
    """
    dirs: List[Path] = []
    ref = str(manifest.get("source_ref") or "")
    if ref.startswith("path:"):
        dirs.append(Path(ref[5:].strip()) / "scripts")
    dirs.append(Path(root) / "source" / "scripts")
    for sdir in dirs:
        if not sdir.is_dir():
            continue
        files = [f for f in sorted(sdir.iterdir()) if f.is_file() and not f.name.startswith(".")]
        if files:
            return files
    return []


def copy_source_scripts(root: Path, manifest: Dict[str, Any], *,
                        category: str = "ingested") -> List[str]:
    """把来源脚本复制到 custom/<category>/<source_id>/scripts/，返回可进台账的相对路径。

    【为什么需要】生成代码按「本文件所在目录上溯 + <source_id>/scripts」探测脚本；
    登记原先只写 <name>.py，换机/清空 custom 后工具会报「未找到 deploy.sh」。
    目标目录用**原始 skill_id**（可能带连字符），与生成代码的探测路径一致。
    """
    files = source_script_files(root, manifest)
    if not files:
        return []
    from agent.tools.persistence import CUSTOM_TOOLS_DIR

    source_id = _safe_dir_name(str(manifest.get("skill_id") or ""))
    target = Path(CUSTOM_TOOLS_DIR) / category / source_id / "scripts"
    target.mkdir(parents=True, exist_ok=True)
    repo_root = Path(CUSTOM_TOOLS_DIR).parents[2]
    out: List[str] = []
    for src in files[:MAX_SOURCE_SCRIPTS]:
        try:
            if src.stat().st_size > MAX_SOURCE_SCRIPT_BYTES:
                continue
            dst = target / src.name
            if not dst.is_file() or dst.read_bytes() != src.read_bytes():
                shutil.copyfile(src, dst)
        except OSError:
            continue
        try:
            out.append(dst.relative_to(repo_root).as_posix())
        except ValueError:
            out.append(str(dst))
    return out


def _yaml_description(path: Path) -> str:
    """读 YAML 里的 description（文件不存在/解析失败返回空串）。"""
    try:
        import yaml
        meta = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 缺 yaml / 坏文件都只影响说明，不该阻断登记
        return ""
    return str(meta.get("description") or "").strip() if isinstance(meta, dict) else ""


def _skill_md_description(manifest: Dict[str, Any]) -> str:
    """读来源 SKILL.md front matter 的 description（中文说明通常在这里）。"""
    ref = str(manifest.get("source_ref") or "")
    if not ref.startswith("path:"):
        return ""
    md = Path(ref[5:].strip()) / "SKILL.md"
    try:
        text = md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return ""
    try:
        import yaml
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(fm, dict):
        return ""
    return str(fm.get("description") or fm.get("description_zh") or "").strip()


def resolve_description(root: Path, manifest: Dict[str, Any], name: str) -> str:
    """登记用的工具说明：草稿工具 YAML → 来源 SKILL.md → skill_name。

    【为什么不直接用 skill_name】实测（2026-10-27，adaptive_skill_stack / byted_*）：
    吞噬在草稿的 data/tool_definitions/<name>.yaml 里已抄好 SKILL.md 的中文 description，
    而 register 只认 manifest.skill_name（= 英文 id）⇒ 工具页「说明」列显示
    `adaptive-skill-stack`、台账与治理 YAML 也跟着是英文 id。
    """
    for cand in (_yaml_description(root / "data" / "tool_definitions" / (name + ".yaml")),
                 _skill_md_description(manifest)):
        if cand:
            return cand
    return str(manifest.get("skill_name") or name)


def update_persisted_description(name: str, description: str) -> None:
    """把说明写回已落盘的 data/tool_definitions/<name>.yaml（只改 description）。

    【为什么必须单独写】`ensure_yaml_definition(overwrite=False)` 遇到已存在文件直接
    返回，重新登记时说明永远是第一次写进去的那个（英文 id）；只改这一个键，
    人工调过的 tags/plane 等其它声明保持不动。
    """
    try:
        import yaml
        from agent.tools.persistence import TOOL_DEFS_DIR
    except Exception:  # noqa: BLE001
        return
    path = Path(TOOL_DEFS_DIR) / (name + ".yaml")
    try:
        meta = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, yaml.YAMLError):
        return
    if not isinstance(meta, dict) or str(meta.get("description") or "") == description:
        return
    meta["description"] = description
    tmp = path.with_suffix(".yaml.tmp")
    try:
        tmp.write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def load_materials(draft_id: str, *, draft_dir: Any = None) -> Dict[str, Any]:
    """读取草稿 + 来源材料（SKILL.md / scripts / 现有骨架）。"""
    data = drafts.load_manifest(draft_id, draft_dir=draft_dir)
    root, manifest = data["root"], data["manifest"]
    raw_id = str(manifest.get("skill_id") or "")
    if not raw_id:
        raise BuildError("草稿缺少 skill_id，无法实现")
    name = safe_name(raw_id)
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
    tool_dir = root / "agent" / "tools"
    tool_file = tool_dir / (name + ".py")
    if not tool_file.is_file() and tool_dir.is_dir():
        candidates = sorted(tool_dir.glob("*.py"))
        if candidates:
            tool_file = candidates[0]
            name = tool_file.stem
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
        "3. 必须定义 TOOL_SCHEMA（type=object，properties 写出真实参数与说明，required 合理）；"
        "schema 必须是纯字面量（enum 直接写数组，不要引用常量）；\n"
        "4. 必须定义函数 " + name + "(**kwargs) -> dict，缺参返回 {\"ok\": false, \"error\": \"...\"}；\n"
        "5. 返回统一结构 {\"ok\": bool, \"data\": ..., \"error\": \"\"}；不要 print / sys.exit；\n"
        "6. 优先调用来源脚本（subprocess 列表参数、shell=False、timeout），"
        "或使用材料中出现的 HTTP 端点/签名；材料里没有的 API 不要编造；\n"
        "7. 凭据只从环境变量（BYTEPLUS_ACCESS_KEY / BYTEPLUS_SECRET_KEY）或 secrets 文件读，"
        "禁止写进代码；网络调用必须设 timeout；\n"
        "8. 对写操作/部署类动作，先校验输入（如目录存在、含 index.html），失败要回可读错误；\n"
        "9. 若来源脚本是 bash/sh：**Windows 上 bash 由 WSL 提供、看不到 C:\\ 绝对路径**"
        "（实测 exit=127 \"No such file or directory\"）⇒ 构造 argv 时必须走 "
        "agent.tools.win_bash.bash_argv([...]) 把 Windows 路径翻成 /mnt/<盘>/... "
        "（非 Windows 平台逐字返回，零影响）；另外**不要**指望环境变量跨进 WSL"
        "（AK/SK 要在 WSL 里 nest config set，或按来源脚本约定走 argv）。\n"
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


def _literal_with_names(node: ast.AST, env: Dict[str, Any]) -> Any:
    """安全求值"字面量 + 已知常量名"表达式（不执行代码）。"""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise ValueError("unknown name: " + node.id)
    if isinstance(node, ast.List):
        return [_literal_with_names(e, env) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_literal_with_names(e, env) for e in node.elts)
    if isinstance(node, ast.Dict):
        if any(k is None for k in node.keys):
            raise ValueError("dict unpacking not supported")
        return {_literal_with_names(k, env): _literal_with_names(v, env)
                for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _literal_with_names(node.operand, env)
        return -value if isinstance(node.op, ast.USub) else +value
    raise ValueError("not a literal: " + type(node).__name__)


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
    # 先收集"纯字面量"的模块常量（如 _ACTIONS = [...]），schema 里允许引用它们
    const_env: Dict[str, Any] = {}
    for _ in range(3):  # 允许常量互相引用，最多 3 层
        for node in tree.body:
            pairs = []
            if isinstance(node, ast.Assign):
                pairs = [(t.id, node.value) for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                pairs = [(node.target.id, node.value)]
            for const_name, value_node in pairs:
                if const_name in const_env or value_node is None:
                    continue
                try:
                    const_env[const_name] = _literal_with_names(value_node, const_env)
                except (ValueError, TypeError):
                    continue
    schema = None
    for node in tree.body:
        target_name = None
        value_node = None
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "TOOL_SCHEMA":
                    target_name = target.id
                    value_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == "TOOL_SCHEMA":
            target_name = node.target.id
            value_node = node.value
        if target_name and value_node is not None:
            try:
                schema = _literal_with_names(value_node, const_env)
            except (ValueError, TypeError):
                errors.append("TOOL_SCHEMA 必须是字面量 dict（可引用纯字面量常量）")
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
    if not code.strip():
        validation = {**validation, "ok": False,
                      "errors": list(validation.get("errors") or []) +
                      ["模型没有输出代码（若为推理模型，token 预算可能被 reasoning 占满）"]}
    try:
        from . import rewrite
        model = rewrite.model_name(llm)
    except Exception:  # noqa: BLE001
        model = ""
    return {"code": code, "validation": validation, "model": model,
            "prompt_chars": len(prompt), "raw_chars": len(text)}


def apply(draft_id: str, code: str, validation: Dict[str, Any], *,
          draft_dir: Any = None) -> Dict[str, Any]:
    """把实现写回草稿（工具文件 / YAML schema / 测试骨架 / manifest 警告）。"""
    data = drafts.load_manifest(draft_id, draft_dir=draft_dir)
    root, manifest = data["root"], data["manifest"]
    name = safe_name(str(manifest.get("skill_id") or ""))
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
    name = safe_name(str(manifest.get("skill_id") or ""))
    tool_file = root / "agent" / "tools" / (name + ".py")
    if not tool_file.is_file():
        candidates = sorted((root / "agent" / "tools").glob("*.py"))
        if candidates:
            tool_file = candidates[0]
            name = tool_file.stem
    if not tool_file.is_file():
        raise BuildError("还没有实现文件，先生成实现")
    code = tool_file.read_text(encoding="utf-8", errors="replace")
    validation = validate_code(code, name)
    if not validation.get("ok"):
        raise BuildError("实现未通过静态校验，拒绝登记",
                         detail="；".join(validation.get("errors") or []))
    schema = validation.get("schema") or {}
    description = resolve_description(root, manifest, name)
    if generator is None:
        from agent.tools.tool_generator import ToolGenEngine
        generator = ToolGenEngine()
    ok = bool(generator.generate_persistent(name, description, code, schema, category="ingested"))
    if not ok:
        raise BuildError("动态工具注册失败（AST/子进程校验未通过）", status=500)
    scripts = copy_source_scripts(root, manifest)
    from agent.tools.persistence import persist_dynamic_tool
    persisted = persist_dynamic_tool(name, description, schema, module_path=str(tool_file),
                                     source="ingestion", category="ingested", verify=False,
                                     assets=scripts)
    if not persisted:
        raise BuildError("动态工具台账写入失败（台账/治理 YAML 不可写）", status=500)
    update_persisted_description(name, description)
    return {"tool_name": name, "module": str(tool_file), "schema": schema,
            "validation": validation, "scripts": scripts, "description": description}
