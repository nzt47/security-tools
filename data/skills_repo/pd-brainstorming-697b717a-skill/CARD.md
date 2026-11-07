# brainstorming

在进行任何创造性工作（比如开发新功能、构建组件、添加功能或修改现有行为）之前，你【必须】先使用此流程。务必在动手写代码实现之前，先充分探索并明确用户的真实意图、具体需求和设计方案。由

## 何时使用

- **该用**（满足任一条就该走本流程）：
- 要**新建**功能 / 组件 / 模块，或**改变已有行为**（含"小改动"——本流程对简单项目同样适用，只是设计可以只有几句话）；
- 需求只有一句话、验收标准不清，需要**一次一个问题**把意图问出来；
- 存在**多种合理方案**（自研 / 复用现成能力 / 换形态），需要摆出 2-3 个取舍再定；
- 涉及**多个相互独立的子系统**（如"做个带聊天、文件、计费、报表的平台"）—— 先拆子项目，再对**第一个**子项目走完整设计流；
- 在**既有代码库**里改动，且改动会碰到边界不清 / 文件过大的地方（设计里要带上针对性的小改进）。
- **不必单独走**（但仍受下面的硬门约束）：
- 纯缺陷修复、纯文档/配置改动、已有明确规格的实施任务 —— 直接进实现；

## 步骤

- You MUST create a task for each of these items and complete them in order:
- **Explore project context** — check files, docs, recent commits
- **Offer visual companion** (if topic will involve visual questions) — this is its own message, not combined with a clari…
- **Ask clarifying questions** — one at a time, understand purpose/constraints/success criteria
- **Propose 2-3 approaches** — with trade-offs and your recommendation
- **Present design** — in sections scaled to their complexity, get user approval after each section
- **Wri…

## 参数

（原文没有这一节）

## 约束 / 反例

（原文没有这一节）

## 示例

用户：「给工作台加个批量导出」。

1. **探上下文**：先看 `yunshu-ui/src/pages` 与现有 `transform` 队列、`/api` 里已有的导出/下载端点，确认能复用什么；
2. **判断要不要提视觉伴侣**：若接下来要问"导出对话框长什么样"，单独发一条消息询问，不与其他问题混排；
3. **一次一个问题**：先问"导出哪些对象（会话 / 知识条目 / 工具调用记录）"，再问规模与格式（一次几百条还是几万条；zip / csv / json）；
4. **给 2-3 个方案**：① 前端打包 zip（简单、受浏览器内存限制）；② 后端异步任务 + 下载链接（可支撑大数量，需轮询）；③ 复用现有转化队列的批量通道。给出推荐与理由；
5. **分节呈现设计**：架构 → 组件 → 数据流 → 错误处理 → 测试，每节问一句"这样对吗"；
6. **写 spec** 到 `docs/superpowers/specs/2026-11-07-batch-export-design.md` 并提交；
7. **自审**：扫 `TBD` / 自相矛盾 / 范围过大 / 二义性，就地改；
8. **用户复核 spec**，批准后才调用 writing-plans 生成实施计划。

反例（同一次对话里常见的越界）：用户说"先随便搞个能点的"，于是直接改代码 —— 违…
