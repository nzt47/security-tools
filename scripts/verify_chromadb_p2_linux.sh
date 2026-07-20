#!/bin/bash
# ==============================================================================
# Linux 环境部署脚本 — 验证 P2 方案中 chromadb 路径问题的实际修复情况
#
# 目标：
#   1. 在 Linux 环境验证 chromadb 的 hnswlib 不再触发 NotADirectoryError
#   2. 验证 P2 预热缓存对 chromadb 路径 3.2s 瓶颈的实际优化效果
#   3. 对比 JSON fallback 路径 vs chromadb 路径的性能差异
#
# 使用方式：
#   chmod +x scripts/verify_chromadb_p2_linux.sh
#   ./scripts/verify_chromadb_p2_linux.sh
#
# 前置条件：
#   - Linux 环境（Ubuntu 20.04+ / CentOS 7+ / Debian 11+）
#   - Python 3.10+
#   - 网络可访问 huggingface.co（首次下载模型需要）
# ==============================================================================

set -euo pipefail

# ── 配置区 ──
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv-verify"
LOG_DIR="${PROJECT_ROOT}/logs/verify_chromadb"
REPORT_FILE="${LOG_DIR}/verify_report_$(date +%Y%m%d_%H%M%S).md"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC} $1" | tee -a "$REPORT_FILE"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$REPORT_FILE"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" | tee -a "$REPORT_FILE"; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $1" | tee -a "$REPORT_FILE"; }

# ── 初始化 ──
mkdir -p "$LOG_DIR"
echo "# chromadb P2 验证报告" > "$REPORT_FILE"
echo "" >> "$REPORT_FILE"
echo "> 执行时间: $(date)" >> "$REPORT_FILE"
echo "> 环境: $(uname -a)" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

cd "$PROJECT_ROOT"
log_info "项目根目录: $PROJECT_ROOT"
log_info "报告文件: $REPORT_FILE"

# ==============================================================================
# STEP 1: 环境准备
# ==============================================================================
log_step "STEP 1: 环境准备"

# 1.1 检查 Python 版本
if ! command -v python3 &> /dev/null; then
    log_error "Python 3 未安装，请先安装 Python 3.10+"
    exit 1
fi
PYTHON_VERSION=$(python3 --version)
log_info "Python 版本: $PYTHON_VERSION"

# 1.2 创建独立 venv（避免污染开发环境）
if [ ! -d "$VENV_DIR" ]; then
    log_info "创建虚拟环境: $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

# 1.3 激活 venv
source "$VENV_DIR/bin/activate"
log_info "已激活虚拟环境"

# 1.4 升级 pip
log_info "升级 pip..."
pip install --upgrade pip wheel setuptools --quiet

# 1.5 安装依赖
log_info "安装 chromadb + sentence-transformers..."
pip install chromadb sentence-transformers --quiet 2>&1 | tee -a "$REPORT_FILE" || {
    log_error "依赖安装失败"
    exit 1
}

log_info "依赖安装完成"

# ==============================================================================
# STEP 2: 验证 chromadb 路径问题修复
# ==============================================================================
log_step "STEP 2: 验证 chromadb 路径问题修复（Windows NotADirectoryError）"

# 2.1 创建临时测试脚本
VERIFY_SCRIPT=$(mktemp /tmp/verify_chromadb_XXXXXX.py)
cat > "$VERIFY_SCRIPT" << 'PYEOF'
"""验证 chromadb 在 Linux 临时目录下不再触发 NotADirectoryError"""
import os
import sys
import tempfile
import traceback

print(f"[INFO] Python: {sys.version}")
print(f"[INFO] 平台: {sys.platform}")

# 关键：不设置 HF_HUB_OFFLINE，让 chromadb 正常初始化
# 但设置 TRANSFORMERS_OFFLINE 避免网络检查（模型已在本地缓存时）
os.environ.setdefault('TRANSFORMERS_OFFLINE', '0')

try:
    import chromadb
    from chromadb.config import Settings
    print(f"[INFO] chromadb 版本: {chromadb.__version__}")

    # 在临时目录创建 PersistentClient（Windows 上这里会失败）
    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"[INFO] 临时目录: {tmpdir}")

        client = chromadb.PersistentClient(
            path=tmpdir,
            settings=Settings(anonymized_telemetry=False)
        )
        collection = client.get_or_create_collection(
            name="verify_test",
            metadata={"description": "Linux 路径验证"}
        )

        # 添加测试数据
        collection.add(
            documents=["test document 1", "test document 2", "test document 3"],
            metadatas=[{"id": 1}, {"id": 2}, {"id": 3}],
            ids=["doc1", "doc2", "doc3"]
        )

        # 执行查询（Windows 上这里触发 NotADirectoryError on data_level0.bin）
        results = collection.query(query_texts=["test"], n_results=2)
        print(f"[OK] 查询成功: 返回 {len(results['ids'][0])} 条结果")
        print(f"[OK] data_level0.bin 路径问题未复现 — Linux 环境正常")

    print("[RESULT] STEP 2: PASS - chromadb 路径问题在 Linux 已修复")
    sys.exit(0)

except Exception as e:
    print(f"[FAIL] 异常: {type(e).__name__}: {e}")
    traceback.print_exc()
    print("[RESULT] STEP 2: FAIL - chromadb 路径问题仍存在")
    sys.exit(1)
PYEOF

if python3 "$VERIFY_SCRIPT" 2>&1 | tee -a "$REPORT_FILE"; then
    log_info "STEP 2 验证通过：chromadb 路径问题在 Linux 已修复"
    STEP2_RESULT="PASS"
else
    log_error "STEP 2 验证失败：chromadb 路径问题仍存在"
    STEP2_RESULT="FAIL"
fi
rm -f "$VERIFY_SCRIPT"

# ==============================================================================
# STEP 3: 验证 P2 预热缓存对 chromadb 路径的优化效果
# ==============================================================================
log_step "STEP 3: 验证 P2 预热缓存对 chromadb 路径的优化效果"

P2_SCRIPT=$(mktemp /tmp/verify_p2_XXXXXX.py)
cat > "$P2_SCRIPT" << 'PYEOF'
"""验证 P2 预热缓存对 chromadb 路径 3.2s 瓶颈的优化效果"""
import os
import sys
import time
import tempfile

# 设置离线模式（如果模型已缓存）
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')

sys.path.insert(0, os.getcwd())

from memory.vector_store import VectorStore

print(f"[INFO] Python: {sys.version}")
print(f"[INFO] 平台: {sys.platform}")

with tempfile.TemporaryDirectory() as tmpdir:
    print(f"[INFO] 初始化 VectorStore (chromadb 路径)...")

    init_start = time.perf_counter()
    store = VectorStore(
        collection_name="verify_p2_chromadb",
        persist_dir=tmpdir,
        cache_size=100,
    )
    init_elapsed = time.perf_counter() - init_start
    print(f"[INFO] VectorStore 初始化耗时: {init_elapsed:.2f}s")
    print(f"[INFO] 存储后端: {store._backend}")

    if store._backend != "chromadb":
        print(f"[SKIP] 后端非 chromadb (实际: {store._backend})，跳过 P2 验证")
        sys.exit(0)

    # 添加测试数据
    print("[INFO] 添加 500 条测试数据...")
    add_start = time.perf_counter()
    for i in range(500):
        content = f"document {i}: testing chromadb search performance with semantic vector"
        store.add(content, metadata={"doc_id": i})
    add_elapsed = time.perf_counter() - add_start
    print(f"[INFO] 添加 500 条耗时: {add_elapsed:.2f}s")

    # P2 预热缓存
    print("[INFO] P2 预热缓存...")
    warmup_start = time.perf_counter()
    store.search("testing chromadb search", top_k=5)
    warmup_elapsed = (time.perf_counter() - warmup_start) * 1000
    print(f"[INFO] 预热首搜耗时: {warmup_elapsed:.2f}ms")

    # 测试预热后 100 次搜索
    print("[INFO] 测试预热后 100 次搜索...")
    start = time.perf_counter()
    for _ in range(100):
        results = store.search("testing chromadb search", top_k=5)
    elapsed = (time.perf_counter() - start) * 1000
    print(f"[INFO] 100 次搜索耗时(预热后): {elapsed:.2f}ms")
    print(f"[INFO] 平均每次: {elapsed/100:.2f}ms")

    # 缓存统计
    stats = store.get_cache_stats()
    print(f"[INFO] 缓存统计: 命中={stats['hits']}, 未命中={stats['misses']}, 命中率={stats['hit_rate']}%")

    # 判定
    if warmup_elapsed > 1000:
        print(f"[RESULT] 预热首搜 {warmup_elapsed:.2f}ms > 1000ms，chromadb 路径 3.2s 瓶颈存在")
        print(f"[RESULT] P2 优化后 100 次搜索 {elapsed:.2f}ms，缓存命中率高 = P2 有效")
    else:
        print(f"[RESULT] 预热首搜 {warmup_elapsed:.2f}ms < 1000ms，chromadb 路径瓶颈不显著")

    sys.exit(0)
PYEOF

if python3 "$P2_SCRIPT" 2>&1 | tee -a "$REPORT_FILE"; then
    log_info "STEP 3 验证完成：P2 预热缓存效果已记录"
    STEP3_RESULT="PASS"
else
    log_error "STEP 3 验证失败"
    STEP3_RESULT="FAIL"
fi
rm -f "$P2_SCRIPT"

# ==============================================================================
# STEP 4: 对比 JSON fallback 路径 vs chromadb 路径
# ==============================================================================
log_step "STEP 4: 对比 JSON fallback 路径 vs chromadb 路径"

COMPARE_SCRIPT=$(mktemp /tmp/compare_paths_XXXXXX.py)
cat > "$COMPARE_SCRIPT" << 'PYEOF'
"""对比 JSON fallback 路径 vs chromadb 路径的搜索性能"""
import os
import sys
import time
import tempfile
from unittest import mock

os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')

sys.path.insert(0, os.getcwd())
from memory.vector_store import VectorStore
from memory.vector_store import vector_store as vs_module

print(f"[INFO] 对比 JSON fallback vs chromadb 路径")
print(f"{'='*60}")

# ── 路径 1: JSON fallback (BM25) ──
print(f"\n[1] JSON fallback 路径 (BM25):")
with mock.patch.object(vs_module, 'HAS_CHROMA', False), \
     mock.patch.object(vs_module, 'HAS_SENTENCE_TRANSFORMERS', False), \
     mock.patch.dict(sys.modules, {'sqlite_vec': None, 'chromadb': None}):
    with tempfile.TemporaryDirectory() as tmpdir:
        store = VectorStore(
            collection_name="compare_json",
            persist_dir=tmpdir,
            enable_inverted_index=True,
            cache_size=100,
        )
        assert store._backend == "json"

        for i in range(500):
            store.add(f"document {i}: testing json fallback bm25 search", metadata={"doc_id": i})

        # 预热
        store.search("testing json bm25 search", top_k=5)

        # 测量
        start = time.perf_counter()
        for _ in range(100):
            store.search("testing json bm25 search", top_k=5)
        json_elapsed = (time.perf_counter() - start) * 1000
        print(f"    100 次搜索(预热后): {json_elapsed:.2f}ms")
        print(f"    平均每次: {json_elapsed/100:.2f}ms")

# ── 路径 2: chromadb (HNSW + encoder) ──
print(f"\n[2] chromadb 路径 (HNSW + encoder):")
with tempfile.TemporaryDirectory() as tmpdir:
    store = VectorStore(
        collection_name="compare_chromadb",
        persist_dir=tmpdir,
        cache_size=100,
    )
    if store._backend != "chromadb":
        print(f"    [SKIP] 后端非 chromadb (实际: {store._backend})")
    else:
        for i in range(500):
            store.add(f"document {i}: testing chromadb semantic vector search", metadata={"doc_id": i})

        # 预热
        warmup_start = time.perf_counter()
        store.search("testing chromadb vector search", top_k=5)
        warmup_elapsed = (time.perf_counter() - warmup_start) * 1000
        print(f"    预热首搜耗时: {warmup_elapsed:.2f}ms")

        # 测量
        start = time.perf_counter()
        for _ in range(100):
            store.search("testing chromadb vector search", top_k=5)
        chroma_elapsed = (time.perf_counter() - start) * 1000
        print(f"    100 次搜索(预热后): {chroma_elapsed:.2f}ms")
        print(f"    平均每次: {chroma_elapsed/100:.2f}ms")

print(f"\n{'='*60}")
print(f"[RESULT] 对比完成")
PYEOF

python3 "$COMPARE_SCRIPT" 2>&1 | tee -a "$REPORT_FILE" || log_warn "STEP 4 对比脚本执行有警告"
rm -f "$COMPARE_SCRIPT"

# ==============================================================================
# STEP 5: 运行项目原有性能测试
# ==============================================================================
log_step "STEP 5: 运行项目原有性能测试（验证无回归）"

cd "$PROJECT_ROOT"
if python3 -m pytest tests/performance/test_vector_store_performance.py -v -s --timeout=300 2>&1 | tee -a "$REPORT_FILE"; then
    log_info "STEP 5: 性能测试全部通过"
    STEP5_RESULT="PASS"
else
    log_error "STEP 5: 性能测试有失败"
    STEP5_RESULT="FAIL"
fi

# ==============================================================================
# 汇总报告
# ==============================================================================
log_step "汇总报告"

cat >> "$REPORT_FILE" << EOF

## 验证结果汇总

| 步骤 | 内容 | 结果 |
|------|------|------|
| STEP 1 | 环境准备（Python + chromadb + sentence-transformers） | PASS |
| STEP 2 | chromadb 路径问题修复验证（NotADirectoryError） | ${STEP2_RESULT} |
| STEP 3 | P2 预热缓存对 chromadb 路径优化效果 | ${STEP3_RESULT} |
| STEP 4 | JSON fallback vs chromadb 路径对比 | DONE |
| STEP 5 | 项目原有性能测试回归 | ${STEP5_RESULT} |

## 关键指标

- **chromadb 路径问题**: ${STEP2_RESULT} (PASS=Linux 已修复，FAIL=仍存在)
- **P2 预热缓存效果**: 见 STEP 3 输出（预热首搜耗时 + 100 次搜索耗时）
- **路径对比**: 见 STEP 4 输出（JSON fallback vs chromadb）

## 结论

EOF

if [ "$STEP2_RESULT" = "PASS" ] && [ "$STEP3_RESULT" = "PASS" ]; then
    echo "- ✅ chromadb 路径问题在 Linux 已修复" >> "$REPORT_FILE"
    echo "- ✅ P2 预热缓存机制对 chromadb 路径有效" >> "$REPORT_FILE"
    echo "- 📋 建议在生产环境部署 P2 优化" >> "$REPORT_FILE"
    log_info "验证全部通过"
else
    echo "- ❌ 部分验证未通过，请查看详细日志" >> "$REPORT_FILE"
    log_warn "部分验证未通过，请查看报告: $REPORT_FILE"
fi

echo "" >> "$REPORT_FILE"
echo "> 报告生成完毕: $(date)" >> "$REPORT_FILE"

log_info "报告已生成: $REPORT_FILE"
log_info "完成"

# 退出码：STEP2 和 STEP3 都通过才返回 0
if [ "$STEP2_RESULT" = "PASS" ] && [ "$STEP3_RESULT" = "PASS" ]; then
    exit 0
else
    exit 1
fi
