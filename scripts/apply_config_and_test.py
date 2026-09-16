#!/usr/bin/env python3
"""
配置应用脚本 - 将默认配置应用到当前项目

【默认不可破坏（防误覆盖改造）】
data/tool_router_keywords.json 是运行时真实生效的关键词表（agent/tool_router.py 的
KEYWORDS_FILE 指向它），而模板 data/tool_router_default_config.json 长期失同步。
旧版本"无参数即整体覆盖"，会静默抹掉真实词表里的新增关键词（例如 grep/edit 的检索
编辑意图词会整批消失），故命令行接口改为：

  - 不带参数  → dry-run：只打印逐类别 diff（新增/丢失/总数变化），**绝不写文件**
  - --apply   → 写前自动备份到 .backups/，写入后继续原有的全量测试与边界分析
  - --force   → 允许"有损覆盖"（会删掉目标文件已有而模板缺失的关键词）
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 项目根目录与默认路径（模块级常量，便于测试 monkeypatch 到临时目录）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_PATH = os.path.join(
    PROJECT_ROOT, "data", "tool_router_default_config.json")
TARGET_CONFIG_PATH = os.path.join(
    PROJECT_ROOT, "data", "tool_router_keywords.json")
BACKUP_DIR = os.path.join(PROJECT_ROOT, ".backups")


def load_template_keywords(template_path=None):
    """读取模板文件的 keywords_config.keywords；失败时打印错误并返回 None"""
    template_path = template_path or DEFAULT_CONFIG_PATH
    
    if not os.path.isfile(template_path):
        print(f"❌ 模板文件不存在: {template_path}")
        return None
    
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            default_config = json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ 模板文件 JSON 解析失败: {template_path}\n   {e}")
        return None
    except OSError as e:
        print(f"❌ 模板文件读取失败: {template_path}\n   {e}")
        return None
    
    keywords = None
    keywords_config = default_config.get("keywords_config") if isinstance(default_config, dict) else None
    if isinstance(keywords_config, dict):
        keywords = keywords_config.get("keywords")
    if not isinstance(keywords, dict):
        print(f"❌ 模板文件结构异常（缺少 keywords_config.keywords）: {template_path}")
        return None
    return keywords


def load_target_keywords(target_path=None):
    """读取目标文件已有词表；文件不存在按空词表处理；JSON/结构损坏打印错误并返回 None"""
    target_path = target_path or TARGET_CONFIG_PATH
    
    if not os.path.isfile(target_path):
        print(f"ℹ️ 目标文件不存在（按空词表处理）: {target_path}")
        return {}
    
    try:
        with open(target_path, "r", encoding="utf-8") as f:
            target_config = json.load(f)
    except json.JSONDecodeError as e:
        # 【不易】目标文件损坏时无法判断会丢失什么，宁可不写
        print(f"❌ 目标文件 JSON 解析失败（拒绝写入）: {target_path}\n   {e}")
        return None
    except OSError as e:
        print(f"❌ 目标文件读取失败（拒绝写入）: {target_path}\n   {e}")
        return None
    
    keywords = target_config.get("keywords") if isinstance(target_config, dict) else None
    if not isinstance(keywords, dict):
        print(f"❌ 目标文件结构异常（缺少 keywords）: {target_path}")
        return None
    return keywords


def diff_keywords(template_keywords, target_keywords):
    """逐类别比对模板与目标词表，返回差异列表（纯计算，不触碰文件）"""
    categories = list(template_keywords) + [
        c for c in target_keywords if c not in template_keywords]
    changes = []
    for category in categories:
        template_list = template_keywords.get(category)
        target_list = target_keywords.get(category)
        changes.append({
            "category": category,
            "new_category": template_list is not None and target_list is None,
            "dropped_category": target_list is not None and template_list is None,
            "added": [k for k in (template_list or []) if k not in (target_list or [])],
            "removed": [k for k in (target_list or []) if k not in (template_list or [])],
            "old_total": len(target_list or []),
            "new_total": len(template_list or []),
        })
    return changes


def collect_lost_keywords(template_keywords, target_keywords):
    """汇总"目标文件已有、模板缺失"的关键词，返回 [(category, keyword), ...]"""
    lost = []
    for change in diff_keywords(template_keywords, target_keywords):
        lost.extend((change["category"], k) for k in change["removed"])
    return lost


def print_keyword_diff(template_keywords, target_keywords):
    """打印逐类别 diff：新增了哪些关键词、将丢失哪些关键词、总词数变化"""
    old_total = sum(len(v) for v in target_keywords.values())
    new_total = sum(len(v) for v in template_keywords.values())
    changes = diff_keywords(template_keywords, target_keywords)
    
    for change in changes:
        category = change["category"]
        if not change["added"] and not change["removed"]:
            print(f"  {category}: 无变化（{change['new_total']} 个关键词）")
            continue
        delta = change["new_total"] - change["old_total"]
        print(f"  {category}: {change['old_total']} → {change['new_total']} 个关键词（{delta:+d}）")
        if change["dropped_category"]:
            print(f"    ⚠️ 该类别不在模板中 → 整个类别及其 {change['old_total']} 个关键词都将丢失")
        if change["added"]:
            print(f"    + 新增 {len(change['added'])} 个: {'、'.join(change['added'])}")
        if change["removed"]:
            print(f"    - 将丢失 {len(change['removed'])} 个: {'、'.join(change['removed'])}")
    
    print(f"\n  总词数: {old_total} → {new_total}（{new_total - old_total:+d}）")
    return changes


def backup_target_config(target_path=None, backup_dir=None):
    """写前把目标文件备份到 .backups/（UTC 时间戳），返回备份路径
    
    目标文件不存在时返回 None（无需备份）；备份失败向上抛 OSError，由调用方中止写入。
    """
    target_path = target_path or TARGET_CONFIG_PATH
    backup_dir = backup_dir or BACKUP_DIR
    
    if not os.path.exists(target_path):
        return None
    
    os.makedirs(backup_dir, exist_ok=True)
    name = os.path.basename(target_path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"{name}.bak_{stamp}")
    seq = 1
    while os.path.exists(backup_path):  # 同一秒内重复运行：追加序号，避免备份互相覆盖
        seq += 1
        backup_path = os.path.join(backup_dir, f"{name}.bak_{stamp}_{seq}")
    shutil.copy2(target_path, backup_path)
    return backup_path


def apply_default_config(apply=False, force=False):
    """应用默认配置文件
    
    apply=False（默认）：dry-run，只打印逐类别 diff，绝不写文件
    apply=True：备份后写入目标文件；有损覆盖且未 force 时拒绝写入
    返回 True 表示流程成功（dry-run 完成 / 写入完成），False 表示失败（调用方据此非零退出）
    """
    print("=" * 80)
    print("应用默认配置文件" + ("" if apply else "（dry-run：只打印差异，不写文件）"))
    print("=" * 80)
    
    print(f"\n默认配置文件: {DEFAULT_CONFIG_PATH}")
    print(f"目标配置文件: {TARGET_CONFIG_PATH}")
    
    # 读取默认配置（模板缺失/解析失败/结构异常 → 不写文件，由调用方非零退出）
    template_keywords = load_template_keywords()
    if template_keywords is None:
        return False
    
    # 读取目标配置（用于 diff 与有损覆盖保护）
    target_keywords = load_target_keywords()
    if target_keywords is None:
        return False
    
    # 逐类别 diff：新增了哪些关键词、将丢失哪些关键词、总词数变化
    print("\n关键词差异（模板 → 目标）:")
    print_keyword_diff(template_keywords, target_keywords)
    
    lost = collect_lost_keywords(template_keywords, target_keywords)
    
    if not apply:
        print("\n（dry-run）未写入任何文件。确认差异无误后执行:"
              " python scripts/apply_config_and_test.py --apply")
        return True
    
    # 有损覆盖保护：目标已有而模板缺失的关键词一旦写入就会被静默删除
    if lost:
        if not force:
            print(f"\n❌ 拒绝写入：目标文件已有、但模板缺失的 {len(lost)} 个关键词会被删除：")
            for category, keyword in lost:
                print(f"    - [{category}] {keyword}")
            print("\n若确认要执行有损覆盖，请追加 --force:"
                  " python scripts/apply_config_and_test.py --apply --force")
            print("已中止写入，目标文件未发生任何改动。")
            return False
        print(f"\n⚠️ 警告：--force 已启用，将执行有损覆盖，以下 {len(lost)} 个关键词会丢失：")
        for category, keyword in lost:
            print(f"    - [{category}] {keyword}")
    
    # 写前备份（备份失败则中止写入，绝不破坏原文件）
    try:
        backup_path = backup_target_config()
    except OSError as e:
        print(f"\n❌ 备份失败，已中止写入: {e}")
        return False
    if backup_path:
        print(f"\n📦 已备份原文件: {backup_path}")
    
    # 提取关键词配置
    keywords_config = {
        "keywords": template_keywords
    }
    
    # 保存到目标配置文件
    try:
        target_dir = os.path.dirname(TARGET_CONFIG_PATH)
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)
        with open(TARGET_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(keywords_config, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"\n❌ 写入失败: {TARGET_CONFIG_PATH}\n   {e}")
        return False
    
    print("✅ 配置文件已应用")
    
    # 显示应用的配置摘要
    print("\n应用的配置摘要:")
    total_keywords = 0
    for category, keywords in keywords_config["keywords"].items():
        count = len(keywords)
        total_keywords += count
        print(f"  {category}: {count} 个关键词")
    print(f"\n  总计: {total_keywords} 个关键词")
    
    return True


def run_full_test():
    """运行全量测试"""
    print("\n" + "=" * 80)
    print("运行全量测试")
    print("=" * 80)
    
    # agent/tests 已归档至 docs/archive/agent_tests_20260810，包导入不可用，改文件加载
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "tool_router_tester",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "docs", "archive", "agent_tests_20260810", "test_tool_router.py"))
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    ToolRouterTester = _mod.ToolRouterTester
    
    tester = ToolRouterTester()
    results = tester.run_all_tests()
    
    # 打印详细报告
    print("\n测试报告:")
    print(tester.generate_report())
    
    return results["summary"]["success_rate"] == 100.0


def analyze_boundary_conditions():
    """分析边界条件"""
    print("\n" + "=" * 80)
    print("边界条件分析")
    print("=" * 80)
    
    from agent.tool_router import ALL_TOOLS_SET, TOOL_CATEGORIES, TOOL_ALIASES
    
    print("\n当前工具状态:")
    print(f"  工具总数: {len(ALL_TOOLS_SET)}")
    print(f"  类别总数: {len(TOOL_CATEGORIES)}")
    print(f"  别名规则数: {len(TOOL_ALIASES)}")
    
    # 分析潜在边界条件
    boundary_conditions = [
        {
            "name": "工具数量为0",
            "description": "当 ALL_TOOLS_SET 为空时的处理",
            "risk": "高",
            "status": "未测试",
        },
        {
            "name": "类别无工具",
            "description": "某个类别没有工具时的处理",
            "risk": "中",
            "status": "未测试",
        },
        {
            "name": "关键词为空",
            "description": "某个类别关键词为空时的处理",
            "risk": "中",
            "status": "已测试",
        },
        {
            "name": "工具动态添加",
            "description": "运行时动态添加工具的处理",
            "risk": "高",
            "status": "未测试",
        },
        {
            "name": "工具动态删除",
            "description": "运行时动态删除工具的处理",
            "risk": "高",
            "status": "未测试",
        },
        {
            "name": "优先级冲突",
            "description": "多个类别优先级相同的处理",
            "risk": "中",
            "status": "已测试",
        },
        {
            "name": "别名循环引用",
            "description": "别名形成循环的处理",
            "risk": "高",
            "status": "未测试",
        },
        {
            "name": "配置文件损坏",
            "description": "配置文件JSON格式错误的处理",
            "risk": "高",
            "status": "未测试",
        },
        {
            "name": "工具名称冲突",
            "description": "不同类别包含同名工具的处理",
            "risk": "中",
            "status": "已测试",
        },
        {
            "name": "极端关键词数量",
            "description": "单个类别包含大量关键词的性能影响",
            "risk": "中",
            "status": "已测试",
        },
    ]
    
    print("\n边界条件清单:")
    print("-" * 80)
    print(f"{'名称':<20} {'风险':<6} {'状态':<10} {'描述'}")
    print("-" * 80)
    
    for bc in boundary_conditions:
        print(f"{bc['name']:<20} {bc['risk']:<6} {bc['status']:<10} {bc['description']}")
    
    # 统计未测试项
    untested = [bc for bc in boundary_conditions if bc["status"] == "未测试"]
    print(f"\n未测试的边界条件: {len(untested)} 个")
    
    return boundary_conditions


def main(argv=None):
    """命令行入口：解析参数并执行，返回进程退出码（0 成功 / 1 失败）"""
    parser = argparse.ArgumentParser(
        description="应用默认配置到当前项目（默认 dry-run，只打印差异、不写文件）")
    parser.add_argument("--apply", action="store_true",
                        help="真正写入 data/tool_router_keywords.json（写前自动备份到 .backups/）")
    parser.add_argument("--force", action="store_true",
                        help="允许有损覆盖（会删掉目标文件已有而模板缺失的关键词）")
    args = parser.parse_args(argv)
    
    # 应用配置（dry-run 默认；失败一律非零退出且不写文件）
    if not apply_default_config(apply=args.apply, force=args.force):
        print("\n❌ 未应用配置，目标文件未发生任何改动")
        return 1
    
    # dry-run：只打印差异，不触碰文件，也不跑后续测试流程
    if not args.apply:
        if args.force:
            print("\nℹ️ --force 仅在 --apply 时生效（当前为 dry-run，不会写文件）")
        return 0
    
    # 运行全量测试
    success = run_full_test()
    
    # 分析边界条件
    analyze_boundary_conditions()
    
    print("\n" + "=" * 80)
    if success:
        print("🎉 配置应用成功，全量测试通过!")
    else:
        print("⚠️ 全量测试未通过")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())