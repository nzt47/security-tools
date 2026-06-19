import React from 'react';
import './ToolStepsDisplay.css';

export interface ToolStep {
  type: 'tool_call' | 'tool_result' | 'text' | 'error';
  tool?: string;
  args?: Record<string, any>;
  status?: string;
  summary?: string;
  content?: string;
}

export interface ToolStepsDisplayProps {
  steps: ToolStep[];
}

/** 工具调用步骤展示组件 — 实时显示搜索/读文件等操作 */
const ToolStepsDisplay: React.FC<ToolStepsDisplayProps> = ({ steps }) => {
  if (!steps || steps.length === 0) return null;

  return (
    <div className="tool-steps">
      {steps.map((step, idx) => {
        if (step.type === 'tool_call') {
          return (
            <div key={idx} className="tool-step tool-call">
              <span className="tool-step-icon">⚡</span>
              <span className="tool-step-name">{step.tool}</span>
              {step.args && (
                <span className="tool-step-args">
                  {Object.entries(step.args).map(([k, v]) => (
                    <span key={k} className="tool-arg">
                      <span className="tool-arg-key">{k}</span>=
                      <span className="tool-arg-val">{String(v).slice(0, 60)}</span>
                    </span>
                  ))}
                </span>
              )}
              <span className="tool-step-status running">执行中...</span>
            </div>
          );
        }
        if (step.type === 'tool_result') {
          return (
            <div key={idx} className={`tool-step tool-result ${step.status}`}>
              <span className="tool-step-icon">
                {step.status === 'success' ? '✅' : '❌'}
              </span>
              <span className="tool-step-name">{step.tool}</span>
              {step.summary && (
                <span className="tool-step-summary">
                  {step.summary.slice(0, 80)}
                </span>
              )}
              <span className={`tool-step-status ${step.status}`}>
                {step.status === 'success' ? '完成' : '失败'}
              </span>
            </div>
          );
        }
        return null;
      })}
    </div>
  );
};

export default ToolStepsDisplay;
