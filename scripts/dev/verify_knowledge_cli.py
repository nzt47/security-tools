"""知识卡片引擎 CLI 主入口（python -m agent.knowledge）本地验证脚本。

用法（Windows PowerShell）：
    $env:PYTHONIOENCODING="utf-8"
    python scripts/dev/verify_knowledge_cli.py

验证项（每项带断言，全部通过打印 PASS，任一失败退出码非 0）：
  1. card-list：列出全部卡片（4 张）/ --status draft 过滤只出草稿卡
  2. card-transition：合法 current→archive 成功（触发归档重链，引用卡 links 改写）
  3. card-transition：非法 draft→archive 退出码 1（状态机拒绝）
  4. card-transition：卡片不存在退出码 1
  5. check-links：存在断链退出码 1，stderr 报检出数
  6. orphans：孤儿检测（草稿卡无入链）退出码 0
  7. index-rebuild：全量重建落盘 index.md，索引数正确（归档卡不计数）
  8. subprocess 模式退出码与直接 main() 一致（python -m agent.knowledge 真实入口）
  9. import：全合法成功 / 含损坏文件 rc=1（CI 门禁）/ 目录不存在 rc=1
     export：导出计数 + 落盘 + round-trip（export → import 回读）+ --type 过滤
     list：分组输出 + 状态统计 + --status 过滤 + subprocess 一致性

说明：全部在临时目录中构造 mock 数据，不污染真实 knowledge/ 目录；
日志级别 INFO，可观察 __main__.py 各 cmd_* 分支与 card/links/index 模块的
logger 打印（含耗时统计与断链调试日志）。
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import subprocess
import sys
import tempfile
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

# 使脚本可在任意 cwd 下运行：把项目根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.knowledge.card import CardStore  # noqa: E402
from agent.knowledge.__main__ import main as cli_main  # noqa: E402
from agent.knowledge.schema import Card, slugify  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [%(name)s] %(message)s",
)

_passed = 0
# --pre-commit 静默模式：抑制 PASS/章节/透传输出，失败仅打印 FAIL 到 stderr
_QUIET = False
# --traceback 失败堆栈模式：断言失败时打印完整调用链到 stderr（快速定位）
_TRACEBACK = False


def _p(msg: str = "") -> None:
    """按当前模式输出：静默模式吞掉常规进度信息。"""
    if not _QUIET:
        print(msg)


class _Tee(io.StringIO):
    """同时写入真实 stdout 与缓冲，便于断言输出且运行日志可见。

    静默模式下不透传（hook 输出干净），仅保留缓冲供断言。
    """

    def __init__(self) -> None:
        super().__init__()
        self._real = sys.stdout

    def write(self, s: str) -> int:
        if not _QUIET:
            self._real.write(s)
        return super().write(s)


def check(name: str, cond: bool, extra: str = "") -> None:
    """断言检查：失败立即退出非 0（FAIL 恒打印到 stderr，hook 可见）。

    --traceback 开启时，失败同时打印完整调用堆栈，便于定位到具体断言行。
    """
    global _passed
    if cond:
        _passed += 1
        _p(f"  [PASS] {name} {extra}")
    else:
        print(f"  [FAIL] {name} {extra}", file=sys.stderr)
        if _TRACEBACK:
            traceback.print_stack(file=sys.stderr)
        raise SystemExit(1)


def run_main(args: list[str]) -> str:
    """直接调用 CLI main() 并捕获 stdout（透传显示 + 返回捕获文本）。"""
    tee = _Tee()
    with redirect_stdout(tee):
        rc = cli_main(args)
    if rc != 0:
        raise SystemExit(f"CLI main({args}) 退出码 {rc}（预期 0）")
    return tee.getvalue()


def run_quiet(args: list[str]) -> int:
    """执行 CLI 调用并吞掉其 stdout/stderr 结果行（断言只看退出码）。

    用于负向场景（预期 rc=1）与静默模式下不关心的正向输出（孤儿列表等），
    避免「迁移失败/断链明细」噪音污染 hook 输出。
    """
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return cli_main(args)


def make_card(
    title: str,
    status: str = "current",
    type: str = "concepts",
    links=None,
) -> Card:
    return Card(
        title=title,
        slug=slugify(title),
        status=status,
        type=type,
        source="mock/inbox.md",
        date="2026-08-02",
        tags=[],
        links=links if links is not None else [],
        insight="一句话核心洞见",
    )


def _write_src_card(path: Path, title: str, *, type: str = "concepts") -> None:
    """写一张合法 frontmatter 源卡文件（slug 契约: slug == slugify(title)）。"""
    path.write_text(
        f"---\n"
        f"title: {title}\n"
        f"slug: {slugify(title)}\n"
        f"status: current\n"
        f"type: {type}\n"
        f"source: mock/{path.name}\n"
        f"date: 2026-08-02\n"
        f"tags: []\n"
        f"links: []\n"
        f"insight: 一句话核心洞见\n"
        f"---\n\n"
        f"正文\n",
        encoding="utf-8",
    )


def run_cli(args: list[str], cwd: str | Path) -> subprocess.CompletedProcess:
    """subprocess 跑 `python -m agent.knowledge`（UTF-8 防 Windows 控制台编码）。"""
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


def run() -> int:
    global _passed
    repo_root = Path(__file__).resolve().parents[2]
    tmp = Path(tempfile.mkdtemp(prefix="verify-cli-"))
    wiki = tmp / "wiki"
    index = tmp / "index.md"
    store = CardStore(wiki)

    _p("== 构造 mock 数据（4 张卡片，含双向链接/草稿/断链）==")
    # 双向链接：驾驭工程 → 提示词工程
    store.create(make_card("驾驭工程", links=["提示词工程"]))
    store.create(make_card("提示词工程", type="insights"))
    store.create(make_card("草稿卡", status="draft"))
    # 断链引用：指向不存在的「幽灵引用」
    store.create(make_card("断链引用", links=["幽灵引用"]))

    _p("\n== 1. card-list 列出卡片 ==")
    out = run_main(["card-list", "--wiki", str(wiki)])
    check("card-list 全量列出 4 张", "共 4 张卡片" in out)
    out = run_main(["card-list", "--status", "draft", "--wiki", str(wiki)])
    check("card-list --status draft 只出草稿卡",
          "草稿卡" in out and "断链引用" not in out)

    _p("\n== 2. card-transition 合法迁移（current→archive，触发归档重链）==")
    out = run_main(["card-transition", "提示词工程", "archive", "--wiki", str(wiki)])
    check("提示词工程 → archive 成功", "✓" in out)
    archived = store.get("archives/提示词工程")
    check("归档卡已移入 archives/", archived is not None and archived.status == "archive")
    referrer = store.get("驾驭工程")
    check("引用卡 links 改写为 archives/提示词工程",
          referrer is not None and referrer.links == ["archives/提示词工程"])

    _p("\n== 3. card-transition 非法迁移（draft→archive 被状态机拒绝）==")
    rc = run_quiet(["card-transition", "草稿卡", "archive", "--wiki", str(wiki)])
    check("草稿卡 draft→archive 拒绝", rc == 1, f"rc={rc}")

    _p("\n== 4. card-transition 卡片不存在 ==")
    rc = run_quiet(["card-transition", "幽灵卡", "archive", "--wiki", str(wiki)])
    check("不存在卡片迁移失败", rc == 1, f"rc={rc}")

    _p("\n== 5. check-links 断链检测 ==")
    rc = run_quiet(["check-links", "--wiki", str(wiki)])
    check("检出断链退出码 1", rc == 1, f"rc={rc}")

    _p("\n== 6. orphans 孤儿检测 ==")
    rc = run_quiet(["orphans", "--wiki", str(wiki)])
    check("孤儿检测退出码 0（报告型）", rc == 0, f"rc={rc}")

    _p("\n== 7. index-rebuild 全量重建 ==")
    out = run_main(["index-rebuild", "--wiki", str(wiki), "--index", str(index)])
    check("index-rebuild 成功", "全量重建完成" in out)
    text = index.read_text(encoding="utf-8")
    check("index.md 已落盘含头部", text.startswith("# 知识库全局索引"))
    check("归档卡不计数", "提示词工程" not in text and "驾驭工程" in text)

    _p("\n== 8. subprocess 模式：python -m agent.knowledge 退出码一致性 ==")
    proc = run_cli(["card-list", "--wiki", str(wiki)], cwd=repo_root)
    check("subprocess card-list rc=0", proc.returncode == 0, f"rc={proc.returncode}")
    assert "共 3 张卡片" in proc.stdout  # 归档后剩 3 张
    proc = run_cli(["check-links", "--wiki", str(wiki)], cwd=repo_root)
    check("subprocess check-links rc=1（断链门禁）",
          proc.returncode == 1, f"rc={proc.returncode}")
    assert "检出 1 条断链" in proc.stderr
    proc = run_cli(["card-transition", "草稿卡", "archive", "--wiki", str(wiki)], cwd=repo_root)
    check("subprocess card-transition 非法 rc=1",
          proc.returncode == 1, f"rc={proc.returncode}")
    assert "迁移失败" in proc.stderr
    proc = run_cli(["orphans", "--wiki", str(wiki)], cwd=repo_root)
    check("subprocess orphans rc=0", proc.returncode == 0, f"rc={proc.returncode}")

    _p("\n== 9. import / export / list 批量处理 ==")
    batch_wiki = tmp / "batch-wiki"
    src = tmp / "src"
    src.mkdir()
    _write_src_card(src / "a.md", "批量卡A")
    _write_src_card(src / "b.md", "批量卡B", type="insights")
    (src / "broken.md").write_text("# 无 frontmatter\n", encoding="utf-8")

    # 9.1 全合法导入 → exit 0，计数正确
    src2 = tmp / "src2"
    src2.mkdir()
    _write_src_card(src2 / "c.md", "干净卡")
    out = run_main(["import", str(src2), "--wiki", str(batch_wiki)])
    check("import 全合法 rc=0 且计数正确", "成功 1 / 跳过冲突 0 / 失败 0" in out)

    # 9.2 含损坏文件 → exit 1（CI 门禁），失败明细进 stderr
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        rc = cli_main(["import", str(src), "--wiki", str(batch_wiki)])
    check("import 含损坏文件 rc=1", rc == 1, f"rc={rc}")
    check("import 失败计数 stdout", "失败 1" in buf.getvalue())
    check("import 失败明细 stderr 含文件名", "broken.md" in err.getvalue())

    # 9.3 目录不存在 → exit 1
    rc = run_quiet(["import", str(tmp / "nope"), "--wiki", str(batch_wiki)])
    check("import 目录不存在 rc=1", rc == 1, f"rc={rc}")

    # 9.4 export → import round-trip（数量一致）
    dst = tmp / "export"
    out = run_main(["export", str(dst), "--wiki", str(batch_wiki)])
    check("export 导出 3 张卡", "导出 3 张卡片" in out)
    check("export 落盘文件", (dst / "批量卡a.md").exists())  # 文件名=slug（小写）
    wiki3 = tmp / "wiki3"
    out = run_main(["import", str(dst), "--wiki", str(wiki3)])
    check("round-trip import 成功 3 张", "成功 3" in out)
    check("round-trip 数量一致", len(CardStore(wiki3).list()) == 3)

    # 9.5 export --type 过滤
    dst2 = tmp / "export2"
    out = run_main(
        ["export", str(dst2), "--type", "insights", "--wiki", str(batch_wiki)]
    )
    check("export --type 过滤 1 张", "导出 1 张卡片" in out)
    check("export 过滤后仅 insights 文件",
          list(dst2.glob("*.md")) == [dst2 / "批量卡b.md"])

    # 9.6 list 分组 + 状态统计
    out = run_main(["list", "--wiki", str(batch_wiki)])
    check("list 分组输出",
          "[concepts] 批量卡a (current)" in out
          and "[insights] 批量卡b (current)" in out)
    check("list 状态统计", "共 3 张卡片（current 3）" in out)

    # 9.7 list --status 过滤
    out = run_main(["list", "--status", "draft", "--wiki", str(batch_wiki)])
    check("list --status draft 空结果", "共 0 张卡片" in out)

    # 9.8 subprocess 模式一致性
    proc = run_cli(
        ["import", str(src2), "--wiki", str(tmp / "wiki-s")], cwd=repo_root
    )
    check("subprocess import rc=0", proc.returncode == 0, f"rc={proc.returncode}")
    proc = run_cli(["list", "--wiki", str(batch_wiki)], cwd=repo_root)
    check("subprocess list rc=0", proc.returncode == 0, f"rc={proc.returncode}")

    _p(f"\n=== 全部通过：{_passed} 项断言 PASS ===")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：--pre-commit 提交前静默模式 + --traceback 失败堆栈模式。"""
    global _QUIET, _TRACEBACK
    parser = argparse.ArgumentParser(
        prog="python scripts/dev/verify_knowledge_cli.py",
        description="知识卡片 CLI 全生命周期 mock 断言（32 项）",
    )
    parser.add_argument(
        "--pre-commit",
        action="store_true",
        help="提交前静默模式：抑制常规输出，失败仅打印 FAIL 到 stderr 并 exit 1",
    )
    parser.add_argument(
        "--traceback",
        action="store_true",
        help="失败时打印完整调用堆栈到 stderr（快速定位断言行）",
    )
    args = parser.parse_args(argv)
    if args.pre_commit:
        _QUIET = True
        # hook 输出干净：仅保留 WARNING 及以上（迁移被拒等），关闭 INFO 噪音
        logging.getLogger().setLevel(logging.WARNING)
    if args.traceback:
        _TRACEBACK = True
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
