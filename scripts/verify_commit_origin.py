#!/usr/bin/env python3
"""master commit 来源守卫: 校验 commit 是否为人工发起(阻断脚本伪装)

背景(2026-08-05 复盘): scripts/publish_fix_to_docs.py 用本地 git 身份(nzt47)
直接 git commit + push 到 master, 与人工 commit 无法通过 author email 区分。
本脚本通过"白名单 + GitHub 关联 PR 校验"识别脚本直接 push 的 commit:
脚本 push 的 commit 在 GitHub 上无关联 PR(因未走 PR 流程), 人工 commit 通常走 PR。

校验项:
  ORIGIN-01  author email 不在白名单 → BLOCK; committer 非白名单且非 GitHub 平台邮箱 → BLOCK
  ORIGIN-02  bot commit 修改非白名单路径 → BLOCK
  ORIGIN-03  bot commit subject 缺 [skip ci] → BLOCK
  ORIGIN-04  人工身份 commit 无 GitHub 关联 PR(疑似脚本直接 push) → BLOCK
  ORIGIN-05  subject 命中脚本特征黑名单(可选, 过渡期) → BLOCK

分级:
  - --mode dry-run(默认): 检测到问题仅 ::warning:: 告警, exit 0(灰度期不阻断)
  - --mode enforce: 检测到问题 exit 1(阻断 master push)

可靠性保障(【不易】不锁死 master push):
  - GitHub API 调用失败时降级为 ::warning:: 不阻断(即使 enforce 模式)
  - 本地无 GH_TOKEN/GITHUB_TOKEN 时跳过 PR 校验并 ::notice:: 提示(防本地锁死开发流程)
  - GraphQL 必须用 40 位完整 SHA(短 SHA 报 GitObjectID 类型错误)

用法:
    python scripts/verify_commit_origin.py --sha <SHA> [--mode dry-run|enforce]
    python scripts/verify_commit_origin.py --sha <SHA> --json           # CI 消费
    python scripts/verify_commit_origin.py --base <ref> --sha <SHA>      # 批量模式
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 通用报告生成器(同目录): JSON/文本/HTML 三格式统一输出
sys.path.insert(0, str(Path(__file__).resolve().parent))
import report_generator as rg  # noqa: E402


# ═══ 默认白名单(配置文件缺失时用, 【不易】业务内核不变量) ═══
_DEFAULT_CONFIG: dict[str, Any] = {
    "allowed_authors": [
        {
            "email": "13539371839@139.com",
            "name": "nzt47",
            "require_pr": True,
        },
        {
            "email": "github-actions[bot]@users.noreply.github.com",
            "name": "github-actions[bot]",
            "require_pr": False,
            "allowed_paths": [
                "docs/architecture/*",
                "docs/observability/*",
                "docs/dashboards/*",
                "docs/ci-health/*",
                "VERSION.md",
            ],
            "require_skip_ci": True,
        },
    ],
    "subject_denylist_regex": [],
}


@dataclass
class AuthorRule:
    """白名单中单个 author 的校验规则"""
    email: str
    name: str
    require_pr: bool = False
    allowed_paths: list[str] = field(default_factory=list)
    require_skip_ci: bool = False

    @property
    def is_bot(self) -> bool:
        return "bot]" in self.email or "bot]" in self.name


@dataclass
class CommitMeta:
    """单个 commit 的元信息(从 git 命令提取)"""
    sha: str               # 40 位完整 SHA
    short_sha: str         # 8 位短 SHA(展示用)
    author_name: str
    author_email: str
    committer_name: str
    committer_email: str
    subject: str
    files: list[str]       # 修改的文件路径列表(相对 repo-root)


# ═══ 配置加载 ═══

def load_config(config_path: str | None) -> tuple[dict[str, Any], str]:
    """加载白名单配置, 返回 (config, source_desc)

    【变易】优先读 YAML 配置文件(人可编辑), 缺失或 pyyaml 不可用时用默认值。
    """
    if not config_path:
        # 默认配置文件路径(与脚本同目录)
        default_yaml = Path(__file__).resolve().parent / "commit_origin_whitelist.yaml"
        if default_yaml.exists():
            config_path = str(default_yaml)
        else:
            return _DEFAULT_CONFIG, "内置默认值(配置文件不存在)"

    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        if config_path and Path(config_path).exists():
            print(f"::notice::pyyaml 不可用, 使用内置默认配置(忽略 {config_path})",
                  file=sys.stderr)
        return _DEFAULT_CONFIG, "内置默认值(pyyaml 不可用)"

    path = Path(config_path)
    if not path.exists():
        print(f"::notice::配置文件不存在: {config_path}, 使用内置默认值", file=sys.stderr)
        return _DEFAULT_CONFIG, "内置默认值(配置文件不存在)"

    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    # 合并默认值(允许部分字段缺失)
    merged = dict(_DEFAULT_CONFIG)
    if "allowed_authors" in cfg:
        merged["allowed_authors"] = cfg["allowed_authors"]
    if "subject_denylist_regex" in cfg:
        merged["subject_denylist_regex"] = cfg["subject_denylist_regex"]
    return merged, f"配置文件: {config_path}"


def parse_rules(config: dict[str, Any]) -> list[AuthorRule]:
    """配置 dict → AuthorRule 列表"""
    rules: list[AuthorRule] = []
    for a in config.get("allowed_authors", []):
        rules.append(AuthorRule(
            email=a.get("email", ""),
            name=a.get("name", ""),
            require_pr=a.get("require_pr", False),
            allowed_paths=a.get("allowed_paths", []),
            require_skip_ci=a.get("require_skip_ci", False),
        ))
    return rules


# ═══ git 命令封装 ═══

def _git(args: list[str], repo_root: Path, timeout: int = 15) -> str:
    """执行 git 命令, 返回 stdout(失败抛 RuntimeError)"""
    result = subprocess.run(
        ["git", "-C", str(repo_root)] + args,
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {result.stderr.strip()}")
    return result.stdout


def get_commit_meta(sha: str, repo_root: Path) -> CommitMeta:
    """提取 commit 元信息(author/committer/subject/files)

    【不易】必须用 git rev-parse 取 40 位完整 SHA(GraphQL API 要求)
    """
    full_sha = _git(["rev-parse", sha], repo_root).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", full_sha):
        raise RuntimeError(f"无法解析为 40 位 SHA: {sha} → {full_sha}")

    # 一次性取 author/committer/subject(%an/%ae/%cn/%ce/%s)
    fmt = "%an%x00%ae%x00%cn%x00%ce%x00%s"
    show = _git(["show", "-s", f"--format={fmt}", full_sha], repo_root)
    parts = show.split("\x00")
    if len(parts) < 5:
        raise RuntimeError(f"git show 输出格式异常: {show!r}")
    author_name, author_email, committer_name, committer_email, subject = parts[:5]
    subject = subject.strip()

    # 修改的文件列表(--name-only, 排除 merge commit 的空列表情况)
    files_raw = _git(["diff-tree", "--no-commit-id", "--name-only", "-r", full_sha],
                     repo_root)
    files = [f for f in files_raw.splitlines() if f.strip()]

    return CommitMeta(
        sha=full_sha,
        short_sha=full_sha[:8],
        author_name=author_name,
        author_email=author_email,
        committer_name=committer_name,
        committer_email=committer_email,
        subject=subject,
        files=files,
    )


def expand_shas(sha_arg: str, base_arg: str, repo_root: Path) -> list[str]:
    """展开 SHA 范围: --base 指定时返回 base..sha 的所有 commit; 否则单 commit"""
    if not sha_arg:
        return []
    if base_arg:
        # base..sha 范围(不含 base, 含 sha)
        rev_list = _git(["rev-list", f"{base_arg}..{sha_arg}"], repo_root)
        shas = [s for s in rev_list.splitlines() if s.strip()]
        # rev-list 是逆序(新→旧), 反转为旧→新便于按时间顺序校验
        return list(reversed(shas)) if shas else [sha_arg]
    return [sha_arg]


# ═══ GitHub API 查关联 PR(三级兜底) ═══

def _get_repo_full_name(repo_root: Path) -> str | None:
    """从 git remote 获取 owner/repo(用于 GitHub API)"""
    try:
        url = _git(["remote", "get-url", "origin"], repo_root).strip()
    except RuntimeError:
        return None
    # 支持 git@github.com:owner/repo.git 和 https://github.com/owner/repo.git
    m = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?(?:\s|$)", url)
    return m.group(1) if m else None


def _get_github_token() -> str | None:
    """获取 GitHub token(优先 GH_TOKEN, 其次 GITHUB_TOKEN)"""
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def query_associated_prs_gh(full_sha: str, repo: str) -> list[dict] | None:
    """首选: gh CLI 查 commit 关联 PR(自动用 GITHUB_TOKEN)

    Returns:
        list[dict] | None: PR 列表(空列表=无关联 PR); None=API 调用失败
    """
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/commits/{full_sha}/pulls",
             "--jq", "[.[] | {number, state, head_ref: .head.ref}]"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout or "[]")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None


def query_associated_prs_graphql(full_sha: str, repo: str) -> list[dict] | None:
    """兜底 1: GraphQL associatedPullRequests(必须 40 位完整 SHA)"""
    if "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    query = f"""
    query {{
      repository(owner:"{owner}", name:"{name}") {{
        object(oid:"{full_sha}") {{
          ... on Commit {{
            associatedPullRequests(first: 10) {{
              nodes {{ number title state mergedBy {{ login }} }}
            }}
          }}
        }}
      }}
    }}"""
    try:
        result = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={query}"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout or "{}")
        obj = data.get("data", {}).get("repository", {}).get("object")
        if obj is None:
            return None
        return obj.get("associatedPullRequests", {}).get("nodes", [])
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None


def query_associated_prs_urllib(full_sha: str, repo: str, token: str) -> list[dict] | None:
    """兜底 2: 纯 urllib(gh 不可用时, 用 GITHUB_TOKEN)"""
    url = f"https://api.github.com/repos/{repo}/commits/{full_sha}/pulls"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "verify_commit_origin",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return [{"number": d.get("number"), "state": d.get("state")} for d in data]
    except (urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError, OSError, ssl.SSLError, TimeoutError) as e:
        # 【变易】网络/解析异常降级为 None, 让上游走"API 不可用"路径不阻断
        # (【不易】不锁死 master push); 详见 query_associated_prs 的降级语义
        print(f"::warning::urllib 查关联 PR 失败({type(e).__name__}: {e}), 降级",
              file=sys.stderr)
        return None
    except Exception as e:
        # 兜底: 未知异常也降级, 避免脚本崩溃阻塞 CI(【不易】不锁死 master push)
        print(f"::warning::urllib 查关联 PR 未知异常({type(e).__name__}: {e}), 降级",
              file=sys.stderr)
        return None


def query_associated_prs(full_sha: str, repo: str | None) -> tuple[list[dict] | None, str]:
    """三级兜底查询 commit 关联 PR

    Returns:
        (prs, method_desc): prs=None 表示 API 不可用(应降级不阻断);
                            prs=[] 表示查询成功但无关联 PR(应触发 ORIGIN-04)
    """
    if not repo:
        return None, "未获取到 remote origin 仓库地址"

    # 1. gh CLI 优先
    prs = query_associated_prs_gh(full_sha, repo)
    if prs is not None:
        return prs, "gh API REST"

    # 2. GraphQL 兜底
    prs = query_associated_prs_graphql(full_sha, repo)
    if prs is not None:
        return prs, "gh API GraphQL"

    # 3. urllib 兜底(需 token)
    token = _get_github_token()
    if token:
        prs = query_associated_prs_urllib(full_sha, repo, token)
        if prs is not None:
            return prs, "urllib REST"

    return None, "所有 API 路径不可用(gh 缺失/token 缺失/网络故障)"


# ═══ 校验逻辑 ═══

def _item(item_id: str, path: str, desc: str, status: str, detail: str) -> dict:
    """构造报告 item(遵循 report_generator.py 契约)"""
    return {"id": item_id, "path": path, "desc": desc,
            "status": status, "detail": detail}


def verify_commit(meta: CommitMeta, rules: list[AuthorRule],
                  repo: str | None, mode: str) -> list[dict]:
    """校验单个 commit, 返回 items 列表(供 report_generator 消费)"""
    items: list[dict] = []
    sha_label = meta.short_sha

    # ── ORIGIN-01: author email 严格白名单; committer email 放行 GitHub 平台邮箱 ──
    # 【不易】author email 必须在白名单(防脚本用本地 git 身份伪造 author push 到 master)
    # 【变易】committer email 由 GitHub 平台在 squash merge / web 编辑 / API 提交时
    #         改写为 noreply@github.com(平台行为不可控), 此场景放行不阻断;
    #         但若 committer 既不在白名单也不是 GitHub 平台邮箱, 仍阻断(防 author 伪造
    #         但用真实 committer 蒙混)。
    author_rule = next((r for r in rules if r.email == meta.author_email), None)
    if not author_rule:
        items.append(_item(
            "ORIGIN-01", sha_label,
            "author email 不在白名单",
            "BLOCK",
            f"author={meta.author_email} | subject={meta.subject[:60]}",
        ))
        return items  # author 不合法, 后续校验无意义

    # committer 校验: 白名单命中 OR GitHub 平台邮箱(noreply.github.com 域)放行
    committer_rule = next((r for r in rules if r.email == meta.committer_email), None)
    committer_is_github_platform = (
        meta.committer_email.endswith("@noreply.github.com")
        or meta.committer_email == "noreply@github.com"
    )
    if not committer_rule and not committer_is_github_platform:
        items.append(_item(
            "ORIGIN-01", sha_label,
            "committer email 不在白名单且非 GitHub 平台邮箱",
            "BLOCK",
            f"committer={meta.committer_email} | subject={meta.subject[:60]}",
        ))
        return items

    rule = author_rule  # 后续路径/PR 校验基于 author 规则(防 bot 越权)

    # ── bot 身份校验(ORIGIN-02/03) ──
    if rule.is_bot:
        # ORIGIN-02: bot commit 修改非白名单路径
        if rule.allowed_paths:
            violated = [f for f in meta.files
                        if not any(fnmatch.fnmatch(f, p) for p in rule.allowed_paths)]
            if violated:
                items.append(_item(
                    "ORIGIN-02", sha_label,
                    "bot commit 修改了非白名单路径",
                    "BLOCK",
                    f"违规路径(前5): {violated[:5]} | allowed={rule.allowed_paths}",
                ))

        # ORIGIN-03: bot commit subject 必须含 [skip ci]
        if rule.require_skip_ci and "[skip ci]" not in meta.subject:
            items.append(_item(
                "ORIGIN-03", sha_label,
                "bot commit 缺少 [skip ci] 标记",
                "BLOCK",
                f"subject={meta.subject[:80]}",
            ))
    else:
        # ── 人工身份校验(ORIGIN-04): 必须有关联 PR ──
        if rule.require_pr:
            prs, method = query_associated_prs(meta.sha, repo)
            if prs is None:
                # API 不可用 → 降级不阻断(【不易】不锁死 master push)
                print(f"::warning::GitHub API 不可用({method}), "
                      f"跳过 ORIGIN-04 PR 关联校验(sha={sha_label})", file=sys.stderr)
                items.append(_item(
                    "ORIGIN-04", sha_label,
                    "PR 关联校验跳过(API 不可用, 降级不阻断)",
                    "pass",
                    f"method={method} | subject={meta.subject[:60]}",
                ))
            elif not prs:
                # API 可用但无关联 PR → 疑似脚本直接 push
                items.append(_item(
                    "ORIGIN-04", sha_label,
                    "人工身份 commit 无 GitHub 关联 PR(疑似脚本直接 push)",
                    "BLOCK",
                    f"author={meta.author_email} | method={method} | "
                    f"subject={meta.subject[:60]}",
                ))
            else:
                pr_nums = [str(p.get("number")) for p in prs]
                items.append(_item(
                    "ORIGIN-04", sha_label,
                    "人工身份 commit 有关联 PR",
                    "pass",
                    f"PRs=#{','.join(pr_nums)} | method={method}",
                ))

    # ── ORIGIN-05: subject 黑名单(可选, 过渡期) ──
    for pattern in _SUBJECT_DENYLIST:
        if re.search(pattern, meta.subject):
            items.append(_item(
                "ORIGIN-05", sha_label,
                f"subject 命中脚本特征黑名单: {pattern}",
                "BLOCK",
                f"subject={meta.subject[:80]}",
            ))

    if not items:
        items.append(_item(
            "ORIGIN-00", sha_label,
            f"commit {sha_label} 来源合法",
            "pass",
            f"author={meta.author_email} | subject={meta.subject[:60]}",
        ))
    return items


# 全局 subject 黑名单(load_config 时填充)
_SUBJECT_DENYLIST: list[str] = []


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", default=str(PROJECT_ROOT),
                   help="仓库根目录(默认脚本所在仓库)")
    p.add_argument("--sha", default="", help="被检 commit SHA(默认 HEAD)")
    p.add_argument("--base", default="",
                   help="批量模式: 检查 base..sha 范围内所有 commit")
    p.add_argument("--mode", choices=["dry-run", "enforce"], default="dry-run",
                   help="dry-run(默认, 仅告警)/enforce(阻断)")
    p.add_argument("--config", default="",
                   help="白名单配置文件路径(默认 scripts/commit_origin_whitelist.yaml)")
    p.add_argument("--quiet", action="store_true", help="仅输出 BLOCK 项")
    p.add_argument("--json", action="store_true", help="输出 JSON 报告(stdout 仅 JSON)")
    p.add_argument("--html", metavar="PATH", default="", help="导出 HTML 报告")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    repo_root = Path(args.repo_root).resolve()
    config, config_source = load_config(args.config)
    global _SUBJECT_DENYLIST
    _SUBJECT_DENYLIST = config.get("subject_denylist_regex", [])
    rules = parse_rules(config)

    if not args.sha:
        # 默认 HEAD
        args.sha = "HEAD"

    try:
        shas = expand_shas(args.sha, args.base, repo_root)
    except RuntimeError as e:
        print(f"::error::SHA 范围解析失败: {e}", file=sys.stderr)
        return 1

    if not shas:
        print("::notice::无 commit 需校验(可能是空 push)", file=sys.stderr)
        return 0

    repo = _get_repo_full_name(repo_root)

    all_items: list[dict] = []
    for sha in shas:
        try:
            meta = get_commit_meta(sha, repo_root)
        except RuntimeError as e:
            all_items.append(_item(
                "ORIGIN-ERR", sha[:8],
                f"commit 元信息提取失败: {e}",
                "BLOCK",
                f"sha={sha}",
            ))
            continue
        if not args.quiet and not args.json and not args.html:
            print(f"\n=== 校验 commit {meta.short_sha} ===", file=sys.stderr)
            print(f"  author: {meta.author_name} <{meta.author_email}>", file=sys.stderr)
            print(f"  committer: {meta.committer_name} <{meta.committer_email}>",
                  file=sys.stderr)
            print(f"  subject: {meta.subject}", file=sys.stderr)
            print(f"  files({len(meta.files)}): {meta.files[:3]}", file=sys.stderr)
        all_items.extend(verify_commit(meta, rules, repo, args.mode))

    # 报告生成(复用 report_generator.py 契约)
    report = rg.build_report(
        tool="verify_commit_origin",
        items=all_items,
        meta={
            "mode": args.mode,
            "shas": [s[:8] for s in shas],
            "repo": repo or "unknown",
            "config_source": config_source,
        },
    )

    if args.html:
        path = Path(args.html)
        path.write_text(rg.to_html(report), encoding="utf-8")
        print(f"[INFO] HTML 报告已写入: {path.resolve()}", file=sys.stderr)

    if args.json:
        # stdout 仅 JSON(CI 消费约定, 与 verify_core_invariants.py 一致)
        print(rg.to_json(report))
    else:
        # 人类可读输出走 stderr
        print(rg.to_text(report), file=sys.stderr)
        for it in all_items:
            mark = "PASS" if it["status"] == "pass" else "BLOCK"
            print(f"  [{mark}] [{it['id']}] {it['path']}: {it['desc']} | {it['detail']}",
                  file=sys.stderr)

    blocked = [i for i in all_items if i["status"] == "BLOCK"]
    if blocked:
        if args.mode == "enforce":
            print(f"::error::verify_commit_origin 阻断: {len(blocked)} 项 BLOCK "
                  f"(mode=enforce)", file=sys.stderr)
            return 1
        else:
            print(f"::warning::verify_commit_origin 检测到 {len(blocked)} 项问题, "
                  f"已告警不阻断(mode=dry-run)", file=sys.stderr)
            return 0
    return 0


if __name__ == "__main__":
    # Windows CI runner 默认 stdout 编码可能为 cp1252, 输出中文报告时崩溃
    # (同 verify_core_invariants.py 的修复模式)
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
