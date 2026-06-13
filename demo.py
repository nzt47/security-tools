"""云枢 DEMO — 展示完整感知-认知-行动闭环"""
import sys, io, logging
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
logging.basicConfig(level=logging.WARNING)

from agent import DigitalLife

Yunshu = DigitalLife()
Yunshu.start()

DEMOS = [
    "你好，云枢！",
    "你怎么样？",
    "帮我检查身体",
    "help",
]

for question in DEMOS:
    print(f"\n你 > {question}")
    print(f"云枢 > {Yunshu.chat(question)}")

print("\n" + "=" * 50)
print("演示完成。完整交互请运行: python main.py")
print("=" * 50)

Yunshu.stop()
