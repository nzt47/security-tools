"""技能索引缓存单元测试 — 预解析 front matter，启动时加载

覆盖维度（对应任务测试要求）：
1. test_cache_load_on_startup          — 启动加载成功
2. test_cache_hit_returns_metadata     — 缓存命中
3. test_cache_invalidate_on_mtime_change — mtime 变化时失效
4. test_cache_invalidate_on_hash_change  — hash 变化时失效（即使 mtime 未变）
5. test_cache_rebuild_on_corruption    — 缓存损坏时全量重建
6. test_cache_persist_and_reload       — 持久化后重启可加载
补充：
- test_file_store_write_delete_invalidates_cache — create/update_meta/delete 后同步失效
- test_match_latency_halved_with_cache — 验收：第二次 match 延迟较第一次降低 ≥ 50%

【不易】SkillFileStore 接口不变；索引失效必须回源；仅缓存 front matter（不缓存 body）
"""
import json
import os
import shutil
import time
from pathlib import Path

import pytest

from agent.skills_mgmt.file_store import SkillFileStore
from agent.skills_mgmt.index_cache import SkillIndexCache
from agent.skills_mgmt.loader import SkillLoader


def _write_skill(repo: Path, skill_id: str, name: str, body: str = "正文") -> None:
    """写入标准 skill.md（front matter + body），body 用于验证「不缓存 body」"""
    d = repo / skill_id
    d.mkdir(parents=True, exist_ok=True)
    content = (
        "---\n"
        f"id: {skill_id}\n"
        f"name: {name}\n"
        f"description: 测试技能 {name}\n"
        "category: test\n"
        "tags:\n"
        "  - test\n"
        "version: 0.1.0\n"
        "---\n"
        f"{body}\n"
    )
    (d / "skill.md").write_text(content, encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════
#  1. 启动加载成功
# ═══════════════════════════════════════════════════════════════════

class TestLoadOnStartup:
    def test_cache_load_on_startup(self, tmp_path):
        """先全量解析并持久化，模拟重启后 load_on_startup 直接加载成功"""
        repo = tmp_path / "repo"
        _write_skill(repo, "alpha", "alpha")
        _write_skill(repo, "beta", "beta")

        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)
        cache.rebuild()  # 生成持久化缓存文件

        # 模拟重启：新缓存实例走 load_on_startup
        fs2 = SkillFileStore(repo_path=str(repo))
        cache2 = SkillIndexCache(fs2)
        cache2.load_on_startup()

        assert set(cache2._cache) == {"alpha", "beta"}
        meta = cache2.get_metadata("alpha")
        assert meta is not None
        assert meta["name"] == "alpha"

    def test_load_on_startup_missing_cache_is_lazy(self, tmp_path):
        """缓存文件缺失（首次部署）→ 启动零阻塞，首个访问触发全量解析"""
        repo = tmp_path / "repo"
        _write_skill(repo, "alpha", "alpha")

        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)
        cache.load_on_startup()  # 不应抛异常
        assert cache._cache == {}  # 尚未加载（懒加载）
        index = cache.get_all_metadata()  # 首个访问触发解析
        assert "alpha" in index


# ═══════════════════════════════════════════════════════════════════
#  2. 缓存命中
# ═══════════════════════════════════════════════════════════════════

class TestCacheHit:
    def test_cache_hit_returns_metadata(self, tmp_path):
        """首次访问回源解析，后续命中缓存（同一 dict 对象，未重复解析）"""
        repo = tmp_path / "repo"
        _write_skill(repo, "alpha", "alpha")

        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)
        index = cache.get_all_metadata()
        assert index["alpha"]["name"] == "alpha"

        # 第二次命中缓存：返回同一 dict 对象（未回源重解析）
        m2 = cache.get_metadata("alpha")
        assert m2 is index["alpha"]
        assert m2["name"] == "alpha"

    def test_get_all_metadata_returns_same_object_when_unchanged(self, tmp_path):
        """未变化时 get_all_metadata 返回同一 dict 对象（守 loader 倒排索引 id() 契约）"""
        repo = tmp_path / "repo"
        _write_skill(repo, "alpha", "alpha")

        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)
        first = cache.get_all_metadata()
        second = cache.get_all_metadata()
        assert second is first  # id() 不变 → loader 倒排索引不重建


# ═══════════════════════════════════════════════════════════════════
#  3. mtime 变化 → 失效回源
# ═══════════════════════════════════════════════════════════════════

class TestMtimeInvalidation:
    def test_cache_invalidate_on_mtime_change(self, tmp_path):
        """修改 skill.md（mtime 变化）→ 缓存失效，get_metadata 回源返回新元数据"""
        repo = tmp_path / "repo"
        _write_skill(repo, "alpha", "alpha")

        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)
        cache.get_all_metadata()
        assert cache.get_metadata("alpha")["name"] == "alpha"

        _write_skill(repo, "alpha", "alpha-v2")  # 写回 → mtime 变化
        meta = cache.get_metadata("alpha")
        assert meta["name"] == "alpha-v2"  # 回源重解析

    def test_missing_skill_returns_none_and_cleans_cache(self, tmp_path):
        """技能被删除 → get_metadata 返回 None 且清理缓存条目"""
        repo = tmp_path / "repo"
        _write_skill(repo, "alpha", "alpha")

        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)
        cache.get_all_metadata()
        assert "alpha" in cache._cache

        shutil.rmtree(repo / "alpha")  # 模拟外部删除（目录含 skill.md）
        assert cache.get_metadata("alpha") is None
        assert "alpha" not in cache._cache


# ═══════════════════════════════════════════════════════════════════
#  4. hash 变化 → 失效回源（即使 mtime 未变，防文件被覆盖回去）
# ═══════════════════════════════════════════════════════════════════

class TestHashInvalidation:
    def test_cache_invalidate_on_hash_change(self, tmp_path):
        """内容变化但 mtime 保持不变 → hash 校验触发失效回源"""
        repo = tmp_path / "repo"
        _write_skill(repo, "alpha", "alpha")

        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)
        cache.get_all_metadata()

        md_path = repo / "alpha" / "skill.md"
        orig_mtime = md_path.stat().st_mtime
        _write_skill(repo, "alpha", "alpha-v2")  # 内容变化
        os.utime(md_path, (orig_mtime, orig_mtime))  # 恢复原 mtime
        assert md_path.stat().st_mtime == orig_mtime  # 前置：mtime 确实未变

        meta = cache.get_metadata("alpha")
        assert meta["name"] == "alpha-v2"  # hash 变化 → 失效回源


# ═══════════════════════════════════════════════════════════════════
#  5. 缓存损坏 → 全量重建
# ═══════════════════════════════════════════════════════════════════

class TestCorruptionRecovery:
    def test_cache_rebuild_on_corruption(self, tmp_path):
        """缓存文件损坏 → load_on_startup 全量重建并修复缓存文件"""
        repo = tmp_path / "repo"
        _write_skill(repo, "alpha", "alpha")

        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)
        cache.rebuild()
        cache_file = repo / ".index" / "cache.json"
        assert cache_file.exists()

        # 篡改缓存文件为非法 JSON
        cache_file.write_text("{corrupt!!", encoding="utf-8")

        fs2 = SkillFileStore(repo_path=str(repo))
        cache2 = SkillIndexCache(fs2)
        cache2.load_on_startup()  # 应降级全量重建，不抛异常
        assert "alpha" in cache2.get_all_metadata()
        # 重建后缓存文件被修复
        raw = json.loads(cache_file.read_text(encoding="utf-8"))
        assert raw["cache_version"] == "1.0"

    def test_cache_rebuild_on_version_mismatch(self, tmp_path):
        """cache_version 不匹配 → 全量重建"""
        repo = tmp_path / "repo"
        _write_skill(repo, "alpha", "alpha")

        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)
        cache.rebuild()
        cache_file = repo / ".index" / "cache.json"
        raw = json.loads(cache_file.read_text(encoding="utf-8"))
        raw["cache_version"] = "0.9"  # 版本回退
        cache_file.write_text(json.dumps(raw), encoding="utf-8")

        fs2 = SkillFileStore(repo_path=str(repo))
        cache2 = SkillIndexCache(fs2)
        cache2.load_on_startup()
        assert "alpha" in cache2.get_all_metadata()


# ═══════════════════════════════════════════════════════════════════
#  6. 持久化后重启可加载
# ═══════════════════════════════════════════════════════════════════

class TestPersistReload:
    def test_cache_persist_and_reload(self, tmp_path):
        """rebuild 后持久化；重启 load_on_startup 可直接加载缓存"""
        repo = tmp_path / "repo"
        _write_skill(repo, "alpha", "alpha")
        _write_skill(repo, "beta", "beta")

        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)
        cache.rebuild()

        cache_file = repo / ".index" / "cache.json"
        assert cache_file.exists()
        raw = json.loads(cache_file.read_text(encoding="utf-8"))
        assert raw["cache_version"] == SkillIndexCache.CACHE_VERSION
        assert set(raw["skills"]) == {"alpha", "beta"}
        # 每个条目都有 mtime + hash（供失效校验）
        for info in raw["meta"].values():
            assert "mtime" in info and "hash" in info

        # 重启：新缓存实例加载
        fs2 = SkillFileStore(repo_path=str(repo))
        cache2 = SkillIndexCache(fs2)
        cache2.load_on_startup()
        assert cache2.get_metadata("alpha")["name"] == "alpha"
        assert set(cache2.get_all_metadata()) == {"alpha", "beta"}

    def test_cache_does_not_persist_body(self, tmp_path):
        """仅缓存 front matter 元数据，不缓存 skill.md body（守【不易】）"""
        repo = tmp_path / "repo"
        _write_skill(repo, "alpha", "alpha", body="机密正文，不应出现在缓存中")

        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)
        cache.rebuild()
        cache_file = repo / ".index" / "cache.json"
        raw_text = cache_file.read_text(encoding="utf-8")
        assert "机密正文" not in raw_text  # body 不入缓存
        assert "机密正文" not in json.dumps(cache._cache, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════
#  补充：SkillFileStore 写/删操作后同步失效（集成）
# ═══════════════════════════════════════════════════════════════════

class TestFileStoreIntegration:
    def test_file_store_write_delete_invalidates_cache(self, tmp_path):
        """create/update_meta/delete 后缓存同步失效，下次访问回源"""
        repo = tmp_path / "repo"
        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)  # 构造时挂载到 fs

        # create → 缓存含新技能
        fs.create("alpha", meta={"name": "alpha"})
        assert cache.get_metadata("alpha")["name"] == "alpha"

        # update_meta → 失效回源，得到新值
        fs.update_meta("alpha", {"name": "alpha-updated"})
        assert cache.get_metadata("alpha")["name"] == "alpha-updated"

        # delete → 技能不存在，get_metadata 返回 None
        fs.delete("alpha")
        assert cache.get_metadata("alpha") is None


# ═══════════════════════════════════════════════════════════════════
#  验收：启动后第二次 match 延迟较第一次降低 ≥ 50%
# ═══════════════════════════════════════════════════════════════════

class TestMatchLatencyAcceptance:
    def test_match_latency_halved_with_cache(self, tmp_path):
        """验收标准：第二次调用 match 延迟较第一次降低 ≥ 50%

        第一次：懒加载全量解析 + 倒排索引构建（含 persist I/O）
        第二次：mtime/hash 增量校验通过 → 纯内存命中 + 倒排索引复用
        测量取 min（3 次采样）抗抖动。
        """
        repo = tmp_path / "repo"
        for i in range(8):
            _write_skill(repo, f"skill-{i}", f"skill-{i}", f"body {i}")

        fs = SkillFileStore(repo_path=str(repo))
        cache = SkillIndexCache(fs)  # 冷缓存，首次 match 触发懒加载
        loader = SkillLoader(fs)

        t0 = time.perf_counter()
        loader.match("技能检索测试", top_k=3)
        t_first = (time.perf_counter() - t0) * 1000

        samples = []
        for _ in range(3):
            t0 = time.perf_counter()
            loader.match("技能检索测试", top_k=3)
            samples.append((time.perf_counter() - t0) * 1000)
        t_second = min(samples)

        assert t_second * 2 < t_first, (
            f"第二次 match 延迟未降低 50%: first={t_first:.2f}ms "
            f"second={t_second:.2f}ms"
        )
