"""压缩工具集成测试 -- 测试 compression_tools.py 的 compress/decompress

覆盖范围：
- 单文件压缩（zip 和 tar.gz）
- 目录压缩
- 解压 zip 和 tar.gz
- 内容完整性（压缩后解压对比）
- 错误处理（不存在的源、不支持的格式、空目录）
- Zip Slip 攻击防护
- 进度回调
"""
import os
import pytest

from agent.compression_tools import compress, decompress


# ════════════════════════════════════════════════════════════════════════════════
#  单文件压缩测试
# ════════════════════════════════════════════════════════════════════════════════

class TestCompressSingleFile:
    """单文件压缩"""

    def test_compress_zip_single_file(self, tmp_path):
        """将单个文件压缩为 zip"""
        src = tmp_path / "hello.txt"
        src.write_text("Hello, World!", encoding="utf-8")

        result = compress(str(src), format="zip")
        assert result["ok"] is True
        assert result["format"] == "zip"
        assert result["file_count"] == 1
        assert os.path.exists(result["output_path"])
        assert result["output_path"].endswith(".zip")
        assert result["compressed_size"] > 0

    def test_compress_tar_gz_single_file(self, tmp_path):
        """将单个文件压缩为 tar.gz"""
        src = tmp_path / "hello.txt"
        src.write_text("Hello, World!", encoding="utf-8")

        result = compress(str(src), format="tar.gz")
        assert result["ok"] is True
        assert result["format"] == "tar.gz"
        assert result["file_count"] == 1
        assert os.path.exists(result["output_path"])
        assert result["compressed_size"] > 0

    def test_compress_tgz_alias(self, tmp_path):
        """tgz 格式别名"""
        src = tmp_path / "data.txt"
        src.write_text("some data", encoding="utf-8")

        result = compress(str(src), format="tgz")
        assert result["ok"] is True
        assert result["format"] == "tar.gz"

    def test_compress_auto_output_path(self, tmp_path):
        """未指定输出路径时自动生成"""
        src = tmp_path / "document.txt"
        src.write_text("content", encoding="utf-8")
        result = compress(str(src), format="zip")
        assert result["ok"] is True
        # 默认输出到同目录
        assert "document" in result["output_path"] or tmp_path.resolve().as_posix() in result["output_path"].replace("\\", "/")

    def test_compress_custom_output_path(self, tmp_path):
        """指定自定义输出路径"""
        src = tmp_path / "file.txt"
        src.write_text("hello", encoding="utf-8")
        out = tmp_path / "custom.zip"

        result = compress(str(src), output_path=str(out), format="zip")
        assert result["ok"] is True
        assert os.path.exists(str(out))

    def test_compress_larger_file(self, tmp_path):
        """压缩较大的文本文件"""
        src = tmp_path / "large.txt"
        content = "A" * 10000  # 10KB 文本
        src.write_text(content, encoding="utf-8")

        result = compress(str(src), format="zip")
        assert result["ok"] is True
        assert result["compressed_size"] > 0
        # 压缩后应比原文件小（或至少不会太大）
        original_size = os.path.getsize(str(src))
        assert result["compressed_size"] < original_size * 2


# ════════════════════════════════════════════════════════════════════════════════
#  目录压缩测试
# ════════════════════════════════════════════════════════════════════════════════

class TestCompressDirectory:
    """目录压缩"""

    def test_compress_directory_zip(self, tmp_path):
        """压缩包含多个文件的目录"""
        src_dir = tmp_path / "mydir"
        src_dir.mkdir()
        (src_dir / "a.txt").write_text("file A", encoding="utf-8")
        (src_dir / "b.txt").write_text("file B", encoding="utf-8")
        sub = src_dir / "sub"
        sub.mkdir()
        (sub / "c.txt").write_text("file C", encoding="utf-8")

        result = compress(str(src_dir), format="zip")
        assert result["ok"] is True
        assert result["file_count"] == 3
        assert os.path.exists(result["output_path"])

    def test_compress_directory_tar_gz(self, tmp_path):
        """压缩目录为 tar.gz"""
        src_dir = tmp_path / "project"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("print('hello')", encoding="utf-8")
        (src_dir / "README.txt").write_text("readme", encoding="utf-8")

        result = compress(str(src_dir), format="tar.gz")
        assert result["ok"] is True
        assert result["file_count"] == 2


# ════════════════════════════════════════════════════════════════════════════════
#  解压测试
# ════════════════════════════════════════════════════════════════════════════════

class TestDecompress:
    """解压功能"""

    def test_decompress_zip(self, tmp_path):
        """解压 zip 文件"""
        # 先创建并压缩
        src = tmp_path / "original.txt"
        src.write_text("Hello Decompress!", encoding="utf-8")
        comp_result = compress(str(src), format="zip")
        assert comp_result["ok"] is True

        # 解压到新目录
        out_dir = tmp_path / "extracted"
        result = decompress(comp_result["output_path"], output_dir=str(out_dir))
        assert result["ok"] is True
        assert result["format"] == "zip"
        assert result["file_count"] >= 1
        assert os.path.exists(out_dir)

    def test_decompress_tar_gz(self, tmp_path):
        """解压 tar.gz 文件"""
        src = tmp_path / "data.txt"
        src.write_text("tar.gz test data", encoding="utf-8")
        comp_result = compress(str(src), format="tar.gz")
        assert comp_result["ok"] is True

        out_dir = tmp_path / "tar_out"
        result = decompress(comp_result["output_path"], output_dir=str(out_dir))
        assert result["ok"] is True
        assert result["format"] == "tar.gz"

    def test_decompress_auto_output_dir(self, tmp_path):
        """未指定输出目录时自动创建（解压到压缩文件所在目录下的子目录）"""
        src = tmp_path / "auto_test.txt"
        src.write_text("auto test", encoding="utf-8")
        comp_result = compress(str(src), format="zip")
        assert comp_result["ok"] is True

        # 将 zip 移到新目录再解压，避免输出目录名与已有文件冲突
        import shutil
        new_dir = tmp_path / "extract_here"
        new_dir.mkdir()
        zip_in_new = new_dir / "auto_test.txt.zip"
        shutil.copy(comp_result["output_path"], str(zip_in_new))

        result = decompress(str(zip_in_new))
        assert result["ok"] is True
        assert os.path.exists(result["output_dir"])

    def test_decompress_directory_zip(self, tmp_path):
        """解压包含目录结构的 zip"""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "file1.txt").write_text("f1", encoding="utf-8")
        sub = src_dir / "nested"
        sub.mkdir()
        (sub / "file2.txt").write_text("f2", encoding="utf-8")

        comp_result = compress(str(src_dir), format="zip")
        assert comp_result["ok"] is True

        out_dir = tmp_path / "out"
        result = decompress(comp_result["output_path"], output_dir=str(out_dir))
        assert result["ok"] is True
        assert result["file_count"] == 2


# ════════════════════════════════════════════════════════════════════════════════
#  内容完整性测试
# ════════════════════════════════════════════════════════════════════════════════

class TestContentIntegrity:
    """压缩-解压循环后内容完整性"""

    def test_zip_round_trip_text(self, tmp_path):
        """zip 压缩后解压，文本内容一致"""
        src = tmp_path / "poem.txt"
        original = "Roses are red.\nViolets are blue.\n"
        src.write_text(original, encoding="utf-8")

        comp_result = compress(str(src), format="zip")
        assert comp_result["ok"] is True

        out_dir = tmp_path / "verify"
        decompress(comp_result["output_path"], output_dir=str(out_dir))

        # 验证解压出的文件内容
        extracted = out_dir / "poem.txt"
        assert extracted.exists()
        assert extracted.read_text(encoding="utf-8") == original

    def test_tar_gz_round_trip(self, tmp_path):
        """tar.gz 压缩后解压，内容一致"""
        src = tmp_path / "code.py"
        original = "def hello():\n    return 'world'\n"
        src.write_text(original, encoding="utf-8")

        comp_result = compress(str(src), format="tar.gz")
        assert comp_result["ok"] is True

        out_dir = tmp_path / "verify_tar"
        decompress(comp_result["output_path"], output_dir=str(out_dir))

        extracted = out_dir / "code.py"
        assert extracted.exists()
        assert extracted.read_text(encoding="utf-8") == original

    def test_round_trip_multiple_files(self, tmp_path):
        """多文件压缩-解压后内容一致"""
        src_dir = tmp_path / "multi"
        src_dir.mkdir()
        files = {
            "a.txt": "AAA",
            "b.txt": "BBB",
            "sub/c.txt": "CCC",
        }
        for rel_path, content in files.items():
            full = src_dir / rel_path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")

        comp_result = compress(str(src_dir), format="zip")
        out_dir = tmp_path / "verify_multi"
        decompress(comp_result["output_path"], output_dir=str(out_dir))

        # 验证每个文件
        for rel_path, expected in files.items():
            # 解压后路径可能去掉了 src 目录前缀，文件名在 out_dir 下
            found = False
            for root, dirs, filenames in os.walk(str(out_dir)):
                for fname in filenames:
                    fpath = os.path.join(root, fname)
                    content = open(fpath, encoding="utf-8").read()
                    if content == expected:
                        found = True
                        break
                if found:
                    break
            assert found, f"解压后未找到内容为 '{expected}' 的文件"


# ════════════════════════════════════════════════════════════════════════════════
#  错误处理测试
# ════════════════════════════════════════════════════════════════════════════════

class TestCompressionErrorHandling:
    """压缩/解压错误处理"""

    def test_compress_nonexistent_source(self, tmp_path):
        """源路径不存在"""
        result = compress(str(tmp_path / "nonexistent.txt"), format="zip")
        assert result["ok"] is False
        assert "error" in result

    def test_compress_unsupported_format(self, tmp_path):
        """不支持的压缩格式"""
        src = tmp_path / "f.txt"
        src.write_text("hi", encoding="utf-8")
        result = compress(str(src), format="rar")
        assert result["ok"] is False
        assert "error" in result

    def test_compress_invalid_format(self, tmp_path):
        """无效格式名"""
        src = tmp_path / "f.txt"
        src.write_text("hi", encoding="utf-8")
        result = compress(str(src), format="7zip")
        assert result["ok"] is False

    def test_decompress_nonexistent_file(self, tmp_path):
        """解压不存在的文件"""
        result = decompress(str(tmp_path / "ghost.zip"))
        assert result["ok"] is False
        assert "error" in result

    def test_decompress_non_archive(self, tmp_path):
        """解压非压缩文件"""
        src = tmp_path / "not_archive.txt"
        src.write_text("I am just text", encoding="utf-8")
        result = decompress(str(src))
        assert result["ok"] is False
        assert "error" in result

    def test_compress_empty_directory(self, tmp_path):
        """压缩空目录"""
        src_dir = tmp_path / "empty_dir"
        src_dir.mkdir()
        result = compress(str(src_dir), format="zip")
        assert result["ok"] is False  # 没有可压缩的文件
        assert "error" in result


# ════════════════════════════════════════════════════════════════════════════════
#  进度回调测试
# ════════════════════════════════════════════════════════════════════════════════

class TestProgressCallback:
    """进度回调功能"""

    def test_compress_progress_callback(self, tmp_path):
        """压缩时进度回调被调用"""
        src = tmp_path / "progress_test.txt"
        src.write_text("data", encoding="utf-8")

        call_log = []

        def on_progress(current, total, filename):
            call_log.append((current, total, filename))

        result = compress(str(src), format="zip", progress_callback=on_progress)
        assert result["ok"] is True
        assert len(call_log) >= 1
        # 最后一次调用 current == total
        assert call_log[-1][0] == call_log[-1][1]

    def test_decompress_progress_callback(self, tmp_path):
        """解压时进度回调被调用"""
        src = tmp_path / "orig.txt"
        src.write_text("test", encoding="utf-8")
        comp_result = compress(str(src), format="zip")
        out_dir = tmp_path / "cb_out"

        call_log = []

        def on_progress(current, total, filename):
            call_log.append((current, total, filename))

        result = decompress(comp_result["output_path"], output_dir=str(out_dir), progress_callback=on_progress)
        assert result["ok"] is True
        # 至少有一个文件被解压
        assert len(call_log) >= 1

    def test_progress_callback_exception_ignored(self, tmp_path):
        """进度回调抛出异常不影响压缩流程"""
        src = tmp_path / "safe.txt"
        src.write_text("safe data", encoding="utf-8")

        def broken_callback(current, total, filename):
            raise RuntimeError("callback broken")

        result = compress(str(src), format="zip", progress_callback=broken_callback)
        assert result["ok"] is True  # 异常应被忽略


# ════════════════════════════════════════════════════════════════════════════════
#  Zip Slip 防护测试
# ════════════════════════════════════════════════════════════════════════════════

class TestZipSlipPrevention:
    """Zip Slip 攻击防护"""

    def test_normal_extraction_safe(self, tmp_path):
        """正常解压无安全问题"""
        src = tmp_path / "normal.txt"
        src.write_text("safe", encoding="utf-8")
        comp_result = compress(str(src), format="zip")
        out_dir = tmp_path / "safe_out"

        result = decompress(comp_result["output_path"], output_dir=str(out_dir))
        assert result["ok"] is True
        # 所有文件都应在输出目录内
        for root, dirs, files in os.walk(str(out_dir)):
            for f in files:
                full_path = os.path.join(root, f)
                assert full_path.startswith(str(out_dir.resolve()))
