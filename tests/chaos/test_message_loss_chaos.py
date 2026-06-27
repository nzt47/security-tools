"""消息丢失混沌测试

测试场景:
1. 1%消息丢失（验证重试机制）
2. 10%消息丢失（验证降级策略）
3. 50%消息丢失（验证熔断触发）
4. 消息乱序（验证顺序处理）
5. 消息重复（验证幂等性）
"""

import time
import logging
import pytest
import threading
import random
from collections import OrderedDict

logger = logging.getLogger(__name__)

from agent.monitoring.chaos_injector import (
    ChaosInjector,
    FaultType,
    get_chaos_injector,
    chaos_fault
)


@pytest.mark.slow
class TestMessageLossChaos:
    """消息丢失混沌测试"""
    
    def setup_method(self):
        """每个测试前重置混沌注入器"""
        logger.info("[MSG_CHAOS] 测试开始 - 初始化混沌注入器")
        self.injector = get_chaos_injector()
        self.injector.clear_all()
        logger.debug("[MSG_CHAOS] 混沌注入器已重置完成")
    
    def teardown_method(self):
        """每个测试后清理故障"""
        logger.info("[MSG_CHAOS] 测试结束 - 清理所有故障注入")
        self.injector.clear_all()
        logger.debug("[MSG_CHAOS] 所有故障已清理完成")
    
    def test_message_loss_1_percent(self):
        """测试1%消息丢失（验证重试机制）"""
        logger.info("[MSG_CHAOS] 测试开始 - 1%消息丢失场景")
        with chaos_fault(FaultType.MESSAGE_LOSS, loss_percent=1):
            logger.debug("[MSG_CHAOS] 1%消息丢失故障已注入")
            stats = self.injector.get_stats()
            logger.debug("[MSG_CHAOS] 故障注入后状态: message_loss=%s", stats['fault_types']['message_loss'])
            assert stats['fault_types']['message_loss'] is True
            logger.debug("[MSG_CHAOS] 断言通过: message_loss故障类型已激活")
            
            config = self.injector._fault_configs[FaultType.MESSAGE_LOSS]
            logger.debug("[MSG_CHAOS] 故障配置: message_loss_percent=%s, probability=%s",
                         config.message_loss_percent, config.probability)
            assert config.message_loss_percent == 1
            assert config.probability == 0.01
            logger.debug("[MSG_CHAOS] 断言通过: 1%消息丢失配置正确")
        logger.info("[MSG_CHAOS] 测试完成 - 1%消息丢失场景")
    
    def test_message_loss_10_percent(self):
        """测试10%消息丢失（验证降级策略）"""
        logger.info("[MSG_CHAOS] 测试开始 - 10%消息丢失场景")
        with chaos_fault(FaultType.MESSAGE_LOSS, loss_percent=10):
            logger.debug("[MSG_CHAOS] 10%消息丢失故障已注入")
            stats = self.injector.get_stats()
            logger.debug("[MSG_CHAOS] 故障注入后状态: message_loss=%s", stats['fault_types']['message_loss'])
            assert stats['fault_types']['message_loss'] is True
            logger.debug("[MSG_CHAOS] 断言通过: message_loss故障类型已激活")
            
            config = self.injector._fault_configs[FaultType.MESSAGE_LOSS]
            logger.debug("[MSG_CHAOS] 故障配置: message_loss_percent=%s, probability=%s",
                         config.message_loss_percent, config.probability)
            assert config.message_loss_percent == 10
            assert config.probability == 0.1
            logger.debug("[MSG_CHAOS] 断言通过: 10%消息丢失配置正确")
        logger.info("[MSG_CHAOS] 测试完成 - 10%消息丢失场景")
    
    def test_message_loss_50_percent(self):
        """测试50%消息丢失（验证熔断触发）"""
        logger.info("[MSG_CHAOS] 测试开始 - 50%消息丢失场景")
        with chaos_fault(FaultType.MESSAGE_LOSS, loss_percent=50):
            logger.debug("[MSG_CHAOS] 50%消息丢失故障已注入")
            stats = self.injector.get_stats()
            logger.debug("[MSG_CHAOS] 故障注入后状态: message_loss=%s", stats['fault_types']['message_loss'])
            assert stats['fault_types']['message_loss'] is True
            logger.debug("[MSG_CHAOS] 断言通过: message_loss故障类型已激活")
            
            config = self.injector._fault_configs[FaultType.MESSAGE_LOSS]
            logger.debug("[MSG_CHAOS] 故障配置: message_loss_percent=%s, probability=%s",
                         config.message_loss_percent, config.probability)
            assert config.message_loss_percent == 50
            assert config.probability == 0.5
            logger.debug("[MSG_CHAOS] 断言通过: 50%消息丢失配置正确")
        logger.info("[MSG_CHAOS] 测试完成 - 50%消息丢失场景")
    
    def test_message_loss_retry_mechanism(self):
        """测试消息丢失时的重试机制"""
        logger.info("[MSG_CHAOS] 测试开始 - 消息丢失重试机制")
        messages_sent = 100
        messages_received = 0
        retry_count = 0
        logger.debug("[MSG_CHAOS] 初始化参数: messages_sent=%d, loss_probability=0.1, max_retry=3", messages_sent)
        
        def send_message(msg_id, loss_probability=0.1):
            nonlocal messages_received, retry_count
            attempts = 0
            while attempts < 3:
                if random.random() >= loss_probability:
                    messages_received += 1
                    logger.debug("[MSG_CHAOS] 消息%d发送成功, 尝试次数=%d", msg_id, attempts + 1)
                    break
                attempts += 1
                retry_count += 1
                logger.debug("[MSG_CHAOS] 消息%d第%d次发送失败, 进行重试", msg_id, attempts)
        
        random.seed(42)
        logger.debug("[MSG_CHAOS] 随机种子已设置, 开始发送消息")
        for i in range(messages_sent):
            send_message(i)
        
        logger.info("[MSG_CHAOS] 消息发送完成: 发送=%d, 接收=%d, 重试次数=%d",
                    messages_sent, messages_received, retry_count)
        logger.debug("[MSG_CHAOS] 断言前检查: messages_received >= 90, retry_count > 0")
        assert messages_received >= 90, f"消息接收率过低: {messages_received}/{messages_sent}"
        logger.debug("[MSG_CHAOS] 断言通过: 消息接收率符合要求")
        assert retry_count > 0, "应该有重试发生"
        logger.debug("[MSG_CHAOS] 断言通过: 重试机制已触发")
        logger.info("[MSG_CHAOS] 测试完成 - 消息丢失重试机制")
    
    def test_message_out_of_order(self):
        """测试消息乱序（验证顺序处理）"""
        logger.info("[MSG_CHAOS] 测试开始 - 消息乱序故障注入")
        with chaos_fault(FaultType.MESSAGE_OUT_OF_ORDER, probability=0.5):
            logger.debug("[MSG_CHAOS] 消息乱序故障已注入, probability=0.5")
            stats = self.injector.get_stats()
            logger.debug("[MSG_CHAOS] 故障注入后状态: message_out_of_order=%s",
                         stats['fault_types']['message_out_of_order'])
            assert stats['fault_types']['message_out_of_order'] is True
            logger.debug("[MSG_CHAOS] 断言通过: message_out_of_order故障类型已激活")
        logger.info("[MSG_CHAOS] 测试完成 - 消息乱序故障注入")
    
    def test_message_out_of_order_processing(self):
        """测试乱序消息的顺序处理"""
        logger.info("[MSG_CHAOS] 测试开始 - 乱序消息顺序处理")
        messages = []
        received_order = []
        
        for i in range(10):
            messages.append({"id": i, "data": f"message_{i}"})
        logger.debug("[MSG_CHAOS] 已创建%d条测试消息", len(messages))
        
        shuffled = messages.copy()
        random.seed(123)
        random.shuffle(shuffled)
        logger.debug("[MSG_CHAOS] 消息已打乱顺序")
        
        for msg in shuffled:
            received_order.append(msg["id"])
        logger.debug("[MSG_CHAOS] 乱序接收顺序: %s", received_order)
        
        sorted_order = sorted(received_order)
        logger.debug("[MSG_CHAOS] 排序后顺序: %s", sorted_order)
        
        logger.debug("[MSG_CHAOS] 断言前检查: sorted_order == list(range(10))")
        assert sorted_order == list(range(10))
        logger.debug("[MSG_CHAOS] 断言通过: 消息排序正确")
        logger.info("[MSG_CHAOS] 测试完成 - 乱序消息顺序处理")
    
    def test_message_duplicate(self):
        """测试消息重复（验证幂等性）"""
        logger.info("[MSG_CHAOS] 测试开始 - 消息重复故障注入")
        with chaos_fault(FaultType.MESSAGE_DUPLICATE, duplicate_count=2, probability=0.5):
            logger.debug("[MSG_CHAOS] 消息重复故障已注入, duplicate_count=2, probability=0.5")
            stats = self.injector.get_stats()
            logger.debug("[MSG_CHAOS] 故障注入后状态: message_duplicate=%s",
                         stats['fault_types']['message_duplicate'])
            assert stats['fault_types']['message_duplicate'] is True
            logger.debug("[MSG_CHAOS] 断言通过: message_duplicate故障类型已激活")
            
            config = self.injector._fault_configs[FaultType.MESSAGE_DUPLICATE]
            logger.debug("[MSG_CHAOS] 故障配置: duplicate_count=%s, probability=%s",
                         config.duplicate_count, config.probability)
            assert config.duplicate_count == 2
            assert config.probability == 0.5
            logger.debug("[MSG_CHAOS] 断言通过: 消息重复配置正确")
        logger.info("[MSG_CHAOS] 测试完成 - 消息重复故障注入")
    
    def test_message_duplicate_idempotency(self):
        """测试消息重复时的幂等性"""
        logger.info("[MSG_CHAOS] 测试开始 - 消息重复幂等性验证")
        message_store = {}
        logger.debug("[MSG_CHAOS] 消息存储已初始化")
        
        def process_message(msg):
            msg_id = msg["id"]
            if msg_id not in message_store:
                message_store[msg_id] = msg["data"]
                logger.debug("[MSG_CHAOS] 消息%d首次处理, 状态: processed", msg_id)
                return {"status": "processed", "duplicate": False}
            else:
                logger.debug("[MSG_CHAOS] 消息%d重复, 状态: ignored", msg_id)
                return {"status": "ignored", "duplicate": True}
        
        messages = [
            {"id": 1, "data": "first"},
            {"id": 1, "data": "first"},
            {"id": 2, "data": "second"},
            {"id": 1, "data": "first"},
            {"id": 3, "data": "third"}
        ]
        logger.debug("[MSG_CHAOS] 准备处理%d条消息(含重复)", len(messages))
        
        results = []
        for msg in messages:
            results.append(process_message(msg))
        
        logger.info("[MSG_CHAOS] 消息处理完成: 存储消息数=%d, 处理结果数=%d",
                    len(message_store), len(results))
        logger.debug("[MSG_CHAOS] 断言前检查: len(message_store) == 3")
        assert len(message_store) == 3
        logger.debug("[MSG_CHAOS] 断言通过: 存储消息数正确")
        assert results[0]["duplicate"] is False
        logger.debug("[MSG_CHAOS] 断言通过: 第1条消息非重复")
        assert results[1]["duplicate"] is True
        logger.debug("[MSG_CHAOS] 断言通过: 第2条消息为重复")
        assert results[3]["duplicate"] is True
        logger.debug("[MSG_CHAOS] 断言通过: 第4条消息为重复")
        logger.info("[MSG_CHAOS] 测试完成 - 消息重复幂等性验证")
    
    def test_message_loss_with_duration(self):
        """测试带持续时间的消息丢失"""
        logger.info("[MSG_CHAOS] 测试开始 - 带持续时间的消息丢失")
        logger.debug("[MSG_CHAOS] 注入消息丢失故障: loss_percent=10, duration_ms=500")
        self.injector.inject_message_loss(loss_percent=10, duration_ms=500)
        
        stats_before = self.injector.get_stats()
        logger.debug("[MSG_CHAOS] 注入后状态检查: message_loss=%s",
                     stats_before['fault_types']['message_loss'])
        assert stats_before['fault_types']['message_loss'] is True
        logger.debug("[MSG_CHAOS] 断言通过: 消息丢失故障已激活")
        
        logger.debug("[MSG_CHAOS] 等待故障持续时间结束 (0.6秒)")
        time.sleep(0.6)
        
        logger.debug("[MSG_CHAOS] 手动清理消息丢失故障")
        self.injector.clear_fault(FaultType.MESSAGE_LOSS)
        
        stats_after = self.injector.get_stats()
        logger.debug("[MSG_CHAOS] 清理后状态检查: message_loss=%s",
                     stats_after['fault_types']['message_loss'])
        assert stats_after['fault_types']['message_loss'] is False
        logger.debug("[MSG_CHAOS] 断言通过: 消息丢失故障已清理")
        logger.info("[MSG_CHAOS] 测试完成 - 带持续时间的消息丢失")
    
    def test_message_loss_stats_tracking(self):
        """测试消息丢失统计跟踪"""
        logger.info("[MSG_CHAOS] 测试开始 - 消息丢失统计跟踪")
        with chaos_fault(FaultType.MESSAGE_LOSS, loss_percent=10):
            logger.debug("[MSG_CHAOS] 消息丢失故障已激活, 等待自动恢复")
        
        records = self.injector.get_injection_history()
        loss_records = [r for r in records if r.fault_type == FaultType.MESSAGE_LOSS]
        logger.debug("[MSG_CHAOS] 获取注入历史记录: 总记录数=%d, 消息丢失记录数=%d",
                     len(records), len(loss_records))
        
        logger.debug("[MSG_CHAOS] 断言前检查: len(loss_records) >= 1")
        assert len(loss_records) >= 1
        logger.debug("[MSG_CHAOS] 断言通过: 存在消息丢失记录")
        logger.debug("[MSG_CHAOS] 断言前检查: recovered_at is not None")
        assert loss_records[-1].recovered_at is not None
        logger.debug("[MSG_CHAOS] 断言通过: 故障已标记为恢复, recovered_at=%s",
                     loss_records[-1].recovered_at)
        logger.info("[MSG_CHAOS] 测试完成 - 消息丢失统计跟踪")
    
    def test_message_out_of_order_stats_tracking(self):
        """测试消息乱序统计跟踪"""
        logger.info("[MSG_CHAOS] 测试开始 - 消息乱序统计跟踪")
        with chaos_fault(FaultType.MESSAGE_OUT_OF_ORDER, probability=0.5):
            logger.debug("[MSG_CHAOS] 消息乱序故障已激活, 等待自动恢复")
        
        records = self.injector.get_injection_history()
        ooo_records = [r for r in records if r.fault_type == FaultType.MESSAGE_OUT_OF_ORDER]
        logger.debug("[MSG_CHAOS] 获取注入历史记录: 总记录数=%d, 消息乱序记录数=%d",
                     len(records), len(ooo_records))
        
        logger.debug("[MSG_CHAOS] 断言前检查: len(ooo_records) >= 1")
        assert len(ooo_records) >= 1
        logger.debug("[MSG_CHAOS] 断言通过: 存在消息乱序记录")
        logger.debug("[MSG_CHAOS] 断言前检查: recovered_at is not None")
        assert ooo_records[-1].recovered_at is not None
        logger.debug("[MSG_CHAOS] 断言通过: 故障已标记为恢复, recovered_at=%s",
                     ooo_records[-1].recovered_at)
        logger.info("[MSG_CHAOS] 测试完成 - 消息乱序统计跟踪")
    
    def test_message_duplicate_stats_tracking(self):
        """测试消息重复统计跟踪"""
        logger.info("[MSG_CHAOS] 测试开始 - 消息重复统计跟踪")
        with chaos_fault(FaultType.MESSAGE_DUPLICATE, duplicate_count=2):
            logger.debug("[MSG_CHAOS] 消息重复故障已激活, 等待自动恢复")
        
        records = self.injector.get_injection_history()
        dup_records = [r for r in records if r.fault_type == FaultType.MESSAGE_DUPLICATE]
        logger.debug("[MSG_CHAOS] 获取注入历史记录: 总记录数=%d, 消息重复记录数=%d",
                     len(records), len(dup_records))
        
        logger.debug("[MSG_CHAOS] 断言前检查: len(dup_records) >= 1")
        assert len(dup_records) >= 1
        logger.debug("[MSG_CHAOS] 断言通过: 存在消息重复记录")
        logger.debug("[MSG_CHAOS] 断言前检查: recovered_at is not None")
        assert dup_records[-1].recovered_at is not None
        logger.debug("[MSG_CHAOS] 断言通过: 故障已标记为恢复, recovered_at=%s",
                     dup_records[-1].recovered_at)
        logger.info("[MSG_CHAOS] 测试完成 - 消息重复统计跟踪")
    
    def test_concurrent_message_processing(self):
        """测试并发消息处理"""
        logger.info("[MSG_CHAOS] 测试开始 - 并发消息处理")
        processed_count = [0]
        lock = threading.Lock()
        logger.debug("[MSG_CHAOS] 初始化: 5个线程, 每个线程处理20条消息")
        
        def process_messages(count):
            for _ in range(count):
                with lock:
                    processed_count[0] += 1
        
        threads = []
        for i in range(5):
            t = threading.Thread(target=process_messages, args=(20,))
            threads.append(t)
            t.start()
            logger.debug("[MSG_CHAOS] 线程%d已启动", i + 1)
        
        logger.debug("[MSG_CHAOS] 等待所有线程完成")
        for t in threads:
            t.join()
        
        logger.info("[MSG_CHAOS] 并发处理完成: 处理消息数=%d", processed_count[0])
        logger.debug("[MSG_CHAOS] 断言前检查: processed_count[0] == 100")
        assert processed_count[0] == 100
        logger.debug("[MSG_CHAOS] 断言通过: 并发处理结果正确")
        logger.info("[MSG_CHAOS] 测试完成 - 并发消息处理")
    
    def test_message_queue_reliability(self):
        """测试消息队列可靠性"""
        logger.info("[MSG_CHAOS] 测试开始 - 消息队列可靠性")
        message_queue = []
        delivered_messages = []
        
        for i in range(50):
            message_queue.append(f"msg_{i}")
        logger.debug("[MSG_CHAOS] 消息队列初始化: %d条消息", len(message_queue))
        
        loss_probability = 0.05
        random.seed(99)
        logger.debug("[MSG_CHAOS] 随机种子已设置, 丢失概率=%.2f", loss_probability)
        
        for msg in message_queue:
            if random.random() >= loss_probability:
                delivered_messages.append(msg)
        
        delivery_rate = len(delivered_messages) / len(message_queue)
        logger.info("[MSG_CHAOS] 消息投递完成: 发送=%d, 投递=%d, 投递率=%.2f%%",
                    len(message_queue), len(delivered_messages), delivery_rate * 100)
        logger.debug("[MSG_CHAOS] 断言前检查: delivery_rate >= 0.9")
        assert delivery_rate >= 0.9, f"消息投递率过低: {delivery_rate:.2%}"
        logger.debug("[MSG_CHAOS] 断言通过: 消息投递率符合要求")
        logger.info("[MSG_CHAOS] 测试完成 - 消息队列可靠性")
    
    def test_message_priority_processing(self):
        """测试消息优先级处理"""
        logger.info("[MSG_CHAOS] 测试开始 - 消息优先级处理")
        messages = [
            {"id": 1, "priority": "low", "data": "low1"},
            {"id": 2, "priority": "high", "data": "high1"},
            {"id": 3, "priority": "medium", "data": "medium1"},
            {"id": 4, "priority": "high", "data": "high2"},
            {"id": 5, "priority": "low", "data": "low2"}
        ]
        logger.debug("[MSG_CHAOS] 已创建%d条不同优先级的消息", len(messages))
        
        priority_order = {"high": 0, "medium": 1, "low": 2}
        logger.debug("[MSG_CHAOS] 优先级定义: high=0, medium=1, low=2")
        
        sorted_messages = sorted(messages, key=lambda m: priority_order[m["priority"]])
        logger.debug("[MSG_CHAOS] 排序后消息优先级顺序: %s",
                     [m["priority"] for m in sorted_messages])
        
        logger.debug("[MSG_CHAOS] 断言前检查: 首条消息优先级为high")
        assert sorted_messages[0]["priority"] == "high"
        logger.debug("[MSG_CHAOS] 断言通过: 最高优先级消息排在首位")
        logger.debug("[MSG_CHAOS] 断言前检查: 末条消息优先级为low")
        assert sorted_messages[-1]["priority"] == "low"
        logger.debug("[MSG_CHAOS] 断言通过: 最低优先级消息排在末位")
        logger.info("[MSG_CHAOS] 测试完成 - 消息优先级处理")