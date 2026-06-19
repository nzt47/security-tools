#!/usr/bin/env python3
"""
对话数据注入测试
将真实对话数据注入到人格系统，观察日志输出变化
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime

from persona.persona_model_enhanced import PersonaModel
from persona.persona_injector import PersonaInjector
from persona.distillation_enhanced import PersonalityPreferenceExtractor


class ConversationInjector:
    """对话数据注入器"""

    def __init__(self):
        self.persona_model = PersonaModel()
        self.injector = PersonaInjector(self.persona_model)
        self.extractor = PersonalityPreferenceExtractor()

    def load_conversation_log(self, file_path):
        """加载对话日志"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"加载对话日志失败: {e}")
            return None

    def inject_conversation(self, conversation_data):
        """注入对话数据到人格系统"""
        if not conversation_data:
            print("没有对话数据可注入")
            return

        print("\n" + "="*70)
        print("【对话数据注入测试】")
        print("时间: {}".format(datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        print("="*70)

        # 初始状态
        print("\n[初始状态]")
        self.print_current_state()

        # 提取对话历史
        history = conversation_data.get('conversation_history', [])
        
        for i, item in enumerate(history, 1):
            user_message = item.get('user', '')
            response = item.get('response', '')
            
            print(f"\n{'='*70}")
            print(f"【对话 #{i}】")
            print(f"用户: {user_message}")
            print(f"云枢: {response}")
            print("-"*70)

            # 使用偏好提取器分析用户消息
            conversation_item = [{"user": user_message, "assistant": response}]
            preferences = self.extractor.extract_from_conversation(conversation_item)
            print(f"提取的偏好:")
            print(f"  表达风格: {preferences.get('expression_style', {})}")
            print(f"  话题兴趣: {preferences.get('topic_interests', [])}")
            print(f"  情感倾向: {preferences.get('emotional_tendency', {})}")

            # 根据提取的偏好更新人格
            style = preferences.get('expression_style', {})
            if style:
                # 映射提取器的风格参数到人格模型
                persona_updates = {}
                if 'formality' in style:
                    # formality高表示正式，对应tone低
                    persona_updates['tone'] = 1.0 - style['formality']
                if 'emotional' in style:
                    persona_updates['emotion'] = style['emotional']
                if 'conciseness' in style:
                    persona_updates['conciseness'] = style['conciseness']
                
                if persona_updates:
                    print("\n[人格更新] 基于对话分析自动调整:")
                    self.persona_model.update_expression_style(**persona_updates)

            # 检测用户情绪并调整同理心
            self.adjust_based_on_emotion(user_message)

            # 记录交互
            self.persona_model.record_interaction()

            # 打印当前状态
            print("\n[更新后状态]")
            self.print_current_state()

        # 生成人格漂移报告
        print("\n" + "="*70)
        print("【人格漂移分析报告】")
        print("="*70)
        baseline = PersonaModel()
        drift_report = self.persona_model.analyze_drift(baseline)
        print(json.dumps(drift_report, ensure_ascii=False, indent=2))

    def adjust_based_on_emotion(self, message):
        """根据用户情绪调整人格"""
        positive_words = ["好", "棒", "谢谢", "感谢", "开心", "喜欢", "爱", "😊", "😄", "😆"]
        negative_words = ["不好", "坏", "讨厌", "烦", "生气", "难过", "伤心", "压力", "累"]
        
        style = self.persona_model.get_expression_style()
        changes = {}
        
        if any(word in message for word in positive_words):
            changes['emotion'] = min(1.0, style['emotion'] + 0.1)
            changes['empathy'] = min(1.0, style['empathy'] + 0.05)
            print(f"  [情绪检测] 积极情绪 detected -> 情感+0.1, 同理心+0.05")
        
        if any(word in message for word in negative_words):
            changes['empathy'] = min(1.0, style['empathy'] + 0.15)
            changes['tone'] = max(0.0, style['tone'] - 0.1)
            print(f"  [情绪检测] 消极情绪 detected -> 同理心+0.15, 语气-0.1")
        
        if changes:
            self.persona_model.update_expression_style(**changes)

    def print_current_state(self):
        """打印当前人格状态"""
        style = self.persona_model.get_expression_style()
        identity = self.persona_model.get_identity()
        
        print(f"  交互次数: {self.persona_model.persona['evolution']['interactions']}")
        print(f"  表达风格参数:")
        for key, value in style.items():
            print(f"    {key}: {value:.4f}")
        print(f"  身份: {identity['identity']}")
        print(f"  价值观: {', '.join(identity['values'])}")


if __name__ == "__main__":
    injector = ConversationInjector()
    
    # 加载之前保存的对话日志
    log_file = "conversation_log.json"
    
    if os.path.exists(log_file):
        print(f"正在加载对话日志: {log_file}")
        conversation_data = injector.load_conversation_log(log_file)
        
        if conversation_data:
            injector.inject_conversation(conversation_data)
    else:
        print(f"未找到对话日志文件: {log_file}")
        print("请先运行 tests/test_persona_interaction.py 生成对话日志")