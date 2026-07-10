import type { FlowNodeData, NodeType, ValidationRule, ValidationResult } from '../types';

const NODE_RULES: Record<NodeType, ValidationRule[]> = {
  skill: [
    { field: 'skillId', type: 'string', required: true, message: '技能ID必填' },
    { field: 'skillName', type: 'string', required: true, message: '技能名称必填' },
    { field: 'timeout', type: 'number', min: 1, max: 3600, required: false, message: '超时范围 1-3600s' },
    { field: 'retryCount', type: 'number', min: 0, max: 10, required: false, message: '重试次数 0-10' },
    { field: 'params', type: 'object', required: false, message: '参数必须为对象' },
  ],
  conditional: [
    { field: 'condition', type: 'string', required: true, message: '条件表达式必填' },
    { field: 'trueBranch', type: 'string', required: true, message: 'True 分支必填' },
    { field: 'falseBranch', type: 'string', required: true, message: 'False 分支必填' },
  ],
  loop: [
    { field: 'loopCount', type: 'number', min: 1, max: 1000, required: true, message: '循环次数 1-1000' },
    { field: 'loopVariable', type: 'string', required: true, message: '循环变量必填' },
  ],
  agent: [
    { field: 'agentType', type: 'string', required: true, message: 'Agent 类型必填' },
    { field: 'maxTurns', type: 'number', min: 1, max: 50, required: false, message: '最大轮数 1-50' },
  ],
  workflow: [
    { field: 'workflowId', type: 'string', required: true, message: '工作流ID必填' },
  ],
};

export function validateNode(data: FlowNodeData): ValidationResult {
  const rules = NODE_RULES[data.nodeType];
  if (!rules) {
    return { valid: false, errors: [`未知节点类型: ${data.nodeType}`] };
  }
  const errors: string[] = [];
  for (const rule of rules) {
    const value = data[rule.field as keyof FlowNodeData];
    if (rule.required && (value === undefined || value === null || value === '')) {
      errors.push(rule.message);
      continue;
    }
    if (value === undefined || value === null) continue;
    if (rule.type === 'number') {
      const num = Number(value);
      if (isNaN(num)) {
        errors.push(rule.message);
        continue;
      }
      if (rule.min !== undefined && num < rule.min) errors.push(rule.message);
      if (rule.max !== undefined && num > rule.max) errors.push(rule.message);
    }
    if (rule.type === 'string' && typeof value !== 'string') {
      errors.push(rule.message);
    }
    if (rule.type === 'object' && typeof value !== 'object') {
      errors.push(rule.message);
    }
  }
  return { valid: errors.length === 0, errors };
}

export { NODE_RULES };
