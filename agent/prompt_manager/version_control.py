#!/usr/bin/env python3
"""
版本控制模块

提供版本管理、对比、回滚和影响分析功能
"""

import uuid
import difflib
import json
import logging
from enum import Enum
from typing import Optional, Dict, Any, List

from .storage import (
    PromptStorage, PromptRecord, VersionRecord, PromptType,
    get_prompt_storage
)

logger = logging.getLogger(__name__)


class VersionStatus(Enum):
    """版本状态"""
    DRAFT = "draft"           # 草稿
    TESTING = "testing"       # 测试中
    APPROVED = "approved"     # 已批准（可部署到生产环境）
    DEPRECATED = "deprecated" # 已弃用


class VersionManager:
    """版本管理器"""
    
    def __init__(self, storage: PromptStorage = None):
        self.storage = storage or get_prompt_storage()
    
    def create_version(self, prompt_id: str, change_log: str = "", author: str = "") -> VersionRecord:
        """创建新版本"""
        prompt = self.storage.get_prompt(prompt_id)
        if not prompt:
            raise ValueError(f"提示词不存在: {prompt_id}")
        
        # 获取当前版本号并生成新版本号
        versions = self.storage.get_versions_by_prompt(prompt_id)
        new_version = self._generate_next_version(versions)
        
        version_id = str(uuid.uuid4())
        
        record = VersionRecord(
            version_id=version_id,
            prompt_id=prompt_id,
            version_number=new_version,
            content=prompt.content,
            change_log=change_log,
            author=author,
            status="draft"
        )
        
        self.storage.save_version(record)
        
        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "prompt_manager",
            "action": "create_version",
            "prompt_id": prompt_id,
            "version": new_version,
            "duration_ms": 0,
            "level": "INFO"
        }))
        
        return record
    
    def _generate_next_version(self, versions: List[VersionRecord]) -> str:
        """生成下一个版本号"""
        if not versions:
            return "1.0.0"
        
        max_version = "0.0.0"
        for v in versions:
            if self._compare_versions(v.version_number, max_version) > 0:
                max_version = v.version_number
        
        parts = max_version.split('.')
        if len(parts) == 3:
            major, minor, patch = map(int, parts)
            return f"{major}.{minor}.{patch + 1}"
        return f"{max_version}.1"
    
    def _compare_versions(self, v1: str, v2: str) -> int:
        """比较版本号"""
        parts1 = list(map(int, v1.split('.')))
        parts2 = list(map(int, v2.split('.')))
        
        for p1, p2 in zip(parts1, parts2):
            if p1 > p2:
                return 1
            elif p1 < p2:
                return -1
        
        return len(parts1) - len(parts2)
    
    def get_version_history(self, prompt_id: str) -> List[VersionRecord]:
        """获取版本历史"""
        return self.storage.get_versions_by_prompt(prompt_id)
    
    def rollback_to_version(self, prompt_id: str, version_number: str) -> bool:
        """回滚到指定版本"""
        versions = self.storage.get_versions_by_prompt(prompt_id)
        target_version = None
        
        for v in versions:
            if v.version_number == version_number:
                target_version = v
                break
        
        if not target_version:
            raise ValueError(f"版本不存在: {version_number}")
        
        # 更新提示词内容为目标版本
        prompt = self.storage.get_prompt(prompt_id)
        if prompt:
            prompt.content = target_version.content
            prompt.updated_at = _now()
            self.storage.save_prompt(prompt)
            
            # 创建新版本记录这次回滚
            self.create_version(
                prompt_id,
                change_log=f"回滚到版本 {version_number}",
                author="system"
            )
            
            logger.info(json.dumps({
                "trace_id": "",
                "module_name": "prompt_manager",
                "action": "rollback",
                "prompt_id": prompt_id,
                "target_version": version_number,
                "duration_ms": 0,
                "level": "INFO"
            }))
            
            return True
        
        return False
    
    def compare_versions(self, prompt_id: str, version1: str, version2: str) -> Dict[str, Any]:
        """对比两个版本"""
        versions = self.storage.get_versions_by_prompt(prompt_id)
        
        v1 = next((v for v in versions if v.version_number == version1), None)
        v2 = next((v for v in versions if v.version_number == version2), None)
        
        if not v1:
            raise ValueError(f"版本不存在: {version1}")
        if not v2:
            raise ValueError(f"版本不存在: {version2}")
        
        # 生成差异
        diff_lines = list(difflib.unified_diff(
            v1.content.splitlines(),
            v2.content.splitlines(),
            fromfile=f"{version1}",
            tofile=f"{version2}",
            lineterm=''
        ))
        
        # 统计差异
        added = sum(1 for line in diff_lines if line.startswith('+') and not line.startswith('+++'))
        removed = sum(1 for line in diff_lines if line.startswith('-') and not line.startswith('---'))
        modified = added + removed
        
        return {
            "prompt_id": prompt_id,
            "version1": version1,
            "version2": version2,
            "diff": '\n'.join(diff_lines),
            "added_lines": added,
            "removed_lines": removed,
            "modified_lines": modified,
            "summary": self._generate_diff_summary(diff_lines)
        }
    
    def _generate_diff_summary(self, diff_lines: List[str]) -> str:
        """生成差异摘要"""
        sections = []
        for line in diff_lines:
            if line.startswith('@@'):
                sections.append(line)
        
        if sections:
            return f"修改了 {len(sections)} 个代码块"
        return "无实质性变化"
    
    def analyze_impact(self, prompt_id: str, version_id: str = None) -> Dict[str, Any]:
        """分析版本影响"""
        if version_id:
            version = self.storage.get_version(version_id)
            if not version:
                raise ValueError(f"版本不存在: {version_id}")
            content = version.content
        else:
            prompt = self.storage.get_prompt(prompt_id)
            if not prompt:
                raise ValueError(f"提示词不存在: {prompt_id}")
            content = prompt.content
        
        # 分析内容特征
        lines = content.split('\n')
        word_count = len(content.split())
        char_count = len(content)
        
        # 识别潜在风险模式
        risks = []
        if word_count > 5000:
            risks.append("提示词过长，可能影响性能")
        if 'api_key' in content.lower() or 'secret' in content.lower():
            risks.append("检测到敏感信息关键词")
        if content.count('{') != content.count('}'):
            risks.append("括号不匹配")
        
        # 分析结构特征
        has_variables = '{{' in content or '{' in content and '}' in content
        has_functions = any('def ' in line for line in lines)
        has_json = 'json' in content.lower()
        
        return {
            "prompt_id": prompt_id,
            "version_id": version_id,
            "content_analysis": {
                "lines": len(lines),
                "words": word_count,
                "characters": char_count
            },
            "structure": {
                "has_variables": has_variables,
                "has_functions": has_functions,
                "has_json": has_json
            },
            "risks": risks,
            "suggestions": self._generate_suggestions(risks)
        }
    
    def _generate_suggestions(self, risks: List[str]) -> List[str]:
        """根据风险生成建议"""
        suggestions = []
        
        if "提示词过长" in ' '.join(risks):
            suggestions.append("考虑拆分提示词或使用模板引用")
        
        if "敏感信息" in ' '.join(risks):
            suggestions.append("检查是否包含敏感信息，建议使用环境变量或配置")
        
        if "括号不匹配" in ' '.join(risks):
            suggestions.append("检查括号配对是否正确")
        
        return suggestions
    
    def run_regression_test(self, version_id: str, test_cases: List[Dict]) -> Dict[str, Any]:
        """运行回归测试"""
        version = self.storage.get_version(version_id)
        if not version:
            raise ValueError(f"版本不存在: {version_id}")
        
        results = {
            "version_id": version_id,
            "prompt_id": version.prompt_id,
            "tests": [],
            "passed": 0,
            "failed": 0,
            "status": "pending"
        }
        
        for test_case in test_cases:
            test_result = self._run_single_test(test_case, version.content)
            results["tests"].append(test_result)
            if test_result["passed"]:
                results["passed"] += 1
            else:
                results["failed"] += 1
        
        results["status"] = "passed" if results["failed"] == 0 else "failed"
        
        # 更新版本测试结果
        self.storage.update_version_test_results(version_id, results)
        
        return results
    
    def _run_single_test(self, test_case: Dict, prompt_content: str) -> Dict[str, Any]:
        """运行单个测试用例"""
        test_id = test_case.get('id', 'unknown')
        description = test_case.get('description', '')
        expected_patterns = test_case.get('expected_patterns', [])
        forbidden_patterns = test_case.get('forbidden_patterns', [])
        
        passed = True
        errors = []
        
        # 检查期望模式
        for pattern in expected_patterns:
            if pattern not in prompt_content:
                passed = False
                errors.append(f"缺少期望模式: {pattern}")
        
        # 检查禁止模式
        for pattern in forbidden_patterns:
            if pattern in prompt_content:
                passed = False
                errors.append(f"包含禁止模式: {pattern}")
        
        return {
            "test_id": test_id,
            "description": description,
            "passed": passed,
            "errors": errors
        }
    
    def get_latest_approved_version(self, prompt_id: str) -> Optional[VersionRecord]:
        """获取最新批准版本"""
        versions = self.storage.get_versions_by_prompt(prompt_id)
        for v in versions:
            if v.status == "approved":
                return v
        return None


def _now():
    """获取当前时间戳"""
    import time
    return time.time()


# 全局版本管理器实例
_global_version_manager = None

def get_version_manager() -> VersionManager:
    """获取全局版本管理器实例"""
    global _global_version_manager
    if _global_version_manager is None:
        _global_version_manager = VersionManager()
    return _global_version_manager