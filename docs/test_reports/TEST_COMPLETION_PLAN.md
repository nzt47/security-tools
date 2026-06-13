# 未达标模块测试补充计划

## 总体覆盖率现状

| 模块 | 当前覆盖率 | 目标覆盖率 | 差距 | 优先级 |
|------|-----------|-----------|------|--------|
| `web/processor.py` | 100% | 90% | ✅ 已达标 | - |
| `web/crawler_control.py` | 100% | 90% | ✅ 已达标 | - |
| `utils/compatibility.py` | 100% | 90% | ✅ 已达标 | - |
| `utils/index_manager.py` | 100% | 90% | ✅ 已达标 | - |
| `web/browser_agent.py` | **87%** | 90% | 3% | 中 |
| `web/http_client.py` | 22% | 90% | 68% | 高 |
| `web/scraper.py` | 19% | 90% | 71% | 高 |
| `web/search.py` | 16% | 90% | 74% | 高 |
| `error_handler.py` | 45% | 90% | 45% | 中 |
| 其他模块 | <50% | 90% | - | 低 |

---

## 第一优先级：高覆盖率模块（接近90%）

### 1. web/browser_agent.py - 87%
**当前状态**: 61个测试用例，覆盖率 87%
**目标状态**: 90%+

#### 缺失的代码行分析
```
缺失行: 66-122 (57行)
原因: _start_browser 方法中的 Selenium WebDriver 初始化代码
难度: 需要实际启动浏览器或深度 mock Chrome Options
```

#### 补充策略
1. **集成测试**: 创建专门的集成测试脚本，使用真实的 Chrome WebDriver
2. **选项配置测试**: 单独测试 Chrome Options 配置逻辑
3. **CDP 命令测试**: 测试 execute_cdp_cmd 调用

#### 预估工作量
- 新增测试用例: 10-15个
- 预估覆盖率提升: 3-5%
- 预计达到: 90-92%

---

## 第二优先级：高优先级待测模块

### 2. web/http_client.py - 22%
**当前状态**: 0个专用测试用例
**目标状态**: 90%+

#### 模块功能分析
- HTTP请求发送
- 请求头管理
- 响应处理
- 错误处理和重试
- 超时管理
- 代理支持
- SSL配置
- 连接池管理

#### 缺失的关键场景
- 正常HTTP请求（GET/POST/PUT/DELETE）
- 请求头定制
- 超时配置
- 重试逻辑
- SSL证书验证
- 代理使用
- 流式响应
- 文件上传下载
- 错误处理

#### 补充测试用例计划

| 测试类 | 测试用例数 | 覆盖场景 |
|--------|----------|----------|
| `TestHTTPClientInit` | 5 | 初始化配置、默认参数 |
| `TestHTTPClientBasicRequests` | 8 | GET/POST/PUT/DELETE 请求 |
| `TestHTTPClientHeaders` | 4 | 自定义请求头、默认头 |
| `TestHTTPClientTimeout` | 3 | 连接超时、读取超时 |
| `TestHTTPClientRetry` | 4 | 自动重试、重试次数 |
| `TestHTTPClientErrorHandling` | 6 | 网络错误、超时、4xx/5xx |
| `TestHTTPClientProxy` | 3 | 代理配置、使用 |
| `TestHTTPClientSSL` | 2 | SSL验证、证书 |
| `TestHTTPClientStreaming` | 2 | 流式响应处理 |
| `TestHTTPClientFileUpload` | 2 | 文件上传 |

**预估新增**: 39个测试用例
**预估覆盖率**: 80-85%

---

### 3. web/scraper.py - 19%
**当前状态**: 0个专用测试用例
**目标状态**: 90%+

#### 模块功能分析
- HTML解析
- CSS选择器
- XPath查询
- 内容提取
- 链接发现
- 图片提取
- 表单提取
- 编码处理

#### 补充测试用例计划

| 测试类 | 测试用例数 | 覆盖场景 |
|--------|----------|----------|
| `TestScraperInit` | 3 | 初始化配置 |
| `TestScraperCSSSelection` | 6 | CSS选择器查询 |
| `TestScraperXPathSelection` | 5 | XPath查询 |
| `TestScraperContentExtraction` | 8 | 文本、HTML、属性提取 |
| `TestScraperLinkDiscovery` | 4 | 内部链接、外部链接 |
| `TestScraperImageExtraction` | 3 | 图片URL提取 |
| `TestScraperFormExtraction` | 4 | 表单字段提取 |
| `TestScraperEncoding` | 3 | 编码检测和转换 |
| `TestScraperErrorHandling` | 5 | 解析错误、无效HTML |

**预估新增**: 41个测试用例
**预估覆盖率**: 85-90%

---

### 4. web/search.py - 16%
**当前状态**: 0个专用测试用例
**目标状态**: 90%+

#### 模块功能分析
- 搜索引擎集成
- 查询构建
- 结果解析
- 搜索分页
- 过滤器应用
- 排序处理

#### 补充测试用例计划

| 测试类 | 测试用例数 | 覆盖场景 |
|--------|----------|----------|
| `TestSearchEngineInit` | 3 | 初始化配置 |
| `TestSearchQueryBuilding` | 6 | 查询构建、编码 |
| `TestSearchResultParsing` | 8 | 结果解析、提取 |
| `TestSearchPagination` | 4 | 分页处理 |
| `TestSearchFilters` | 5 | 日期、语言、类型过滤 |
| `TestSearchSorting` | 3 | 结果排序 |
| `TestSearchErrorHandling` | 4 | API错误、网络问题 |
| `TestSearchCaching` | 3 | 结果缓存 |

**预估新增**: 36个测试用例
**预估覆盖率**: 80-88%

---

## 第三优先级：中优先级模块

### 5. error_handler.py - 45%
**当前状态**: 部分测试用例
**目标状态**: 90%+

#### 缺失的关键场景
- 错误分类逻辑
- 错误聚合
- 错误报告格式化
- 特定错误类型处理
- 错误上下文管理

#### 补充测试用例计划

| 测试类 | 测试用例数 | 覆盖场景 |
|--------|----------|----------|
| `TestErrorClassification` | 6 | 错误类型分类 |
| `TestErrorAggregation` | 5 | 错误聚合策略 |
| `TestErrorFormatting` | 4 | 报告格式化 |
| `TestSpecificErrorTypes` | 8 | IOError、ValueError等 |
| `TestErrorContext` | 5 | 错误上下文管理 |

**预估新增**: 28个测试用例
**预估覆盖率**: 75-85%

---

## 实施建议

### 阶段一：完成 browser_agent.py（1天）
1. 补充剩余 3% 覆盖率
2. 创建集成测试脚本
3. 验证 90% 覆盖率目标

### 阶段二：高优先级模块（3-5天）
1. web/http_client.py - 39个测试用例
2. web/scraper.py - 41个测试用例
3. web/search.py - 36个测试用例

### 阶段三：中优先级模块（2-3天）
1. error_handler.py - 28个测试用例

### 总工作量估算
- 新增测试用例: 约 144 个
- 总耗时: 约 6-9 个工作日
- 最终覆盖率目标: 85-90%

---

## 资源需求

### 工具和依赖
- pytest + pytest-cov
- unittest.mock / pytest-mock
- responses (HTTP mock)
- beautifulsoup4 (解析测试)
- selenium (集成测试，可选)

### 人员配置
- 1-2名测试工程师
- 可并行处理多个模块

---

**文档生成时间**: 2026-06-05
**下次更新**: 完成每个阶段后