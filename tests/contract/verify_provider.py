#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Provider 端契约验证脚本

【用途】
在 CI 中启动 Flask 服务后，运行此脚本验证所有契约。
也可本地运行：python tests/contract/verify_provider.py --base-url http://localhost:5678

【可观测性约束】
- 结构化日志：trace_id / module_name / action / duration_ms
- 边界显性化：契约违反输出明确错误码
- 健康检查：输出各契约验证状态汇总
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import traceback
from pathlib import Path

# 加入项目路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests" / "contract"))

from contract_framework import (
    ProviderVerifier,
    VerificationResult,
    save_contract,
    save_verification_report,
    _trace_id,
)
from contract_definitions import get_all_contracts

logger = logging.getLogger("contract_verify")


def _setup_logging(verbose: bool = False) -> None:
    """配置日志"""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format='%(asctime)s [%(levelname)8s] %(name)-30s: %(message)s',
        datefmt='%H:%M:%S',
    )


def verify_all_contracts(base_url: str, contracts_dir: Path, verification_dir: Path) -> int:
    """验证所有契约，返回退出码：0 全部通过 / 1 存在失败 / 2 异常"""
    trace_id = _trace_id()
    start = time.time()

    logger.info(json.dumps({
        "trace_id": trace_id,
        "module_name": "contract_verify",
        "action": "verify_all.start",
        "base_url": base_url,
        "timestamp": __import__("datetime").datetime.now().isoformat(),
    }, ensure_ascii=False))

    try:
        contracts = get_all_contracts()
    except Exception as e:
        logger.error(f"加载契约定义失败: {e}", exc_info=True)
        return 2

    verifier = ProviderVerifier(base_url, timeout=15)
    all_results: list = []
    overall_passed = True

    # 1. 保存契约文件
    for contract in contracts:
        try:
            save_contract(contract, contracts_dir)
        except Exception as e:
            logger.error(f"保存契约 {contract.name} 失败: {e}")

    # 2. 逐个验证
    for contract in contracts:
        logger.info(f"=== 验证契约: {contract.name} ({len(contract.interactions)} 个交互) ===")
        try:
            results = verifier.verify_contract(contract)
            all_results.extend(results)

            # 保存验证报告
            report_path = save_verification_report(
                contract.name, results, verification_dir
            )

            passed_count = sum(1 for r in results if r.passed)
            failed_count = len(results) - passed_count
            contract_passed = failed_count == 0
            overall_passed = overall_passed and contract_passed

            status_icon = "✅" if contract_passed else "❌"
            logger.info(
                f"{status_icon} 契约 {contract.name}: {passed_count} 通过 / {failed_count} 失败 "
                f"(报告: {report_path.name})"
            )

            # 输出失败详情
            for r in results:
                if not r.passed:
                    logger.warning(f"  ❌ {r.interaction_description}: {r.error}")

        except Exception as e:
            logger.error(f"验证契约 {contract.name} 异常: {e}", exc_info=True)
            overall_passed = False

    duration_ms = (time.time() - start) * 1000
    total_passed = sum(1 for r in all_results if r.passed)
    total_failed = len(all_results) - total_passed

    logger.info(json.dumps({
        "trace_id": trace_id,
        "module_name": "contract_verify",
        "action": "verify_all.complete",
        "duration_ms": round(duration_ms, 2),
        "total_contracts": len(contracts),
        "total_interactions": len(all_results),
        "passed": total_passed,
        "failed": total_failed,
        "overall_passed": overall_passed,
    }, ensure_ascii=False))

    # 输出汇总
    print("\n" + "=" * 60)
    print("📊 契约验证汇总")
    print("=" * 60)
    print(f"  契约数: {len(contracts)}")
    print(f"  交互数: {len(all_results)}")
    print(f"  通过: {total_passed} ✅")
    print(f"  失败: {total_failed} {'❌' if total_failed else ''}")
    print(f"  总体: {'✅ 全部通过' if overall_passed else '❌ 存在失败'}")
    print(f"  耗时: {duration_ms:.2f} ms")
    print("=" * 60)

    return 0 if overall_passed else 1


def main(argv=None) -> int:
    """CLI 入口"""
    parser = argparse.ArgumentParser(
        description="Provider 端契约验证：启动服务后验证所有 API 契约"
    )
    parser.add_argument(
        "--base-url", "-u",
        default="http://localhost:5678",
        help="Provider 服务地址（默认 http://localhost:5678）",
    )
    parser.add_argument(
        "--contracts-dir",
        default=str(PROJECT_ROOT / "tests" / "contract" / "contracts"),
        help="契约文件输出目录",
    )
    parser.add_argument(
        "--verification-dir",
        default=str(PROJECT_ROOT / "docs" / "observability" / "contract_verification"),
        help="验证报告输出目录",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)

    contracts_dir = Path(args.contracts_dir)
    verification_dir = Path(args.verification_dir)

    try:
        return verify_all_contracts(args.base_url, contracts_dir, verification_dir)
    except KeyboardInterrupt:
        logger.warning("用户中断")
        return 130
    except Exception as e:
        logger.error(f"验证异常: {e}", exc_info=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
