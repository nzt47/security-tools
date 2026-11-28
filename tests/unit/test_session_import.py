"""E4-5 会话导入：纯解析层（白名单格式 / 上限 / 指纹 / 预览）。"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from agent import session_import as si


def _bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _deepseek(*convs) -> bytes:
    return _bytes(list(convs))


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload if isinstance(payload, bytes)
                            else str(payload).encode("utf-8"))
    return buf.getvalue()


class TestFormats:
    def test_deepseek_数组(self):
        doc = si.parse_upload(_deepseek({
            "id": "c1", "title": "编排讨论", "inserted_at": "2026-11-20T10:00:00Z",
            "messages": [
                {"role": "user", "content": "你好", "created_at": "2026-11-20T10:00:00Z"},
                {"role": "assistant", "content": "在", "created_at": "2026-11-20T10:00:05Z"},
            ],
        }), "export.json")
        assert doc["format"] == "deepseek.export.v1"
        assert doc["stats"]["files"] == 1 and doc["stats"]["messages"] == 2
        conv = doc["sessions"][0]
        assert conv["title"] == "编排讨论"
        assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
        assert conv["messages"][1]["timestamp"] == "2026-11-20T10:00:05Z"

    def test_yunshu_v1_单会话(self):
        doc = si.parse_upload(_bytes({
            "format": "yunshu.session.v1",
            "session": {"title": "往返", "created_at": "2026-11-27"},
            "messages": [{"role": "user", "content": "q"},
                         {"role": "assistant", "content": "a"}],
        }), "s.json")
        assert doc["format"] == "yunshu.session.v1"
        assert doc["sessions"][0]["created_at"] == "2026-11-27"
        assert len(doc["sessions"][0]["messages"]) == 2

    @pytest.mark.parametrize("key", ["conversations", "chats", "data", "items"])
    def test_容器键都接受(self, key):
        doc = si.parse_upload(_bytes({key: [{
            "title": "t", "messages": [{"role": "user", "content": "x"}]}]}), "c.json")
        assert doc["format"] == "deepseek.export.v1"
        assert doc["sessions"][0]["title"] == "t"

    def test_zip_内两种格式混装(self):
        payload = _zip({
            "a.json": _bytes({"format": "yunshu.session.v1",
                              "session": {"title": "云枢"},
                              "messages": [{"role": "user", "content": "1"}]}),
            "b.json": _bytes([{"title": "深度", "messages": [{"role": "user", "content": "2"}]}]),
            "readme.md": "忽略非 json",
        })
        doc = si.parse_upload(payload, "pack.zip")
        assert doc["format"] == "mixed" and len(doc["sessions"]) == 2
        assert doc["stats"]["files"] == 2


class TestRolesAndAliases:
    def test_角色别名与白名单(self):
        doc = si.parse_upload(_deepseek({
            "title": "别名",
            "messages": [
                {"message_role": "human", "text": "问"},
                {"message_role": "ai", "text": "答"},
                {"role": "system", "content": "提示词"},
                {"role": "tool", "content": "原始输出"},
                {"role": "user", "content": "   "},
            ],
        }), "a.json")
        conv = doc["sessions"][0]
        assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
        assert doc["stats"]["dropped_roles"] == 2
        assert doc["stats"]["dropped_empty"] == 1

    def test_正文是列表或字典(self):
        doc = si.parse_upload(_deepseek({
            "title": "多模态",
            "messages": [
                {"role": "user", "content": ["第一段", {"x": 1}, "第二段"]},
                {"role": "assistant", "content": {"text": "结构化正文"}},
            ],
        }), "a.json")
        bodies = [m["content"] for m in doc["sessions"][0]["messages"]]
        assert bodies == ["第一段\n第二段", "结构化正文"]

    def test_只有系统消息等于没会话(self):
        with pytest.raises(si.ImportFormatError) as exc:
            si.parse_upload(_deepseek({"title": "空", "messages": [
                {"role": "system", "content": "只有提示词"}]}), "a.json")
        assert exc.value.code == "no_sessions"


class TestTimestamps:
    @pytest.mark.parametrize("value,expected", [
        (1763623200, "2025-11-20T07:20:00+00:00"),
        (1763623200000, "2025-11-20T07:20:00+00:00"),
        ("1763623200", "2025-11-20T07:20:00+00:00"),
        ("2026-08-01", "2026-08-01"),
        ("2026-08-01 09:00:00", "2026-08-01 09:00:00"),
        ("不是时间", ""),
        ("", ""),
        (None, ""),
    ])
    def test_归一(self, value, expected):
        assert si._iso(value) == expected

    def test_消息时间戳透传_缺失为空(self):
        doc = si.parse_upload(_deepseek({"messages": [
            {"role": "user", "content": "a", "timestamp": "2026-08-01T00:00:00Z"},
            {"role": "assistant", "content": "b"},
        ]}), "a.json")
        assert [m["timestamp"] for m in doc["sessions"][0]["messages"]] == [
            "2026-08-01T00:00:00Z", ""]


class TestLimits:
    def test_正文超长截断并计数(self, monkeypatch):
        monkeypatch.setattr(si, "MAX_CONTENT_CHARS", 4)
        doc = si.parse_upload(_deepseek({"messages": [
            {"role": "user", "content": "0123456789"}]}), "a.json")
        assert doc["sessions"][0]["messages"][0]["content"] == "0123"
        assert doc["stats"]["truncated_messages"] == 1

    def test_单会话条数上限(self, monkeypatch):
        monkeypatch.setattr(si, "MAX_MESSAGES_PER_SESSION", 2)
        doc = si.parse_upload(_deepseek({"messages": [
            {"role": "user", "content": str(i)} for i in range(5)]}), "a.json")
        assert len(doc["sessions"][0]["messages"]) == 2
        assert doc["stats"]["capped_sessions"] == 1

    def test_会话数上限(self, monkeypatch):
        monkeypatch.setattr(si, "MAX_SESSIONS_PER_IMPORT", 1)
        doc = si.parse_upload(_deepseek(
            {"title": "a", "messages": [{"role": "user", "content": "1"}]},
            {"title": "b", "messages": [{"role": "user", "content": "2"}]},
        ), "a.json")
        assert len(doc["sessions"]) == 1
        assert doc["stats"]["truncated_sessions"] == 1

    def test_文件内重复指纹只留一份(self):
        same = {"title": "重复", "messages": [{"role": "user", "content": "同"}]}
        doc = si.parse_upload(_deepseek(dict(same), dict(same)), "a.json")
        assert len(doc["sessions"]) == 1
        assert doc["stats"]["duplicates_in_file"] == 1

    def test_标题默认与截断(self, monkeypatch):
        monkeypatch.setattr(si, "MAX_TITLE_CHARS", 3)
        doc = si.parse_upload(_deepseek({"title": "abcdef", "messages": [
            {"role": "user", "content": "x"}]}), "a.json")
        assert doc["sessions"][0]["title"] == "abc"
        doc2 = si.parse_upload(_deepseek({"messages": [{"role": "user", "content": "y"}]}), "a.json")
        assert doc2["sessions"][0]["title"] == "导入会话"


class TestFingerprintPreview:
    def test_指纹只看角色与正文(self):
        a = {"title": "甲", "messages": [{"role": "user", "content": "x"}]}
        b = {"title": "乙", "messages": [{"role": "user", "content": "x"}]}
        c = {"title": "甲", "messages": [{"role": "user", "content": "y"}]}
        assert si.fingerprint(a) == si.fingerprint(b)
        assert si.fingerprint(a) != si.fingerprint(c)
        assert len(si.fingerprint(a)) == 16

    def test_预览不回正文(self):
        doc = si.parse_upload(_deepseek({
            "title": "敏感正文", "messages": [
                {"role": "user", "content": "秘密内容", "timestamp": "2026-08-01T00:00:00Z"},
                {"role": "assistant", "content": "答复"}]}), "a.json")
        row = si.preview(doc)["sessions"][0]
        assert "秘密内容" not in json.dumps(row, ensure_ascii=False)
        assert row["messages"] == 2 and row["chars"] == 6
        assert row["first_ts"] == "2026-08-01T00:00:00Z" and row["duplicate"] is False

    def test_预览标记重复(self):
        doc = si.parse_upload(_deepseek({
            "messages": [{"role": "user", "content": "x"}]}), "a.json")
        known = {si.fingerprint(doc["sessions"][0])}
        body = si.preview(doc, known=known)
        assert body["duplicates"] == 1 and body["sessions"][0]["duplicate"] is True


class TestErrors:
    def test_空文件(self):
        with pytest.raises(si.ImportFormatError) as exc:
            si.parse_upload(b"", "a.json")
        assert exc.value.code == "empty_file"

    def test_后缀白名单(self):
        with pytest.raises(si.ImportFormatError) as exc:
            si.parse_upload(_bytes([]), "a.txt")
        assert exc.value.code == "unsupported_format"

    def test_坏_json(self):
        with pytest.raises(si.ImportFormatError) as exc:
            si.parse_upload(b"{not json", "a.json")
        assert exc.value.code == "bad_json"

    def test_无法识别的形状(self):
        with pytest.raises(si.ImportFormatError) as exc:
            si.parse_upload(_bytes({"foo": "bar"}), "a.json")
        assert exc.value.code == "unsupported_format"
        assert "yunshu.session.v1" in exc.value.detail

    def test_超过体积上限(self, monkeypatch):
        monkeypatch.setattr(si, "MAX_UPLOAD_BYTES", 4)
        with pytest.raises(si.ImportFormatError) as exc:
            si.parse_upload(b"12345", "a.json")
        assert exc.value.code == "too_large"

    def test_坏_zip(self):
        with pytest.raises(si.ImportFormatError) as exc:
            si.parse_upload(b"PK-not-really-a-zip", "a.zip")
        assert exc.value.code == "bad_zip"

    def test_zip_内没有_json(self):
        with pytest.raises(si.ImportFormatError) as exc:
            si.parse_upload(_zip({"a.md": "x"}), "a.zip")
        assert exc.value.code == "no_sessions"

    def test_zip_条目过多(self, monkeypatch):
        monkeypatch.setattr(si, "MAX_ZIP_ENTRIES", 1)
        payload = _zip({"a.json": _bytes([]), "b.json": _bytes([])})
        with pytest.raises(si.ImportFormatError) as exc:
            si.parse_upload(payload, "a.zip")
        assert exc.value.code == "too_many_files"

    def test_zip_内_json损坏(self):
        with pytest.raises(si.ImportFormatError) as exc:
            si.parse_upload(_zip({"a.json": "{"}), "a.zip")
        assert exc.value.code == "bad_json"
