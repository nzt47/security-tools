"""任务2 · 知识卡片引擎 CLI（python -m agent.knowledge）回归测试

双模式（同 test_preflight_runner.py）：
1. subprocess `python -m agent.knowledge <子命令>` 验证退出码契约与 stdout/stderr；
2. 直接调用 main() 验证各 cmd_* 返回值（供覆盖率统计）。

退出码约定（__main__.py docstring，不易）：
    0 = 成功；1 = 出错（卡片不存在 / 非法状态迁移 / 检出断链）。
所有 CLI 命令以 tmp_path 显式传 --wiki/--index，绝不触碰仓库真实 knowledge/。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent.knowledge.card import CardStore
from agent.knowledge.schema import Card, slugify

_REPO_ROOT = Path(__file__).resolve().parents[2]


def make_card(
    title: str = "",
    slug: str = "",
    type: str = "concepts",
    status: str = "current",
    content: str = "",
    links=None,
) -> Card:
    # 契约（schema.validate_card）：slug 必须等于 slugify(title)；
    # 只传其一即可，缺省的另一方自动补齐，避免双传不一致。
    slug = slug or slugify(title)
    title = title or slug
    card = Card(
        title=title,
        slug=slug,
        status=status,
        type=type,
        source="inbox/test.md",
        date="2026-08-02",
        tags=[],
        links=links if links is not None else [],
        insight="一句话核心洞见",
    )
    card.content = content
    return card


def _run_cli(args: list[str], cwd: str | Path) -> subprocess.CompletedProcess:
    """subprocess 跑 `python -m agent.knowledge`（UTF-8 防 Windows 控制台编码问题）。"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, "-m", "agent.knowledge", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(cwd),
        env=env,
        timeout=90,
    )


def _store(wiki: Path) -> CardStore:
    return CardStore(wiki)


# ---------- subprocess 模式：退出码契约 ----------


def test_cli_index_rebuild_empty_wiki_exit_zero(tmp_path):
    """空 wiki 全量重建 → exit 0，输出完成信息与索引数 0。"""
    wiki = tmp_path / "wiki"
    proc = _run_cli(
        ["index-rebuild", "--wiki", str(wiki), "--index", str(tmp_path / "index.md")],
        cwd=_REPO_ROOT,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr}"
    assert "全量重建完成" in proc.stdout
    assert "索引卡片数=0" in proc.stdout


def test_cli_index_rebuild_writes_index_file(tmp_path):
    """全量重建后 index.md 必须真实落盘（含头部与三个 section 骨架）。"""
    wiki = tmp_path / "wiki"
    index = tmp_path / "index.md"
    _run_cli(
        ["index-rebuild", "--wiki", str(wiki), "--index", str(index)],
        cwd=_REPO_ROOT,
    )
    text = index.read_text(encoding="utf-8")
    assert text.startswith("# 知识库全局索引")
    assert "## 概念 (Concepts)" in text
    assert "## 实体 (Entities)" in text
    assert "## 洞察 (Insights)" in text


def test_cli_index_rebuild_counts_cards(tmp_path):
    """wiki 有 2 张卡 → 重建后索引卡片数=2。"""
    wiki = tmp_path / "wiki"
    store = _store(wiki)
    store.create(make_card(slug="concept-a", type="concepts"))
    store.create(make_card(slug="entity-b", type="entities"))
    proc = _run_cli(
        ["index-rebuild", "--wiki", str(wiki), "--index", str(tmp_path / "index.md")],
        cwd=_REPO_ROOT,
    )
    assert proc.returncode == 0
    assert "索引卡片数=2" in proc.stdout


def test_cli_card_list_shows_cards(tmp_path):
    """card-list 列出卡片（slug/status/type/insight），含总数行。"""
    wiki = tmp_path / "wiki"
    store = _store(wiki)
    store.create(make_card(slug="prompt-engineering"))
    proc = _run_cli(["card-list", "--wiki", str(wiki)], cwd=_REPO_ROOT)
    assert proc.returncode == 0, f"stderr={proc.stderr}"
    assert "prompt-engineering" in proc.stdout
    assert "current" in proc.stdout
    assert "concepts" in proc.stdout
    assert "共 1 张卡片" in proc.stdout


def test_cli_card_list_status_filter(tmp_path):
    """card-list --status draft 只显示 draft 卡。"""
    wiki = tmp_path / "wiki"
    store = _store(wiki)
    store.create(make_card(slug="draft-x", status="draft"))
    store.create(make_card(slug="current-y", status="current"))
    proc = _run_cli(
        ["card-list", "--status", "draft", "--wiki", str(wiki)],
        cwd=_REPO_ROOT,
    )
    assert proc.returncode == 0
    assert "draft-x" in proc.stdout
    assert "current-y" not in proc.stdout


def test_cli_card_transition_valid_exit_zero(tmp_path):
    """合法迁移 current→archive → exit 0 且输出 ✓。"""
    wiki = tmp_path / "wiki"
    store = _store(wiki)
    store.create(make_card(slug="to-archive"))
    proc = _run_cli(
        ["card-transition", "to-archive", "archive", "--wiki", str(wiki)],
        cwd=_REPO_ROOT,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr}"
    assert "to-archive → archive" in proc.stdout
    assert "✓" in proc.stdout


def test_cli_card_transition_invalid_exit_one(tmp_path):
    """非法迁移 draft→archive（状态机拒绝）→ exit 1 且 stderr 含迁移失败。"""
    wiki = tmp_path / "wiki"
    store = _store(wiki)
    store.create(make_card(slug="draft-c", status="draft"))
    proc = _run_cli(
        ["card-transition", "draft-c", "archive", "--wiki", str(wiki)],
        cwd=_REPO_ROOT,
    )
    assert proc.returncode == 1
    assert "迁移失败" in proc.stderr


def test_cli_card_transition_missing_exit_one(tmp_path):
    """目标卡不存在 → exit 1 且 stderr 含迁移失败。"""
    wiki = tmp_path / "wiki"
    proc = _run_cli(
        ["card-transition", "no-such-card", "archive", "--wiki", str(wiki)],
        cwd=_REPO_ROOT,
    )
    assert proc.returncode == 1
    assert "迁移失败" in proc.stderr


def test_cli_check_links_clean_exit_zero(tmp_path):
    """无断链 → exit 0 且输出无断链。"""
    wiki = tmp_path / "wiki"
    store = _store(wiki)
    store.create(make_card(slug="card-a"))
    proc = _run_cli(["check-links", "--wiki", str(wiki)], cwd=_REPO_ROOT)
    assert proc.returncode == 0, f"stderr={proc.stderr}"
    assert "无断链" in proc.stdout


def test_cli_check_links_broken_exit_one(tmp_path):
    """存在断链 → exit 1 且 stdout 列明细、stderr 报检出数。"""
    wiki = tmp_path / "wiki"
    store = _store(wiki)
    store.create(
        make_card(slug="ref-card", links=["ghost-link"])
    )
    proc = _run_cli(["check-links", "--wiki", str(wiki)], cwd=_REPO_ROOT)
    assert proc.returncode == 1
    assert "断链: ref-card → ghost-link" in proc.stdout
    assert "检出 1 条断链" in proc.stderr


def test_cli_orphans_clean_exit_zero(tmp_path):
    """空库孤儿检测 → exit 0。"""
    wiki = tmp_path / "wiki"
    proc = _run_cli(["orphans", "--wiki", str(wiki)], cwd=_REPO_ROOT)
    assert proc.returncode == 0, f"stderr={proc.stderr}"
    assert "无孤儿卡片" in proc.stdout


def test_cli_orphans_reports_orphans(tmp_path):
    """无入链卡片被列出；报告型命令仍 exit 0。"""
    wiki = tmp_path / "wiki"
    store = _store(wiki)
    store.create(make_card(slug="solo-card"))
    proc = _run_cli(["orphans", "--wiki", str(wiki)], cwd=_REPO_ROOT)
    assert proc.returncode == 0
    assert "孤儿: solo-card" in proc.stdout
    assert "共 1 张孤儿卡片" in proc.stdout


# ---------- 直接调用 main()：返回值契约（供覆盖率统计） ----------


def test_main_index_rebuild_direct(tmp_path, capsys):
    from agent.knowledge.__main__ import main

    wiki = tmp_path / "wiki"
    rc = main(
        ["index-rebuild", "--wiki", str(wiki), "--index", str(tmp_path / "index.md")]
    )
    assert rc == 0
    assert "全量重建完成" in capsys.readouterr().out


def test_main_card_list_direct(tmp_path, capsys):
    from agent.knowledge.__main__ import main

    wiki = tmp_path / "wiki"
    _store(wiki).create(make_card(slug="direct-card"))
    assert main(["card-list", "--wiki", str(wiki)]) == 0
    assert "direct-card" in capsys.readouterr().out


def test_main_card_transition_valid_direct(tmp_path):
    from agent.knowledge.__main__ import main

    wiki = tmp_path / "wiki"
    _store(wiki).create(make_card(slug="move-me"))
    assert main(["card-transition", "move-me", "draft", "--wiki", str(wiki)]) == 0


def test_main_card_transition_invalid_direct(tmp_path, capsys):
    """非法迁移直接 main() → 1，stderr 含迁移失败。"""
    from agent.knowledge.__main__ import main

    wiki = tmp_path / "wiki"
    _store(wiki).create(make_card(slug="draft-m", status="draft"))
    assert (
        main(["card-transition", "draft-m", "archive", "--wiki", str(wiki)]) == 1
    )
    assert "迁移失败" in capsys.readouterr().err


def test_main_check_links_broken_direct(tmp_path, capsys):
    from agent.knowledge.__main__ import main

    wiki = tmp_path / "wiki"
    _store(wiki).create(
        make_card(slug="broken-card", links=["missing-target"])
    )
    assert main(["check-links", "--wiki", str(wiki)]) == 1
    assert "检出 1 条断链" in capsys.readouterr().err


def test_main_orphans_direct(tmp_path):
    from agent.knowledge.__main__ import main

    wiki = tmp_path / "wiki"
    assert main(["orphans", "--wiki", str(wiki)]) == 0


def test_main_verbose_branch(tmp_path):
    """--verbose 触发 logging INFO 配置，不影响返回值。"""
    from agent.knowledge.__main__ import main

    wiki = tmp_path / "wiki"
    assert (
        main(["orphans", "--verbose", "--wiki", str(wiki)]) == 0
    )


def test_main_requires_subcommand():
    """未提供子命令（add_subparsers required=True）→ SystemExit(2)。"""
    from agent.knowledge.__main__ import main

    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2


def test_main_module_entrypoint(tmp_path):
    """python -m 直接入口 SystemExit 契约（runpy 注入 argv 指向 tmp）。"""
    import runpy
    import sys
    from unittest import mock

    wiki = tmp_path / "wiki"
    with mock.patch.object(
        sys, "argv", ["agent.knowledge", "orphans", "--wiki", str(wiki)]
    ):
        with pytest.raises(SystemExit) as excinfo:
            runpy.run_module("agent.knowledge", run_name="__main__")
    assert excinfo.value.code == 0


# ---------- 归档副作用集成断言（verify_knowledge_cli.py 场景固化） ----------


def test_cli_card_transition_archive_moves_file_and_rewrites_referrers(tmp_path):
    """CLI card-transition archive 副作用：卡片物理移入 archives/ 且引用卡 links 改写。

    场景（verify 脚本第 2 组）：引用卡「驾驭工程」links=['提示词工程']，
    通过 CLI 归档「提示词工程」后，驾驭工程的 links 必须改写为
    ['archives/提示词工程']（AGENTS.md §3.1 归档重链契约）。
    """
    wiki = tmp_path / "wiki"
    store = _store(wiki)
    store.create(make_card(slug="prompt-engineering", type="insights"))
    store.create(make_card(slug="driving-engineering", links=["prompt-engineering"]))
    proc = _run_cli(
        ["card-transition", "prompt-engineering", "archive", "--wiki", str(wiki)],
        cwd=_REPO_ROOT,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr}"
    archived = store.get("archives/prompt-engineering")
    assert archived is not None and archived.status == "archive"
    referrer = store.get("driving-engineering")
    assert referrer is not None
    assert referrer.links == ["archives/prompt-engineering"]


def test_cli_index_rebuild_excludes_archived(tmp_path):
    """归档卡移出 wiki 后，index-rebuild 不再计数、index.md 无归档 slug。"""
    wiki = tmp_path / "wiki"
    index = tmp_path / "index.md"
    store = _store(wiki)
    store.create(make_card(slug="prompt-engineering", type="insights"))
    store.create(make_card(slug="driving-engineering"))
    proc = _run_cli(
        ["card-transition", "prompt-engineering", "archive", "--wiki", str(wiki)],
        cwd=_REPO_ROOT,
    )
    assert proc.returncode == 0
    proc = _run_cli(
        ["index-rebuild", "--wiki", str(wiki), "--index", str(index)],
        cwd=_REPO_ROOT,
    )
    assert proc.returncode == 0
    assert "索引卡片数=1" in proc.stdout  # 仅剩 driving-engineering
    text = index.read_text(encoding="utf-8")
    assert "prompt-engineering" not in text
    assert "driving-engineering" in text


def test_main_card_transition_archive_rewrites_referrer_links(tmp_path):
    """直接 main() 触发归档重链（不走 subprocess，供覆盖率统计）。"""
    from agent.knowledge.__main__ import main

    wiki = tmp_path / "wiki"
    store = _store(wiki)
    store.create(make_card(slug="prompt-engineering", type="insights"))
    store.create(make_card(slug="driving-engineering", links=["prompt-engineering"]))
    assert (
        main(["card-transition", "prompt-engineering", "archive", "--wiki", str(wiki)])
        == 0
    )
    assert store.get("driving-engineering").links == ["archives/prompt-engineering"]


# ---------- import / export / list（任务2 批量处理） ----------


def _write_src_card(path: Path, slug: str, *, status: str = "current",
                    type: str = "concepts", content: str = "正文") -> None:
    """写一张合法 frontmatter 卡片文件（slug 契约: slug == slugify(title)）。"""
    path.write_text(
        f"---\n"
        f"title: {slug}\n"
        f"slug: {slug}\n"
        f"status: {status}\n"
        f"type: {type}\n"
        f"source: inbox/{slug}.md\n"
        f"date: 2026-08-02\n"
        f"tags: []\n"
        f"links: []\n"
        f"insight: 一句话核心洞见\n"
        f"---\n\n"
        f"{content}\n",
        encoding="utf-8",
    )


def test_cli_import_success_exit_zero(tmp_path):
    """import 2 张合法卡 → exit 0，输出计数，落盘 + index 更新。"""
    wiki = tmp_path / "wiki"
    src = tmp_path / "src"
    src.mkdir()
    _write_src_card(src / "a.md", "driving-engineering")
    _write_src_card(src / "b.md", "prompt-engineering")
    proc = _run_cli(["import", str(src), "--wiki", str(wiki)], cwd=_REPO_ROOT)
    assert proc.returncode == 0, f"stderr={proc.stderr}"
    assert "成功 2 / 跳过冲突 0 / 失败 0" in proc.stdout
    store = _store(wiki)
    assert {c.slug for c in store.list()} == {"driving-engineering", "prompt-engineering"}


def test_cli_import_failure_exit_one(tmp_path):
    """含损坏文件 → exit 1，stderr 输出失败明细（CI 门禁语义）。"""
    wiki = tmp_path / "wiki"
    src = tmp_path / "src"
    src.mkdir()
    (src / "broken.md").write_text("# 无 frontmatter\n", encoding="utf-8")
    _write_src_card(src / "ok.md", "driving-engineering")
    proc = _run_cli(["import", str(src), "--wiki", str(wiki)], cwd=_REPO_ROOT)
    assert proc.returncode == 1
    assert "失败 1" in proc.stdout
    assert "broken.md" in proc.stderr
    assert "frontmatter" in proc.stderr


def test_cli_import_missing_dir_exit_one(tmp_path):
    """目录不存在 → exit 1，stderr 提示导入失败。"""
    proc = _run_cli(
        ["import", str(tmp_path / "nope"), "--wiki", str(tmp_path / "wiki")],
        cwd=_REPO_ROOT,
    )
    assert proc.returncode == 1
    assert "导入失败" in proc.stderr


def test_cli_import_force_updates_conflict(tmp_path):
    """--force：同 slug 冲突改走 update，内容被覆盖且 exit 0。"""
    wiki = tmp_path / "wiki"
    store = _store(wiki)
    store.create(make_card(slug="driving-engineering"))
    src = tmp_path / "src"
    src.mkdir()
    _write_src_card(src / "a.md", "driving-engineering", content="新正文")
    proc = _run_cli(["import", str(src), "--force", "--wiki", str(wiki)],
                    cwd=_REPO_ROOT)
    assert proc.returncode == 0, f"stderr={proc.stderr}"
    assert "成功 1" in proc.stdout
    assert store.get("driving-engineering").content.strip() == "新正文"


def test_cli_export_roundtrip(tmp_path):
    """export 导出 → 空库 import 回读：slug/status/type/content 全等。"""
    wiki = tmp_path / "wiki"
    store = _store(wiki)
    store.create(make_card(slug="driving-engineering", type="concepts",
                           content="原文 A"))
    store.create(make_card(slug="prompt-engineering", type="insights",
                           status="draft", content="原文 B"))
    dst = tmp_path / "export"
    proc = _run_cli(["export", str(dst), "--wiki", str(wiki)], cwd=_REPO_ROOT)
    assert proc.returncode == 0, f"stderr={proc.stderr}"
    assert "导出 2 张卡片" in proc.stdout
    assert (dst / "driving-engineering.md").exists()

    wiki2 = tmp_path / "wiki2"
    proc2 = _run_cli(["import", str(dst), "--wiki", str(wiki2)], cwd=_REPO_ROOT)
    assert proc2.returncode == 0, f"stderr={proc2.stderr}"
    cards2 = {c.slug: c for c in _store(wiki2).list()}
    assert cards2["driving-engineering"].content.strip() == "原文 A"
    assert cards2["prompt-engineering"].status == "draft"
    assert cards2["prompt-engineering"].type == "insights"


def test_cli_export_filter_type(tmp_path):
    """export --type 过滤：仅导出指定类型。"""
    wiki = tmp_path / "wiki"
    store = _store(wiki)
    store.create(make_card(slug="driving-engineering", type="concepts"))
    store.create(make_card(slug="prompt-engineering", type="insights"))
    dst = tmp_path / "export"
    proc = _run_cli(["export", str(dst), "--type", "insights", "--wiki", str(wiki)],
                    cwd=_REPO_ROOT)
    assert proc.returncode == 0
    assert "导出 1 张卡片" in proc.stdout
    assert list(dst.glob("*.md")) == [dst / "prompt-engineering.md"]


def test_cli_list_grouped_and_stats(tmp_path):
    """list 分组 + 状态统计：不同 type/status 卡片分类正确。"""
    wiki = tmp_path / "wiki"
    store = _store(wiki)
    store.create(make_card(slug="driving-engineering", type="concepts"))
    store.create(make_card(slug="prompt-engineering", type="insights",
                           status="draft"))
    proc = _run_cli(["list", "--wiki", str(wiki)], cwd=_REPO_ROOT)
    assert proc.returncode == 0
    assert "[concepts] driving-engineering (current)" in proc.stdout
    assert "[insights] prompt-engineering (draft)" in proc.stdout
    assert "共 2 张卡片（current 1 / draft 1）" in proc.stdout


def test_cli_list_status_filter(tmp_path):
    """list --status 过滤：仅列出指定状态卡片。"""
    wiki = tmp_path / "wiki"
    store = _store(wiki)
    store.create(make_card(slug="driving-engineering"))
    store.create(make_card(slug="prompt-engineering", status="draft"))
    proc = _run_cli(["list", "--status", "draft", "--wiki", str(wiki)],
                    cwd=_REPO_ROOT)
    assert proc.returncode == 0
    assert "prompt-engineering" in proc.stdout
    assert "driving-engineering" not in proc.stdout
    assert "共 1 张卡片" in proc.stdout


def test_main_import_export_list_direct(tmp_path):
    """直接 main() 三命令返回值（供覆盖率统计）。"""
    from agent.knowledge.__main__ import main

    wiki = tmp_path / "wiki"
    store = _store(wiki)
    store.create(make_card(slug="driving-engineering"))
    src = tmp_path / "src"
    src.mkdir()
    _write_src_card(src / "a.md", "prompt-engineering")
    assert main(["import", str(src), "--wiki", str(wiki)]) == 0
    assert main(["list", "--wiki", str(wiki)]) == 0
    assert main(["export", str(tmp_path / "out"), "--wiki", str(wiki)]) == 0
    # 目录不存在 → import exit 1
    assert main(["import", str(tmp_path / "nope"), "--wiki", str(wiki)]) == 1
