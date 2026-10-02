"""JSONL 历史读写工具（agent/jsonl_history.py）单元测试

【为什么单独测这个工具】它有两个**消费方**（后台任务历史 / 委派记录），且两条契约
一旦破掉都是"静默失真"而不是报错：

  1. 尾部窗口读取必须**丢弃窗口首行的半行** —— 否则会冒出一条看似真实的假记录；
  2. 计数在超大文件上必须返回 ``None``（"未统计"），不能返回一个截断后
     看着精确的数字（本仓对"静默失真"的既定态度：宁可缺失，不要假的精确）。

故这里不测"函数能跑"，而是逐条钉死上面这些边界。
"""
from __future__ import annotations

import json
import os

from agent.jsonl_history import (DEFAULT_COUNT_BYTES, DEFAULT_TAIL_BYTES,
                                 append_jsonl, count_jsonl_lines,
                                 read_jsonl_tail)


def _write(path, records):
    for r in records:
        append_jsonl(str(path), r)


class TestAppendAndRead:
    def test_追加后可原序读回(self, tmp_path):
        p = tmp_path / "h.jsonl"
        _write(p, [{"i": 0}, {"i": 1}, {"i": 2}])
        assert [r["i"] for r in read_jsonl_tail(str(p), 10)] == [0, 1, 2]

    def test_只取最后_limit_条(self, tmp_path):
        p = tmp_path / "h.jsonl"
        _write(p, [{"i": i} for i in range(10)])
        assert [r["i"] for r in read_jsonl_tail(str(p), 3)] == [7, 8, 9]

    def test_limit_非正数返回空(self, tmp_path):
        p = tmp_path / "h.jsonl"
        _write(p, [{"i": 0}])
        assert read_jsonl_tail(str(p), 0) == []
        assert read_jsonl_tail(str(p), -5) == []

    def test_中文不被转义成_uXXXX_且能读回(self, tmp_path):
        """ensure_ascii=False：历史文件要给人看，``\u8fc7`` 那种形态没法排查"""
        p = tmp_path / "h.jsonl"
        append_jsonl(str(p), {"name": "过程蒸馏"})
        raw = p.read_text(encoding="utf-8")
        assert "过程蒸馏" in raw
        assert read_jsonl_tail(str(p), 1)[0]["name"] == "过程蒸馏"


class TestTailWindow:
    def test_窗口首行的半行被丢弃(self, tmp_path):
        """**核心不变量**：从字节偏移切开时，首行几乎必然是半行，不能当成记录"""
        p = tmp_path / "h.jsonl"
        _write(p, [{"i": i, "pad": "x" * 40} for i in range(20)])
        got = read_jsonl_tail(str(p), 20, max_bytes=90)
        assert got, "窗口内应至少能读到一条完整记录"
        assert all(isinstance(r.get("i"), int) for r in got), f"混入了半行: {got}"
        assert [r["i"] for r in got] == sorted(r["i"] for r in got)

    def test_窗口内取最后_limit_条(self, tmp_path):
        p = tmp_path / "h.jsonl"
        _write(p, [{"i": i, "pad": "y" * 60} for i in range(30)])
        got = read_jsonl_tail(str(p), 2, max_bytes=DEFAULT_TAIL_BYTES)
        assert [r["i"] for r in got] == [28, 29]


class TestRobustness:
    def test_坏行跳过而不是中断(self, tmp_path):
        p = tmp_path / "h.jsonl"
        with open(p, "a", encoding="utf-8") as f:
            f.write('{"i": 1}\n')
            f.write("{ 这不是 JSON\n")
            f.write('\n')  # 空行
            f.write('[1, 2, 3]\n')  # 合法 JSON 但不是对象
            f.write('{"i": 2}\n')
        assert [r["i"] for r in read_jsonl_tail(str(p), 10)] == [1, 2]

    def test_文件不存在等于空历史(self, tmp_path):
        missing = str(tmp_path / "nope.jsonl")
        assert read_jsonl_tail(missing, 5) == []
        assert count_jsonl_lines(missing) == 0

    def test_空文件等于空历史(self, tmp_path):
        p = tmp_path / "empty.jsonl"
        p.write_text("", encoding="utf-8")
        assert read_jsonl_tail(str(p), 5) == []
        assert count_jsonl_lines(str(p)) == 0

    def test_追加失败只返回_False_不抛(self, tmp_path):
        """路径是目录 ⇒ 写入必失败；调用方（委派/任务链路）不能被它打挂"""
        assert append_jsonl(str(tmp_path), {"i": 1}) is False

    def test_不可序列化记录只返回_False_不抛(self, tmp_path):
        p = str(tmp_path / "h.jsonl")
        assert append_jsonl(p, {"bad": object()}) is False
        assert read_jsonl_tail(p, 5) == []

    def test_追加自动建父目录(self, tmp_path):
        p = tmp_path / "deep" / "nested" / "h.jsonl"
        assert append_jsonl(str(p), {"i": 1}) is True
        assert os.path.isfile(p)


class TestCount:
    def test_只数非空行(self, tmp_path):
        p = tmp_path / "h.jsonl"
        with open(p, "a", encoding="utf-8") as f:
            f.write('{"i": 1}\n\n{"i": 2}\n')
            f.write('{"i": 3}')  # 末行无换行
        assert count_jsonl_lines(str(p)) == 3

    def test_反序列化失败不影响计数(self, tmp_path):
        """计数是"文件里有多少行"，不是"有多少条合法记录"（两者语义不同，别混）"""
        p = tmp_path / "h.jsonl"
        with open(p, "a", encoding="utf-8") as f:
            f.write("坏行\n{\"i\": 1}\n")
        assert count_jsonl_lines(str(p)) == 2

    def test_超大文件返回_None_而不是截断数字(self, tmp_path):
        p = tmp_path / "h.jsonl"
        _write(p, [{"i": i, "pad": "z" * 50} for i in range(10)])
        assert count_jsonl_lines(str(p)) == 10
        assert count_jsonl_lines(str(p), max_count_bytes=32) is None

    def test_默认上限是个正数(self):
        assert DEFAULT_COUNT_BYTES > 0
        assert DEFAULT_TAIL_BYTES > 0

    def test_计数不因分块边界丢行(self, tmp_path):
        """1 MiB 分块计数：跨块边界时既不能丢行也不能重复计数"""
        p = tmp_path / "h.jsonl"
        pad = "k" * 1000
        with open(p, "w", encoding="utf-8") as f:
            for i in range(3000):
                f.write(json.dumps({"i": i, "pad": pad}, ensure_ascii=False) + "\n")
        assert count_jsonl_lines(str(p)) == 3000
