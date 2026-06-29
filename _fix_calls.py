"""批量修正 file_store.py 中的异常调用方式"""
import re
import subprocess

p = "agent/skills_mgmt/file_store.py"
with open(p, "r", encoding="utf-8") as f:
    content = f.read()

# 模式: SkillValidationError(ErrorCode.XXX, "msg")  ->  SkillValidationError("msg", code=ErrorCode.XXX)
content = re.sub(
    r'SkillValidationError\((ErrorCode\.\w+),\s*(f?"[^"]*"|f?\'[^\']*\')\)',
    r'SkillValidationError(\2, code=\1)',
    content,
)

# 模式: SkillFileError(ErrorCode.XXX, "msg")  ->  SkillFileError("msg", code=ErrorCode.XXX)
content = re.sub(
    r'SkillFileError\((ErrorCode\.\w+),\s*(f?"[^"]*"|f?\'[^\']*\')\)',
    r'SkillFileError(\2, code=\1)',
    content,
)

with open(p, "w", encoding="utf-8") as f:
    f.write(content)

# 验证语法
r = subprocess.run(
    ["python", "-c", f"import ast; ast.parse(open('{p}', encoding='utf-8').read()); print('OK')"],
    capture_output=True, text=True,
)
print(r.stdout.strip())
if r.returncode != 0:
    print("ERROR:", r.stderr)
