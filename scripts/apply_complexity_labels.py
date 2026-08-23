"""复杂度样本人工标注应用（复查补充 · P0-1 判定源选型数据）

把 `data/complexity_samples.json`（150 条）的复杂度 ground truth 标注写入
`data/complexity_labeled.jsonl`（label_status=labeled, labeled_by=manual）。

标注标准（与 wire 启发式语义对齐的 canonical 分档）：
    TRIVIAL   单步信息获取/概念问答/简单计算（无工具链、无多步骤）
    SIMPLE    单一生成/单次工具任务（单段文案、单脚本、单次整理/翻译/设置）
    NORMAL    分析类/中等设计/局部重构/多实体组合（需综合多源信息或组合能力）
    COMPLEX   系统级设计/架构/平台搭建/重构迁移（系统对象）/显式多步骤流程/
              完整方案/分布式系统

判定以 message（用户语句）为主，note（来源备注）为辅；每条给出判定依据。

用法:
    python scripts/apply_complexity_labels.py            # 应用标注并校验
    python scripts/apply_complexity_labels.py --dry-run  # 只打印统计不写文件
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "complexity_samples.json"
TARGET = Path(__file__).resolve().parent.parent / "data" / "complexity_labeled.jsonl"

# 标注映射（id → (level, 判定依据)）。依据仅记录于本字典与生成的注释字段，
# 不改动样本原始内容。标注口径见模块 docstring。
LABELS: dict = {
    # ── TRIVIAL：单步信息获取 / 概念问答 / 简单计算 ──
    "curated-001": ("TRIVIAL", "单步天气查询"),
    "curated-002": ("TRIVIAL", "单步时间查询"),
    "curated-003": ("TRIVIAL", "单步航班信息查询"),
    "curated-004": ("TRIVIAL", "概念问答"),
    "curated-005": ("TRIVIAL", "概念问答"),
    "curated-006": ("TRIVIAL", "单步交通信息查询"),
    "curated-008": ("TRIVIAL", "简单数学计算"),
    "curated-009": ("TRIVIAL", "知识问答"),
    "curated-010": ("TRIVIAL", "单步新闻查询"),
    "curated-011": ("TRIVIAL", "单步信息查询"),
    "curated-012": ("TRIVIAL", "概念问答"),
    "curated-013": ("TRIVIAL", "单步推荐问答"),
    "curated-014": ("TRIVIAL", "单步信息查询"),
    "curated-016": ("TRIVIAL", "单步天气查询"),
    "curated-017": ("TRIVIAL", "概念问答"),
    "curated-018": ("TRIVIAL", "知识问答"),
    "curated-019": ("TRIVIAL", "简单计算"),
    "curated-020": ("TRIVIAL", "单步推荐问答"),
    "curated-021": ("TRIVIAL", "单步查询"),
    "curated-022": ("TRIVIAL", "单步检查"),
    "curated-023": ("TRIVIAL", "知识问答"),
    "curated-024": ("TRIVIAL", "概念问答"),
    "curated-025": ("TRIVIAL", "单步查询"),
    "curated-026": ("TRIVIAL", "单步导航查询"),
    "curated-027": ("TRIVIAL", "单步查询"),
    "curated-028": ("TRIVIAL", "知识问答"),
    "curated-029": ("TRIVIAL", "知识问答"),
    "curated-030": ("TRIVIAL", "单步信息查询"),
    "curated-053": ("TRIVIAL", "单步快递查询"),
    "curated-059": ("TRIVIAL", "单步限行查询"),
    # ── SIMPLE：单一生成 / 单次工具任务 ──
    "curated-007": ("SIMPLE", "单段翻译"),
    "curated-015": ("SIMPLE", "单次提醒设置"),
    "curated-031": ("SIMPLE", "单段文案生成"),
    "curated-032": ("SIMPLE", "单封邮件生成"),
    "curated-033": ("SIMPLE", "单模板生成"),
    "curated-034": ("SIMPLE", "单一脚本生成（明确简单）"),
    "curated-035": ("SIMPLE", "单段文案生成"),
    "curated-036": ("SIMPLE", "单份纪要起草"),
    "curated-037": ("SIMPLE", "单一函数实现"),
    "curated-038": ("SIMPLE", "单封感谢信生成"),
    "curated-039": ("SIMPLE", "单次密码生成"),
    "curated-040": ("SIMPLE", "单篇文案生成"),
    "curated-041": ("SIMPLE", "单次文件夹整理"),
    "curated-042": ("SIMPLE", "单次会议记录整理"),
    "curated-044": ("SIMPLE", "单次拼写检查"),
    "curated-045": ("SIMPLE", "单次数据汇总"),
    "curated-046": ("SIMPLE", "单次书单整理"),
    "curated-047": ("SIMPLE", "单次待办整理"),
    "curated-048": ("SIMPLE", "单张统计表生成"),
    "curated-049": ("SIMPLE", "单次语法检查"),
    "curated-050": ("SIMPLE", "单次清单整理"),
    "curated-051": ("SIMPLE", "单段翻译"),
    "curated-052": ("SIMPLE", "单次笔记整理"),
    "curated-055": ("SIMPLE", "单次清单整理"),
    "curated-056": ("SIMPLE", "单次二维码生成"),
    "curated-057": ("SIMPLE", "单段创意文案"),
    "curated-058": ("SIMPLE", "单次记录"),
    "curated-060": ("SIMPLE", "单份邀请生成"),
    # ── NORMAL：分析 / 中等设计 / 多实体组合 / 局部重构 ──
    "curated-043": ("NORMAL", "合同多条款审查"),
    "curated-054": ("NORMAL", "批量重命名脚本（多文件处理）"),
    "curated-061": ("NORMAL", "数据分析需综合多字段"),
    "curated-062": ("NORMAL", "可行性分析需多维度"),
    "curated-063": ("NORMAL", "日志归因分析"),
    "curated-064": ("NORMAL", "竞品多对象分析"),
    "curated-065": ("NORMAL", "数据集分布分析"),
    "curated-066": ("NORMAL", "用户流失归因分析"),
    "curated-067": ("NORMAL", "性能瓶颈分析"),
    "curated-068": ("NORMAL", "市场趋势多源分析"),
    "curated-069": ("NORMAL", "方案多角度分析"),
    "curated-070": ("NORMAL", "文本语气分析"),
    "curated-071": ("NORMAL", "产品原型中等设计"),
    "curated-072": ("NORMAL", "调研问卷中等设计"),
    "curated-073": ("NORMAL", "可视化方案中等设计"),
    "curated-074": ("NORMAL", "测试用例集设计（多用例）"),
    "curated-075": ("NORMAL", "课程大纲中等设计"),
    "curated-077": ("NORMAL", "API 接口文档（多接口）"),
    "curated-078": ("NORMAL", "数据库表结构（多表）"),
    "curated-079": ("NORMAL", "报表模板设计"),
    "curated-080": ("NORMAL", "营销方案（单一领域方案）"),
    "curated-091": ("NORMAL", "模块级代码重构（局部对象）"),
    "curated-098": ("NORMAL", "局部遗留代码重构"),
    "curated-099": ("NORMAL", "单配置迁移（局部对象）"),
    "curated-100": ("NORMAL", "测试套件重构（局部对象）"),
    # ── COMPLEX：系统级设计 / 架构 / 平台 / 显式多步骤 / 完整方案 ──
    "curated-076": ("COMPLEX", "推荐算法设计（算法级）"),
    "curated-081": ("COMPLEX", "架构方案（系统级）"),
    "curated-082": ("COMPLEX", "微服务架构（系统级）"),
    "curated-083": ("COMPLEX", "分布式缓存系统（系统级）"),
    "curated-084": ("COMPLEX", "高可用系统（系统级）"),
    "curated-085": ("COMPLEX", "消息队列方案（系统级）"),
    "curated-086": ("COMPLEX", "数据中台（系统级）"),
    "curated-087": ("COMPLEX", "权限系统（系统级）"),
    "curated-088": ("COMPLEX", "监控告警平台（系统级）"),
    "curated-089": ("COMPLEX", "推荐系统架构（系统级）"),
    "curated-090": ("COMPLEX", "订单系统整体架构（系统级）"),
    "curated-092": ("COMPLEX", "系统登录流程重构（系统对象）"),
    "curated-093": ("COMPLEX", "项目迁移新框架（系统级迁移）"),
    "curated-094": ("COMPLEX", "数据库迁移（系统级）"),
    "curated-095": ("COMPLEX", "服务迁移 K8s（系统级）"),
    "curated-096": ("COMPLEX", "报表系统性能重构（系统对象）"),
    "curated-097": ("COMPLEX", "接口迁移新网关（系统级）"),
    "curated-101": ("COMPLEX", "完整上线方案（多阶段）"),
    "curated-102": ("COMPLEX", "完整营销推广方案"),
    "curated-103": ("COMPLEX", "完整灾备方案"),
    "curated-104": ("COMPLEX", "完整安全审计方案"),
    "curated-105": ("COMPLEX", "完整测试计划"),
    "curated-106": ("COMPLEX", "完整发布流程"),
    "curated-107": ("COMPLEX", "完整性能优化方案"),
    "curated-108": ("COMPLEX", "完整数据治理方案"),
    "curated-109": ("COMPLEX", "完整团队建设方案"),
    "curated-110": ("COMPLEX", "完整客户成功方案"),
    "curated-111": ("COMPLEX", "显式多步骤（第一步/第二步）"),
    "curated-112": ("COMPLEX", "显式多步骤（三步）"),
    "curated-113": ("COMPLEX", "显式多步骤（三步）"),
    "curated-114": ("COMPLEX", "显式多步骤（三步）"),
    "curated-115": ("COMPLEX", "显式多步骤（三步）"),
    "curated-116": ("COMPLEX", "显式多步骤（三步）"),
    "curated-117": ("COMPLEX", "显式多步骤（三步）"),
    "curated-118": ("COMPLEX", "显式多步骤（三步）"),
    "curated-119": ("COMPLEX", "显式多步骤（三步）"),
    "curated-120": ("COMPLEX", "显式多步骤（三步）"),
    "curated-121": ("COMPLEX", "完整电商平台搭建"),
    "curated-122": ("COMPLEX", "完整监控系统搭建"),
    "curated-123": ("COMPLEX", "完整日志系统搭建"),
    "curated-124": ("COMPLEX", "完整客服系统搭建"),
    "curated-125": ("COMPLEX", "完整风控系统搭建"),
    "curated-126": ("COMPLEX", "完整消息系统搭建"),
    "curated-127": ("COMPLEX", "完整文件存储系统搭建"),
    "curated-128": ("COMPLEX", "完整搜索系统搭建"),
    "curated-129": ("COMPLEX", "完整支付系统搭建"),
    "curated-130": ("COMPLEX", "完整用户系统搭建"),
    "curated-131": ("COMPLEX", "分布式任务调度系统"),
    "curated-132": ("COMPLEX", "分布式文件系统"),
    "curated-133": ("COMPLEX", "分布式日志采集系统"),
    "curated-134": ("COMPLEX", "分布式事务方案"),
    "curated-135": ("COMPLEX", "分布式锁"),
    "curated-136": ("COMPLEX", "分布式 ID 生成器"),
    "curated-137": ("COMPLEX", "分布式配置中心"),
    "curated-138": ("COMPLEX", "分布式链路追踪系统"),
    "curated-139": ("COMPLEX", "分布式限流系统"),
    "curated-140": ("COMPLEX", "分布式搜索引擎"),
    "curated-141": ("COMPLEX", "设计并实现+完整方案"),
    "curated-142": ("COMPLEX", "设计并实现+完整方案"),
    "curated-143": ("COMPLEX", "设计并实现+完整方案"),
    "curated-144": ("COMPLEX", "设计并实现+完整方案"),
    "curated-145": ("COMPLEX", "设计并实现+完整方案"),
    "curated-146": ("COMPLEX", "设计并实现+完整方案"),
    "curated-147": ("COMPLEX", "设计并实现+完整方案"),
    "curated-148": ("COMPLEX", "设计并实现+完整方案"),
    "curated-149": ("COMPLEX", "设计并实现+完整方案"),
    "curated-150": ("COMPLEX", "设计并实现+完整方案"),
}

# 评估集 v1 补充样本标注（source=eval_set；id 前缀 eval-<类别>-<序号>）。
# 评估集样本为任务描述文本（含指令性前缀），口径与 curated 一致；
# 补充后标注资产达 200 条（满足路线图 S4 启用门 ≥200）。
EVAL_LABELS: dict = {
    # search（单步信息查询 → TRIVIAL）
    "eval-search-001": ("TRIVIAL", "单步信息查询"), "eval-search-002": ("TRIVIAL", "单步天气查询"),
    "eval-search-003": ("TRIVIAL", "单步信息查询"), "eval-search-004": ("TRIVIAL", "单步信息查询"),
    "eval-search-005": ("TRIVIAL", "单步信息查询"), "eval-search-006": ("TRIVIAL", "单步信息查询"),
    "eval-search-007": ("TRIVIAL", "单步信息检索"), "eval-search-008": ("TRIVIAL", "单步信息查询"),
    "eval-search-009": ("TRIVIAL", "单步信息检索"), "eval-search-010": ("TRIVIAL", "单步信息查询"),
    "eval-search-011": ("TRIVIAL", "单步信息检索"), "eval-search-012": ("TRIVIAL", "单步信息查询"),
    # code（单一函数实现 → SIMPLE）
    "eval-code-001": ("SIMPLE", "单一函数实现"), "eval-code-002": ("SIMPLE", "单一函数实现"),
    "eval-code-003": ("SIMPLE", "单一函数实现"), "eval-code-004": ("SIMPLE", "单一函数实现"),
    "eval-code-005": ("SIMPLE", "单一函数实现"), "eval-code-006": ("SIMPLE", "单一函数实现"),
    "eval-code-007": ("SIMPLE", "单一函数实现"), "eval-code-008": ("SIMPLE", "单一函数实现"),
    "eval-code-009": ("SIMPLE", "单一函数实现"), "eval-code-010": ("SIMPLE", "单一函数实现"),
    "eval-code-011": ("SIMPLE", "单一函数实现"), "eval-code-012": ("SIMPLE", "单一函数实现"),
    # chat
    "eval-chat-001": ("SIMPLE", "单段自我介绍生成"), "eval-chat-002": ("SIMPLE", "单次共情回应"),
    "eval-chat-003": ("SIMPLE", "单主题建议"), "eval-chat-004": ("NORMAL", "多时段日程规划"),
    "eval-chat-005": ("TRIVIAL", "礼貌回应"), "eval-chat-006": ("SIMPLE", "单次安慰回应"),
    "eval-chat-007": ("SIMPLE", "单主题建议"), "eval-chat-008": ("TRIVIAL", "单段幽默"),
    "eval-chat-009": ("SIMPLE", "单主题解释"), "eval-chat-010": ("SIMPLE", "单主题学习路径"),
    "eval-chat-011": ("SIMPLE", "单主题建议"), "eval-chat-012": ("SIMPLE", "单封邮件生成"),
    # planning
    "eval-planning-001": ("NORMAL", "多天多活动行程规划"), "eval-planning-002": ("NORMAL", "任务拆解"),
    "eval-planning-003": ("SIMPLE", "例行计划（30 分钟）"), "eval-planning-004": ("NORMAL", "分阶段计划"),
    "eval-planning-005": ("NORMAL", "发布会执行清单"), "eval-planning-006": ("SIMPLE", "优先级排序"),
    "eval-planning-007": ("NORMAL", "分阶段可执行计划"),
    # tool（单次工具调用 → TRIVIAL）
    "eval-tool-001": ("TRIVIAL", "单次工具调用"), "eval-tool-002": ("TRIVIAL", "单次工具调用"),
    "eval-tool-003": ("TRIVIAL", "单次工具调用"), "eval-tool-004": ("TRIVIAL", "单次工具调用"),
    "eval-tool-005": ("TRIVIAL", "单次工具调用"), "eval-tool-006": ("TRIVIAL", "单次工具调用"),
    "eval-tool-007": ("TRIVIAL", "单次工具调用"),
}

# 评估集样本文件（category → path）
EVAL_FILES = {
    "search": "data/evals/search/qa_pairs.json",
    "code": "data/evals/code/code_tasks.json",
    "chat": "data/evals/chat/dialog_flows.json",
    "planning": "data/evals/planning/planning_tasks.json",
    "tool": "data/evals/tool/tool_tasks.json",
}


def _load_eval_samples() -> list:
    """加载评估集样本为 (id, message, source, note) 结构"""
    root = Path(__file__).resolve().parent.parent
    out = []
    for cat, rel in EVAL_FILES.items():
        path = root / rel
        if not path.exists():
            continue
        arr = json.loads(path.read_text(encoding="utf-8"))
        for s in arr:
            sid = str(s.get("id") or "")
            if not sid:
                continue
            out.append({
                "id": f"eval-{sid}",
                "message": str(s.get("task") or s.get("message") or ""),
                "source": "eval_set",
                "note": f"category={cat}",
            })
    return out


def apply(dry_run: bool = False) -> int:
    if not SAMPLES.exists():
        print(f"错误：源样本集不存在 {SAMPLES}")
        return 2
    raw = json.loads(SAMPLES.read_text(encoding="utf-8"))
    samples = raw.get("samples") if isinstance(raw, dict) else raw
    if not isinstance(samples, list):
        print("错误：源样本集结构异常")
        return 2
    # 合并评估集补充样本（source=eval_set）
    samples = list(samples) + _load_eval_samples()

    # 标注映射 = curated + eval
    all_labels = dict(LABELS)
    all_labels.update(EVAL_LABELS)

    unknown = [s.get("id") for s in samples if s.get("id") not in all_labels]
    if unknown:
        print(f"错误：{len(unknown)} 条样本无标注（{unknown[:10]}）")
        return 2

    records = []
    for s in samples:
        sid = str(s.get("id"))
        level, reason = all_labels[sid]
        records.append({
            "id": sid,
            "message": str(s.get("message", "")),
            "source": str(s.get("source", "")),
            "note": str(s.get("note", "") or ""),
            "expected_level": level,
            "label_status": "labeled",
            "labeled_by": "manual",
            "label_reason": reason,
        })

    counts: dict = {}
    for r in records:
        counts[r["expected_level"]] = counts.get(r["expected_level"], 0) + 1
    print(f"标注分布: {counts}（共 {len(records)} 条）")

    if dry_run:
        print("dry-run：未写文件")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text("\n".join(
        json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8")
    print(f"已写入 {TARGET}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="复杂度样本标注应用/校验")
    parser.add_argument("--dry-run", action="store_true", help="只打印统计不写文件")
    parser.add_argument("--check", action="store_true",
                        help="校验既有标注资产（调用 prepare 脚本 check）")
    args = parser.parse_args()
    if args.check:
        from scripts.prepare_complexity_labeling import check
        return check()
    return apply(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
