#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TLM 核心模块测试覆盖率 HTML 报告生成器

【不易】基于 coverage.xml 真实数据生成报告，不编造数据
【变易】支持多维度分析：模块级/类级/行级
【简易】单一脚本输出 HTML + Markdown 双格式报告

用法:
    python scripts/generate_coverage_html_report.py
    python scripts/generate_coverage_html_report.py --xml coverage.xml --out-html docs/coverage_report.html
"""
import argparse
import os
import sys
from datetime import datetime
from html import escape
from pathlib import Path
from xml.etree import ElementTree as ET


# =====================================================================
# 核心模块定义（按业务重要性排序）
# =====================================================================
CORE_MODULES = {
    "agent/memory/long_term_memory.py": {
        "name": "LongTermMemory",
        "desc": "TLM 三层记忆架构核心 - 长期记忆存储",
        "threshold": 80,
        "critical": True,
        "fallback_coverage": None,  # coverage.xml 中有真实数据
    },
    "memory/vector_store/vector_store.py": {
        "name": "VectorStore",
        "desc": "向量存储抽象层 - 语义检索入口",
        "threshold": 80,
        "critical": True,
        # 【变易】coverage.xml(07-12) 中缺失，使用 L3 测试日志参考值
        "fallback_coverage": 44.0,
        "fallback_source": "L3 Docker 测试日志（2026-07-29）",
    },
    "memory/vector_store/sqlite_vec_backend.py": {
        "name": "SqliteVecBackend",
        "desc": "sqlite-vec KNN 后端 - L2 性能关键路径",
        "threshold": 80,
        "critical": True,
        "fallback_coverage": 89.0,
        "fallback_source": "L3 Docker 测试日志（2026-07-29）",
    },
    "agent/env_config_manager.py": {
        "name": "EnvConfigManager",
        "desc": "环境配置管理 - 单例工厂（历史 P1 故障模块）",
        "threshold": 80,
        "critical": True,
        "fallback_coverage": None,  # 待测，无参考数据
    },
    "agent/network_config.py": {
        "name": "NetworkConfig",
        "desc": "网络配置管理",
        "threshold": 80,
        "critical": False,
        "fallback_coverage": None,
    },
}

# 次要模块（信息展示，不强制阈值）
SECONDARY_MODULES = {
    "agent/memory/": "Memory 子模块汇总",
    "memory/vector_store/": "VectorStore 子模块汇总",
    "scripts/predownload_models.py": "预下载模型脚本",
}


def parse_coverage_xml(xml_path: str) -> dict:
    """解析 coverage.xml，提取模块覆盖率数据

    Returns:
        {
            "summary": {total_lines, covered_lines, line_rate, ...},
            "files": {filepath: {line_rate, covered, missing, total, missing_lines: [...]}}
        }
    """
    if not os.path.exists(xml_path):
        print(f"[ERROR] coverage.xml 不存在: {xml_path}")
        sys.exit(1)

    tree = ET.parse(xml_path)
    root = tree.getroot()

    # 全局汇总
    summary = {
        "line_rate": float(root.get("line-rate", "0")),
        "branch_rate": float(root.get("branch-rate", "0")),
        "lines_covered": int(root.get("lines-covered", "0")),
        "lines_valid": int(root.get("lines-valid", "0")),
        "branches_covered": int(root.get("branches-covered", "0")),
        "branches_valid": int(root.get("branches-valid", "0")),
    }

    files = {}
    # coverage.py 的 XML 结构: <packages><package><classes><class filename=...>
    for cls in root.iter("class"):
        filename = cls.get("filename", "")
        if not filename:
            continue

        line_rate = float(cls.get("line-rate", "0"))
        branch_rate = float(cls.get("branch-rate", "0"))

        # 提取行级缺失信息
        missing_lines = []
        covered_count = 0
        total_count = 0

        for lines_el in cls.iter("lines"):
            for line_el in lines_el.iter("line"):
                line_num = int(line_el.get("number", "0"))
                hits = int(line_el.get("hits", "0"))
                total_count += 1
                if hits > 0:
                    covered_count += 1
                else:
                    missing_lines.append(line_num)

        # 若 lines 标签缺失，用 line-rate 推算
        if total_count == 0:
            # 从 class 属性获取
            for lines_el in cls.iter("lines"):
                total_count = int(lines_el.get("count", "0"))

        files[filename] = {
            "line_rate": line_rate,
            "branch_rate": branch_rate,
            "covered": covered_count,
            "total": total_count,
            "missing_lines": missing_lines,
        }

    return {"summary": summary, "files": files}


def analyze_core_modules(data: dict) -> list:
    """分析核心模块覆盖率，返回排序后的结果列表

    【变易】coverage.xml 路径格式可能因生成环境不同而异:
      - 相对于 agent/ 目录: memory/long_term_memory.py
      - 完整路径: agent/memory/long_term_memory.py
      - 绝对路径: /app/agent/memory/long_term_memory.py
    采用文件名 + 父目录双重模糊匹配，兼容所有格式。
    """
    results = []
    files = data["files"]

    for filepath, meta in CORE_MODULES.items():
        # 提取目标文件名和父目录用于模糊匹配
        norm_target = filepath.replace("\\", "/").lower()
        target_parts = norm_target.split("/")
        target_filename = target_parts[-1]  # 如 long_term_memory.py
        target_parent = target_parts[-2] if len(target_parts) >= 2 else ""  # 如 memory

        matched = None
        best_score = 0
        for xml_path in files:
            norm_xml = xml_path.replace("\\", "/").lower()
            xml_parts = norm_xml.split("/")
            xml_filename = xml_parts[-1]
            xml_parent = xml_parts[-2] if len(xml_parts) >= 2 else ""

            # 精确路径匹配（最高优先级）
            if norm_target in norm_xml or norm_xml.endswith(norm_target):
                matched = xml_path
                best_score = 100
                break

            # 文件名 + 父目录匹配（次优先级）
            if xml_filename == target_filename and target_parent and xml_parent == target_parent:
                score = 80
                if score > best_score:
                    matched = xml_path
                    best_score = score

            # 仅文件名匹配（最低优先级，需文件名足够独特）
            elif xml_filename == target_filename and target_filename not in (
                "__init__.py", "config.py", "base.py"
            ):
                score = 50
                if score > best_score:
                    matched = xml_path
                    best_score = score

        if matched:
            fdata = files[matched]
            pct = fdata["line_rate"] * 100
            results.append({
                "path": filepath,
                "xml_path": matched,
                "name": meta["name"],
                "desc": meta["desc"],
                "critical": meta["critical"],
                "threshold": meta["threshold"],
                "coverage": pct,
                "covered": fdata["covered"],
                "total": fdata["total"],
                "missing_count": len(fdata["missing_lines"]),
                "missing_lines": fdata["missing_lines"],
                "status": "pass" if pct >= meta["threshold"] else "fail",
                "data_source": "coverage.xml（真实数据）",
            })
        else:
            # 【变易】coverage.xml 中缺失时，使用 fallback 参考数据
            fallback = meta.get("fallback_coverage")
            fallback_src = meta.get("fallback_source", "未知")
            if fallback is not None:
                pct = fallback
                status = "pass" if pct >= meta["threshold"] else "fail"
                data_src = f"参考值（{fallback_src}）"
            else:
                pct = 0.0
                status = "missing"
                data_src = "待测（无参考数据）"
            results.append({
                "path": filepath,
                "xml_path": None,
                "name": meta["name"],
                "desc": meta["desc"],
                "critical": meta["critical"],
                "threshold": meta["threshold"],
                "coverage": pct,
                "covered": 0,
                "total": 0,
                "missing_count": 0,
                "missing_lines": [],
                "status": status,
                "data_source": data_src,
            })

    # 按覆盖率升序（最低的排前面）
    results.sort(key=lambda x: x["coverage"])
    return results


def render_range(missing_lines: list) -> str:
    """将缺失行号列表压缩为区间字符串（如 1-5, 10, 20-25）"""
    if not missing_lines:
        return "<span class='good'>无缺失</span>"
    missing_sorted = sorted(set(missing_lines))
    ranges = []
    start = missing_sorted[0]
    prev = start
    for n in missing_sorted[1:]:
        if n == prev + 1:
            prev = n
        else:
            ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
            start = n
            prev = n
    ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
    # 限制显示长度
    text = ", ".join(ranges)
    if len(text) > 200:
        text = text[:200] + f"... (共 {len(missing_sorted)} 行)"
    return f"<code class='missing'>{escape(text)}</code>"


def generate_html_report(data: dict, core_results: list, output_path: str) -> None:
    """生成 HTML 覆盖率报告"""
    summary = data["summary"]
    overall_pct = summary["line_rate"] * 100

    # 统计核心模块通过率
    core_pass = sum(1 for r in core_results if r["status"] == "pass")
    core_fail = sum(1 for r in core_results if r["status"] == "fail")
    core_missing = sum(1 for r in core_results if r["status"] == "missing")

    # 生成核心模块表格行
    rows_html = []
    for r in core_results:
        status_class = r["status"]
        status_icon = {"pass": "✅", "fail": "❌", "missing": "⚠️"}[r["status"]]
        critical_tag = '<span class="tag critical">核心</span>' if r["critical"] else ""
        missing_html = render_range(r["missing_lines"]) if r["status"] != "missing" else "<em>未在 coverage.xml 中找到</em>"
        # 数据来源标注
        src = r.get("data_source", "")
        src_tag = f'<br><small class="src">{escape(src)}</small>' if src else ""
        # 覆盖/总行数（fallback 数据时显示 N/A）
        cov_total = f"{r['covered']}/{r['total']}" if r["total"] > 0 else "<em>参考值</em>"

        rows_html.append(f"""
        <tr class="{status_class}">
          <td><strong>{escape(r['name'])}</strong>{critical_tag}<br><small>{escape(r['desc'])}</small></td>
          <td><code>{escape(r['path'])}</code></td>
          <td class="pct {status_class}">{r['coverage']:.1f}%{src_tag}</td>
          <td>{cov_total}</td>
          <td>{r['missing_count'] if r['total'] > 0 else '-'}</td>
          <td>{status_icon} {r['threshold']}%</td>
          <td class="missing-lines">{missing_html}</td>
        </tr>""")

    # 覆盖率柱状图（纯 CSS）
    bar_chart = []
    for r in core_results:
        color = "#28a745" if r["status"] == "pass" else "#dc3545" if r["status"] == "fail" else "#ffc107"
        bar_chart.append(f"""
        <div class="bar-row">
          <span class="bar-label">{escape(r['name'])}</span>
          <div class="bar-track">
            <div class="bar-fill" style="width:{r['coverage']:.1f}%;background:{color}"></div>
            <span class="bar-pct">{r['coverage']:.1f}%</span>
          </div>
        </div>""")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TLM 核心模块测试覆盖率报告</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; background: #f5f7fa; color: #2c3e50; line-height: 1.6; padding: 20px; }}
    .container {{ max-width: 1400px; margin: 0 auto; }}
    h1 {{ color: #1a1a2e; border-bottom: 3px solid #4a6fa5; padding-bottom: 10px; margin-bottom: 20px; }}
    h2 {{ color: #2c3e50; margin: 30px 0 15px; padding-left: 12px; border-left: 4px solid #4a6fa5; }}
    .meta {{ background: #fff; padding: 15px 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 20px; font-size: 14px; color: #6c757d; }}

    .summary-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px; margin-bottom: 30px; }}
    .card {{ background: #fff; padding: 20px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); text-align: center; }}
    .card .value {{ font-size: 32px; font-weight: 700; margin: 8px 0; }}
    .card .label {{ font-size: 13px; color: #6c757d; text-transform: uppercase; letter-spacing: 0.5px; }}
    .card.pass .value {{ color: #28a745; }}
    .card.fail .value {{ color: #dc3545; }}
    .card.warn .value {{ color: #ffc107; }}
    .card.info .value {{ color: #4a6fa5; }}

    .bar-chart {{ background: #fff; padding: 20px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); margin-bottom: 30px; }}
    .bar-row {{ display: flex; align-items: center; margin-bottom: 10px; }}
    .bar-label {{ width: 220px; font-size: 14px; font-weight: 500; flex-shrink: 0; }}
    .bar-track {{ flex: 1; height: 28px; background: #e9ecef; border-radius: 14px; position: relative; overflow: hidden; }}
    .bar-fill {{ height: 100%; border-radius: 14px; transition: width 0.8s ease; min-width: 2px; }}
    .bar-pct {{ position: absolute; right: 10px; top: 50%; transform: translateY(-50%); font-size: 13px; font-weight: 600; color: #2c3e50; }}

    table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); font-size: 14px; }}
    th {{ background: #4a6fa5; color: #fff; padding: 12px 15px; text-align: left; font-weight: 600; }}
    td {{ padding: 12px 15px; border-bottom: 1px solid #e9ecef; vertical-align: top; }}
    tr:hover td {{ background: #f8f9fa; }}
    tr.fail td {{ background: #fff5f5; }}
    tr.pass td {{ background: #f0fff4; }}
    tr.missing td {{ background: #fffbeb; }}

    .pct {{ font-weight: 700; font-size: 16px; }}
    .pct.pass {{ color: #28a745; }}
    .pct.fail {{ color: #dc3545; }}
    .pct.missing {{ color: #ffc107; }}

    .tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; margin-left: 8px; }}
    .tag.critical {{ background: #fee2e2; color: #dc2626; }}

    .missing-lines {{ max-width: 300px; word-break: break-all; }}
    .missing {{ background: #fee2e2; padding: 2px 6px; border-radius: 3px; font-size: 12px; color: #dc2626; }}
    .good {{ color: #28a745; font-weight: 600; }}
    .src {{ color: #6c757d; font-size: 11px; font-style: italic; }}

    .alert {{ padding: 15px 20px; border-radius: 8px; margin-bottom: 20px; }}
    .alert.danger {{ background: #fee2e2; border-left: 4px solid #dc2626; color: #991b1b; }}
    .alert.success {{ background: #d1fae5; border-left: 4px solid #28a745; color: #065f46; }}
    .alert h3 {{ margin-bottom: 8px; }}

    .footer {{ text-align: center; margin-top: 40px; padding: 20px; color: #6c757d; font-size: 13px; border-top: 1px solid #e9ecef; }}
    code {{ background: #f1f3f5; padding: 1px 4px; border-radius: 3px; font-size: 12px; }}
    small {{ color: #6c757d; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>📊 TLM 核心模块测试覆盖率报告</h1>
    <div class="meta">
      <strong>生成时间</strong>: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} |
      <strong>数据来源</strong>: coverage.xml（2026-07-12）+ L3 测试日志补充 |
      <strong>报告类型</strong>: TLM 核心模块覆盖率分析 |
      <strong>全局行覆盖率</strong>: {overall_pct:.1f}%
    </div>
    <div class="alert danger" style="margin-bottom:20px">
      <strong>数据说明</strong>: coverage.xml 生成于 2026-07-12，部分新增模块（VectorStore/SqliteVecBackend）
      在该文件中缺失，已用 L3 Docker 测试日志（2026-07-29）的参考覆盖率补充。
      建议运行 <code>run_l3_regression_tests.ps1 -Mode all</code> 重新生成完整 coverage.xml。
    </div>

    <h2>📈 全局覆盖率概览</h2>
    <div class="summary-cards">
      <div class="card {'pass' if overall_pct >= 80 else 'fail'}">
        <div class="label">全局行覆盖率</div>
        <div class="value">{overall_pct:.1f}%</div>
      </div>
      <div class="card info">
        <div class="label">已覆盖行数</div>
        <div class="value">{summary['lines_covered']:,}</div>
      </div>
      <div class="card info">
        <div class="label">总有效行数</div>
        <div class="value">{summary['lines_valid']:,}</div>
      </div>
      <div class="card {'pass' if core_pass > core_fail else 'fail'}">
        <div class="label">核心模块通过数</div>
        <div class="value">{core_pass}/{len(core_results)}</div>
      </div>
    </div>

    <h2>📊 核心模块覆盖率柱状图</h2>
    <div class="bar-chart">
      {''.join(bar_chart)}
    </div>

    {_render_alert(core_fail, core_missing)}

    <h2>核心模块覆盖率明细</h2>
    <table>
      <thead>
        <tr>
          <th>模块</th>
          <th>文件路径</th>
          <th>覆盖率</th>
          <th>覆盖/总行</th>
          <th>缺失行数</th>
          <th>阈值</th>
          <th>缺失行号</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows_html)}
      </tbody>
    </table>

    <h2>💡 覆盖率优化建议</h2>
    <div class="alert {'danger' if core_fail > 0 else 'success'}">
      <h3>{'❌ 存在覆盖率不足 80% 的核心模块' if core_fail > 0 else '✅ 所有核心模块覆盖率达标'}</h3>
      {_render_suggestions(core_results)}
    </div>

    <div class="footer">
      本报告由 <code>scripts/generate_coverage_html_report.py</code> 自动生成 |
      基于 coverage.py XML 数据 | 三义原则: 不易(真实数据) · 变易(多维分析) · 简易(单一脚本)
    </div>
  </div>
</body>
</html>"""

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[OK] HTML 报告已生成: {output_path}")


def _render_alert(core_fail: int, core_missing: int) -> str:
    if core_fail == 0 and core_missing == 0:
        return '<div class="alert success"><h3>✅ 所有核心模块覆盖率 ≥ 80%</h3><p>当前测试覆盖充分，无紧急补充需求。</p></div>'
    parts = ['<div class="alert danger"><h3>⚠️ 核心模块覆盖率告警</h3><ul>']
    if core_fail > 0:
        parts.append(f"<li><strong>{core_fail}</strong> 个核心模块覆盖率低于 80% 阈值，需补充测试用例</li>")
    if core_missing > 0:
        parts.append(f"<li><strong>{core_missing}</strong> 个核心模块在 coverage.xml 中未找到（可能未被测试覆盖）</li>")
    parts.append("</ul></div>")
    return "".join(parts)


def _render_suggestions(core_results: list) -> str:
    failing = [r for r in core_results if r["status"] == "fail"]
    if not failing:
        return "<p>当前所有核心模块覆盖率达标，建议定期回归验证保持水位。</p>"

    suggestions = ["<ul>"]
    for r in failing:
        suggestions.append(f"<li><strong>{escape(r['name'])}</strong> ({r['coverage']:.1f}%): ")
        # 根据模块给出针对性建议
        if "long_term_memory" in r["path"]:
            suggestions.append("补充 <code>search()</code>、<code>search_semantic_vec_knn()</code>、<code>_normalize_vector()</code> 的边界测试；")
            suggestions.append("添加 vec0 表维度不匹配降级路径测试；")
            suggestions.append("补充 BLOB/JSON TEXT 多格式兼容性测试（<code>_blob_to_embedding</code>）。")
        elif "vector_store.py" in r["path"]:
            suggestions.append("补充 <code>_init_chroma()</code> 失败降级路径测试；")
            suggestions.append("添加 ChromaDB 不可用时 BM25 fallback 测试；")
            suggestions.append("补充 <code>add()/search()</code> 异常输入测试。")
        elif "env_config_manager" in r["path"]:
            suggestions.append("补充单例工厂 <code>get_instance()</code> 并发测试；")
            suggestions.append("添加 .env 文件缺失时的降级测试。")
        else:
            suggestions.append(f"补充未覆盖的 {r['missing_count']} 行代码的测试用例。")
        suggestions.append("</li>")
    suggestions.append("</ul>")
    return "".join(suggestions)


def generate_markdown_summary(core_results: list, data: dict, output_path: str) -> None:
    """生成 Markdown 覆盖率分析摘要"""
    summary = data["summary"]
    overall_pct = summary["line_rate"] * 100

    lines = [
        "# TLM 核心模块测试覆盖率分析报告",
        "",
        f"> **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"> **数据来源**: coverage.xml（2026-07-12 生成）+ L3 测试日志补充  ",
        f"> **全局行覆盖率**: {overall_pct:.1f}% ({summary['lines_covered']:,}/{summary['lines_valid']:,})",
        "",
        "> **重要说明**: coverage.xml 生成于 2026-07-12，部分新增模块（VectorStore/SqliteVecBackend）",
        "> 在该文件中缺失，已用 L3 Docker 测试日志（2026-07-29）的参考覆盖率补充。",
        "> EnvConfigManager 暂无覆盖率数据（待测）。建议运行 L3 全量测试重新生成 coverage.xml。",
        "",
        "---",
        "",
        "## 核心模块覆盖率明细",
        "",
        "| 模块 | 文件路径 | 覆盖率 | 覆盖/总行 | 缺失行数 | 阈值 | 状态 | 数据来源 |",
        "|------|---------|--------|----------|---------|------|------|---------|",
    ]

    for r in core_results:
        icon = "✅" if r["status"] == "pass" else "❌" if r["status"] == "fail" else "⚠️"
        cov_total = f"{r['covered']}/{r['total']}" if r["total"] > 0 else "参考值"
        missing = str(r["missing_count"]) if r["total"] > 0 else "-"
        src = r.get("data_source", "")
        lines.append(
            f"| {r['name']} | `{r['path']}` | {r['coverage']:.1f}% | {cov_total} | {missing} | {r['threshold']}% | {icon} | {src} |"
        )

    # 覆盖率不足模块分析
    failing = [r for r in core_results if r["status"] == "fail"]
    missing_mods = [r for r in core_results if r["status"] == "missing"]
    lines.extend(["", "## 覆盖率不足 80% 的核心模块分析", ""])

    if not failing:
        lines.append("✅ 所有核心模块覆盖率均 ≥ 80%，无紧急补充需求。")
    else:
        lines.append(f"共 **{len(failing)}** 个核心模块覆盖率不足 80%，需补充测试用例：")
        lines.append("")
        for r in failing:
            lines.extend([
                f"### {r['name']} ({r['coverage']:.1f}%)",
                "",
                f"- **文件**: `{r['path']}`",
                f"- **描述**: {r['desc']}",
                f"- **数据来源**: {r.get('data_source', '未知')}",
            ])
            if r["total"] > 0:
                lines.extend([
                    f"- **缺失行数**: {r['missing_count']} 行",
                    f"- **缺失行号**: {render_range_md(r['missing_lines']) if r['missing_lines'] else '无'}",
                ])
            else:
                lines.append("- **缺失行数**: 暂无行级数据（参考值来自 L3 测试日志）")
            lines.append("")

            # 针对性建议
            if "long_term_memory" in r["path"]:
                lines.extend([
                    "**优化建议**:",
                    "- 补充 `search()` / `search_semantic_vec_knn()` 的边界测试（空查询、维度不匹配）",
                    "- 添加 vec0 表降级路径测试（sqlite-vec 不可用时回退纯 Python）",
                    "- 补充 `_blob_to_embedding` 五种格式兼容性测试（BLOB/JSON TEXT/memoryview/str/list）",
                    "- 添加 `_normalize_vector` 零向量输入测试",
                    "",
                ])
            elif "vector_store.py" in r["path"]:
                lines.extend([
                    "**优化建议**:",
                    "- 补充 `_init_chroma()` 失败降级路径测试（Rust 后端不兼容场景）",
                    "- 添加 ChromaDB 不可用时 BM25 fallback 完整测试",
                    "- 补充 `add()` / `search()` 异常输入测试（None、空列表、超大输入）",
                    "- 添加并发写入测试（验证线程安全）",
                    "",
                ])
            elif "network_config" in r["path"]:
                lines.extend([
                    "**优化建议**:",
                    "- 清理历史类型债（29 个 mypy 错误，详见 ci.yml TODO 注释）",
                    "- 补充网络配置异常路径测试（DNS 解析失败、连接超时）",
                    "- 添加配置热更新测试（运行时修改 .env 的行为验证）",
                    "",
                ])

    # 缺失数据模块说明
    if missing_mods:
        lines.extend([
            "## 覆盖率数据缺失模块（待测）",
            "",
            "以下模块在 coverage.xml 中未找到，且无 L3 测试日志参考数据：",
            "",
        ])
        for r in missing_mods:
            lines.extend([
                f"### {r['name']}",
                "",
                f"- **文件**: `{r['path']}`",
                f"- **描述**: {r['desc']}",
                f"- **状态**: 待测（需运行 L3 测试获取覆盖率数据）",
                "",
            ])
            if "env_config_manager" in r["path"]:
                lines.extend([
                    "**重要性说明**: 此模块是历史 P1 故障模块（v1.2.1-fix-secure-manager-return），",
                    "单例工厂 return 缺失曾导致生产故障，必须优先补全覆盖率数据。",
                    "",
                    "**获取覆盖率方法**:",
                    "```bash",
                    ".\\scripts\\run_l3_regression_tests.ps1 -Mode all -Rebuild",
                    "```",
                    "",
                ])

    lines.extend([
        "---",
        "",
        "## 报告说明",
        "",
        "- **数据来源**: coverage.xml（2026-07-12）+ L3 Docker 测试日志（2026-07-29）参考值",
        "- **覆盖率计算**: `line_rate = 已覆盖行数 / 总有效行数`",
        "- **阈值标准**: 核心模块 ≥ 80%（业务关键路径）",
        "- **生成工具**: `scripts/generate_coverage_html_report.py`",
        "- **三义原则**: 不易(真实数据不编造) · 变易(多数据源融合) · 简易(单一脚本双格式输出)",
        "",
    ])

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[OK] Markdown 报告已生成: {output_path}")


def render_range_md(missing_lines: list) -> str:
    """Markdown 格式的缺失行号区间"""
    if not missing_lines:
        return "无"
    missing_sorted = sorted(set(missing_lines))
    ranges = []
    start = missing_sorted[0]
    prev = start
    for n in missing_sorted[1:]:
        if n == prev + 1:
            prev = n
        else:
            ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
            start = n
            prev = n
    ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
    text = ", ".join(ranges)
    if len(text) > 300:
        text = text[:300] + f"... (共 {len(missing_sorted)} 行)"
    return text


def main():
    parser = argparse.ArgumentParser(description="TLM 核心模块测试覆盖率报告生成器")
    parser.add_argument("--xml", default="coverage.xml", help="coverage.xml 路径")
    parser.add_argument("--out-html", default="docs/coverage_report.html", help="HTML 报告输出路径")
    parser.add_argument("--out-md", default="docs/COVERAGE_ANALYSIS.md", help="Markdown 报告输出路径")
    args = parser.parse_args()

    print(f"=== 解析 coverage.xml: {args.xml} ===")
    data = parse_coverage_xml(args.xml)
    print(f"[OK] 全局行覆盖率: {data['summary']['line_rate']*100:.1f}%")
    print(f"[OK] 已测量文件数: {len(data['files'])}")

    print("\n=== 分析核心模块覆盖率 ===")
    core_results = analyze_core_modules(data)
    for r in core_results:
        icon = "✅" if r["status"] == "pass" else "❌" if r["status"] == "fail" else "⚠️"
        print(f"  {icon} {r['name']}: {r['coverage']:.1f}% ({r['covered']}/{r['total']}) - {r['status']}")

    print(f"\n=== 生成 HTML 报告: {args.out_html} ===")
    generate_html_report(data, core_results, args.out_html)

    print(f"\n=== 生成 Markdown 报告: {args.out_md} ===")
    generate_markdown_summary(core_results, data, args.out_md)

    print("\n=== 完成 ===")


if __name__ == "__main__":
    main()
