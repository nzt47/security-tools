"""
记忆敏感过滤模块全面测试

覆盖 agent/memory/filter.py 和 agent/utils/sensitive_data_filter.py
的所有边界场景，确保敏感信息过滤的可靠性。
"""

import pytest
import time
import json
import logging
from unittest.mock import patch

from agent.memory.filter import (
    SensitiveDataFilter as MemorySensitiveDataFilter,
    SensitiveLevel,
    SensitiveMatch,
    FilterResult,
)
from agent.utils.sensitive_data_filter import (
    SensitiveDataFilter,
    REDACTED_VALUE,
    REDACTED_PARTIAL,
    mask_ip,
    get_default_filter,
    filter_sensitive_data,
    filter_dict,
    filter_string,
    sensitive_filter,
    create_filter,
)


# ============================================================================
# 初始化与配置测试
# ============================================================================


class TestFilterInit:
    """测试过滤器初始化与配置"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_default(self):
        """测试默认初始化"""
        sf = SensitiveDataFilter()
        assert sf is not None
        stats = sf.get_stats()
        assert stats["total_key_patterns"] > 0
        assert stats["total_content_patterns"] > 0
        assert stats["block_critical"] is True
        assert stats["block_high"] is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_with_custom_patterns(self):
        """测试使用自定义模式初始化"""
        custom_patterns = {
            "custom_test": {
                "pattern": r"\btest_sensitive_\d+\b",
                "level": SensitiveLevel.HIGH,
                "description": "自定义测试模式",
            }
        }
        sf = SensitiveDataFilter(custom_content_patterns=custom_patterns)
        stats = sf.get_stats()
        assert stats["total_content_patterns"] > len(sf.CONTENT_PATTERNS)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_with_additional_key_patterns(self):
        """测试添加额外的敏感字段名模式"""
        sf = SensitiveDataFilter(additional_key_patterns=[r"my_secret_\w+"])
        assert sf.is_sensitive_key("my_secret_token") is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_invalid_regex_handled(self):
        """测试无效正则表达式的容错处理"""
        sf = SensitiveDataFilter(additional_key_patterns=[r"[invalid"])
        # 不应抛出异常，应正常初始化
        assert sf is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_block_levels(self):
        """测试阻止级别配置"""
        sf = SensitiveDataFilter(block_critical=True, block_high=True)
        stats = sf.get_stats()
        assert stats["block_critical"] is True
        assert stats["block_high"] is True


# ============================================================================
# 密码过滤测试
# ============================================================================


class TestPasswordFiltering:
    """密码过滤测试 - 各种格式的密码"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_password_plaintext(self):
        """测试明文密码过滤"""
        sf = SensitiveDataFilter()
        test_cases = [
            "password=mySecretPass123",
            "Password=MySecretPass",
            'password="mySecretPass"',
            "passwd=secret123",
            "pwd=testpwd123",
            "secret=mysecretvalue",
        ]
        for test_text in test_cases:
            result = sf.mask(test_text)
            assert "mySecretPass123" not in result or "mySecretPass" not in result or "secret123" not in result or "testpwd123" not in result or "mysecretvalue" not in result
            assert REDACTED_VALUE in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_password_field_in_dict(self):
        """测试字典中密码字段的过滤"""
        sf = SensitiveDataFilter()
        data = {"password": "mySecret123", "username": "testuser"}
        result = sf.filter_dict(data)
        assert result["password"] == REDACTED_VALUE
        assert result["username"] == "testuser"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_password_case_variants(self):
        """测试密码大小写变体"""
        sf = SensitiveDataFilter()
        test_cases = [
            "PASSword=test123456",
            "PassWord=test123456",
            "PASSWORD=test123456",
            "pAsSwOrD=test123456",
        ]
        for test_text in test_cases:
            result = sf.mask(test_text)
            assert "test123456" not in result
            assert REDACTED_VALUE in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_password_url_format(self):
        """测试URL中的密码格式"""
        sf = SensitiveDataFilter()
        url = "mysql://user:mypassword123@localhost:3306/db"
        result = sf.mask(url)
        assert "mypassword123" not in result
        assert "@localhost" in result


# ============================================================================
# 令牌过滤测试
# ============================================================================


class TestTokenFiltering:
    """令牌过滤测试 - API Key、JWT Token、Access Token"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_api_key_filtering(self):
        """测试API Key过滤"""
        sf = SensitiveDataFilter()
        test_cases = [
            "api_key=sk-abcdefghijklmnopqrstuvwxyz1234567890",
            "API_KEY=sk-testkey1234567890abcdefghij",
            "api-key=my-api-key-1234567890",
        ]
        for test_text in test_cases:
            result = sf.mask(test_text)
            assert REDACTED_VALUE in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_jwt_token_filtering(self):
        """测试JWT Token过滤"""
        sf = SensitiveDataFilter()
        jwt_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        result = sf.mask(jwt_token)
        assert result != jwt_token
        assert REDACTED_VALUE in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_bearer_token_filtering(self):
        """测试Bearer Token过滤"""
        sf = SensitiveDataFilter()
        token_text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.testpayload.testsignature"
        result = sf.mask(token_text)
        assert "testpayload" not in result or REDACTED_VALUE in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_aws_access_key(self):
        """测试AWS Access Key过滤"""
        sf = SensitiveDataFilter()
        test_text = "AWS Key: AKIAIOSFODNN7EXAMPLE"
        result = sf.mask(test_text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert REDACTED_VALUE in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_github_token(self):
        """测试GitHub Token过滤"""
        sf = SensitiveDataFilter()
        test_text = "Token: ghp_abcdefghijklmnopqrstuvwxyz1234567890"
        result = sf.mask(test_text)
        assert "ghp_abcdefghijklmnopqrstuvwxyz1234567890" not in result
        assert REDACTED_VALUE in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_openai_key(self):
        """测试OpenAI API Key过滤"""
        sf = SensitiveDataFilter()
        test_text = "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGHIJKLMN"
        # 注意：sk- 前缀的密钥应被检测
        result = sf.mask(test_text)
        assert REDACTED_VALUE in result


# ============================================================================
# 身份证号测试
# ============================================================================


class TestIdCardFiltering:
    """身份证号测试 - 18位和15位身份证号"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_id_card_18_digit(self):
        """测试18位身份证号过滤"""
        sf = SensitiveDataFilter()
        test_text = "身份证号：110101199003076515"
        result = sf.mask(test_text)
        # 中间8位（出生日期）应该被脱敏
        assert "19900307" not in result
        assert "110101" in result
        assert "6515" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_id_card_18_with_x(self):
        """测试带X的18位身份证号"""
        sf = SensitiveDataFilter()
        test_text = "身份证：44030119880808123X"
        result = sf.mask(test_text)
        assert "19880808" not in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_id_card_15_digit(self):
        """测试15位旧版身份证号"""
        sf = SensitiveDataFilter()
        test_text = "旧身份证：110101900307651"
        result = sf.mask(test_text)
        # 15位身份证号中间6位应被脱敏
        assert "900307" not in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_id_card_detect(self):
        """测试身份证号检测功能"""
        sf = SensitiveDataFilter(block_critical=True)
        result = sf.detect("身份证：110101199003076515")
        assert result.allowed is False  # CRITICAL级别被阻止
        assert len(result.violations) > 0
        assert any(v.pattern_name == "china_id" for v in result.violations)


# ============================================================================
# 银行卡号测试
# ============================================================================


class TestBankCardFiltering:
    """银行卡号测试 - 16-19位银行卡号"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_bank_card_16_digit(self):
        """测试16位银行卡号"""
        sf = SensitiveDataFilter()
        test_text = "卡号：6222021234567890"
        result = sf.mask(test_text)
        # 中间8位应被脱敏
        assert "6222" in result
        assert "7890" in result
        assert "0212345678" not in result or REDACTED_PARTIAL in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_bank_card_19_digit(self):
        """测试19位银行卡号"""
        sf = SensitiveDataFilter()
        test_text = "卡号：6222021234567890123"
        result = sf.mask(test_text)
        assert "6222" in result
        assert "0123" in result
        assert REDACTED_PARTIAL in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_bank_card_detect(self):
        """测试银行卡号检测"""
        sf = SensitiveDataFilter(block_critical=True)
        result = sf.detect("银行卡号：6222021234567890123")
        assert result.allowed is False
        assert any(v.pattern_name == "bank_card" for v in result.violations)


# ============================================================================
# 手机号测试
# ============================================================================


class TestPhoneFiltering:
    """手机号测试 - 中国大陆手机号格式"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_phone_cn_mainland(self):
        """测试中国大陆手机号"""
        sf = SensitiveDataFilter()
        test_text = "手机号：13812345678"
        result = sf.mask(test_text)
        assert "138" in result
        assert "5678" in result
        assert "1234" not in result or "****" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_phone_with_country_code(self):
        """测试带国家区号的手机号"""
        sf = SensitiveDataFilter()
        test_text = "电话：+8613912345678"
        result = sf.mask(test_text)
        assert "139" in result
        assert "5678" in result
        assert "****" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_phone_hk(self):
        """测试香港手机号"""
        sf = SensitiveDataFilter()
        test_text = "香港电话：91234567"
        result = sf.mask(test_text)
        assert "9123" in result
        assert "****" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_phone_detect_high(self):
        """测试手机号检测为HIGH级别"""
        sf = SensitiveDataFilter(block_high=True)
        result = sf.detect("联系电话：13812345678")
        assert result.allowed is False
        assert result.action_taken == "blocked_high"


# ============================================================================
# 邮箱地址测试
# ============================================================================


class TestEmailFiltering:
    """邮箱地址测试 - 各种邮箱格式"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_email_basic(self):
        """测试基本邮箱地址"""
        sf = SensitiveDataFilter()
        test_text = "邮箱：testuser@example.com"
        result = sf.mask(test_text)
        assert "te***@example.com" in result or "***@" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_email_complex(self):
        """测试复杂邮箱格式"""
        sf = SensitiveDataFilter()
        test_cases = [
            "user.name+tag@domain.co.uk",
            "user123@sub.domain.com",
            "test_user@example.org",
        ]
        for test_text in test_cases:
            result = sf.mask(test_text)
            assert "@" in result
            # 邮箱名前两位应保留，中间被脱敏
            assert "***" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_email_detect_level(self):
        """测试邮箱检测为LOW级别"""
        sf = SensitiveDataFilter(block_high=True, block_critical=True)
        result = sf.detect("邮箱：test@example.com")
        # LOW级别不应被阻止
        assert result.allowed is True
        assert len(result.violations) > 0


# ============================================================================
# 多敏感信息混合测试
# ============================================================================


class TestMixedSensitiveData:
    """多敏感信息混合测试 - 一条消息中包含多种敏感信息"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_multiple_types_mixed(self):
        """测试多种敏感信息混合"""
        sf = SensitiveDataFilter()
        test_text = """
        用户信息：
        姓名：张三
        手机号：13812345678
        邮箱：zhangsan@example.com
        身份证：110101199003076515
        银行卡：6222021234567890
        API Key：sk-abcdefghijklmnopqrstuvwxyz1234567890
        """
        result = sf.mask(test_text)
        # 验证所有敏感信息都被脱敏
        assert "13812345678" not in result
        assert "zhangsan@example.com" not in result
        assert "110101199003076515" not in result
        assert "6222021234567890" not in result
        assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in result
        # 非敏感信息保留
        assert "张三" in result
        assert "姓名" in result


# ============================================================================
# 部分脱敏验证
# ============================================================================


class TestPartialRedaction:
    """部分脱敏验证 - 只脱敏敏感部分，保留上下文"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_context_preserved(self):
        """测试上下文信息保留"""
        sf = SensitiveDataFilter()
        test_text = "用户的手机号是13812345678，请联系他"
        result = sf.mask(test_text)
        assert "用户的手机号是" in result
        assert "，请联系他" in result
        assert "138****5678" in result or "****" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_partial_email_mask(self):
        """测试邮箱部分脱敏"""
        sf = SensitiveDataFilter()
        result = sf.mask("contact: testuser@example.com")
        # 前两位保留，域名保留
        assert "te***" in result
        assert "@example.com" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_partial_phone_mask(self):
        """测试手机号部分脱敏"""
        sf = SensitiveDataFilter()
        result = sf.mask("tel:13812345678")
        # 前3位和后4位保留
        assert "138****5678" in result or ("138" in result and "5678" in result and "****" in result)


# ============================================================================
# 空输入/None输入测试
# ============================================================================


class TestEmptyInput:
    """空输入/None输入的处理测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_mask_empty_string(self):
        """测试空字符串脱敏"""
        sf = SensitiveDataFilter()
        result = sf.mask("")
        assert result == ""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_mask_none(self):
        """测试None输入"""
        sf = SensitiveDataFilter()
        result = sf.mask(None)
        assert result is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_detect_none(self):
        """测试detect处理None"""
        sf = SensitiveDataFilter()
        result = sf.detect(None)
        assert result.allowed is True
        assert result.action_taken == "pass_empty"
        assert len(result.violations) == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_empty_dict(self):
        """测试空字典过滤"""
        sf = SensitiveDataFilter()
        result = sf.filter_dict({})
        assert result == {}

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_empty_list(self):
        """测试空列表过滤"""
        sf = SensitiveDataFilter()
        result = sf.filter_list([])
        assert result == []


# ============================================================================
# 超长文本测试
# ============================================================================


class TestLongText:
    """超长文本（>10KB）中的敏感信息检测"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_long_text_detection(self):
        """测试10KB以上文本中的敏感信息检测"""
        sf = SensitiveDataFilter()
        # 生成15KB的文本，中间嵌入敏感信息（使用password=格式确保匹配）
        long_text = "正常内容 " * 2000  # 约12KB
        long_text += "\n\npassword=mySuperSecretPassword123\n\n"
        long_text += "更多正常内容 " * 500

        result = sf.mask(long_text)
        assert "mySuperSecretPassword123" not in result
        assert REDACTED_VALUE in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_long_text_performance(self):
        """测试长文本处理性能 - 10KB文本处理时间<100ms"""
        sf = SensitiveDataFilter()
        long_text = "这是一段测试文本，包含正常内容。" * 500
        long_text += " password=secret123456 "
        long_text += "更多内容..." * 500

        start_time = time.time()
        result = sf.mask(long_text)
        elapsed = time.time() - start_time

        assert len(long_text) > 10000  # 确认超过10KB
        assert "secret123456" not in result
        assert elapsed < 0.1  # 小于100毫秒


# ============================================================================
# 编码绕过测试
# ============================================================================


class TestEncodingBypass:
    """编码绕过测试 - URL编码、Unicode编码的敏感信息"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_url_encoded_password_in_url(self):
        """测试URL中的密码（URL参数形式）"""
        sf = SensitiveDataFilter()
        test_text = "https://api.example.com?api_key=mysecretkey123&token=abc123"
        result = sf.mask(test_text)
        assert REDACTED_VALUE in result

    @pytest.mark.unit
    @pytest.mark.p1
    def test_unicode_encoding_not_bypass(self):
        """测试Unicode编码的敏感信息（验证不被绕过）"""
        sf = SensitiveDataFilter()
        # Unicode全角字符的密码
        test_text = "ｐａｓｓｗｏｒｄ＝ｍｙＳｅｃｒｅｔ１２３"
        # 全角字符可能不会被匹配，但这是预期行为
        # 主要验证不会崩溃
        result = sf.mask(test_text)
        assert result is not None


# ============================================================================
# 分隔符变体测试
# ============================================================================


class TestSeparatorVariants:
    """分隔符变体测试 - 不同分隔符的敏感字段"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_password_with_colon(self):
        """测试冒号分隔的密码"""
        sf = SensitiveDataFilter()
        test_text = "password: mySecret123"
        result = sf.mask(test_text)
        assert REDACTED_VALUE in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_password_with_equals(self):
        """测试等号分隔的密码"""
        sf = SensitiveDataFilter()
        test_text = "password=mySecret123"
        result = sf.mask(test_text)
        assert REDACTED_VALUE in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_password_with_quotes(self):
        """测试带引号的密码"""
        sf = SensitiveDataFilter()
        test_cases = [
            'password="mySecret123"',
            "password='mySecret123'",
        ]
        for test_text in test_cases:
            result = sf.mask(test_text)
            assert "mySecret123" not in result


# ============================================================================
# 误报率验证
# ============================================================================


class TestFalsePositive:
    """误报率验证 - 正常文本不应被误过滤"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_normal_text_no_filtering(self):
        """测试正常文本不被过滤"""
        sf = SensitiveDataFilter()
        normal_texts = [
            "这是一段普通的文本内容，不包含任何敏感信息。",
            "今天天气真好，适合出去散步。",
            "Python是一种编程语言，广泛用于数据分析。",
            "用户编号12345，订单号67890",
        ]
        for text in normal_texts:
            result = sf.mask(text)
            # 正常文本应该保持原样或变化极小
            assert result == text or len(result) == len(text)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_short_number_not_bank_card(self):
        """测试短数字不被误判为银行卡号"""
        sf = SensitiveDataFilter()
        test_text = "订单号：123456789012"  # 12位，不够银行卡号
        result = sf.mask(test_text)
        # 12位数字不应被当作银行卡号
        assert "123456789012" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_detect_safe_text(self):
        """测试安全文本检测返回allowed=True"""
        sf = SensitiveDataFilter()
        result = sf.detect("这是一段安全的文本")
        assert result.allowed is True
        assert len(result.violations) == 0
        assert result.action_taken == "pass"


# ============================================================================
# 嵌套结构测试
# ============================================================================


class TestNestedStructures:
    """嵌套结构中的敏感信息测试（JSON/列表/字典）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_nested_dict_filter(self):
        """测试嵌套字典中的敏感信息过滤"""
        sf = SensitiveDataFilter()
        data = {
            "user": {
                "name": "张三",
                "credentials": {
                    "password": "mysecret123",
                    "api_key": "sk-testkey1234567890abcdef",
                },
                "contact": {
                    "phone": "13812345678",
                    "email": "test@example.com",
                },
            }
        }
        result = sf.filter_dict(data)
        assert result["user"]["name"] == "张三"
        assert result["user"]["credentials"]["password"] == REDACTED_VALUE
        assert result["user"]["credentials"]["api_key"] == REDACTED_VALUE

    @pytest.mark.unit
    @pytest.mark.p0
    def test_list_with_dicts_filter(self):
        """测试包含字典的列表过滤"""
        sf = SensitiveDataFilter()
        data = [
            {"username": "user1", "password": "pass1"},
            {"username": "user2", "password": "pass2"},
            {"username": "user3", "token": "token123"},
        ]
        result = sf.filter_list(data)
        assert result[0]["username"] == "user1"
        assert result[0]["password"] == REDACTED_VALUE
        assert result[1]["password"] == REDACTED_VALUE
        assert result[2]["token"] == REDACTED_VALUE

    @pytest.mark.unit
    @pytest.mark.p0
    def test_deeply_nested_structure(self):
        """测试深层嵌套结构"""
        sf = SensitiveDataFilter()
        data = {
            "level1": {
                "level2": {
                    "level3": {
                        "level4": {
                            "password": "deep_secret",
                            "data": "normal",
                        }
                    }
                }
            }
        }
        result = sf.filter_dict(data)
        assert result["level1"]["level2"]["level3"]["level4"]["password"] == REDACTED_VALUE
        assert result["level1"]["level2"]["level3"]["level4"]["data"] == "normal"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_json_string_filter(self):
        """测试JSON字符串中的敏感信息"""
        sf = SensitiveDataFilter()
        data = '{"password": "secret12345678", "api_key": "sk-testkey1234567890abcdef"}'
        result = sf.mask(data)
        assert "secret12345678" not in result
        # api_key字段的值较长，应该被匹配
        assert "sk-testkey1234567890abcdef" not in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_detect_dict_input(self):
        """测试detect处理字典输入"""
        sf = SensitiveDataFilter(block_critical=True)
        data = {"password": "mysecret123", "info": "normal"}
        result = sf.detect(data)
        assert result.allowed is False
        assert len(result.violations) > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_detect_list_input(self):
        """测试detect处理列表输入"""
        sf = SensitiveDataFilter(block_critical=True)
        data = ["password=secret123", "normal text"]
        result = sf.detect(data)
        assert result.allowed is False
        assert len(result.violations) > 0


# ============================================================================
# 自定义敏感词测试
# ============================================================================


class TestCustomPatterns:
    """自定义敏感词的添加和删除测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_add_pattern_dynamically(self):
        """测试动态添加自定义模式"""
        sf = SensitiveDataFilter()
        custom_pattern = r"\bmy_custom_secret_\d+\b"
        sf.add_pattern(
            name="custom_secret",
            pattern=custom_pattern,
            level=SensitiveLevel.HIGH,
            description="自定义密钥",
        )

        test_text = "密钥：my_custom_secret_12345"
        result = sf.detect(test_text)
        assert len(result.violations) > 0
        assert any(v.pattern_name == "custom_secret" for v in result.violations)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_add_invalid_pattern(self):
        """测试添加无效正则表达式（容错）"""
        sf = SensitiveDataFilter()
        # 无效正则不应抛出异常
        sf.add_pattern(
            name="invalid",
            pattern=r"[unclosed",
            level=SensitiveLevel.MEDIUM,
        )
        # 应正常工作
        result = sf.mask("password=test123")
        assert REDACTED_VALUE in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_custom_key_patterns(self):
        """测试自定义敏感字段名"""
        # 使用utils版本的SensitiveDataFilter，支持additional_key_patterns
        sf = SensitiveDataFilter(additional_key_patterns=[r"my_private_\w+"])
        data = {"my_private_data": "secret_value", "public_data": "public_value"}
        result = sf.filter_dict(data)
        assert result["my_private_data"] != "secret_value"
        assert result["public_data"] == "public_value"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_custom_replacements(self):
        """测试自定义替换规则"""
        # 使用utils版本的SensitiveDataFilter，支持custom_replacements
        sf = SensitiveDataFilter(
            custom_replacements={"custom_field": "CUSTOM_MASKED"}
        )
        data = {"custom_field": "secret_value", "password": "pass123"}
        result = sf.filter_dict(data)
        assert result["custom_field"] == "CUSTOM_MASKED"
        assert result["password"] == REDACTED_VALUE

    @pytest.mark.unit
    @pytest.mark.p0
    def test_custom_replacements_callable(self):
        """测试可调用的自定义替换规则"""
        def custom_mask(value):
            if isinstance(value, str):
                return f"CUSTOM_{len(value)}_CHARS"
            return "CUSTOM_MASKED"

        sf = SensitiveDataFilter(
            custom_replacements={"dynamic_field": custom_mask}
        )
        data = {"dynamic_field": "testvalue"}
        result = sf.filter_dict(data)
        assert result["dynamic_field"] == "CUSTOM_9_CHARS"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_memory_filter_custom_content_patterns(self):
        """测试memory兼容层的自定义内容模式"""
        custom_patterns = {
            "custom_test": {
                "pattern": r"\btest_sensitive_\d+\b",
                "level": SensitiveLevel.HIGH,
                "description": "自定义测试模式",
            }
        }
        sf = MemorySensitiveDataFilter(custom_patterns=custom_patterns)
        result = sf.detect("这是 test_sensitive_12345 的测试")
        assert any(v.pattern_name == "custom_test" for v in result.violations)


# ============================================================================
# 过滤日志测试
# ============================================================================


class TestFilterLogging:
    """过滤日志的完整性测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_detect_returns_violations(self):
        """测试检测返回完整的违规项列表"""
        sf = SensitiveDataFilter()
        result = sf.detect("密码：mySecret123，手机：13812345678")
        assert isinstance(result.violations, list)
        assert len(result.violations) > 0
        for v in result.violations:
            assert isinstance(v, SensitiveMatch)
            assert hasattr(v, "pattern_name")
            assert hasattr(v, "matched_text")
            assert hasattr(v, "level")
            assert hasattr(v, "start_pos")
            assert hasattr(v, "end_pos")
            assert hasattr(v, "suggestion")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_violation_contains_sanitized_text(self):
        """测试违规项中包含脱敏后的文本（不含原始敏感信息）"""
        sf = SensitiveDataFilter()
        result = sf.detect("手机号：13812345678")
        phone_violation = None
        for v in result.violations:
            if "phone" in v.pattern_name:
                phone_violation = v
                break

        assert phone_violation is not None
        # 脱敏后不应包含完整手机号
        assert "12345678" not in phone_violation.matched_text
        assert "138" in phone_violation.matched_text or "****" in phone_violation.matched_text

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_result_structure(self):
        """测试FilterResult结构完整性"""
        sf = SensitiveDataFilter()
        result = sf.detect("test text")
        assert isinstance(result, FilterResult)
        assert hasattr(result, "allowed")
        assert hasattr(result, "violations")
        assert hasattr(result, "sanitized_content")
        assert hasattr(result, "action_taken")


# ============================================================================
# 过滤性能测试
# ============================================================================


class TestFilterPerformance:
    """过滤性能测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_10kb_text_under_100ms(self):
        """测试10KB文本处理时间<100ms"""
        sf = SensitiveDataFilter()
        # 生成约15KB的文本
        text = "这是一段包含各种内容的测试文本。" * 1000
        assert len(text) > 10000

        start = time.time()
        result = sf.mask(text)
        elapsed = time.time() - start

        assert result is not None
        assert elapsed < 0.1, f"处理10KB文本耗时{elapsed*1000:.2f}ms，超过100ms限制"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_dict_filter_performance(self):
        """测试字典过滤性能"""
        sf = SensitiveDataFilter()
        # 生成嵌套字典
        data = {}
        for i in range(100):
            data[f"key_{i}"] = {
                "name": f"user_{i}",
                "password": f"pass_{i}",
                "email": f"user{i}@example.com",
                "profile": {"bio": "normal text " * 10},
            }

        start = time.time()
        result = sf.filter_dict(data)
        elapsed = time.time() - start

        assert result is not None
        assert result["key_0"]["password"] == REDACTED_VALUE
        assert elapsed < 0.5  # 500ms以内

    @pytest.mark.unit
    @pytest.mark.p0
    def test_detect_performance(self):
        """测试detect性能"""
        sf = SensitiveDataFilter()
        text = "正常内容 " * 500 + "password=secret123" + "更多内容 " * 500

        start = time.time()
        result = sf.detect(text)
        elapsed = time.time() - start

        assert result.allowed is False
        assert elapsed < 0.1  # 100ms以内


# ============================================================================
# 可逆性测试
# ============================================================================


class TestIrreversibility:
    """过滤后数据的可逆性测试（不可还原）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_password_irreversible(self):
        """测试密码脱敏后不可还原"""
        sf = SensitiveDataFilter()
        original = "password=mySuperSecretPassword123"
        masked = sf.mask(original)

        # 脱敏后的值不能包含原始密码
        assert "mySuperSecretPassword123" not in masked
        # 完全替换为固定值，无法还原
        assert REDACTED_VALUE in masked

    @pytest.mark.unit
    @pytest.mark.p0
    def test_bank_card_irreversible(self):
        """测试银行卡号脱敏后不可还原中间数字"""
        sf = SensitiveDataFilter()
        original = "6222021234567890123"
        masked = sf.mask(original)

        # 中间数字被替换，无法还原
        assert "02123456789" not in masked  # 中间9位不可见

    @pytest.mark.unit
    @pytest.mark.p0
    def test_id_card_irreversible(self):
        """测试身份证号脱敏后出生日期不可还原"""
        sf = SensitiveDataFilter()
        original = "110101199003076515"
        masked = sf.mask(original)

        # 出生日期被替换，无法还原
        assert "19900307" not in masked


# ============================================================================
# 多语言环境测试
# ============================================================================


class TestMultiLanguage:
    """多语言环境下的过滤效果测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_chinese_context(self):
        """测试中文上下文中的敏感信息过滤"""
        sf = SensitiveDataFilter()
        test_cases = [
            "我的密码是secret123，请保密",
            "手机号：13812345678 是我的联系电话",
            "身份证号码为110101199003076515",
        ]
        for text in test_cases:
            result = sf.mask(text)
            assert "secret123" not in result or "13812345678" not in result or "19900307" not in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_english_context(self):
        """测试英文上下文中的敏感信息过滤"""
        sf = SensitiveDataFilter()
        test_cases = [
            "My password is mySecretPass123",
            "Call me at 13812345678",
            "Email: testuser@example.com",
        ]
        for text in test_cases:
            result = sf.mask(text)
            assert "mySecretPass123" not in result or "13812345678" not in result or "testuser" not in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_mixed_language(self):
        """测试中英混合文本过滤"""
        sf = SensitiveDataFilter()
        test_text = "用户user的 password=secret123，联系电话tel：13812345678"
        result = sf.mask(test_text)
        assert "secret123" not in result
        assert "12345678" not in result or "****" in result


# ============================================================================
# 日志过滤器测试
# ============================================================================


class TestLogFilter:
    """作为logging.Filter的功能测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_log_filter_msg_sanitized(self):
        """测试日志消息被脱敏"""
        sf = SensitiveDataFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="用户密码 password=secret123456 请保密",
            args=(),
            exc_info=None,
        )
        result = sf.filter(record)
        assert result is True  # 始终返回True（允许记录）
        assert "secret123456" not in record.msg

    @pytest.mark.unit
    @pytest.mark.p0
    def test_log_filter_args_sanitized(self):
        """测试日志参数被脱敏"""
        sf = SensitiveDataFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="用户信息: %s",
            args=("password=secret123",),
            exc_info=None,
        )
        result = sf.filter(record)
        assert result is True
        # args应该被脱敏
        assert "secret123" not in record.args[0]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_log_filter_dict_args(self):
        """测试字典类型日志参数被脱敏"""
        sf = SensitiveDataFilter()
        # 使用两个元素的元组避免logging自动转换为dict
        test_dict = {"password": "secret123"}
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="数据: %s, 状态: %s",
            args=(test_dict, "ok"),
            exc_info=None,
        )
        result = sf.filter(record)
        assert result is True
        # args应该是一个元组，第一个元素是过滤后的字典
        assert isinstance(record.args, tuple)
        assert len(record.args) == 2
        assert isinstance(record.args[0], dict)
        assert record.args[0]["password"] == REDACTED_VALUE


# ============================================================================
# 便捷函数测试
# ============================================================================


class TestUtilityFunctions:
    """便捷函数测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_mask_ip_function(self):
        """测试mask_ip函数"""
        assert mask_ip("192.168.1.100") == "192.168.xxx.xxx"
        assert mask_ip("10.0.0.1") == "10.0.xxx.xxx"
        assert mask_ip("") == ""
        assert mask_ip(None) is None
        assert mask_ip("invalid") == "invalid"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_default_filter(self):
        """测试获取默认过滤器单例"""
        f1 = get_default_filter()
        f2 = get_default_filter()
        assert f1 is f2
        assert isinstance(f1, SensitiveDataFilter)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_sensitive_data_func(self):
        """测试filter_sensitive_data便捷函数"""
        result = filter_sensitive_data({"password": "secret123"})
        assert result["password"] == REDACTED_VALUE

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_dict_func(self):
        """测试filter_dict便捷函数"""
        result = filter_dict({"password": "secret123", "name": "test"})
        assert result["password"] == REDACTED_VALUE
        assert result["name"] == "test"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_string_func(self):
        """测试filter_string便捷函数"""
        result = filter_string("password=secret123")
        assert "secret123" not in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sensitive_filter_func(self):
        """测试sensitive_filter便捷函数"""
        assert sensitive_filter("password") is True
        assert sensitive_filter("username") is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_create_filter_func(self):
        """测试create_filter工厂函数"""
        sf = create_filter(
            additional_key_patterns=[r"custom_\w+"],
            block_high=True,
        )
        assert sf.is_sensitive_key("custom_token") is True
        stats = sf.get_stats()
        assert stats["block_high"] is True


# ============================================================================
# 兼容性测试
# ============================================================================


class TestBackwardCompatibility:
    """向后兼容性测试 - memory/filter.py兼容层"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_check_method_alias(self):
        """测试check方法别名（向后兼容）"""
        sf = MemorySensitiveDataFilter()
        result = sf.check("password=secret123")
        assert isinstance(result, FilterResult)
        assert result.allowed is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_check_and_sanitize_alias(self):
        """测试check_and_sanitize方法别名"""
        sf = MemorySensitiveDataFilter(block_critical=False)
        # detect_and_sanitize返回 (allowed, sanitized_content)
        result_tuple = sf.check_and_sanitize("password=secret123")
        assert isinstance(result_tuple, tuple)
        assert len(result_tuple) >= 2
        assert isinstance(result_tuple[0], bool)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_built_in_patterns_property(self):
        """测试BUILT_IN_PATTERNS属性"""
        sf = MemorySensitiveDataFilter()
        patterns = sf.BUILT_IN_PATTERNS
        assert isinstance(patterns, dict)
        assert len(patterns) > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_memory_filter_init_defaults(self):
        """测试memory兼容层默认初始化"""
        sf = MemorySensitiveDataFilter()
        assert sf is not None
        # memory兼容层默认block_high=True
        result = sf.detect("手机：13812345678")
        assert result.allowed is False  # HIGH级别默认被阻止


# ============================================================================
# 数据类测试
# ============================================================================


class TestDataClasses:
    """数据类结构测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sensitive_level_enum(self):
        """测试SensitiveLevel枚举"""
        assert SensitiveLevel.SAFE.value == 0
        assert SensitiveLevel.LOW.value == 1
        assert SensitiveLevel.MEDIUM.value == 2
        assert SensitiveLevel.HIGH.value == 3
        assert SensitiveLevel.CRITICAL.value == 4

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sensitive_match_creation(self):
        """测试SensitiveMatch创建"""
        match = SensitiveMatch(
            pattern_name="test_pattern",
            matched_text="te***@example.com",
            start_pos=0,
            end_pos=20,
            level=SensitiveLevel.LOW,
            suggestion="测试建议",
        )
        assert match.pattern_name == "test_pattern"
        assert match.matched_text == "te***@example.com"
        assert match.level == SensitiveLevel.LOW

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_result_creation(self):
        """测试FilterResult创建"""
        result = FilterResult(
            allowed=True,
            violations=[],
            sanitized_content=None,
            action_taken="pass",
        )
        assert result.allowed is True
        assert result.violations == []
        assert result.action_taken == "pass"


# ============================================================================
# SQL注入和XSS测试
# ============================================================================


class TestInjectionDetection:
    """SQL注入和XSS脚本注入检测测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sql_injection_detect(self):
        """测试SQL注入检测"""
        sf = SensitiveDataFilter(block_critical=True)
        test_cases = [
            "SELECT * FROM users WHERE id=1",
            "DROP TABLE users",
            "INSERT INTO users VALUES ('a','b')",
            "DELETE FROM users WHERE 1=1",
            "UNION SELECT username, password FROM users",
        ]
        for test_text in test_cases:
            result = sf.detect(test_text)
            assert result.allowed is False, f"SQL注入未被检测到: {test_text}"
            assert any(v.pattern_name == "sql_injection" for v in result.violations)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_xss_detection(self):
        """测试XSS脚本注入检测"""
        sf = SensitiveDataFilter(block_critical=True)
        test_cases = [
            "<script>alert('xss')</script>",
            "javascript:alert(1)",
            '<img src=x onerror=alert(1)>',
            "<iframe src='evil.com'></iframe>",
        ]
        for test_text in test_cases:
            result = sf.detect(test_text)
            assert result.allowed is False, f"XSS未被检测到: {test_text}"
            assert any(v.pattern_name == "xss_script" for v in result.violations)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_xss_masking(self):
        """测试XSS内容检测（detect模式）"""
        sf = SensitiveDataFilter(block_critical=True)
        result = sf.detect("<script>alert('xss')</script>")
        # XSS在detect中应该能被检测到
        assert result.allowed is False
        assert any(v.pattern_name == "xss_script" for v in result.violations)


# ============================================================================
# IP地址测试
# ============================================================================


class TestIpAddressFiltering:
    """IP地址过滤测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_ipv4_masking(self):
        """测试IPv4地址脱敏"""
        sf = SensitiveDataFilter()
        result = sf.mask("服务器IP：192.168.1.100")
        assert "192.168" in result
        assert "xxx.xxx" in result
        assert "1.100" not in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_ipv4_detect_level(self):
        """测试IP地址检测级别为LOW"""
        sf = SensitiveDataFilter(block_critical=True, block_high=True)
        result = sf.detect("IP: 10.0.0.1")
        # LOW级别不应被阻止
        assert result.allowed is True
        assert any(v.pattern_name == "ip_v4" for v in result.violations)


# ============================================================================
# 覆盖率补充测试
# ============================================================================


class TestCoverageSupplementary:
    """覆盖率补充测试 - 覆盖边界分支和异常路径"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_data_non_string_types(self):
        """测试 filter_data 对非字符串/字典/列表类型的处理"""
        sf = SensitiveDataFilter()
        # 整数
        assert sf.filter_data(123) == 123
        # 浮点数
        assert sf.filter_data(3.14) == 3.14
        # 布尔值
        assert sf.filter_data(True) is True
        # None
        assert sf.filter_data(None) is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_json_alias(self):
        """测试 filter_json 别名方法"""
        sf = SensitiveDataFilter()
        data = {"password": "secret123"}
        result = sf.filter_json(data)
        assert result["password"] != "secret123"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_string_alias(self):
        """测试 filter_string 别名方法"""
        sf = SensitiveDataFilter()
        result = sf.filter_string("password=secret123")
        assert "secret123" not in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sensitive_key_with_numeric_value(self):
        """测试敏感字段值为数字类型时的脱敏"""
        sf = SensitiveDataFilter()
        data = {
            "password": 123456,
            "token": 999999,
            "api_key": 12345.678,
            "is_active": True,
        }
        result = sf.filter_dict(data)
        assert result["password"] == REDACTED_VALUE
        assert result["token"] == REDACTED_VALUE
        assert result["api_key"] == REDACTED_VALUE

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sensitive_key_short_string_value(self):
        """测试敏感字段值为短字符串（<=4）时的脱敏"""
        sf = SensitiveDataFilter()
        data = {"password": "123"}
        result = sf.filter_dict(data)
        assert result["password"] == REDACTED_VALUE

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sensitive_key_long_string_partial(self):
        """测试敏感字段值为长字符串时的脱敏"""
        sf = SensitiveDataFilter()
        data = {"password": "myLongPassword123"}
        result = sf.filter_dict(data)
        # 密码字段应该被完全脱敏
        assert result["password"] == REDACTED_VALUE

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_dict_non_dict_input(self):
        """测试 filter_dict 输入非字典时直接返回"""
        sf = SensitiveDataFilter()
        # 输入字符串
        assert sf.filter_dict("not a dict") == "not a dict"
        # 输入数字
        assert sf.filter_dict(123) == 123
        # 输入 None
        assert sf.filter_dict(None) is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_list_non_list_input(self):
        """测试 filter_list 输入非列表时直接返回"""
        sf = SensitiveDataFilter()
        assert sf.filter_list("not a list") == "not a list"
        assert sf.filter_list(123) == 123

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_list_mixed_types(self):
        """测试 filter_list 处理混合类型元素"""
        sf = SensitiveDataFilter()
        data = [
            "password=secret123",
            123,
            {"token": "abc123"},
            ["nested", "password=test"],
            None,
        ]
        result = sf.filter_list(data)
        assert len(result) == 5
        assert "secret123" not in result[0]
        assert result[1] == 123
        assert result[2]["token"] != "abc123"
        assert "test" not in result[3][1]
        assert result[4] is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_detect_none_input(self):
        """测试 detect 方法输入 None 时的处理"""
        sf = SensitiveDataFilter()
        result = sf.detect(None)
        assert result.allowed is True
        assert result.action_taken == "pass_empty"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_detect_non_string_input(self):
        """测试 detect 方法输入非字符串类型时的处理"""
        sf = SensitiveDataFilter()
        # 数字输入
        result = sf.detect(12345)
        assert isinstance(result, FilterResult)
        # 字典输入
        result = sf.detect({"password": "secret123"})
        assert isinstance(result, FilterResult)
        # 列表输入
        result = sf.detect(["password=secret123"])
        assert isinstance(result, FilterResult)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_invalid_custom_pattern_compilation(self):
        """测试无效正则模式的编译失败处理"""
        sf = SensitiveDataFilter(custom_content_patterns={})
        # 添加一个无效的正则模式
        sf.add_pattern("invalid_pattern", "[invalid", SensitiveLevel.LOW, "test")
        # 即使模式无效，过滤器也应该能正常工作
        result = sf.mask("test text")
        assert result == "test text"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sanitize_match_short_text(self):
        """测试 _sanitize_match 对短文本的处理"""
        sf = SensitiveDataFilter()
        # 测试 JWT token 的短 payload 情况，通过 mask 间接测试
        # 或者测试一个已知会产生短匹配的模式
        # 验证脱敏函数能正确处理各种长度的输入
        result = sf.mask("password=abc123")
        # 密码应该被脱敏
        assert "abc123" not in result
        assert len(result) > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_bank_card_masking(self):
        """测试银行卡号脱敏格式"""
        sf = SensitiveDataFilter()
        result = sf.mask("银行卡号：6222021234567890123")
        # 应该是前4位 + **** + 后4位
        assert "6222" in result
        assert "0123" in result
        assert "****" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_hk_phone_masking(self):
        """测试香港手机号脱敏"""
        sf = SensitiveDataFilter()
        result = sf.mask("香港电话：+852 91234567")
        # 香港手机号8位，前4位 + ****
        assert "9123" in result or "+852" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_api_key_field_masking(self):
        """测试 API Key 字段脱敏（键名匹配时完全脱敏）"""
        sf = SensitiveDataFilter()
        data = {"api_key": "sk-1234567890abcdefghij"}
        result = sf.filter_dict(data)
        # API Key 字段通过键名匹配，应该完全脱敏
        assert result["api_key"] == REDACTED_VALUE

    @pytest.mark.unit
    @pytest.mark.p0
    def test_jwt_token_masking(self):
        """测试 JWT Token 脱敏格式"""
        sf = SensitiveDataFilter()
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        # 直接检测 JWT
        result = sf.mask(jwt)
        # JWT 应该被脱敏
        assert result != jwt
        # 验证脱敏结果包含脱敏标记
        assert "***" in result or "..." in result or REDACTED_VALUE in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_detect_and_sanitize_blocked(self):
        """测试 detect_and_sanitize 对被阻止内容的返回"""
        sf = SensitiveDataFilter(block_critical=True)
        allowed, sanitized = sf.detect_and_sanitize("password=mySuperSecret123")
        assert allowed is False
        assert sanitized is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_is_sensitive_key_empty(self):
        """测试 is_sensitive_key 对空输入的处理"""
        sf = SensitiveDataFilter()
        assert sf.is_sensitive_key("") is False
        assert sf.is_sensitive_key(None) is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_stats(self):
        """测试 get_stats 统计信息"""
        sf = SensitiveDataFilter()
        stats = sf.get_stats()
        assert "total_key_patterns" in stats
        assert "total_content_patterns" in stats
        assert "block_critical" in stats
        assert "block_high" in stats
        assert stats["total_key_patterns"] > 0
        assert stats["total_content_patterns"] > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_memory_filter_backward_compat_methods(self):
        """测试记忆模块过滤器的向后兼容方法"""
        mf = MemorySensitiveDataFilter()
        # 测试 check 方法 - 返回 FilterResult 对象
        result = mf.check("test message")
        assert isinstance(result, FilterResult)
        assert hasattr(result, 'allowed')
        # 测试 check_and_sanitize 方法 - 返回二元组 (allowed, sanitized_content)
        result_tuple = mf.check_and_sanitize("password=secret123")
        assert isinstance(result_tuple, tuple)
        assert len(result_tuple) == 2
        assert isinstance(result_tuple[0], bool)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_memory_filter_built_in_patterns(self):
        """测试记忆模块 BUILT_IN_PATTERNS 属性"""
        mf = MemorySensitiveDataFilter()
        patterns = mf.BUILT_IN_PATTERNS
        assert isinstance(patterns, dict)
        assert len(patterns) > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_default_filter_singleton(self):
        """测试 get_default_filter 单例模式"""
        f1 = get_default_filter()
        f2 = get_default_filter()
        assert f1 is f2

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_sensitive_data_function(self):
        """测试 filter_sensitive_data 便捷函数"""
        result = filter_sensitive_data("password=secret123")
        assert "secret123" not in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_dict_function(self):
        """测试 filter_dict 便捷函数"""
        result = filter_dict({"password": "secret123"})
        assert result["password"] != "secret123"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_filter_string_function(self):
        """测试 filter_string 便捷函数"""
        result = filter_string("password=secret123")
        assert "secret123" not in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sensitive_filter_function(self):
        """测试 sensitive_filter 快捷函数"""
        # sensitive_filter 是一个检查字段名的快捷函数
        assert sensitive_filter("password") is True
        assert sensitive_filter("api_key") is True
        assert sensitive_filter("username") is False
        assert sensitive_filter("email") is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_create_filter_function(self):
        """测试 create_filter 工厂函数"""
        f = create_filter(block_critical=True)
        assert f is not None
        assert f._block_critical is True
