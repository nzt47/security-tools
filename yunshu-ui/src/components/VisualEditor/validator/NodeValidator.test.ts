import { describe, it, expect } from 'vitest';
import { validateNode, NODE_RULES } from './NodeValidator';
import type { FlowNodeData, NodeType } from '../types';

function makeNode(overrides: Partial<FlowNodeData> = {}): FlowNodeData {
  return { label: 'test', nodeType: 'skill', ...overrides };
}

describe('NodeValidator', () => {
  describe('NODE_RULES 完整性', () => {
    it('5 种节点类型均有规则', () => {
      const types: NodeType[] = ['skill', 'conditional', 'loop', 'agent', 'workflow'];
      for (const t of types) {
        expect(NODE_RULES[t]).toBeDefined();
        expect(NODE_RULES[t].length).toBeGreaterThan(0);
      }
    });
  });

  describe('skill 节点', () => {
    it('NV-01: 缺 skillId 时报错', () => {
      const result = validateNode(makeNode({ nodeType: 'skill', skillName: '解析' }));
      expect(result.valid).toBe(false);
      expect(result.errors).toContain('技能ID必填');
    });

    it('NV-02: 超时超出范围时报错', () => {
      const result = validateNode(
        makeNode({ nodeType: 'skill', skillId: 'p1', skillName: '解析', timeout: 5000 })
      );
      expect(result.valid).toBe(false);
      expect(result.errors.some(e => e.includes('超时范围'))).toBe(true);
    });

    it('NV-04: 合法 skill 节点通过', () => {
      const result = validateNode(
        makeNode({ nodeType: 'skill', skillId: 'p1', skillName: '解析', timeout: 30, retryCount: 3 })
      );
      expect(result.valid).toBe(true);
      expect(result.errors).toHaveLength(0);
    });

    it('NV-05: params 对象保留', () => {
      const result = validateNode(
        makeNode({ nodeType: 'skill', skillId: 'p1', skillName: '解析', params: { key: 'val' } })
      );
      expect(result.valid).toBe(true);
    });

    it('retryCount 超出范围时报错', () => {
      const result = validateNode(
        makeNode({ nodeType: 'skill', skillId: 'p1', skillName: '解析', retryCount: 20 })
      );
      expect(result.valid).toBe(false);
      expect(result.errors.some(e => e.includes('重试次数'))).toBe(true);
    });

    it('timeout 为非数字时报错', () => {
      const result = validateNode(
        makeNode({ nodeType: 'skill', skillId: 'p1', skillName: '解析', timeout: 'abc' as unknown as number })
      );
      expect(result.valid).toBe(false);
      expect(result.errors.some(e => e.includes('超时范围'))).toBe(true);
    });
  });

  describe('conditional 节点', () => {
    it('NV-03: 缺 trueBranch 时报错', () => {
      const result = validateNode(
        makeNode({ nodeType: 'conditional', condition: 'x > 0', falseBranch: 'B' })
      );
      expect(result.valid).toBe(false);
      expect(result.errors).toContain('True 分支必填');
    });

    it('缺 condition 时报错', () => {
      const result = validateNode(
        makeNode({ nodeType: 'conditional', trueBranch: 'A', falseBranch: 'B' })
      );
      expect(result.valid).toBe(false);
      expect(result.errors).toContain('条件表达式必填');
    });

    it('合法 conditional 节点通过', () => {
      const result = validateNode(
        makeNode({ nodeType: 'conditional', condition: 'x > 0', trueBranch: 'A', falseBranch: 'B' })
      );
      expect(result.valid).toBe(true);
    });
  });

  describe('loop 节点', () => {
    it('缺 loopVariable 时报错', () => {
      const result = validateNode(makeNode({ nodeType: 'loop', loopCount: 5 }));
      expect(result.valid).toBe(false);
      expect(result.errors).toContain('循环变量必填');
    });

    it('loopCount 超出范围时报错', () => {
      const result = validateNode(
        makeNode({ nodeType: 'loop', loopCount: 2000, loopVariable: 'i' })
      );
      expect(result.valid).toBe(false);
      expect(result.errors.some(e => e.includes('循环次数'))).toBe(true);
    });

    it('合法 loop 节点通过', () => {
      const result = validateNode(
        makeNode({ nodeType: 'loop', loopCount: 10, loopVariable: 'i' })
      );
      expect(result.valid).toBe(true);
    });
  });

  describe('agent 节点', () => {
    it('缺 agentType 时报错', () => {
      const result = validateNode(makeNode({ nodeType: 'agent' }));
      expect(result.valid).toBe(false);
      expect(result.errors).toContain('Agent 类型必填');
    });

    it('maxTurns 超出范围时报错', () => {
      const result = validateNode(
        makeNode({ nodeType: 'agent', agentType: 'researcher', maxTurns: 100 })
      );
      expect(result.valid).toBe(false);
      expect(result.errors.some(e => e.includes('最大轮数'))).toBe(true);
    });

    it('合法 agent 节点通过', () => {
      const result = validateNode(
        makeNode({ nodeType: 'agent', agentType: 'researcher', maxTurns: 10 })
      );
      expect(result.valid).toBe(true);
    });
  });

  describe('workflow 节点', () => {
    it('缺 workflowId 时报错', () => {
      const result = validateNode(makeNode({ nodeType: 'workflow' }));
      expect(result.valid).toBe(false);
      expect(result.errors).toContain('工作流ID必填');
    });

    it('合法 workflow 节点通过', () => {
      const result = validateNode(makeNode({ nodeType: 'workflow', workflowId: 'wf-001' }));
      expect(result.valid).toBe(true);
    });
  });

  describe('NV-06: 所有节点类型通过', () => {
    it('5 种节点类型各 1 个合法节点全部通过', () => {
      const validNodes: FlowNodeData[] = [
        { label: 's', nodeType: 'skill', skillId: 'id', skillName: 'n' },
        { label: 'c', nodeType: 'conditional', condition: 'x', trueBranch: 'A', falseBranch: 'B' },
        { label: 'l', nodeType: 'loop', loopCount: 3, loopVariable: 'i' },
        { label: 'a', nodeType: 'agent', agentType: 'r' },
        { label: 'w', nodeType: 'workflow', workflowId: 'w1' },
      ];
      for (const data of validNodes) {
        const result = validateNode(data);
        expect(result.valid).toBe(true);
      }
    });
  });

  describe('未知节点类型', () => {
    it('未知类型返回错误', () => {
      const result = validateNode(makeNode({ nodeType: 'unknown' as NodeType }));
      expect(result.valid).toBe(false);
      expect(result.errors.some(e => e.includes('未知节点类型'))).toBe(true);
    });
  });
});
