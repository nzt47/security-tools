import React, { useRef, useEffect, useState } from 'react';
import './ThinkingBlock.css';

export interface ThinkingBlockProps {
  /** 思考内容（完整文本） */
  content: string;
  /** 是否正在思考中（流式） */
  streaming?: boolean;
  /** 思考标签 */
  label?: string;
}

/**
 * 可折叠的流式思考展示组件
 * 模仿 Claude Code 的 thinking 块风格
 */
const ThinkingBlock: React.FC<ThinkingBlockProps> = ({
  content,
  streaming = false,
  label = '思考过程',
}) => {
  const [collapsed, setCollapsed] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // 内容更新时自动滚到底部
  useEffect(() => {
    if (scrollRef.current && streaming) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [content, streaming]);

  const hasContent = content.trim().length > 0;

  return (
    <div className={`thinking-block ${streaming ? 'streaming' : ''} ${collapsed ? 'collapsed' : ''}`}>
      <button
        className="thinking-header"
        onClick={() => setCollapsed(!collapsed)}
        type="button"
      >
        <span className="thinking-icon">
          {streaming ? '⟳' : collapsed ? '›' : '↓'}
        </span>
        <span className="thinking-label">
          {streaming ? `${label}...` : label}
        </span>
        <span className="thinking-status">
          {streaming && <span className="thinking-spinner" />}
          <span className="thinking-count">{content.length} 字</span>
          <span className="thinking-chevron">{collapsed ? '展开' : '收起'}</span>
        </span>
      </button>

      {!collapsed && (
        <div className="thinking-body" ref={scrollRef}>
          {hasContent ? (
            <pre className="thinking-text">{content}</pre>
          ) : (
            <div className="thinking-placeholder">
              <span className="thinking-dot" />
              <span className="thinking-dot" />
              <span className="thinking-dot" />
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default ThinkingBlock;
