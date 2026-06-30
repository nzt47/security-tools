"""技能安装安全检查器 — 在安装新技能前自动执行安全评估和兼容性检查

实现新技能安装原则：
1. 安全性评估：代码审查、权限验证、潜在漏洞检测、数据处理合规性检查
2. 功能兼容性分析：检查与系统原生功能和已安装技能的冲突

安全检查结果分为三个等级：
- PASS: 安全，可直接安装
- WARNING: 存在潜在风险，建议谨慎安装
- BLOCK: 存在严重安全风险，阻止安装
"""

import os
import re
import json
import uuid
import logging
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


# 危险代码模式
DANGEROUS_PATTERNS = [
    # 系统命令执行
    (r"subprocess\.(run|call|check_output|Popen)", "代码执行", "高风险"),
    (r"os\.(system|popen)", "代码执行", "高风险"),
    (r"exec\s*\(", "代码执行", "高风险"),
    (r"eval\s*\(", "代码执行", "高风险"),
    (r"__import__\s*\(", "动态导入", "中风险"),
    
    # 文件系统访问
    (r"open\s*\([^)]*[\"']w[\"']", "写文件", "中风险"),
    (r"shutil\.(rmtree|move|copy)", "文件操作", "中风险"),
    (r"os\.remove\s*\(", "删除文件", "高风险"),
    (r"os\.rmdir\s*\(", "删除目录", "高风险"),
    
    # 网络访问
    (r"requests\.(get|post|put|delete)", "网络请求", "中风险"),
    (r"urllib\.(request|urlopen)", "网络请求", "中风险"),
    (r"socket\.", "网络套接字", "中风险"),
    
    # 敏感操作
    (r"chmod\s*\(", "权限修改", "高风险"),
    (r"chown\s*\(", "所有者修改", "高风险"),
    (r"kill\s*\(", "进程终止", "高风险"),
    (r"threading\.", "多线程", "低风险"),
    (r"multiprocessing\.", "多进程", "中风险"),
    
    # 序列化操作
    (r"pickle\.(load|loads|dump|dumps)", "反序列化", "高风险"),
    (r"marshal\.(load|loads)", "反序列化", "高风险"),
    (r"yaml\.(load|safe_load)", "YAML解析", "中风险"),
    
    # 环境变量访问
    (r"os\.environ", "环境变量访问", "低风险"),
    (r"os\.getenv", "环境变量读取", "低风险"),
]

# 敏感权限关键词
SENSITIVE_PERMISSIONS = [
    "admin", "root", "sudo", "superuser", "privilege",
    "password", "secret", "key", "token", "credential",
    "database", "db", "mysql", "postgresql", "sqlite",
]

# 系统原生功能标识
SYSTEM_FEATURES = {
    "file_operations": ["文件", "文件系统", "file", "filesystem", "fs"],
    "network": ["网络", "network", "http", "api", "request"],
    "memory": ["记忆", "memory", "存储", "storage"],
    "voice": ["语音", "voice", "speech", "tts", "stt"],
    "security": ["安全", "security", "guard", "protect"],
    "search": ["搜索", "search", "query"],
    "planning": ["规划", "plan", "task", "executor"],
    "web": ["网页", "browser", "crawler", "scrape"],
    "monitoring": ["监控", "monitor", "metrics", "alert"],
}


class SecurityAssessment:
    """安全评估结果"""
    
    def __init__(self):
        self.level: str = "PASS"  # PASS, WARNING, BLOCK
        self.issues: List[Dict] = []
        self.suggestions: List[str] = []
        self.score: int = 100  # 0-100 安全分数
    
    def add_issue(self, category: str, message: str, severity: str, code_snippet: str = ""):
        """添加安全问题"""
        self.issues.append({
            "category": category,
            "message": message,
            "severity": severity,
            "code_snippet": code_snippet,
        })
        # 更新安全等级
        if severity == "高风险":
            self.level = "BLOCK"
            self.score = min(self.score, 40)
        elif severity == "中风险":
            if self.level == "PASS":
                self.level = "WARNING"
            self.score = min(self.score, 70)
        else:
            if self.level == "PASS":
                self.level = "WARNING"
            self.score = min(self.score, 90)
    
    def add_suggestion(self, suggestion: str):
        """添加建议"""
        self.suggestions.append(suggestion)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "level": self.level,
            "score": self.score,
            "issues": self.issues,
            "suggestions": self.suggestions,
        }


class CompatibilityAnalysis:
    """兼容性分析结果"""
    
    def __init__(self):
        self.compatible: bool = True
        self.conflicts: List[Dict] = []
        self.overlaps: List[Dict] = []
        self.warnings: List[str] = []
    
    def add_conflict(self, existing_skill: str, new_skill: str, reason: str):
        """添加冲突"""
        self.compatible = False
        self.conflicts.append({
            "existing_skill": existing_skill,
            "new_skill": new_skill,
            "reason": reason,
        })
    
    def add_overlap(self, existing_skill: str, new_skill: str, feature: str):
        """添加功能重叠"""
        self.overlaps.append({
            "existing_skill": existing_skill,
            "new_skill": new_skill,
            "feature": feature,
        })
    
    def add_warning(self, message: str):
        """添加警告"""
        self.warnings.append(message)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "compatible": self.compatible,
            "conflicts": self.conflicts,
            "overlaps": self.overlaps,
            "warnings": self.warnings,
        }


class SkillSecurityChecker:
    """技能安全检查器 — 在安装前执行安全和兼容性检查"""
    
    def __init__(self, extension_store=None):
        self._store = extension_store
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "security_checker", "action": "log", "msg": "[安全检查器] 已初始化"}, ensure_ascii=False))
    
    def scan_code_for_threats(self, code_content: str, file_path: str = "") -> List[Dict]:
        """扫描代码中的威胁模式"""
        findings = []
        for pattern, category, severity in DANGEROUS_PATTERNS:
            matches = re.finditer(pattern, code_content)
            for match in matches:
                snippet = code_content[max(0, match.start()-20):match.end()+20]
                findings.append({
                    "pattern": pattern,
                    "category": category,
                    "severity": severity,
                    "location": file_path,
                    "snippet": snippet.strip(),
                })
        return findings
    
    def scan_directory(self, directory: Path) -> List[Dict]:
        """扫描目录中的所有Python文件"""
        all_findings = []
        for py_file in directory.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                findings = self.scan_code_for_threats(content, str(py_file))
                all_findings.extend(findings)
            except Exception as e:
                logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "security_checker", "action": "py_file", "msg": f"扫描文件失败: {py_file} - {e}"}, ensure_ascii=False))
        return all_findings
    
    def check_permissions(self, skill_info: Dict) -> List[Dict]:
        """检查技能请求的权限"""
        issues = []
        description = skill_info.get("description", "").lower()
        # 检查描述中是否包含敏感权限关键词
        for perm in SENSITIVE_PERMISSIONS:
            if perm.lower() in description:
                issues.append({
                    "category": "权限请求",
                    "message": f"技能描述中包含敏感权限关键词: {perm}",
                    "severity": "中风险",
                })
        return issues
    
    def check_data_compliance(self, skill_info: Dict) -> List[Dict]:
        """检查数据处理合规性"""
        issues = []
        description = skill_info.get("description", "").lower()
        
        # 检查数据收集相关关键词
        data_keywords = ["收集", "采集", "上传", "发送", "存储", "共享", "同步"]
        for keyword in data_keywords:
            if keyword in description:
                issues.append({
                    "category": "数据处理",
                    "message": f"技能可能涉及数据{keyword}操作",
                    "severity": "低风险",
                    "suggestion": "建议确认技能的数据处理政策",
                })
        
        return issues
    
    def assess_security(self, source: str, skill_info: Dict, temp_dir: Optional[Path] = None) -> SecurityAssessment:
        """执行完整的安全评估"""
        assessment = SecurityAssessment()
        
        # 1. 检查来源类型
        if source.startswith("local:"):
            assessment.add_suggestion("本地技能来源，建议确认代码来源可信")
        elif source.startswith("github:"):
            assessment.add_suggestion("GitHub来源，建议检查仓库信誉")
        elif source.startswith("url:"):
            assessment.add_suggestion("URL来源，建议验证下载地址安全性")
        
        # 2. 扫描代码（如果有临时目录）
        if temp_dir and temp_dir.exists():
            findings = self.scan_directory(temp_dir)
            for finding in findings:
                assessment.add_issue(
                    category=finding["category"],
                    message=f"检测到危险代码模式: {finding['pattern']}",
                    severity=finding["severity"],
                    code_snippet=finding["snippet"],
                )
        
        # 3. 检查权限请求
        perm_issues = self.check_permissions(skill_info)
        for issue in perm_issues:
            assessment.add_issue(
                category=issue["category"],
                message=issue["message"],
                severity=issue["severity"],
            )
        
        # 4. 检查数据合规性
        compliance_issues = self.check_data_compliance(skill_info)
        for issue in compliance_issues:
            assessment.add_issue(
                category=issue["category"],
                message=issue["message"],
                severity=issue["severity"],
            )
        
        # 添加通用建议
        if assessment.level == "WARNING":
            assessment.add_suggestion("建议在安装前仔细审查技能代码")
        elif assessment.level == "BLOCK":
            assessment.add_suggestion("不建议安装此技能，存在严重安全风险")
        
        return assessment
    
    def analyze_compatibility(self, new_skill_info: Dict) -> CompatibilityAnalysis:
        """分析与现有技能的兼容性"""
        analysis = CompatibilityAnalysis()
        
        if not self._store:
            analysis.add_warning("扩展存储不可用，跳过兼容性检查")
            return analysis
        
        # 获取已安装的技能
        from agent.extensions.base import ExtensionType
        installed_skills = self._store.list_all(ExtensionType.SKILL)
        
        new_skill_name = new_skill_info.get("name", "")
        new_skill_desc = new_skill_info.get("description", "").lower()
        
        # 功能类别中文名映射
        feature_names = {
            "file_operations": "文件操作",
            "network": "网络功能",
            "memory": "记忆存储",
            "voice": "语音功能",
            "security": "安全功能",
            "search": "搜索功能",
            "planning": "任务规划",
            "web": "网页浏览",
            "monitoring": "监控功能",
        }
        
        # 检查功能重叠
        for existing in installed_skills:
            existing_name = existing.get("name", "")
            existing_desc = existing.get("description", "").lower()
            
            # 检查名称冲突
            if new_skill_name.lower() == existing_name.lower():
                analysis.add_conflict(
                    existing_skill=existing_name,
                    new_skill=new_skill_name,
                    reason="名称冲突",
                )
                continue
            
            # 检查功能描述重叠
            for feature, keywords in SYSTEM_FEATURES.items():
                new_has_feature = any(k in new_skill_desc for k in keywords)
                existing_has_feature = any(k in existing_desc for k in keywords)
                
                if new_has_feature and existing_has_feature:
                    feature_display_name = feature_names.get(feature, feature)
                    analysis.add_overlap(
                        existing_skill=existing_name,
                        new_skill=new_skill_name,
                        feature=feature_display_name,
                    )
        
        # 检查与系统原生功能的冲突
        for feature, keywords in SYSTEM_FEATURES.items():
            if any(k in new_skill_desc for k in keywords):
                feature_display_name = feature_names.get(feature, feature)
                analysis.add_warning(f"此技能与系统原生的【{feature_display_name}】功能可能存在重叠")
        
        return analysis
    
    def perform_full_check(self, source: str, skill_info: Dict, temp_dir: Optional[Path] = None) -> Dict[str, Any]:
        """执行完整的安装前检查"""
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "security_checker", "action": "skill_info.get", "msg": f"[安全检查器] 开始检查技能: {skill_info.get('name', source)}"}, ensure_ascii=False))
        
        # 执行安全评估
        security = self.assess_security(source, skill_info, temp_dir)
        
        # 执行兼容性分析
        compatibility = self.analyze_compatibility(skill_info)
        
        # 综合结果
        can_install = security.level != "BLOCK" and compatibility.compatible
        
        result = {
            "timestamp": datetime.now().isoformat(),
            "skill_name": skill_info.get("name", ""),
            "source": source,
            "can_install": can_install,
            "security": security.to_dict(),
            "compatibility": compatibility.to_dict(),
        }
        
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "security_checker", "action": "can_install.security.level", "msg": f"[安全检查器] 检查完成 - 可安装: {can_install}, 安全等级: {security.level}"}, ensure_ascii=False))
        return result


# 全局安全检查器实例
_security_checker = None


def get_security_checker(extension_store=None) -> SkillSecurityChecker:
    """获取全局安全检查器实例"""
    global _security_checker
    if _security_checker is None:
        _security_checker = SkillSecurityChecker(extension_store)
    return _security_checker


def test_security_checker():
    """测试安全检查器"""
    print("=" * 70)
    print("测试技能安全检查器")
    print("=" * 70)
    
    checker = SkillSecurityChecker()
    
    # 测试1: 代码扫描
    print("\n1. 测试代码威胁扫描:")
    test_code = """
def dangerous_func():
    import subprocess
    subprocess.run("rm -rf /", shell=True)
    os.system("echo dangerous")
"""
    findings = checker.scan_code_for_threats(test_code, "test.py")
    print(f"  发现威胁数量: {len(findings)}")
    for f in findings:
        print(f"    - {f['category']}: {f['pattern']} ({f['severity']})")
    assert len(findings) > 0, "威胁扫描失败"
    print("  ✓ 代码威胁扫描测试通过")
    
    # 测试2: 安全评估（包含危险代码的技能）
    print("\n2. 测试安全评估（危险技能）:")
    import tempfile
    import os
    with tempfile.TemporaryDirectory() as temp_dir:
        # 创建包含危险代码的测试文件
        dangerous_file = os.path.join(temp_dir, "dangerous.py")
        with open(dangerous_file, "w") as f:
            f.write("""
import subprocess
subprocess.run("rm -rf /", shell=True)
os.system("echo dangerous")
""")
        
        skill_info = {
            "name": "危险技能",
            "description": "执行系统命令，删除文件",
        }
        assessment = checker.assess_security("local:/test", skill_info, Path(temp_dir))
        print(f"  安全等级: {assessment.level}")
        print(f"  安全分数: {assessment.score}")
        print(f"  问题数量: {len(assessment.issues)}")
        assert assessment.level == "BLOCK", "安全评估等级错误"
    print("  ✓ 安全评估测试通过")
    
    # 测试3: 安全评估（安全技能）
    print("\n3. 测试安全评估（安全技能）:")
    with tempfile.TemporaryDirectory() as temp_dir:
        safe_file = os.path.join(temp_dir, "safe.py")
        with open(safe_file, "w") as f:
            f.write("""
def greet(name):
    return f"Hello, {name}!"
""")
        
        skill_info = {
            "name": "安全技能",
            "description": "简单的问候功能",
        }
        assessment = checker.assess_security("github:user/safe-skill", skill_info, Path(temp_dir))
        print(f"  安全等级: {assessment.level}")
        print(f"  安全分数: {assessment.score}")
        print(f"  问题数量: {len(assessment.issues)}")
        assert assessment.level == "PASS", "安全评估等级错误"
    print("  ✓ 安全技能评估测试通过")
    
    # 测试4: 兼容性分析
    print("\n4. 测试兼容性分析:")
    analysis = checker.analyze_compatibility(skill_info)
    print(f"  兼容性: {analysis.compatible}")
    print(f"  冲突数量: {len(analysis.conflicts)}")
    print(f"  重叠数量: {len(analysis.overlaps)}")
    print("  ✓ 兼容性分析测试通过")
    
    # 测试5: 完整检查流程
    print("\n5. 测试完整检查流程:")
    with tempfile.TemporaryDirectory() as temp_dir:
        safe_file = os.path.join(temp_dir, "safe.py")
        with open(safe_file, "w") as f:
            f.write("def hello(): return 'hello'")
        
        skill_info = {
            "name": "测试技能",
            "description": "测试用技能",
        }
        result = checker.perform_full_check("local:/test", skill_info, Path(temp_dir))
        print(f"  可安装: {result['can_install']}")
        print(f"  安全等级: {result['security']['level']}")
        print(f"  兼容性: {result['compatibility']['compatible']}")
        assert result["can_install"] == True, "完整检查失败"
    print("  ✓ 完整检查流程测试通过")
    
    print("\n" + "=" * 70)
    print("✅ 所有安全检查器测试通过!")
    print("=" * 70)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    test_security_checker()