"""极端场景压力测试：数千张损坏卡下 light_loader 并行扫描顺序一致性。

【背景】scan_light_cards(parallel=True) 用线程池并发解析，损坏卡（无
frontmatter / YAML 语法错误）返回 None 被跳过；本脚本验证「大量损坏卡
穿插在正常卡之间」时，并行结果与串行结果在 slug 顺序上**完全一致**
（保序契约：类型目录序 + 组内 slug 字典序，不能被线程调度破坏）。

【构造】5000 张卡，损坏率 60%：
- 正常卡（40%）：合法 frontmatter，分散在三个类型目录、字母位置交错；
- 损坏卡（60%）：两种形态——无 frontmatter / YAML 语法错误，同样交错分布；
- 通过「损坏卡与正常卡按 0..N 逐张轮换文件名」保证排序上两者交错，
  并发跳过损坏卡时若顺序被破坏（如 ex.map 乱序/None 提前填充），断言即失败。

用法（仓库根目录下）：
    $env:PYTHONIOENCODING="utf-8"
    python scripts/dev/stress_light_loader_parallel.py [--n 5000] [--corrupt-ratio 0.6]

退出码：0 = 串行/并行 slug 顺序一致（契约满足）；1 = 不一致（回归）。
"""
from __future__ import annotations

import argparse
import random
import sys
import tempfile
import time
from pathlib import Path

# 使脚本可在任意 cwd 下运行：把仓库根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.knowledge.light_loader import (  # noqa: E402
    DEFAULT_TYPE_DIRS,
    scan_light_cards,
)

_GOOD_FM = (
    "---\nslug: {slug}\nstatus: current\ntype: {t}\ndate: 2026-08-01\n"
    "links:\n  - hub\n---\n\n正常卡片正文\n"
)
_NO_FM = "没有 frontmatter 的正文，解析会失败\n"
_BAD_YAML = "---\nslug: [{slug}\nstatus: current\n---\n\nYAML 语法错误\n"


def build_wiki(root: Path, n: int, corrupt_ratio: float) -> tuple[int, int]:
    """构造 n 张混合卡（正常 + 损坏交错），返回 (正常数, 损坏数)。"""
    rng = random.Random(20260811)  # 固定种子，结果可复现
    good = bad = 0
    for i in range(n):
        t = DEFAULT_TYPE_DIRS[i % len(DEFAULT_TYPE_DIRS)]
        d = root / t
        d.mkdir(parents=True, exist_ok=True)
        slug = f"card{i:05d}"  # 固定宽度 → 字典序即数值序，便于校验
        is_corrupt = rng.random() < corrupt_ratio
        if is_corrupt:
            bad += 1
            text = _NO_FM if i % 2 == 0 else _BAD_YAML.format(slug=slug)
        else:
            good += 1
            text = _GOOD_FM.format(slug=slug, t=t)
        (d / f"{slug}.md").write_text(text, encoding="utf-8")
    return good, bad


def main() -> int:
    parser = argparse.ArgumentParser(description="light_loader 并行顺序一致性压力测试")
    parser.add_argument("--n", type=int, default=5000, help="卡片总数（默认 5000）")
    parser.add_argument("--corrupt-ratio", type=float, default=0.6,
                        help="损坏卡比例（默认 0.6）")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="ll-stress-") as tmp:
        root = Path(tmp)
        good, bad = build_wiki(root / "wiki", args.n, args.corrupt_ratio)
        print(f"mock 库构造完成: 共 {args.n} 卡（正常 {good} / 损坏 {bad}）")

        t0 = time.perf_counter()
        serial = scan_light_cards(root / "wiki")
        t_serial = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        parallel = scan_light_cards(root / "wiki", parallel=True)
        t_parallel = (time.perf_counter() - t0) * 1000

        serial_slugs = [c.slug for c in serial]
        parallel_slugs = [c.slug for c in parallel]

        print(f"串行扫描: {len(serial)} 张正常卡 {t_serial:.1f}ms")
        print(f"并行扫描: {len(parallel)} 张正常卡 {t_parallel:.1f}ms")
        assert len(serial) == len(parallel) == good, (
            f"正常卡数量不一致: 串行 {len(serial)} / 并行 {len(parallel)} / 期望 {good}"
        )

        if serial_slugs != parallel_slugs:
            # 定位首个不一致位置（便于排查线程调度破坏顺序的证据）
            first = next(
                (i for i, (a, b) in enumerate(zip(serial_slugs, parallel_slugs))
                 if a != b),
                -1,
            )
            print(f"✗ 顺序不一致: 首个差异位置={first} "
                  f"串行={serial_slugs[first:first + 3]} 并行={parallel_slugs[first:first + 3]}")
            return 1

        # 校验结果符合保序契约：类型目录序 + 组内 slug 字典序
        by_type: dict[str, list[str]] = {}
        for c in serial:
            by_type.setdefault(c.type, []).append(c.slug)
        assert list(by_type) == list(DEFAULT_TYPE_DIRS), (
            f"类型目录顺序被破坏: {list(by_type)}"
        )
        for t, slugs in by_type.items():
            assert slugs == sorted(slugs), f"目录 {t} 组内未按 slug 字典序: {slugs[:5]}"
        print(f"✓ 顺序一致: 串行/并行 {good} 张 slug 完全一致；"
              f"组序={list(by_type)} 各组内字典序成立")
        print(f"✓ 耗时: 串行 {t_serial:.1f}ms → 并行 {t_parallel:.1f}ms "
              f"({t_serial / t_parallel:.2f}x)")
        print("结论: 数千张损坏卡并存时，light_loader 并行扫描顺序与串行完全一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
