"""云枢 T8 租户隔离修复结项报告 PDF 生成器

数据来源（2026-08-16 实测）：
- 迁移脚本实测：29 文件 233 条中补全 228 条 → system
- 风险量化：跨租户可见记录 233 → 0（修复前 100% 可见）
- 回归：相关套件 101 passed / 0 failed
- 故障演练：五场景（401×2 / 403 / 429 限流 / 429 配额）全复现 + 定位四步法

用法：python scripts/gen_t8_isolation_report_pdf.py [--output docs/zh/云枢T8租户隔离修复结项报告_20260816.pdf]
依赖：reportlab（已装 4.5.1），中文字体微软雅黑/黑体（Windows）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── 中文字体注册（Windows） ──────────────────────────────
_FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\msyh.ttc", 0),     # 微软雅黑
    (r"C:\Windows\Fonts\simhei.ttf", None),  # 黑体
    (r"C:\Windows\Fonts\simsun.ttc", 0),   # 宋体
]
_FONT_NAME = "HeiTi"
_registered = False
for _path, _idx in _FONT_CANDIDATES:
    if Path(_path).exists():
        pdfmetrics.registerFont(TTFont(_FONT_NAME, _path, subfontIndex=_idx or 0))
        _registered = True
        break
if not _registered:
    # 回退：无中文字体则用内置 Helvetica（中文会缺失，仅兜底）
    _FONT_NAME = "Helvetica"

# ── 样式 ─────────────────────────────────────────────────
def _style(font_size=10.5, leading=16, color="#22262e", bold=False, space_after=6):
    return ParagraphStyle(
        "s", fontName=_FONT_NAME, fontSize=font_size, leading=leading,
        textColor=colors.HexColor(color), spaceAfter=space_after,
        fontName2=_FONT_NAME if not bold else _FONT_NAME,
    )

TITLE = ParagraphStyle("title", fontName=_FONT_NAME, fontSize=20, leading=28,
                       textColor=colors.HexColor("#0f1420"), spaceAfter=4)
SUB = ParagraphStyle("sub", fontName=_FONT_NAME, fontSize=10.5, leading=15,
                     textColor=colors.HexColor("#5a6b85"), spaceAfter=14)
H1 = ParagraphStyle("h1", fontName=_FONT_NAME, fontSize=14, leading=20,
                    textColor=colors.HexColor("#1a6dd8"), spaceBefore=14, spaceAfter=8)
H2 = ParagraphStyle("h2", fontName=_FONT_NAME, fontSize=11.5, leading=17,
                    textColor=colors.HexColor("#2b3a55"), spaceBefore=8, spaceAfter=5)
BODY = _style()
BOLD = _style(bold=True)

NAVY = colors.HexColor("#1a6dd8")
LIGHT = colors.HexColor("#eef3fb")


def _table(rows, widths=None, header=True):
    t = Table(rows, colWidths=widths, hAlign="LEFT")
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d4e5")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), NAVY),
                  ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]
    t.setStyle(TableStyle(style))
    return t


def build(output: Path) -> None:
    doc = SimpleDocTemplate(str(output), pagesize=A4,
                            leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=2 * cm, bottomMargin=2 * cm,
                            title="云枢 T8 租户隔离修复结项报告",
                            author="云枢项目组")
    story = []

    # 封面标题
    story.append(Paragraph("云枢 T8 多租户开放 API", TITLE))
    story.append(Paragraph("租户数据隔离修复 — 结项汇报", TITLE))
    story.append(Paragraph("2026-08-16 · 项目结项汇报用", SUB))
    story.append(Spacer(1, 6))

    # 一、背景与风险
    story.append(Paragraph("一、背景与风险", H1))
    story.append(Paragraph(
        "T8 多租户开放 API 灰度开放 /api/audit/logs 审计日志查询端点后，故障演练剧本场景 B 实测确认："
        "该端点为全局 Append-only 日志，未按租户维度过滤，任意绑定租户的 API Key 可读取全部审计记录，"
        "定级为<b>中风险</b>（元数据级：trace_id / action / metadata，输入输出为 SHA-256 截断哈希）。", BODY))

    # 二、修复内容
    story.append(Paragraph("二、修复内容", H1))
    story.append(_table([
        ["层", "措施", "说明"],
        ["读侧隔离", "网关注入身份", "handle_request 认证后注入 request._gateway_key_info（tenant_id/role）"],
        ["读侧隔离", "AuditLogger.filter_by_key", "绑定租户仅见本租户；未绑定 Key 空集 + warning；内部通道全量"],
        ["写侧就绪", "log(tenant_id=...) 自动注入", "未显式传时从请求上下文推断，业务调用点 0 改动"],
        ["历史数据", "migrate_audit_logs_tenant.py", "228 条补全为 system，幂等 + 自动备份，--yes 支持 CI/容器"],
        ["部署集成", "docker_entrypoint.sh", "容器启动自动补全历史数据，隔离语义开箱生效"],
        ["可观测", "analyze_audit_logs.py + 告警", "SQL 分析租户分布/异常占比，超阈值邮件告警（SMTP 走 .env）"],
    ]))

    # 三、收益数据
    story.append(Paragraph("三、收益数据（修复记录数 / 风险量级 / 效率）", H1))
    story.append(_table([
        ["维度", "修复前", "修复后", "变化"],
        ["历史记录归属", "228 条无 tenant_id", "全部补全为 system", "228 条迁移（幂等+可回滚备份）"],
        ["跨租户可见记录", "233 条（100% 可见）", "0 条", "暴露面归零（绑定租户仅见本租户）"],
        ["隔离语义", "任意 Key 可读全量", "按租户收敛（97.85% 记录不可跨租户见）", "风险中 → 低"],
        ["业务接入成本", "—", "自动注入", "0 行业务调用点改动"],
        ["回归保障", "—", "RBAC 30 + 隔离 13 用例", "相关套件 101 passed / 0 failed"],
        ["故障定位", "凭经验排查", "日志定位四步法 + 模拟脚本", "401/403/429 可复现、可演练"],
    ]))
    story.append(Paragraph(
        "风险消除量级：修复前跨租户可读率为 100%（233/233），修复后绑定租户 Key 仅可读本租户记录，"
        "跨租户可见记录 233 → 0；审计字段（trace_id/action/metadata）不再跨租户泄露。", BODY))

    # 四、故障演练结果
    story.append(Paragraph("四、故障演练结果（2026-08-16 重跑）", H1))
    story.append(Paragraph("场景 A — 网关宕机", H2))
    story.append(Paragraph(
        "单进程架构：网关与内部 API 同进程，宕机即全挂。数据 RPO≈0（租户/Key/审计日志磁盘持久化），"
        "限流/配额内存态重启重置（符合预期）。恢复判定 4 项：探活 200 / 带 Key 开放端点 200 / "
        "租户与审计数据完整 / 限流桶重置 100.0。", BODY))
    story.append(Paragraph("场景 B — 租户数据隔离（含 401/403/429 验证）", H2))
    story.append(_table([
        ["场景", "触发", "实测结果", "日志定位"],
        ["S1 无 Key", "GET /api/audit/logs 无认证头", "401 Unauthorized", "四步法①-④"],
        ["S2 伪造 Key", "X-API-Key: ffff...（64 位）", "401 Unauthorized", "四步法①-④"],
        ["S3 scope 不足", "read Key 访问 write 端点", "403 Forbidden", "四步法①-④"],
        ["S4 接口限流", "15 连发", "第 10 次起 429 Rate limit exceeded", "四步法①-④"],
        ["S5 租户配额", "limit=1 连续 2 次", "第 2 次 429 Tenant quota exceeded", "四步法①-④"],
    ]))
    story.append(Paragraph(
        "日志定位四步法：① 响应体 error 字段 → ② /api/open/stats 网关统计 → ③ app_server "
        "结构化日志 access 条目 → ④ /api/audit/logs 审计记录（成功请求）。跨租户隔离正向验证："
        "租户 B owner Key 查询 count=1（仅本租户），跨租户记录 0。", BODY))

    # 五、测试与回归
    story.append(Paragraph("五、测试与回归", H1))
    story.append(_table([
        ["套件", "用例数", "结果"],
        ["test_audit_tenant_isolation.py（隔离+自动注入）", "13", "通过"],
        ["test_rbac_permissions_matrix.py（权限矩阵）", "30", "通过"],
        ["test_api_gateway_flask_t84.py（灰度开放）", "16", "通过"],
        ["test_api_gateway_tenant_quota.py / rbac / routes_tenants / multi_tenant", "42", "通过"],
        ["相关套件合计", "101", "101 passed / 0 failed"],
    ]))
    story.append(Paragraph(
        "SQL 分析实测（233 条）：租户分布 system 228（97.85%）、演练租户记录 5；"
        "状态 100% success（异常占比 0%，未触发告警阈值）；按日分布 07-05 → 08-16。", BODY))

    # 六、遗留与后续
    story.append(Paragraph("六、遗留与后续", H1))
    story.append(_table([
        ["项", "状态", "说明"],
        ["历史 system 记录回看", "已迁移", "绑定租户 Key 不可见，内部通道可读（保守隔离）"],
        ["业务侧审计写点统一接入 tenant_id", "参数就绪", "自动注入已生效，调用点按需接入"],
        ["Docker 完整构建验证", "待环境", "daemon 就绪后 docker compose up（镜像含 torch CPU 依赖）"],
        ["邮件告警实测", "逻辑就绪", "需 .env 配置 SMTP_HOST/TO 后联调"],
    ]))

    story.append(Spacer(1, 16))
    story.append(Paragraph(
        "结语：租户隔离修复完成闭环 —— 风险暴露（演练发现）→ 读侧隔离 + 写侧字段 + 历史迁移 + "
        "自动注入 + 容器集成 + 告警可观测，收益数据全量实测。", BOLD))

    doc.build(story)
    print(f"[OK] PDF 已生成: {output}（{_FONT_NAME} 字体）")


def main():
    ap = argparse.ArgumentParser(description="生成 T8 租户隔离修复结项报告 PDF")
    ap.add_argument("--output", type=Path,
                    default=ROOT / "docs" / "zh" / "云枢T8租户隔离修复结项报告_20260816.pdf")
    args = ap.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    build(args.output)


if __name__ == "__main__":
    main()
