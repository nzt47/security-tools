#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GlitchTip 错误上报链路验证脚本

前置条件：
1. 已启动 GlitchTip Docker 环境：
   cd docker/glitchtip && docker compose up -d
2. 在 GlitchTip Web 界面创建项目和组织
3. 从项目设置中获取 DSN

使用方式：
   方式 1（设置环境变量）：
   set SENTRY_DSN=https://<key>@localhost:8000/1
   set SENTRY_ENVIRONMENT=dev
   python docker/glitchtip/verify_error_reporting.py

   方式 2（直接传入 DSN）：
   python docker/glitchtip/verify_error_reporting.py --dsn https://<key>@localhost:8000/1

验证步骤：
1. 脚本初始化 Sentry SDK（连接 GlitchTip）
2. 上报一条测试错误（带 trace_id 和敏感上下文）
3. 上报一条测试消息
4. 在 GlitchTip Web 界面 http://localhost:8000 查看是否收到事件
5. 检查事件中敏感字段（password/api_key）是否被过滤为 [REDACTED]
6. 检查事件 tags 中是否包含 trace_id

可观测性：
- 所有步骤输出结构化 JSON 日志（trace_id/module_name/action/duration_ms）
- 验证结果包含 success/failure 标签
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# 将项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.error_reporting_config import (
    capture_error,
    capture_message,
    get_config,
    init_sentry,
    is_sentry_enabled,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _log_struct(action: str, duration_ms: float = 0, **kwargs):
    """输出结构化 JSON 日志（满足可观测性约束）"""
    entry = {
        "trace_id": kwargs.get("trace_id", ""),
        "module_name": "glitchtip_verify",
        "action": action,
        "duration_ms": round(duration_ms, 2),
    }
    entry.update({k: v for k, v in kwargs.items() if k != "trace_id"})
    print(json.dumps(entry, ensure_ascii=False))


def verify_error_reporting(dsn: str = None, environment: str = "dev"):
    """验证错误上报完整链路

    状态同步机制说明：
    - 使用 sentry_sdk.push_scope 确保每次上报的 scope 独立，避免上下文污染
    - capture_error 内部通过 _sentry_before_send 过滤敏感字段，保护隐私
    - trace_id 注入到 Sentry tags，实现 OpenTelemetry ↔ Sentry 链路关联
    """
    trace_id = f"verify-{int(time.time())}"
    start_total = time.time()

    # ─── 步骤 1：设置环境变量 ───────────────────────────
    if dsn:
        os.environ["SENTRY_DSN"] = dsn
    os.environ["SENTRY_ENVIRONMENT"] = environment
    os.environ.setdefault("SENTRY_SAMPLE_RATE", "1.0")

    _log_struct("set_env", trace_id=trace_id, dsn=os.environ.get("SENTRY_DSN", "")[:30] + "...")

    # ─── 步骤 2：获取配置 ────────────────────────────────
    cfg = get_config()
    sentry_cfg = cfg["sentry"]
    if not sentry_cfg["enabled"]:
        _log_struct("sentry_disabled", trace_id=trace_id, result="skip",
                     reason="SENTRY_DSN 未配置")
        print("\n[ERROR] Sentry 未启用，请设置 SENTRY_DSN 环境变量")
        print("  示例：set SENTRY_DSN=https://<公钥>@<host>/<项目ID>")
        return False

    _log_struct("config_loaded", trace_id=trace_id,
                 enabled=sentry_cfg["enabled"],
                 environment=sentry_cfg["environment"],
                 sample_rate=sentry_cfg["sample_rate"])

    # ─── 步骤 3：初始化 Sentry SDK ───────────────────────
    init_start = time.time()
    ok = init_sentry(force=True)
    init_ms = (time.time() - init_start) * 1000

    if not ok:
        _log_struct("init_failed", init_ms, trace_id=trace_id, result="failure")
        print("\n[ERROR] Sentry SDK 初始化失败")
        print("  可能原因：")
        print("  1. sentry-sdk 未安装：pip install sentry-sdk")
        print("  2. DSN 格式非法：必须以 http:// 或 https:// 开头")
        print("  3. GlitchTip 服务未启动：docker compose up -d")
        return False

    _log_struct("init_success", init_ms, trace_id=trace_id, result="success")

    # ─── 步骤 4：上报测试错误 ────────────────────────────
    # 构造包含敏感信息的上下文，验证 before_send 过滤钩子
    test_context = {
        "user_action": "提交订单",
        "password": "should_be_redacted",    # 应被过滤
        "api_key": "sk-should_be_redacted", # 应被过滤
        "order_id": "ORD-2026-001",         # 正常字段保留
        "amount": 99.50,
    }

    err_start = time.time()
    error_event_id = capture_error(
        error=RuntimeError("GlitchTip 链路验证：模拟业务异常"),
        level="error",
        context=test_context,
        trace_id=trace_id,
        user_id="test-user-001",
    )
    err_ms = (time.time() - err_start) * 1000

    if error_event_id:
        _log_struct("error_reported", err_ms, trace_id=trace_id,
                     event_id=error_event_id, result="success")
    else:
        _log_struct("error_report_failed", err_ms, trace_id=trace_id, result="failure")
        print("\n[ERROR] 错误上报失败，事件 ID 为空")

    # ─── 步骤 5：上报测试消息 ────────────────────────────
    msg_start = time.time()
    msg_event_id = capture_message(
        message="GlitchTip 链路验证：测试消息上报",
        level="info",
        context={"verify_type": "glitchtip_chain", "trace_id": trace_id},
        trace_id=trace_id,
    )
    msg_ms = (time.time() - msg_start) * 1000

    if msg_event_id:
        _log_struct("message_reported", msg_ms, trace_id=trace_id,
                     event_id=msg_event_id, result="success")
    else:
        _log_struct("message_report_failed", msg_ms, trace_id=trace_id, result="failure")

    # ─── 步骤 6：输出验证结果 ────────────────────────────
    total_ms = (time.time() - start_total) * 1000
    _log_struct("verify_complete", total_ms, trace_id=trace_id,
                 error_event_id=error_event_id,
                 message_event_id=msg_event_id,
                 sentry_enabled=is_sentry_enabled())

    print("\n" + "=" * 60)
    print("GlitchTip 错误上报链路验证结果")
    print("=" * 60)
    print(f"  Sentry 初始化:  {'PASS' if is_sentry_enabled() else 'FAIL'}")
    print(f"  错误事件 ID:     {error_event_id or '(失败)'}")
    print(f"  消息事件 ID:     {msg_event_id or '(失败)'}")
    print(f"  trace_id:        {trace_id}")
    print(f"  验证耗时:        {total_ms:.0f}ms")
    print("=" * 60)

    if error_event_id and msg_event_id:
        print("\n  [PASS] 链路验证通过！请检查 GlitchTip 界面：")
        print(f"    http://localhost:8000")
        print(f"    1. 在 Issues 中查找 trace_id={trace_id}")
        print(f"    2. 确认 password/api_key 字段显示为 [REDACTED]")
        print(f"    3. 确认 tags 中包含 trace_id={trace_id}")
        return True
    else:
        print("\n  [FAIL] 部分上报失败，请检查：")
        print(f"    1. GlitchTip Docker 是否正常运行：docker compose ps")
        print(f"    2. DSN 是否正确（在 GlitchTip 项目设置中获取）")
        print(f"    3. 网络是否可达：curl {os.environ.get('SENTRY_DSN', '').split('@')[-1]}/api/0/")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GlitchTip 错误上报链路验证")
    parser.add_argument("--dsn", help="Sentry/GlitchTip DSN（也可通过 SENTRY_DSN 环境变量设置）")
    parser.add_argument("--env", default="dev", help="环境名（dev/staging/production，默认 dev）")
    args = parser.parse_args()

    success = verify_error_reporting(dsn=args.dsn, environment=args.env)
    sys.exit(0 if success else 1)
