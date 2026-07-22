"""生成 100 个 Git 分支状态模拟 JSON 数据

覆盖边界情况:
- 覆盖率: 0/null/负数/小数/100%
- 文件大小: 0/极小/正常/极大
- 时间: 远古/最近/未来(异常)
- 文件名: 中文/空格/超长/隐藏文件
- 功能说明: XSS 注入测试/空/超长/多行
- Git 阶段: 10 种阶段全覆盖
"""
import json
import hashlib
from datetime import datetime, timedelta

GIT_STATES = ['clean', 'modified', 'added', 'untracked', 'deleted', 'renamed']
GIT_STAGES = [
    ('untracked', '未跟踪'),
    ('staged_new', '已暂存(新增)'),
    ('modified', '已修改(未提交)'),
    ('committed', '已提交'),
    ('dev_branch', '开发分支(待合并)'),
    ('release', '预发布分支'),
    ('merged_main', '已合并至主干'),
    ('pending_review', '待审核'),
    ('merged_dev', '已合并至开发'),
    ('released_prod', '已发布至生产'),
]
FILE_TYPES = ['Python', 'JavaScript', 'TypeScript', 'HTML', 'CSS', 'JSON', 'YAML', 'Markdown', 'SQL', 'Shell']
MODULES = [
    'agent/orchestrator', 'agent/memory', 'agent/skills_mgmt', 'agent/tools', 'agent/cognitive',
    'agent/guardrails', 'agent/health', 'agent/network', 'lifetrace', 'planning', 'tests', 'scripts',
    'configs', 'static/js', 'static/css', 'templates', 'core', 'persona', 'mcp_services', 'docs',
]
BRANCHES = [
    'master', 'main', 'feature/auth-v2', 'feature/memory-opt', 'release/v2.1', 'release/v2.0-hotfix',
    'develop', 'dev/api-refactor', 'hotfix/urgent-fix', 'bugfix/login-crash', 'topic/experiment',
]
AUTHORS = ['nzt47', 'alice', 'bob', 'charlie', 'david', 'eve']
EXT_MAP = {
    'Python': '.py', 'JavaScript': '.js', 'TypeScript': '.ts', 'HTML': '.html',
    'CSS': '.css', 'JSON': '.json', 'YAML': '.yaml', 'Markdown': '.md', 'SQL': '.sql', 'Shell': '.sh',
}

# 边界覆盖率: 0% / null / 负数(异常) / 小数 / 100%
COVERAGES = [0, 0, 0.0, 0.5, 0.85, 1.0, 1.0, None, None, 0.333, 0.999, -0.1, 1.5]
# 边界大小: 0 / 极小 / 正常 / 极大
SIZES = [0, 1, 42, 1024, 51200, 1048576, 52428800, 1073741824]
# 边界时间偏移(天): 远古 / 近期 / 未来(异常)
TIME_OFFSETS = [-365, -180, -90, -30, -7, -1, 0, 1, 7]
# 边界文件名模式
NAME_PATTERNS = ['file_{idx:03d}{ext}', '测试文件_{idx}{ext}', 'file with spaces {idx}{ext}',
                 '{a50}_{idx}{ext}', '.hidden_{idx}']
# 边界路径模式
PATH_PATTERNS = ['{module}/{name}', '{module}/sub/deep/{name}', '{name}', '{module}/{idx:03d}/{name}']
# 边界功能说明(含 XSS / 空 / 超长 / 多行)
FUNCS = [
    '模块功能说明 #{idx}',
    'Contains <script>alert(1)</script> XSS test',
    'Normal description with quotes',
    '',
    'x' * 200,
    '多行\n说明\n第{idx}行',
]


def generate():
    files = []
    base_time = datetime(2026, 7, 15, 12, 0, 0)
    for i in range(100):
        idx = i + 1
        stage_key, stage_label = GIT_STAGES[i % len(GIT_STAGES)]
        state = GIT_STATES[i % len(GIT_STATES)]
        # 已提交阶段强制 clean 状态
        committed_stages = {'committed', 'dev_branch', 'release', 'merged_main',
                            'pending_review', 'merged_dev', 'released_prod'}
        if stage_key in committed_stages:
            state = 'clean'
        ftype = FILE_TYPES[i % len(FILE_TYPES)]
        module = MODULES[i % len(MODULES)]
        branch = BRANCHES[i % len(BRANCHES)]
        author = AUTHORS[i % len(AUTHORS)]
        cov = COVERAGES[i % len(COVERAGES)]
        size = SIZES[i % len(SIZES)]
        offset_days = TIME_OFFSETS[i % len(TIME_OFFSETS)]
        mtime = (base_time + timedelta(days=offset_days, hours=i % 24)).isoformat() + 'Z'
        atime = (base_time + timedelta(days=offset_days + 1)).isoformat() + 'Z'
        ctime = (base_time + timedelta(days=offset_days - 30)).isoformat() + 'Z'
        ext = EXT_MAP[ftype]
        name = NAME_PATTERNS[i % len(NAME_PATTERNS)].format(idx=idx, ext=ext, a50='a' * 50)
        path = PATH_PATTERNS[i % len(PATH_PATTERNS)].format(module=module, name=name, idx=idx)
        func = FUNCS[i % len(FUNCS)]
        if '{idx}' in func:
            func = func.format(idx=idx)
        last_commit = None
        if state != 'untracked':
            last_commit = {
                'hash': hashlib.md5(str(idx).encode()).hexdigest()[:8],
                'author': author,
                'date': (base_time + timedelta(days=-offset_days)).strftime('%Y-%m-%d'),
                'message': f'commit message for file {idx}',
            }
        files.append({
            'name': name, 'path': path, 'type': ftype, 'size': size,
            'module': module, 'function': func, 'coverage': cov,
            'git_state': state, 'git_stage': stage_key, 'git_stage_label': stage_label,
            'mtime': mtime, 'atime': atime, 'ctime': ctime,
            'branch': branch, 'last_commit': last_commit,
        })
    data = {
        'description': '100 个 Git 分支状态模拟数据，覆盖边界情况：0/null/负数覆盖率、0/超大文件大小、远古/未来时间、中文/空格/超长文件名、XSS 注入测试、空功能说明',
        'generated_at': datetime.now().isoformat(),
        'total': len(files),
        'stage_distribution': {s[0]: sum(1 for f in files if f['git_stage'] == s[0]) for s in GIT_STAGES},
        'state_distribution': {s: sum(1 for f in files if f['git_state'] == s) for s in GIT_STATES},
        'files': files,
    }
    with open('mock_git_states.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'已生成 mock_git_states.json: {len(files)} 条数据')
    print('阶段分布:', data['stage_distribution'])
    print('状态分布:', data['state_distribution'])


if __name__ == '__main__':
    generate()
