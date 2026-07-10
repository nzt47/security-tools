# Changelog

All notable changes to the Yunshu-UI project will be documented in this file.

## [0.1.0] - 2026-07-11

### Added — P2-2 VisualEditor 可视化工作流编辑器（Phase 1-3）

#### Phase 1：基础架构
- 5 种节点类型组件（SkillNode / ConditionalNode / LoopNode / AgentNode / WorkflowNode）
- NodeRenderer 注册表 + React.memo 自定义比较函数
- NodeValidator 校验器（5 种节点类型规则、20 个测试）
- TypeScript 类型定义（FlowNodeData / NodeType / NodeProps）

#### Phase 2：代码生成
- CodeGenerator 图→YAML 转换器
  - Kahn 算法拓扑排序 O(V+E)
  - 5 种节点类型 YAML 映射
  - 自实现 YAML 序列化（避免 js-yaml 依赖）
  - 环检测兜底（所有节点仍出现）
- 20 个单元测试覆盖

#### Phase 3：交互逻辑 + 撤销/重做
- useFlowStore Zustand 状态管理
  - nodes/edges CRUD + 选中态 + YAML 预览
  - 撤销/重做（MAX_HISTORY=50 浅引用快照）
  - VE_DEBUG 性能埋点（localStorage 开关，零开销）
- FlowCanvas 画布交互（拖拽/连线/键盘快捷键）
- ComponentPalette 组件面板（5 种节点分组）
- PropertiesPanel 属性编辑（按节点类型动态字段）
- YamlPreview YAML 预览（防抖 300ms）
- VisualEditor 主入口（三栏布局 + 懒加载）
- 集成到 SkillManagement 第三个 Tab

#### 性能优化策略（5 种）
1. React.memo + 自定义比较（节点组件）
2. 防抖 YAML 生成（300ms）
3. 虚拟化节点列表（ComponentPalette）
4. React.lazy + Suspense（VisualEditor Tab）
5. Zustand 选择器精确订阅

#### 测试 suite（8 个文件 / 107 个测试 / 100% 通过）
- `CodeGenerator.test.ts` — 20 个单元测试
- `NodeValidator.test.ts` — 20 个单元测试
- `NodeRenderer.test.tsx` — 19 个组件测试
- `performance.test.tsx` — 16 个性能策略测试
- `stress500.test.ts` — 7 个 500 节点极端压测
- `memory500.test.ts` — 8 个内存泄漏检测（需 --expose-gc）
- `ui-fluency.test.tsx` — 9 个 UI 流畅度测试
- `undo-redo-stress.test.ts` — 8 个快速连续 undo/redo 边界测试

#### 性能实测数据（500 节点场景）
- generateYaml: 6.4-27ms（验收线 100ms）
- topologicalSort: 2.8-7.4ms（验收线 50ms）
- UI 操作端到端: 0.27-3.18ms（流畅线 16ms）
- 50 步历史内存增量: +0.23MB（验收线 15MB）
- 250 次 undo/redo 循环泄漏: +0.02MB（验收线 5MB）
- 1000 次连续 undo: 不抛异常（无栈溢出）

### Changed
- `SkillManagement.tsx` 新增 `visual-editor` Tab，懒加载 VisualEditor
- `vitest.config.ts` 将 VisualEditor 核心模块纳入覆盖率统计
- `package.json` 新增 `@xyflow/react`、`zustand`、`lucide-react` 依赖

### Documentation
- `P2_可视化脚手架.md` — P2-2 技术方案文档（Phase 1-5 实施计划）
- `P2_性能对比报告.md` — 5 种优化策略对比 + 极端场景分析 + 内存检测
- `P2_生产环境性能基线建议.md` — 部署性能基线 + 监控指标 + 降级策略
- `P2_测试报告总结.md` — 集成测试 suite 报告

### Dependencies
- `@xyflow/react` ^12.11.2 — ReactFlow 可视化画布
- `zustand` ^5.0.3 — 轻量状态管理
- `lucide-react` ^0.511.0 — 图标库
