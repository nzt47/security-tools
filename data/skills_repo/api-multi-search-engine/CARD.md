# multi-search-engine

Multi search engine integration with 17 engines (8 CN + 9 Global). Supports advanced search operators, time filters, site search, privacy engines, and WolframAlpha knowledge queries. No API keys required.

## 何时使用

（原文没有这一节）

## 步骤

（原文没有这一节）

## 参数

（原文没有这一节）

## 约束 / 反例

（原文没有这一节）

## 示例

```javascript
// Basic search
web_fetch({"url": "https://www.google.com/search?q=python+tutorial"})

// Site-specific
web_fetch({"url": "https://www.google.com/search?q=site:github.com+react"})

// File type
web_fetch({"url": "https://www.google.com/search?q=machine+learning+filetype:pdf"})

// Time filter (past week)
web_fetch({"url": "https://www.google.com/search?q=ai+news&tbs=qdr:w"})

// Privacy search
web_fetch({"url": "https://duckduckgo.com/html/?q=privacy+tools"})

// DuckDuckGo Bangs
web_fetch({"url": "https://duckduckgo.com/html/?q=!gh+tensorflow"})

// Knowledge calculation
web_fet…

