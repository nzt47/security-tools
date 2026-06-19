#!/usr/bin/env python3
"""
人格系统交互测试脚本
模拟用户与云枢的对话，展示人格注入器如何根据对话动态调整人格状态
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from persona.persona_model_enhanced import PersonaModel
from persona.persona_injector import PersonaInjector
import json
from datetime import datetime


class PersonaInteractionSimulator:
    """人格交互模拟器"""

    def __init__(self):
        # 初始化人格模型和注入器
        self.persona_model = PersonaModel()
        self.injector = PersonaInjector(self.persona_model)
        self.conversation_history = []
        self.interaction_count = 0
        
    def log_state(self, title):
        """记录当前人格状态"""
        style = self.persona_model.get_expression_style()
        identity = self.persona_model.get_identity()
        
        print(f"\n{'='*60}")
        print(f"【{title}】")
        print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"交互次数: {self.persona_model.persona['evolution']['interactions']}")
        print("\n当前表达风格参数:")
        for key, value in style.items():
            print(f"  {key}: {value:.2f}")
        print(f"\n身份: {identity['identity']}")
        print(f"价值观: {', '.join(identity['values'])}")
        print(f"{'='*60}")

    def simulate_interaction(self, user_message, context=None):
        """模拟一次交互"""
        self.interaction_count += 1
        
        print(f"\n[{self.interaction_count}] 用户: {user_message}")
        
        # 检测是否需要拒绝任务
        should_refuse, reason = self.injector.should_refuse_task(user_message)
        if should_refuse:
            print(f"云枢: {reason}")
            return
            
        # 获取当前系统提示词
        system_prompt = self.injector.build_system_prompt(
            body_status="CPU: 正常, 内存: 75%",
            memory_context=context,
            additional_rules=["保持对话自然流畅"]
        )
        
        # 根据用户消息调整人格状态
        self.adjust_persona_based_on_message(user_message)
        
        # 生成响应
        response = self.generate_response(user_message, system_prompt)
        print(f"云枢: {response}")
        
        # 记录交互
        self.persona_model.record_interaction()
        self.conversation_history.append({
            "user": user_message,
            "response": response,
            "timestamp": datetime.now().isoformat()
        })
        
        return response

    def adjust_persona_based_on_message(self, message):
        """根据消息内容调整人格"""
        # 分析用户情绪
        positive_words = ["好", "棒", "谢谢", "感谢", "开心", "喜欢", "爱"]
        negative_words = ["不好", "坏", "讨厌", "烦", "生气", "难过", "伤心"]
        question_words = ["?", "？", "什么", "怎么", "如何", "为什么"]
        
        style = self.persona_model.get_expression_style()
        changes = {}
        
        # 如果用户表达积极情绪，增加情感参数
        if any(word in message for word in positive_words):
            changes['emotion'] = min(1.0, style['emotion'] + 0.1)
            changes['empathy'] = min(1.0, style['empathy'] + 0.05)
            print("  [人格调整] 检测到积极情绪，增加情感和同理心")
        
        # 如果用户表达消极情绪，增加同理心
        if any(word in message for word in negative_words):
            changes['empathy'] = min(1.0, style['empathy'] + 0.15)
            changes['tone'] = max(0.0, style['tone'] - 0.1)
            print("  [人格调整] 检测到消极情绪，增加同理心，降低语气活泼度")
        
        # 如果是问题，增加正式度
        if any(word in message for word in question_words):
            changes['tone'] = max(0.0, style['tone'] - 0.05)
            changes['conciseness'] = min(1.0, style['conciseness'] + 0.05)
            print("  [人格调整] 检测到问题，增加正式度和简洁度")
        
        # 如果消息较长，增加简洁度
        if len(message) > 50:
            changes['conciseness'] = min(1.0, style['conciseness'] + 0.05)
        
        # 如果使用表情符号，增加幽默度
        if any(char in message for char in ['😊', '😂', '😄', '😆', '😎']):
            changes['humor'] = min(1.0, style['humor'] + 0.1)
            changes['tone'] = min(1.0, style['tone'] + 0.1)
            print("  [人格调整] 检测到表情，增加幽默感和活泼度")
        
        if changes:
            self.persona_model.update_expression_style(**changes)

    def generate_response(self, message, system_prompt):
        """生成响应（模拟LLM响应）"""
        style = self.persona_model.get_expression_style()
        
        # 根据人格风格生成不同类型的响应
        responses = {
            "greeting": [
                "你好呀！我是云枢，很高兴认识你~",
                "你好！有什么我可以帮你的吗？",
                "嗨！今天过得怎么样？"
            ],
            "positive": [
                "太棒了！听到你这么说我很开心😊",
                "真好呀！继续保持这种状态~",
                "我也为你感到高兴！"
            ],
            "negative": [
                "我理解你的感受，有什么我可以帮你的吗？",
                "别难过，我在这里陪伴你。",
                "听起来确实让人困扰，我们一起想想办法吧。"
            ],
            "question": [
                "好的，让我来帮你分析一下。",
                "这是一个很好的问题！",
                "让我仔细思考一下..."
            ],
            "thank": [
                "不客气！能帮到你我很开心~",
                "不用谢！随时找我哦~",
                "很高兴能为你服务！"
            ],
            "default": [
                "我明白你的意思了。",
                "嗯，我理解。",
                "好的，我记住了。"
            ]
        }
        
        # 根据消息类型选择响应
        if message in ["你好", "嗨", "哈喽", "hi", "hello"]:
            idx = int(style['tone'] * len(responses['greeting']))
            return responses['greeting'][min(idx, len(responses['greeting'])-1)]
        
        if any(word in message for word in ["谢谢", "感谢", "辛苦了"]):
            idx = int(style['emotion'] * len(responses['thank']))
            return responses['thank'][min(idx, len(responses['thank'])-1)]
        
        if any(word in message for word in ["好", "棒", "开心"]):
            idx = int(style['emotion'] * len(responses['positive']))
            return responses['positive'][min(idx, len(responses['positive'])-1)]
        
        if any(word in message for word in ["不好", "烦", "难过"]):
            idx = int(style['empathy'] * len(responses['negative']))
            return responses['negative'][min(idx, len(responses['negative'])-1)]
        
        if any(word in message for word in ["?", "？", "什么", "怎么"]):
            idx = int(style['conciseness'] * len(responses['question']))
            return responses['question'][min(idx, len(responses['question'])-1)]
        
        return responses['default'][0]

    def run_demo(self):
        """运行完整的交互演示"""
        print("="*60)
        print("云枢人格系统交互演示")
        print("="*60)
        print("\n本演示展示人格注入器如何根据对话动态调整人格状态")
        print("每个交互后，系统会根据用户消息自动调整表达风格参数")
        print("="*60)
        
        # 初始状态
        self.log_state("初始状态")
        
        # 模拟对话流程
        conversations = [
            ("你好！", "首次问候"),
            ("今天心情特别好！😊", "表达积极情绪"),
            ("你能帮我解释一下什么是人工智能吗？", "提出问题"),
            ("谢谢你的解释，很清楚！", "表达感谢"),
            ("最近工作有点烦，压力很大...", "表达消极情绪"),
            ("你有什么建议吗？", "寻求建议"),
            ("好的，我试试！😄", "积极回应"),
        ]
        
        for message, context in conversations:
            self.simulate_interaction(message, context)
            self.log_state(f"交互 {self.interaction_count} 后状态")
        
        # 展示最终人格漂移报告
        print("\n" + "="*60)
        print("人格漂移分析报告")
        print("="*60)
        baseline = PersonaModel()  # 原始基准人格
        drift_report = self.persona_model.analyze_drift(baseline)
        print(json.dumps(drift_report, ensure_ascii=False, indent=2))
        
        # 保存对话记录
        with open("conversation_log.json", "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "total_interactions": self.interaction_count,
                "final_persona": self.persona_model.persona,
                "conversation_history": self.conversation_history
            }, f, ensure_ascii=False, indent=2)
        print("\n📝 对话记录已保存到 conversation_log.json")


if __name__ == "__main__":
    simulator = PersonaInteractionSimulator()
    simulator.run_demo()