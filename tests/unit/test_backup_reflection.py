#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backup_reflection.py 单元测试（阶段5 C1 灰度发布前置）

覆盖：快照创建 / 文件与子目录递归复制 / keep 轮转 / 源目录缺失退出码。
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'scripts'))

from backup_reflection import backup  # noqa: E402


class TestBackupReflection:
    def test_备份创建快照并复制文件子目录(self, tmp_path):
        src = tmp_path / 'reflection'
        src.mkdir()
        (src / 'a.md').write_text('A', encoding='utf-8')
        (src / 'sub').mkdir()
        (src / 'sub' / 'b.json').write_text('{}', encoding='utf-8')
        root = tmp_path / 'backups'

        code = backup(src, root, keep=3)
        assert code == 0
        snaps = sorted(p for p in root.iterdir() if p.is_dir())
        assert len(snaps) == 1
        assert (snaps[0] / 'a.md').read_text(encoding='utf-8') == 'A'
        assert (snaps[0] / 'sub' / 'b.json').read_text(encoding='utf-8') == '{}'

    def test_keep轮转删除最旧快照(self, tmp_path):
        src = tmp_path / 'reflection'
        src.mkdir()
        (src / 'x.txt').write_text('x', encoding='utf-8')
        root = tmp_path / 'backups'

        # 连续备份 5 次（keep=3）→ 应只剩最近 3 份
        for _ in range(5):
            code = backup(src, root, keep=3)
            assert code == 0
        snaps = sorted(p.name for p in root.iterdir() if p.is_dir())
        assert len(snaps) == 3, f'期望保留 3 份，实际 {snaps}'
        # 快照名按时间戳字典序，最早的 2 份应已被删除
        assert snaps[0] < snaps[1] < snaps[2]

    def test_源目录缺失返回退出码1(self, tmp_path):
        code = backup(tmp_path / 'not_exist', tmp_path / 'out', keep=3)
        assert code == 1
