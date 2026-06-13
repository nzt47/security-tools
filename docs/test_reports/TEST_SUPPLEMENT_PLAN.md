# 测试用例补充计划

## 概述

本计划针对项目中代码覆盖率未达到 90% 的模块，制定详细的测试补充方案。

## 当前覆盖率状态

| 模块 | 代码行数 | 未覆盖行数 | 当前覆盖率 | 优先级 |
|------|---------|-----------|-----------|--------|
| agent/web/scraper.py | 192 | 155 | 19% | **高** |
| agent/web/search.py | 153 | 128 | 16% | **高** |
| agent/error_handler.py | 268 | 148 | 45% | **中** |

---

## 一、web/scraper.py - 网页解析引擎

### 1.1 模块功能概述
- HTML 抓取与解析
- XPath/CSS 选择器提取
- 链接提取与清洗
- meta 信息提取
- 正文提取（Readability 风格）
- 动态内容检测

### 1.2 测试覆盖策略

#### 1.2.1 初始化与配置
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| 默认初始化 | `test_init_default` | 实例化成功，默认配置正确 |
| 设置 HTTP 客户端 | `test_set_http_client` | HTTP 客户端配置生效 |

#### 1.2.2 抓取与解析
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| HTTP 客户端未配置 | `test_fetch_no_http_client` | 返回错误 |
| 抓取失败（网络错误） | `test_fetch_network_error` | 返回错误状态 |
| 解析空 HTML | `test_parse_empty_html` | 返回错误 |
| 解析无效 HTML | `test_parse_invalid_html` | 返回解析错误 |
| 解析正常 HTML | `test_parse_valid_html` | 正确提取标题、文本、链接等 |

#### 1.2.3 XPath 提取
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| 有效 XPath 表达式 | `test_xpath_valid_expression` | 返回正确结果列表 |
| 无效 XPath 表达式 | `test_xpath_invalid_expression` | 返回空列表，记录警告 |
| 无解析树时提取 | `test_xpath_no_tree` | 返回空列表 |

#### 1.2.4 CSS 选择器提取
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| 提取文本内容 | `test_css_extract_text` | 返回文本列表 |
| 提取属性值 | `test_css_extract_attribute` | 返回属性值列表 |
| 无效选择器 | `test_css_invalid_selector` | 返回空列表 |

#### 1.2.5 结构化提取
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| 正常提取多个字段 | `test_extract_multiple_fields` | 返回结构化数据 |
| 提取失败（抓取失败） | `test_extract_fetch_failed` | 返回错误 |

#### 1.2.6 动态内容检测
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| React/Vue 页面 | `test_detect_react_vue` | 返回 True |
| 静态 HTML 页面 | `test_detect_static_page` | 返回 False |

#### 1.2.7 内部提取方法
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| 提取标题 | `test_extract_title` | 返回正确标题 |
| 提取正文文本 | `test_extract_text` | 返回清理后的文本 |
| 提取链接 | `test_extract_links` | 返回链接列表 |
| 提取图片 | `test_extract_images` | 返回图片列表 |
| 提取 meta | `test_extract_meta` | 返回 meta 字典 |
| 提取标题层级 | `test_extract_headings` | 返回层级结构 |

#### 1.2.8 工具方法
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| 清洗 HTML | `test_clean_html` | 移除脚本、样式、注释 |
| 快速提取文本 | `test_extract_text_from_html` | 返回纯文本 |
| 获取统计信息 | `test_get_stats` | 返回正确统计数据 |

### 1.3 预估测试用例数量：20-25 个

---

## 二、web/search.py - 搜索引擎集成

### 2.1 模块功能概述
- DuckDuckGo / Bing / Google / Brave 搜索集成
- 搜索缓存机制
- 批量搜索
- 搜索结果结构化

### 2.2 测试覆盖策略

#### 2.2.1 初始化与配置
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| 默认初始化 | `test_init_default` | 默认引擎为 duckduckgo |
| 自定义配置初始化 | `test_init_custom_config` | 配置正确加载 |
| 设置 HTTP 客户端 | `test_set_http_client` | HTTP 客户端配置生效 |

#### 2.2.2 搜索接口
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| 执行搜索（DuckDuckGo） | `test_search_duckduckgo` | 返回搜索结果 |
| 不支持的引擎 | `test_search_unsupported_engine` | 返回错误 |
| HTTP 客户端未配置 | `test_search_no_http_client` | 返回错误 |

#### 2.2.3 DuckDuckGo 搜索
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| 解析正常 HTML | `test_parse_duckduckgo_html` | 返回结构化结果 |
| 解析失败回退 | `test_parse_duckduckgo_fallback` | 使用正则回退解析 |

#### 2.2.4 Bing 搜索
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| API Key 未配置 | `test_search_bing_no_api_key` | 返回错误 |
| 解析 API 响应 | `test_parse_bing_response` | 返回结构化结果 |

#### 2.2.5 Google 搜索
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| API Key/CX 未配置 | `test_search_google_missing_config` | 返回错误 |
| 解析 API 响应 | `test_parse_google_response` | 返回结构化结果 |

#### 2.2.6 Brave 搜索
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| API Key 未配置 | `test_search_brave_no_api_key` | 返回错误 |
| 解析 API 响应 | `test_parse_brave_response` | 返回结构化结果 |

#### 2.2.7 缓存机制
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| 缓存命中 | `test_cache_hit` | 返回缓存结果 |
| 缓存过期 | `test_cache_expired` | 重新获取 |
| 缓存清理 | `test_clear_cache` | 缓存被清空 |

#### 2.2.8 批量搜索
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| 批量搜索多个关键词 | `test_multi_search` | 返回多个搜索结果 |

#### 2.2.9 工具方法
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| 获取可用引擎 | `test_get_available_engines` | 返回引擎列表及状态 |
| 获取搜索统计 | `test_get_stats` | 返回统计数据 |

### 2.3 预估测试用例数量：25-30 个

---

## 三、error_handler.py - 统一错误处理

### 3.1 模块功能概述
- 错误分类与标准化
- 断路器模式
- 自动重试策略
- 错误指标收集

### 3.2 测试覆盖策略

#### 3.2.1 错误类型定义
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| YunshuError 基础功能 | `test_yunshu_error_base` | 正确初始化和转换 |
| RecoverableError | `test_recoverable_error` | 可恢复标记正确 |
| CriticalError | `test_critical_error` | 严重错误标记正确 |
| 网络错误类型 | `test_network_errors` | 各类网络错误正确分类 |
| 数据错误类型 | `test_data_errors` | 数据错误正确分类 |
| 安全错误 | `test_security_error` | 安全错误标记正确 |

#### 3.2.2 断路器模式
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| 断路器初始化 | `test_circuit_breaker_init` | 状态为 CLOSED |
| 正常执行 | `test_circuit_breaker_success` | 正常执行，记录成功 |
| 失败次数达到阈值 | `test_circuit_breaker_open` | 状态变为 OPEN |
| 半开状态恢复 | `test_circuit_breaker_half_open_recover` | 成功后恢复 CLOSED |
| 半开状态失败 | `test_circuit_breaker_half_open_fail` | 重新变为 OPEN |
| 获取状态 | `test_circuit_breaker_status` | 返回正确状态信息 |

#### 3.2.3 重试策略
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| 指数退避计算 | `test_retry_policy_backoff` | 延迟正确递增 |
| 最大延迟限制 | `test_retry_policy_max_delay` | 延迟不超过最大值 |
| 抖动计算 | `test_retry_policy_jitter` | 添加随机抖动 |

#### 3.2.4 错误处理器
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| 记录错误 | `test_record_error` | 错误被记录，指标更新 |
| 注册熔断器 | `test_register_circuit_breaker` | 熔断器注册成功 |
| 执行带重试 | `test_execute_with_retry` | 自动重试失败操作 |
| 不可重试异常 | `test_execute_non_retryable` | 不重试，直接抛出 |
| 获取指标 | `test_get_metrics` | 返回正确指标数据 |

#### 3.2.5 装饰器
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| @with_retry 装饰器 | `test_with_retry_decorator` | 自动重试功能正常 |
| @with_circuit_breaker 装饰器 | `test_with_circuit_breaker_decorator` | 熔断器保护正常 |

#### 3.2.6 全局错误处理器
| 测试场景 | 测试方法 | 预期结果 |
|---------|---------|---------|
| 获取全局实例 | `test_get_global_handler` | 返回单例实例 |

### 3.3 预估测试用例数量：30-35 个

---

## 四、优先级排序

| 优先级 | 模块 | 原因 |
|--------|------|------|
| **P0** | web/scraper.py | 核心网页解析功能，影响数据抓取流程 |
| **P0** | web/search.py | 搜索引擎集成，对外接口关键 |
| **P1** | error_handler.py | 错误处理基础组件，影响系统稳定性 |

---

## 五、执行计划

| 阶段 | 任务 | 预估时间 | 负责人 |
|------|------|---------|--------|
| 第1周 | web/scraper.py 测试开发与验证 | 2-3 天 | - |
| 第1周 | web/search.py 测试开发与验证 | 2-3 天 | - |
| 第2周 | error_handler.py 测试开发与验证 | 3-4 天 | - |
| 第2周 | 全量测试回归验证 | 1 天 | - |

---

## 六、验收标准

1. 各模块代码行覆盖率 ≥ 90%
2. 各模块函数覆盖率 ≥ 90%
3. 各模块分支覆盖率 ≥ 90%
4. 所有新增测试用例通过
5. 无回归错误