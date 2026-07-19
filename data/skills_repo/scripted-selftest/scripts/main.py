"""scripted-selftest 技能的主脚本

读取 stdin JSON 参数，输出 JSON 结果到 stdout。
最后一行必须是 JSON，SkillExecutor 会自动解析。
"""
import sys
import json


def main():
    # 从 stdin 读取参数（SkillExecutor 通过 stdin 传递 JSON）
    raw = sys.stdin.read() or "{}"
    params = json.loads(raw)

    greeting = params.get("greeting", "hi")
    count = int(params.get("count", 1))

    # 输出 JSON 结果到 stdout 最后一行
    result = {
        "ok": True,
        "echo": greeting,
        "count": count,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
