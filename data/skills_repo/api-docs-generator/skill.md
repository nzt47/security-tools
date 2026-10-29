---
id: api-docs-generator
name: api-docs-generator
description: Scan code endpoints and generate comprehensive API documentation in OpenAPI,
  Markdown, or custom format. Use when asked to "document this API", "generate API
  docs", "create API documentation", "make docs for my endpoints", or "document my
  routes".
version: 1.0.0
author: ''
category: claude
source: claude-skills
tags:
- claude-skills
content_type: markdown
enabled: true
---

# API Docs Generator

Generate comprehensive API documentation from source code. Supports REST, GraphQL, and general function/method APIs.

## When to Use

- Asked to "document this API", "generate API docs", "create API documentation", "make docs for my endpoints", or "document my routes"

## Workflow

### Step 1: Discovery
- Scan the project structure to identify API endpoints/routes
- Detect framework (Express, FastAPI, Flask, Next.js, Django, Spring, etc.)
- Locate route definitions, controllers, handlers
- Identify input validation schemas and models

### Step 2: Extraction
For each endpoint, extract:
- **HTTP method** and **path** (for REST APIs)
- **Input parameters**: path params, query params, request body, headers
- **Response format**: status codes, response body shape
- **Authentication/authorization requirements**
- **Middleware or guards** applied
- **Inline comments** or docstrings (JSDoc, Sphinx, etc.)
- **Type annotations** on function signatures

### Step 3: Generation
Generate documentation in the requested format:

**OpenAPI 3.x** (default):
```yaml
openapi: 3.0.3
info:
  title: Project API
  version: 1.0.0
paths:
  /api/users:
    get:
      summary: List users
      parameters:
        - name: page
          in: query
          schema: { type: integer }
      responses:
        "200":
          description: User list
```

**Markdown**:
```markdown
## GET /api/users
List all users with pagination.

**Parameters:**
- `page` (query, optional) — Page number, defaults to 1
- `limit` (query, optional) — Items per page, defaults to 20

**Response:** `200 OK`
```json
{ "users": [...], "total": 100 }
```
```

**Custom format** — adapt to user-specified template.

### Step 4: Output
- Write to file(s) in project root or `docs/` directory
- Update existing API docs if present
- If OpenAPI spec exists, merge changes rather than overwrite

## Reference
When generating docs, consider:
- Generate README API section for quick reference
- Create `docs/api/` directory with one file per resource group
- For OpenAPI, include `servers` section with dev/staging/prod URLs
- Note deprecated endpoints and suggested alternatives
- Flag endpoints missing authentication or validation as warnings
