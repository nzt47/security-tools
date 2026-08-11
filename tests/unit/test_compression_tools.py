"""压缩/解压工具单元测试 — 覆盖正常路径、错误分支与路径安全防护。

对应 agent/compression_tools.py：
- compress / decompress 公开接口（参数校验、路径安全、格式检测、异常处理）
- _safe_extract_zip / _safe_extract_tar 内部安全解压（Zip Slip / 路径遍历防护）
- _add_large_file_to_zip / _extract_large_zip_member 大文件分块分支

Why 真实文件：压缩/解压逻辑依赖 zipfile/tarfile 真实读写，
使用 tmp_path 创建几 KB 小文件做压缩/解压往返验证；
超过 100MB 的大文件分支通过 mock 触发。
"""
# pylint: disable=redefined-outer-name,missing-function-docstring,protected-access

import io
import os
import tarfile
import zipfile
from unittest.mock import patch

import pytest

from agent import compression_tools as ct


def _make_zip(path, members):
    """创建 zip 文件：members 为 {arcname: bytes}"""
    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return path


def _make_tar_gz(path, members):
    """创建 tar.gz 文件：members 为 {arcname: bytes}"""
    with tarfile.open(str(path), "w:gz") as tf:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return path


class _FakeZipInfo:
    """模拟 zipfile.ZipInfo 最小接口"""

    def __init__(self, filename, file_size=10, is_dir=False):
        self.filename = filename
        self.file_size = file_size
        self._is_dir = is_dir

    def is_dir(self):
        return self._is_dir


class _FakeTarInfo:
    """模拟 tarfile.TarInfo 最小接口"""

    def __init__(self, name, size=10, isfile=True):
        self.name = name
        self.size = size
        self._isfile = isfile

    def isfile(self):
        return self._isfile


class _FakeZipFile:
    """模拟 zipfile.ZipFile 读取接口（with 上下文）"""

    def __init__(self, members):
        self._members = members

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def infolist(self):
        return self._members

    def open(self, member):
        return io.BytesIO(b"x" * min(member.file_size, 4096))


class _FakeTarFile:
    """模拟 tarfile.TarFile 读取接口（with 上下文）"""

    def __init__(self, members):
        self._members = members

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def getmembers(self):
        return self._members

    def extractfile(self, member):
        return io.BytesIO(b"x" * min(member.size, 4096))

    def extract(self, member, path, set_attrs=True):
        target = os.path.join(path, member.name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(b"x" * member.size)


class TestTraceId:
    """_trace_id() 生成短 trace_id"""

    def test_trace_id_hex_length(self):
        """trace_id 为 16 位十六进制字符串"""
        tid = ct._trace_id()
        assert len(tid) == 16
        int(tid, 16)  # 合法十六进制


class TestCompress:
    """compress() 单元测试"""

    def test_compress_file_to_zip(self, tmp_path):
        """压缩单个文件为 zip，往返验证内容"""
        src = tmp_path / "data.txt"
        src.write_text("hello compression", encoding="utf-8")
        out = tmp_path / "data.zip"
        result = ct.compress(str(src), str(out))
        assert result["ok"] is True
        assert result["format"] == "zip"
        assert result["file_count"] == 1
        assert result["output_path"] == str(out)
        assert result["compressed_size"] > 0
        assert out.exists()
        with zipfile.ZipFile(str(out)) as zf:
            assert zf.read("data.txt") == b"hello compression"

    def test_compress_dir_to_zip(self, tmp_path):
        """压缩目录（含子目录）为 zip，归档名相对父目录"""
        src_dir = tmp_path / "src"
        (src_dir / "sub").mkdir(parents=True)
        (src_dir / "a.txt").write_text("aaa", encoding="utf-8")
        (src_dir / "sub" / "b.txt").write_text("bbb", encoding="utf-8")
        out = tmp_path / "dir.zip"
        result = ct.compress(str(src_dir), str(out))
        assert result["ok"] is True
        assert result["file_count"] == 2
        with zipfile.ZipFile(str(out)) as zf:
            names = set(zf.namelist())
            assert "src/a.txt" in names
            assert "src/sub/b.txt" in names

    def test_compress_tar_gz(self, tmp_path):
        """压缩为 tar.gz 格式"""
        src = tmp_path / "data.txt"
        src.write_text("hello tar", encoding="utf-8")
        out = tmp_path / "data.tar.gz"
        result = ct.compress(str(src), str(out), format="tar.gz")
        assert result["ok"] is True
        assert result["format"] == "tar.gz"
        with tarfile.open(str(out), "r:gz") as tf:
            names = tf.getnames()
        assert "data.txt" in names

    def test_compress_tgz_alias(self, tmp_path):
        """tgz 缩写归一化为 tar.gz"""
        src = tmp_path / "data.txt"
        src.write_text("x", encoding="utf-8")
        out = tmp_path / "data.tgz"
        result = ct.compress(str(src), str(out), format="tgz")
        assert result["ok"] is True
        assert result["format"] == "tar.gz"

    def test_compress_uppercase_format(self, tmp_path):
        """大写格式名经 lower/strip 归一化后可用"""
        src = tmp_path / "data.txt"
        src.write_text("x", encoding="utf-8")
        out = tmp_path / "data.zip"
        result = ct.compress(str(src), str(out), format="ZIP")
        assert result["ok"] is True

    def test_compress_unsupported_format(self, tmp_path):
        """不支持的压缩格式返回错误"""
        src = tmp_path / "data.txt"
        src.write_text("x", encoding="utf-8")
        result = ct.compress(str(src), str(tmp_path / "o.rar"), format="rar")
        assert result["ok"] is False
        assert "不支持的压缩格式" in result["error"]

    def test_compress_auto_output_path(self, tmp_path):
        """未指定输出路径时自动生成 源名.zip 于同级目录"""
        src = tmp_path / "data.txt"
        src.write_text("x", encoding="utf-8")
        result = ct.compress(str(src))
        assert result["ok"] is True
        assert result["output_path"] == str(tmp_path / "data.txt.zip")
        assert (tmp_path / "data.txt.zip").exists()

    def test_compress_auto_output_path_tar_gz(self, tmp_path):
        """未指定输出路径且格式为 tar.gz 时生成 源名.tar.gz"""
        src = tmp_path / "data.txt"
        src.write_text("x", encoding="utf-8")
        result = ct.compress(str(src), format="tar.gz")
        assert result["ok"] is True
        assert result["output_path"] == str(tmp_path / "data.txt.tar.gz")

    def test_compress_source_not_exists(self, tmp_path):
        """源路径不存在返回错误"""
        result = ct.compress(str(tmp_path / "nope.txt"), str(tmp_path / "o.zip"))
        assert result["ok"] is False
        assert "源路径不存在" in result["error"]

    def test_compress_empty_dir(self, tmp_path):
        """空目录无可压缩文件时返回错误"""
        src_dir = tmp_path / "empty"
        src_dir.mkdir()
        result = ct.compress(str(src_dir), str(tmp_path / "o.zip"))
        assert result["ok"] is False
        assert "没有可压缩的文件" in result["error"]

    def test_compress_safe_resolve_rejected(self, tmp_path):
        """safe_resolve_path 拒绝源路径时返回错误"""
        with patch("agent.system_tools.safe_resolve_path",
                   side_effect=ValueError("路径位于系统保护目录")):
            result = ct.compress(str(tmp_path / "x.txt"), str(tmp_path / "o.zip"))
        assert result["ok"] is False
        assert "路径位于系统保护目录" in result["error"]

    def test_compress_output_resolve_rejected(self, tmp_path):
        """输出路径被 safe_resolve_path 拒绝时返回错误"""
        src = tmp_path / "data.txt"
        src.write_text("x", encoding="utf-8")
        with patch("agent.system_tools.safe_resolve_path",
                   side_effect=[str(src), ValueError("输出路径不安全")]):
            result = ct.compress(str(src), str(tmp_path / "o.zip"))
        assert result["ok"] is False
        assert "输出路径不安全" in result["error"]

    def test_compress_makedirs_fails(self, tmp_path):
        """无法创建输出目录时返回错误"""
        src = tmp_path / "data.txt"
        src.write_text("x", encoding="utf-8")
        with patch("agent.compression_tools.os.makedirs",
                   side_effect=OSError("denied")):
            result = ct.compress(str(src), str(tmp_path / "sub" / "o.zip"))
        assert result["ok"] is False
        assert "无法创建输出目录" in result["error"]

    def test_compress_failure_cleans_partial_output(self, tmp_path):
        """压缩失败时清理可能生成的不完整输出文件"""
        src = tmp_path / "data.txt"
        src.write_text("x", encoding="utf-8")
        out = tmp_path / "data.zip"
        out.write_text("partial", encoding="utf-8")  # 模拟已存在的不完整文件
        with patch("agent.compression_tools._compress_zip",
                   side_effect=RuntimeError("boom")):
            result = ct.compress(str(src), str(out))
        assert result["ok"] is False
        assert "压缩失败" in result["error"]
        assert not out.exists()  # 不完整文件被清理

    def test_compress_failure_remove_output_error_silent(self, tmp_path):
        """压缩失败且清理不完整输出文件也失败时静默吞掉"""
        src = tmp_path / "data.txt"
        src.write_text("x", encoding="utf-8")
        out = tmp_path / "data.zip"
        out.write_text("partial", encoding="utf-8")
        with patch("agent.compression_tools._compress_zip",
                   side_effect=RuntimeError("boom")), \
             patch("agent.compression_tools.os.remove",
                   side_effect=OSError("denied")):
            result = ct.compress(str(src), str(out))
        assert result["ok"] is False
        assert "压缩失败" in result["error"]

    def test_compress_tar_failure_cleans_partial_output(self, tmp_path):
        """tar.gz 压缩失败时同样清理不完整输出文件"""
        src = tmp_path / "data.txt"
        src.write_text("x", encoding="utf-8")
        out = tmp_path / "data.tar.gz"
        out.write_text("partial", encoding="utf-8")
        with patch("agent.compression_tools._compress_tar_gz",
                   side_effect=RuntimeError("boom")):
            result = ct.compress(str(src), str(out), format="tar.gz")
        assert result["ok"] is False
        assert not out.exists()

    def test_compress_progress_callback_invoked(self, tmp_path):
        """压缩时进度回调按 (current, total, filename) 被调用"""
        src = tmp_path / "data.txt"
        src.write_text("x", encoding="utf-8")
        calls = []
        result = ct.compress(str(src), str(tmp_path / "o.zip"),
                             progress_callback=lambda c, t, f: calls.append((c, t, f)))
        assert result["ok"] is True
        assert calls == [(1, 1, "data.txt")]

    def test_compress_large_file_uses_chunked_write(self, tmp_path):
        """超过 100MB 的大文件走分块写入分支"""
        src = tmp_path / "big.txt"
        src.write_text("x" * 100, encoding="utf-8")
        out = tmp_path / "big.zip"
        big_size = 101 * 1024 * 1024
        with patch("agent.compression_tools.os.path.getsize", return_value=big_size):
            result = ct.compress(str(src), str(out))
        # getsize 亦被压缩完成统计复用，故 compressed_size 为 mock 值
        assert result["ok"] is True
        assert result["compressed_size"] == big_size
        assert out.exists()

    def test_compress_zip_callback_exception_swallowed(self, tmp_path):
        """进度回调抛异常不影响压缩主流程"""
        src = tmp_path / "data.txt"
        src.write_text("x", encoding="utf-8")

        def bad_cb(current, total, filename):
            raise RuntimeError("callback boom")

        result = ct.compress(str(src), str(tmp_path / "o.zip"), progress_callback=bad_cb)
        assert result["ok"] is True

    def test_compress_tar_callback_and_exception_swallowed(self, tmp_path):
        """tar.gz 压缩调用进度回调，回调抛异常被吞掉"""
        src = tmp_path / "data.txt"
        src.write_text("x", encoding="utf-8")
        calls = []

        def bad_cb(current, total, filename):
            calls.append((current, total, filename))
            raise RuntimeError("callback boom")

        result = ct.compress(str(src), str(tmp_path / "o.tar.gz"),
                             format="tar.gz", progress_callback=bad_cb)
        assert result["ok"] is True
        assert calls == [(1, 1, "data.txt")]


class TestAddLargeFileToZip:
    """_add_large_file_to_zip() 大文件分块写入"""

    def test_add_large_file_to_zip_chunked(self, tmp_path):
        """分块写入后 zip 内容与源文件一致"""
        src = tmp_path / "big.txt"
        src.write_text("data" * 1000, encoding="utf-8")
        out = tmp_path / "big.zip"
        with zipfile.ZipFile(str(out), "w") as zf:
            ct._add_large_file_to_zip(zf, str(src), "big.txt")
        with zipfile.ZipFile(str(out)) as zf:
            assert zf.read("big.txt") == b"data" * 1000


class TestDecompress:
    """decompress() 单元测试"""

    def test_decompress_zip(self, tmp_path):
        """解压 zip 到指定目录，验证文件与返回字段"""
        arc = tmp_path / "archive.zip"
        _make_zip(arc, {"a.txt": b"hello", "sub/b.txt": b"world"})
        out_dir = tmp_path / "out"
        result = ct.decompress(str(arc), str(out_dir))
        assert result["ok"] is True
        assert result["format"] == "zip"
        assert result["file_count"] == 2
        assert result["output_dir"] == str(out_dir)
        assert result["extracted_size"] > 0
        assert (out_dir / "a.txt").read_bytes() == b"hello"
        assert (out_dir / "sub" / "b.txt").read_bytes() == b"world"

    def test_decompress_default_output_dir(self, tmp_path):
        """未指定输出目录时解压到压缩文件同名目录"""
        arc = tmp_path / "archive.zip"
        _make_zip(arc, {"a.txt": b"x"})
        result = ct.decompress(str(arc))
        assert result["ok"] is True
        assert result["output_dir"] == str(tmp_path / "archive")
        assert (tmp_path / "archive" / "a.txt").exists()

    def test_decompress_tar_gz(self, tmp_path):
        """解压 tar.gz 到指定目录"""
        arc = tmp_path / "archive.tar.gz"
        _make_tar_gz(arc, {"a.txt": b"hello"})
        out_dir = tmp_path / "out"
        result = ct.decompress(str(arc), str(out_dir))
        assert result["ok"] is True
        assert result["format"] == "tar.gz"
        assert (out_dir / "a.txt").read_bytes() == b"hello"

    def test_decompress_tar(self, tmp_path):
        """.tar 后缀识别为 tar 格式"""
        arc = tmp_path / "archive.tar"
        with tarfile.open(str(arc), "w") as tf:
            info = tarfile.TarInfo("a.txt")
            info.size = 5
            tf.addfile(info, io.BytesIO(b"hello"))
        result = ct.decompress(str(arc), str(tmp_path / "out"))
        assert result["ok"] is True
        assert result["format"] == "tar"

    def test_decompress_default_output_dir_tar_gz_double_ext(self, tmp_path):
        """.tar.gz 双扩展名默认输出目录去除 .tar 后缀"""
        arc = tmp_path / "archive.tar.gz"
        _make_tar_gz(arc, {"a.txt": b"x"})
        result = ct.decompress(str(arc))
        assert result["output_dir"] == str(tmp_path / "archive")

    def test_decompress_format_detected_by_magic_zip(self, tmp_path):
        """无常见后缀时通过 magic bytes 识别 zip"""
        arc = tmp_path / "data.bin"
        _make_zip(arc, {"a.txt": b"x"})
        result = ct.decompress(str(arc), str(tmp_path / "out"))
        assert result["ok"] is True
        assert result["format"] == "zip"

    def test_decompress_format_detected_by_magic_gzip(self, tmp_path):
        """无常见后缀时通过 magic bytes 识别 gzip"""
        arc = tmp_path / "data.bin"
        _make_tar_gz(arc, {"a.txt": b"x"})
        result = ct.decompress(str(arc), str(tmp_path / "out"))
        assert result["ok"] is True
        assert result["format"] == "tar.gz"

    def test_decompress_unknown_format(self, tmp_path):
        """magic bytes 无法识别时返回错误"""
        arc = tmp_path / "data.bin"
        arc.write_bytes(b"plain text not an archive")
        result = ct.decompress(str(arc), str(tmp_path / "out"))
        assert result["ok"] is False
        assert "无法识别压缩格式" in result["error"]

    def test_decompress_magic_read_error(self, tmp_path):
        """读取 magic bytes 失败时返回错误"""
        arc = tmp_path / "data.bin"
        arc.write_bytes(b"abc")
        with patch("agent.compression_tools.open",
                   side_effect=OSError("io error")):
            result = ct.decompress(str(arc), str(tmp_path / "out"))
        assert result["ok"] is False
        assert "无法读取文件" in result["error"]

    def test_decompress_safe_resolve_rejected(self, tmp_path):
        """源文件被 safe_resolve_path 拒绝时返回错误"""
        with patch("agent.system_tools.safe_resolve_path",
                   side_effect=ValueError("拒绝访问")):
            result = ct.decompress(str(tmp_path / "a.zip"), str(tmp_path / "out"))
        assert result["ok"] is False
        assert "拒绝访问" in result["error"]

    def test_decompress_file_not_exists(self, tmp_path):
        """源文件不存在时返回错误"""
        result = ct.decompress(str(tmp_path / "missing.zip"), str(tmp_path / "out"))
        assert result["ok"] is False
        assert "文件不存在" in result["error"]

    def test_decompress_path_is_dir(self, tmp_path):
        """源路径是目录时返回错误"""
        result = ct.decompress(str(tmp_path), str(tmp_path / "out"))
        assert result["ok"] is False
        assert "路径不是文件" in result["error"]

    def test_decompress_output_dir_rejected(self, tmp_path):
        """输出目录被 safe_resolve_path 拒绝时返回错误"""
        arc = tmp_path / "a.zip"
        _make_zip(arc, {"f.txt": b"x"})
        with patch("agent.system_tools.safe_resolve_path",
                   side_effect=[str(arc), ValueError("输出目录不安全")]):
            result = ct.decompress(str(arc), str(tmp_path / "out"))
        assert result["ok"] is False
        assert "输出目录不安全" in result["error"]

    def test_decompress_protected_output_dir(self, tmp_path):
        """输出目录位于系统保护目录时拒绝解压"""
        arc = tmp_path / "a.zip"
        _make_zip(arc, {"f.txt": b"x"})
        with patch("agent.system_tools.is_protected_path", return_value=True):
            result = ct.decompress(str(arc), str(tmp_path / "out"))
        assert result["ok"] is False
        assert "系统保护目录" in result["error"]

    def test_decompress_makedirs_fails(self, tmp_path):
        """无法创建输出目录时返回错误"""
        arc = tmp_path / "a.zip"
        _make_zip(arc, {"f.txt": b"x"})
        with patch("agent.compression_tools.os.makedirs",
                   side_effect=OSError("denied")):
            result = ct.decompress(str(arc), str(tmp_path / "out"))
        assert result["ok"] is False
        assert "无法创建输出目录" in result["error"]

    def test_decompress_bad_zip(self, tmp_path):
        """损坏的 zip 文件返回 ZIP 损坏错误"""
        arc = tmp_path / "bad.zip"
        arc.write_bytes(b"not a real zip file")
        result = ct.decompress(str(arc), str(tmp_path / "out"))
        assert result["ok"] is False
        assert "ZIP 文件损坏" in result["error"]

    def test_decompress_bad_tar(self, tmp_path):
        """损坏的 tar.gz 文件返回 TAR 损坏错误"""
        arc = tmp_path / "bad.tar.gz"
        arc.write_bytes(b"not a real gzip stream")
        result = ct.decompress(str(arc), str(tmp_path / "out"))
        assert result["ok"] is False
        assert "TAR 文件损坏" in result["error"]

    def test_decompress_generic_failure(self, tmp_path):
        """解压过程其他异常返回解压失败"""
        arc = tmp_path / "a.zip"
        _make_zip(arc, {"f.txt": b"x"})
        with patch("agent.compression_tools._safe_extract_zip",
                   side_effect=RuntimeError("boom")):
            result = ct.decompress(str(arc), str(tmp_path / "out"))
        assert result["ok"] is False
        assert "解压失败" in result["error"]

    def test_decompress_zip_slip_blocked(self, tmp_path):
        """Zip Slip 攻击（../ 成员）被防护，不逃逸到输出目录外"""
        arc = tmp_path / "evil.zip"
        with zipfile.ZipFile(str(arc), "w") as zf:
            zf.writestr("../evil.txt", b"pwned")
            zf.writestr("ok.txt", b"fine")
        out_dir = tmp_path / "out"
        result = ct.decompress(str(arc), str(out_dir))
        assert result["ok"] is True
        assert (out_dir / "ok.txt").read_bytes() == b"fine"
        assert not (tmp_path / "evil.txt").exists()  # 未逃逸到父目录

    def test_decompress_tar_path_traversal_blocked(self, tmp_path):
        """tar 路径遍历（../ 成员）被防护，不逃逸到输出目录外"""
        arc = tmp_path / "evil.tar.gz"
        with tarfile.open(str(arc), "w:gz") as tf:
            info = tarfile.TarInfo("../evil.txt")
            info.size = 6
            tf.addfile(info, io.BytesIO(b"pwned!"))
            info2 = tarfile.TarInfo("ok.txt")
            info2.size = 4
            tf.addfile(info2, io.BytesIO(b"fine"))
        out_dir = tmp_path / "out"
        result = ct.decompress(str(arc), str(out_dir))
        assert result["ok"] is True
        assert (out_dir / "ok.txt").read_bytes() == b"fine"
        assert not (tmp_path / "evil.txt").exists()  # 未逃逸到父目录


class TestSafeExtractZip:
    """_safe_extract_zip() 内部安全解压"""

    def test_no_file_members_returns_zero(self, tmp_path):
        """仅含目录条目时返回 (0, 0)"""
        fake = _FakeZipFile([_FakeZipInfo("dir/", is_dir=True)])
        with patch("agent.compression_tools.zipfile.ZipFile", return_value=fake):
            count, size = ct._safe_extract_zip("d.zip", str(tmp_path))
        assert (count, size) == (0, 0)

    def test_small_member_extracted(self, tmp_path):
        """普通小文件成员被真实解压"""
        fake = _FakeZipFile([_FakeZipInfo("ok.txt", file_size=4)])
        with patch("agent.compression_tools.zipfile.ZipFile", return_value=fake):
            count, size = ct._safe_extract_zip("d.zip", str(tmp_path))
        assert count == 1
        assert size > 0
        assert (tmp_path / "ok.txt").exists()

    def test_zip_slip_member_skipped(self, tmp_path):
        """含 ../ 的成员被跳过，其余成员正常解压"""
        fake = _FakeZipFile([_FakeZipInfo("../evil.txt"), _FakeZipInfo("ok.txt")])
        with patch("agent.compression_tools.zipfile.ZipFile", return_value=fake):
            count, size = ct._safe_extract_zip("d.zip", str(tmp_path))
        # total_files 统计全部文件成员（含被跳过的）
        assert count == 2
        assert (tmp_path / "ok.txt").exists()
        assert not (tmp_path.parent / "evil.txt").exists()

    def test_absolute_path_member_skipped(self, tmp_path):
        """绝对路径成员被跳过"""
        abs_name = os.path.abspath(os.path.join(str(tmp_path), "abs.txt"))
        fake = _FakeZipFile([_FakeZipInfo(abs_name)])
        with patch("agent.compression_tools.zipfile.ZipFile", return_value=fake):
            count, size = ct._safe_extract_zip("d.zip", str(tmp_path))
        assert size == 0

    def test_realpath_escape_skipped(self, tmp_path):
        """解析后逃逸出输出目录的成员被二次防护拦截"""
        fake = _FakeZipFile([_FakeZipInfo("ok.txt")])
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        with patch("agent.compression_tools.zipfile.ZipFile", return_value=fake), \
             patch("agent.compression_tools.os.path.realpath",
                   side_effect=["C:/outside/evil.txt", str(out_dir)]):
            count, size = ct._safe_extract_zip("d.zip", str(out_dir))
        assert size == 0  # 逃逸成员被跳过

    def test_large_member_uses_chunked_extract(self, tmp_path):
        """超过 100MB 的成员走分块解压"""
        fake = _FakeZipFile([_FakeZipInfo("big.bin", file_size=101 * 1024 * 1024)])
        with patch("agent.compression_tools.zipfile.ZipFile", return_value=fake):
            count, size = ct._safe_extract_zip("d.zip", str(tmp_path))
        assert count == 1
        assert (tmp_path / "big.bin").exists()

    def test_callback_exception_swallowed(self, tmp_path):
        """进度回调抛异常不影响解压"""
        def bad_cb(*args):
            raise RuntimeError("boom")

        fake = _FakeZipFile([_FakeZipInfo("ok.txt")])
        with patch("agent.compression_tools.zipfile.ZipFile", return_value=fake):
            count, size = ct._safe_extract_zip("d.zip", str(tmp_path), bad_cb)
        assert count == 1
        assert (tmp_path / "ok.txt").exists()


class TestExtractLargeZipMember:
    """_extract_large_zip_member() 分块解压大成员"""

    def test_chunked_write_roundtrip(self, tmp_path):
        """分块解压内容与源流一致"""
        member = _FakeZipInfo("big.bin", file_size=10)
        fake = _FakeZipFile([member])
        target = tmp_path / "big.bin"
        ct._extract_large_zip_member(fake, member, str(target))
        assert target.read_bytes() == b"x" * 10


class TestSafeExtractTar:
    """_safe_extract_tar() 内部安全解压"""

    def test_no_file_members_returns_zero(self, tmp_path):
        """仅含目录条目时返回 (0, 0)"""
        fake = _FakeTarFile([_FakeTarInfo("dir/", isfile=False)])
        with patch("agent.compression_tools.tarfile.open", return_value=fake):
            count, size = ct._safe_extract_tar("d.tar.gz", str(tmp_path))
        assert (count, size) == (0, 0)

    def test_small_member_extracted(self, tmp_path):
        """普通小文件成员走 tarfile.extract"""
        fake = _FakeTarFile([_FakeTarInfo("small.txt", size=4)])
        with patch("agent.compression_tools.tarfile.open", return_value=fake):
            count, size = ct._safe_extract_tar("d.tar.gz", str(tmp_path))
        assert count == 1
        assert size == 4
        assert (tmp_path / "small.txt").exists()

    def test_path_traversal_member_skipped(self, tmp_path):
        """含 ../ 的成员被跳过，其余成员正常解压"""
        fake = _FakeTarFile([_FakeTarInfo("../evil.txt"), _FakeTarInfo("ok.txt")])
        with patch("agent.compression_tools.tarfile.open", return_value=fake):
            count, size = ct._safe_extract_tar("d.tar.gz", str(tmp_path))
        assert count == 2
        assert (tmp_path / "ok.txt").exists()
        assert not (tmp_path.parent / "evil.txt").exists()

    def test_absolute_path_member_skipped(self, tmp_path):
        """绝对路径成员被跳过"""
        abs_name = os.path.abspath(os.path.join(str(tmp_path), "abs.txt"))
        fake = _FakeTarFile([_FakeTarInfo(abs_name)])
        with patch("agent.compression_tools.tarfile.open", return_value=fake):
            count, size = ct._safe_extract_tar("d.tar.gz", str(tmp_path))
        assert size == 0

    def test_realpath_escape_skipped(self, tmp_path):
        """解析后逃逸出输出目录的成员被二次防护拦截"""
        fake = _FakeTarFile([_FakeTarInfo("ok.txt")])
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        with patch("agent.compression_tools.tarfile.open", return_value=fake), \
             patch("agent.compression_tools.os.path.realpath",
                   side_effect=["C:/outside/evil.txt", str(out_dir)]):
            count, size = ct._safe_extract_tar("d.tar.gz", str(out_dir))
        assert size == 0  # 逃逸成员被跳过

    def test_large_member_uses_chunked_extract(self, tmp_path):
        """超过 100MB 的成员走 extractfile 分块解压"""
        fake = _FakeTarFile([_FakeTarInfo("big.bin", size=101 * 1024 * 1024)])
        with patch("agent.compression_tools.tarfile.open", return_value=fake):
            count, size = ct._safe_extract_tar("d.tar.gz", str(tmp_path))
        assert count == 1
        assert size == 101 * 1024 * 1024  # 按 member.size 累加
        assert (tmp_path / "big.bin").exists()

    def test_callback_exception_swallowed(self, tmp_path):
        """进度回调抛异常不影响解压"""
        def bad_cb(*args):
            raise RuntimeError("boom")

        fake = _FakeTarFile([_FakeTarInfo("ok.txt")])
        with patch("agent.compression_tools.tarfile.open", return_value=fake):
            count, size = ct._safe_extract_tar("d.tar.gz", str(tmp_path), bad_cb)
        assert count == 1
        assert (tmp_path / "ok.txt").exists()
