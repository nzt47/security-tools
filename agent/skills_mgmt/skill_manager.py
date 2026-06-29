"""通用 SkillManager — 三层架构统一门面

将 SkillFileStore / SkillLoader / SkillExecutor / ContextInjector 封装为一个
开箱即用的技能管理器，提供 install / match / load_instruction / execute /
build_context / uninstall 等简洁 API，便于在任意项目中直接复用。

设计原则:
    - 可观测性: 所有核心操作输出结构化 JSON 日志 (trace_id/module_name/action/duration_ms)
    - 边界显性化: 可能失败的分支抛出带业务错误码的 Error，不静默返回 None
    - 埋点预留: 关键交互点预留 trackEvent 占位符
    - 幂等安全: install 支持覆盖安装，execute 支持超时与防连点

使用示例:
    mgr = SkillManager(repo_path="/data/skills_repo")
    matches = mgr.match("解析 PDF 文件")          # L1
    instruction = mgr.load_instruction("pdf")     # L2
    result = mgr.execute("pdf", params={...})     # L3
    context = mgr.build_context("解析 PDF")        # L1+L2 一站式
"""

from __future__ import annotations

import json
import shutil
import zipfile
import uuid
import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .file_store import SkillFileStore, SkillMDParser
from .loader import SkillLoader, SkillMatch, MatchResult, estimate_tokens
from .executor import SkillExecutor, ExecutionResult
from .context_injector import ContextInjector
from .exceptions import (
    SkillMgmtError,
    SkillNotFoundError,
    SkillValidationError,
    SkillFileError,
    SkillExecutionError,
    ErrorCode,
)
from .observability import logger, traced_action, emit_metric

# 默认技能仓库路径
_DEFAULT_REPO_PATH = Path(__file__).parent.parent.parent / "data" / "skills_repo"

# 技能包清单文件名
_MANIFEST_FILENAME = "manifest.json"

# 合法的 manifest 字段白名单
_MANIFEST_FIELDS = {
    "id", "name", "description", "category", "tags", "version",
    "enabled", "status", "author", "source", "content_type",
    "default_params", "dependencies",
}


def _track_event(event_name: str, payload: Optional[Dict] = None) -> None:
    """埋点占位符 — 关键用户交互点的事件追踪

    生产环境可替换为真实的埋点 SDK 调用。
    约定: event_name 格式为 yunshu_skill_<动作>，payload 包含 skill_id 等上下文。
    """
    # 占位符: 实际部署时接入 BusinessMetricsCollector
    logger.debug("[TrackEvent] %s: %s", event_name, json.dumps(payload or {}, ensure_ascii=False))


class SkillManager:
    """三层架构技能管理器 — 通用门面

    封装 file_store / loader / executor / context_injector 四个组件，
    对外暴露简洁的安装/匹配/加载/执行/卸载 API。

    Attributes:
        repo_path: 技能仓库根目录
        file_store: 文件系统存储层 (L1/L2/L3 物理基础)
        loader: 三层检索引擎
        executor: 脚本沙箱执行器
        injector: LLM 上下文注入器
    """

    def __init__(self, repo_path: Optional[str] = None):
        """初始化技能管理器

        Args:
            repo_path: 技能仓库根目录路径，None 时使用默认路径 (data/skills_repo)
        """
        self.repo_path = Path(repo_path) if repo_path else _DEFAULT_REPO_PATH
        self.file_store = SkillFileStore(repo_path=str(self.repo_path))
        self.loader = SkillLoader(self.file_store)
        self.executor = SkillExecutor(self.file_store)
        self.injector = ContextInjector(self.loader)
        logger.info(json.dumps({
            "trace_id": uuid.uuid4().hex[:16],
            "module_name": "skill_manager",
            "action": "init",
            "repo_path": str(self.repo_path),
        }, ensure_ascii=False))

    # ═══════════════════════════════════════════════════════
    #  安装 / 卸载
    # ═══════════════════════════════════════════════════════

    def install_from_dir(self, skill_dir: str) -> str:
        """从本地目录安装技能

        目录结构要求:
            skill_dir/
                skill.md          (必需) 元数据 + 使用说明
                scripts/          (可选) 脚本目录
                    main.py
                temp/             (可选) 临时文件目录

        Args:
            skill_dir: 技能目录路径

        Returns:
            skill_id: 安装的技能ID

        Raises:
            SkillValidationError: 目录结构不合法
            SkillFileError: skill.md 解析失败
        """
        t0 = time.time()
        skill_dir = Path(skill_dir).resolve()
        if not skill_dir.is_dir():
            raise SkillValidationError(
                f"技能目录不存在: {skill_dir}",
                code=ErrorCode.VALIDATION_ERROR,
                fields={"path": str(skill_dir)},
            )

        md_path = skill_dir / "skill.md"
        if not md_path.exists():
            raise SkillValidationError(
                f"技能目录缺少 skill.md: {skill_dir}",
                code=ErrorCode.MD_READ_ERROR,
                fields={"path": str(md_path)},
            )

        # 解析 skill.md
        content = md_path.read_text(encoding="utf-8")
        meta, instruction = SkillMDParser.parse(content)
        if not meta.get("id"):
            raise SkillValidationError(
                "skill.md front matter 缺少 id 字段",
                code=ErrorCode.VALIDATION_ERROR,
                fields={"file": str(md_path)},
            )

        skill_id = meta["id"]

        # 收集脚本
        scripts = {}
        scripts_dir = skill_dir / "scripts"
        if scripts_dir.is_dir():
            for py_file in scripts_dir.glob("*.py"):
                scripts[py_file.name] = py_file.read_text(encoding="utf-8")

        # 收集 temp 文件
        temp_files = {}
        temp_dir = skill_dir / "temp"
        if temp_dir.is_dir():
            for f in temp_dir.iterdir():
                if f.is_file():
                    temp_files[f.name] = f.read_bytes()

        # 如果已存在则先删除（覆盖安装）
        try:
            self.file_store.delete(skill_id)
            logger.info("[SkillManager] 覆盖安装技能: %s", skill_id)
        except Exception:
            pass

        self.file_store.create(
            skill_id=skill_id,
            meta=meta,
            instruction=instruction,
            scripts=scripts,
            temp_files=temp_files,
        )

        elapsed = (time.time() - t0) * 1000
        _track_event("yunshu_skill_install", {"skill_id": skill_id, "from": "dir"})
        logger.info(json.dumps({
            "trace_id": uuid.uuid4().hex[:16],
            "module_name": "skill_manager",
            "action": "install_from_dir",
            "skill_id": skill_id,
            "duration_ms": round(elapsed, 2),
            "scripts": len(scripts),
        }, ensure_ascii=False))
        return skill_id

    def install_from_zip(self, zip_path: str) -> str:
        """从 .zip 技能包安装技能

        zip 包结构要求:
            skill.zip
                manifest.json     (必需) 元数据
                skill.md          (可选) 使用说明，无则用 manifest.description
                scripts/          (可选) 脚本目录
                temp/             (可选) 临时文件目录

        Args:
            zip_path: zip 文件路径

        Returns:
            skill_id: 安装的技能ID

        Raises:
            SkillValidationError: zip 结构不合法
            SkillFileError: 解压或解析失败
        """
        t0 = time.time()
        zip_path = Path(zip_path)
        if not zip_path.exists():
            raise SkillValidationError(
                f"技能包不存在: {zip_path}",
                code=ErrorCode.VALIDATION_ERROR,
                fields={"path": str(zip_path)},
            )

        import tempfile
        with tempfile.TemporaryDirectory(prefix="skill_install_") as tmp:
            tmp_dir = Path(tmp)
            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(tmp_dir)
            except zipfile.BadZipFile as e:
                raise SkillFileError(
                    f"无效的 zip 文件: {e}",
                    code=ErrorCode.MD_READ_ERROR,
                ) from e

            # 查找 manifest.json（可能在根目录或子目录）
            manifest_path = tmp_dir / _MANIFEST_FILENAME
            if not manifest_path.exists():
                # 在子目录中查找
                manifests = list(tmp_dir.rglob(_MANIFEST_FILENAME))
                if not manifests:
                    raise SkillValidationError(
                        f"技能包缺少 {_MANIFEST_FILENAME}",
                        code=ErrorCode.VALIDATION_ERROR,
                    )
                manifest_path = manifests[0]
                # 技能根目录为 manifest 所在目录
                skill_root = manifest_path.parent
            else:
                skill_root = tmp_dir

            # 解析 manifest
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise SkillFileError(
                    f"manifest.json 解析失败: {e}",
                    code=ErrorCode.MD_YAML_ERROR,
                ) from e

            if not manifest.get("id"):
                raise SkillValidationError(
                    "manifest.json 缺少 id 字段",
                    code=ErrorCode.VALIDATION_ERROR,
                )

            skill_id = manifest["id"]
            # 过滤白名单字段
            meta = {k: v for k, v in manifest.items() if k in _MANIFEST_FIELDS}

            # 读取 skill.md（可选）
            md_path = skill_root / "skill.md"
            if md_path.exists():
                content = md_path.read_text(encoding="utf-8")
                _, instruction = SkillMDParser.parse(content)
            else:
                instruction = manifest.get("description", "")

            # 收集脚本
            scripts = {}
            scripts_dir = skill_root / "scripts"
            if scripts_dir.is_dir():
                for py_file in scripts_dir.glob("*.py"):
                    scripts[py_file.name] = py_file.read_text(encoding="utf-8")

            # 收集 temp 文件
            temp_files = {}
            temp_dir = skill_root / "temp"
            if temp_dir.is_dir():
                for f in temp_dir.iterdir():
                    if f.is_file():
                        temp_files[f.name] = f.read_bytes()

            # 覆盖安装
            try:
                self.file_store.delete(skill_id)
            except Exception:
                pass

            self.file_store.create(
                skill_id=skill_id,
                meta=meta,
                instruction=instruction,
                scripts=scripts,
                temp_files=temp_files,
            )

        elapsed = (time.time() - t0) * 1000
        _track_event("yunshu_skill_install", {"skill_id": skill_id, "from": "zip"})
        logger.info(json.dumps({
            "trace_id": uuid.uuid4().hex[:16],
            "module_name": "skill_manager",
            "action": "install_from_zip",
            "skill_id": skill_id,
            "duration_ms": round(elapsed, 2),
            "scripts": len(scripts),
        }, ensure_ascii=False))
        return skill_id

    def uninstall(self, skill_id: str) -> bool:
        """卸载技能

        Args:
            skill_id: 技能ID

        Returns:
            True 表示卸载成功

        Raises:
            SkillNotFoundError: 技能不存在
        """
        t0 = time.time()
        with traced_action("mgr_uninstall", skill_id=skill_id):
            ok = self.file_store.delete(skill_id)
            if not ok:
                raise SkillNotFoundError(skill_id)
            _track_event("yunshu_skill_uninstall", {"skill_id": skill_id})
            elapsed = (time.time() - t0) * 1000
            logger.info(json.dumps({
                "trace_id": uuid.uuid4().hex[:16],
                "module_name": "skill_manager",
                "action": "uninstall",
                "skill_id": skill_id,
                "duration_ms": round(elapsed, 2),
            }, ensure_ascii=False))
            return True

    # ═══════════════════════════════════════════════════════
    #  L1: 元数据匹配
    # ═══════════════════════════════════════════════════════

    def match(self, intent: str, *, top_k: int = 5,
              enabled_only: bool = True,
              min_score: float = 0.01) -> MatchResult:
        """L1 意图匹配 — 在元数据索引上做快速检索

        仅加载元数据（~100 token/技能），不加载使用说明和脚本代码。

        Args:
            intent: 用户意图文本
            top_k: 返回前 K 个匹配
            enabled_only: 仅返回启用状态的技能
            min_score: 最低匹配分阈值

        Returns:
            MatchResult — 包含 matches 列表与统计信息

        Raises:
            SkillValidationError: intent 为空或参数非法
        """
        if not intent or not intent.strip():
            raise SkillValidationError(
                "意图不能为空",
                code=ErrorCode.VALIDATION_ERROR,
                fields={"intent": intent},
            )
        if top_k < 1:
            raise SkillValidationError(
                f"top_k 必须 >= 1, got {top_k}",
                code=ErrorCode.VALIDATION_ERROR,
            )
        _track_event("yunshu_skill_match", {"intent": intent[:50], "top_k": top_k})
        return self.loader.match(
            intent, top_k=top_k,
            enabled_only=enabled_only, min_score=min_score,
        )

    # ═══════════════════════════════════════════════════════
    #  L2: 按需加载使用说明
    # ═══════════════════════════════════════════════════════

    def load_instruction(self, skill_id: str) -> Dict[str, Any]:
        """L2 按需加载技能使用说明 (skill.md 正文)

        仅在 L1 匹配命中后才应调用，避免无谓加载。

        Args:
            skill_id: 技能ID

        Returns:
            dict — {skill_id, instruction, estimated_tokens, layer}

        Raises:
            SkillNotFoundError: 技能不存在
        """
        _track_event("yunshu_skill_load_instruction", {"skill_id": skill_id})
        return self.loader.load_instruction(skill_id)

    # ═══════════════════════════════════════════════════════
    #  L3: 脚本沙箱执行
    # ═══════════════════════════════════════════════════════

    def execute(self, skill_id: str, *,
                script_name: str = "main.py",
                params: Optional[Dict[str, Any]] = None,
                timeout: Optional[float] = None) -> ExecutionResult:
        """L3 沙箱执行技能脚本

        脚本在独立子进程中执行，stdin 接收 JSON 参数，stdout 返回 JSON 结果。
        代码不进入 LLM 上下文，只有执行结果进入。

        Args:
            skill_id: 技能ID
            script_name: 脚本文件名 (必须位于技能的 scripts/ 目录)
            params: 传入脚本的 JSON 参数
            timeout: 执行超时秒数 (None=默认 30s)

        Returns:
            ExecutionResult — 包含 success/result/error/duration_ms

        Raises:
            SkillNotFoundError: 技能或脚本不存在
            SkillExecutionError: 执行超时或失败（超时返回 result 而非异常）
        """
        _track_event("yunshu_skill_execute", {
            "skill_id": skill_id, "script": script_name,
        })
        result = self.executor.execute(
            skill_id, script_name=script_name,
            params=params, timeout=timeout,
        )
        # 埋点: 执行成功率与延迟
        try:
            emit_metric("yunshu_skill_mgr_exec_total",
                        value=1, kind="counter",
                        labels={"skill_id": skill_id,
                                "success": str(result.success).lower()})
            emit_metric("yunshu_skill_mgr_exec_latency_ms",
                        value=result.duration_ms, kind="histogram",
                        labels={"skill_id": skill_id})
        except Exception:  # noqa: BLE001 埋点失败不影响主流程
            pass
        return result

    # ═══════════════════════════════════════════════════════
    #  一站式上下文构建 (L1 + L2)
    # ═══════════════════════════════════════════════════════

    def build_context(self, intent: str, *, max_tokens: int = 6000,
                      top_k: int = 5,
                      auto_load_instruction: bool = False,
                      skill_id: Optional[str] = None) -> Dict[str, Any]:
        """一站式构建 LLM 上下文 (L1 匹配 + L2 按需加载)

        Args:
            intent: 用户意图
            max_tokens: 上下文 token 预算上限
            top_k: L1 返回的最大候选数
            auto_load_instruction: 是否自动加载 top-1 技能的说明
            skill_id: 显式指定要加载说明的技能ID

        Returns:
            dict — {prompt, matches, instruction?, total_tokens, layers, budget}
        """
        if not intent or not intent.strip():
            raise SkillValidationError(
                "意图不能为空",
                code=ErrorCode.VALIDATION_ERROR,
            )
        _track_event("yunshu_skill_build_context", {"intent": intent[:50]})
        return self.injector.build_context(
            intent, max_tokens=max_tokens, top_k=top_k,
            auto_load_instruction=auto_load_instruction,
            skill_id=skill_id,
        )

    # ═══════════════════════════════════════════════════════
    #  查询
    # ═══════════════════════════════════════════════════════

    def list_skills(self) -> List[Dict[str, Any]]:
        """列出所有技能的元数据 (L1 索引)"""
        return self.loader.list_all_metadata(enabled_only=False)

    def get_skill_info(self, skill_id: str) -> Dict[str, Any]:
        """获取技能详细信息（三层全部元信息，不含代码）"""
        meta = self.file_store.get_metadata(skill_id)
        if not meta:
            raise SkillNotFoundError(skill_id)
        scripts = self.loader.list_scripts(skill_id)
        temp_files = self.loader.list_temp_files(skill_id)
        return {
            "skill_id": skill_id,
            "meta": meta,
            "scripts": scripts,
            "temp_files": temp_files,
            "layer": "all",
        }

    def get_layer_summary(self) -> Dict[str, Any]:
        """三层架构统计摘要"""
        return self.loader.get_layer_summary()

    # ═══════════════════════════════════════════════════════
    #  健康检查 (供 /health 端点调用)
    # ═══════════════════════════════════════════════════════

    def health(self) -> Dict[str, Any]:
        """健康检查 — 返回三层架构各组件的连接状态

        Returns:
            dict — {ok, module, version, repo_path, file_store, executor, layer_summary}
        """
        try:
            fs_health = self.file_store.health()
        except Exception as e:  # noqa: BLE001
            fs_health = {"ok": False, "error": str(e)}
        try:
            exec_health = self.executor.health()
        except Exception as e:  # noqa: BLE001
            exec_health = {"ok": False, "error": str(e)}
        try:
            summary = self.loader.get_layer_summary()
        except Exception as e:  # noqa: BLE001
            summary = {"error": str(e)}
        return {
            "ok": fs_health.get("ok", False) and exec_health.get("ok", False),
            "module": "skill_manager",
            "version": "1.0.0",
            "repo_path": str(self.repo_path),
            "file_store": fs_health,
            "executor": exec_health,
            "layer_summary": summary,
        }

    # ═══════════════════════════════════════════════════════
    #  技能包打包
    # ═══════════════════════════════════════════════════════

    def export_to_zip(self, skill_id: str, zip_path: str) -> str:
        """将技能导出为标准 .zip 技能包

        生成的 zip 包结构:
            skill.zip
                manifest.json     — 元数据
                skill.md          — 使用说明
                scripts/          — 脚本目录
                temp/             — 临时文件目录

        Args:
            skill_id: 技能ID
            zip_path: 输出 zip 文件路径

        Returns:
            zip_path: 生成的 zip 文件路径

        Raises:
            SkillNotFoundError: 技能不存在
        """
        t0 = time.time()
        meta, instruction, scripts, temp_files = self.file_store.read(skill_id)

        zip_path = Path(zip_path)
        zip_path.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # manifest.json — 仅白名单字段
            manifest = {k: v for k, v in meta.items() if k in _MANIFEST_FIELDS}
            zf.writestr(_MANIFEST_FILENAME,
                        json.dumps(manifest, ensure_ascii=False, indent=2))

            # skill.md — 使用说明
            zf.writestr("skill.md", instruction)

            # scripts/
            for script_info in scripts:
                sname = script_info["name"] if isinstance(script_info, dict) else script_info
                spath = self.file_store.get_script_path(skill_id, sname)
                if spath and spath.exists():
                    zf.writestr(f"scripts/{sname}",
                                spath.read_text(encoding="utf-8"))

            # temp/
            for temp_info in temp_files:
                tname = temp_info["name"] if isinstance(temp_info, dict) else temp_info
                tpath = self.file_store.get_temp_path(skill_id, tname)
                if tpath and tpath.exists():
                    zf.writestr(f"temp/{tname}", tpath.read_bytes())

        elapsed = (time.time() - t0) * 1000
        _track_event("yunshu_skill_export", {"skill_id": skill_id})
        logger.info(json.dumps({
            "trace_id": uuid.uuid4().hex[:16],
            "module_name": "skill_manager",
            "action": "export_to_zip",
            "skill_id": skill_id,
            "zip_path": str(zip_path),
            "duration_ms": round(elapsed, 2),
        }, ensure_ascii=False))
        return str(zip_path)
