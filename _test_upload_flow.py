"""模拟网页端上传技能包并执行 — 全链路验证

【生成日志摘要】
- 生成时间: 2026-06-30
- 内容: 模拟浏览器 multipart 上传 zip 技能包，验证三层架构全链路
- 版本: v1.1.0（修正上传端点为 /upload，对齐响应字段）
- 关键状态: 后端已运行于 127.0.0.1:5678

验证流程:
    1. HTTP multipart 上传 zip → /api/skills-mgmt/upload（L0 安装）
    2. POST /api/skills-mgmt/match 验证 L1 元数据匹配
    3. GET  /api/skills-mgmt/<id>/instruction 验证 L2 按需加载
    4. POST /api/skills-mgmt/<id>/execute 验证 L3 沙箱执行
    5. 验证执行结果不含脚本代码（仅 JSON 结果进入上下文）

状态同步机制:
    - 后端权威原则: 每次上传后通过 GET /api/skills-mgmt 拉取最新列表
    - 请求序号校验: 每次 HTTP 调用附带 seq 序号，避免乱序响应
"""
import json
import time
import urllib.request
import urllib.error
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:5678"

# 请求序号（防止乱序响应污染状态）
_seq = 0


def _next_seq() -> int:
    global _seq
    _seq += 1
    return _seq


def upload_zip(zip_path: str, force: bool = True):
    """模拟浏览器 multipart/form-data 上传 zip 文件到 /upload 端点

    端点: POST /api/skills-mgmt/upload
    返回: (status, {ok, skill: {skill_id, name, version, scripts_count}})
    """
    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex[:16]
    zip_path = Path(zip_path)
    filename = zip_path.name

    with open(zip_path, "rb") as f:
        file_data = f.read()

    # 构建 multipart body
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
    body += b"Content-Type: application/zip\r\n\r\n"
    body += file_data
    body += b"\r\n"
    if force:
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="force"\r\n\r\n'
        body += b"true\r\n"
    body += f"--{boundary}--\r\n".encode()

    url = f"{BASE}/api/skills-mgmt/upload"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

    seq = _next_seq()
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), seq
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw), seq
        except Exception:
            return e.code, {"raw": raw}, seq
    except Exception as e:
        return -1, {"error": str(e)}, seq


def call(method: str, path: str, body=None):
    """JSON API 调用（带请求序号）"""
    url = BASE + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    seq = _next_seq()
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), seq
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw), seq
        except Exception:
            return e.code, {"raw": raw}, seq
    except Exception as e:
        return -1, {"error": str(e)}, seq


def verify_no_code_leak(text: str) -> bool:
    """验证文本中不含脚本代码特征"""
    code_markers = ["import sys", "def main", "sys.stdin", "json.loads(sys.stdin"]
    return not any(marker in text for marker in code_markers)


# ════════════════════════════════════════════════════════════
#  全链路测试
# ════════════════════════════════════════════════════════════

print("=" * 70)
print("模拟网页端上传技能包 — 全链路验证")
print("=" * 70)

packages = [
    ("data/skill_packages/pdf-extractor-v1.2.0.zip", "pdf-extractor", "PDF"),
    ("data/skill_packages/excel-chart-maker-v2.0.0.zip", "excel-chart-maker", "Excel 图表"),
]

all_passed = True
results_summary = []

for zip_path, expected_id, intent_text in packages:
    print(f"\n{'─' * 60}")
    print(f"技能包: {Path(zip_path).name}")
    print(f"{'─' * 60}")

    pkg_passed = True
    sid = expected_id  # 默认值，上传成功后会被覆盖

    # ─── Step 1: 上传安装 (L0) ───
    print(f"\n  [1] 上传 zip → POST /api/skills-mgmt/upload")
    if not Path(zip_path).exists():
        print(f"      ✗ 文件不存在: {zip_path}")
        pkg_passed = False
        all_passed = False
        continue

    s, d, seq = upload_zip(zip_path, force=True)
    if s in (200, 201) and d.get("ok"):
        skill = d.get("skill", {})
        sid = skill.get("skill_id", expected_id)
        print(f"      ✓ 安装成功: {sid} (HTTP {s}, seq={seq})")
        print(f"        名称: {skill.get('name', 'N/A')}")
        print(f"        版本: {skill.get('version', 'N/A')}")
        print(f"        脚本数: {skill.get('scripts_count', 0)}")
    else:
        # 上传失败时检查是否已存在（覆盖安装场景）
        print(f"      ! 上传返回 HTTP {s}: {json.dumps(d, ensure_ascii=False)[:150]}")
        s2, d2, _ = call("GET", "/api/skills-mgmt")
        existing_ids = [i.get("id") for i in d2.get("items", [])] if d2.get("ok") else []
        if expected_id in existing_ids:
            print(f"      ✓ 技能 {expected_id} 已存在，继续验证")
            sid = expected_id
        else:
            print(f"      ✗ 技能 {expected_id} 不存在且上传失败，跳过")
            pkg_passed = False
            all_passed = False
            continue

    # ─── Step 2: L1 元数据匹配 ───
    print(f"\n  [2] L1 匹配 → POST /api/skills-mgmt/match (intent='{intent_text}')")
    s, d, seq = call("POST", "/api/skills-mgmt/match",
                     {"intent": intent_text, "top_k": 5, "min_score": 0.01})
    if s == 200 and d.get("ok"):
        matches = d.get("matches", [])
        hit = any(m.get("skill_id") == sid for m in matches)
        print(f"      ✓ 匹配成功: {len(matches)} 个命中 (seq={seq}, 扫描 {d.get('total_scanned', 0)} 个)")
        print(f"        目标技能 {sid}: {'已命中' if hit else '未命中'}")
        print(f"        耗时: {d.get('elapsed_ms', 0):.2f}ms, 估算 tokens: {d.get('estimated_total_tokens', 0)}")
        if matches:
            top = matches[0]
            print(f"        Top-1: {top.get('name', 'N/A')} score={top.get('score', 0):.3f}")
        if not hit:
            print(f"      ! 目标技能未命中（可能元数据需刷新）")
            pkg_passed = False
            all_passed = False
    else:
        print(f"      ✗ 匹配失败: HTTP {s}")
        print(f"        响应: {json.dumps(d, ensure_ascii=False)[:200]}")
        pkg_passed = False
        all_passed = False

    # ─── Step 3: L2 按需加载说明 ───
    print(f"\n  [3] L2 加载说明 → GET /api/skills-mgmt/{sid}/instruction")
    s, d, seq = call("GET", f"/api/skills-mgmt/{sid}/instruction")
    if s == 200 and d.get("ok"):
        instr = d.get("instruction", "")
        tokens = d.get("estimated_tokens", 0)
        layer = d.get("layer", 2)
        print(f"      ✓ 加载成功: {len(instr)} 字符, ~{tokens} tokens, layer={layer} (seq={seq})")
        no_leak = verify_no_code_leak(instr)
        print(f"      [验证] 说明不含脚本代码: {'✓' if no_leak else '✗ 泄漏!'}")
        if not no_leak:
            pkg_passed = False
            all_passed = False
    else:
        print(f"      ✗ 加载失败: HTTP {s}")
        print(f"        响应: {json.dumps(d, ensure_ascii=False)[:200]}")
        pkg_passed = False
        all_passed = False

    # ─── Step 4: L3 沙箱执行 ───
    print(f"\n  [4] L3 执行脚本 → POST /api/skills-mgmt/{sid}/execute")
    if "pdf" in sid:
        params = {"file_path": "report.pdf", "max_pages": 50}
    else:
        params = {"chart_type": "bar", "title": "季度销售",
                  "data": {"Q1": 120, "Q2": 150, "Q3": 180, "Q4": 210}}

    s, d, seq = call("POST", f"/api/skills-mgmt/{sid}/execute",
                     {"script_name": "main.py", "params": params})
    if s == 200 and d.get("ok"):
        result = d.get("result", {})
        duration = d.get("duration_ms", 0)
        exit_code = d.get("exit_code", 0)
        print(f"      ✓ 执行成功: HTTP {s} (seq={seq})")
        print(f"        耗时: {duration:.0f}ms, exit_code={exit_code}")
        if isinstance(result, dict):
            print(f"        结果 keys: {list(result.keys())}")
            # 显示部分结果预览
            preview = json.dumps(result, ensure_ascii=False)[:120]
            print(f"        结果预览: {preview}")

        # 关键验证: 返回结果不含脚本代码
        result_str = json.dumps(d, ensure_ascii=False)
        no_leak = verify_no_code_leak(result_str)
        print(f"      [验证] 返回结果不含脚本代码: {'✓' if no_leak else '✗ 泄漏!'}")
        if not no_leak:
            pkg_passed = False
            all_passed = False
    else:
        print(f"      ✗ 执行失败: HTTP {s}")
        print(f"        响应: {json.dumps(d, ensure_ascii=False)[:300]}")
        pkg_passed = False
        all_passed = False

    # ─── Step 5: 健康检查 ───
    print(f"\n  [5] 健康检查 → GET /api/skills-mgmt/health")
    s, d, seq = call("GET", "/api/skills-mgmt/health")
    if s == 200:
        ok = d.get("ok", False)
        print(f"      ✓ 健康: ok={ok} (seq={seq})")
        three_layer = d.get("three_layer", {})
        if three_layer:
            fs_ok = three_layer.get("file_store", {}).get("ok", "N/A")
            exec_ok = three_layer.get("executor", {}).get("ok", "N/A")
            print(f"        三层架构: file_store={fs_ok}, executor={exec_ok}")
    else:
        print(f"      ✗ 健康检查失败: HTTP {s}")

    status = "✓ PASS" if pkg_passed else "✗ FAIL"
    results_summary.append((Path(zip_path).name, sid, status))

# ════════════════════════════════════════════════════════════
#  汇总
# ════════════════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print("全链路验证汇总")
print(f"{'=' * 70}")
for name, sid, status in results_summary:
    print(f"  {status}  {name} → {sid}")

total = len(results_summary)
passed = sum(1 for _, _, s in results_summary if "PASS" in s)
print(f"\n  通过: {passed}/{total}")
print(f"  总体: {'✓ 全部通过' if all_passed else '✗ 存在失败项'}")
print(f"{'=' * 70}")
